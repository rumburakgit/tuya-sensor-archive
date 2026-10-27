# Warstwa prezentacji

Prezentuje pomiary zapisane w Supabase. **Nie komunikuje się z Tuya** —
korzysta wyłącznie z tabel `sensor_readings` (i w przyszłości tabel analitycznych).

## Konfiguracja

Plik `prezentacja/.env` (osobny od `pobieranie/.env`) zawiera tylko:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` albo `SUPABASE_ANON_KEY`

Prezentacja niczego nie zapisuje, więc wystarcza klucz `anon` (gdy jest ustawiony, ma pierwszeństwo).
Wymaga jednorazowego uruchomienia `prezentacja/odczyt_anon.sql` w Supabase SQL Editor:
nadaje `anon` wyłącznie odczyt (`SELECT` + polityki RLS) tabel `sensor_readings`, `sensor_aggregates`
i `heating_periods`; `sensor_archive_state` (z tokenem Tuya) pozostaje niedostępna.

## `panel.py` — panel prezentacji

```bash
./.venv/bin/python3 prezentacja/panel.py
```

Okno z listą sposobów prezentacji. Dla każdego:

- **Uruchom / Zatrzymaj** — startuje lub zatrzymuje serwer,
- **Otwórz w przeglądarce** — aktywny, gdy serwer działa (przy pierwszym starcie strona otwiera się sama).

Logi serwerów są wyświetlane w konsoli panelu. Zamknięcie okna zatrzymuje wszystkie serwery.
Kolejne prezentacje (np. Grafana) dodaje się jako nowe pozycje listy `PREZENTACJE` w `panel.py`.

## `app_archive.py` — dashboard z Supabase (port 5001)

Podstawowa prezentacja: lokalny dashboard Flask + Chart.js.

- Pobiera dane z tabeli `sensor_readings` w Supabase (stronicowanie po 1000 wierszy).
- Wyświetla wykresy temperatury i wilgotności z ostatnich 48 godzin.
- Obsługuje wiele czujników (lista `DEVICES` w pliku — osobna od `pobieranie/czujniki.py`).
- Odświeża dane automatycznie co 60 sekund.
- Wykres **Średnie temperatury** pokazuje średnie z tabeli `sensor_aggregates`
  (mieszkanie — średnia 3h, Jasionka — średnia dobowa) jako poziome odcinki na długość okresu;
  w podpowiedzi widać pokrycie okresu danymi. Lista serii: `SREDNIE` w `app_archive.py`.
- Wykres **Różnica temperatur** pokazuje serie `roznica_srednich` (średnia 3h mieszkania − średnia dobowa Jasionki z tej samej doby);
  okresy bez jednej ze średnich są przerwą na wykresie. Lista serii: `ROZNICE` w `app_archive.py`.
  Gdy tabeli nie ma (SQL z `analityka/` niewdrożony), pod wykresem pojawia się komunikat,
  a reszta dashboardu działa normalnie.
- Okresy grzania z tabeli `heating_periods` są zaznaczone na wykresach temperatury, średnich i różnicy:
  ciągła pomarańczowa kreska w chwili włączenia grzejnika, pas do szczytu temperatury i przerywana kreska
  w szczycie (brak jej, gdy okres jeszcze trwa); drobne włączenia — bledszy pas i przerywana kreska z podpisem „drobne”. Czujnik grzejnika: `GRZEJNIK_DEVICE_ID` w `app_archive.py`.
  Brak tabeli działa jak wyżej — komunikat pod wykresem temperatury.
- Działa pod adresem `http://127.0.0.1:5001`.

Można go też uruchomić bez panelu:

```bash
./.venv/bin/python3 prezentacja/app_archive.py
```

## `app_echarts.py` — wykresy temperatury w Apache ECharts (port 5002)

Te same dane co `app_archive.py` (korzysta z jego funkcji odczytu i list `DEVICES`, `SREDNIE`, `ROZNICE`),
ale tylko cztery wykresy temperatury: **Temperatura** (czujniki w mieszkaniu), **Temperatura — Jasionka**,
**Średnie temperatury** i **Różnica temperatur**, narysowane w Apache ECharts.
Okresy grzania są zaznaczone na wszystkich poza wykresem Jasionki.

- Te same przyciski zakresu co w `app_archive.py`, łącznie ze strzałkami przesuwającymi o 12h.
- Przybliżanie kółkiem myszy, przeciąganie i suwak pod wykresem; wykresy są połączone, więc przybliżenie działa na wszystkich naraz.
- Działa pod adresem `http://127.0.0.1:5002`.

```bash
./.venv/bin/python3 prezentacja/app_echarts.py
```

## `streamlit_app.py` — wykresy ECharts w Streamlit (Streamlit Community Cloud)

Te same cztery wykresy co `app_echarts.py` (wspólny kod wykresów i danych), ale jako aplikacja Streamlit.
Zakres wybiera się kontrolkami Streamlit: czas trwania (2h–7 dni, cała historia), strzałki ◀ 12h / 12h ▶ i **Teraz**.
Dane są trzymane w pamięci podręcznej przez 5 minut. Przybliżanie i suwak działają jak w `app_echarts.py`.

Lokalnie (używa `prezentacja/.env`):

```bash
./.venv/bin/python3 -m streamlit run prezentacja/streamlit_app.py
```

Wdrożenie w [Streamlit Community Cloud](https://share.streamlit.io):

1. Raz uruchomić `prezentacja/odczyt_anon.sql` w Supabase SQL Editor.
2. **Create app** → repozytorium z GitHuba, gałąź `main`, plik główny `prezentacja/streamlit_app.py`
   (zależności z `prezentacja/requirements.txt`).
3. W **Advanced settings → Secrets** wpisać (klucz `anon` z Supabase → Project Settings → API):

   ```toml
   SUPABASE_URL = "https://<projekt>.supabase.co"
   SUPABASE_ANON_KEY = "<klucz anon>"
   ```

   Nie wpisywać tam klucza `service_role`. Zamiast `SUPABASE_ANON_KEY` można użyć nazwy `SUPABASE_KEY`.
4. Aplikacja jest domyślnie publiczna; dostęp można ograniczyć w ustawieniach aplikacji (**Sharing**) do zaproszonych adresów e-mail.
