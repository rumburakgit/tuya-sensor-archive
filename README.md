# Tuya Sensor Archive

Archiwizacja pomiarów temperatury i wilgotności (czujniki Tuya, METAR EPRZ) w Supabase.

## Struktura

- `pobieranie/` — warstwa pobierająca dane: skrypty archiwizujące (Tuya, METAR),
  klient Tuya, panel do ręcznego uruchamiania oraz podgląd na żywo z Tuya (`app.py`).
  Szczegóły w [`pobieranie/README.md`](pobieranie/README.md).
- `analityka/` — funkcje SQL liczące średnie ważone czasem do tabeli `sensor_aggregates`
  (wdrażane w Supabase SQL Editor). Szczegóły w [`analityka/README.md`](analityka/README.md).
- `prezentacja/` — warstwa prezentacji, korzysta wyłącznie z Supabase, bez kontaktu z Tuya.
  Panel prezentacji i dashboard na porcie 5001. Szczegóły w [`prezentacja/README.md`](prezentacja/README.md).

## Szybki start

```bash
python3 -m venv .venv
./.venv/bin/pip install -r pobieranie/requirements.txt
# pobieranie/.env — zmienne opisane w pobieranie/README.md
./.venv/bin/python3 pobieranie/panel.py    # archiwizacja
# prezentacja/.env — SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
./.venv/bin/python3 prezentacja/panel.py   # prezentacja
```
