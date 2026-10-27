"""
Wykresy temperatury z archiwum Supabase w Apache ECharts.

Te same dane co app_archive.py (z niego pochodzą funkcje odczytu),
ale tylko cztery wykresy temperatury: czujniki w mieszkaniu,
Jasionka (METAR), średnie i różnica średnich z sensor_aggregates.

Uruchomienie:
    ./.venv/bin/python3 prezentacja/app_echarts.py   # http://127.0.0.1:5002
"""

from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

import app_archive


app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archiwum — ECharts</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>

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

.chart-card {
  background: white;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 3px 12px #0000000a;
  margin-bottom: 18px;
}

.chart-wrap {
  height: 380px;
}

.small {
  font-size: 13px;
  color: #64748b;
  margin-top: 10px;
  line-height: 1.6;
}

@media (max-width: 600px) {
  body { padding: 12px; }
  .chart-card { padding: 15px; }
  .chart-wrap { height: 300px; }
}
</style>
</head>

<body>
<h1>🌦️ Moja stacja pogodowa — ECharts</h1>

<div class="sub">
  Archiwum Supabase · wykresy temperatury w Apache ECharts
  <br>
  Wczytane rekordy: <span id="recordCount">—</span>
</div>

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

<div class="chart-card">
  <h3>🌡️ Temperatura</h3>
  <div id="tempChart" class="chart-wrap"></div>
  <div id="grzanieInfo" class="small"></div>
</div>

<div class="chart-card">
  <h3>✈️ Temperatura — Jasionka (METAR)</h3>
  <div id="jasionkaChart" class="chart-wrap"></div>
</div>

<div class="chart-card">
  <h3>📊 Średnie temperatury — mieszkanie i Jasionka</h3>
  <div id="srednieChart" class="chart-wrap"></div>
  <div id="srednieInfo" class="small"></div>
</div>

<div class="chart-card">
  <h3>↕️ Różnica temperatur — mieszkanie − Jasionka</h3>
  <div id="roznicaChart" class="chart-wrap"></div>
  <div id="roznicaInfo" class="small"></div>
</div>

<script>
const GODZINA_MS = 3600000;
const KROK_PRZESUNIECIA_MS = 12 * GODZINA_MS;
const KOLOR_GRZANIA = 'rgba(234, 88, 12, ';

const chartInstances = {};
let currentDurationHours = 48;
let requestId = 0;

function formatDate(value) {
  return new Date(Number(value)).toLocaleString('pl-PL', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function setActiveButton(btn) {
  document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

function buttonByLabel(label) {
  return [...document.querySelectorAll('.controls .btn')]
    .find(b => b.innerText === label);
}

// Okresy grzania jako pasy (markArea) i kreski włączenia (markLine).
// Drobne włączenia — bledszy pas i przerywana kreska.
function znacznikiGrzania(okresy) {
  return {
    markArea: {
      silent: true,
      data: okresy.map(o => [
        {
          xAxis: o.od,
          itemStyle: { color: KOLOR_GRZANIA + (o.rodzaj === 'drobne' ? '0.06)' : '0.12)') }
        },
        { xAxis: o.do }
      ])
    },
    markLine: {
      silent: true,
      symbol: 'none',
      label: {
        position: 'insideEndTop',
        color: KOLOR_GRZANIA + '1)',
        fontSize: 11,
        formatter: p => p.data.rodzaj === 'drobne' ? 'drobne' : 'grzejnik'
      },
      data: okresy.map(o => ({
        xAxis: o.od,
        rodzaj: o.rodzaj,
        lineStyle: {
          color: KOLOR_GRZANIA + (o.rodzaj === 'drobne' ? '0.6)' : '0.9)'),
          width: o.rodzaj === 'drobne' ? 1.5 : 2,
          type: o.rodzaj === 'drobne' ? 'dashed' : 'solid'
        }
      }))
    }
  };
}

// Datasety w kształcie Chart.js ({label, data: [{x, y}]}) z app_archive
// zamieniane na serie ECharts; null w y to przerwa w linii.
function naSerie(datasety, schodkowe) {
  return datasety.map(ds => ({
    name: ds.label,
    type: 'line',
    showSymbol: !schodkowe,
    symbolSize: 3,
    connectNulls: false,
    lineStyle: { width: 2 },
    data: ds.data.map(p => ({ value: [p.x, p.y], pokrycie: p.pokrycie }))
  }));
}

function makeChart(id) {
  const chart = echarts.init(document.getElementById(id));
  chart.group = 'temperatury';

  chart.setOption({
    animation: false,
    grid: { left: 50, right: 20, top: 20, bottom: 95 },
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        if (!params.length) return '';
        const wiersze = params
          .filter(p => p.value[1] != null)
          .map(p => p.marker + p.seriesName + ': ' + p.value[1].toFixed(1) + ' °C'
            + (p.data.pokrycie != null ? ' · pokrycie ' + p.data.pokrycie + '%' : ''));
        return [formatDate(params[0].value[0]), ...wiersze].join('<br>');
      }
    },
    legend: { bottom: 0, type: 'scroll' },
    xAxis: {
      type: 'time',
      axisLabel: { hideOverlap: true }
    },
    yAxis: {
      type: 'value',
      name: '°C',
      scale: true
    },
    dataZoom: [
      { type: 'inside', filterMode: 'none' },
      { type: 'slider', filterMode: 'none', bottom: 30, height: 20 }
    ],
    series: []
  });

  chartInstances[id] = chart;
}

// Zwraca [startMs, endMs]; startMs === null oznacza całą historię.
function currentRange() {
  const picker = document.getElementById('startTimePicker');
  const now = Date.now();

  if (currentDurationHours === null) return [null, now];

  if (picker.value) {
    const startMs = new Date(picker.value).getTime();
    return [startMs, startMs + (currentDurationHours * GODZINA_MS)];
  }

  return [now - (currentDurationHours * GODZINA_MS), now];
}

function ustawSerie(id, serie, startMs, endMs) {
  chartInstances[id].setOption({
    xAxis: { min: startMs, max: endMs },
    dataZoom: [{ start: 0, end: 100 }, { start: 0, end: 100 }],
    series: serie
  }, { replaceMerge: ['series'] });
}

function opisGrzania(okresy, blad) {
  if (blad) return blad;
  if (!okresy.length) return 'Brak wykrytych okresów grzania w zakresie.';
  const drobnych = okresy.filter(o => o.rodzaj === 'drobne').length;
  return 'Pomarańczowe pasy: okresy grzania grzejnika w kuchni '
    + '(od włączenia do szczytu temperatury; bledsze — drobne włączenia) · w zakresie: '
    + (okresy.length - drobnych) + ' wyraźnych, ' + drobnych + ' drobnych';
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

    const start = startMs ?? dane.poczatek_ms;
    const grzanie = znacznikiGrzania(dane.okresy_grzania);

    const temp = naSerie(dane.temp, false);
    if (temp.length) Object.assign(temp[0], grzanie);
    ustawSerie('tempChart', temp, start, endMs);

    ustawSerie('jasionkaChart', naSerie(dane.jasionka, false), start, endMs);

    const srednie = naSerie(dane.srednie, true);
    if (srednie.length) Object.assign(srednie[0], grzanie);
    ustawSerie('srednieChart', srednie, start, endMs);

    const roznice = naSerie(dane.roznice, true);
    if (roznice.length) Object.assign(roznice[0], grzanie);
    ustawSerie('roznicaChart', roznice, start, endMs);

    document.getElementById('grzanieInfo').textContent =
      opisGrzania(dane.okresy_grzania, dane.blad_grzania);
    document.getElementById('srednieInfo').textContent = dane.blad_srednich || '';
    document.getElementById('roznicaInfo').textContent = dane.blad_roznic || '';
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
    setRange(48, buttonByLabel('48h'));
    return;
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

function showAll(btn) {
  document.getElementById('startTimePicker').value = '';
  setRange(null, btn);
}

function resetCharts() {
  document.getElementById('startTimePicker').value = '';
  setRange(48, buttonByLabel('48h'));
}

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

makeChart('tempChart');
makeChart('jasionkaChart');
makeChart('srednieChart');
makeChart('roznicaChart');
// Przybliżanie i przesuwanie myszą działa na wszystkich wykresach naraz.
echarts.connect('temperatury');
window.addEventListener('resize', () => {
  for (const id in chartInstances) chartInstances[id].resize();
});

loadRange();
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/historia")
def api_historia():
    """Dane czterech wykresów temperatury dla zakresu od/do (ms); bez od — cała historia."""
    od_ms = request.args.get("od", type=int)
    do_ms = request.args.get("do", type=int)
    teraz_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    od_iso = app_archive.ms_na_iso(od_ms) if od_ms is not None else None
    do_iso = app_archive.ms_na_iso(do_ms) if do_ms is not None else None

    try:
        rekordy = app_archive.pobierz_wszystkie_pomiary(od_iso, do_iso)
        _, historia_temp, _, historia_jasionka = (
            app_archive.przygotuj_widok(rekordy, teraz_ms)
        )

    except Exception as exc:
        app.logger.exception("Błąd odczytu archiwum Supabase")
        return jsonify({"blad": str(exc)}), 500

    historia_srednie, blad_srednich = app_archive.srednie_lub_blad(
        app_archive.SREDNIE, od_iso, do_iso
    )
    historia_roznice, blad_roznic = app_archive.srednie_lub_blad(
        app_archive.ROZNICE, od_iso, do_iso
    )
    okresy_grzania, blad_grzania = app_archive.okresy_grzania_lub_blad(
        od_iso, do_iso
    )

    if od_ms is None:
        od_ms = min(
            (int(r["event_time_ms"]) for r in rekordy),
            default=teraz_ms,
        )

    return jsonify({
        "temp": historia_temp,
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
        port=5002,
        debug=False,
    )
