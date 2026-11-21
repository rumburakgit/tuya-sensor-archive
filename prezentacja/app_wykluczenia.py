"""
Wykluczanie błędnych odczytów czujników (port 5004).

Czujnik ze słabą baterią potrafi podawać bzdury (np. 35,2 °C w zimnym
pokoju). Tu wpisuje się okresy, z których odczyty danego czujnika mają
być pomijane. Wpisy trafiają do tabeli sensor_exclusions; widok
sensor_readings_czyste (analityka/000_sensor_exclusions.sql) pomija je
w wykresach, średnich, okresach grzania i szacowaniu gazu. Po każdej
zmianie wyzwalacz w bazie sam przelicza średnie i okresy grzania
w zakresie wykluczenia. Opis: wykluczenia/SPECYFIKACJA.md.

Surowe odczyty (również wykluczone) pokazuje tylko ta strona.

Zapisuje wykluczenia, więc używa klucza service_role i działa tylko
lokalnie (klucz nie trafia do przeglądarki).

Uruchomienie:
    ./.venv/bin/python3 prezentacja/app_wykluczenia.py   ->  http://127.0.0.1:5004
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template_string, request, url_for
from supabase import create_client

from app_archive import DEVICES


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

ECHARTS_JS = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"

STREFA = ZoneInfo("Europe/Warsaw")
DOMYSLNY_ZAKRES_DNI = 7
PAGE_SIZE = 1000

NAZWY = {device_id: nazwa for nazwa, device_id in DEVICES}

# Wartość pola „pomiar” -> kolumna code (None = wszystkie pomiary czujnika).
POMIARY = {
    "oba": None,
    "va_temperature": "va_temperature",
    "va_humidity": "va_humidity",
}
NAZWY_POMIAROW = {None: "temperatura i wilgotność", "va_temperature": "temperatura",
                  "va_humidity": "wilgotność"}

KOMUNIKAT_SQL = "czy wdrożono analityka/000_sensor_exclusions.sql?"


# --------------------------------------------------
# Czas
# --------------------------------------------------

def z_formularza(tekst):
    """'2026-11-20T17:58' albo '...T17:58:41' (czas polski) -> datetime ze strefą."""
    return datetime.fromisoformat(tekst).replace(tzinfo=STREFA)


def koniec_z_formularza(tekst):
    """Koniec okresu włącznie: wpisana minuta bez sekund obejmuje całą minutę."""
    do = z_formularza(tekst)
    if do.second == 0 and do.microsecond == 0:
        do += timedelta(seconds=59, microseconds=999000)
    return do


def do_formularza(dt):
    return dt.astimezone(STREFA).strftime("%Y-%m-%dT%H:%M")


def z_bazy(tekst):
    return datetime.fromisoformat(tekst).astimezone(STREFA)


def odczytow(n):
    """1 odczyt, 2 odczyty, 5 odczytów, 22 odczyty."""
    if n == 1:
        return "1 odczyt"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} odczyty"
    return f"{n} odczytów"


@app.template_filter("czas")
def filtr_czas(dt):
    if isinstance(dt, str):
        dt = z_bazy(dt)
    return dt.astimezone(STREFA).strftime("%d.%m.%Y %H:%M:%S").removesuffix(":00")


# --------------------------------------------------
# Dane z Supabase
# --------------------------------------------------

def surowe_odczyty(device_id, od, do):
    """Wszystkie odczyty czujnika z sensor_readings (również wykluczone)."""
    wszystkie, offset = [], 0
    while True:
        rekordy = (
            supabase.table("sensor_readings")
            .select("event_time_ms,measured_at,code,value")
            .eq("device_id", device_id)
            .gte("measured_at", od.isoformat())
            .lte("measured_at", do.isoformat())
            .order("event_time_ms")
            .order("code")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        wszystkie.extend(rekordy)
        if len(rekordy) < PAGE_SIZE:
            return wszystkie
        offset += PAGE_SIZE


def pobierz_wykluczenia():
    wiersze = (
        supabase.table("sensor_exclusions")
        .select("*")
        .order("od", desc=True)
        .execute()
        .data
        or []
    )
    for w in wiersze:
        w["od_dt"], w["do_dt"] = z_bazy(w["od"]), z_bazy(w["do_"])
        w["nazwa"] = NAZWY.get(w["device_id"], w["device_id"])
        w["pomiar"] = NAZWY_POMIAROW.get(w["code"], w["code"])
    return wiersze


def odczyty_w_okresie(device_id, code, od, do):
    """Liczba surowych odczytów w okresie i (najwyżej PAGE_SIZE) same odczyty."""
    zapytanie = (
        supabase.table("sensor_readings")
        .select("code,value", count="exact")
        .eq("device_id", device_id)
        .gte("measured_at", od.isoformat())
        .lte("measured_at", do.isoformat())
    )
    if code:
        zapytanie = zapytanie.eq("code", code)
    wynik = zapytanie.limit(PAGE_SIZE).execute()
    return wynik.count or 0, wynik.data or []


def obejmuje(wykluczenie, code, chwila):
    return (
        wykluczenie["aktywny"]
        and (wykluczenie["code"] is None or wykluczenie["code"] == code)
        and wykluczenie["od_dt"] <= chwila <= wykluczenie["do_dt"]
    )


def dane_wykresu(odczyty, wykluczenia):
    """Punkty [ms, wartość] osobno dla odczytów zwykłych i wykluczonych."""
    serie = {
        code: {"zwykle": [], "wykluczone": []}
        for code in ("va_temperature", "va_humidity")
    }
    for r in odczyty:
        if r["code"] not in serie:
            continue
        chwila = z_bazy(r["measured_at"])
        rodzaj = (
            "wykluczone"
            if any(obejmuje(w, r["code"], chwila) for w in wykluczenia)
            else "zwykle"
        )
        serie[r["code"]][rodzaj].append([int(r["event_time_ms"]), float(r["value"])])
    return serie


# --------------------------------------------------
# Strona
# --------------------------------------------------

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wykluczenia odczytów</title>
<script src="{{ echarts_js }}"></script>
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
input, select { font: inherit; font-size: 14px; padding: 7px 9px; border: 1px solid var(--ramka);
        border-radius: 8px; background: var(--karta); color: var(--tekst); }
button { font: inherit; font-size: 14px; font-weight: 500; padding: 7px 14px; cursor: pointer;
         border-radius: 8px; border: 1px solid var(--ramka); background: var(--karta); color: var(--tekst); }
button.glowny { background: var(--akcent); border-color: var(--akcent); color: white; }
button.maly { padding: 3px 9px; font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--ramka); text-align: left; white-space: nowrap; }
th { color: var(--slaby); font-weight: 600; font-size: 13px; }
td.l, th.l { text-align: right; font-variant-numeric: tabular-nums; }
tr.wylaczony td { opacity: .45; }
.kom { padding: 9px 12px; border-radius: 8px; margin: 8px 0; font-size: 14px; }
.kom.uwaga { background: var(--uwaga-tlo); color: var(--uwaga); }
.kom.blad { background: var(--zly-tlo); color: var(--zly); }
.kom.ok { background: var(--dobry-tlo); color: var(--dobry); }
#wykres { height: 420px; }
#podglad { margin-top: 10px; }
@media (max-width: 600px) { #wykres { height: 320px; } }
</style>
</head>
<body>
<main>
<h1>Wykluczenia odczytów</h1>
<div class="podtytul">
  Odczyty z wykluczonych okresów są pomijane na wykresach, w średnich, okresach grzania i szacowaniu gazu.
  Surowe dane zostają w bazie — wykluczenie można wyłączyć albo usunąć.
</div>

{% for kategoria, tekst in get_flashed_messages(with_categories=true) %}
  <div class="kom {{ kategoria }}">{{ tekst }}</div>
{% endfor %}
{% if blad %}<div class="kom blad">{{ blad }}</div>{% endif %}

<section class="karta">
  <h2>Surowe odczyty</h2>
  <form class="wiersz" method="get" action="{{ url_for('home') }}">
    <label>Czujnik
      <select name="czujnik">
        {% for nazwa, id in urzadzenia %}
          <option value="{{ id }}" {{ 'selected' if id == czujnik }}>{{ nazwa }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Od <input type="datetime-local" name="od" value="{{ od_formularz }}" required></label>
    <label>Do <input type="datetime-local" name="do" value="{{ do_formularz }}" required></label>
    <button class="glowny" type="submit">Pokaż</button>
  </form>
  <div id="wykres"></div>
  <p class="slaby">
    Przeciągnij myszą po wykresie, żeby zaznaczyć okres — wypełni pola formularza niżej.
    Kółko myszy przybliża. Szare punkty i pasy: odczyty już wykluczone.
  </p>
</section>

<section class="karta">
  <h2>Dodaj wykluczenie</h2>
  <form class="wiersz" method="post"
        action="{{ url_for('dodaj', czujnik=czujnik, od=od_formularz, do=do_formularz) }}">
    <input type="hidden" name="czujnik" value="{{ czujnik }}">
    <label>Czujnik <input type="text" value="{{ nazwa_czujnika }}" disabled></label>
    <label>Pomiar
      <select name="pomiar" id="pomiar">
        <option value="oba">temperatura i wilgotność</option>
        <option value="va_temperature">tylko temperatura</option>
        <option value="va_humidity">tylko wilgotność</option>
      </select>
    </label>
    <label>Od <input type="datetime-local" step="1" name="od" id="w_od" required></label>
    <label>Do (włącznie) <input type="datetime-local" step="1" name="do" id="w_do" required></label>
    <label>Powód <input type="text" name="powod" size="24" placeholder="np. słaba bateria"></label>
    <button class="glowny" type="submit">Wyklucz</button>
  </form>
  <div id="podglad" class="slaby"></div>
  <p class="slaby">
    Koniec wpisany bez sekund obejmuje całą minutę. Zapis przelicza średnie i okresy grzania
    w zakresie wykluczenia (± 2 dni), więc trwa chwilę.
  </p>
</section>

<section class="karta">
  <h2>Wykluczenia</h2>
  {% if wykluczenia %}
  <table>
    <tr>
      <th>Czujnik</th><th>Pomiar</th><th>Od</th><th>Do</th><th>Powód</th><th>Dodano</th><th></th>
    </tr>
    {% for w in wykluczenia %}
    <tr class="{{ '' if w.aktywny else 'wylaczony' }}">
      <td><a href="{{ url_for('home', czujnik=w.device_id,
                     od=(w.od_dt - pokaz_margines).strftime('%Y-%m-%dT%H:%M'),
                     do=(w.do_dt + pokaz_margines).strftime('%Y-%m-%dT%H:%M')) }}">{{ w.nazwa }}</a></td>
      <td>{{ w.pomiar }}</td>
      <td>{{ w.od_dt | czas }}</td>
      <td>{{ w.do_dt | czas }}</td>
      <td>{{ w.powod or '' }}</td>
      <td>{{ w.created_at | czas }}</td>
      <td>
        <form method="post" action="{{ url_for('przelacz', id=w.id, czujnik=czujnik, od=od_formularz, do=do_formularz) }}" style="display:inline">
          <button class="maly" type="submit">{{ 'Wyłącz' if w.aktywny else 'Włącz' }}</button>
        </form>
        <form method="post" action="{{ url_for('usun', id=w.id, czujnik=czujnik, od=od_formularz, do=do_formularz) }}" style="display:inline"
              onsubmit="return confirm('Usunąć to wykluczenie?')">
          <button class="maly" type="submit">Usuń</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  <p class="slaby">Wyłączone wykluczenie nie działa, ale zostaje na liście. Nazwa czujnika otwiera podgląd okresu.</p>
  {% else %}
  <p class="slaby">Brak wykluczeń.</p>
  {% endif %}
</section>
</main>

<script>
const dane = {{ dane | tojson }};
const okresy = {{ okresy | tojson }};
const ciemny = matchMedia('(prefers-color-scheme: dark)').matches;
const SZARY = ciemny ? '#94a3b8' : '#64748b';

function naFormularz(ms) {
  // 'sv-SE' daje format 2026-11-20 17:58:41 — po zamianie spacji pasuje do datetime-local.
  return new Date(ms).toLocaleString('sv-SE', { timeZone: 'Europe/Warsaw' }).replace(' ', 'T');
}

function zFormularza(tekst, koniec) {
  if (!tekst) return null;
  // Czas polski z formularza -> ms (przesunięcie strefy liczone dla tej chwili).
  const [d, t] = tekst.split('T');
  const [r, m, dz] = d.split('-').map(Number);
  const [g, mi, s = 0] = t.split(':').map(Number);
  const utc = Date.UTC(r, m - 1, dz, g, mi, s);
  const naiwny = new Date(utc).toLocaleString('sv-SE', { timeZone: 'Europe/Warsaw' }).replace(' ', 'T');
  const przesuniecie = Date.parse(naiwny + 'Z') - utc;
  let ms = utc - przesuniecie;
  if (koniec && t.split(':').length === 2) ms += 59999;
  return ms;
}

const pasy = {
  silent: true,
  itemStyle: { color: ciemny ? 'rgba(148,163,184,0.18)' : 'rgba(100,116,139,0.14)' },
  data: okresy.map(o => [{ xAxis: o.od }, { xAxis: o.do }])
};

const wykres = echarts.init(document.getElementById('wykres'), ciemny ? 'dark' : null);
wykres.setOption({
  backgroundColor: 'transparent',
  animation: false,
  grid: { left: 50, right: 50, top: 40, bottom: 80 },
  legend: { bottom: 0 },
  tooltip: {
    trigger: 'item',
    formatter: p => naFormularz(p.value[0]).replace('T', ' ') + '<br>'
      + p.marker + p.seriesName + ': ' + p.value[1]
  },
  toolbox: { feature: { brush: { type: ['lineX', 'clear'] } }, right: 10 },
  brush: { xAxisIndex: 0, brushLink: 'none', brushMode: 'single',
           brushStyle: { color: 'rgba(37,99,235,0.15)', borderColor: 'rgba(37,99,235,0.6)' } },
  xAxis: { type: 'time', axisLabel: { hideOverlap: true } },
  yAxis: [
    { type: 'value', name: '°C', scale: true },
    { type: 'value', name: '%', scale: true, splitLine: { show: false } }
  ],
  dataZoom: [
    { type: 'inside', filterMode: 'none', moveOnMouseMove: false },
    { type: 'slider', filterMode: 'none', bottom: 30, height: 18 }
  ],
  series: [
    { name: 'Temperatura', type: 'line', symbolSize: 4, data: dane.va_temperature.zwykle,
      markArea: pasy },
    { name: 'Wilgotność', type: 'line', yAxisIndex: 1, symbolSize: 3,
      lineStyle: { width: 1, opacity: 0.6 }, itemStyle: { opacity: 0.6 },
      data: dane.va_humidity.zwykle },
    { name: 'Wykluczone (temp.)', type: 'scatter', symbolSize: 7, itemStyle: { color: SZARY },
      data: dane.va_temperature.wykluczone },
    { name: 'Wykluczone (wilg.)', type: 'scatter', yAxisIndex: 1, symbolSize: 5,
      symbol: 'diamond', itemStyle: { color: SZARY }, data: dane.va_humidity.wykluczone }
  ]
});
// Przeciąganie myszą od razu zaznacza okres.
wykres.dispatchAction({
  type: 'takeGlobalCursor', key: 'brush',
  brushOption: { brushType: 'lineX', brushMode: 'single' }
});
wykres.on('brushEnd', p => {
  const zakres = p.areas.length && p.areas[0].coordRange;
  if (!zakres) return;
  document.getElementById('w_od').value = naFormularz(Math.floor(zakres[0] / 1000) * 1000);
  document.getElementById('w_do').value = naFormularz(Math.ceil(zakres[1] / 1000) * 1000);
  podglad();
});
window.addEventListener('resize', () => wykres.resize());

function podglad() {
  const od = zFormularza(document.getElementById('w_od').value, false);
  const doMs = zFormularza(document.getElementById('w_do').value, true);
  const pole = document.getElementById('podglad');
  if (od == null || doMs == null) { pole.textContent = ''; return; }
  if (doMs < od) { pole.textContent = 'Koniec jest wcześniej niż początek.'; return; }

  const pomiar = document.getElementById('pomiar').value;
  const kody = pomiar === 'oba' ? ['va_temperature', 'va_humidity'] : [pomiar];
  const opisy = { va_temperature: ['temperatura', ' °C'], va_humidity: ['wilgotność', ' %'] };
  const czesci = kody.map(kod => {
    const w = dane[kod].zwykle.filter(([t]) => t >= od && t <= doMs).map(([, v]) => v);
    if (!w.length) return opisy[kod][0] + ': 0';
    return opisy[kod][0] + ': ' + w.length + ' (' + Math.min(...w) + '–' + Math.max(...w) + opisy[kod][1] + ')';
  });
  pole.textContent = 'W pokazanym zakresie zostanie wykluczonych odczytów — ' + czesci.join(', ');
}
for (const id of ['w_od', 'w_do', 'pomiar']) {
  document.getElementById(id).addEventListener('input', podglad);
}
</script>
</body>
</html>
"""


def wroc():
    return redirect(url_for(
        "home",
        czujnik=request.args.get("czujnik"),
        od=request.args.get("od"),
        do=request.args.get("do"),
    ))


@app.route("/")
def home():
    teraz = datetime.now(STREFA).replace(second=0, microsecond=0)
    blad = None

    czujnik = request.args.get("czujnik")
    if czujnik not in NAZWY:
        czujnik = DEVICES[0][1]

    try:
        od = z_formularza(request.args["od"]) if request.args.get("od") else teraz - timedelta(days=DOMYSLNY_ZAKRES_DNI)
        do = z_formularza(request.args["do"]) if request.args.get("do") else teraz
    except ValueError:
        od, do = teraz - timedelta(days=DOMYSLNY_ZAKRES_DNI), teraz
        blad = "Niepoprawna data — pokazuję ostatnie 7 dni."

    wykluczenia = []
    dane = {code: {"zwykle": [], "wykluczone": []} for code in ("va_temperature", "va_humidity")}
    try:
        wykluczenia = pobierz_wykluczenia()
        if do <= od:
            blad = "Koniec zakresu musi być późniejszy niż początek."
        else:
            dane = dane_wykresu(
                surowe_odczyty(czujnik, od, do),
                [w for w in wykluczenia if w["device_id"] == czujnik],
            )
    except Exception as exc:
        app.logger.exception("Błąd odczytu z Supabase")
        blad = f"Błąd odczytu z Supabase ({KOMUNIKAT_SQL}): {exc}"

    okresy = [
        {
            "od": int(w["od_dt"].timestamp() * 1000),
            "do": int(w["do_dt"].timestamp() * 1000),
        }
        for w in wykluczenia
        if w["device_id"] == czujnik and w["aktywny"]
    ]

    return render_template_string(
        HTML,
        echarts_js=ECHARTS_JS,
        urzadzenia=DEVICES,
        czujnik=czujnik,
        nazwa_czujnika=NAZWY[czujnik],
        od_formularz=do_formularza(od),
        do_formularz=do_formularza(do),
        dane=dane,
        okresy=okresy,
        wykluczenia=wykluczenia,
        pokaz_margines=timedelta(hours=6),
        blad=blad,
    )


@app.post("/wykluczenia")
def dodaj():
    czujnik = request.form.get("czujnik")
    pomiar = request.form.get("pomiar", "oba")
    if czujnik not in NAZWY or pomiar not in POMIARY:
        flash("Nieznany czujnik albo pomiar.", "blad")
        return wroc()

    try:
        od = z_formularza(request.form["od"])
        do = koniec_z_formularza(request.form["do"])
    except (KeyError, ValueError):
        flash("Niepoprawna data.", "blad")
        return wroc()

    if do < od:
        flash("Koniec okresu nie może być wcześniejszy niż początek.", "blad")
        return wroc()

    code = POMIARY[pomiar]
    try:
        liczba, odczyty = odczyty_w_okresie(czujnik, code, od, do)
        supabase.table("sensor_exclusions").insert({
            "device_id": czujnik,
            "code": code,
            "od": od.isoformat(),
            "do_": do.isoformat(),
            "powod": request.form.get("powod", "").strip() or None,
        }).execute()
    except Exception as exc:
        app.logger.exception("Błąd zapisu wykluczenia")
        flash(f"Nie udało się zapisać wykluczenia ({KOMUNIKAT_SQL}): {exc}", "blad")
        return wroc()

    temperatury = [float(r["value"]) for r in odczyty if r["code"] == "va_temperature"]
    opis = odczytow(liczba)
    if temperatury and len(odczyty) == liczba:
        opis += f", temperatura {min(temperatury):g}–{max(temperatury):g} °C".replace(".", ",")
    flash(
        f"Wykluczono: {NAZWY[czujnik]}, {NAZWY_POMIAROW[code]}, "
        f"{filtr_czas(od)} – {filtr_czas(do)} ({opis}). Średnie i okresy grzania przeliczone.",
        "ok",
    )
    return wroc()


@app.post("/wykluczenia/<int:id>/przelacz")
def przelacz(id):
    try:
        wiersz = supabase.table("sensor_exclusions").select("aktywny").eq("id", id).execute().data
        if wiersz:
            supabase.table("sensor_exclusions").update(
                {"aktywny": not wiersz[0]["aktywny"]}
            ).eq("id", id).execute()
    except Exception as exc:
        app.logger.exception("Błąd zmiany wykluczenia")
        flash(f"Nie udało się zmienić wykluczenia: {exc}", "blad")
    return wroc()


@app.post("/wykluczenia/<int:id>/usun")
def usun(id):
    try:
        supabase.table("sensor_exclusions").delete().eq("id", id).execute()
    except Exception as exc:
        app.logger.exception("Błąd usuwania wykluczenia")
        flash(f"Nie udało się usunąć wykluczenia: {exc}", "blad")
    return wroc()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5004,
        debug=False,
    )
