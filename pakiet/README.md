# Pakowanie do jednego pliku

Oba panele (`pobieranie/panel.py`, `prezentacja/panel.py`) wraz z Pythonem,
tkinter i wszystkimi bibliotekami są pakowane przez PyInstaller (`--onefile`)
do jednego pliku wykonywalnego `czujniki-tuya` (Linux x86_64).

## Uruchomienie na innym komputerze

1. Pobierz `czujniki-tuya-linux-x86_64.tar.gz` z release **najnowsza** na GitHubie
   (repozytorium jest prywatne, np. `gh release download najnowsza -R rumburakgit/tuya-sensor-archive`).
2. Rozpakuj: `tar xzf czujniki-tuya-linux-x86_64.tar.gz`.
3. Połóż plik `.env` obok `czujniki-tuya` **albo** w `~/.config/tuya-sensor-archive/.env`.
   Wystarczy kopia `pobieranie/.env` (zawiera też klucze potrzebne prezentacji).
4. `./czujniki-tuya` otwiera okno startowe z przyciskami **Pobieranie danych** i **Prezentacja**.

Działa na Ubuntu 22.04 i nowszych. Python nie jest potrzebny.

Odświeżone tokeny Tuya są zapisywane do tego samego pliku `.env`.
Okna startowego nie da się zamknąć, dopóki otwarte są panele, bo po jego
zamknięciu znika katalog tymczasowy, z którego panele korzystają.

Pojedynczy skrypt bez okna (np. z crona):

```bash
./czujniki-tuya -u pobieranie/archive.py
./czujniki-tuya -u pobieranie/archive_metar.py --dry-run
```

## Budowanie

Automatycznie: `.github/workflows/build.yml` buduje plik przy każdym pushu do `main`,
który zmienia `pobieranie/`, `prezentacja/` lub `pakiet/` (albo ręcznie przez *Run workflow*),
i podmienia release **najnowsza**. Budowanie odbywa się na `ubuntu-22.04`, żeby plik
działał także na starszych systemach.

Lokalnie (plik `dist/czujniki-tuya`, działa na systemach z glibc nie starszą niż lokalna):

```bash
./pakiet/build.sh
```

Skrypt tworzy własne środowisko `pakiet/.venv-build` (z PyInstallerem), niezależne od `.venv`.

## Jak to działa

- `start.py` to punkt wejścia: okno startowe uruchamia każdy panel jako osobny proces.
- Panele uruchamiają skrypty przez `sys.executable -u skrypt.py`; w spakowanym pliku
  `sys.executable` to sam program, więc `start.py` obsługuje wywołanie `-u skrypt.py`
  jak interpreter Pythona. Dzięki temu panele działają bez zmian.
- Skrypty są dołączane jako pliki danych. Jeśli nowy skrypt importuje bibliotekę,
  której nie używa żaden inny, albo dochodzi nowy skrypt, trzeba go dopisać do
  list `SKRYPTY` / `--hidden-import` w `build.sh`.
