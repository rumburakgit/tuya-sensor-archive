# Tuya Sensor Archive

Archiwizacja pomiarów temperatury i wilgotności (czujniki Tuya, METAR EPRZ) w Supabase.

## Struktura

- `pobieranie/` — warstwa pobierająca dane: skrypty archiwizujące (Tuya, METAR),
  klient Tuya, panel do ręcznego uruchamiania oraz podgląd na żywo z Tuya (`app.py`).
  Szczegóły w [`pobieranie/README.md`](pobieranie/README.md).
- `analityka/` — funkcje SQL liczące średnie ważone czasem do tabeli `sensor_aggregates`
  i wykrywające okresy grzania do `heating_periods` (wdrażane w Supabase SQL Editor). Szczegóły w [`analityka/README.md`](analityka/README.md).
- `prezentacja/` — warstwa prezentacji, korzysta wyłącznie z Supabase, bez kontaktu z Tuya.
  Panel prezentacji i dashboard na porcie 5001. Szczegóły w [`prezentacja/README.md`](prezentacja/README.md).
- `pakiet/` — okno startowe dla obu paneli i pakowanie ich do jednego pliku wykonywalnego
  (PyInstaller, budowany w GitHub Actions). Szczegóły w [`pakiet/README.md`](pakiet/README.md).

## Szybki start

```bash
python3 -m venv .venv
./.venv/bin/pip install -r pobieranie/requirements.txt
# pobieranie/.env — zmienne opisane w pobieranie/README.md
./.venv/bin/python3 pobieranie/panel.py    # archiwizacja
# prezentacja/.env — SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
./.venv/bin/python3 prezentacja/panel.py   # prezentacja
./.venv/bin/python3 pakiet/start.py        # okno startowe z oboma panelami
```

Na komputerze bez Pythona można użyć gotowego pliku `czujniki-tuya`
z release **najnowsza** — zob. [`pakiet/README.md`](pakiet/README.md).
