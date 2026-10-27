-- ==================================================
-- Warstwa analityczna: okresy grzania grzejnika
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić,
-- a potem (ponownie) 001_sensor_aggregates.sql — jego przelicz_srednie()
-- wywołuje wykryj_okresy_grzania(). Oba skrypty można uruchamiać
-- wielokrotnie (create ... if not exists, create or replace).
--
-- Okres grzania = czujnik przy grzejniku zaczyna szybko rosnąć,
-- a czujnik odniesienia w tym samym pomieszczeniu rośnie wyraźnie
-- wolniej (dogrzewa się z opóźnieniem). Słońce ogrzewa całe
-- pomieszczenie, więc oba czujniki rosną wtedy wolno i podobnie
-- i okres nie zostaje wykryty. Okres trwa do maksimum temperatury
-- przy grzejniku (grzejnik przestał podnosić temperaturę).


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
    primary key (device_id, poczatek)
);

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
-- 3. Wykrywanie okresów grzania
-- --------------------------------------------------
--
-- Dla każdego odczytu czujnika przy grzejniku liczy prędkości wzrostu
-- (°C/h) obu czujników w oknie p_okno wstecz. Start okresu, gdy:
--     prędkość grzejnika >= p_prog
--     prędkość grzejnika - prędkość odniesienia >= p_prog_przewagi
-- Koniec, gdy temperatura przy grzejniku przestaje rosnąć w oknie
-- (albo przy przerwie w danych). Okresy z przyrostem mniejszym niż
-- p_min_przyrost są pomijane.
--
-- Okresy w zakresie są wykrywane od nowa (z marginesem 6 h
-- i od początku okresu, który nachodzi na zakres).
-- Zwraca liczbę zapisanych okresów.

create or replace function public.wykryj_okresy_grzania(
    p_device_id text,
    p_device_id_odn text,
    p_od timestamptz default null,
    p_do timestamptz default now(),
    p_okno interval default interval '30 minutes',
    p_prog numeric default 1.5,
    p_prog_przewagi numeric default 1.0,
    p_min_przyrost numeric default 2.0
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
                insert into heating_periods
                select
                    p_device_id, p_device_id_odn,
                    to_timestamp(v_start_ms / 1000.0),
                    to_timestamp(v_max_ms / 1000.0),
                    v_start_t, v_max_t,
                    (select wartosc from ostatnia_temperatura(p_device_id_odn, v_start_ms)),
                    (select wartosc from ostatnia_temperatura(p_device_id_odn, v_max_ms)),
                    false, now()
                on conflict (device_id, poczatek) do update set
                    device_id_odniesienia = excluded.device_id_odniesienia,
                    szczyt = excluded.szczyt,
                    temp_poczatek = excluded.temp_poczatek,
                    temp_szczyt = excluded.temp_szczyt,
                    temp_odn_poczatek = excluded.temp_odn_poczatek,
                    temp_odn_szczyt = excluded.temp_odn_szczyt,
                    trwa = excluded.trwa,
                    przeliczono_at = excluded.przeliczono_at;

                v_liczba := v_liczba + 1;
            end if;
        end if;
    end loop;

    -- Okres niezakończony na końcu danych: zapisany z trwa = true,
    -- przy następnym przeliczeniu zostanie domknięty.
    if v_w_okresie and v_max_t - v_start_t >= p_min_przyrost then
        insert into heating_periods
        select
            p_device_id, p_device_id_odn,
            to_timestamp(v_start_ms / 1000.0),
            to_timestamp(v_max_ms / 1000.0),
            v_start_t, v_max_t,
            (select wartosc from ostatnia_temperatura(p_device_id_odn, v_start_ms)),
            (select wartosc from ostatnia_temperatura(p_device_id_odn, v_max_ms)),
            true, now()
        on conflict (device_id, poczatek) do update set
            device_id_odniesienia = excluded.device_id_odniesienia,
            szczyt = excluded.szczyt,
            temp_poczatek = excluded.temp_poczatek,
            temp_szczyt = excluded.temp_szczyt,
            temp_odn_poczatek = excluded.temp_odn_poczatek,
            temp_odn_szczyt = excluded.temp_odn_szczyt,
            trwa = excluded.trwa,
            przeliczono_at = excluded.przeliczono_at;

        v_liczba := v_liczba + 1;
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
-- 4. Uprawnienia
-- --------------------------------------------------

grant select, insert, update, delete on public.heating_periods to service_role;

revoke execute on function public.ostatnia_temperatura(text, bigint)
    from public, anon, authenticated;

revoke execute on function public.wykryj_okresy_grzania(
    text, text, timestamptz, timestamptz, interval, numeric, numeric, numeric
) from public, anon, authenticated;

grant execute on function public.ostatnia_temperatura(text, bigint) to service_role;

grant execute on function public.wykryj_okresy_grzania(
    text, text, timestamptz, timestamptz, interval, numeric, numeric, numeric
) to service_role;
