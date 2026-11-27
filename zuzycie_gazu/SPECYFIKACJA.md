# Szacowanie zużycia gazu z pracy grzejnika — specyfikacja

Stan: **zaimplementowane** (`analityka/003_gas_usage.sql`, `prezentacja/app_gaz.py`, port 5003).
Model będzie poprawiany wraz z kolejnymi pomiarami.

## Cel

Na podstawie pracy grzejnika (odczyty „Temp Grzejnik” i „Temp Kuchnias” z `sensor_readings_czyste`)
i okresów grzania z tabeli `heating_periods` szacować, ile m³ gazu zużył kocioł gazowy na ogrzewanie domu w wybranym zakresie dat. Mnożniki
zamieniające „ilość grzania” na m³ aplikacja dobiera sama z **czystych pomiarów** wpisywanych
przez użytkownika.

## Co wpisuje użytkownik, a co liczy program

Użytkownik **nie wylicza mnożnika**. Wpisuje **czyste pomiary**: zakresy, w których gaz służył
wyłącznie do ogrzewania domu (nikt nie gotował, nie kąpał się, nie paliło się w piecu węglowym).
Pomiar to:

- **od** i **do** (data i godzina),
- **zużycie** w m³ — wpisane wprost albo jako dwa stany licznika (wtedy zużycie = różnica),
- opcjonalna notatka.

Tylko użytkownik wie, które zakresy są czyste, więc program nie tworzy pomiarów sam. Pomiary nie
muszą do siebie przylegać. Zakresy „brudne” (węgiel, obiad, kąpiel) się pomija.

Dla zakresu każdego pomiaru program liczy cechy (niżej) i dopasowuje mnożniki do wszystkich
aktywnych pomiarów naraz. Im więcej pomiarów, tym pewniejsze mnożniki.

## Fizyka

Gaz grzeje cały dom przez kocioł (pod nieobecność domowników jedynym źródłem ciepła jest gaz).
Czujnik „Temp Grzejnik” w kuchni mierzy pracę tylko jednego grzejnika, ale ten grzejnik grzeje
wtedy, gdy pracuje kocioł, więc jest **przybliżeniem pracy kotła dla całego domu**.

Ciepło oddawane przez grzejnik jest proporcjonalne do różnicy temperatur grzejnik − pomieszczenie,
więc gaz ≈ k · **pole pod krzywą pracy grzejnika** (°C·h).

### Kiedy grzejnik pracuje

Grzejnik wisi przy zimnej ścianie, a „Temp Kuchnias” mierzy temperaturę w głębi kuchni, przy
ścianach wewnętrznych. Gdy kocioł nie pracuje, grzejnik − kuchnia wynosi **ok. −0,7 °C** (od −0,5
do −1,1 °C zależnie od pogody). Grzejnik **pracuje, gdy grzejnik − kuchnia ≥ 0,2 °C**. Sprawdzone
na całej historii od 21.10.2026: każdy przedział z różnicą ≥ 0,2 °C pokrywa się z wykrytym okresem
grzania, bez fałszywych alarmów.

Kocioł potrafi lekko dogrzewać bez przerwy przez dobę i dłużej (np. od 26.11.2026 14:00 grzejnik
stale ~0,7 °C ponad kuchnią). Dlatego pole liczone jest wprost z odczytów, niezależnie od okresów
grzania i bez limitu czasu.

### Jeden mnożnik dla podtrzymania i przygotowania

Pierwsza wersja liczyła pole od różnicy grzejnik − kuchnia z początku każdego okresu grzania
(„garb” okresu, najdłużej 48 h). Ta różnica waha się między okresami od −0,9 do −0,1 °C, więc
przy długim, delikatnym dogrzewaniu pole zależało głównie od przypadkowego punktu startu.
Wychodziło wtedy, że przygotowanie zużywa na jednostkę pola ~1,7 raza więcej gazu niż
podtrzymanie, i potrzebna była druga cecha (wzrost temperatury mieszkania).

Przy polu liczonym od stałego poziomu „kocioł nie pracuje” (−0,7 °C) trzy czyste pomiary
(listopad 2026) dają ten sam mnożnik:

| Pomiar | Gaz | Czas pracy | Pole | m³ / °C·h |
|---|---|---|---|---|
| Podtrzymanie ~8 °C, nikogo nie ma (9.11 19:00 → 11.11 12:00) | 1,5 m³ | 12,7 h | 15,3 °C·h | 0,098 |
| Przygotowanie na przyjazd do 15 °C (1.11 8:00 → 2.11 12:00) | 10 m³ | 23,5 h | 101,2 °C·h | 0,099 |
| Podtrzymanie (24.11 1:00 → 26.11 9:15) | 2,5 m³ | 19,1 h | 24,5 °C·h | 0,102 |

Dla porównania przy poziomie odniesienia 0,1 °C (pole tylko ponad progiem pracy) mnożniki
wynoszą 0,29 / 0,12 / 0,27 — podtrzymanie byłoby wtedy mocno niedoszacowane.

## Cechy (liczone w Postgresie)

### Pole i czas pracy — `praca_grzejnika(p_od, p_do)`

```
x(t)  = T_grzejnik(t) − T_kuchnia(t)
pole  = ∫ max(0, x(t) − p_baza) dt   po odcinkach, w których grzejnik pracuje
```

- `T_kuchnia(t)` = ostatni odczyt kuchni nie późniejszy niż odczyt grzejnika (`ostatnia_temperatura()`).
- Odcinek między kolejnymi odczytami grzejnika jest liczony, gdy na którymkolwiek jego końcu
  `x ≥ p_prog` (0,2 °C) — łapie też rozgrzewanie i stygnięcie przy progu — a odczyty dzieli
  najwyżej `p_max_przerwa` (1 h; odczyty są co ~5 min, dłuższa przerwa = brak danych, odcinek pomijany).
- Trapezy, odcinki na granicach zakresu przycinane z interpolacją liniową, więc **pole i czas
  pracy są ściśle addytywne** (suma po kolejnych zakresach = wynik dla zakresu łącznego) i granice
  pomiaru mogą wypadać w trakcie grzania.
- `czas_pracy_h` = łączna długość liczonych odcinków w zakresie.
- `pracuje_na_koncu = true`, gdy po końcu zakresu nie ma odczytów, a w ostatnim grzejnik pracował
  (wynik może jeszcze wzrosnąć).
- Parametry: `p_prog = 0,2`, `p_baza = −0,7`, `p_max_przerwa = 1 h` (domyślne w SQL).

### Wzrost mieszkania — `cechy_okresow_grzania(p_od, p_do)`

Okresy z `heating_periods`, których faza `poczatek → szczyt` nachodzi na zakres:

```
wzrost_mieszkania = max(0, T_mieszkanie(szczyt) − T_mieszkanie(poczatek))
```

z obiema chwilami przyciętymi do zakresu; `T_mieszkanie(t)` = średnia z ostatnich odczytów
czujników grupy `mieszkanie` (`temperatura_grupy()`). Tylko faza rozgrzewania, bo później łapie
nagrzewanie od słońca. Przy polu od stałego poziomu ta cecha okazała się zbędna (model C dobiera
c = 0), zostaje do sprawdzania na kolejnych pomiarach.

### Cechy pomocnicze (model B)

| Cecha | Jednostka | Opis |
|---|---|---|
| `czas_h` | h | `szczyt − poczatek` (całego okresu) |
| `skok` | °C | `temp_szczyt − temp_poczatek` |

## Modele

| Model | Wzór | Minimum pomiarów |
|---|---|---|
| **A (domyślny)** | `m³ = k · pole` | 1 |
| C | `m³ = k · pole + c · Σ wzrost_mieszkania` | 2 |
| B | `m³ = x · Σ czas_h + y · Σ skok` | 2 |

- Domyślny jest pierwszy dopasowany z listy A, C, B.
- **A**: `k = Σ m³ / Σ pole` (iloraz sum, dłuższe pomiary mają większą wagę).
- **B, C**: najmniejsze kwadraty bez wyrazu wolnego, ze współczynnikami ≥ 0.
- Bez wyrazu wolnego (stałego zużycia na dobę), bo w czystych pomiarach gaz idzie tylko na ogrzewanie.
- **Jakość**: błąd walidacji „zostaw jeden” (każdy pomiar przewidywany z mnożników dopasowanych
  do pozostałych), w m³ i %. Liczony, gdy pomiarów jest więcej niż współczynników modelu.

Dopasowanie do trzech pomiarów: **k = 0,099 m³/°C·h**, błąd „zostaw jeden” 0,09 m³ (2%).
Poziom `p_baza` dobrany z danych ręcznie; przy większej liczbie pomiarów można go dopasowywać
razem z k.

## Aplikacja (`prezentacja/app_gaz.py`, port 5003)

Flask, jeden plik w stylu `prezentacja/app_archive.py` (HTML w stałej `HTML`, renderowany po
stronie serwera). Używa `prezentacja/.env` z kluczem **`SUPABASE_SERVICE_ROLE_KEY`** (zapisuje
pomiary; działa tylko lokalnie, klucz nie trafia do przeglądarki). Pozycja „Zużycie gazu”
w `prezentacja/panel.py`. Opcjonalnie w `.env`: `GAZ_KWH_NA_M3` (domyślnie 11,1) i
`GAZ_CENA_ZL_M3` (koszt w zł).

- **Szacunek**: zakres od–do (domyślnie ostatnie 7 dni), wynik modelu domyślnego w m³, kWh
  (i zł), obok wyniki pozostałych modeli; czas pracy i pole; tabela okresów grzania z cechami;
  ostrzeżenia, gdy grzejnik pracuje w ostatnim odczycie albo rozgrzewanie wychodzi poza zakres. Zakres szacunku nie musi być czysty.
- **Modele**: współczynniki i błąd „zostaw jeden”.
- **Czyste pomiary**: formularz (od, do, stany licznika albo zużycie, notatka); tabela z cechami,
  przewidywaniem modelu domyślnego i różnicą; pomiar można wyłączyć z dopasowania albo usunąć.
  Granice pomiaru mogą wypadać w trakcie grzania (pole jest addytywne).

## Dane (`analityka/003_gas_usage.sql`)

Idempotentny, uruchamiany ręcznie w Supabase SQL Editor po `002` i `001`:

- tabela `gas_calibration (id, poczatek, koniec, zuzycie_m3, stan_poczatek_m3, stan_koniec_m3,
  aktywny, notatka, utworzono_at)`, RLS bez polityk, dostęp tylko `service_role`;
- `temperatura_grupy(p_grupa, p_ms)` — średnia temperatura grupy czujników w chwili `p_ms`;
- `praca_grzejnika(p_od, p_do, ...)` — pole i czas pracy grzejnika (sekcja *Cechy*);
- `cechy_okresow_grzania(p_od, p_do, ...)` — okresy grzania z wzrostem mieszkania (sekcja *Cechy*).

Dopasowanie mnożników (kilka–kilkadziesiąt pomiarów) robi Python w aplikacji, bez nowych bibliotek.
Weryfikacja SQL: lokalny Postgres 16 i niezależne liczenie cech w Pythonie (zgodność do 0,001).
