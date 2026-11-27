# Do zrobienia

## Szacowanie gazu: poziom „kocioł nie pracuje” (`p_baza`)

Stan (27.11.2026): `praca_grzejnika()` w `analityka/003_gas_usage.sql` liczy pole od **stałego**
poziomu `p_baza = −0,7 °C` (różnica grzejnik − kuchnia przy wyłączonym kotle), dobranego ręcznie
z 3 czystych pomiarów.

Co wiadomo:

- Dzienna mediana tego poziomu waha się od −0,6 do −1,0 °C (w ciepłe dni końca października
  do −1,1); w listopadzie zwykle −0,7…−0,8.
- Przesunięcie poziomu o 0,2 °C zmienia szacunek o ok. 7% (k częściowo to wyrównuje).
  Na 3 pomiarach najmniejszy błąd „zostaw jeden”: −0,7 (3,5%) i −0,8 (3,3%).
- Poziom liczony na bieżąco (mediana z chwil bez grzania w ostatnich 12–96 h) wypadł **gorzej**
  (błąd 5,6–9%) i nie działa przy dogrzewaniu bez przerwy dłużej niż dobę.

Do zrobienia, gdy w `gas_calibration` będzie ok. **5 czystych pomiarów** (najlepiej przy różnych
temperaturach na zewnątrz):

1. W `prezentacja/app_gaz.py` dobierać `p_baza` razem z k: siatka −0,4…−1,1 co 0,05, wybór
   najmniejszego błędu „zostaw jeden”. Poniżej 5 pomiarów zostaje −0,7, a obok pokazywać poziom
   wychodzący z danych.
2. Sprawdzić, czy poziom zależy od pogody (np. średnia dobowa `weather_eprz`); jeśli tak —
   uzależnić go od niej.
3. Zaktualizować `zuzycie_gazu/SPECYFIKACJA.md` (sekcje *Cechy* i *Modele*).
