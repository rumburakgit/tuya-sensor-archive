#!/usr/bin/env python3
"""
Okno startowe i punkt wejścia spakowanego programu (PyInstaller).

Okno ma dwa przyciski: panel pobierania i panel prezentacji.
Każdy panel startuje jako osobny proces, więc oba mogą działać naraz.

Spakowany program zastępuje też interpreter Pythona: panele uruchamiają
skrypty przez `sys.executable -u skrypt.py ...`, a w spakowanym pliku
sys.executable to ten program, nie Python. Dlatego wywołanie
`czujniki-tuya -u skrypt.py [argumenty]` wykonuje wskazany skrypt.
Ścieżki, których nie ma w bieżącym katalogu, liczone są od katalogu
projektu, np.:
    ./czujniki-tuya -u pobieranie/archive.py

Konfiguracja (tylko w spakowanym pliku): jeden wspólny plik .env
(klucze z pobieranie/.env), szukany kolejno:
    1. obok pliku wykonywalnego,
    2. ~/.config/tuya-sensor-archive/.env

Uruchomienie bez pakowania (używa pobieranie/.env i prezentacja/.env):
    ./.venv/bin/python3 pakiet/start.py
"""

import os
import runpy
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from dotenv import load_dotenv


SPAKOWANY = getattr(sys, "frozen", False)

# W spakowanym pliku skrypty leżą w katalogu rozpakowania (_MEIPASS).
KATALOG_PROJEKTU = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
)

PANELE = [
    ("Pobieranie danych", "pobieranie/panel.py"),
    ("Prezentacja", "prezentacja/panel.py"),
]

NAZWA_KONFIGURACJI = "tuya-sensor-archive"


# --------------------------------------------------
# Konfiguracja
# --------------------------------------------------

def miejsca_env():
    katalog_config = Path(
        os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    )
    return [
        Path(sys.executable).resolve().parent / ".env",
        katalog_config / NAZWA_KONFIGURACJI / ".env",
    ]


def zaladuj_env():
    """Wczytuje wspólny .env do środowiska; procesy potomne je dziedziczą."""
    for sciezka in miejsca_env():
        if sciezka.is_file():
            # override: plik mógł zostać zaktualizowany (odświeżone tokeny)
            # od startu procesu nadrzędnego.
            load_dotenv(sciezka, override=True)
            # czujniki.py zapisuje tu odświeżone tokeny Tuya.
            os.environ["SENSOR_ENV_FILE"] = str(sciezka)
            return sciezka
    return None


# --------------------------------------------------
# Tryb interpretera
# --------------------------------------------------

def uruchom_skrypt(argumenty):
    """Odpowiednik `python -u skrypt.py argumenty...`."""
    if not argumenty:
        sys.exit("Użycie: czujniki-tuya -u skrypt.py [argumenty]")

    skrypt = Path(argumenty[0])
    if not skrypt.exists():
        skrypt = KATALOG_PROJEKTU / skrypt

    # Spakowany Python może ignorować PYTHONUNBUFFERED/PYTHONIOENCODING,
    # a panel czyta wyjście linia po linii.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

    sys.argv = [str(skrypt), *argumenty[1:]]
    sys.path.insert(0, str(skrypt.parent))
    runpy.run_path(str(skrypt), run_name="__main__")


# --------------------------------------------------
# Okno startowe
# --------------------------------------------------

class OknoStartowe:
    def __init__(self, root, plik_env):
        self.root = root
        self.procesy = []

        root.title("Czujniki Tuya")
        root.resizable(False, False)

        ramka = ttk.Frame(root, padding=20)
        ramka.pack(fill="both", expand=True)

        for etykieta, panel in PANELE:
            ttk.Button(
                ramka,
                text=etykieta,
                width=30,
                command=lambda p=panel: self.otworz(p),
            ).pack(pady=5)

        if not SPAKOWANY:
            opis, kolor = "Konfiguracja: pobieranie/.env, prezentacja/.env", ""
        elif plik_env is not None:
            opis, kolor = f"Konfiguracja: {plik_env}", ""
        else:
            opis, kolor = "Brak pliku .env — skrypty nie zadziałają", "#c00000"

        ttk.Label(ramka, text=opis, foreground=kolor).pack(pady=(15, 0))

        root.protocol("WM_DELETE_WINDOW", self.zamknij)

        if SPAKOWANY and plik_env is None:
            miejsca = "\n".join(f"  {m}" for m in miejsca_env())
            messagebox.showwarning(
                "Brak konfiguracji",
                "Nie znaleziono pliku .env. Umieść go w jednym z miejsc:\n\n"
                f"{miejsca}\n\n"
                "Wymagane klucze: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, "
                "TUYA_CLIENT_ID, TUYA_CLIENT_SECRET "
                "(opcjonalnie TUYA_ACCESS_TOKEN, TUYA_REFRESH_TOKEN).",
            )

    def otworz(self, panel):
        sciezka = KATALOG_PROJEKTU / panel
        try:
            proces = subprocess.Popen(
                [sys.executable, "-u", str(sciezka)],
                cwd=sciezka.parent,
            )
        except OSError as exc:
            messagebox.showerror("Błąd", f"Nie udało się uruchomić panelu: {exc}")
            return
        self.procesy.append(proces)

    def zamknij(self):
        # Spakowany program usuwa po zamknięciu katalog rozpakowania,
        # z którego korzystają otwarte panele — nie można ich osierocić.
        if any(proces.poll() is None for proces in self.procesy):
            messagebox.showinfo(
                "Panele otwarte",
                "Najpierw zamknij okna paneli, potem okno startowe.",
            )
            return

        self.root.destroy()


def main():
    plik_env = zaladuj_env() if SPAKOWANY else None

    if len(sys.argv) > 1 and sys.argv[1] == "-u":
        uruchom_skrypt(sys.argv[2:])
        return

    root = tk.Tk()
    OknoStartowe(root, plik_env)
    root.mainloop()


if __name__ == "__main__":
    main()
