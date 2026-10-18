# Tuya Sensor Archive

Archiwizacja odczytów temperatury i wilgotności z czujników Tuya.

## Założenia

- Pobieranie nowych pomiarów co 15 minut.
- Zapis historii w Supabase (PostgreSQL).
- Automatyczne uruchamianie przez GitHub Actions.
- Ochrona przed duplikatami i możliwość nadrabiania zaległości.
- Sekrety przechowywane poza repozytorium.

## Uruchomienie lokalne

Aktywuj środowisko wirtualne i uruchom aplikację zgodnie
z instrukcjami projektu.

source .venv/bin/activate
python3 app.py
