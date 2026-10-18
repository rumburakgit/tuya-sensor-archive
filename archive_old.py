import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

import czujniki


load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)

TABLE_READINGS = "sensor_readings"
TABLE_STATE = "sensor_archive_state"

# Pierwsze uruchomienie: maksymalnie 7 dni historii.
FIRST_RUN_MS = 7 * 24 * 60 * 60 * 1000

# Nakładanie zakresów pomaga wychwycić opóźnione raporty.
OVERLAP_MS = 60 * 60 * 1000

PAGE_SIZE = 100
MAX_PAGES = 100
BATCH_SIZE = 300

CODES = {"va_temperature", "va_humidity"}


def pobierz_stan(state_key):
    response = (
        supabase.table(TABLE_STATE)
        .select("*")
        .eq("state_key", state_key)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def zapisz_token():
    """Zachowuje najnowszy refresh token również po zakończeniu zadania."""
    token = czujniki.REFRESH_TOKEN
    if not token:
        return

    supabase.table(TABLE_STATE).upsert(
        {
            "state_key": "tuya_tokens",
            "refresh_token": token,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="state_key",
    ).execute()


def tuya_get_i_zachowaj_token(path, params):
    """Zachowuje token nawet wtedy, gdy zapytanie Tuya zakończy się błędem."""
    try:
        return czujniki.tuya_get(path, params)
    finally:
        zapisz_token()


def pobierz_logi(device_id, start_ms, end_ms):
    """Pobiera wszystkie strony logów w danym przedziale czasu."""
    path = f"/v2.0/cloud/thing/{device_id}/logs"
    start_row_key = None
    widziane_kursory = set()
    wszystkie = []

    for numer_strony in range(MAX_PAGES):
        params = {
            "type": "7",
            "start_time": str(start_ms),
            "end_time": str(end_ms),
            "query_type": "1",
            "size": str(PAGE_SIZE),
            "codes": ",".join(sorted(CODES)),
        }

        if start_row_key:
            params["start_row_key"] = start_row_key

        wynik = tuya_get_i_zachowaj_token(path, params)

        if not isinstance(wynik, dict):
            raise RuntimeError("Nieoczekiwany format odpowiedzi Tuya")

        logi = wynik.get("logs", [])
        if not isinstance(logi, list):
            raise RuntimeError("Pole logs w odpowiedzi Tuya nie jest listą")

        wszystkie.extend(logi)

        if not wynik.get("has_next", False):
            return wszystkie

        nowy_kursor = wynik.get("current_row_key")
        if not nowy_kursor:
            raise RuntimeError(
                "Tuya informuje o następnej stronie, "
                "ale nie zwraca current_row_key"
            )

        nowy_kursor = str(nowy_kursor)

        if nowy_kursor in widziane_kursory:
            raise RuntimeError(
                "Powtórzony kursor paginacji Tuya; "
                "przerywam, aby nie zapętlić pobierania"
            )

        widziane_kursory.add(nowy_kursor)
        start_row_key = nowy_kursor

    raise RuntimeError(
        f"Przekroczono limit {MAX_PAGES} stron; "
        "postęp synchronizacji nie zostanie przesunięty"
    )


def przygotuj_rekordy(nazwa, device_id, logi):
    rekordy = []

    for log in logi:
        kod = log.get("code")
        if kod not in CODES:
            continue

        try:
            czas_ms = int(log["event_time"])
            wartosc_surowa = float(log["value"])
        except (KeyError, TypeError, ValueError):
            continue

        if kod == "va_temperature":
            wartosc = wartosc_surowa / 10
            jednostka = "°C"
        else:
            wartosc = wartosc_surowa
            jednostka = "%"

        czas_utc = datetime.fromtimestamp(
            czas_ms / 1000,
            tz=timezone.utc,
        ).isoformat()

        rekordy.append(
            {
                "device_id": device_id,
                "device_name": nazwa,
                "event_time_ms": czas_ms,
                "measured_at": czas_utc,
                "code": kod,
                "value": wartosc,
                "unit": jednostka,
            }
        )

    return rekordy


def zapisz_rekordy(rekordy):
    """Zapisuje porcjami; istniejące rekordy pozostawia bez zmian."""
    zapisane_do_przetworzenia = len(rekordy)

    for poczatek in range(0, len(rekordy), BATCH_SIZE):
        partia = rekordy[poczatek:poczatek + BATCH_SIZE]

        (
            supabase.table(TABLE_READINGS)
            .upsert(
                partia,
                on_conflict="device_id,event_time_ms,code",
                ignore_duplicates=True,
            )
            .execute()
        )

    return zapisane_do_przetworzenia


def ustaw_postep(device_id, koniec_ms):
    supabase.table(TABLE_STATE).upsert(
        {
            "state_key": f"device:{device_id}",
            "last_success_end_time_ms": koniec_ms,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="state_key",
    ).execute()


def archiwizuj_czujnik(nazwa, device_id, teraz_ms):
    stan = pobierz_stan(f"device:{device_id}")
    poprzedni_koniec = (
        stan.get("last_success_end_time_ms") if stan else None
    )

    if poprzedni_koniec is None:
        start_ms = teraz_ms - FIRST_RUN_MS
        tryb = "pierwsze pobranie"
    else:
        start_ms = max(
            teraz_ms - FIRST_RUN_MS,
            int(poprzedni_koniec) - OVERLAP_MS,
        )
        tryb = "synchronizacja przyrostowa"

    print(
        f"\n[{nazwa}] {tryb}: "
        f"{datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat()} "
        f"— {datetime.fromtimestamp(teraz_ms / 1000, timezone.utc).isoformat()}"
    )

    logi = pobierz_logi(device_id, start_ms, teraz_ms)
    rekordy = przygotuj_rekordy(nazwa, device_id, logi)

    # Jeśli pobieranie lub zapis się nie powiedzie, nie przesuwamy postępu.
    liczba = zapisz_rekordy(rekordy)

    ustaw_postep(device_id, teraz_ms)

    print(
        f"[{nazwa}] pobrane logi: {len(logi)}, "
        f"rekordy kwalifikujące się do zapisu: {liczba}"
    )


def main():
    if not czujniki.CLIENT_ID or not czujniki.CLIENT_SECRET:
        raise RuntimeError("Brakuje danych dostępowych Tuya")

    # Przy kolejnych uruchomieniach używamy najnowszego tokenu
    # zapisanego w bazie, nie tylko wartości z pliku .env.
    stan_tokena = pobierz_stan("tuya_tokens")
    if stan_tokena and stan_tokena.get("refresh_token"):
        czujniki.REFRESH_TOKEN = stan_tokena["refresh_token"]
        czujniki.ACCESS_TOKEN = ""
        czujniki.TOKEN_EXPIRES_AT = 0

    teraz_ms = int(time.time() * 1000)

    # Przerwanie na błędzie jest celowe: GitHub Actions oznaczy zadanie
    # jako nieudane, a kolejny przebieg ponowi synchronizację.
    for nazwa, device_id in czujniki.DEVICES:
        archiwizuj_czujnik(nazwa, device_id, teraz_ms)

    print("\nArchiwizacja zakończona poprawnie.")


if __name__ == "__main__":
    main()
