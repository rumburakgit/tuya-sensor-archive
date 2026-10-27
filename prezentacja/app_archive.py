
import os
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template_string, request
from dotenv import load_dotenv
from supabase import create_client


# --------------------------------------------------
# Konfiguracja
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
# Prezentacja tylko czyta, więc wystarcza klucz anon (odczyt przez polityki
# RLS z prezentacja/odczyt_anon.sql); bez niego — klucz service_role.
SUPABASE_KEY = (
    os.environ.get("SUPABASE_ANON_KEY")
    or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)

app = Flask(__name__)

HISTORIA_GODZINY = 48
PROG_BRAKU_DANYCH_MIN = 10
PAGE_SIZE = 1000

DEVICES = [
    ("Temp Kuchnias", "bf09f3b9302d16a74froza"),
    ("Temp Grzejnik", "bf333171b6295a9253wx0y"),
    ("Temp Zielony", "bff67b6528b7b61ab34bab"),
    ("Temp Jadalnia", "bf820e12f85f729ddaxkre"),
    ("Temp Przedpokój", "bf587bca663683547dxy4p"),
    ("Temp żółty pok", "bfbf68aa252e1f7893a3zz"),
    ("Temp Jasionka", "weather_eprz")
]

# Urządzenia pokazywane na osobnym wykresie temperatury
# (stacja METAR EPRZ, bez wilgotności).
OSOBNY_WYKRES = {"weather_eprz"}

# Serie temperatury z tabeli sensor_aggregates (warstwa analityczna):
# (seria, okres, metoda, etykieta na wykresie).
SREDNIE = [
    ("mieszkanie", "3h", "srednia_wazona_czasem", "Mieszkanie — średnia 3h"),
    ("weather_eprz", "1d", "srednia_wazona_czasem", "Jasionka — średnia dobowa"),
]

ROZNICE = [
    (
        "mieszkanie-weather_eprz", "3h", "roznica_srednich",
        "Mieszkanie (średnia 3h) − Jasionka (średnia dobowa)",
    ),
]

# Czujnik przy grzejniku, dla którego wykrywane są okresy grzania
# (tabela heating_periods, analityka/002_heating_periods.sql).
GRZEJNIK_DEVICE_ID = "bf333171b6295a9253wx0y"

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archiwum stacji pogodowej</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1"></script>

<style>
* { box-sizing: border-box; }

body {
  font-family: system-ui, sans-serif;
  max-width: 1200px;
  margin: auto;
  padding: 22px;
  background: #f1f5f9;
  color: #172033;
}

h1 { margin-bottom: 4px; }

.sub {
  color: #64748b;
  margin-bottom: 24px;
  line-height: 1.6;
}

.controls {
  margin-bottom: 20px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.btn {
  background: white;
  border: 1px solid #cbd5e1;
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s;
}

.btn:hover {
  background: #f8fafc;
  border-color: #94a3b8;
}

.btn.active {
  background: #2563eb;
  color: white;
  border-color: #2563eb;
}

.btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.custom-time {
  display: flex;
  align-items: center;
  gap: 8px;
  background: white;
  padding: 4px 12px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
}

input[type="datetime-local"] {
  border: none;
  font-family: inherit;
  font-size: 14px;
  color: #1e293b;
  outline: none;
}

.cards, .charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 16px;
}

.card, .chart-card {
  background: white;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 3px 12px #0000000a;
  margin-bottom: 18px;
  min-width: 0;
}

.value {
  font-size: 36px;
  font-weight: 700;
  margin-top: 14px;
}

.humidity {
  font-size: 21px;
  margin-top: 4px;
}

.small {
  font-size: 13px;
  color: #64748b;
  margin-top: 10px;
  line-height: 1.6;
}

.status {
  display: inline-block;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 650;
  margin-top: 5px;
}

.ok { background: #dcfce7; color: #166534; }
.stale { background: #fef3c7; color: #92400e; }
.error { background: #fee2e2; color: #991b1b; }

.chart-col {
  min-width: 0;
}

.chart-wrap {
  height: 340px;
  position: relative;
}

.error-box {
  background: #fee2e2;
  color: #991b1b;
  padding: 16px;
  border-radius: 12px;
  overflow-wrap: anywhere;
}

@media (max-width: 600px) {
  body { padding: 12px; }
  .card, .chart-card { padding: 15px; }
  .chart-wrap { height: 280px; }
}
</style>
</head>

<body>
<h1>🌦️ Moja stacja pogodowa</h1>

<div class="sub">
  Archiwum Supabase · dane wykresów wczytywane przy zmianie zakresu
  <br>
  Wczytanie strony: {{ czas_odswiezenia }}
  <br>
  Wczytane rekordy: <span id="recordCount">{{ liczba_rekordow }}</span>
</div>

{% if blad %}
  <div class="error-box">
    <strong>Nie udało się pobrać danych z Supabase.</strong>
    <div>{{ blad }}</div>
  </div>
{% else %}

<div class="cards">
{% for c in czujniki %}
<div class="card">
  <h2>{{ c.nazwa }}</h2>

  {% if c.status == 'Działa' %}
    <span class="status ok">● Działa</span>
  {% elif c.status == 'Brak nowych danych' %}
    <span class="status stale">● Brak nowych danych</span>
  {% else %}
    <span class="status error">● Brak pomiarów z 48 godzin</span>
  {% endif %}

  <div class="value">
    {% if c.temperatura is not none %}
      {{ c.temperatura }}°C
    {% else %}
      —°C
    {% endif %}
  </div>

  <div class="humidity">
    💧 Wilgotność:
    {% if c.wilgotnosc is not none %}
      {{ c.wilgotnosc }}%
    {% else %}
      —%
    {% endif %}
  </div>

  <div class="small">
    {% if c.czas %}
      Ostatni pomiar: {{ c.czas }}
      {% if c.wiek_min is not none %}
        <br>Wiek danych: {{ c.wiek_min }} min
      {% endif %}
    {% else %}
      Brak pomiarów w wybranym okresie
    {% endif %}
  </div>
</div>
{% endfor %}
</div>

<h2>Wykresy</h2>

<div class="controls">
  <span style="align-self: center; font-weight: 600; font-size: 14px; margin-right: 4px;">Start:</span>
  <div class="custom-time">
    <input type="datetime-local" id="startTimePicker" onchange="updateCustomRange()">
  </div>

  <span style="align-self: center; font-weight: 600; font-size: 14px; margin-left: 8px; margin-right: 4px;">Czas trwania:</span>
  <button class="btn" onclick="setRange(2, this)">2h</button>
  <button class="btn" onclick="setRange(6, this)">6h</button>
  <button class="btn" onclick="setRange(12, this)">12h</button>
  <button class="btn" onclick="setRange(24, this)">24h</button>
  <button class="btn active" onclick="setRange(48, this)">48h</button>
  <button class="btn" onclick="setRange(7 * 24, this)">7 dni</button>
  <button class="btn" onclick="showAll(this)">Cała historia</button>
  <button class="btn przesun" onclick="przesunZakres(-1)" title="12 godzin wcześniej">◀ 12h</button>
  <button class="btn przesun" onclick="przesunZakres(1)" title="12 godzin później">12h ▶</button>
  <button class="btn" onclick="resetCharts()" style="margin-left: auto;">Reset / Teraz</button>
</div>

<div id="loadStatus" class="small" style="margin: -8px 0 16px;"></div>

<div class="charts">
  <div class="chart-col">
    <div class="chart-card">
      <h3>🌡️ Temperatura</h3>
      <div class="chart-wrap">
        <canvas id="tempChart"></canvas>
      </div>
      <div id="grzanieInfo" class="small">{{ blad_grzania or '' }}</div>
    </div>

    <div class="chart-card">
      <h3>✈️ Temperatura — Jasionka (METAR)</h3>
      <div class="chart-wrap">
        <canvas id="jasionkaChart"></canvas>
      </div>
    </div>

    <div class="chart-card">
      <h3>📊 Średnie temperatury — mieszkanie i Jasionka</h3>
      <div class="chart-wrap">
        <canvas id="srednieChart"></canvas>
      </div>
      <div id="srednieInfo" class="small">{{ blad_srednich or '' }}</div>
    </div>

    <div class="chart-card">
      <h3>↕️ Różnica temperatur — mieszkanie − Jasionka</h3>
      <div class="chart-wrap">
        <canvas id="roznicaChart"></canvas>
      </div>
      <div id="roznicaInfo" class="small">{{ blad_roznic or '' }}</div>
    </div>
  </div>

  <div class="chart-card">
    <h3>💧 Wilgotność</h3>
    <div class="chart-wrap">
      <canvas id="humChart"></canvas>
    </div>
  </div>
</div>

<script>
const temps = {{ historia_temp | safe }};
const hums = {{ historia_hum | safe }};
const jasionka = {{ historia_jasionka | safe }};
const srednie = {{ historia_srednie | safe }};
const roznice = {{ historia_roznice | safe }};
let okresyGrzania = {{ okresy_grzania | safe }};
const chartInstances = {};
let currentDurationHours = 48;

function formatDate(value) {
  return new Date(Number(value)).toLocaleString('pl-PL', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

const GODZINA_MS = 3600000;
// Do tej długości widocznego zakresu oś X pokazuje godziny, powyżej — dni.
const PROG_GODZIN_MS = 24 * GODZINA_MS + 60000;
const MAKS_ZNACZNIKOW = 8;

// Znaczniki osi X w "okrągłych" chwilach czasu lokalnego:
// pełne godziny (co 1, 2, 3, 4, 6 lub 12 h) albo północe (co 1, 2, 3, 7... dni).
function znacznikiCzasu(min, max) {
  const wynik = [];
  const d = new Date(min);

  if (max - min <= PROG_GODZIN_MS) {
    const krok = [1, 2, 3, 4, 6, 12]
      .find(k => (max - min) / (k * GODZINA_MS) <= MAKS_ZNACZNIKOW) || 12;
    d.setMinutes(0, 0, 0);
    while (d.getTime() < min || d.getHours() % krok !== 0) {
      d.setHours(d.getHours() + 1);
    }
    while (d.getTime() <= max) {
      wynik.push({ value: d.getTime() });
      d.setHours(d.getHours() + krok);
    }
  } else {
    const dni = (max - min) / (24 * GODZINA_MS);
    const krok = [1, 2, 3, 7, 14, 30, 61, 91]
      .find(k => dni / k <= MAKS_ZNACZNIKOW) || 182;
    d.setHours(0, 0, 0, 0);
    if (d.getTime() < min) d.setDate(d.getDate() + 1);
    while (d.getTime() <= max) {
      wynik.push({ value: d.getTime() });
      d.setDate(d.getDate() + krok);
    }
  }

  return wynik;
}

// Etykieta znacznika: godzina (z datą pod spodem przy pierwszym
// znaczniku i o północy) albo sama data.
function etykietaCzasu(value, index, zakresMs) {
  const d = new Date(value);
  const data = d.toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit' });

  if (zakresMs > PROG_GODZIN_MS) return data;

  const godzina = d.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' });
  return (index === 0 || (d.getHours() === 0 && d.getMinutes() === 0))
    ? [godzina, data]
    : godzina;
}

let requestId = 0;

function setActiveButton(btn) {
  document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

function buttonByLabel(label) {
  return [...document.querySelectorAll('.controls .btn')]
    .find(b => b.innerText === label);
}

function updateCharts(startMs, endMs) {
  for (const id in chartInstances) {
    const chart = chartInstances[id];
    chart.options.scales.x.min = startMs;
    chart.options.scales.x.max = endMs;
    chart.update('none');
  }
}

// Zwraca [startMs, endMs]; startMs === null oznacza całą historię.
function currentRange() {
  const picker = document.getElementById('startTimePicker');
  const now = Date.now();

  if (currentDurationHours === null) return [null, now];

  if (picker.value) {
    const startMs = new Date(picker.value).getTime();
    return [startMs, startMs + (currentDurationHours * 3600000)];
  }

  return [now - (currentDurationHours * 3600000), now];
}

// Pobiera z serwera dane dla zakresu i podmienia je na wykresach.
async function loadRange() {
  const [startMs, endMs] = currentRange();
  const myId = ++requestId;
  const status = document.getElementById('loadStatus');
  status.textContent = 'Wczytywanie…';

  const params = new URLSearchParams({ do: endMs });
  if (startMs !== null) params.set('od', startMs);

  try {
    const response = await fetch('/api/historia?' + params);
    const dane = await response.json();
    if (!response.ok) throw new Error(dane.blad || response.status);

    // Ignoruj odpowiedź, jeśli w międzyczasie wybrano inny zakres.
    if (myId !== requestId) return;

    chartInstances.tempChart.data.datasets = dane.temp;
    chartInstances.humChart.data.datasets = dane.hum;
    chartInstances.jasionkaChart.data.datasets = dane.jasionka;
    chartInstances.srednieChart.data.datasets = dane.srednie;
    document.getElementById('srednieInfo').textContent = dane.blad_srednich || '';
    chartInstances.roznicaChart.data.datasets = dane.roznice;
    document.getElementById('roznicaInfo').textContent = dane.blad_roznic || '';
    okresyGrzania = dane.okresy_grzania;
    document.getElementById('grzanieInfo').textContent = opisGrzania(dane.blad_grzania);

    for (const id in chartInstances) {
      chartInstances[id].resetZoom('none');
    }
    updateCharts(startMs ?? dane.poczatek_ms, endMs);

    document.getElementById('recordCount').textContent = dane.liczba_rekordow;
    status.textContent = '';
  } catch (exc) {
    if (myId !== requestId) return;
    status.textContent = 'Błąd wczytywania danych: ' + exc.message;
  }
}

function updateCustomRange() {
  const picker = document.getElementById('startTimePicker');
  if (!picker.value) return;

  // Data "Start" nie ma sensu dla całej historii — wracamy do 48h.
  if (currentDurationHours === null) {
    currentDurationHours = 48;
    setActiveButton(buttonByLabel('48h'));
  }

  loadRange();
}

function setRange(hours, btn) {
  currentDurationHours = hours;
  setActiveButton(btn);
  // Przesuwanie nie ma sensu dla całej historii.
  document.querySelectorAll('.przesun').forEach(b => b.disabled = hours === null);
  loadRange();
}

const KROK_PRZESUNIECIA_MS = 12 * GODZINA_MS;

// Wartość dla <input type="datetime-local"> w czasie lokalnym.
function naDatetimeLocal(ms) {
  const d = new Date(ms);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

// Przesuwa zakres o 12 h (kierunek -1 = wcześniej, 1 = później),
// zachowując czas trwania. Zakres sięgający teraźniejszości wraca
// do trybu "do teraz" (puste pole Start).
function przesunZakres(kierunek) {
  if (currentDurationHours === null) return;

  const picker = document.getElementById('startTimePicker');
  const [startMs] = currentRange();
  const nowyStart = startMs + kierunek * KROK_PRZESUNIECIA_MS;

  // Tolerancja minuty: pole Start ma dokładność do minut.
  if (nowyStart + currentDurationHours * GODZINA_MS >= Date.now() - 60000) {
    picker.value = '';
  } else {
    picker.value = naDatetimeLocal(nowyStart);
  }

  loadRange();
}

function showAll(btn) {
  document.getElementById('startTimePicker').value = '';
  setRange(null, btn);
}

function resetCharts() {
  document.getElementById('startTimePicker').value = '';
  setRange(48, buttonByLabel('48h'));
}

const KOLOR_GRZANIA = 'rgba(234, 88, 12, ';

// Okresy grzania: kreska w chwili włączenia grzejnika, pas do szczytu
// temperatury i przerywana kreska w szczycie (brak, gdy okres trwa).
// Drobne włączenia: bledszy pas i przerywana kreska włączenia.
const wtyczkaGrzania = {
  id: 'okresyGrzania',
  beforeDatasetsDraw(chart, args, opcje) {
    if (!opcje.pokaz || !okresyGrzania.length) return;

    const { ctx, chartArea: obszar, scales: { x } } = chart;
    ctx.save();
    ctx.beginPath();
    ctx.rect(obszar.left, obszar.top, obszar.width, obszar.height);
    ctx.clip();

    for (const okres of okresyGrzania) {
      const x1 = x.getPixelForValue(okres.od);
      const x2 = x.getPixelForValue(okres.do);
      if (x2 < obszar.left || x1 > obszar.right) continue;

      const drobne = okres.rodzaj === 'drobne';

      ctx.fillStyle = KOLOR_GRZANIA + (drobne ? '0.06)' : '0.12)');
      ctx.fillRect(x1, obszar.top, Math.max(x2 - x1, 1), obszar.height);

      ctx.strokeStyle = KOLOR_GRZANIA + (drobne ? '0.6)' : '0.9)');
      ctx.lineWidth = drobne ? 1.5 : 2;
      ctx.setLineDash(drobne ? [6, 3] : []);
      ctx.beginPath();
      ctx.moveTo(x1, obszar.top);
      ctx.lineTo(x1, obszar.bottom);
      ctx.stroke();

      if (!okres.trwa) {
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(x2, obszar.top);
        ctx.lineTo(x2, obszar.bottom);
        ctx.stroke();
      }

      ctx.fillStyle = KOLOR_GRZANIA + '1)';
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillText(drobne ? 'drobne' : 'grzejnik', x1 + 3, obszar.top + 12);
    }

    ctx.restore();
  }
};

function opisGrzania(blad) {
  if (blad) return blad;
  if (!okresyGrzania.length) return 'Brak wykrytych okresów grzania w zakresie.';
  const drobnych = okresyGrzania.filter(o => o.rodzaj === 'drobne').length;
  return 'Pomarańczowe pasy: okresy grzania grzejnika w kuchni '
    + '(od włączenia do szczytu temperatury; bledsze — drobne włączenia) · w zakresie: '
    + (okresyGrzania.length - drobnych) + ' wyraźnych, ' + drobnych + ' drobnych';
}

function makeChart(id, datasets, unit, pokazGrzanie = false) {
  chartInstances[id] = new Chart(document.getElementById(id), {
    type: 'line',
    data: { datasets: datasets },
    plugins: [wtyczkaGrzania],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      normalized: false,
      interaction: {
        intersect: false,
        mode: 'index'
      },
      elements: {
        line: { tension: 0.15 },
        point: { radius: 1.5, hoverRadius: 5 }
      },
      scales: {
        x: {
          type: 'linear',
          title: {
            display: true,
            text: 'Data i godzina'
          },
          min: {{ poczatek_ms }},
          max: {{ koniec_ms }},
          afterBuildTicks: scale => {
            scale.ticks = znacznikiCzasu(scale.min, scale.max);
          },
          ticks: {
            maxRotation: 0,
            callback: function (value, index) {
              return etykietaCzasu(value, index, this.max - this.min);
            }
          }
        },
        y: {
          title: {
            display: true,
            text: unit
          }
        }
      },
      plugins: {
        okresyGrzania: {
          pokaz: pokazGrzanie
        },
        zoom: {
          pan: {
            enabled: true,
            mode: 'x',
          },
          zoom: {
            wheel: {
              enabled: true,
            },
            pinch: {
              enabled: true
            },
            mode: 'x',
          }
        },
        tooltip: {
          callbacks: {
            title: items => items.length
              ? formatDate(items[0].parsed.x)
              : '',
            // Punkty średnich niosą dodatkowo pokrycie okresu danymi.
            label: ctx => ctx.dataset.label + ': ' + ctx.formattedValue
              + (ctx.raw.pokrycie != null ? ' · pokrycie ' + ctx.raw.pokrycie + '%' : '')
          }
        },
        legend: {
          position: 'bottom'
        }
      }
    }
  });
}

makeChart('tempChart', temps, 'Temperatura (°C)', true);
makeChart('humChart', hums, 'Wilgotność (%)');
makeChart('jasionkaChart', jasionka, 'Temperatura (°C)');
makeChart('srednieChart', srednie, 'Temperatura (°C)', true);
makeChart('roznicaChart', roznice, 'Różnica temperatur (°C)', true);
document.getElementById('grzanieInfo').textContent =
  opisGrzania({{ blad_grzania | tojson }});
</script>

{% endif %}
</body>
</html>
"""


def ms_na_iso(timestamp_ms):
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).isoformat()


def pobierz_wszystkie_pomiary(poczatek_iso=None, koniec_iso=None):
    """
    Czyta historię porcjami, również gdy jest ponad 1000 rekordów.
    Bez poczatek_iso czyta od najstarszego rekordu.
    """
    wszystkie = []
    offset = 0

    while True:
        zapytanie = (
            supabase.table("sensor_readings")
            .select(
                "device_id,device_name,event_time_ms,"
                "measured_at,code,value,unit"
            )
        )

        if poczatek_iso:
            zapytanie = zapytanie.gte("measured_at", poczatek_iso)
        if koniec_iso:
            zapytanie = zapytanie.lte("measured_at", koniec_iso)

        # Sortowanie po pełnym kluczu daje stałą kolejność,
        # więc porcje nie gubią ani nie dublują rekordów.
        wynik = (
            zapytanie
            .order("measured_at")
            .order("device_id")
            .order("code")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rekordy = wynik.data or []
        wszystkie.extend(rekordy)

        if len(rekordy) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return wszystkie


def iso_na_ms(tekst):
    return int(datetime.fromisoformat(tekst).timestamp() * 1000)


def pobierz_srednie(serie, poczatek_iso=None, koniec_iso=None):
    """
    Czyta serie temperatury (lista jak SREDNIE) z sensor_aggregates
    dla okresów nachodzących na zakres. Bez poczatek_iso — od najstarszego.
    Zapytanie może zwrócić nadmiarowe kombinacje seria/okres/metoda;
    odfiltrowuje je przygotuj_srednie().
    """
    wszystkie = []
    offset = 0

    while True:
        zapytanie = (
            supabase.table("sensor_aggregates")
            .select("seria,okres,metoda,poczatek,koniec,wartosc,coverage_seconds")
            .eq("code", "va_temperature")
            .in_("seria", sorted({s[0] for s in serie}))
            .in_("okres", sorted({s[1] for s in serie}))
            .in_("metoda", sorted({s[2] for s in serie}))
        )

        if poczatek_iso:
            zapytanie = zapytanie.gt("koniec", poczatek_iso)
        if koniec_iso:
            zapytanie = zapytanie.lt("poczatek", koniec_iso)

        wynik = (
            zapytanie
            .order("poczatek")
            .order("seria")
            .order("okres")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rekordy = wynik.data or []
        wszystkie.extend(rekordy)

        if len(rekordy) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return wszystkie


def przygotuj_srednie(rekordy, serie):
    """
    Każda średnia to poziomy odcinek od początku do końca okresu.
    Między niesąsiadującymi okresami wstawiamy przerwę (y = null).
    """
    datasets = []

    for seria, okres, metoda, etykieta in serie:
        punkty = []
        poprzedni_koniec = None

        for r in rekordy:
            if (r["seria"], r["okres"], r["metoda"]) != (seria, okres, metoda):
                continue

            poczatek = iso_na_ms(r["poczatek"])
            koniec = iso_na_ms(r["koniec"])

            if poprzedni_koniec is not None and poczatek != poprzedni_koniec:
                punkty.append({"x": poprzedni_koniec, "y": None})

            wartosc = (
                float(r["wartosc"])
                if r["wartosc"] is not None else None
            )
            pokrycie = round(
                100 * r["coverage_seconds"] * 1000 / (koniec - poczatek)
            )

            punkty.append({"x": poczatek, "y": wartosc, "pokrycie": pokrycie})
            punkty.append({"x": koniec, "y": wartosc, "pokrycie": pokrycie})
            poprzedni_koniec = koniec

        datasets.append({
            "label": etykieta,
            "data": punkty,
            "pointRadius": 0,
            "borderWidth": 2,
            "tension": 0,
            "spanGaps": False,
        })

    return datasets


def srednie_lub_blad(serie, poczatek_iso=None, koniec_iso=None):
    """Brak średnich nie może zablokować reszty dashboardu."""
    try:
        return przygotuj_srednie(
            pobierz_srednie(serie, poczatek_iso, koniec_iso),
            serie,
        ), None
    except Exception as exc:
        app.logger.exception("Błąd odczytu sensor_aggregates")
        return [], (
            "Nie udało się wczytać średnich z sensor_aggregates "
            "(czy wdrożono analityka/001_sensor_aggregates.sql?): "
            f"{exc}"
        )


def pobierz_okresy_grzania(poczatek_iso=None, koniec_iso=None):
    """Okresy grzania nachodzące na zakres; bez poczatek_iso — od najstarszego."""
    zapytanie = (
        supabase.table("heating_periods")
        .select("poczatek,szczyt,trwa,rodzaj")
        .eq("device_id", GRZEJNIK_DEVICE_ID)
    )

    if poczatek_iso:
        zapytanie = zapytanie.gte("szczyt", poczatek_iso)
    if koniec_iso:
        zapytanie = zapytanie.lte("poczatek", koniec_iso)

    # Okres grzania trwa godzinę-dwie, więc 1000 wierszy wystarczy
    # na lata historii.
    wynik = (
        zapytanie
        .order("poczatek")
        .limit(PAGE_SIZE)
        .execute()
    )

    return [
        {
            "od": iso_na_ms(r["poczatek"]),
            "do": iso_na_ms(r["szczyt"]),
            "trwa": r["trwa"],
            "rodzaj": r["rodzaj"],
        }
        for r in wynik.data or []
    ]


def okresy_grzania_lub_blad(poczatek_iso=None, koniec_iso=None):
    """Brak okresów grzania nie może zablokować reszty dashboardu."""
    try:
        return pobierz_okresy_grzania(poczatek_iso, koniec_iso), None
    except Exception as exc:
        app.logger.exception("Błąd odczytu heating_periods")
        return [], (
            "Nie udało się wczytać okresów grzania z heating_periods "
            "(czy wdrożono analityka/002_heating_periods.sql?): "
            f"{exc}"
        )


def przygotuj_widok(rekordy, teraz):
    ostatnie = {}
    temp_datasets = []
    hum_datasets = []
    jasionka_datasets = []

    for nazwa, device_id in DEVICES:
        odczyty = [
            r for r in rekordy
            if r["device_id"] == device_id
        ]

        temp = [
            r for r in odczyty
            if r["code"] == "va_temperature"
        ]
        hum = [
            r for r in odczyty
            if r["code"] == "va_humidity"
        ]

        ostatnia_temp = max(
            temp,
            key=lambda r: int(r["event_time_ms"]),
            default=None,
        )
        ostatnia_hum = max(
            hum,
            key=lambda r: int(r["event_time_ms"]),
            default=None,
        )

        pomiary = [
            r for r in (ostatnia_temp, ostatnia_hum)
            if r is not None
        ]

        ostatni = max(
            pomiary,
            key=lambda r: int(r["event_time_ms"]),
            default=None,
        )

        wiek_min = None
        czas = None

        if ostatni:
            timestamp = int(ostatni["event_time_ms"])
            wiek_min = max(0, (teraz - timestamp) / 60000)
            czas = datetime.fromtimestamp(
                timestamp / 1000
            ).astimezone().strftime("%d.%m %H:%M:%S")

        if ostatni is None:
            status = "Brak pomiarów"
        elif wiek_min > PROG_BRAKU_DANYCH_MIN:
            status = "Brak nowych danych"
        else:
            status = "Działa"

        ostatnie[device_id] = {
            "nazwa": nazwa,
            "temperatura": (
                round(float(ostatnia_temp["value"]), 1)
                if ostatnia_temp else None
            ),
            "wilgotnosc": (
                round(float(ostatnia_hum["value"]), 1)
                if ostatnia_hum else None
            ),
            "czas": czas,
            "wiek_min": round(wiek_min, 1) if wiek_min is not None else None,
            "status": status,
        }

        def punkty(lista, kod):
            wynik = []

            for r in lista:
                timestamp = int(r["event_time_ms"])
                wartosc = float(r["value"])

                # Dane temperatury w bazie powinny być już
                # zapisane w stopniach Celsjusza.
                wynik.append({"x": timestamp, "y": wartosc})

            wynik.sort(key=lambda p: p["x"])
            return wynik

        temp_dataset = {
            "label": nazwa,
            "data": punkty(temp, "va_temperature"),
            "pointRadius": 1.5,
            "borderWidth": 2,
            "spanGaps": False,
        }

        if device_id in OSOBNY_WYKRES:
            jasionka_datasets.append(temp_dataset)
            continue

        temp_datasets.append(temp_dataset)

        hum_datasets.append({
            "label": nazwa,
            "data": punkty(hum, "va_humidity"),
            "pointRadius": 1.5,
            "borderWidth": 2,
            "spanGaps": False,
        })

    return (
        list(ostatnie.values()),
        temp_datasets,
        hum_datasets,
        jasionka_datasets,
    )


@app.route("/")
def home():
    teraz = datetime.now(timezone.utc)
    poczatek = teraz - timedelta(hours=HISTORIA_GODZINY)

    poczatek_ms = int(poczatek.timestamp() * 1000)
    koniec_ms = int(teraz.timestamp() * 1000)

    try:
        rekordy = pobierz_wszystkie_pomiary(
            poczatek.isoformat()
        )

        (
            czujniki,
            historia_temp,
            historia_hum,
            historia_jasionka,
        ) = przygotuj_widok(
            rekordy,
            koniec_ms,
        )

        historia_srednie, blad_srednich = srednie_lub_blad(
            SREDNIE, poczatek.isoformat()
        )
        historia_roznice, blad_roznic = srednie_lub_blad(
            ROZNICE, poczatek.isoformat()
        )
        okresy_grzania, blad_grzania = okresy_grzania_lub_blad(
            poczatek.isoformat()
        )

        return render_template_string(
            HTML,
            czujniki=czujniki,
            historia_temp=json.dumps(historia_temp),
            historia_hum=json.dumps(historia_hum),
            historia_jasionka=json.dumps(historia_jasionka),
            historia_srednie=json.dumps(historia_srednie),
            blad_srednich=blad_srednich,
            historia_roznice=json.dumps(historia_roznice),
            blad_roznic=blad_roznic,
            okresy_grzania=json.dumps(okresy_grzania),
            blad_grzania=blad_grzania,
            liczba_rekordow=len(rekordy),
            czas_odswiezenia=teraz.astimezone().strftime(
                "%d.%m.%Y %H:%M:%S"
            ),
            poczatek_ms=poczatek_ms,
            koniec_ms=koniec_ms,
            blad=None,
        )

    except Exception as exc:
        app.logger.exception("Błąd odczytu archiwum Supabase")

        return render_template_string(
            HTML,
            czujniki=[],
            historia_temp="[]",
            historia_hum="[]",
            historia_jasionka="[]",
            historia_srednie="[]",
            blad_srednich=None,
            historia_roznice="[]",
            blad_roznic=None,
            okresy_grzania="[]",
            blad_grzania=None,
            liczba_rekordow=0,
            czas_odswiezenia=teraz.astimezone().strftime(
                "%d.%m.%Y %H:%M:%S"
            ),
            poczatek_ms=poczatek_ms,
            koniec_ms=koniec_ms,
            blad=str(exc),
        ), 500


@app.route("/api/historia")
def api_historia():
    """Dane wykresów dla zakresu od/do (ms); bez od — cała historia."""
    od_ms = request.args.get("od", type=int)
    do_ms = request.args.get("do", type=int)
    teraz_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    od_iso = ms_na_iso(od_ms) if od_ms is not None else None
    do_iso = ms_na_iso(do_ms) if do_ms is not None else None

    try:
        rekordy = pobierz_wszystkie_pomiary(od_iso, do_iso)

        _, historia_temp, historia_hum, historia_jasionka = (
            przygotuj_widok(rekordy, teraz_ms)
        )

    except Exception as exc:
        app.logger.exception("Błąd odczytu archiwum Supabase")
        return jsonify({"blad": str(exc)}), 500

    historia_srednie, blad_srednich = srednie_lub_blad(SREDNIE, od_iso, do_iso)
    historia_roznice, blad_roznic = srednie_lub_blad(ROZNICE, od_iso, do_iso)
    okresy_grzania, blad_grzania = okresy_grzania_lub_blad(od_iso, do_iso)

    if od_ms is None:
        od_ms = min(
            (int(r["event_time_ms"]) for r in rekordy),
            default=teraz_ms,
        )

    return jsonify({
        "temp": historia_temp,
        "hum": historia_hum,
        "jasionka": historia_jasionka,
        "srednie": historia_srednie,
        "blad_srednich": blad_srednich,
        "roznice": historia_roznice,
        "blad_roznic": blad_roznic,
        "okresy_grzania": okresy_grzania,
        "blad_grzania": blad_grzania,
        "liczba_rekordow": len(rekordy),
        "poczatek_ms": od_ms,
    })


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5001,
        debug=False,
    )
