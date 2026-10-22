
import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode, quote
from dotenv import load_dotenv, set_key

load_dotenv()

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
CLIENT_ID = os.environ["TUYA_CLIENT_ID"]
CLIENT_SECRET = os.environ["TUYA_CLIENT_SECRET"]
BASE_URL = "https://openapi.tuyaeu.com"

ACCESS_TOKEN = os.getenv("TUYA_ACCESS_TOKEN", "")
REFRESH_TOKEN = os.getenv("TUYA_REFRESH_TOKEN", "")
TOKEN_EXPIRES_AT = 0

DEVICES = [
    ("Temp Kuchnias", "bf09f3b9302d16a74froza"),
    ("Temp Grzejnik", "bf333171b6295a9253wx0y"),
    ("Temp Zielony", "bff67b6528b7b61ab34bab"),
    ("Temp Jadalnia", "bf820e12f85f729ddaxkre"),
    ("Temp Przedpokój", "bf587bca663683547dxy4p"),
    ("Temp żółty pok", "bfbf68aa252e1f7893a3zz"),
    
]

PROG_BRAKU_DANYCH_MIN = 10


def podpis(path, timestamp, access_token=""):
    string_to_sign = (
        "GET\n"
        + hashlib.sha256(b"").hexdigest()
        + "\n\n"
        + path
    )
    sign_str = CLIENT_ID + access_token + timestamp + string_to_sign

    return hmac.new(
        CLIENT_SECRET.encode(),
        sign_str.encode(),
        hashlib.sha256,
    ).hexdigest().upper()


def zapisz_tokeny(access_token, refresh_token, expire_time):
    global ACCESS_TOKEN, REFRESH_TOKEN, TOKEN_EXPIRES_AT

    ACCESS_TOKEN = access_token
    REFRESH_TOKEN = refresh_token
    TOKEN_EXPIRES_AT = time.time() + int(expire_time) - 120

    set_key(ENV_FILE, "TUYA_ACCESS_TOKEN", ACCESS_TOKEN)
    set_key(ENV_FILE, "TUYA_REFRESH_TOKEN", REFRESH_TOKEN)


def zapytanie_tokenowe(path):
    timestamp = str(int(time.time() * 1000))
    signature = podpis(path, timestamp)

    response = requests.get(
        BASE_URL + path,
        headers={
            "client_id": CLIENT_ID,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "sign": signature,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Tuya token: {data.get('code')} {data.get('msg', '')}"
        )

    return data["result"]


def pobierz_nowy_token():
    result = zapytanie_tokenowe("/v1.0/token?grant_type=1")
    zapisz_tokeny(
        result["access_token"],
        result["refresh_token"],
        result.get("expire_time", result.get("expire", 7200)),
    )


def odswiez_token():
    if not REFRESH_TOKEN:
        pobierz_nowy_token()
        return

    path = "/v1.0/token/" + quote(REFRESH_TOKEN, safe="")

    try:
        result = zapytanie_tokenowe(path)
        zapisz_tokeny(
            result["access_token"],
            result["refresh_token"],
            result.get("expire_time", result.get("expire", 7200)),
        )
    except Exception:
        pobierz_nowy_token()


def zapewnij_token():
    if not ACCESS_TOKEN or time.time() >= TOKEN_EXPIRES_AT:
        odswiez_token()


def tuya_get(path, params=None):
    global TOKEN_EXPIRES_AT

    params = params or {}
    query = urlencode(sorted(params.items()))
    url_path = path + ("?" + query if query else "")

    for proba in range(2):
        zapewnij_token()

        timestamp = str(int(time.time() * 1000))
        signature = podpis(url_path, timestamp, ACCESS_TOKEN)

        headers = {
            "client_id": CLIENT_ID,
            "access_token": ACCESS_TOKEN,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "sign": signature,
        }

        response = requests.get(
            BASE_URL + path,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("success"):
            return data["result"]

        if proba == 0 and str(data.get("code")) in (
            "1010", "1011", "1012"
        ):
            TOKEN_EXPIRES_AT = 0
            odswiez_token()
            continue

        raise RuntimeError(
            f"Tuya API: {data.get('code')} {data.get('msg', '')}"
        )

    raise RuntimeError("Nie udało się wykonać zapytania Tuya API")


def pobierz_historie(device_id):
    teraz = int(time.time() * 1000)

    wynik = tuya_get(
        f"/v2.0/cloud/thing/{device_id}/logs",
        {
            "type": "7",
            "start_time": str(teraz - 60 * 60 * 1000),
            "end_time": str(teraz),
            "query_type": "1",
            "size": "100",
        },
    )

    punkty = {"temperatura": [], "wilgotnosc": []}

    for log in wynik.get("logs", []):
        try:
            czas = int(log["event_time"])
            wartosc = float(log["value"])
        except (KeyError, ValueError, TypeError):
            continue

        if log.get("code") == "va_temperature":
            punkty["temperatura"].append(
                {"x": czas, "y": wartosc / 10}
            )
        elif log.get("code") == "va_humidity":
            punkty["wilgotnosc"].append(
                {"x": czas, "y": wartosc}
            )

    for lista in punkty.values():
        lista.sort(key=lambda p: p["x"])

    return punkty


def pobierz_zdarzenie_polaczenia(device_id):
    """Zwraca 'Offline', 'Online' albo None, gdy brak potwierdzenia."""
    teraz = int(time.time() * 1000)

    try:
        wynik = tuya_get(
            f"/v1.0/iot-03/devices/{device_id}/logs",
            {
                "event_types": "1,2",
                "start_time": str(teraz - 7 * 24 * 60 * 60 * 1000),
                "end_time": str(teraz),
                "size": "50",
            },
        )
    except Exception:
        # Jeśli API zdarzeń nie jest dostępne, status ustalimy
        # na podstawie wieku ostatniego raportu.
        return None

    logi = wynik.get("logs", wynik.get("list", []))
    if not isinstance(logi, list) or not logi:
        return None

    def czas_logu(log):
        try:
            return int(log.get("event_time", 0))
        except (TypeError, ValueError):
            return 0

    ostatni = max(logi, key=czas_logu)
    identyfikator = str(
        ostatni.get("event_id", ostatni.get("event_type", ""))
    ).lower()

    if identyfikator in ("2", "offline"):
        return "Offline"
    if identyfikator in ("1", "online"):
        return "Online"

    return None


def pobierz_czujniki():
    wyniki = []
    teraz = int(time.time() * 1000)

    for nazwa, device_id in DEVICES:
        try:
            # Dłuższy zakres pozwala zachować ostatni pomiar,
            # nawet gdy czujnik nie raportuje już od godziny.
            wynik = tuya_get(
                f"/v2.0/cloud/thing/{device_id}/logs",
                {
                    "type": "7",
                    "start_time": str(teraz - 7 * 24 * 60 * 60 * 1000),
                    "end_time": str(teraz),
                    "query_type": "1",
                    "size": "100",
                },
            )

            odczyty = {}

            for log in wynik.get("logs", []):
                kod = log.get("code")
                if kod not in ("va_temperature", "va_humidity"):
                    continue

                try:
                    czas = int(log["event_time"])
                    wartosc = float(log["value"])
                except (KeyError, ValueError, TypeError):
                    continue

                if kod not in odczyty or czas > odczyty[kod][0]:
                    odczyty[kod] = (czas, wartosc)

            temp = odczyty.get("va_temperature")
            wilg = odczyty.get("va_humidity")
            ostatni_czas = max(
                (x[0] for x in (temp, wilg) if x),
                default=None,
            )

            wiek_min = (
                (teraz - ostatni_czas) / 60000
                if ostatni_czas is not None
                else None
            )

            zdarzenie = pobierz_zdarzenie_polaczenia(device_id)

            if zdarzenie == "Offline":
                status = "Offline"
            elif wiek_min is None:
                status = "Brak danych"
            elif wiek_min > PROG_BRAKU_DANYCH_MIN:
                status = "Brak nowych danych"
            elif zdarzenie == "Online":
                status = "Działa"
            else:
                status = "Działa"

            wyniki.append({
                "nazwa": nazwa,
                "temperatura": round(temp[1] / 10, 1) if temp else None,
                "wilgotnosc": round(wilg[1], 1) if wilg else None,
                "czas": ostatni_czas,
                "wiek_min": round(wiek_min, 1) if wiek_min is not None else None,
                "status": status,
                "blad": None,
            })

        except Exception as e:
            wyniki.append({
                "nazwa": nazwa,
                "temperatura": None,
                "wilgotnosc": None,
                "czas": None,
                "wiek_min": None,
                "status": "Błąd odczytu",
                "blad": str(e),
            })

    return wyniki


if __name__ == "__main__":
    for czujnik in pobierz_czujniki():
        print(czujnik)
