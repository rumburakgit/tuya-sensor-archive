# Wykluczanie błędnych odczytów — specyfikacja

Stan: **zaimplementowane** (`analityka/000_sensor_exclusions.sql`, `prezentacja/app_wykluczenia.py`, port 5004).

## Cel

Czujnik ze słabą baterią potrafi podawać bzdury (np. „Temp żółty pok” 20.11 o 17:58 pokazał
35,2 °C przy realnych 8–9 °C). Użytkownik chce wpisać **okres błędnych odczytów dla wybranego
czujnika**, a program ma ten okres zapamiętać i pomijać go **wszędzie**: na wykresach,
w średnich (`sensor_aggregates`), w wykrywaniu okresów grzania (`heating_periods`)
i w szacowaniu zużycia gazu.

## Zasady

1. **Surowe dane zostają nietknięte.** Nic nie jest kasowane z `sensor_readings`. Powody:
   - `archive.py` pobiera z zakładką 30 min (a przy pierwszym uruchomieniu 7 dni wstecz),
     więc skasowany wiersz i tak wróciłby przy następnym archiwizowaniu,
   - pomyłkę przy wpisywaniu okresu da się cofnąć (usunięcie/wyłączenie wykluczenia).
2. **Wykluczenie to osobny wpis w nowej tabeli** `sensor_exclusions`.
3. **Filtrowanie odbywa się w jednym miejscu — w Postgresie**, przez widok
   `sensor_readings_czyste` = `sensor_readings` bez odczytów objętych aktywnym wykluczeniem.
   Wszyscy odbiorcy danych (funkcje SQL i aplikacje prezentacji) czytają z widoku zamiast
   z tabeli. Dzięki temu nie ma ryzyka, że jedno miejsce filtruje, a inne nie.
4. **Przeliczenie po zmianie wykluczeń robi baza** (wyzwalacz na `sensor_exclusions`), nie
   aplikacja prezentacji — zgodnie z zasadą, że przeliczanie nie należy do warstwy prezentacji.

## Tabela `sensor_exclusions`

| kolumna | typ | opis |
|---|---|---|
| `id` | `bigint generated always as identity` PK | |
| `device_id` | `text not null` | czujnik (jak w `sensor_readings`) |
| `code` | `text null` | `va_temperature` / `va_humidity`; **`null` = wszystkie pomiary czujnika** (domyślnie — padająca bateria psuje zwykle oba) |
| `od` | `timestamptz not null` | początek okresu (włącznie) |
| `do_` | `timestamptz not null` | koniec okresu (włącznie), `check (do_ >= od)` |
| `powod` | `text null` | np. „słaba bateria” |
| `aktywny` | `boolean not null default true` | wyłączone wykluczenie nie działa, ale zostaje w historii |
| `created_at` | `timestamptz not null default now()` | |

Indeks na `(device_id, od, do_)`. Okresy tego samego czujnika mogą na siebie nachodzić (nic złego
się nie dzieje — odczyt jest po prostu wykluczony).

Granice wpisuje się w czasie lokalnym (Europe/Warsaw), zapisywane są jako `timestamptz`.
Żeby wykluczyć jeden odczyt, wystarczy krótki okres wokół niego, np. 20.11 17:55–18:00.

## Widok `sensor_readings_czyste`

```sql
create or replace view public.sensor_readings_czyste
with (security_invoker = true) as
select r.*
from sensor_readings r
where not exists (
    select 1 from sensor_exclusions w
    where w.aktywny
      and w.device_id = r.device_id
      and (w.code is null or w.code = r.code)
      and r.measured_at between w.od and w.do_
);
```

`security_invoker` — widok działa z uprawnieniami czytającego, więc RLS/GRANT-y dla `anon`
(Streamlit) obowiązują normalnie; `anon` dostaje `select` na widoku i na `sensor_exclusions`
(dopisane w `prezentacja/odczyt_anon.sql`).

## Co się zmienia w obliczeniach

Wszędzie `sensor_readings` → `sensor_readings_czyste` w miejscach **odczytu do obliczeń**
(grant `delete` na `sensor_aggregates` dla wyzwalacza nadaje 001):

- `analityka/001_sensor_aggregates.sql` — `agreguj_wazona_czasem()` (granice serii i odczyty),
- `analityka/002_heating_periods.sql` — `ostatnia_temperatura()`, `wykryj_okresy_grzania()`,
- `analityka/003_gas_usage.sql` — `cechy_okresow_grzania()`, `temperatura_grupy()`.

Bez zmian: `archive.py`, `archive_metar.py` (zapisują surowe dane) i `przelicz_srednie.py`
(szuka nowych wierszy po `inserted_at` — dotyczy surowych danych).

**Skutek dla średniej ważonej czasem:** wykluczony odczyt znika, więc poprzedni odczyt obowiązuje
dalej, ale jak zwykle najwyżej 15 min (Tuya) / 60 min (METAR). Dłuższy wykluczony okres = luka
w danych tego czujnika; średnia grupy `mieszkanie` liczy się wtedy z pozostałych czujników
(tak jak przy zwykłym braku danych). Nic nie trzeba dodatkowo obsługiwać.

## Przeliczenie po zmianie wykluczeń

Wyzwalacz `after insert or update or delete` na `sensor_exclusions` (na poziomie wiersza, ze
starym i nowym zakresem) wywołuje `przelicz_srednie(p_od, p_do)` dla zakresu rozszerzonego tak,
żeby objąć wszystko, na co odczyt mógł wpłynąć:

- **od** = początek doby (Europe/Warsaw) zawierającej `od` − 2 dni,
- **do** = koniec doby zawierającej `do_` + 2 dni.

Zapas 2 dni wynika z okresów grzania (garb do 48 h) i średnich dobowych METAR. Przed
przeliczeniem średnie z zakresu są usuwane: gdy wykluczenie obejmie ostatnie odczyty czujnika,
okresy bez danych nie powstają od nowa, więc stare wartości nie mogą zostać.

Sprawdzone na lokalnym Postgresie 16 na kopii danych (105 tys. odczytów): bez wykluczeń wyniki
(średnie, okresy grzania, cechy gazu) są identyczne jak przed zmianą; po dodaniu, zmianie,
wyłączeniu i usunięciu wykluczenia wynik wyzwalacza = pełne przeliczenie od zera; średnie
z wykluczeniem zgadzają się z niezależnym obliczeniem w Pythonie. Wyzwalacz trwa ok. 1 s.

## Interfejs do wpisywania — `prezentacja/app_wykluczenia.py` (port 5004)

Flask, klucz `service_role` (zapisuje do `sensor_exclusions`, jak `app_gaz.py` do
`gas_calibration`). Dopisana do listy `PREZENTACJE` w `prezentacja/panel.py` i do
`pakiet/build.sh`.

Strona:

1. **Podgląd surowych danych** — wybór czujnika i zakresu dat (domyślnie ostatnie 7 dni), wykres
   ECharts z odczytami z `sensor_readings` (surowymi!). Odczyty już wykluczone — szare, okresy
   wykluczeń — szare pasy. Dzięki temu widać skok i dokładny czas.
2. **Formularz** — czujnik, pomiar (temperatura / wilgotność / **oba**, domyślnie oba), od, do,
   powód. Przed zapisem podgląd: „wykluczonych zostanie N odczytów (min … max …)” (liczony
   z odczytów pokazanych na wykresie). Przeciągnięcie myszą po wykresie wypełnia pola od/do.
   Koniec wpisany bez sekund obejmuje całą minutę.
3. **Lista wykluczeń** — czujnik (link do podglądu okresu), pomiar, okres, powód;
   przyciski „wyłącz/włącz” i „usuń”. Po dodaniu komunikat podaje liczbę wykluczonych odczytów.

## Prezentacja

- `app_archive.py` (`pobierz_wszystkie_pomiary`, kafelki z ostatnim odczytem), więc też
  `app_echarts.py` i `streamlit_app.py` — czytają z `sensor_readings_czyste`. Wykluczone odczyty
  po prostu nie są rysowane.
- `app_gaz.py` — korzysta z funkcji SQL, więc zmiana przychodzi sama.

## Wdrożenie

1. W Supabase SQL Editor, w tej kolejności: `analityka/000_sensor_exclusions.sql` (tabela, widok,
   wyzwalacz; musi być pierwszy, bo funkcje z 001–003 czytają widok), potem ponownie
   `002_heating_periods.sql`, `001_sensor_aggregates.sql`, `003_gas_usage.sql`
   i `prezentacja/odczyt_anon.sql`.
2. Bez wykluczeń wyniki się nie zmieniają, więc pełne przeliczenie nie jest potrzebne.
3. Pierwsze wykluczenie: „Temp żółty pok”, 20.11 ok. 17:58.

## Decyzje

1. **Bez warunku na wartość** — wykluczany jest cały okres. Próg (np. „w tym okresie tylko
   odczyty > 25 °C”) można dodać później jako kolumny `min_wartosc`/`max_wartosc`.
2. **Szare pasy wykluczeń tylko na stronie wykluczeń** — na głównych wykresach jest kilka
   czujników naraz, więc pas byłby niejednoznaczny. Wykluczone odczyty po prostu nie są tam rysowane.
3. **Bez automatycznego wykrywania** podejrzanych odczytów (np. skok > 10 °C względem sąsiednich
   odczytów z podpowiedzią „dodać wykluczenie?”) — możliwe później.
