#!/usr/bin/env python3
"""
Panel do ręcznego uruchamiania archiwizacji.

Przyciski uruchamiają archive.py (Tuya -> Supabase),
archive_metar.py (METAR EPRZ -> Supabase) oraz przelicz_srednie.py
(cała historia średnich). Po każdej archiwizacji średnie dla nowych
danych są przeliczane automatycznie.
Wyjście skryptów jest wyświetlane na bieżąco w oknie konsoli.

Uruchomienie:
    ./.venv/bin/python3 pobieranie/panel.py
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


BASE_DIR = Path(__file__).resolve().parent

SKRYPTY = [
    ("Archiwizuj Tuya", "archive.py"),
    ("Archiwizuj METAR", "archive_metar.py"),
    ("Przelicz średnie", "przelicz_srednie.py"),
]

# Po każdej archiwizacji przeliczamy średnie dla nowo dodanych pomiarów.
ARCHIWIZATORY = {"archive.py", "archive_metar.py"}
PRZELICZENIE = "przelicz_srednie.py"

# Zapas na różnicę zegarów komputera i bazy (inserted_at).
ZAPAS_ZEGARA = timedelta(minutes=5)

ODSWIEZANIE_MS = 100


class Panel:
    def __init__(self, root):
        self.root = root
        self.proces = None
        self.start_utc = None
        self.kolejka = queue.Queue()

        root.title("Archiwizacja czujników")
        root.geometry("900x560")
        root.minsize(600, 360)

        gora = ttk.Frame(root, padding=10)
        gora.pack(fill="x")

        self.przyciski = []
        for etykieta, skrypt in SKRYPTY:
            przycisk = ttk.Button(
                gora,
                text=f"{etykieta}  ({skrypt})",
                command=lambda s=skrypt: self.uruchom(s),
            )
            przycisk.pack(side="left", padx=(0, 10))
            self.przyciski.append(przycisk)

        self.status = ttk.Label(gora, text="Gotowy")
        self.status.pack(side="right")

        self.konsola = ScrolledText(
            root,
            wrap="word",
            font=("DejaVu Sans Mono", 10),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            state="disabled",
        )
        self.konsola.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.konsola.tag_configure("info", foreground="#4fc1ff")
        self.konsola.tag_configure("ok", foreground="#6a9955")
        self.konsola.tag_configure("blad", foreground="#f44747")

        root.protocol("WM_DELETE_WINDOW", self.zamknij)
        root.after(ODSWIEZANIE_MS, self.odbierz_wyjscie)

    # --------------------------------------------------
    # Konsola
    # --------------------------------------------------

    def wypisz(self, tekst, tag=None):
        self.konsola.configure(state="normal")
        self.konsola.insert("end", tekst, tag)
        self.konsola.see("end")
        self.konsola.configure(state="disabled")

    def ustaw_przyciski(self, aktywne):
        stan = "normal" if aktywne else "disabled"
        for przycisk in self.przyciski:
            przycisk.configure(state=stan)

    # --------------------------------------------------
    # Uruchamianie skryptów
    # --------------------------------------------------

    def uruchom(self, skrypt, argumenty=()):
        if self.proces is not None:
            return

        czas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        polecenie = " ".join([skrypt, *argumenty])
        self.wypisz(f"\n[{czas}] Uruchamiam {polecenie}\n", "info")
        self.start_utc = datetime.now(timezone.utc)

        # -u oraz PYTHONUNBUFFERED: wyjście pojawia się od razu,
        # a nie dopiero po zakończeniu skryptu.
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")

        try:
            self.proces = subprocess.Popen(
                [sys.executable, "-u", str(BASE_DIR / skrypt), *argumenty],
                cwd=BASE_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self.wypisz(f"Nie udało się uruchomić {skrypt}: {exc}\n", "blad")
            self.proces = None
            return

        self.ustaw_przyciski(False)
        self.status.configure(text=f"Działa: {skrypt}")

        threading.Thread(
            target=self.czytaj_wyjscie,
            args=(self.proces, skrypt),
            daemon=True,
        ).start()

    def czytaj_wyjscie(self, proces, skrypt):
        """Wątek roboczy: przekazuje linie wyjścia do kolejki."""
        for linia in proces.stdout:
            self.kolejka.put(("linia", linia))
        proces.stdout.close()
        self.kolejka.put(("koniec", (skrypt, proces.wait())))

    def odbierz_wyjscie(self):
        """Wątek GUI: tkinter można modyfikować tylko stąd."""
        try:
            while True:
                rodzaj, dane = self.kolejka.get_nowait()

                if rodzaj == "linia":
                    self.wypisz(dane)
                    continue

                skrypt, kod = dane
                czas = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if kod == 0:
                    self.wypisz(f"[{czas}] {skrypt}: zakończono pomyślnie\n", "ok")
                    self.status.configure(text=f"OK: {skrypt}")
                else:
                    self.wypisz(f"[{czas}] {skrypt}: błąd (kod {kod})\n", "blad")
                    self.status.configure(text=f"Błąd: {skrypt}")

                self.proces = None
                self.ustaw_przyciski(True)

                # Także po błędzie: część danych mogła zostać zapisana.
                if skrypt in ARCHIWIZATORY:
                    nowe_od = self.start_utc - ZAPAS_ZEGARA
                    self.uruchom(PRZELICZENIE, ["--nowe-od", nowe_od.isoformat()])
        except queue.Empty:
            pass

        self.root.after(ODSWIEZANIE_MS, self.odbierz_wyjscie)

    def zamknij(self):
        if self.proces is not None:
            if not messagebox.askyesno(
                "Archiwizacja w toku",
                "Skrypt nadal działa. Przerwać go i zamknąć panel?",
            ):
                return
            self.proces.terminate()

        self.root.destroy()


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
