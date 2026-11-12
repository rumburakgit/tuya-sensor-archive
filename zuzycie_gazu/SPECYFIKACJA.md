# Szacowanie zużycia gazu z okresów grzania — specyfikacja

Stan: **zaimplementowane** (`analityka/003_gas_usage.sql`, `prezentacja/app_gaz.py`, port 5003).
Model będzie poprawiany wraz z kolejnymi pomiarami.

## Cel

Na podstawie okresów grzania z tabeli `heating_periods` (i surowych odczytów z `sensor_readings`)
szacować, ile m³ gazu zużył kocioł gazowy na ogrzewanie domu w wybranym zakresie dat. Mnożniki
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
więc gaz ≈ k · **pole pod krzywą nadwyżki temperatury grzejnika** (°C·h).

### Dwa tryby grzania — jeden mnożnik nie wystarcza

Sprawdzone na dwóch przykładach (listopad 2026):

| | okresy | Σ pole | Σ wzrost mieszkania | gaz | m³ / °C·h |
|---|---|---|---|---|---|
| **Podtrzymanie** ~8 °C, nikogo nie ma (9.11 19:00 → 11.11 12:00) | 6 × `drobne` | 20,7 °C·h | 0,32 °C | ~1,5 m³ | 0,073 |
| **Przygotowanie na przyjazd** do 15 °C (1.11 8:00 → 2.11 6:00) | 1 × `wyrazne` | 79,5 °C·h | 4,14 °C | ~10 m³ | 0,126 |

Przygotowanie zużywa na jednostkę pola ~1,7 raza więcej gazu. Jeden wspólny mnożnik
(0,115) daje 2,4 m³ zamiast 1,5 dla podtrzymania (+60%). Wyjaśnienie: przy przygotowaniu
mieszkanie nagrzało się z 11,5 do 16,7 °C — duża część gazu poszła w nagrzanie murów i powietrza
(ciepło zgromadzone), czego sama różnica grzejnik − kuchnia nie oddaje (kuchnia rośnie razem
z grzejnikiem, a pozostałe grzejniki w zimnym domu pracują mocniej). Przy podtrzymaniu temperatura
mieszkania stoi w miejscu.

Zamiast pola wyboru „podtrzymanie / przygotowanie” model dostaje drugą cechę — **wzrost
temperatury mieszkania** — więc tryb wynika z danych i model obsłuży też np. przygotowanie
do 18 °C zamiast do 15 °C.

## Cechy (liczone w Postgresie)

Funkcja `cechy_okresow_grzania(p_od, p_do)` zwraca okresy grzania, których garb nachodzi na
zakres `[p_od, p_do)`, z cechami policzonymi **tylko dla części garbu leżącej w zakresie**
(cechy są addytywne: suma po kolejnych zakresach = wynik dla zakresu łącznego).

### Pole (°C·h)

```
nadwyżka(t) = max(0, (T_grzejnik(t) − T_kuchnia(t)) − (T_grzejnik − T_kuchnia)_w chwili poczatek)
pole        = ∫ nadwyżka(t) dt   po części garbu w zakresie (trapezy, interpolacja na granicach)
```

- **Garb** trwa od `poczatek` do pierwszego odczytu po `szczyt`, w którym nadwyżka ≤ 0,1 °C;
  najdłużej do początku następnego okresu albo 48 h. Garb trzeba liczyć dalej niż do `szczyt`,
  bo grzejnik po szczycie dalej pracuje (plateau). Przy przygotowaniu 1.11 grzejnik trzymał
  ~3 °C ponad kuchnią przez całą noc — pierwotny limit 12 h ucinał pole z 79 do 60 °C·h.
  Gdy dane kończą się przed końcem garbu, `garb_trwa = true`.
- **Różnica grzejnik − kuchnia** zamiast samej temperatury przy grzejniku: słońce i ogólne zmiany
  temperatury podnoszą oba czujniki podobnie i się znoszą.
- **Odjęcie różnicy z chwili `poczatek`**: czujniki mają stałe przesunięcie także przy wyłączonym
  grzejniku (np. 10.11: grzejnik 7,1, kuchnia 7,8).
- Temperatura kuchni to ostatni odczyt nie późniejszy niż dany odczyt grzejnika
  (`ostatnia_temperatura()`).

### Wzrost mieszkania (°C)

```
wzrost_mieszkania = max(0, T_mieszkanie(szczyt) − T_mieszkanie(poczatek))
```

z obiema chwilami przyciętymi do zakresu; `T_mieszkanie(t)` = średnia z ostatnich odczytów
czujników grupy `mieszkanie` (`temperatura_grupy()`). Tylko faza **poczatek → szczyt**: garb
podtrzymania trwa godzinami do południa i łapie nagrzewanie od słońca (wzrost do końca garbu
dawał przy podtrzymaniu 1,9 °C zamiast 0,3 °C i psuł dopasowanie).

### Cechy pomocnicze

| Cecha | Jednostka | Opis |
|---|---|---|
| `czas_h` | h | `szczyt − poczatek` (całego okresu) |
| `skok` | °C | `temp_szczyt − temp_poczatek` |

## Modele

| Model | Wzór (sumy po okresach w zakresie) | Minimum pomiarów |
|---|---|---|
| **C (domyślny)** | `m³ = k · Σ pole + c · Σ wzrost_mieszkania` | 2 |
| A | `m³ = k · Σ pole` | 1 |
| B | `m³ = x · Σ czas_h + y · Σ skok` | 2 |

- Domyślny jest pierwszy dopasowany z listy C, A, B.
- **A**: `k = Σ m³ / Σ pole` (iloraz sum, dłuższe pomiary mają większą wagę).
- **B, C**: najmniejsze kwadraty bez wyrazu wolnego, ze współczynnikami ≥ 0.
- Bez wyrazu wolnego (stałego zużycia na dobę), bo w czystych pomiarach gaz idzie tylko na ogrzewanie.
- **Jakość**: błąd walidacji „zostaw jeden” (każdy pomiar przewidywany z mnożników dopasowanych
  do pozostałych), w m³ i %. Liczony, gdy pomiarów jest więcej niż współczynników modelu.

Dopasowanie do dwóch przykładów: **k = 0,050 m³/°C·h, c = 1,45 m³/°C** (model C przechodzi
dokładnie przez oba pomiary — dwa pomiary, dwa współczynniki, więc nic jeszcze nie jest
sprawdzone). Do potwierdzenia potrzebne są kolejne pomiary obu rodzajów; przy pomiarze
podtrzymania warto brać dłuższe zakresy, bo 1,5 m³ przy dokładności odczytu licznika ~0,1 m³
to ±7%.

## Aplikacja (`prezentacja/app_gaz.py`, port 5003)

Flask, jeden plik w stylu `prezentacja/app_archive.py` (HTML w stałej `HTML`, renderowany po
stronie serwera). Używa `prezentacja/.env` z kluczem **`SUPABASE_SERVICE_ROLE_KEY`** (zapisuje
pomiary; działa tylko lokalnie, klucz nie trafia do przeglądarki). Pozycja „Zużycie gazu”
w `prezentacja/panel.py`. Opcjonalnie w `.env`: `GAZ_KWH_NA_M3` (domyślnie 11,1) i
`GAZ_CENA_ZL_M3` (koszt w zł).

- **Szacunek**: zakres od–do (domyślnie ostatnie 7 dni), wynik modelu domyślnego w m³, kWh
  (i zł), obok wyniki pozostałych modeli; tabela okresów z cechami; ostrzeżenia, gdy garb
  wychodzi poza zakres albo ostatni okres jeszcze trwa. Zakres szacunku nie musi być czysty.
- **Modele**: współczynniki i błąd „zostaw jeden”.
- **Czyste pomiary**: formularz (od, do, stany licznika albo zużycie, notatka); tabela z cechami,
  przewidywaniem modelu domyślnego i różnicą; pomiar można wyłączyć z dopasowania albo usunąć.
  Przy pomiarze pojawia się ostrzeżenie, gdy garb przecina jego granicę (liczy się wtedy tylko
  część w zakresie).

## Dane (`analityka/003_gas_usage.sql`)

Idempotentny, uruchamiany ręcznie w Supabase SQL Editor po `002` i `001`:

- tabela `gas_calibration (id, poczatek, koniec, zuzycie_m3, stan_poczatek_m3, stan_koniec_m3,
  aktywny, notatka, utworzono_at)`, RLS bez polityk, dostęp tylko `service_role`;
- `temperatura_grupy(p_grupa, p_ms)` — średnia temperatura grupy czujników w chwili `p_ms`;
- `cechy_okresow_grzania(p_od, p_do, ...)` — okresy z cechami (sekcja *Cechy*).

Dopasowanie mnożników (kilka–kilkadziesiąt pomiarów) robi Python w aplikacji, bez nowych bibliotek.
Weryfikacja SQL: lokalny Postgres 16 i niezależne liczenie cech w Pythonie (zgodność do 0,001).
