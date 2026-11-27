-- ==================================================
-- Warstwa analityczna: szacowanie zużycia gazu z okresów grzania
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić
-- (po 000_sensor_exclusions.sql, 002_heating_periods.sql
-- i 001_sensor_aggregates.sql). Skrypt
-- można uruchamiać wielokrotnie (create ... if not exists,
-- create or replace).
--
-- Opis modelu: zuzycie_gazu/SPECYFIKACJA.md. Baza liczy tylko cechy
-- (pole pod krzywą pracy grzejnika, wzrost temperatury mieszkania
-- w okresach grzania); mnożniki dopasowuje aplikacja
-- prezentacja/app_gaz.py do czystych pomiarów z tabeli gas_calibration.


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
-- 3. Praca grzejnika w zakresie [p_od, p_do)
-- --------------------------------------------------

-- Niezależnie od okresów grzania, wprost z odczytów:
--
--   x(t) = T_grzejnik(t) − T_kuchnia(t)
--
-- Gdy kocioł nie pracuje, x leży na poziomie p_baza (ok. −0,7 °C:
-- grzejnik wisi przy zimnej ścianie), więc x >= p_prog (0,2 °C)
-- oznacza, że grzejnik pracuje. Odcinek między kolejnymi odczytami
-- grzejnika jest liczony, gdy na którymkolwiek jego końcu x >= p_prog
-- (łapie też rozgrzewanie i stygnięcie przy progu), a odczyty dzieli
-- najwyżej p_max_przerwa (dłuższa przerwa w danych = odcinek pomijany).
--
--   pole         = ∫ max(0, x − p_baza) dt  (°C·h, trapezy) po liczonych
--                  odcinkach, przyciętych do zakresu (interpolacja liniowa);
--   czas_pracy_h = łączna długość liczonych odcinków w zakresie.
--
-- Pole liczone od stałego poziomu „kocioł nie pracuje”, a nie od
-- różnicy z początku okresu: przy podtrzymaniu i przy przygotowaniu
-- na przyjazd daje wtedy podobny mnożnik m³/°C·h.
--
-- Obie cechy są ściśle addytywne: suma po kolejnych zakresach = wynik
-- dla zakresu łącznego. Nie ma limitu czasu pracy.
--
--   pracuje_na_koncu = brak odczytów od p_do, a w ostatnim odczycie
--                      grzejnik pracował (wynik może jeszcze wzrosnąć).

create or replace function public.praca_grzejnika(
    p_od timestamptz,
    p_do timestamptz,
    p_device_id text default 'bf333171b6295a9253wx0y',      -- Temp Grzejnik
    p_device_id_odn text default 'bf09f3b9302d16a74froza',  -- Temp Kuchnias
    p_prog numeric default 0.2,
    p_baza numeric default -0.7,
    p_max_przerwa interval default interval '1 hour'
)
returns table (
    pole numeric,
    czas_pracy_h numeric,
    pracuje_na_koncu boolean,
    ostatni_odczyt timestamptz
)
language sql
stable
set search_path = public
as $$
with granice as (
    select
        (extract(epoch from p_od) * 1000)::bigint as lo,
        (extract(epoch from p_do) * 1000)::bigint as hi,
        (extract(epoch from p_max_przerwa) * 1000)::bigint as przerwa
),
zasieg as (
    -- Odczyty zakresu plus ostatni przed nim i pierwszy po nim
    -- (odcinki przecinające granice zakresu).
    select
        g.*,
        coalesce((
            select max(r.event_time_ms) from sensor_readings_czyste r
            where r.device_id = p_device_id
              and r.code = 'va_temperature'
              and r.event_time_ms <= g.lo
        ), g.lo) as t_od,
        (
            select min(r.event_time_ms) from sensor_readings_czyste r
            where r.device_id = p_device_id
              and r.code = 'va_temperature'
              and r.event_time_ms >= g.hi
        ) as t_po
    from granice g
),
odczyty as (
    select r.event_time_ms as t, r.value - k.wartosc as x
    from zasieg z
    join sensor_readings_czyste r
      on r.device_id = p_device_id
     and r.code = 'va_temperature'
     and r.event_time_ms >= z.t_od
     and r.event_time_ms <= coalesce(z.t_po, z.hi)
    cross join lateral ostatnia_temperatura(p_device_id_odn, r.event_time_ms) k
    where k.wartosc is not null
),
odcinki as (
    select
        lag(o.t) over w as t1,
        lag(o.x) over w as x1,
        o.t as t2,
        o.x as x2
    from odczyty o
    window w as (order by o.t)
),
liczone as (
    select
        s.t1, s.t2,
        greatest(0, s.x1 - p_baza) as y1,
        greatest(0, s.x2 - p_baza) as y2,
        greatest(s.t1, z.lo) as a,
        least(s.t2, z.hi) as b
    from odcinki s
    cross join zasieg z
    where s.t1 is not null
      and s.t2 > s.t1
      and s.t2 - s.t1 <= z.przerwa
      and greatest(s.x1, s.x2) >= p_prog
      and least(s.t2, z.hi) > greatest(s.t1, z.lo)
),
ostatni as (
    select o.t, o.x
    from odczyty o cross join zasieg z
    where o.t < z.hi
    order by o.t desc
    limit 1
)
select
    round(coalesce((
        select sum(
            (
                (y1 + (y2 - y1) * (a - t1) / (t2 - t1))
              + (y1 + (y2 - y1) * (b - t1) / (t2 - t1))
            ) / 2 * (b - a) / 3600000.0
        ) from liczone
    ), 0), 3) as pole,
    round(coalesce((select sum(b - a) from liczone), 0) / 3600000.0, 3) as czas_pracy_h,
    coalesce((select z.t_po is null from zasieg z) and (select o.x >= p_prog from ostatni o), false)
        as pracuje_na_koncu,
    (select to_timestamp(o.t / 1000.0) from ostatni o) as ostatni_odczyt;
$$;


-- --------------------------------------------------
-- 4. Cechy okresów grzania w zakresie [p_od, p_do)
-- --------------------------------------------------

-- Okresy z heating_periods, których faza poczatek → szczyt nachodzi
-- na zakres:
--
--   wzrost_mieszkania = max(0, T_grupy(szczyt) − T_grupy(poczatek)), °C,
--                       z obu chwil przyciętymi do zakresu: o ile nagrzało
--                       się mieszkanie, gdy grzejnik się rozgrzewał
--                       (energia zgromadzona w murach);
--   czas_h, skok      = cechy całego okresu (model B).
--
-- Pole pod krzywą liczy praca_grzejnika(), niezależnie od okresów.

-- Poprzednia wersja (garb okresu z polem, limit 48 h) miała inną sygnaturę.
drop function if exists public.cechy_okresow_grzania(
    timestamptz, timestamptz, text, text, numeric, interval
);

create or replace function public.cechy_okresow_grzania(
    p_od timestamptz,
    p_do timestamptz,
    p_device_id text default 'bf333171b6295a9253wx0y',  -- Temp Grzejnik
    p_grupa text default 'mieszkanie'
)
returns table (
    poczatek timestamptz,
    szczyt timestamptz,
    rodzaj text,
    trwa boolean,
    czesc_od timestamptz,
    czesc_do timestamptz,
    czas_h numeric,
    skok numeric,
    wzrost_mieszkania numeric
)
language sql
stable
set search_path = public
as $$
with czesci as (
    select
        hp.*,
        greatest(hp.poczatek, p_od) as od,
        least(hp.szczyt, p_do) as do_
    from heating_periods hp
    where hp.device_id = p_device_id
      and hp.poczatek < p_do
      and hp.szczyt >= p_od
)
select
    c.poczatek,
    c.szczyt,
    c.rodzaj,
    c.trwa,
    c.od as czesc_od,
    c.do_ as czesc_do,
    round(extract(epoch from c.szczyt - c.poczatek) / 3600.0, 3) as czas_h,
    c.temp_szczyt - c.temp_poczatek as skok,
    case
        when c.do_ > c.od then round(greatest(0,
            temperatura_grupy(p_grupa, (extract(epoch from c.do_) * 1000)::bigint)
          - temperatura_grupy(p_grupa, (extract(epoch from c.od) * 1000)::bigint)
        ), 3)
        else 0
    end as wzrost_mieszkania
from czesci c
order by c.poczatek;
$$;


-- --------------------------------------------------
-- 5. Uprawnienia
-- --------------------------------------------------

-- Pomiary wpisuje lokalna aplikacja z kluczem service_role;
-- anon/authenticated nie mają dostępu (RLS bez polityk).
grant select, insert, update, delete on public.gas_calibration to service_role;

revoke execute on function public.temperatura_grupy(text, bigint)
    from public, anon, authenticated;

revoke execute on function public.praca_grzejnika(
    timestamptz, timestamptz, text, text, numeric, numeric, interval
) from public, anon, authenticated;

revoke execute on function public.cechy_okresow_grzania(
    timestamptz, timestamptz, text, text
) from public, anon, authenticated;

grant execute on function public.temperatura_grupy(text, bigint) to service_role;

grant execute on function public.praca_grzejnika(
    timestamptz, timestamptz, text, text, numeric, numeric, interval
) to service_role;

grant execute on function public.cechy_okresow_grzania(
    timestamptz, timestamptz, text, text
) to service_role;
