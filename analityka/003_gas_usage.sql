-- ==================================================
-- Warstwa analityczna: szacowanie zużycia gazu z okresów grzania
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić
-- (po 002_heating_periods.sql i 001_sensor_aggregates.sql). Skrypt
-- można uruchamiać wielokrotnie (create ... if not exists,
-- create or replace).
--
-- Opis modelu: zuzycie_gazu/SPECYFIKACJA.md. Baza liczy tylko cechy
-- okresów grzania (pole pod krzywą nadwyżki, wzrost temperatury
-- mieszkania); mnożniki dopasowuje aplikacja prezentacja/app_gaz.py
-- do czystych pomiarów z tabeli gas_calibration.


-- --------------------------------------------------
-- 1. Czyste pomiary zużycia gazu (kalibracja)
-- --------------------------------------------------

-- Zakres, w którym gaz szedł wyłącznie na ogrzewanie domu. Zużycie
-- wpisuje się wprost albo jako dwa stany licznika.
create table if not exists public.gas_calibration (
    id bigint generated always as identity primary key,
    poczatek timestamptz not null,
    koniec timestamptz not null,
    zuzycie_m3 numeric not null,
    stan_poczatek_m3 numeric,
    stan_koniec_m3 numeric,
    aktywny boolean not null default true,  -- false = pomijany w dopasowaniu
    notatka text,
    utworzono_at timestamptz not null default now(),
    check (koniec > poczatek),
    check (zuzycie_m3 >= 0)
);

alter table public.gas_calibration enable row level security;


-- --------------------------------------------------
-- 2. Średnia temperatura grupy czujników w chwili p_ms
-- --------------------------------------------------

-- Średnia z ostatnich odczytów członków grupy (sensor_groups),
-- nie późniejszych niż p_ms.
create or replace function public.temperatura_grupy(
    p_grupa text,
    p_ms bigint
)
returns numeric
language sql
stable
set search_path = public
as $$
select avg(t.wartosc)
from sensor_groups g
cross join lateral ostatnia_temperatura(g.device_id, p_ms) t
where g.grupa = p_grupa;
$$;


-- --------------------------------------------------
-- 3. Cechy okresów grzania w zakresie [p_od, p_do)
-- --------------------------------------------------

-- Dla każdego okresu z heating_periods, którego garb nachodzi na zakres:
--
--   nadwyżka(t) = max(0, (grzejnik(t) − kuchnia(t)) − (grzejnik − kuchnia)_poczatek)
--
-- Garb trwa od początku okresu do pierwszego odczytu po szczycie
-- z nadwyżką <= p_prog_konca, najdłużej do początku następnego okresu
-- albo p_max_garb. Gdy dane kończą się wcześniej, garb_trwa = true.
--
--   pole              = ∫ nadwyżka dt (°C·h, trapezy) w części garbu
--                       leżącej w zakresie;
--   wzrost_mieszkania = max(0, T_grupy(szczyt) − T_grupy(poczatek)), °C,
--                       z obu chwil przyciętymi do zakresu: o ile nagrzało
--                       się mieszkanie, gdy grzejnik się rozgrzewał
--                       (energia zgromadzona w murach). Tylko faza
--                       poczatek → szczyt, bo dalsza część garbu trwa
--                       godzinami i łapie nagrzewanie od słońca.
--
-- Pola są addytywne: suma po kolejnych zakresach = wynik dla zakresu
-- łącznego. Wzrost prawie też (obcinany do zera w każdej części osobno).
create or replace function public.cechy_okresow_grzania(
    p_od timestamptz,
    p_do timestamptz,
    p_device_id text default 'bf333171b6295a9253wx0y',  -- Temp Grzejnik
    p_grupa text default 'mieszkanie',
    p_prog_konca numeric default 0.1,
    p_max_garb interval default interval '48 hours'
)
returns table (
    poczatek timestamptz,
    szczyt timestamptz,
    rodzaj text,
    trwa boolean,
    koniec_garbu timestamptz,
    garb_trwa boolean,
    czesc_od timestamptz,
    czesc_do timestamptz,
    czas_h numeric,
    skok numeric,
    pole numeric,
    wzrost_mieszkania numeric
)
language sql
stable
set search_path = public
as $$
with okresy as (
    select
        hp.*,
        (extract(epoch from hp.poczatek) * 1000)::bigint as start_ms,
        (extract(epoch from hp.szczyt) * 1000)::bigint as szczyt_ms,
        hp.temp_poczatek - coalesce(hp.temp_odn_poczatek, hp.temp_poczatek) as roznica0,
        least(
            coalesce(
                lead(hp.poczatek) over (order by hp.poczatek),
                'infinity'::timestamptz
            ),
            hp.poczatek + p_max_garb
        ) as granica
    from heating_periods hp
    where hp.device_id = p_device_id
),
kandydaci as (
    select o.*, (extract(epoch from o.granica) * 1000)::bigint as granica_ms
    from okresy o
    where o.poczatek < p_do
      and o.granica > p_od
),
odczyty as (
    select
        k.start_ms,
        r.event_time_ms as t,
        greatest(0, r.value - kuch.wartosc - k.roznica0) as x
    from kandydaci k
    join sensor_readings r
      on r.device_id = k.device_id
     and r.code = 'va_temperature'
     and r.event_time_ms >= k.start_ms
     and r.event_time_ms <= k.granica_ms
    cross join lateral ostatnia_temperatura(k.device_id_odniesienia, r.event_time_ms) kuch
),
konce as (
    select
        k.start_ms,
        (
            select min(o.t) from odczyty o
            where o.start_ms = k.start_ms
              and o.t > k.szczyt_ms
              and o.x <= p_prog_konca
        ) as koniec_ms,
        exists (
            select 1 from sensor_readings r
            where r.device_id = k.device_id
              and r.code = 'va_temperature'
              and r.event_time_ms >= k.granica_ms
        ) as dane_po_granicy
    from kandydaci k
),
garby as (
    select
        k.*,
        case
            when c.koniec_ms is not null then c.koniec_ms
            when c.dane_po_granicy then k.granica_ms
            else (select max(o.t) from odczyty o where o.start_ms = k.start_ms)
        end as koniec_ms,
        k.trwa or (c.koniec_ms is null and not c.dane_po_granicy) as garb_trwa
    from kandydaci k
    join konce c using (start_ms)
),
czesci as (
    select
        g.*,
        greatest(g.start_ms, (extract(epoch from p_od) * 1000)::bigint) as lo,
        least(g.koniec_ms, (extract(epoch from p_do) * 1000)::bigint) as hi
    from garby g
),
odcinki as (
    select
        o.start_ms,
        lag(o.t) over w as t1,
        lag(o.x) over w as x1,
        o.t as t2,
        o.x as x2
    from odczyty o
    window w as (partition by o.start_ms order by o.t)
),
przyciete as (
    select
        c.start_ms,
        greatest(s.t1, c.lo) as a,
        least(s.t2, c.hi) as b,
        s.t1, s.x1, s.t2, s.x2
    from czesci c
    join odcinki s on s.start_ms = c.start_ms
    where s.t1 is not null
      and s.t2 > s.t1
      and least(s.t2, c.hi) > greatest(s.t1, c.lo)
),
pola as (
    select
        start_ms,
        sum(
            (
                (x1 + (x2 - x1) * (a - t1) / (t2 - t1))
              + (x1 + (x2 - x1) * (b - t1) / (t2 - t1))
            ) / 2 * (b - a) / 3600000.0
        ) as pole
    from przyciete
    group by start_ms
)
select
    c.poczatek,
    c.szczyt,
    c.rodzaj,
    c.trwa,
    to_timestamp(c.koniec_ms / 1000.0) as koniec_garbu,
    c.garb_trwa,
    to_timestamp(c.lo / 1000.0) as czesc_od,
    to_timestamp(c.hi / 1000.0) as czesc_do,
    round((c.szczyt_ms - c.start_ms) / 3600000.0, 3) as czas_h,
    c.temp_szczyt - c.temp_poczatek as skok,
    round(coalesce(p.pole, 0), 3) as pole,
    case
        when least(c.hi, c.szczyt_ms) > c.lo then round(greatest(0,
            temperatura_grupy(p_grupa, least(c.hi, c.szczyt_ms))
          - temperatura_grupy(p_grupa, c.lo)
        ), 3)
        else 0
    end as wzrost_mieszkania
from czesci c
left join pola p using (start_ms)
where c.hi > c.lo
order by c.poczatek;
$$;


-- --------------------------------------------------
-- 4. Uprawnienia
-- --------------------------------------------------

-- Pomiary wpisuje lokalna aplikacja z kluczem service_role;
-- anon/authenticated nie mają dostępu (RLS bez polityk).
grant select, insert, update, delete on public.gas_calibration to service_role;

revoke execute on function public.temperatura_grupy(text, bigint)
    from public, anon, authenticated;

revoke execute on function public.cechy_okresow_grzania(
    timestamptz, timestamptz, text, text, numeric, interval
) from public, anon, authenticated;

grant execute on function public.temperatura_grupy(text, bigint) to service_role;

grant execute on function public.cechy_okresow_grzania(
    timestamptz, timestamptz, text, text, numeric, interval
) to service_role;
