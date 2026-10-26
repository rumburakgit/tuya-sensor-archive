# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Archives temperature/humidity readings from Tuya sensors (Tuya Cloud API, EU endpoint `openapi.tuyaeu.com`) into Supabase (PostgreSQL), plus local Flask dashboards. Plain procedural Python 3.12; identifiers, comments, logs and docs are in Polish — keep that convention. Detailed docs (incl. full DB schema) live in `pobieranie/README.md` and `prezentacja/README.md` (Polish).

## Layout

- `pobieranie/` — the data-collection layer (own `.env` and `requirements.txt`). Archiving is run **manually**, normally via `pobieranie/panel.py`.
- `analityka/` — the analytics layer: SQL only (`001_sensor_aggregates.sql`, applied manually in the Supabase SQL Editor; idempotent). Averages are computed **in Postgres** into `sensor_aggregates`; Python only calls `rpc("przelicz_srednie")`. Recalculation belongs to the collection side (`pobieranie/przelicz_srednie.py`, auto-run by `pobieranie/panel.py` after each archive run), never to the presentation layer.
- `prezentacja/` — the presentation layer: reads only from Supabase, must never touch Tuya. Own `.env` (only `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) and `requirements.txt`. `prezentacja/panel.py` starts/stops presentation servers; more (Apache ECharts, Grafana) will be added to its `PREZENTACJE` list.
- `.venv/` stays at the repo root.

## Commands

There is no test suite, linter or build step. Run from the repo root:

```bash
./.venv/bin/pip install -r pobieranie/requirements.txt
./.venv/bin/python3 pobieranie/panel.py           # tkinter GUI: buttons for archive.py / archive_metar.py + live console
./.venv/bin/python3 pobieranie/archive.py         # one archive run: Tuya -> Supabase
./.venv/bin/python3 pobieranie/archive_metar.py   # one archive run: METAR EPRZ -> Supabase (--dry-run = no writes)
./.venv/bin/python3 pobieranie/przelicz_srednie.py  # recompute sensor_aggregates (all history; --nowe-od ISO = only since rows inserted after ISO)
./.venv/bin/python3 prezentacja/panel.py          # tkinter GUI: start/stop presentation servers + live log console
./.venv/bin/python3 prezentacja/app_archive.py    # dashboard from Supabase, last 48h, http://127.0.0.1:5001
./.venv/bin/python3 pobieranie/app.py             # live dashboard straight from Tuya, last 1h, http://127.0.0.1:5000
./.venv/bin/python3 pobieranie/rzeszow_metar_to_csv.py  # standalone METAR (EPRZ) -> CSV in the cwd
```

Scripts need the `.env` of their own directory (`pobieranie/.env`, `prezentacja/.env`; see the READMEs there); they locate it relative to their own file, not the cwd. `.github/workflows/archive.yml` runs `archive.py` (in `pobieranie/`) with GitHub Secrets; it is **`workflow_dispatch` only** — the cron was removed, so README/GEMINI.md statements about "every 15 minutes" are out of date.

## Architecture

- `czujniki.py` — Tuya client layer. Reads credentials from env **at import time** (so callers must `load_dotenv()` before `import czujniki`, as `archive.py` does). Handles HMAC-SHA256 request signing (`podpis`), token fetch/refresh, and `tuya_get()` which retries once on token error codes 1010/1011/1012. Token state is module-level globals (`ACCESS_TOKEN`, `REFRESH_TOKEN`, `TOKEN_EXPIRES_AT`); refreshed tokens are also written back into `.env` via `set_key`.
- `archive.py` — the archiver. For each device in `czujniki.DEVICES`: reads `device:<id>` from `sensor_archive_state`, fetches `/v2.0/cloud/thing/{id}/logs` from `last_success_end_time_ms - 30 min` (or 7 days back on first run), paginates via `has_next`/`next_row_key` → `start_row_key`, upserts into `sensor_readings` on PK `(device_id, event_time_ms, code)`, and only then advances the device state. One device failing doesn't stop others, but the script raises at the end so CI fails.
- Token persistence across CI runs: `archive.py` loads the refresh token from the `tuya_tokens` row in `sensor_archive_state` (overriding `.env`), forces a refresh, and saves the current refresh token back after every page and in `finally`. `app.py` uses only `.env` tokens, so the `.env` and DB copies can diverge.
- `app.py` / `app_archive.py` — single-file Flask apps with the HTML/CSS/Chart.js (CDN) template inline in an `HTML` string. `app.py` calls Tuya live via `czujniki`; `app_archive.py` reads only Supabase (paged in 1000-row chunks) and never touches Tuya.
- `archive_metar.py` — like `archive.py` but for METAR from aviationweather.gov (station EPRZ, `device_id="weather_eprz"`, state key `device:weather_eprz`, 2 h overlap, 24 h windows walked backwards).
- `pobieranie/panel.py` — tkinter GUI; runs the two archivers as subprocesses (`sys.executable -u`, cwd `pobieranie/`) one at a time and streams their output via a reader thread + queue polled with `after()`.
- `prezentacja/panel.py` — same subprocess/queue pattern, but long-running servers: start/stop toggle per entry, waits for the port to open then opens the browser, strips ANSI codes from werkzeug logs.
- Analytics (`analityka/001_sensor_aggregates.sql`): `agreguj_wazona_czasem()` = time-weighted mean where each reading is valid until the next one but at most a max validity (Tuya 15 min, METAR 60 min); buckets aligned to Europe/Warsaw midnight (DST days have 23/25 h); groups sum integrals over all member sensors. `przelicz_srednie(p_od, p_do)` holds the series definitions (`mieszkanie` 3h + each member sensor 3h, `weather_eprz` 1d) and then derived series via `oblicz_roznice()` (`mieszkanie-weather_eprz` 3h = mieszkanie 3h avg minus the Jasionka daily avg of the containing day, metoda `roznica_srednich`, null when the daily avg is missing). New calculations go here as new series in `sensor_aggregates`; the dashboard only plots them (`SREDNIE`/`ROZNICE` lists in `prezentacja/app_archive.py`, matched on seria+okres+metoda). Group membership lives in table `sensor_groups` (`mieszkanie` excludes "Temp Grzejnik"). To verify SQL changes, a local Postgres 16 is available (`/usr/lib/postgresql/16/bin`); compare against an independent Python computation.
- `rzeszow_metar_to_csv.py` — independent of the rest; pulls METAR from aviationweather.gov and writes CSV rows shaped like `sensor_readings` (`device_id="weather_eprz"`) for manual import.
- `api_list` — reference dump of the Tuya Cloud APIs subscribed for this project.

## Gotchas

- **Device lists are duplicated**: `czujniki.DEVICES` (used by `archive.py` and `app.py`) and `app_archive.DEVICES` are separate and currently differ (`app_archive.py` lists more sensors than `czujniki.py` archives). When adding/removing a sensor, update both.
- Tuya reports `va_temperature` in tenths of a degree — divide by 10 (done in both `archive.py` and `czujniki.pobierz_historie`). `va_humidity` is used as-is.
- Timestamps are kept internally in Unix milliseconds (Tuya style) and converted to ISO UTC only when written to `measured_at`.
- Scripts use the Supabase `service_role` key, which bypasses RLS — it must never end up in browser-facing code.
