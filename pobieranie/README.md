# Tuya Sensor Archive

Archiwizacja odczytów temperatury i wilgotności z czujników Tuya.

## Założenia

- Pobieranie nowych pomiarów co 15 minut.
- Zapis historii w Supabase (PostgreSQL).
- Automatyczne uruchamianie przez GitHub Actions.
- Ochrona przed duplikatami i możliwość nadrabiania zaległości.
- Sekrety przechowywane poza repozytorium.

## Uruchomienie lokalne

Wszystkie pliki warstwy pobierającej leżą w katalogu `pobieranie/`, razem z plikiem `.env`.
Środowisko wirtualne `.venv` znajduje się w katalogu głównym repozytorium.
Polecenia poniżej zakładają uruchamianie z katalogu głównego.
Dashboard z Supabase (`app_archive.py`) przeniesiono do warstwy prezentacji — zob. `prezentacja/README.md`.

```bash
./.venv/bin/python3 pobieranie/panel.py          # panel GUI (archiwizacja ręczna)
./.venv/bin/python3 pobieranie/app.py            # dashboard na żywo z Tuya, port 5000
```

## `panel.py` — ręczne uruchamianie archiwizacji

Małe okno (tkinter) z przyciskami:

- **Archiwizuj Tuya** — uruchamia `archive.py`,
- **Archiwizuj METAR** — uruchamia `archive_metar.py`,
- **Przelicz średnie** — uruchamia `przelicz_srednie.py` (cała historia).

Po każdej archiwizacji panel sam uruchamia `przelicz_srednie.py --nowe-od ...`,
który przelicza średnie dla nowo pobranych danych (zob. `analityka/README.md`).

Wyjście skryptu pojawia się na bieżąco w oknie konsoli. Na raz działa tylko jeden skrypt
(przyciski są blokowane do jego zakończenia), a na końcu wyświetlany jest wynik
(sukces albo kod błędu). Zamknięcie okna w trakcie pracy pyta o przerwanie skryptu.

## `archive.py` — archiwizacja pomiarów z czujników Tuya

Skrypt `archive.py` odpowiada za automatyczne pobieranie historycznych pomiarów temperatury i wilgotności z czujników Tuya oraz zapisywanie ich w bazie danych Supabase.

### Główne funkcje

- **Pobieranie danych z Tuya API** — odczytuje logi pomiarowe z urządzeń skonfigurowanych w module `czujniki.py`.
- **Obsługa wielu czujników** — umożliwia archiwizację pomiarów ze wszystkich urządzeń zdefiniowanych w konfiguracji.
- **Pobieranie kolejnych stron wyników** — obsługuje paginację za pomocą `next_row_key` i `start_row_key`, dzięki czemu może pobierać więcej logów niż mieści się na jednej stronie odpowiedzi API.
- **Przyrostowa archiwizacja** — zapamiętuje stan pobierania dla każdego czujnika i wykorzystuje go przy kolejnych uruchomieniach, aby ograniczyć zakres ponownie pobieranej historii.
- **Obsługa nakładania się zakresów czasu** — ponownie pobiera część wcześniejszych logów, aby ograniczyć ryzyko pominięcia danych. Duplikaty są eliminowane podczas zapisu do Supabase.
- **Zapisywanie danych w Supabase** — zapisuje temperaturę i wilgotność w tabeli `sensor_readings`, wykorzystując operację upsert i klucz identyfikujący pomiar.
- **Przechowywanie stanu archiwizacji** — zapisuje postęp i dane potrzebne do kolejnych uruchomień w tabeli `sensor_archive_state`.
- **Obsługa tokenów Tuya** — korzysta z mechanizmu odświeżania tokenów i przechowuje token odświeżający w stanie archiwizacji w Supabase.
- **Kontrola błędów** — raportuje problemy z pobieraniem lub zapisem danych. Jeśli archiwizacja któregoś czujnika się nie powiedzie, skrypt kończy działanie błędem, aby nie zgłaszać fałszywego sukcesu całego zadania.

### Przebieg działania

1. Odczytanie konfiguracji i danych dostępowych.
2. Odczytanie zapisanego stanu archiwizacji oraz tokenu Tuya.
3. Ustalenie zakresu czasu, z którego należy pobrać logi.
4. Pobranie logów z Tuya API, w razie potrzeby na kolejnych stronach.
5. Przekształcenie danych i zapis pomiarów do Supabase.
6. Aktualizacja stanu danego czujnika dopiero po poprawnym pobraniu i zapisaniu całego zakresu.
7. Wyświetlenie podsumowania zakończonej archiwizacji.

### Konfiguracja

Dane dostępowe przechowywane są w zmiennych środowiskowych:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TUYA_CLIENT_ID`
- `TUYA_CLIENT_SECRET`
- `TUYA_ACCESS_TOKEN`
- `TUYA_REFRESH_TOKEN`

Lokalnie zmienne te mogą być przechowywane w pliku `.env`. Plik ten nie powinien być dodawany do repozytorium. W GitHub Actions dane dostępowe przekazywane są przez GitHub Actions Secrets.

Listę czujników i funkcje komunikacji z Tuya API obsługuje moduł `czujniki.py`.

### Uruchamianie

Lokalnie, w aktywnym środowisku wirtualnym:

```bash
python archive.py
```

W projekcie uruchamianym z istniejącym środowiskiem wirtualnym można użyć:

```bash
./.venv/bin/python3 archive.py
```

Skrypt może być uruchamiany ręcznie lub automatycznie przez GitHub Actions zgodnie z harmonogramem zapisanym w `.github/workflows/archive.yml`.

### Baza danych

Skrypt korzysta z dwóch tabel Supabase:

- `sensor_readings` — przechowuje pomiary wraz z identyfikatorem czujnika, nazwą urządzenia, czasem pomiaru, kodem parametru, wartością i jednostką.
- `sensor_archive_state` — przechowuje stan archiwizacji i informacje potrzebne do kontynuowania pobierania przy kolejnych uruchomieniach.

Dzięki kluczowi unikalnemu dla urządzenia, czasu zdarzenia i parametru ponowne pobranie tego samego logu nie powinno tworzyć kolejnego rekordu.

### Ważne uwagi

- Poprawne zakończenie skryptu oznacza, że archiwizacja przebiegła bez wykrytych błędów. Nie gwarantuje jednak, że Tuya udostępniła wszystkie istniejące pomiary.
- Komunikat o liczbie przetworzonych logów nie musi oznaczać takiej samej liczby nowych rekordów w bazie, ponieważ część danych może już istnieć.
- Harmonogram GitHub Actions nie gwarantuje uruchomienia dokładnie o zaplanowanej godzinie.
- Przed zmianą konfiguracji czujników należy zaktualizować listę urządzeń w `czujniki.py` i sprawdzić, czy ich identyfikatory są poprawne.


## Struktura bazy danych Supabase

Archiwizacja korzysta z dwóch tabel w schemacie `public`:

- `sensor_readings` — przechowuje historyczne pomiary z czujników.
- `sensor_archive_state` — przechowuje stan procesu archiwizacji i dane potrzebne do kontynuowania pobierania przy kolejnych uruchomieniach.

### 1. Tabela `sensor_readings`

Tabela przechowuje pomiary pobrane z Tuya API. Każdy rekord reprezentuje jeden parametr pomiarowy, np. temperaturę albo wilgotność, zarejestrowany przez konkretne urządzenie w określonym czasie.

| Kolumna | Typ | Znaczenie |
|---|---|---|
| `device_id` | `text` | Unikalny identyfikator urządzenia w systemie Tuya. Pozwala przypisać pomiar do konkretnego czujnika. |
| `device_name` | `text` | Czytelna nazwa urządzenia, np. `Czujnik główny`. Ułatwia analizę danych i wyświetlanie wykresów. |
| `event_time_ms` | `bigint` | Czas zdarzenia przekazany przez Tuya w formacie Unix timestamp w milisekundach. Służy m.in. do identyfikowania tego samego pomiaru. |
| `measured_at` | `timestamptz` | Data i czas wykonania pomiaru w formacie daty obsługiwanym przez PostgreSQL. Kolumna wykorzystywana do filtrowania danych i budowania wykresów. |
| `code` | `text` | Kod parametru pomiarowego. `va_temperature` oznacza temperaturę, a `va_humidity` — wilgotność. |
| `value` | `numeric` | Wartość pomiaru, np. temperatura `21.5` albo wilgotność `48`. |
| `unit` | `text` | Jednostka pomiaru, np. `°C` dla temperatury lub `%` dla wilgotności. |
| `inserted_at` | `timestamptz` | Data i czas dodania rekordu do bazy. Pozwala sprawdzić, kiedy pomiar został zapisany w Supabase, niezależnie od czasu jego wykonania. |

#### Klucz główny i indeks

Klucz główny składa się z trzech kolumn:

`(device_id, event_time_ms, code)`

Dzięki temu ten sam parametr zarejestrowany przez to samo urządzenie w tym samym czasie nie powinien zostać zapisany drugi raz. Skrypt używa operacji `upsert`, która pozwala bezpiecznie ponawiać pobieranie danych.

Indeks `sensor_readings_measured_at_idx` przyspiesza wyszukiwanie pomiarów według czasu, np. podczas pobierania danych z ostatnich 10 lub 48 godzin.

### 2. Tabela `sensor_archive_state`

Tabela przechowuje informacje pomocnicze potrzebne do wznowienia archiwizacji. Nie zawiera pomiarów temperatury ani wilgotności.

| Kolumna | Typ | Znaczenie |
|---|---|---|
| `state_key` | `text` | Klucz identyfikujący zapisany stan. Jest kluczem głównym tabeli. Może wskazywać stan konkretnego urządzenia, np. `device:<device_id>`, albo wspólny stan tokenów Tuya, np. `tuya_tokens`. |
| `last_success_end_time_ms` | `bigint`, dopuszcza `NULL` | Znacznik czasu w milisekundach do którego zakończono ostatnią poprawną archiwizację danego urządzenia. Pomaga wyznaczyć zakres pobierania przy następnym uruchomieniu. Dla wpisów niezwiązanych z postępem urządzenia może pozostać pusty. |
| `refresh_token` | `text`, dopuszcza `NULL` | Token odświeżający Tuya, używany do uzyskania nowego tokenu dostępu. Jego przechowywanie w bazie pozwala zachować aktualny token pomiędzy uruchomieniami GitHub Actions. |
| `updated_at` | `timestamptz` | Data i czas ostatniej aktualizacji danego wpisu stanu. Ułatwia kontrolę działania archiwizacji i aktualizacji tokenów. |


### Dlaczego potrzebujesz obu kolumn?
- `last_success_end_time_ms` pomaga skryptowi zdecydować, od jakiego momentu pobierać kolejne dane.
- `updated_at` pomaga sprawdzić, kiedy skrypt ostatnio aktualizował stan.
Te dwie kolumny używają różnych formatów, ale oba można interpretować względem UTC.

#### Przykładowe wpisy

W tabeli mogą znajdować się wpisy o różnych zastosowaniach:

| `state_key` | Zastosowanie |
|---|---|
| `device:bfa896516d1a62fa5epwgt` | Stan archiwizacji konkretnego czujnika. |
| `device:bf8619c31ef4b9f86898nr` | Stan archiwizacji innego czujnika. |
| `tuya_tokens` | Wspólny wpis przechowujący token odświeżający Tuya. |

Powyższe identyfikatory są przykładami. Rzeczywiste wpisy zależą od aktualnej konfiguracji urządzeń.




### 3. Jak obie tabele współpracują?

1. `archive.py` odczytuje stan urządzenia z `sensor_archive_state`.
2. Na podstawie zapisanego postępu ustala zakres historii do pobrania z Tuya API.
3. Pobrane pomiary zapisuje w `sensor_readings`.
4. Jeśli wszystkie strony historii zostaną poprawnie pobrane i zapisane, aktualizuje stan urządzenia w `sensor_archive_state`.
5. Przy następnym uruchomieniu proces może kontynuować pobieranie od zapisanego punktu, z uwzględnieniem skonfigurowanego zakresu nakładania się historii.

Takie rozdzielenie pozwala niezależnie przechowywać dane pomiarowe i informacje o postępie procesu archiwizacji.

### 4. Bezpieczeństwo

Obie tabele mają włączone Row Level Security (RLS). Dostęp do zapisu i odczytu danych przez proces archiwizacji realizowany jest za pomocą klucza `service_role`, przechowywanego w zmiennej środowiskowej `SUPABASE_SERVICE_ROLE_KEY`.

Klucz ten omija reguły RLS, dlatego nie wolno umieszczać go w kodzie publicznym, repozytorium ani aplikacji działającej w przeglądarce. Dostęp do tabel powinien być ograniczony do zaufanych procesów i odpowiednio skonfigurowanych uprawnień.



### `czujniki.py` — komunikacja z API Tuya

Plik `czujniki.py` odpowiada za komunikację z platformą Tuya Cloud i pobieranie danych z czujników temperatury oraz wilgotności. Udostępnia funkcje wykorzystywane przez pozostałe skrypty projektu, w szczególności `archive.py`.

**Funkcje:**
- Przechowuje konfigurację czujników, w tym ich identyfikatory i nazwy.
- Obsługuje autoryzację w Tuya Cloud API oraz odświeżanie tokenów dostępu.
- Podpisuje żądania API zgodnie z wymaganiami Tuya.
- Wysyła zapytania do API i obsługuje odpowiedzi oraz błędy.
- Udostępnia dane potrzebne do pobierania historii pomiarów.

**Ważne:** dane dostępowe, takie jak klucz klienta i tokeny, są pobierane ze zmiennych środowiskowych zapisanych w pliku `.env`. Nie należy umieszczać ich bezpośrednio w kodzie ani publikować w repozytorium.
Odświeżone tokeny są zapisywane z powrotem do `pobieranie/.env`, a jeśli ustawiona jest
zmienna `SENSOR_ENV_FILE` (robi to spakowany program, zob. `pakiet/README.md`) — do wskazanego w niej pliku.

Plik `czujniki.py` stanowi warstwę komunikacyjną między projektem a Tuya Cloud — nie zapisuje samodzielnie archiwum w Supabase. Za archiwizację odpowiada `archive.py`.