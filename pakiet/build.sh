#!/usr/bin/env bash
# Buduje jeden plik wykonywalny dist/czujniki-tuya (PyInstaller --onefile).
# Uruchomienie z dowolnego katalogu: ./pakiet/build.sh
# Python do budowania można wskazać zmienną PYTHON (domyślnie python3).
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
PYTHON="${PYTHON:-python3}"
VENV=pakiet/.venv-build

[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install -q -r pakiet/requirements.txt

# Skrypty są dołączane jako pliki (panele uruchamiają je po ścieżce),
# a jako --hidden-import, żeby PyInstaller dołączył ich zależności.
SKRYPTY=(
    pobieranie/panel.py
    pobieranie/archive.py
    pobieranie/archive_metar.py
    pobieranie/przelicz_srednie.py
    pobieranie/czujniki.py
    prezentacja/panel.py
    prezentacja/app_archive.py
)

ARGUMENTY=()
for skrypt in "${SKRYPTY[@]}"; do
    ARGUMENTY+=(--add-data "$REPO/$skrypt:$(dirname "$skrypt")")
done

# Moduły z panel.py (dwa pliki o tej samej nazwie, więc wypisane ręcznie).
for modul in archive archive_metar przelicz_srednie czujniki app_archive \
        tkinter.messagebox tkinter.scrolledtext tkinter.ttk \
        queue socket threading webbrowser; do
    ARGUMENTY+=(--hidden-import "$modul")
done

"$VENV/bin/pyinstaller" \
    --noconfirm --clean --onefile \
    --name czujniki-tuya \
    --distpath dist --workpath build --specpath build \
    --paths pobieranie --paths prezentacja \
    "${ARGUMENTY[@]}" \
    pakiet/start.py

echo "Gotowe: $REPO/dist/czujniki-tuya"
