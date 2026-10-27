-- Odczyt danych prezentacji kluczem anon (np. aplikacja w Streamlit Community Cloud).
--
-- Uruchamiane ręcznie w Supabase SQL Editor, po skryptach z analityka/.
-- Idempotentne (drop policy if exists + create policy).
--
-- anon dostaje wyłącznie SELECT na trzech tabelach, które czyta prezentacja.
-- Nie dostaje nic na sensor_archive_state (tam jest refresh token Tuya)
-- ani prawa zapisu gdziekolwiek. RLS jest już włączone na wszystkich tabelach.

alter table public.sensor_readings enable row level security;
alter table public.sensor_aggregates enable row level security;
alter table public.heating_periods enable row level security;

-- W tym projekcie nowe tabele nie dostają automatycznie GRANT-ów,
-- a polityka RLS bez GRANT-u nic nie daje.
grant select on public.sensor_readings to anon;
grant select on public.sensor_aggregates to anon;
grant select on public.heating_periods to anon;

drop policy if exists odczyt_anon on public.sensor_readings;
create policy odczyt_anon on public.sensor_readings
    for select to anon using (true);

drop policy if exists odczyt_anon on public.sensor_aggregates;
create policy odczyt_anon on public.sensor_aggregates
    for select to anon using (true);

drop policy if exists odczyt_anon on public.heating_periods;
create policy odczyt_anon on public.heating_periods
    for select to anon using (true);
