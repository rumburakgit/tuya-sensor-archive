
import os
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template_string
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

app = Flask(__name__)

HISTORIA_GODZINY = 48
PROG_BRAKU_DANYCH_MIN = 10
PAGE_SIZE = 1000

DEVICES = [
    ("Czujnik główny", "bfff2b43e5cd3b4a8enrgm"),
    ("Czujnik 1", "bf4ac560e810e7c0ff1e1h"),
    ("Czujnik 2", "bff6e8a18f0a5e6663oeoo"),
]

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
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
  Archiwum Supabase · ostatnie 48 godzin ·
  automatyczne odświeżanie co 60 sekund
  <br>
  Ostatnie odświeżenie: {{ czas_odswiezenia }}
  <br>
  Wczytane rekordy: {{ liczba_rekordow }}
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

<h2>Wykresy — ostatnie 48 godzin</h2>

<div class="controls">
  <span style="align-self: center; font-weight: 600; font-size: 14px; margin-right: 4px;">Start:</span>
  <div class="custom-time">
    <input type="datetime-local" id="startTimePicker" onchange="updateCustomRange()">
  </div>

  <span style="align-self: center; font-weight: 600; font-size: 14px; margin-left: 8px; margin-right: 4px;">Czas trwania:</span>
  <button class="btn" onclick="setRange(1, this)">1h</button>
  <button class="btn" onclick="setRange(2, this)">2h</button>
  <button class="btn" onclick="setRange(3, this)">3h</button>
  <button class="btn" onclick="setRange(6, this)">6h</button>
  <button class="btn" onclick="setRange(12, this)">12h</button>
  <button class="btn" onclick="setRange(24, this)">24h</button>
  <button class="btn active" onclick="setRange(48, this)">48h</button>
  <button class="btn" onclick="resetCharts()" style="margin-left: auto;">Reset / Teraz</button>
</div>

<div class="charts">
  <div class="chart-card">
    <h3>🌡️ Temperatura</h3>
    <div class="chart-wrap">
      <canvas id="tempChart"></canvas>
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

function updateCharts(startMs, endMs) {
  for (const id in chartInstances) {
    const chart = chartInstances[id];
    chart.options.scales.x.min = startMs;
    chart.options.scales.x.max = endMs;
    chart.update('none');
  }
}

function updateCustomRange() {
  const picker = document.getElementById('startTimePicker');
  if (!picker.value) return;
  
  const startMs = new Date(picker.value).getTime();
  const endMs = startMs + (currentDurationHours * 3600000);
  updateCharts(startMs, endMs);
}

function setRange(hours, btn) {
  currentDurationHours = hours;
  
  document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const picker = document.getElementById('startTimePicker');
  let startMs;
  let endMs;

  if (picker.value) {
    startMs = new Date(picker.value).getTime();
    endMs = startMs + (hours * 3600000);
  } else {
    endMs = {{ koniec_ms }};
    startMs = endMs - (hours * 3600000);
  }
  
  updateCharts(startMs, endMs);
}

function resetCharts() {
  document.getElementById('startTimePicker').value = '';
  currentDurationHours = 48;
  
  for (const id in chartInstances) {
    chartInstances[id].resetZoom();
  }
  
  document.querySelectorAll('.controls .btn').forEach(b => {
    if (b.innerText === '48h') b.classList.add('active');
    else b.classList.remove('active');
  });
}

function makeChart(id, datasets, unit) {
  chartInstances[id] = new Chart(document.getElementById(id), {
    type: 'line',
    data: { datasets: datasets },
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
          ticks: {
            maxTicksLimit: 8,
            callback: value => formatDate(value)
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
              : ''
          }
        },
        legend: {
          position: 'bottom'
        }
      }
    }
  });
}

makeChart('tempChart', temps, 'Temperatura (°C)');
makeChart('humChart', hums, 'Wilgotność (%)');
</script>

{% endif %}
</body>
</html>
"""


def pobierz_wszystkie_pomiary(poczatek_iso):
    """Czyta historię porcjami, również gdy jest ponad 1000 rekordów."""
    wszystkie = []
    offset = 0

    while True:
        wynik = (
            supabase.table("sensor_readings")
            .select(
                "device_id,device_name,event_time_ms,"
                "measured_at,code,value,unit"
            )
            .gte("measured_at", poczatek_iso)
            .order("measured_at")
            .order("device_id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rekordy = wynik.data or []
        wszystkie.extend(rekordy)

        if len(rekordy) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return wszystkie


def przygotuj_widok(rekordy, teraz):
    ostatnie = {}
    temp_datasets = []
    hum_datasets = []

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

        temp_datasets.append({
            "label": nazwa,
            "data": punkty(temp, "va_temperature"),
            "pointRadius": 1.5,
            "borderWidth": 2,
            "spanGaps": False,
        })

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

        czujniki, historia_temp, historia_hum = przygotuj_widok(
            rekordy,
            koniec_ms,
        )

        return render_template_string(
            HTML,
            czujniki=czujniki,
            historia_temp=json.dumps(historia_temp),
            historia_hum=json.dumps(historia_hum),
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
            liczba_rekordow=0,
            czas_odswiezenia=teraz.astimezone().strftime(
                "%d.%m.%Y %H:%M:%S"
            ),
            poczatek_ms=poczatek_ms,
            koniec_ms=koniec_ms,
            blad=str(exc),
        ), 500


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5001,
        debug=False,
    )
