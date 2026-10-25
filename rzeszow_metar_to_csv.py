
#!/usr/bin/env python3
"""
Pobiera rzeczywiste obserwacje METAR/SPECI dla EPRZ
od podanej daty do teraz i zapisuje je do CSV.

Instalacja:
    pip install requests

Uruchomienie:
    python rzeszow_metar_to_csv.py
"""

import csv
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


# =========================
# KONFIGURACJA
# =========================

STATION = "EPRZ"
DEVICE_ID = "weather_eprz"
DEVICE_NAME = "Temp Rzeszow Jasionka"
CODE = "va_temperature"
UNIT = "°C"

API_URL = "https://aviationweather.gov/api/data/metar"
OUTPUT = Path("rzeszow_sensor_readings.csv")

LOCAL_TZ = ZoneInfo("Europe/Warsaw")

# Podaj datę początkową w formacie RRRR-MM-DD.
START_DATE = "2026-10-20"

COLUMNS = [
    "device_id",
    "device_name",
    "event_time_ms",
    "measured_at",
    "code",
    "value",
    "unit",
    "inserted_at",
]

MAX_RETRIES = 4
REQUEST_TIMEOUT = 40
PAUSE_BETWEEN_REQUESTS = 1.2


# =========================
# POBIERANIE DANYCH
# =========================

def fetch_window(session, end_utc):
    """
    Pobiera raporty z 24 godzin poprzedzających end_utc.
    Parametr date wskazuje koniec okna archiwalnego.
    """
    params = {
        "ids": STATION,
        "format": "json",
        "hours": "24",
        "date": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(
                API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 204:
                return []

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, list):
                raise ValueError(
                    f"Nieoczekiwany format odpowiedzi API: "
                    f"{type(payload).__name__}"
                )

            return payload

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(
                    f"Nie udało się pobrać okna kończącego się "
                    f"{end_utc.isoformat()}: {exc}"
                ) from exc

            wait_seconds = min(2 ** attempt, 15)
            print(
                f"Błąd pobierania: {exc}. "
                f"Ponawiam za {wait_seconds} s..."
            )
            time.sleep(wait_seconds)

    return []


def fetch_all(start_date):
    """
    Pobiera kolejne 24-godzinne okna od daty początkowej
    do bieżącego czasu UTC.
    """
    now_utc = datetime.now(timezone.utc)

    start_local = datetime.combine(
        start_date,
        datetime.min.time(),
        tzinfo=LOCAL_TZ,
    )
    start_utc = start_local.astimezone(timezone.utc)

    if start_utc > now_utc:
        raise ValueError("Data początkowa nie może być w przyszłości.")

    # API udostępnia dane historyczne z ograniczonego okresu.
    if now_utc - start_utc > timedelta(days=30):
        raise ValueError(
            "Data początkowa jest starsza niż 30 dni. "
            "To API nie gwarantuje dostępu do starszych danych."
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": "RzeszowWeatherArchive/1.0",
        "Accept": "application/json",
    })

    reports_by_time = {}

    # Okna dobowe z niewielkim nakładaniem się na granicach.
    cursor = now_utc

    try:
        while cursor > start_utc:
            print(
                "Pobieranie raportów do: "
                + cursor.strftime("%Y-%m-%d %H:%M UTC")
            )

            reports = fetch_window(session, cursor)

            for report in reports:
                if report.get("icaoId") != STATION:
                    continue

                obs_timestamp = report.get("obsTime")
                if obs_timestamp is None:
                    continue

                try:
                    obs_utc = datetime.fromtimestamp(
                        float(obs_timestamp),
                        tz=timezone.utc,
                    )
                except (ValueError, TypeError, OverflowError):
                    continue

                # Zachowujemy tylko raporty z żądanego przedziału.
                if not (start_utc <= obs_utc <= now_utc):
                    continue

                raw = (report.get("rawOb") or "").strip()
                if not (
                    raw.startswith(f"METAR {STATION}")
                    or raw.startswith(f"SPECI {STATION}")
                    or raw.startswith(f"METAR COR {STATION}")
                    or raw.startswith(f"SPECI COR {STATION}")
                ):
                    continue

                temp = report.get("temp")
                if temp is None:
                    continue

                try:
                    temp = float(temp)
                except (ValueError, TypeError):
                    continue

                # Jedna wartość na godzinę UTC:
                # zachowujemy najnowszą rzeczywistą obserwację.
                hour_utc = obs_utc.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                key = int(hour_utc.timestamp())

                previous = reports_by_time.get(key)
                if (
                    previous is None
                    or obs_timestamp > previous["obsTime"]
                ):
                    report_copy = dict(report)
                    report_copy["_obs_datetime"] = obs_utc
                    report_copy["_hour_utc"] = hour_utc
                    report_copy["_temperature"] = temp
                    reports_by_time[key] = report_copy

            cursor -= timedelta(hours=24)
            time.sleep(PAUSE_BETWEEN_REQUESTS)

    finally:
        session.close()

    return [
        reports_by_time[key]
        for key in sorted(reports_by_time)
    ]


# =========================
# ZAPIS CSV
# =========================

def save_csv(reports):
    inserted_at = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )[:-3] + "+00"

    with OUTPUT.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=COLUMNS,
        )
        writer.writeheader()

        for report in reports:
            hour_utc = report["_hour_utc"]

            writer.writerow({
                "device_id": DEVICE_ID,
                "device_name": DEVICE_NAME,
                "event_time_ms": int(hour_utc.timestamp() * 1000),
                "measured_at": hour_utc.strftime(
                    "%Y-%m-%d %H:00:00.000+00"
                ),
                "code": CODE,
                "value": report["_temperature"],
                "unit": UNIT,
                "inserted_at": inserted_at,
            })

    print(f"\nZapisano rekordów: {len(reports)}")
    print(f"Plik CSV: {OUTPUT.resolve()}")


# =========================
# START PROGRAMU
# =========================

def main():
    try:
        start_date = date.fromisoformat(START_DATE)

        print(f"Stacja: {STATION}")
        print(f"Data początkowa: {START_DATE}")
        print("Data końcowa: teraz")
        print()

        reports = fetch_all(start_date)

        if not reports:
            print(
                "Nie znaleziono raportów z temperaturą "
                "w wybranym przedziale."
            )

        save_csv(reports)

    except (ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"\nBŁĄD: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

