-- ==================================================
-- Warstwa analityczna: tabele i funkcje średnich
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić.
-- Skrypt można uruchamiać wielokrotnie (create ... if not exists,
-- create or replace, on conflict do nothing).
--
-- Wszystkie średnie są ważone czasem: każdy odczyt obowiązuje
-- do następnego odczytu, ale nie dłużej niż ustalony czas ważności.
-- Okresy (doba, 3 h) są wyznaczane w czasie polskim (Europe/Warsaw).


-- --------------------------------------------------
-- 1. Grupy czujników
-- --------------------------------------------------

create table if not exists public.sensor_groups (
    grupa text not null,
    device_id text not null,
    primary key (grupa, device_id)
);

alter table public.sensor_groups enable row level security;

-- Mieszkanie: wszystkie czujniki Tuya oprócz "Temp Grzejnik"
-- (bf333171b6295a9253wx0y), który mierzy temperaturę przy grzejniku.
insert into public.sensor_groups (grupa, device_id) values
    ('mieszkanie', 'bf09f3b9302d16a74froza'),  -- Temp Kuchnias
    ('mieszkanie', 'bff67b6528b7b61ab34bab'),  -- Temp Zielony
    ('mieszkanie', 'bf820e12f85f729ddaxkre'),  -- Temp Jadalnia
    ('mieszkanie', 'bf587bca663683547dxy4p'),  -- Temp Przedpokój
    ('mieszkanie', 'bfbf68aa252e1f7893a3zz')   -- Temp żółty pok
on conflict do nothing;


-- --------------------------------------------------
-- 2. Wyniki agregacji
-- --------------------------------------------------

create table if not exists public.sensor_aggregates (
    seria text not null,              -- weather_eprz, id czujnika lub nazwa grupy
    code text not null,               -- va_temperature, va_humidity
    okres text not null,              -- 3h, 1d
    metoda text not null,             -- srednia_wazona_czasem
    poczatek timestamptz not null,    -- początek okresu (włącznie)
    koniec timestamptz not null,      -- koniec okresu (wyłącznie)
    zrodlo text not null,             -- tuya, metar
    typ_serii text not null,          -- czujnik, grupa
    wartosc numeric,                  -- null, gdy w okresie nie było danych
    min numeric,
    max numeric,
    unit text,
    liczba_probek integer not null,   -- odczyty użyte do obliczeń
    liczba_czujnikow integer not null,-- czujniki, które dały dane w okresie
    coverage_seconds integer not null,-- sekundy okresu pokryte danymi (dla grupy: średnio na czujnik)
    przeliczono_at timestamptz not null default now(),
    primary key (seria, code, okres, metoda, poczatek)
);

create index if not exists sensor_aggregates_poczatek_idx
    on public.sensor_aggregates (poczatek);

alter table public.sensor_aggregates enable row level security;


-- --------------------------------------------------
-- 3. Średnia ważona czasem dla jednej serii
-- --------------------------------------------------
--
-- Dzieli zakres [p_od, p_do) na okresy po p_krok_godz godzin liczone
-- od północy czasu polskiego i dla każdego zapisuje (upsert) wynik.
-- Dla kilku czujników (grupa) całki wszystkich czujników są sumowane,
-- więc każda sekunda pomiaru z każdego czujnika waży tyle samo.
-- Okres zawierający p_od jest przeliczany w całości.
-- Zwraca liczbę zapisanych wierszy.

create or replace function public.agreguj_wazona_czasem(
    p_seria text,
    p_typ_serii text,
    p_device_ids text[],
    p_code text,
    p_zrodlo text,
    p_okres text,
    p_krok_godz integer,
    p_waznosc interval,
    p_od timestamptz,
    p_do timestamptz
)
returns integer
language sql
set search_path = public
as $$
with
granice as (
    -- Nie twórz pustych okresów przed pierwszym ani po ostatnim odczycie
    -- serii (np. od ostatniego pobrania do teraz).
    -- Brak odczytów -> od = null -> brak okresów.
    select
        case when min(measured_at) is not null
             then greatest(p_od, min(measured_at)) end as od,
        least(p_do, max(measured_at) + p_waznosc) as do_
    from sensor_readings
    where device_id = any(p_device_ids)
      and code = p_code
),
przedzialy as (
    select
        (d + k * make_interval(hours => p_krok_godz)) at time zone 'Europe/Warsaw' as poczatek,
        (d + (k + 1) * make_interval(hours => p_krok_godz)) at time zone 'Europe/Warsaw' as koniec
    from granice,
        generate_series(
            (granice.od at time zone 'Europe/Warsaw')::date::timestamp,
            (p_do at time zone 'Europe/Warsaw')::date::timestamp,
            interval '1 day'
        ) as d,
        generate_series(0, 24 / p_krok_godz - 1) as k
),
okresy as (
    select p.*
    from przedzialy p, granice g
    where p.koniec > g.od
      and p.poczatek < g.do_
),
odczyty as (
    -- Każdy odczyt obowiązuje do następnego, maksymalnie p_waznosc.
    select
        device_id,
        measured_at as od,
        least(
            coalesce(
                lead(measured_at) over (partition by device_id order by event_time_ms),
                measured_at + p_waznosc
            ),
            measured_at + p_waznosc
        ) as do_,
        value::float8 as v,
        unit
    from sensor_readings
    where device_id = any(p_device_ids)
      and code = p_code
      and measured_at >= (select min(poczatek) from okresy) - p_waznosc
      and measured_at < (select max(koniec) from okresy)
),
odcinki as (
    select
        o.poczatek,
        r.device_id,
        r.v,
        r.unit,
        extract(epoch from least(r.do_, o.koniec) - greatest(r.od, o.poczatek))::float8 as sek
    from okresy o
    join odczyty r
      on r.od < o.koniec
     and r.do_ > o.poczatek
),
wyniki as (
    select
        o.poczatek,
        o.koniec,
        sum(s.v * s.sek) / nullif(sum(s.sek), 0) as wartosc,
        min(s.v) as min,
        max(s.v) as max,
        max(s.unit) as unit,
        count(s.v)::integer as liczba_probek,
        count(distinct s.device_id)::integer as liczba_czujnikow,
        round(coalesce(sum(s.sek), 0) / cardinality(p_device_ids))::integer as coverage_seconds
    from okresy o
    left join odcinki s
      on s.poczatek = o.poczatek
     and s.sek > 0
    group by o.poczatek, o.koniec
),
zapis as (
    insert into sensor_aggregates (
        seria, code, okres, metoda, poczatek, koniec, zrodlo, typ_serii,
        wartosc, min, max, unit,
        liczba_probek, liczba_czujnikow, coverage_seconds, przeliczono_at
    )
    select
        p_seria, p_code, p_okres, 'srednia_wazona_czasem', poczatek, koniec, p_zrodlo, p_typ_serii,
        round(wartosc::numeric, 2), min::numeric, max::numeric, unit,
        liczba_probek, liczba_czujnikow, coverage_seconds, now()
    from wyniki
    on conflict (seria, code, okres, metoda, poczatek) do update set
        koniec = excluded.koniec,
        zrodlo = excluded.zrodlo,
        typ_serii = excluded.typ_serii,
        wartosc = excluded.wartosc,
        min = excluded.min,
        max = excluded.max,
        unit = excluded.unit,
        liczba_probek = excluded.liczba_probek,
        liczba_czujnikow = excluded.liczba_czujnikow,
        coverage_seconds = excluded.coverage_seconds,
        przeliczono_at = excluded.przeliczono_at
    returning 1
)
select count(*)::integer from zapis;
$$;


-- --------------------------------------------------
-- 4. Przeliczenie wszystkich zdefiniowanych średnich
-- --------------------------------------------------
--
-- Tu jest "konfiguracja" analityki: jakie serie, okresy i czasy ważności.
-- p_od = null oznacza całą historię.

create or replace function public.przelicz_srednie(
    p_od timestamptz default null,
    p_do timestamptz default now()
)
returns integer
language plpgsql
set search_path = public
as $$
declare
    v_od timestamptz := coalesce(p_od, '-infinity'::timestamptz);
    v_czujniki text[];
    v_code text;
    v_device text;
    v_liczba integer := 0;
begin
    -- Mieszkanie: średnie 3-godzinne, odczyt ważny maks. 15 minut.
    v_czujniki := array(
        select device_id from sensor_groups where grupa = 'mieszkanie' order by device_id
    );

    for v_code in select unnest(array['va_temperature', 'va_humidity']) loop
        v_liczba := v_liczba + agreguj_wazona_czasem(
            'mieszkanie', 'grupa', v_czujniki, v_code, 'tuya',
            '3h', 3, interval '15 minutes', v_od, p_do
        );

        foreach v_device in array v_czujniki loop
            v_liczba := v_liczba + agreguj_wazona_czasem(
                v_device, 'czujnik', array[v_device], v_code, 'tuya',
                '3h', 3, interval '15 minutes', v_od, p_do
            );
        end loop;
    end loop;

    -- Na zewnątrz: średnia dobowa z METAR/SPECI EPRZ.
    -- Raporty co 30 min; ważność 60 min toleruje jeden brakujący raport.
    v_liczba := v_liczba + agreguj_wazona_czasem(
        'weather_eprz', 'czujnik', array['weather_eprz'], 'va_temperature', 'metar',
        '1d', 24, interval '60 minutes', v_od, p_do
    );

    return v_liczba;
end;
$$;


-- --------------------------------------------------
-- 5. Uprawnienia
-- --------------------------------------------------
-- Nowe tabele nie dostają automatycznie uprawnień dla ról API,
-- więc nadajemy je jawnie. service_role omija RLS, ale nie GRANT-y.

grant select on public.sensor_groups to service_role;
grant select, insert, update on public.sensor_aggregates to service_role;

-- Funkcje zapisują dane, więc wywołuje je tylko service_role
-- (warstwa pobierania), a nie anon/authenticated przez API.

revoke execute on function public.agreguj_wazona_czasem(
    text, text, text[], text, text, text, integer, interval, timestamptz, timestamptz
) from public, anon, authenticated;

revoke execute on function public.przelicz_srednie(timestamptz, timestamptz)
    from public, anon, authenticated;

grant execute on function public.agreguj_wazona_czasem(
    text, text, text[], text, text, text, integer, interval, timestamptz, timestamptz
) to service_role;

grant execute on function public.przelicz_srednie(timestamptz, timestamptz)
    to service_role;
