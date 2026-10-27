# Warstwa prezentacji

Prezentuje pomiary zapisane w Supabase. **Nie komunikuje się z Tuya** —
korzysta wyłącznie z tabel `sensor_readings` (i w przyszłości tabel analitycznych).

## Konfiguracja

Plik `prezentacja/.env` (osobny od `pobieranie/.env`) zawiera tylko:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Docelowo warto zastąpić klucz `service_role` kluczem tylko do odczytu
(klucz `anon` + polityki RLS `select`), bo prezentacja niczego nie zapisuje.

## `panel.py` — panel prezentacji

```bash
./.venv/bin/python3 prezentacja/panel.py
```

Okno z listą sposobów prezentacji. Dla każdego:

- **Uruchom / Zatrzymaj** — startuje lub zatrzymuje serwer,
- **Otwórz w przeglądarce** — aktywny, gdy serwer działa (przy pierwszym starcie strona otwiera się sama).

Logi serwerów są wyświetlane w konsoli panelu. Zamknięcie okna zatrzymuje wszystkie serwery.
Kolejne prezentacje (ECharts, Grafana...) dodaje się jako nowe pozycje listy `PREZENTACJE` w `panel.py`.

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
  w szczycie (brak jej, gdy okres jeszcze trwa). Czujnik grzejnika: `GRZEJNIK_DEVICE_ID` w `app_archive.py`.
  Brak tabeli działa jak wyżej — komunikat pod wykresem temperatury.
- Działa pod adresem `http://127.0.0.1:5001`.

Można go też uruchomić bez panelu:

```bash
./.venv/bin/python3 prezentacja/app_archive.py
```
