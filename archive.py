
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client


# --------------------------------------------------
# Konfiguracja
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

import czujniki  # noqa: E402


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)

TABLE_READINGS = "sensor_readings"
TABLE_STATE = "sensor_archive_state"

# Przy pierwszym uruchomieniu pobierz do 7 dni historii.
INITIAL_LOOKBACK_HOURS = 7 * 24

# Przy kolejnych uruchomieniach cofnij zakres o 30 minut.
# Dzięki temu wychwycimy również spóźnione raporty.
OVERLAP_MINUTES = 30

PAGE_SIZE = 100
MAX_PAGES = 1000

CODES = {
    "va_temperature": ("temperatura", "°C"),
    "va_humidity": ("wilgotnosc", "%"),
}


def teraz_ms():
    return int(time.time() * 1000)


def iso_utc(timestamp_ms):
    """Zamienia czas Tuya w milisekundach na datę UTC."""
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).isoformat()


# --------------------------------------------------
# Stan archiwizacji i token Tuya
# --------------------------------------------------

def pobierz_stan(state_key):
    wynik = (
        supabase.table(TABLE_STATE)
        .select("*")
        .eq("state_key", state_key)
        .limit(1)
        .execute()
    )

    return wynik.data[0] if wynik.data else None


def zapisz_stan(state_key, **pola):
    dane = {
        "state_key": state_key,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **pola,
    }

    supabase.table(TABLE_STATE).upsert(
        dane,
        on_conflict="state_key",
    ).execute()


def zaladuj_token_z_bazy():
    """
    GitHub Actions uruchamia nowy proces przy każdym cyklu.
    Dlatego pobieramy ostatni refresh token zapisany w Supabase.
    """
    stan = pobierz_stan("tuya_tokens")

    if stan and stan.get("refresh_token"):
        czujniki.REFRESH_TOKEN = stan["refresh_token"]

    # W nowym procesie nie znamy czasu ważności tokenu.
    # Wymuszamy odświeżenie z użyciem aktualnego refresh tokenu.
    czujniki.TOKEN_EXPIRES_AT = 0


def zapisz_token_do_bazy():
    """
    Zachowujemy odświeżony refresh token, aby kolejny
    proces GitHub Actions mógł z niego skorzystać.
    """
    if czujniki.REFRESH_TOKEN:
        zapisz_stan(
            "tuya_tokens",
            refresh_token=czujniki.REFRESH_TOKEN,
        )


def pobierz_zapisany_koniec(device_id):
    stan = pobierz_stan(f"device:{device_id}")

    if stan and stan.get("last_success_end_time_ms") is not None:
        return int(stan["last_success_end_time_ms"])

    return None


# --------------------------------------------------
# Pobieranie i zapisywanie pomiarów
# --------------------------------------------------

def zapisz_pomiary(logi, device_id, device_name):
    rekordy = []

    for log in logi:
        kod = log.get("code")

        if kod not in CODES:
            continue

        try:
            czas_ms = int(log["event_time"])
            wartosc = float(log["value"])
        except (KeyError, TypeError, ValueError):
            print(
                f"Pomijam nieprawidłowy log: "
                f"{device_name}, {kod}"
            )
            continue

        nazwa_pomiaru, jednostka = CODES[kod]

        # Tuya zapisuje temperaturę w dziesiątych częściach
        # stopnia. Przykład: 227 oznacza 22,7°C.
        if nazwa_pomiaru == "temperatura":
            wartosc /= 10

        rekordy.append({
            "device_id": device_id,
            "device_name": device_name,
            "event_time_ms": czas_ms,
            "measured_at": iso_utc(czas_ms),
            "code": kod,
            "value": wartosc,
            "unit": jednostka,
        })

    if not rekordy:
        return 0

    # Klucz główny tabeli uniemożliwia powstawanie duplikatów.
    # Powtórne pobranie tego samego pomiaru jest bezpieczne.
    supabase.table(TABLE_READINGS).upsert(
        rekordy,
        on_conflict="device_id,event_time_ms,code",
    ).execute()

    return len(rekordy)


def archiwizuj_czujnik(nazwa, device_id):
    teraz = teraz_ms()
    ostatni_koniec = pobierz_zapisany_koniec(device_id)

    if ostatni_koniec is None:
        poczatek = teraz - INITIAL_LOOKBACK_HOURS * 60 * 60 * 1000
        print(f"{nazwa}: pierwsze pobranie, do 7 dni historii.")
    else:
        poczatek = max(
            0,
            ostatni_koniec - OVERLAP_MINUTES * 60 * 1000,
        )

    koniec = teraz
    sciezka = f"/v2.0/cloud/thing/{device_id}/logs"

    params = {
        "type": "7",
        "start_time": str(poczatek),
        "end_time": str(koniec),
        "query_type": "1",
        "size": str(PAGE_SIZE),
    }

    zapisane_logi = 0
    przetworzone_strony = 0
    uzyte_klucze = set()

    print(f"{nazwa}: pobieram historię...")

    try:
        for numer_strony in range(1, MAX_PAGES + 1):
            wynik = czujniki.tuya_get(sciezka, params)

            # Zapis tokenu również po nieudanej próbie pobrania.
            zapisz_token_do_bazy()

            if not isinstance(wynik, dict):
                raise RuntimeError(
                    f"Nieoczekiwany format odpowiedzi Tuya: {type(wynik)}"
                )

            logi = wynik.get("logs", [])

            if not isinstance(logi, list):
                raise RuntimeError(
                    "Tuya zwróciła pole 'logs' w nieprawidłowym formacie."
                )

            zapisane_logi += zapisz_pomiary(
                logi,
                device_id,
                nazwa,
            )

            przetworzone_strony += 1

            print(
                f"  Strona {numer_strony}: "
                f"{len(logi)} logów; "
                f"zapisano {zapisane_logi} rekordów."
            )

            if not wynik.get("has_next"):
                break

            kolejny_klucz = wynik.get("next_row_key")

            if not kolejny_klucz:
                raise RuntimeError(
                    "Tuya zgłasza kolejną stronę, "
                    "ale nie zwraca next_row_key."
                )

            if kolejny_klucz in uzyte_klucze:
                raise RuntimeError(
                    "Wykryto powtórzony next_row_key. "
                    "Przerywam, aby uniknąć zapętlenia."
                )

            uzyte_klucze.add(kolejny_klucz)

            # To stronicowanie sprawdziliśmy wcześniej na żywym API.
            params["start_row_key"] = kolejny_klucz

        else:
            raise RuntimeError(
                f"Przekroczono limit {MAX_PAGES} stron."
            )

        # Postęp zapisujemy dopiero po pobraniu wszystkich stron
        # i udanym zapisie ich rekordów do Supabase.
        zapisz_stan(
            f"device:{device_id}",
            last_success_end_time_ms=koniec,
        )

        print(
            f"{nazwa}: OK; stron: {przetworzone_strony}; "
            f"zapisanych rekordów: {zapisane_logi}."
        )

    finally:
        # Zachowaj aktualny refresh token również w razie błędu.
        zapisz_token_do_bazy()


def main():
    print("=== Archiwizacja czujników Tuya ===")

    # Jeżeli nie uda się połączyć z Supabase albo odczytać stanu,
    # skrypt zakończy się błędem zamiast ignorować problem.
    zaladuj_token_z_bazy()

    bledy = []

    for nazwa, device_id in czujniki.DEVICES:
        try:
            archiwizuj_czujnik(nazwa, device_id)
        except Exception as exc:
            komunikat = f"{nazwa} ({device_id}): {exc}"
            bledy.append(komunikat)
            print(f"BŁĄD: {komunikat}")

    if bledy:
        raise RuntimeError(
            "Nie udało się zarchiwizować wszystkich czujników:\n"
            + "\n".join(bledy)
        )

    print("=== Archiwizacja zakończona pomyślnie ===")


if __name__ == "__main__":
    main()
