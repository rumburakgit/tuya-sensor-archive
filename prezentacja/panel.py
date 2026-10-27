#!/usr/bin/env python3
"""
Panel warstwy prezentacji.

Każda pozycja z listy PREZENTACJE to osobny serwer (np. dashboard Flask),
który można uruchomić, zatrzymać i otworzyć w przeglądarce.
Wyjście serwerów jest wyświetlane na bieżąco w oknie konsoli.

Uruchomienie:
    ./.venv/bin/python3 prezentacja/panel.py
"""

import os
import queue
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


BASE_DIR = Path(__file__).resolve().parent

# Kolejne sposoby prezentacji (np. Grafana) dopisujemy tutaj.
PREZENTACJE = [
    {
        "nazwa": "Dashboard Supabase",
        "skrypt": "app_archive.py",
        "port": 5001,
    },
    {
        "nazwa": "Wykresy ECharts",
        "skrypt": "app_echarts.py",
        "port": 5002,
    },
]

ODSWIEZANIE_MS = 100
# Flask/werkzeug koloruje logi kodami ANSI, których konsola tkinter nie rozumie.
KODY_ANSI = re.compile(r"\x1b\[[0-9;]*m")
SPRAWDZANIE_PORTU_MS = 500


def port_otwarty(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.05):
            return True
    except OSError:
        return False


class Prezentacja:
    """Jeden wiersz panelu: serwer, jego przyciski i status."""

    def __init__(self, panel, rodzic, wiersz, opis):
        self.panel = panel
        self.nazwa = opis["nazwa"]
        self.skrypt = opis["skrypt"]
        self.port = opis["port"]
        self.url = f"http://127.0.0.1:{self.port}"
        self.proces = None
        self.otwarto_przegladarke = False

        ttk.Label(
            rodzic,
            text=f"{self.nazwa}  ({self.skrypt}, port {self.port})",
        ).grid(row=wiersz, column=0, sticky="w", padx=(0, 20), pady=3)

        self.przycisk = ttk.Button(rodzic, text="Uruchom", command=self.przelacz)
        self.przycisk.grid(row=wiersz, column=1, padx=(0, 10), pady=3)

        self.przycisk_otworz = ttk.Button(
            rodzic,
            text="Otwórz w przeglądarce",
            command=lambda: webbrowser.open(self.url),
            state="disabled",
        )
        self.przycisk_otworz.grid(row=wiersz, column=2, padx=(0, 10), pady=3)

        self.status = ttk.Label(rodzic, text="Zatrzymany", width=14)
        self.status.grid(row=wiersz, column=3, sticky="w", pady=3)

    def przelacz(self):
        if self.proces is None:
            self.uruchom()
        else:
            self.zatrzymaj()

    def uruchom(self):
        if port_otwarty(self.port):
            self.panel.wypisz(
                f"[{self.nazwa}] Port {self.port} jest już zajęty "
                "(czy serwer nie działa w innym oknie?)\n",
                "blad",
            )
            return

        self.panel.wypisz(
            f"\n[{teraz()}] [{self.nazwa}] Uruchamiam {self.skrypt}\n", "info"
        )

        # -u oraz PYTHONUNBUFFERED: logi serwera pojawiają się od razu.
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")

        try:
            self.proces = subprocess.Popen(
                [sys.executable, "-u", str(BASE_DIR / self.skrypt)],
                cwd=BASE_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self.panel.wypisz(
                f"[{self.nazwa}] Nie udało się uruchomić: {exc}\n", "blad"
            )
            self.proces = None
            return

        self.otwarto_przegladarke = False
        self.przycisk.configure(text="Zatrzymaj")
        self.status.configure(text="Startuje...")

        threading.Thread(
            target=self.czytaj_wyjscie,
            args=(self.proces,),
            daemon=True,
        ).start()

        self.panel.root.after(SPRAWDZANIE_PORTU_MS, self.czekaj_na_port)

    def czekaj_na_port(self):
        """Po starcie serwera otwiera przeglądarkę (jednorazowo)."""
        if self.proces is None or self.otwarto_przegladarke:
            return

        if port_otwarty(self.port):
            self.otwarto_przegladarke = True
            self.status.configure(text="Działa")
            self.przycisk_otworz.configure(state="normal")
            webbrowser.open(self.url)
            return

        self.panel.root.after(SPRAWDZANIE_PORTU_MS, self.czekaj_na_port)

    def zatrzymaj(self):
        if self.proces is not None:
            self.panel.wypisz(f"[{teraz()}] [{self.nazwa}] Zatrzymuję\n", "info")
            self.proces.terminate()

    def czytaj_wyjscie(self, proces):
        """Wątek roboczy: przekazuje linie wyjścia do kolejki panelu."""
        for linia in proces.stdout:
            self.panel.kolejka.put((self, "linia", KODY_ANSI.sub("", linia)))
        proces.stdout.close()
        self.panel.kolejka.put((self, "koniec", proces.wait()))

    def zakonczono(self, kod):
        # Kod ujemny = zakończony sygnałem (np. przyciskiem Zatrzymaj).
        if kod == 0 or kod < 0:
            self.panel.wypisz(f"[{teraz()}] [{self.nazwa}] Zatrzymany\n", "ok")
            self.status.configure(text="Zatrzymany")
        else:
            self.panel.wypisz(
                f"[{teraz()}] [{self.nazwa}] Błąd (kod {kod})\n", "blad"
            )
            self.status.configure(text=f"Błąd (kod {kod})")

        self.proces = None
        self.przycisk.configure(text="Uruchom")
        self.przycisk_otworz.configure(state="disabled")


class Panel:
    def __init__(self, root):
        self.root = root
        self.kolejka = queue.Queue()

        root.title("Prezentacja pomiarów")
        root.geometry("900x560")
        root.minsize(600, 360)

        gora = ttk.Frame(root, padding=10)
        gora.pack(fill="x")

        self.prezentacje = [
            Prezentacja(self, gora, wiersz, opis)
            for wiersz, opis in enumerate(PREZENTACJE)
        ]

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

    def wypisz(self, tekst, tag=None):
        self.konsola.configure(state="normal")
        self.konsola.insert("end", tekst, tag)
        self.konsola.see("end")
        self.konsola.configure(state="disabled")

    def odbierz_wyjscie(self):
        """Wątek GUI: tkinter można modyfikować tylko stąd."""
        try:
            while True:
                prezentacja, rodzaj, dane = self.kolejka.get_nowait()

                if rodzaj == "linia":
                    self.wypisz(f"[{prezentacja.nazwa}] {dane}")
                else:
                    prezentacja.zakonczono(dane)
        except queue.Empty:
            pass

        self.root.after(ODSWIEZANIE_MS, self.odbierz_wyjscie)

    def zamknij(self):
        # Serwery nie zapisują danych, więc zatrzymujemy je bez pytania.
        for prezentacja in self.prezentacje:
            if prezentacja.proces is not None:
                prezentacja.proces.terminate()

        self.root.destroy()


def teraz():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
