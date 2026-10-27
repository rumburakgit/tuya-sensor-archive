# Warstwa analityczna

Średnie liczone są **w bazie** (funkcje SQL w Supabase) i zapisywane w tabeli
`sensor_aggregates`, a wykryte okresy grzania — w tabeli `heating_periods`.
Surowe pomiary w `sensor_readings` pozostają bez zmian.

## Wdrożenie

Zawartość `002_heating_periods.sql`, a potem `001_sensor_aggregates.sql` wkleić w Supabase → SQL Editor
i uruchomić (`przelicz_srednie()` z 001 wywołuje funkcję wykrywania z 002).
Skrypty można bezpiecznie uruchamiać wielokrotnie (np. po zmianie definicji funkcji).
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

### Serie pochodne (różnice)

| Seria | Metoda | Okres | Wzór |
|---|---|---|---|
| `mieszkanie-weather_eprz` | `roznica_srednich` | 3h | średnia 3h `mieszkanie` − średnia dobowa `weather_eprz` z doby zawierającej ten okres (temperatura; 8 wyników na dobę) |

- Liczone funkcją `oblicz_roznice()` z już zapisanych średnich, więc w `przelicz_srednie()` muszą być po nich.
- Okresy różnicy = okresy średniej 3h mieszkania. Gdy brakuje średniej dobowej Jasionki, `wartosc = null` (na wykresie przerwa).
- `coverage_seconds` = pokrycie okresu 3h mieszkania × ułamek pokrycia doby Jasionki
  (bieżąca, niepełna doba ma średnią dobową z dotychczasowych raportów).
- `typ_serii = 'roznica'`, `zrodlo` = `tuya-metar`.
- Kolejne wyliczenia dodaje się tak samo: nowa funkcja SQL (lub wywołanie istniejącej) w `przelicz_srednie()`,
  wynik jako nowa seria w `sensor_aggregates`; prezentacja tylko ją rysuje.

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
| `metoda` | `srednia_wazona_czasem`, `roznica_srednich` |
| `poczatek`, `koniec` | granice okresu `[poczatek, koniec)` |
| `zrodlo` | `tuya`, `metar` |
| `typ_serii` | `czujnik`, `grupa`, `roznica` |
| `wartosc` | wynik (zaokrąglony do 0,01); `null`, gdy w okresie nie było danych |
| `min`, `max` | skrajne odczyty użyte w okresie |
| `unit` | jednostka |
| `liczba_probek` | liczba odczytów użytych do obliczeń |
| `liczba_czujnikow` | liczba czujników, które dały dane w okresie |
| `coverage_seconds` | sekundy okresu pokryte danymi (pokrycie % = `coverage_seconds / (koniec - poczatek)`) |
| `przeliczono_at` | czas ostatniego przeliczenia |

Klucz główny: `(seria, code, okres, metoda, poczatek)` — przeliczenie nadpisuje wynik (upsert).

## Okresy grzania (`heating_periods`)

Wykrywa chwile włączenia grzejnika w kuchni: czujnik **Temp Grzejnik** (`bf333171b6295a9253wx0y`)
zaczyna szybko rosnąć, a **Temp Kuchnias** (`bf09f3b9302d16a74froza`) rośnie wyraźnie wolniej
i dołącza z opóźnieniem. Słońce ogrzewa całe pomieszczenie — oba czujniki rosną wtedy wolno
i podobnie, więc taki wzrost nie jest okresem grzania.

Funkcja `wykryj_okresy_grzania()` dla każdego odczytu czujnika przy grzejniku liczy prędkość wzrostu
(°C/h) obu czujników w oknie 30 min wstecz:

| Warunek | Domyślnie | Parametr |
|---|---|---|
| start: prędkość grzejnika ≥ | 1,5 °C/h | `p_prog` |
| start: prędkość grzejnika − prędkość kuchni ≥ | 1,0 °C/h | `p_prog_przewagi` |
| koniec: prędkość grzejnika ≤ 0 (przestał rosnąć) albo przerwa w danych > 2 × okno | — | `p_okno` |
| okres zapisywany, gdy przyrost przy grzejniku ≥ | 2,0 °C | `p_min_przyrost` |

- `poczatek` = najniższy odczyt grzejnika w oknie, w którym spełniono warunek startu (ostatni odczyt przed wzrostem),
  `szczyt` = maksimum temperatury przy grzejniku w okresie.
- Okres niezakończony na końcu danych ma `trwa = true` i jest domykany przy następnym przeliczeniu.
- Przeliczenie od `p_od` wykrywa okresy od nowa z marginesem 6 h (i od początku okresu nachodzącego na zakres);
  okresy z tego zakresu, których nie wykryto ponownie, są usuwane.
- Parę czujników ustawia wywołanie w `przelicz_srednie()`; wynik nie wlicza się do zwracanej liczby wierszy.

| Kolumna | Znaczenie |
|---|---|
| `device_id` | czujnik przy grzejniku |
| `device_id_odniesienia` | czujnik w tym samym pomieszczeniu |
| `poczatek`, `szczyt` | włączenie grzejnika i maksimum temperatury przy nim |
| `temp_poczatek`, `temp_szczyt` | temperatura przy grzejniku w tych chwilach |
| `temp_odn_poczatek`, `temp_odn_szczyt` | temperatura czujnika odniesienia w tych chwilach |
| `trwa` | temperatura wciąż rosła na końcu danych |
| `przeliczono_at` | czas ostatniego przeliczenia |

Klucz główny: `(device_id, poczatek)`.

## Kiedy liczone

Przeliczanie należy do warstwy pobierania (prezentacja tylko czyta):

- po każdej archiwizacji z `pobieranie/panel.py` automatycznie uruchamia się
  `przelicz_srednie.py --nowe-od <start archiwizacji>` — przelicza okresy od najstarszego
  nowo wstawionego pomiaru (i zawsze co najmniej ostatnią dobę, na wypadek korekt METAR COR);
- przycisk **Przelicz średnie** przelicza całą historię.

Funkcje mogą wywoływać tylko `service_role` (uprawnienia odebrane `anon`/`authenticated`).
Skrypt nadaje też jawnie uprawnienia do tabel (`GRANT`) dla `service_role` — w tym projekcie nowe tabele
ich nie dostają automatycznie, a `service_role` omija RLS, ale nie `GRANT`-y.
