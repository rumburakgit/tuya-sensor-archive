# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Archives temperature/humidity readings from Tuya sensors (Tuya Cloud API, EU endpoint `openapi.tuyaeu.com`) into Supabase (PostgreSQL), plus local Flask dashboards. Plain procedural Python 3.12; identifiers, comments, logs and docs are in Polish — keep that convention. Detailed docs (incl. full DB schema) live in `README.md` (Polish).

## Commands

There is no test suite, linter or build step.

```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python3 archive.py          # one archive run: Tuya -> Supabase
./.venv/bin/python3 app_archive.py      # dashboard from Supabase, last 48h, http://127.0.0.1:5001
./.venv/bin/python3 app.py              # live dashboard straight from Tuya, last 1h, http://127.0.0.1:5000
./.venv/bin/python3 rzeszow_metar_to_csv.py  # standalone METAR (EPRZ) -> rzeszow_sensor_readings.csv
```

All scripts need `.env` (see README for the variable list). `.github/workflows/archive.yml` runs `archive.py` with GitHub Secrets; it is currently **`workflow_dispatch` only** — the cron was removed, so README/GEMINI.md statements about "every 15 minutes" are out of date.

## Architecture

- `czujniki.py` — Tuya client layer. Reads credentials from env **at import time** (so callers must `load_dotenv()` before `import czujniki`, as `archive.py` does). Handles HMAC-SHA256 request signing (`podpis`), token fetch/refresh, and `tuya_get()` which retries once on token error codes 1010/1011/1012. Token state is module-level globals (`ACCESS_TOKEN`, `REFRESH_TOKEN`, `TOKEN_EXPIRES_AT`); refreshed tokens are also written back into `.env` via `set_key`.
- `archive.py` — the archiver. For each device in `czujniki.DEVICES`: reads `device:<id>` from `sensor_archive_state`, fetches `/v2.0/cloud/thing/{id}/logs` from `last_success_end_time_ms - 30 min` (or 7 days back on first run), paginates via `has_next`/`next_row_key` → `start_row_key`, upserts into `sensor_readings` on PK `(device_id, event_time_ms, code)`, and only then advances the device state. One device failing doesn't stop others, but the script raises at the end so CI fails.
- Token persistence across CI runs: `archive.py` loads the refresh token from the `tuya_tokens` row in `sensor_archive_state` (overriding `.env`), forces a refresh, and saves the current refresh token back after every page and in `finally`. `app.py` uses only `.env` tokens, so the `.env` and DB copies can diverge.
- `app.py` / `app_archive.py` — single-file Flask apps with the HTML/CSS/Chart.js (CDN) template inline in an `HTML` string. `app.py` calls Tuya live via `czujniki`; `app_archive.py` reads only Supabase (paged in 1000-row chunks) and never touches Tuya.
- `rzeszow_metar_to_csv.py` — independent of the rest; pulls METAR from aviationweather.gov and writes CSV rows shaped like `sensor_readings` (`device_id="weather_eprz"`) for manual import.
- `api_list` — reference dump of the Tuya Cloud APIs subscribed for this project.

## Gotchas

- **Device lists are duplicated**: `czujniki.DEVICES` (used by `archive.py` and `app.py`) and `app_archive.DEVICES` are separate and currently differ (`app_archive.py` lists more sensors than `czujniki.py` archives). When adding/removing a sensor, update both.
- Tuya reports `va_temperature` in tenths of a degree — divide by 10 (done in both `archive.py` and `czujniki.pobierz_historie`). `va_humidity` is used as-is.
- Timestamps are kept internally in Unix milliseconds (Tuya style) and converted to ISO UTC only when written to `measured_at`.
- Scripts use the Supabase `service_role` key, which bypasses RLS — it must never end up in browser-facing code.
