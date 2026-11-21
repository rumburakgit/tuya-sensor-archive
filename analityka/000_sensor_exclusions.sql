-- ==================================================
-- Warstwa analityczna: wykluczanie błędnych odczytów
-- ==================================================
--
-- Wdrożenie: wkleić całość w Supabase -> SQL Editor i uruchomić
-- PRZED 002, 001 i 003 — ich funkcje czytają z widoku
-- sensor_readings_czyste, który musi już istnieć. Skrypt można
-- uruchamiać wielokrotnie (create ... if not exists, create or replace).
--
-- Opis: wykluczenia/SPECYFIKACJA.md. Surowe odczyty w sensor_readings
-- zostają nietknięte; wykluczenie to wpis w sensor_exclusions, a widok
-- sensor_readings_czyste pokazuje odczyty bez wykluczonych. Z widoku
-- czytają wszystkie obliczenia i wykresy.


-- --------------------------------------------------
-- 1. Okresy wykluczeń
-- --------------------------------------------------

create table if not exists public.sensor_exclusions (
    id bigint generated always as identity primary key,
    device_id text not null,
    code text,                               -- null = wszystkie pomiary czujnika
    od timestamptz not null,                 -- początek (włącznie)
    do_ timestamptz not null,                -- koniec (włącznie)
    powod text,
    aktywny boolean not null default true,   -- false = wykluczenie nie działa
    created_at timestamptz not null default now(),
    check (do_ >= od)
);

create index if not exists sensor_exclusions_device_idx
    on public.sensor_exclusions (device_id, od, do_);

alter table public.sensor_exclusions enable row level security;


-- --------------------------------------------------
-- 2. Odczyty bez wykluczonych
-- --------------------------------------------------

-- security_invoker: widok działa z uprawnieniami czytającego,
-- więc GRANT-y i RLS (np. dla anon) obowiązują jak przy tabeli.
create or replace view public.sensor_readings_czyste
with (security_invoker = true) as
select r.*
from public.sensor_readings r
where not exists (
    select 1
    from public.sensor_exclusions w
    where w.aktywny
      and w.device_id = r.device_id
      and (w.code is null or w.code = r.code)
      and r.measured_at between w.od and w.do_
);


-- --------------------------------------------------
-- 3. Przeliczenie po zmianie wykluczeń
-- --------------------------------------------------
--
-- Po dodaniu, zmianie albo usunięciu wykluczenia przelicza średnie
-- i okresy grzania w zakresie: pełne doby (Europe/Warsaw) okresu
-- wykluczenia z zapasem 2 dni z każdej strony (garb okresu grzania
-- trwa do 48 h). Średnie z zakresu są najpierw usuwane — gdy
-- wykluczenie obejmie ostatnie odczyty, okresy bez danych nie powstaną
-- od nowa, więc stare wartości nie mogą zostać.

create or replace function public.wykluczenia_przelicz()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    v_od timestamptz;
    v_do timestamptz;
begin
    if tg_op = 'INSERT' then
        v_od := new.od;
        v_do := new.do_;
    elsif tg_op = 'DELETE' then
        v_od := old.od;
        v_do := old.do_;
    else
        v_od := least(old.od, new.od);
        v_do := greatest(old.do_, new.do_);
    end if;

    v_od := (date_trunc('day', v_od at time zone 'Europe/Warsaw') - interval '2 days')
            at time zone 'Europe/Warsaw';
    v_do := (date_trunc('day', v_do at time zone 'Europe/Warsaw') + interval '3 days')
            at time zone 'Europe/Warsaw';

    delete from sensor_aggregates
    where poczatek >= v_od
      and poczatek < v_do;

    perform przelicz_srednie(v_od, v_do);

    return null;
end;
$$;

drop trigger if exists sensor_exclusions_przelicz on public.sensor_exclusions;
create trigger sensor_exclusions_przelicz
    after insert or update or delete on public.sensor_exclusions
    for each row execute function public.wykluczenia_przelicz();


-- --------------------------------------------------
-- 4. Uprawnienia
-- --------------------------------------------------

grant select, insert, update, delete on public.sensor_exclusions to service_role;
grant select on public.sensor_readings_czyste to service_role;

-- Wyzwalacz działa z uprawnieniami zapisującego (service_role), więc
-- service_role potrzebuje też delete na sensor_aggregates — nadaje je
-- 001_sensor_aggregates.sql (tabela powstaje dopiero tam).

revoke execute on function public.wykluczenia_przelicz()
    from public, anon, authenticated;
