-- ==================================================
-- Warstwa analityczna: okresy grzania grzejnika
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić,
-- a potem (ponownie) 001_sensor_aggregates.sql — jego przelicz_srednie()
-- wywołuje wykryj_okresy_grzania(). Oba skrypty można uruchamiać
-- wielokrotnie (create ... if not exists, create or replace).
--
-- Okres grzania = czujnik przy grzejniku zaczyna rosnąć, a czujnik
-- odniesienia w tym samym pomieszczeniu rośnie wyraźnie wolniej
-- (dogrzewa się z opóźnieniem) albo wcale. Słońce ogrzewa całe
-- pomieszczenie, więc oba czujniki rosną wtedy podobnie i okres
-- nie zostaje wykryty. Okres trwa do maksimum temperatury przy
-- grzejniku (grzejnik przestał podnosić temperaturę).
--
-- Rodzaje okresów:
--   wyrazne — szybki wzrost przy grzejniku (kilka °C), pomieszczenie
--             dogrzewa się wolniej;
--   drobne  — krótkie włączenie: przy grzejniku wzrost o ułamek
--             stopnia, pomieszczenie w tym czasie nie rośnie.


-- --------------------------------------------------
-- 1. Wyniki
-- --------------------------------------------------

create table if not exists public.heating_periods (
    device_id text not null,              -- czujnik przy grzejniku
    device_id_odniesienia text not null,  -- czujnik w tym samym pomieszczeniu
    poczatek timestamptz not null,        -- ostatni odczyt przed wzrostem
    szczyt timestamptz not null,          -- maksimum temperatury przy grzejniku
    temp_poczatek numeric not null,
    temp_szczyt numeric not null,
    temp_odn_poczatek numeric,
    temp_odn_szczyt numeric,
    trwa boolean not null,                -- temperatura wciąż rosła na końcu danych
    przeliczono_at timestamptz not null default now(),
    rodzaj text not null default 'wyrazne', -- wyrazne, drobne
    primary key (device_id, poczatek)
);

-- Tabela z pierwszej wersji (bez rodzaju okresu).
alter table public.heating_periods
    add column if not exists rodzaj text not null default 'wyrazne';

alter table public.heating_periods enable row level security;


-- --------------------------------------------------
-- 2. Ostatni odczyt temperatury nie późniejszy niż p_ms
-- --------------------------------------------------

create or replace function public.ostatnia_temperatura(
    p_device_id text,
    p_ms bigint,
    out event_time_ms bigint,
    out wartosc numeric
)
language sql
stable
set search_path = public
as $$
select r.event_time_ms, r.value
from sensor_readings r
where r.device_id = p_device_id
  and r.code = 'va_temperature'
  and r.event_time_ms <= p_ms
order by r.event_time_ms desc
limit 1;
$$;


-- --------------------------------------------------
-- 3. Zapis jednego okresu (upsert)
-- --------------------------------------------------

create or replace function public.zapisz_okres_grzania(
    p_device_id text,
    p_device_id_odn text,
    p_rodzaj text,
    p_start_ms bigint,
    p_start_t numeric,
    p_szczyt_ms bigint,
    p_szczyt_t numeric,
    p_trwa boolean
)
returns void
language sql
set search_path = public
as $$
insert into heating_periods (
    device_id, device_id_odniesienia, poczatek, szczyt,
    temp_poczatek, temp_szczyt, temp_odn_poczatek, temp_odn_szczyt,
    trwa, przeliczono_at, rodzaj
)
values (
    p_device_id, p_device_id_odn,
    to_timestamp(p_start_ms / 1000.0),
    to_timestamp(p_szczyt_ms / 1000.0),
    p_start_t, p_szczyt_t,
    (select wartosc from ostatnia_temperatura(p_device_id_odn, p_start_ms)),
    (select wartosc from ostatnia_temperatura(p_device_id_odn, p_szczyt_ms)),
    p_trwa, now(), p_rodzaj
)
on conflict (device_id, poczatek) do update set
    device_id_odniesienia = excluded.device_id_odniesienia,
    szczyt = excluded.szczyt,
    temp_poczatek = excluded.temp_poczatek,
    temp_szczyt = excluded.temp_szczyt,
    temp_odn_poczatek = excluded.temp_odn_poczatek,
    temp_odn_szczyt = excluded.temp_odn_szczyt,
    trwa = excluded.trwa,
    przeliczono_at = excluded.przeliczono_at,
    rodzaj = excluded.rodzaj;
$$;


-- Zapisuje wzrost jako drobny okres, jeśli spełnia warunki
-- (opis w sekcji 4). p_od_przeliczenia: wyraźne okresy sprzed tej
-- chwili nie są przeliczane, więc liczą się także bez świeżego zapisu.
-- Zwraca 1, gdy zapisano okres, inaczej 0.

create or replace function public.zapisz_drobny_okres(
    p_device_id text,
    p_device_id_odn text,
    p_od_przeliczenia timestamptz,
    p_start_ms bigint,
    p_start_t numeric,
    p_szczyt_ms bigint,
    p_szczyt_t numeric,
    p_trwa boolean,
    p_min_przyrost numeric
)
returns integer
language plpgsql
set search_path = public
as $$
begin
    if p_szczyt_t - p_start_t < p_min_przyrost then
        return 0;
    end if;

    -- Pomieszczenie w tym czasie nie może rosnąć.
    if (select wartosc from ostatnia_temperatura(p_device_id_odn, p_szczyt_ms))
       > (select wartosc from ostatnia_temperatura(p_device_id_odn, p_start_ms))
       or (select wartosc from ostatnia_temperatura(p_device_id_odn, p_start_ms)) is null then
        return 0;
    end if;

    if exists (
        select 1
        from heating_periods h
        where h.device_id = p_device_id
          and h.rodzaj = 'wyrazne'
          and (h.przeliczono_at = now() or h.poczatek < p_od_przeliczenia)
          and h.poczatek <= to_timestamp(p_szczyt_ms / 1000.0)
          and h.szczyt >= to_timestamp(p_start_ms / 1000.0)
    ) then
        return 0;
    end if;

    perform zapisz_okres_grzania(
        p_device_id, p_device_id_odn, 'drobne',
        p_start_ms, p_start_t, p_szczyt_ms, p_szczyt_t, p_trwa
    );
    return 1;
end;
$$;


-- --------------------------------------------------
-- 4. Wykrywanie okresów grzania
-- --------------------------------------------------
--
-- Wyraźne: dla każdego odczytu czujnika przy grzejniku liczy prędkości
-- wzrostu (°C/h) obu czujników w oknie p_okno wstecz. Start okresu, gdy:
--     prędkość grzejnika >= p_prog
--     prędkość grzejnika - prędkość odniesienia >= p_prog_przewagi
-- Koniec, gdy temperatura przy grzejniku przestaje rosnąć w oknie
-- (albo przy przerwie w danych). Okresy z przyrostem mniejszym niż
-- p_min_przyrost są pomijane.
--
-- Drobne: przebieg temperatury przy grzejniku dzielony jest na wzrosty
-- od dołka do szczytu (kierunek zmienia się dopiero po zmianie
-- o p_drobne_zawracanie, więc pojedyncze wahnięcia o 0,1 °C nie
-- przerywają wzrostu). Wzrost jest drobnym okresem, gdy:
--     przyrost przy grzejniku >= p_drobne_przyrost
--     temperatura odniesienia w szczycie <= temperatura na początku
--     nie nachodzi na wyraźny okres
--
-- Okresy w zakresie są wykrywane od nowa (z marginesem 6 h
-- i od początku okresu, który nachodzi na zakres).
-- Zwraca liczbę zapisanych okresów.

-- Pierwsza wersja (bez wykrywania drobnych okresów) miała inną sygnaturę.
drop function if exists public.wykryj_okresy_grzania(
    text, text, timestamptz, timestamptz, interval, numeric, numeric, numeric
);

create or replace function public.wykryj_okresy_grzania(
    p_device_id text,
    p_device_id_odn text,
    p_od timestamptz default null,
    p_do timestamptz default now(),
    p_okno interval default interval '30 minutes',
    p_prog numeric default 1.5,
    p_prog_przewagi numeric default 1.0,
    p_min_przyrost numeric default 2.0,
    p_drobne_przyrost numeric default 0.5,
    p_drobne_zawracanie numeric default 0.2
)
returns integer
language plpgsql
set search_path = public
as $$
declare
    v_okno_ms bigint := (extract(epoch from p_okno) * 1000)::bigint;
    v_od timestamptz;
    r record;
    v_godz numeric;
    v_pr_g numeric;
    v_pr_odn numeric;
    v_w_okresie boolean := false;
    v_start_ms bigint;
    v_start_t numeric;
    v_max_ms bigint;
    v_max_t numeric;
    v_rosnie boolean := false;
    v_poprzedni_ms bigint;
    v_liczba integer := 0;
begin
    v_od := coalesce(p_od, '-infinity'::timestamptz) - interval '6 hours';

    -- Okres zaczęty przed zakresem wykrywamy od jego początku.
    select least(v_od, min(poczatek))
    into v_od
    from heating_periods
    where device_id = p_device_id
      and szczyt >= v_od - p_okno
      and poczatek < p_do;

    for r in
        select
            g.event_time_ms as t,
            g.value as tg,
            g0.event_time_ms as t0,
            g0.wartosc as tg0,
            k.wartosc as tk,
            k0.wartosc as tk0
        from sensor_readings g
        cross join lateral ostatnia_temperatura(p_device_id, g.event_time_ms - v_okno_ms) g0
        cross join lateral ostatnia_temperatura(p_device_id_odn, g.event_time_ms) k
        cross join lateral ostatnia_temperatura(p_device_id_odn, g0.event_time_ms) k0
        where g.device_id = p_device_id
          and g.code = 'va_temperature'
          and g.measured_at >= v_od
          and g.measured_at < p_do
        order by g.event_time_ms
    loop
        v_godz := (r.t - r.t0) / 3600000.0;

        -- Przerwa w danych: okno sięga zbyt daleko wstecz.
        if r.t - r.t0 > 2 * v_okno_ms then
            v_pr_g := null;
        else
            v_pr_g := (r.tg - r.tg0) / v_godz;
            v_pr_odn := (r.tk - r.tk0) / v_godz;
        end if;

        if not v_w_okresie then
            if v_pr_g >= p_prog and v_pr_g - v_pr_odn >= p_prog_przewagi then
                v_w_okresie := true;

                -- Początek: najniższy (ostatni z równych) odczyt w oknie.
                select s.event_time_ms, s.value
                into v_start_ms, v_start_t
                from sensor_readings s
                where s.device_id = p_device_id
                  and s.code = 'va_temperature'
                  and s.event_time_ms between r.t0 and r.t
                order by s.value, s.event_time_ms desc
                limit 1;

                v_max_ms := r.t;
                v_max_t := r.tg;
            end if;
            continue;
        end if;

        if r.tg > v_max_t then
            v_max_ms := r.t;
            v_max_t := r.tg;
        end if;

        if v_pr_g is null or v_pr_g <= 0 then
            v_w_okresie := false;

            if v_max_t - v_start_t >= p_min_przyrost then
                perform zapisz_okres_grzania(
                    p_device_id, p_device_id_odn, 'wyrazne',
                    v_start_ms, v_start_t, v_max_ms, v_max_t, false
                );
                v_liczba := v_liczba + 1;
            end if;
        end if;
    end loop;

    -- Okres niezakończony na końcu danych: zapisany z trwa = true,
    -- przy następnym przeliczeniu zostanie domknięty.
    if v_w_okresie and v_max_t - v_start_t >= p_min_przyrost then
        perform zapisz_okres_grzania(
            p_device_id, p_device_id_odn, 'wyrazne',
            v_start_ms, v_start_t, v_max_ms, v_max_t, true
        );
        v_liczba := v_liczba + 1;
    end if;

    -- Drobne okresy: wzrosty od dołka do szczytu.
    v_start_ms := null;

    for r in
        select g.event_time_ms as t, g.value as tg
        from sensor_readings g
        where g.device_id = p_device_id
          and g.code = 'va_temperature'
          and g.measured_at >= v_od
          and g.measured_at < p_do
        order by g.event_time_ms
    loop
        -- Początek danych albo przerwa w danych: szukamy od nowa.
        if v_start_ms is null or r.t - v_poprzedni_ms > 2 * v_okno_ms then
            v_rosnie := false;
            v_start_ms := r.t;
            v_start_t := r.tg;
        end if;
        v_poprzedni_ms := r.t;

        if not v_rosnie then
            if r.tg <= v_start_t then
                v_start_ms := r.t;
                v_start_t := r.tg;
            elsif r.tg >= v_start_t + p_drobne_zawracanie then
                v_rosnie := true;
                v_max_ms := r.t;
                v_max_t := r.tg;
            end if;
            continue;
        end if;

        if r.tg > v_max_t then
            v_max_ms := r.t;
            v_max_t := r.tg;
        elsif r.tg <= v_max_t - p_drobne_zawracanie then
            v_liczba := v_liczba + zapisz_drobny_okres(
                p_device_id, p_device_id_odn, v_od - p_okno,
                v_start_ms, v_start_t, v_max_ms, v_max_t, false, p_drobne_przyrost
            );
            v_rosnie := false;
            v_start_ms := r.t;
            v_start_t := r.tg;
        end if;
    end loop;

    if v_rosnie then
        v_liczba := v_liczba + zapisz_drobny_okres(
            p_device_id, p_device_id_odn, v_od - p_okno,
            v_start_ms, v_start_t, v_max_ms, v_max_t, true, p_drobne_przyrost
        );
    end if;

    -- Okresy z przeliczanego zakresu, których tym razem nie wykryto
    -- (now() jest stałe w transakcji, więc nowe wiersze mają je w przeliczono_at).
    delete from heating_periods
    where device_id = p_device_id
      and poczatek >= v_od - p_okno
      and poczatek < p_do
      and przeliczono_at <> now();

    return v_liczba;
end;
$$;


-- --------------------------------------------------
-- 5. Uprawnienia
-- --------------------------------------------------

grant select, insert, update, delete on public.heating_periods to service_role;

revoke execute on function public.ostatnia_temperatura(text, bigint)
    from public, anon, authenticated;

revoke execute on function public.zapisz_okres_grzania(
    text, text, text, bigint, numeric, bigint, numeric, boolean
) from public, anon, authenticated;

revoke execute on function public.zapisz_drobny_okres(
    text, text, timestamptz, bigint, numeric, bigint, numeric, boolean, numeric
) from public, anon, authenticated;

revoke execute on function public.wykryj_okresy_grzania(
    text, text, timestamptz, timestamptz, interval, numeric, numeric, numeric, numeric, numeric
) from public, anon, authenticated;

grant execute on function public.ostatnia_temperatura(text, bigint) to service_role;

grant execute on function public.zapisz_okres_grzania(
    text, text, text, bigint, numeric, bigint, numeric, boolean
) to service_role;

grant execute on function public.zapisz_drobny_okres(
    text, text, timestamptz, bigint, numeric, bigint, numeric, boolean, numeric
) to service_role;

grant execute on function public.wykryj_okresy_grzania(
    text, text, timestamptz, timestamptz, interval, numeric, numeric, numeric, numeric, numeric
) to service_role;
