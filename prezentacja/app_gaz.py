"""
Szacowanie zużycia gazu na ogrzewanie z okresów grzania (port 5003).

Cechy okresów (pole pod krzywą nadwyżki temperatury grzejnika, wzrost
temperatury mieszkania) liczy Postgres: cechy_okresow_grzania()
z analityka/003_gas_usage.sql. Aplikacja dopasowuje do czystych pomiarów
z tabeli gas_calibration mnożniki kilku modeli i szacuje zużycie
w wybranym zakresie dat. Opis modelu: zuzycie_gazu/SPECYFIKACJA.md.

Zapisuje pomiary, więc używa klucza service_role i działa tylko lokalnie
(klucz nie trafia do przeglądarki).

Uruchomienie:
    ./.venv/bin/python3 prezentacja/app_gaz.py   ->  http://127.0.0.1:5003
"""

import os
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template_string, request, url_for
from supabase import create_client


# --------------------------------------------------
# Konfiguracja
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

app = Flask(__name__)
app.secret_key = os.urandom(16)  # tylko na komunikaty flash

STREFA = ZoneInfo("Europe/Warsaw")
DOMYSLNY_ZAKRES_DNI = 7
KWH_NA_M3 = float(os.environ.get("GAZ_KWH_NA_M3", "11.1"))
CENA_ZL_M3 = (
    float(os.environ["GAZ_CENA_ZL_M3"]) if os.environ.get("GAZ_CENA_ZL_M3") else None
)

# Cechy z cechy_okresow_grzania() sumowane po okresach zakresu.
CECHY = ["pole", "wzrost_mieszkania", "czas_h", "skok"]

# Modele: m³ = Σ współczynnik · Σ cecha. Pierwszy dopasowany z listy
# jest modelem domyślnym.
MODELE = [
    {
        "kod": "C",
        "wzor": "k · Σ pole + c · Σ wzrost mieszkania",
        "cechy": ["pole", "wzrost_mieszkania"],
        "symbole": ["k", "c"],
        "jednostki": ["m³/°C·h", "m³/°C"],
    },
    {
        "kod": "A",
        "wzor": "k · Σ pole",
        "cechy": ["pole"],
        "symbole": ["k"],
        "jednostki": ["m³/°C·h"],
    },
    {
        "kod": "B",
        "wzor": "x · Σ czas + y · Σ skok",
        "cechy": ["czas_h", "skok"],
        "symbole": ["x", "y"],
        "jednostki": ["m³/h", "m³/°C"],
    },
]

KOMUNIKAT_SQL = "czy wdrożono analityka/003_gas_usage.sql?"


# --------------------------------------------------
# Czas
# --------------------------------------------------

def z_formularza(tekst):
    """'2026-11-09T19:00' (czas polski z <input type=datetime-local>) -> datetime z strefą."""
    return datetime.fromisoformat(tekst).replace(tzinfo=STREFA)


def do_formularza(dt):
    return dt.astimezone(STREFA).strftime("%Y-%m-%dT%H:%M")


def z_bazy(tekst):
    return datetime.fromisoformat(tekst).astimezone(STREFA)


@app.template_filter("czas")
def filtr_czas(dt):
    if isinstance(dt, str):
        dt = z_bazy(dt)
    return dt.astimezone(STREFA).strftime("%d.%m.%Y %H:%M")


@app.template_filter("liczba")
def filtr_liczba(x, miejsca=2):
    if x is None:
        return "—"
    return f"{x:,.{miejsca}f}".replace(",", " ").replace(".", ",")


# --------------------------------------------------
# Dane z Supabase
# --------------------------------------------------

def cechy_zakresu(od, do):
    """Okresy grzania w zakresie [od, do) z cechami, sumy i ostrzeżenia."""
    wiersze = (
        supabase.rpc(
            "cechy_okresow_grzania",
            {"p_od": od.isoformat(), "p_do": do.isoformat()},
        )
        .execute()
        .data
        or []
    )

    okresy = []
    for w in wiersze:
        okres = {k: w[k] for k in ("rodzaj", "trwa", "garb_trwa")}
        for k in ("poczatek", "szczyt", "koniec_garbu", "czesc_od", "czesc_do"):
            okres[k] = z_bazy(w[k])
        for k in CECHY:
            okres[k] = float(w[k])
        okres["przed_od"] = okres["poczatek"] < okres["czesc_od"]
        okres["po_do"] = okres["garb_trwa"] or okres["koniec_garbu"] > okres["czesc_do"]
        okresy.append(okres)

    sumy = {k: sum(o[k] for o in okresy) for k in CECHY}

    ostrzezenia = []
    if any(o["przed_od"] for o in okresy):
        ostrzezenia.append(
            "Garb okresu grzania zaczął się przed początkiem zakresu — "
            "liczona jest tylko jego część w zakresie."
        )
    if any(o["po_do"] and not o["garb_trwa"] for o in okresy):
        ostrzezenia.append(
            "Garb okresu grzania trwa jeszcze po końcu zakresu — "
            "liczona jest tylko jego część w zakresie."
        )
    if any(o["garb_trwa"] for o in okresy):
        ostrzezenia.append(
            "Ostatni okres grzania jeszcze trwa (brak nowszych danych) — "
            "wynik może wzrosnąć."
        )

    return okresy, sumy, ostrzezenia


def pobierz_pomiary():
    """Czyste pomiary z gas_calibration wraz z cechami ich zakresów."""
    wiersze = (
        supabase.table("gas_calibration")
        .select("*")
        .order("poczatek")
        .execute()
        .data
        or []
    )

    pomiary = []
    for w in wiersze:
        od, do = z_bazy(w["poczatek"]), z_bazy(w["koniec"])
        okresy, sumy, ostrzezenia = cechy_zakresu(od, do)
        pomiary.append({
            "id": w["id"],
            "poczatek": od,
            "koniec": do,
            "zuzycie": float(w["zuzycie_m3"]),
            "stan_poczatek": w["stan_poczatek_m3"],
            "stan_koniec": w["stan_koniec_m3"],
            "aktywny": w["aktywny"],
            "notatka": w["notatka"] or "",
            "liczba_okresow": len(okresy),
            "sumy": sumy,
            "ostrzezenia": ostrzezenia,
        })
    return pomiary


# --------------------------------------------------
# Dopasowanie modeli
# --------------------------------------------------

def najmniejsze_kwadraty(X, y):
    """Najmniejsze kwadraty bez wyrazu wolnego dla 1-2 cech; None, gdy układ osobliwy."""
    if len(X[0]) == 1:
        sxx = sum(r[0] * r[0] for r in X)
        return [sum(r[0] * v for r, v in zip(X, y)) / sxx] if sxx > 0 else None

    a = sum(r[0] * r[0] for r in X)
    b = sum(r[0] * r[1] for r in X)
    d = sum(r[1] * r[1] for r in X)
    e = sum(r[0] * v for r, v in zip(X, y))
    f = sum(r[1] * v for r, v in zip(X, y))
    wyznacznik = a * d - b * b
    if abs(wyznacznik) < 1e-12 * max(a * d, 1e-12):
        return None
    return [(e * d - b * f) / wyznacznik, (a * f - b * e) / wyznacznik]


def nieujemne_kwadraty(X, y):
    """Najmniejsze kwadraty ze współczynnikami >= 0 (przegląd podzbiorów cech)."""
    liczba_cech = len(X[0])
    najlepsze, najmniejszy_blad = None, None

    for rozmiar in range(1, liczba_cech + 1):
        for podzbior in combinations(range(liczba_cech), rozmiar):
            wsp = najmniejsze_kwadraty([[r[i] for i in podzbior] for r in X], y)
            if wsp is None or any(w < 0 for w in wsp):
                continue
            pelne = [0.0] * liczba_cech
            for i, w in zip(podzbior, wsp):
                pelne[i] = w
            blad = sum(
                (sum(p * c for p, c in zip(pelne, r)) - v) ** 2
                for r, v in zip(X, y)
            )
            if najmniejszy_blad is None or blad < najmniejszy_blad - 1e-15:
                najlepsze, najmniejszy_blad = pelne, blad

    return najlepsze


def dopasuj(model, pomiary):
    X = [[p["sumy"][c] for c in model["cechy"]] for p in pomiary]
    y = [p["zuzycie"] for p in pomiary]

    if model["kod"] == "A":
        # Iloraz sum: dłuższe pomiary mają naturalnie większą wagę.
        suma_pol = sum(r[0] for r in X)
        return [sum(y) / suma_pol] if suma_pol > 0 else None

    return nieujemne_kwadraty(X, y)


def przewidz(wspolczynniki, model, sumy):
    return sum(w * sumy[c] for w, c in zip(wspolczynniki, model["cechy"]))


def ocen_modele(pomiary):
    """Dopasowane modele z błędem walidacji „zostaw jeden”."""
    aktywne = [p for p in pomiary if p["aktywny"]]
    wyniki = []

    for model in MODELE:
        wynik = dict(model, wspolczynniki=None, blad_m3=None, blad_proc=None)
        wyniki.append(wynik)
        if len(aktywne) < len(model["cechy"]):
            wynik["powod"] = f"potrzeba co najmniej {len(model['cechy'])} pomiarów"
            continue

        wynik["wspolczynniki"] = dopasuj(model, aktywne)
        if wynik["wspolczynniki"] is None:
            wynik["powod"] = "brak okresów grzania w pomiarach"
            continue

        # Pomiar przewidywany z modelu dopasowanego do pozostałych.
        if len(aktywne) > len(model["cechy"]):
            bledy, bledy_proc = [], []
            for i, pomiar in enumerate(aktywne):
                wsp = dopasuj(model, aktywne[:i] + aktywne[i + 1:])
                if wsp is None:
                    break
                blad = abs(przewidz(wsp, model, pomiar["sumy"]) - pomiar["zuzycie"])
                bledy.append(blad)
                if pomiar["zuzycie"] > 0:
                    bledy_proc.append(100 * blad / pomiar["zuzycie"])
            else:
                wynik["blad_m3"] = sum(bledy) / len(bledy)
                if bledy_proc:
                    wynik["blad_proc"] = sum(bledy_proc) / len(bledy_proc)

        if wynik["blad_m3"] is None:
            wynik["powod"] = f"ocena jakości od {len(model['cechy']) + 1} pomiarów"

    domyslny = next((m for m in wyniki if m["wspolczynniki"]), None)
    return wyniki, domyslny


# --------------------------------------------------
# Strony
# --------------------------------------------------

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zużycie gazu</title>
<style>
:root {
  --tlo: #f1f5f9; --karta: #ffffff; --tekst: #172033; --slaby: #64748b;
  --ramka: #e2e8f0; --akcent: #2563eb; --dobry: #166534; --dobry-tlo: #dcfce7;
  --uwaga: #92400e; --uwaga-tlo: #fef3c7; --zly: #991b1b; --zly-tlo: #fee2e2;
}
@media (prefers-color-scheme: dark) {
  :root {
    --tlo: #0f172a; --karta: #1e293b; --tekst: #e2e8f0; --slaby: #94a3b8;
    --ramka: #334155; --akcent: #60a5fa; --dobry: #86efac; --dobry-tlo: #14532d;
    --uwaga: #fcd34d; --uwaga-tlo: #422006; --zly: #fca5a5; --zly-tlo: #450a0a;
  }
}
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 24px 16px;
       background: var(--tlo); color: var(--tekst); }
main { max-width: 1100px; margin: 0 auto; }
h1 { margin: 0 0 4px; font-size: 26px; }
h2 { margin: 0 0 12px; font-size: 19px; }
.podtytul, .slaby { color: var(--slaby); font-size: 13px; }
.karta { background: var(--karta); border: 1px solid var(--ramka); border-radius: 12px;
         padding: 18px; margin: 18px 0; overflow-x: auto; }
form.wiersz { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--slaby); }
input { font: inherit; font-size: 14px; padding: 7px 9px; border: 1px solid var(--ramka);
        border-radius: 8px; background: var(--karta); color: var(--tekst); }
input[type=number] { width: 110px; }
button { font: inherit; font-size: 14px; font-weight: 500; padding: 7px 14px; cursor: pointer;
         border-radius: 8px; border: 1px solid var(--ramka); background: var(--karta); color: var(--tekst); }
button.glowny { background: var(--akcent); border-color: var(--akcent); color: white; }
button.maly { padding: 3px 9px; font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--ramka); text-align: left; white-space: nowrap; }
th { color: var(--slaby); font-weight: 600; font-size: 13px; }
td.l, th.l { text-align: right; font-variant-numeric: tabular-nums; }
tr.wylaczony td { opacity: .45; }
.wynik { font-size: 34px; font-weight: 700; margin: 6px 0; }
.wynik small { font-size: 16px; font-weight: 500; color: var(--slaby); }
.modele { display: flex; flex-wrap: wrap; gap: 24px; margin-top: 8px; font-size: 14px; }
.kom { padding: 9px 12px; border-radius: 8px; margin: 8px 0; font-size: 14px; }
.kom.uwaga { background: var(--uwaga-tlo); color: var(--uwaga); }
.kom.blad { background: var(--zly-tlo); color: var(--zly); }
.kom.ok { background: var(--dobry-tlo); color: var(--dobry); }
.znacznik { font-size: 12px; padding: 1px 7px; border-radius: 999px; background: var(--uwaga-tlo); color: var(--uwaga); }
</style>
</head>
<body>
<main>
<h1>Zużycie gazu na ogrzewanie</h1>
<div class="podtytul">
  Szacunek z okresów grzania (czujnik „Temp Grzejnik”), mnożniki dopasowane do czystych pomiarów z licznika.
</div>

{% for kategoria, tekst in get_flashed_messages(with_categories=true) %}
  <div class="kom {{ kategoria }}">{{ tekst }}</div>
{% endfor %}
{% if blad %}<div class="kom blad">{{ blad }}</div>{% endif %}

<section class="karta">
  <h2>Szacunek</h2>
  <form class="wiersz" method="get" action="{{ url_for('home') }}">
    <label>Od <input type="datetime-local" name="od" value="{{ od_formularz }}" required></label>
    <label>Do <input type="datetime-local" name="do" value="{{ do_formularz }}" required></label>
    <button class="glowny" type="submit">Policz</button>
  </form>

  {% if szacunek %}
    {% for o in szacunek.ostrzezenia %}<div class="kom uwaga">{{ o }}</div>{% endfor %}

    {% if domyslny %}
      <div class="wynik">
        {{ szacunek.prognozy[domyslny.kod] | liczba(1) }} m³
        <small>model {{ domyslny.kod }}
        · {{ (szacunek.prognozy[domyslny.kod] * kwh_na_m3) | liczba(0) }} kWh
        {% if cena %} · {{ (szacunek.prognozy[domyslny.kod] * cena) | liczba(2) }} zł{% endif %}</small>
      </div>
      <div class="modele">
        {% for m in modele if m.wspolczynniki and m.kod != domyslny.kod %}
          <span>Model {{ m.kod }}: <b>{{ szacunek.prognozy[m.kod] | liczba(1) }} m³</b></span>
        {% endfor %}
      </div>
    {% else %}
      <div class="kom uwaga">Brak dopasowanego modelu — dodaj co najmniej jeden czysty pomiar poniżej.</div>
    {% endif %}

    <p class="slaby">
      {{ szacunek.okresy | length }} okresów grzania ·
      Σ pole {{ szacunek.sumy.pole | liczba(1) }} °C·h ·
      Σ wzrost mieszkania {{ szacunek.sumy.wzrost_mieszkania | liczba(2) }} °C ·
      Σ czas {{ szacunek.sumy.czas_h | liczba(1) }} h ·
      Σ skok {{ szacunek.sumy.skok | liczba(1) }} °C
    </p>

    {% if szacunek.okresy %}
    <table>
      <tr>
        <th>Początek</th><th>Szczyt</th><th>Koniec garbu</th><th>Rodzaj</th>
        <th class="l">Czas [h]</th><th class="l">Skok [°C]</th>
        <th class="l">Pole [°C·h]</th><th class="l">Wzrost mieszk. [°C]</th><th></th>
      </tr>
      {% for o in szacunek.okresy %}
      <tr>
        <td>{{ o.poczatek | czas }}</td>
        <td>{{ o.szczyt | czas }}</td>
        <td>{{ o.koniec_garbu | czas }}{% if o.garb_trwa %} (trwa){% endif %}</td>
        <td>{{ o.rodzaj }}</td>
        <td class="l">{{ o.czas_h | liczba(2) }}</td>
        <td class="l">{{ o.skok | liczba(1) }}</td>
        <td class="l">{{ o.pole | liczba(2) }}</td>
        <td class="l">{{ o.wzrost_mieszkania | liczba(2) }}</td>
        <td>{% if o.przed_od or o.po_do %}<span class="znacznik">część w zakresie</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}
  {% endif %}
</section>

<section class="karta">
  <h2>Modele</h2>
  <table>
    <tr><th>Model</th><th>Wzór</th><th>Współczynniki</th><th class="l">Błąd „zostaw jeden”</th></tr>
    {% for m in modele %}
    <tr>
      <td><b>{{ m.kod }}</b>{% if domyslny and m.kod == domyslny.kod %} (domyślny){% endif %}</td>
      <td>m³ = {{ m.wzor }}</td>
      <td>
        {% if m.wspolczynniki %}
          {% for s in m.symbole %}{{ s }} = {{ m.wspolczynniki[loop.index0] | liczba(4) }} {{ m.jednostki[loop.index0] }}{% if not loop.last %}, {% endif %}{% endfor %}
        {% else %}<span class="slaby">{{ m.powod }}</span>{% endif %}
      </td>
      <td class="l">
        {% if m.blad_m3 is not none %}
          {{ m.blad_m3 | liczba(2) }} m³{% if m.blad_proc is not none %} ({{ m.blad_proc | liczba(0) }}%){% endif %}
        {% elif m.wspolczynniki %}<span class="slaby">{{ m.powod }}</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  <p class="slaby">
    Pole = ∫ nadwyżki różnicy grzejnik − kuchnia ponad różnicę z początku okresu.
    Wzrost mieszkania = o ile podniosła się średnia temperatura mieszkania od początku okresu do szczytu
    (energia na nagrzanie domu, np. przy przygotowaniu na przyjazd).
  </p>
</section>

<section class="karta">
  <h2>Czyste pomiary (kalibracja)</h2>
  <p class="slaby">
    Zakresy, w których gaz szedł wyłącznie na ogrzewanie (bez gotowania, kąpieli, pieca węglowego).
    Granice najlepiej ustawiać, gdy grzejnik nie grzeje. Wpisz zużycie albo oba stany licznika.
  </p>
  <form class="wiersz" method="post" action="{{ url_for('dodaj_pomiar', od=od_formularz, do=do_formularz) }}">
    <label>Od <input type="datetime-local" name="poczatek" required></label>
    <label>Stan licznika od [m³] <input type="number" step="0.001" min="0" name="stan_poczatek"></label>
    <label>Do <input type="datetime-local" name="koniec" required></label>
    <label>Stan licznika do [m³] <input type="number" step="0.001" min="0" name="stan_koniec"></label>
    <label>albo zużycie [m³] <input type="number" step="0.001" min="0" name="zuzycie"></label>
    <label>Notatka <input type="text" name="notatka" size="28"></label>
    <button class="glowny" type="submit">Dodaj pomiar</button>
  </form>

  {% if pomiary %}
  <table style="margin-top: 14px">
    <tr>
      <th>Od</th><th>Do</th><th class="l">Zużycie [m³]</th><th class="l">Okresy</th>
      <th class="l">Σ pole</th><th class="l">Σ wzrost</th>
      {% if domyslny %}<th class="l">Model {{ domyslny.kod }} [m³]</th><th class="l">Różnica</th>{% endif %}
      <th>Notatka</th><th></th>
    </tr>
    {% for p in pomiary %}
    <tr class="{{ '' if p.aktywny else 'wylaczony' }}">
      <td>{{ p.poczatek | czas }}</td>
      <td>{{ p.koniec | czas }}</td>
      <td class="l">{{ p.zuzycie | liczba(3) }}</td>
      <td class="l">{{ p.liczba_okresow }}</td>
      <td class="l">{{ p.sumy.pole | liczba(1) }}</td>
      <td class="l">{{ p.sumy.wzrost_mieszkania | liczba(2) }}</td>
      {% if domyslny %}
        <td class="l">{{ p.prognoza | liczba(2) }}</td>
        <td class="l">{{ (p.prognoza - p.zuzycie) | liczba(2) }}</td>
      {% endif %}
      <td>{{ p.notatka }}{% for o in p.ostrzezenia %}<br><span class="znacznik">{{ o }}</span>{% endfor %}</td>
      <td>
        <form method="post" action="{{ url_for('przelacz_pomiar', id=p.id, od=od_formularz, do=do_formularz) }}" style="display:inline">
          <button class="maly" type="submit">{{ 'Wyłącz' if p.aktywny else 'Włącz' }}</button>
        </form>
        <form method="post" action="{{ url_for('usun_pomiar', id=p.id, od=od_formularz, do=do_formularz) }}" style="display:inline"
              onsubmit="return confirm('Usunąć ten pomiar?')">
          <button class="maly" type="submit">Usuń</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  <p class="slaby">Wyłączony pomiar nie bierze udziału w dopasowaniu (np. gdy wyraźnie odstaje, bo jednak nie był czysty).</p>
  {% endif %}
</section>
</main>
</body>
</html>
"""


def wroc():
    return redirect(url_for("home", od=request.args.get("od"), do=request.args.get("do")))


@app.route("/")
def home():
    teraz = datetime.now(STREFA).replace(second=0, microsecond=0)
    blad = None
    try:
        od = z_formularza(request.args["od"]) if request.args.get("od") else teraz - timedelta(days=DOMYSLNY_ZAKRES_DNI)
        do = z_formularza(request.args["do"]) if request.args.get("do") else teraz
    except ValueError:
        od, do = teraz - timedelta(days=DOMYSLNY_ZAKRES_DNI), teraz
        blad = "Niepoprawna data — pokazuję ostatnie 7 dni."

    pomiary, modele, domyslny, szacunek = [], [], None, None
    try:
        pomiary = pobierz_pomiary()
        modele, domyslny = ocen_modele(pomiary)
        for p in pomiary:
            p["prognoza"] = (
                przewidz(domyslny["wspolczynniki"], domyslny, p["sumy"]) if domyslny else None
            )

        if do <= od:
            blad = "Koniec zakresu musi być późniejszy niż początek."
        else:
            okresy, sumy, ostrzezenia = cechy_zakresu(od, do)
            szacunek = {
                "okresy": okresy,
                "sumy": sumy,
                "ostrzezenia": ostrzezenia,
                "prognozy": {
                    m["kod"]: przewidz(m["wspolczynniki"], m, sumy)
                    for m in modele if m["wspolczynniki"]
                },
            }
    except Exception as exc:
        app.logger.exception("Błąd odczytu z Supabase")
        blad = f"Błąd odczytu z Supabase ({KOMUNIKAT_SQL}): {exc}"

    return render_template_string(
        HTML,
        od_formularz=do_formularza(od),
        do_formularz=do_formularza(do),
        szacunek=szacunek,
        pomiary=pomiary,
        modele=modele,
        domyslny=domyslny,
        kwh_na_m3=KWH_NA_M3,
        cena=CENA_ZL_M3,
        blad=blad,
    )


def liczba_z_formularza(nazwa):
    tekst = request.form.get(nazwa, "").strip().replace(",", ".")
    return float(tekst) if tekst else None


@app.post("/pomiary")
def dodaj_pomiar():
    try:
        od = z_formularza(request.form["poczatek"])
        do = z_formularza(request.form["koniec"])
        stan_od = liczba_z_formularza("stan_poczatek")
        stan_do = liczba_z_formularza("stan_koniec")
        zuzycie = liczba_z_formularza("zuzycie")
    except ValueError:
        flash("Niepoprawna data albo liczba.", "blad")
        return wroc()

    if stan_od is not None and stan_do is not None:
        if zuzycie is not None and abs(zuzycie - (stan_do - stan_od)) > 1e-6:
            flash("Zużycie nie zgadza się ze stanami licznika — wpisz jedno albo drugie.", "blad")
            return wroc()
        zuzycie = stan_do - stan_od
    elif zuzycie is None:
        flash("Wpisz zużycie albo oba stany licznika.", "blad")
        return wroc()

    if do <= od:
        flash("Koniec pomiaru musi być późniejszy niż początek.", "blad")
        return wroc()
    if zuzycie < 0:
        flash("Zużycie nie może być ujemne.", "blad")
        return wroc()

    try:
        supabase.table("gas_calibration").insert({
            "poczatek": od.isoformat(),
            "koniec": do.isoformat(),
            "zuzycie_m3": zuzycie,
            "stan_poczatek_m3": stan_od,
            "stan_koniec_m3": stan_do,
            "notatka": request.form.get("notatka", "").strip() or None,
        }).execute()
    except Exception as exc:
        app.logger.exception("Błąd zapisu pomiaru")
        flash(f"Nie udało się zapisać pomiaru ({KOMUNIKAT_SQL}): {exc}", "blad")
        return wroc()

    flash(f"Dodano pomiar: {zuzycie:.3f} m³.".replace(".", ",", 1), "ok")
    return wroc()


@app.post("/pomiary/<int:id>/przelacz")
def przelacz_pomiar(id):
    try:
        wiersz = supabase.table("gas_calibration").select("aktywny").eq("id", id).execute().data
        if wiersz:
            supabase.table("gas_calibration").update(
                {"aktywny": not wiersz[0]["aktywny"]}
            ).eq("id", id).execute()
    except Exception as exc:
        app.logger.exception("Błąd zmiany pomiaru")
        flash(f"Nie udało się zmienić pomiaru: {exc}", "blad")
    return wroc()


@app.post("/pomiary/<int:id>/usun")
def usun_pomiar(id):
    try:
        supabase.table("gas_calibration").delete().eq("id", id).execute()
    except Exception as exc:
        app.logger.exception("Błąd usuwania pomiaru")
        flash(f"Nie udało się usunąć pomiaru: {exc}", "blad")
    return wroc()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5003,
        debug=False,
    )
