#!/usr/bin/env python3
"""
Przelicza średnie w tabeli sensor_aggregates.

Obliczenia wykonuje funkcja SQL przelicz_srednie() w Supabase
(definicja: analityka/001_sensor_aggregates.sql). Ten skrypt
tylko ustala zakres i wywołuje ją przez RPC.

Uruchomienie:
    python przelicz_srednie.py                      # cała historia
    python przelicz_srednie.py --nowe-od 2026-10-26T10:00:00+00:00
        # tylko okresy z pomiarami dodanymi do bazy od podanej chwili
        # (używane przez panel po każdej archiwizacji)
"""

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

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

# Zawsze przeliczaj co najmniej ostatnią dobę przed archiwizacją.
# Wychwytuje to korekty METAR (COR), które zmieniają istniejące
# wiersze bez zmiany inserted_at.
MINIMALNY_ZAKRES = timedelta(days=1)


def najstarszy_nowy_pomiar(nowe_od_iso):
    """Najwcześniejszy measured_at wśród wierszy wstawionych od nowe_od."""
    wynik = (
        supabase.table(TABLE_READINGS)
        .select("measured_at")
        .gte("inserted_at", nowe_od_iso)
        .order("measured_at")
        .limit(1)
        .execute()
    )

    return wynik.data[0]["measured_at"] if wynik.data else None


def wyznacz_poczatek(nowe_od_iso):
    nowe_od = datetime.fromisoformat(nowe_od_iso)
    poczatek = nowe_od - MINIMALNY_ZAKRES

    najstarszy = najstarszy_nowy_pomiar(nowe_od_iso)
    if najstarszy:
        print(f"Najstarszy nowy pomiar: {najstarszy}")
        poczatek = min(poczatek, datetime.fromisoformat(najstarszy))
    else:
        print("Brak nowych pomiarów — przeliczam tylko ostatnią dobę.")

    return poczatek


# --------------------------------------------------
# Start programu
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Przeliczenie średnich")
    parser.add_argument(
        "--nowe-od",
        help="chwila startu archiwizacji (ISO 8601 ze strefą czasową)",
    )
    args = parser.parse_args()

    print("=== Przeliczanie średnich ===")

    if args.nowe_od:
        poczatek = wyznacz_poczatek(args.nowe_od)
        print(f"Zakres: od {poczatek.isoformat()} do teraz")
        parametry = {"p_od": poczatek.isoformat()}
    else:
        print("Zakres: cała historia")
        parametry = {}

    wynik = supabase.rpc("przelicz_srednie", parametry).execute()

    print(f"Zapisano wierszy w sensor_aggregates: {wynik.data}")
    print("=== Przeliczanie zakończone pomyślnie ===")


if __name__ == "__main__":
    main()
