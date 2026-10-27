"""
Wykresy temperatury z archiwum Supabase w Apache ECharts — wersja Streamlit
(do wdrożenia w Streamlit Community Cloud). Wykresy i dane pochodzą
z app_echarts.py; zakres wybiera się kontrolkami Streamlit.

Uruchomienie lokalne:
    ./.venv/bin/python3 -m streamlit run prezentacja/streamlit_app.py
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

# Lokalnie klucze są w prezentacja/.env, w Streamlit Cloud — w Secrets.
# app_archive czyta zmienne środowiskowe, więc sekrety przepisujemy do nich
# jawnie (Streamlit robi to sam tylko w niektórych ścieżkach uruchomienia).
load_dotenv(Path(__file__).resolve().parent / ".env")

try:
    nazwy_sekretow = list(st.secrets.keys())
except Exception:
    # Brak pliku sekretów (np. lokalnie) — zostaje .env.
    nazwy_sekretow = []

# (zmienna środowiskowa, nazwy w Secrets — SUPABASE_KEY to popularna nazwa klucza anon)
for nazwa, nazwy_w_sekretach in (
    ("SUPABASE_URL", ("SUPABASE_URL",)),
    ("SUPABASE_ANON_KEY", ("SUPABASE_ANON_KEY", "SUPABASE_KEY")),
):
    for nazwa_w_sekretach in nazwy_w_sekretach:
        if nazwa_w_sekretach in nazwy_sekretow and not os.environ.get(nazwa):
            os.environ[nazwa] = str(st.secrets[nazwa_w_sekretach])

if not os.environ.get("SUPABASE_URL") or not (
    os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
):
    st.error(
        "Brak konfiguracji Supabase. W Streamlit Cloud: Settings → Secrets, "
        "na najwyższym poziomie (bez nagłówka [sekcji]):\n\n"
        '`SUPABASE_URL = "https://<id-projektu>.supabase.co"`\n\n'
        '`SUPABASE_ANON_KEY = "sb_publishable_..."`\n\n'
        "Po zapisaniu: Reboot app.\n\n"
        "Nazwy wpisów widoczne teraz w Secrets: "
        + (", ".join(f"`{n}`" for n in nazwy_sekretow) or "brak")
    )
    st.stop()

import app_echarts  # noqa: E402 — wymaga zmiennych sprawdzonych wyżej


GODZINA_MS = 3600000
KROK_PRZESUNIECIA_H = 12
DOMYSLNY_ZAKRES = "48h"
ZAKRESY = {
    "2h": 2,
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "48h": 48,
    "7 dni": 7 * 24,
    "Cała historia": None,
}
STREFA = ZoneInfo("Europe/Warsaw")
# Jak długo trzymać pobrane dane w pamięci podręcznej (s).
PAMIEC_PODRECZNA_S = 300


@st.cache_data(ttl=PAMIEC_PODRECZNA_S, show_spinner="Wczytywanie danych…")
def dane_wykresow(od_ms, do_ms):
    return app_echarts.dane_wykresow(od_ms, do_ms)


def na_czas_lokalny(ms):
    return datetime.fromtimestamp(ms / 1000, tz=STREFA).strftime("%d.%m.%Y %H:%M")


def wczesniej():
    st.session_state.przesuniecie_h += KROK_PRZESUNIECIA_H


def pozniej():
    st.session_state.przesuniecie_h = max(
        0, st.session_state.przesuniecie_h - KROK_PRZESUNIECIA_H
    )


def teraz():
    st.session_state.przesuniecie_h = 0
    st.session_state.zakres = DOMYSLNY_ZAKRES


def strona_wykresow(dane, od_ms, do_ms):
    """Samodzielna strona HTML z wykresami ECharts i wstawionymi danymi."""
    # "</" w JSON-ie zamknęłoby znacznik <script>.
    dane_json = json.dumps(dane).replace("</", "<\\/")

    return f"""
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<script src="{app_echarts.ECHARTS_JS}"></script>
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 4px;
  font-family: system-ui, sans-serif;
  background: #f1f5f9;
  color: #172033;
}}
{app_echarts.STYL_WYKRESOW}
</style>
</head>
<body>
{app_echarts.WYKRESY_HTML}
<script>
{app_echarts.WYKRESY_JS}
pokazDane({dane_json}, {json.dumps(od_ms)}, {json.dumps(do_ms)});
</script>
</body>
</html>
"""


# --------------------------------------------------
# Strona
# --------------------------------------------------

st.set_page_config(
    page_title="Archiwum — ECharts",
    page_icon="🌦️",
    layout="wide",
)

st.title("🌦️ Moja stacja pogodowa — ECharts")

if "przesuniecie_h" not in st.session_state:
    st.session_state.przesuniecie_h = 0
if "zakres" not in st.session_state:
    st.session_state.zakres = DOMYSLNY_ZAKRES

kolumny = st.columns([6, 1, 1, 1], vertical_alignment="bottom")

with kolumny[0]:
    st.segmented_control("Czas trwania", list(ZAKRESY), key="zakres")

# Odznaczenie przycisku w segmented_control daje None — traktujemy jak domyślny.
czas_trwania_h = ZAKRESY[st.session_state.zakres or DOMYSLNY_ZAKRES]
cala_historia = czas_trwania_h is None

kolumny[1].button(
    "◀ 12h", on_click=wczesniej, disabled=cala_historia,
    help="12 godzin wcześniej", use_container_width=True,
)
kolumny[2].button(
    "12h ▶", on_click=pozniej,
    disabled=cala_historia or st.session_state.przesuniecie_h == 0,
    help="12 godzin później", use_container_width=True,
)
kolumny[3].button("Teraz", on_click=teraz, use_container_width=True)

# Koniec zakresu zaokrąglony do minuty, żeby pamięć podręczna działała.
teraz_ms = int(datetime.now(timezone.utc).timestamp() // 60 * 60000)

if cala_historia:
    od_ms, do_ms = None, teraz_ms
else:
    do_ms = teraz_ms - st.session_state.przesuniecie_h * GODZINA_MS
    od_ms = do_ms - czas_trwania_h * GODZINA_MS

try:
    dane = dane_wykresow(od_ms, do_ms)
except Exception as exc:
    st.error(f"Nie udało się pobrać danych z Supabase: {exc}")
    st.stop()

st.caption(
    f"Zakres: {na_czas_lokalny(dane['poczatek_ms'])} – {na_czas_lokalny(do_ms)} "
    f"(czas polski) · wczytane rekordy: {dane['liczba_rekordow']} · "
    f"dane odświeżane co {PAMIEC_PODRECZNA_S // 60} min"
)

# Ramka sama dopasowuje wysokość do czterech wykresów.
st.iframe(strona_wykresow(dane, od_ms, do_ms), height="content")
