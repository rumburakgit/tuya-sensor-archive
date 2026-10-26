# Warstwa analityczna

Średnie liczone są **w bazie** (funkcje SQL w Supabase) i zapisywane w tabeli
`sensor_aggregates`. Surowe pomiary w `sensor_readings` pozostają bez zmian.

## Wdrożenie

Zawartość `001_sensor_aggregates.sql` wkleić w Supabase → SQL Editor i uruchomić.
Skrypt można bezpiecznie uruchamiać wielokrotnie (np. po zmianie definicji funkcji).
Pierwsze wypełnienie tabeli: przycisk **Przelicz średnie** w `pobieranie/panel.py`.

## Zasady liczenia

Wszystkie średnie są **ważone czasem**: każdy odczyt obowiązuje do następnego,
ale nie dłużej niż ustalony czas ważności — przerwa w danych nie jest „zasypywana”
ostatnią wartością, tylko obniża `coverage_seconds`.

| Seria | Źródło | Okres | Ważność odczytu | Uwagi |
|---|---|---|---|---|
| `mieszkanie` | Tuya | 3h | 15 min | grupa: wszystkie czujniki oprócz „Temp Grzejnik”; temperatura i wilgotność |
| `<device_id>` (każdy czujnik z grupy) | Tuya | 3h | 15 min | te same średnie osobno dla każdego czujnika |
| `weather_eprz` | METAR/SPECI | 1d | 60 min | raporty co 30 min, toleruje jeden brakujący raport |

- Okresy liczone są w czasie polskim (`Europe/Warsaw`): doba 00:00–24:00, okresy 3h od północy
  (00–03, 03–06, …). W dniu zmiany czasu doba ma 23 lub 25 h, a okres 00–03 trwa 2 lub 4 h.
- Okresy powstają od pierwszego do ostatniego odczytu serii — bieżący, niepełny okres
  ma niepełne `coverage_seconds` i jest nadpisywany przy kolejnym przeliczeniu.
- Grupa: całki wszystkich czujników są sumowane (każda sekunda z każdego czujnika waży tyle samo),
  `coverage_seconds` to średnie pokrycie na czujnik grupy, `liczba_czujnikow` — ile czujników dało dane.
- Skład grup jest w tabeli `sensor_groups` (zmiana = `insert`/`delete` w tej tabeli, bez zmian w kodzie).
- Definicje serii (okresy, czasy ważności) są w funkcji `przelicz_srednie()`.

## Tabela `sensor_aggregates`

| Kolumna | Znaczenie |
|---|---|
| `seria` | `weather_eprz`, id czujnika lub nazwa grupy |
| `code` | `va_temperature`, `va_humidity` |
| `okres` | `3h`, `1d` |
| `metoda` | `srednia_wazona_czasem` |
| `poczatek`, `koniec` | granice okresu `[poczatek, koniec)` |
| `zrodlo` | `tuya`, `metar` |
| `typ_serii` | `czujnik`, `grupa` |
| `wartosc` | wynik (zaokrąglony do 0,01); `null`, gdy w okresie nie było danych |
| `min`, `max` | skrajne odczyty użyte w okresie |
| `unit` | jednostka |
| `liczba_probek` | liczba odczytów użytych do obliczeń |
| `liczba_czujnikow` | liczba czujników, które dały dane w okresie |
| `coverage_seconds` | sekundy okresu pokryte danymi (pokrycie % = `coverage_seconds / (koniec - poczatek)`) |
| `przeliczono_at` | czas ostatniego przeliczenia |

Klucz główny: `(seria, code, okres, metoda, poczatek)` — przeliczenie nadpisuje wynik (upsert).

## Kiedy liczone

Przeliczanie należy do warstwy pobierania (prezentacja tylko czyta):

- po każdej archiwizacji z `pobieranie/panel.py` automatycznie uruchamia się
  `przelicz_srednie.py --nowe-od <start archiwizacji>` — przelicza okresy od najstarszego
  nowo wstawionego pomiaru (i zawsze co najmniej ostatnią dobę, na wypadek korekt METAR COR);
- przycisk **Przelicz średnie** przelicza całą historię.

Funkcje mogą wywoływać tylko `service_role` (uprawnienia odebrane `anon`/`authenticated`).
Skrypt nadaje też jawnie uprawnienia do tabel (`GRANT`) dla `service_role` — w tym projekcie nowe tabele
ich nie dostają automatycznie, a `service_role` omija RLS, ale nie `GRANT`-y.
