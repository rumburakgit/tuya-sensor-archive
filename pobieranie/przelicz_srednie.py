#!/usr/bin/env python3
"""
Przelicza średnie w tabeli sensor_aggregates.

Obliczenia wykonuje funkcja SQL przelicz_srednie() w Supabase
(definicja: analityka/001_sensor_aggregates.sql). Ten skrypt
tylko ustala zakres i wywołuje ją przez RPC.

Uruchomienie:
    python przelicz_srednie.py                      # cała historia
        # (w kawałkach po KAWALEK_DNI dni, bo jedno wywołanie
        # dla całej historii przekracza limit czasu zapytania Supabase)
    python przelicz_srednie.py --nowe-od 2026-10-26T10:00:00+00:00
        # tylko okresy z pomiarami dodanymi do bazy od podanej chwili
        # (używane przez panel po każdej archiwizacji)
"""

import argparse
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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

# Pełne przeliczenie idzie w kawałkach po tyle dni: każde wywołanie RPC
# musi zmieścić się w limicie czasu zapytania (statement_timeout ~8 s).
# Granice kawałków wypadają o północy czasu polskiego, czyli na
# granicach okresów 3h i 1d, więc wynik jest taki sam jak w jednym
# wywołaniu (okresy grzania na granicy funkcja SQL wykrywa od ich początku).
KAWALEK_DNI = 7
STREFA = ZoneInfo("Europe/Warsaw")


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


def najstarszy_pomiar():
    wynik = (
        supabase.table(TABLE_READINGS)
        .select("measured_at")
        .order("measured_at")
        .limit(1)
        .execute()
    )

    return wynik.data[0]["measured_at"] if wynik.data else None


def kawalki_historii(najstarszy_iso):
    """Granice [od, do) kolejnych kawałków; ostatni ma do = None (teraz)."""
    pierwszy_dzien = datetime.fromisoformat(najstarszy_iso).astimezone(STREFA).date()
    od = datetime.combine(pierwszy_dzien, time(), STREFA)
    teraz = datetime.now(STREFA)

    while True:
        dzien_do = od.date() + timedelta(days=KAWALEK_DNI)
        do = datetime.combine(dzien_do, time(), STREFA)
        if do >= teraz:
            yield od, None
            return
        yield od, do
        od = do


def przelicz_cala_historie():
    najstarszy = najstarszy_pomiar()
    if not najstarszy:
        print("Brak pomiarów — nie ma czego przeliczać.")
        return 0

    print(f"Najstarszy pomiar: {najstarszy}")
    kawalki = list(kawalki_historii(najstarszy))
    razem = 0

    for nr, (od, do) in enumerate(kawalki, start=1):
        parametry = {"p_od": od.isoformat()}
        if do:
            parametry["p_do"] = do.isoformat()

        opis_do = do.date().isoformat() if do else "teraz"
        print(f"[{nr}/{len(kawalki)}] {od.date().isoformat()} – {opis_do} ...", end=" ", flush=True)
        wynik = supabase.rpc("przelicz_srednie", parametry).execute()
        print(f"{wynik.data} wierszy", flush=True)
        razem += wynik.data or 0

    return razem


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
        wynik = supabase.rpc(
            "przelicz_srednie", {"p_od": poczatek.isoformat()}
        ).execute()
        zapisano = wynik.data
    else:
        print(f"Zakres: cała historia (w kawałkach po {KAWALEK_DNI} dni)")
        zapisano = przelicz_cala_historie()

    print(f"Zapisano wierszy w sensor_aggregates: {zapisano}")
    print("=== Przeliczanie zakończone pomyślnie ===")


if __name__ == "__main__":
    main()
