# Tuya Sensor Archive

Archiwizacja i wizualizacja pomiarów temperatury i wilgotności z czujników Tuya.

## Project Overview

Ten projekt służy do automatycznego pobierania danych z czujników Tuya (poprzez Tuya Cloud API) i ich trwałego przechowywania w bazie danych Supabase (PostgreSQL). Projekt zawiera również webowy dashboard do podglądu aktualnych i historycznych odczytów.

### Technologie
- **Język:** Python 3.12
- **Baza danych:** Supabase (PostgreSQL)
- **API:** Tuya Cloud API
- **Web:** Flask + Chart.js (wizualizacja danych)
- **Automatyzacja:** GitHub Actions

## Project Structure

Wszystkie poniższe pliki znajdują się w katalogu `pobieranie/` (warstwa pobierająca dane).
Ręczną archiwizację uruchamia się przez `pobieranie/panel.py`.
Wyjątek: `app_archive.py` jest w `prezentacja/` (warstwa prezentacji, tylko Supabase).

- `archive.py`: Główny skrypt archiwizujący. Pobiera logi z Tuya API i zapisuje je do Supabase, zapobiegając duplikatom.
- `czujniki.py`: Moduł komunikacji z Tuya API. Zarządza autoryzacją (tokeny) i pobieraniem surowych danych.
- `app_archive.py`: Dashboard Flask wyświetlający dane historyczne (ostatnie 48h) bezpośrednio z Supabase.
- `app.py`: Uproszczony dashboard wyświetlający ostatnią godzinę pomiarów.
- `.github/workflows/archive.yml`: Konfiguracja GitHub Actions (archiwizacja uruchamiana ręcznie, `workflow_dispatch`).
- `requirements.txt`: Zależności projektu (`requests`, `python-dotenv`, `flask`, `supabase`).

## Building and Running

### Środowisko lokalne
1. Stwórz i aktywuj środowisko wirtualne:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Zainstaluj zależności:
   ```bash
   pip install -r pobieranie/requirements.txt
   ```
3. Skonfiguruj plik `pobieranie/.env` (patrz sekcja Konfiguracja).

### Uruchamianie
- **Panel archiwizacji:** `python pobieranie/panel.py`
- **Archiwizacja:** `python pobieranie/archive.py`, `python pobieranie/archive_metar.py`
- **Panel prezentacji:** `python prezentacja/panel.py`
- **Dashboard (48h):** `python prezentacja/app_archive.py` (dostępny pod `http://127.0.0.1:5001`)
- **Dashboard (1h):** `python pobieranie/app.py` (dostępny pod `http://127.0.0.1:5000`)

## Configuration

Projekt wymaga zestawu zmiennych środowiskowych w pliku `.env`:

```env
# Supabase
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key

# Tuya API
TUYA_CLIENT_ID=your_tuya_client_id
TUYA_CLIENT_SECRET=your_tuya_client_secret
TUYA_ACCESS_TOKEN=initial_access_token (opcjonalnie)
TUYA_REFRESH_TOKEN=initial_refresh_token (wymagane przy pierwszym uruchomieniu)
```

## Development Conventions

- **Baza danych:** Projekt korzysta z tabel `sensor_readings` (dane) oraz `sensor_archive_state` (stan archiwizacji i tokeny).
- **Obsługa czasu:** Wszystkie znaczniki czasu wewnątrz systemu są przetwarzane w milisekundach (Tuya style) i konwertowane na ISO UTC przy zapisie do bazy.
- **Bezpieczeństwo:** Nigdy nie commituj pliku `.env`. Sekrety w GitHub Actions są zarządzane przez GitHub Secrets.
- **Styl:** Skrypty są napisane w prostym, proceduralnym stylu z jasnym podziałem na moduły. Dashboardy używają inline CSS i Chart.js dostarczanego przez CDN.
