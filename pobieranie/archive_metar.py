#!/usr/bin/env python3
"""
Archiwizuje temperaturę z raportów METAR/SPECI dla EPRZ
(Rzeszów-Jasionka) w tabeli sensor_readings w Supabase.

Pierwsze uruchomienie pobiera dane od START_DATE.
Kolejne uruchomienia pobierają dane od miejsca zapisanego
w sensor_archive_state (z niewielkim nakładaniem się).

Uruchomienie:
    python archive_metar.py            # zapis do bazy
    python archive_metar.py --dry-run  # tylko podgląd, bez zapisu
"""

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from supabase import create_client


# --------------------------------------------------
# Konfiguracja
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)

TABLE_READINGS = "sensor_readings"
TABLE_STATE = "sensor_archive_state"

STATION = "EPRZ"
DEVICE_ID = "weather_eprz"
DEVICE_NAME = "Temp Rzeszow Jasionka"
CODE = "va_temperature"
UNIT = "°C"

STATE_KEY = f"device:{DEVICE_ID}"

API_URL = "https://aviationweather.gov/api/data/metar"

LOCAL_TZ = ZoneInfo("Europe/Warsaw")

# Data początkowa (północ czasu polskiego) dla pierwszego uruchomienia.
START_DATE = "2026-10-20"

# API nie gwarantuje dostępu do starszych danych.
MAX_HISTORY_DAYS = 30

# Przy kolejnych uruchomieniach cofnij zakres o 2 godziny,
# aby wychwycić spóźnione raporty i korekty (METAR COR).
OVERLAP_MINUTES = 120

WINDOW_HOURS = 24
MAX_RETRIES = 4
REQUEST_TIMEOUT = 40
PAUSE_BETWEEN_REQUESTS = 1.2


def iso_utc(timestamp_ms):
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).isoformat()


# --------------------------------------------------
# Stan archiwizacji
# --------------------------------------------------

def pobierz_stan():
    wynik = (
        supabase.table(TABLE_STATE)
        .select("*")
        .eq("state_key", STATE_KEY)
        .limit(1)
        .execute()
    )

    return wynik.data[0] if wynik.data else None


def zapisz_stan(end_time_ms):
    supabase.table(TABLE_STATE).upsert(
        {
            "state_key": STATE_KEY,
            "last_success_end_time_ms": end_time_ms,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="state_key",
    ).execute()


def wyznacz_poczatek(now_utc):
    stan = pobierz_stan()

    if stan and stan.get("last_success_end_time_ms") is not None:
        ostatni_koniec = datetime.fromtimestamp(
            int(stan["last_success_end_time_ms"]) / 1000,
            tz=timezone.utc,
        )
        start_utc = ostatni_koniec - timedelta(minutes=OVERLAP_MINUTES)
        print(
            "Kontynuacja od: "
            + start_utc.strftime("%Y-%m-%d %H:%M UTC")
        )
    else:
        start_local = datetime.combine(
            date.fromisoformat(START_DATE),
            datetime.min.time(),
            tzinfo=LOCAL_TZ,
        )
        start_utc = start_local.astimezone(timezone.utc)
        print(
            f"Pierwsze pobranie od {START_DATE} "
            f"({start_utc.strftime('%Y-%m-%d %H:%M UTC')})"
        )

    if start_utc > now_utc:
        raise ValueError("Data początkowa nie może być w przyszłości.")

    najstarszy = now_utc - timedelta(days=MAX_HISTORY_DAYS)
    if start_utc < najstarszy:
        print(
            f"UWAGA: początek starszy niż {MAX_HISTORY_DAYS} dni; "
            "starsze dane mogą być niedostępne w API. "
            f"Pobieram od {najstarszy.strftime('%Y-%m-%d %H:%M UTC')}."
        )
        start_utc = najstarszy

    return start_utc


# --------------------------------------------------
# Pobieranie danych METAR
# --------------------------------------------------

def fetch_window(session, end_utc):
    """
    Pobiera raporty z WINDOW_HOURS godzin poprzedzających end_utc.
    Parametr date wskazuje koniec okna archiwalnego.
    """
    params = {
        "ids": STATION,
        "format": "json",
        "hours": str(WINDOW_HOURS),
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


def fetch_all(start_utc, now_utc):
    """
    Pobiera kolejne okna od now_utc wstecz do start_utc.
    Zwraca słownik {obs_time_ms: temperatura}.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "RzeszowWeatherArchive/1.0",
        "Accept": "application/json",
    })

    # Dla tego samego czasu obserwacji wygrywa korekta (COR).
    pomiary = {}
    korekty = set()

    cursor = now_utc

    try:
        while cursor > start_utc:
            print(
                "Pobieranie raportów do: "
                + cursor.strftime("%Y-%m-%d %H:%M UTC")
            )

            for report in fetch_window(session, cursor):
                if report.get("icaoId") != STATION:
                    continue

                obs_timestamp = report.get("obsTime")
                if obs_timestamp is None:
                    continue

                try:
                    # obsTime to Unix timestamp w sekundach (UTC).
                    obs_ms = int(float(obs_timestamp)) * 1000
                except (ValueError, TypeError, OverflowError):
                    continue

                obs_utc = datetime.fromtimestamp(
                    obs_ms / 1000,
                    tz=timezone.utc,
                )

                if not (start_utc <= obs_utc <= now_utc):
                    continue

                raw = (report.get("rawOb") or "").strip()
                jest_korekta = raw.startswith((
                    f"METAR COR {STATION}",
                    f"SPECI COR {STATION}",
                ))
                if not (
                    jest_korekta
                    or raw.startswith((
                        f"METAR {STATION}",
                        f"SPECI {STATION}",
                    ))
                ):
                    continue

                temp = report.get("temp")
                if temp is None:
                    continue

                try:
                    temp = float(temp)
                except (ValueError, TypeError):
                    continue

                if obs_ms in korekty and not jest_korekta:
                    continue

                pomiary[obs_ms] = temp
                if jest_korekta:
                    korekty.add(obs_ms)

            cursor -= timedelta(hours=WINDOW_HOURS)

            if cursor > start_utc:
                time.sleep(PAUSE_BETWEEN_REQUESTS)

    finally:
        session.close()

    return pomiary


# --------------------------------------------------
# Zapis do Supabase
# --------------------------------------------------

def przygotuj_rekordy(pomiary):
    return [
        {
            "device_id": DEVICE_ID,
            "device_name": DEVICE_NAME,
            "event_time_ms": obs_ms,
            "measured_at": iso_utc(obs_ms),
            "code": CODE,
            "value": pomiary[obs_ms],
            "unit": UNIT,
        }
        for obs_ms in sorted(pomiary)
    ]


def zapisz_pomiary(rekordy):
    if not rekordy:
        return

    # Klucz główny (device_id, event_time_ms, code)
    # zapobiega duplikatom przy nakładaniu się zakresów.
    supabase.table(TABLE_READINGS).upsert(
        rekordy,
        on_conflict="device_id,event_time_ms,code",
    ).execute()


# --------------------------------------------------
# Start programu
# --------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv[1:]

    print(f"=== Archiwizacja METAR {STATION} ===")
    if dry_run:
        print("Tryb --dry-run: bez zapisu do bazy.")

    now_utc = datetime.now(timezone.utc)
    start_utc = wyznacz_poczatek(now_utc)

    pomiary = fetch_all(start_utc, now_utc)
    rekordy = przygotuj_rekordy(pomiary)

    print(f"Pobrano pomiarów: {len(rekordy)}")

    if dry_run:
        for rekord in rekordy:
            print(f"  {rekord['measured_at']}  {rekord['value']} {UNIT}")
        return

    zapisz_pomiary(rekordy)

    # Postęp zapisujemy dopiero po udanym zapisie pomiarów.
    zapisz_stan(int(now_utc.timestamp() * 1000))

    print("=== Archiwizacja METAR zakończona pomyślnie ===")


if __name__ == "__main__":
    main()
