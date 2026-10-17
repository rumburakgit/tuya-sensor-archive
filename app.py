
from flask import Flask, render_template_string
from czujniki import pobierz_czujniki, pobierz_historie, DEVICES
from datetime import datetime
import json

app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Moja stacja pogodowa</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
* { box-sizing: border-box; }
body {
  font-family: system-ui,sans-serif;
  max-width: 1100px;
  margin: auto;
  padding: 22px;
  background: #f1f5f9;
  color: #172033;
}
h1 { margin-bottom: 4px; }
.sub { color: #64748b; margin-bottom: 24px; }
.cards,.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit,minmax(230px,1fr));
  gap: 16px;
}
.card,.chart-card {
  background: white;
  border-radius: 16px;
  padding: 20px;
  box-shadow: 0 3px 12px #0000000a;
  margin-bottom: 18px;
}
.value { font-size: 36px; font-weight: 700; }
.humidity { font-size: 21px; margin-top: 4px; }
.small { font-size: 13px; color: #64748b; margin-top: 10px; }
.chart-wrap { height: 280px; }
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
.offline { background: #fee2e2; color: #991b1b; }
.error { background: #e2e8f0; color: #334155; }
</style>
</head>
<body>
<h1>🌦️ Moja stacja pogodowa</h1>
<div class="sub">
Automatyczne odświeżanie co 60 sekund · historia z ostatniej godziny
</div>

<div class="cards">
{% for c in czujniki %}
<div class="card">
  <h2>{{ c.nazwa }}</h2>

  {% if c.status == 'Działa' %}
    <span class="status ok">● Działa</span>
  {% elif c.status == 'Offline' %}
    <span class="status offline">● Offline</span>
  {% elif c.status == 'Brak nowych danych' %}
    <span class="status stale">● Brak nowych danych</span>
  {% else %}
    <span class="status error">● {{ c.status }}</span>
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
      Ostatni pomiar: {{ c.czas | timestamp }}
      {% if c.wiek_min is not none %}
        <br>Wiek danych: {{ c.wiek_min }} min
      {% endif %}
    {% else %}
      Brak zapisanego pomiaru
    {% endif %}
  </div>

  {% if c.blad %}
    <div class="small">{{ c.blad }}</div>
  {% endif %}
</div>
{% endfor %}
</div>

<h2>Wykresy — ostatnia godzina</h2>
<div class="charts">
  <div class="chart-card">
    <h3>🌡️ Temperatura</h3>
    <div class="chart-wrap"><canvas id="tempChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>💧 Wilgotność</h3>
    <div class="chart-wrap"><canvas id="humChart"></canvas></div>
  </div>
</div>

<script>
const temps = {{ historia_temp | safe }};
const hums = {{ historia_hum | safe }};

function makeChart(id, datasets, unit) {
  new Chart(document.getElementById(id), {
    type: 'line',
    data: { datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      interaction: { intersect: false, mode: 'nearest' },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Godzina pomiaru' },
          ticks: {
            callback: value => {
              const d = new Date(Number(value));
              return d.toLocaleTimeString('pl-PL', {
                hour: '2-digit',
                minute: '2-digit'
              });
            },
            maxTicksLimit: 6
          }
        },
        y: { title: { display: true, text: unit } }
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: items => new Date(items[0].parsed.x)
              .toLocaleTimeString('pl-PL')
          }
        }
      }
    }
  });
}

makeChart('tempChart', temps, '°C');
makeChart('humChart', hums, '%');
</script>
</body>
</html>
"""

app.jinja_env.filters["timestamp"] = (
    lambda ms: datetime.fromtimestamp(ms / 1000)
    .astimezone()
    .strftime("%H:%M:%S")
)


@app.route("/")
def home():
    czujniki = pobierz_czujniki()
    historia_temp = []
    historia_hum = []

    for nazwa, device_id in DEVICES:
        punkty = pobierz_historie(device_id)

        historia_temp.append({
            "label": nazwa,
            "data": punkty["temperatura"],
            "pointRadius": 2,
            "tension": 0.2,
        })

        historia_hum.append({
            "label": nazwa,
            "data": punkty["wilgotnosc"],
            "pointRadius": 2,
            "tension": 0.2,
        })

    return render_template_string(
        HTML,
        czujniki=czujniki,
        historia_temp=json.dumps(historia_temp),
        historia_hum=json.dumps(historia_hum),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
