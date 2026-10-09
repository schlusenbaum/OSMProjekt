# OSMProjekt – Bedienungsanleitung

## Installation

Voraussetzungen:
- Python 3.10 oder neuer
- Git
- Internetzugang für den Abruf neuer OSM-Daten

Repository herunterladen und in das Projektverzeichnis wechseln:

    git clone https://github.com/schlusenbaum/OSMProjekt.git
    cd OSMProjekt

Installation prüfen:

    python3 runner/run.py --help

Alle folgenden Befehle werden aus dem Projektverzeichnis ausgeführt.

## Zentraler Einstieg

    python3 runner/run.py --help

### POIs

    python3 runner/run.py poi --region
    python3 runner/run.py poi --bbox 51.88,8.75,51.98,8.98
    python3 runner/run.py poi --region --force

Die POI-Kategorien werden nach der Bereichsauswahl interaktiv gewählt. Die verfügbaren Kategorien stehen in config/poi_types.json.

### Wanderrouten

    python3 runner/run.py route --type hiking --region

### Buslinien

    python3 runner/run.py route --type bus --region

### Fahrradrouten

    python3 runner/run.py route --type bicycle --region

### Regionsimport

    python3 runner/run.py regions import --country PL
    python3 runner/run.py regions import

Der zweite Aufruf zeigt eine interaktive Länderauswahl.

## Plugins direkt starten

Die Plugins sind unabhängig vom Runner ausführbar:

    python3 plugins/poi.py --help
    python3 plugins/poi.py --region
    python3 plugins/hiking.py --region
    python3 plugins/bus.py --region
    python3 plugins/bicycle.py --help
    python3 plugins/regions.py import --country DE

## Ausgabeverzeichnisse

- POI-GPX-Dateien: output/gpx/poi/
- Wander-GPX-Dateien: output/gpx/routes/
- Bus-GPX-Dateien: output/gpx/routes/bus/
- Regionsdatenbank: regions/regions.db
- Cache: cache/

## Konfiguration

Overpass-Server, Retries, Timeouts und Cache-Gültigkeit werden in config/config.json eingestellt. POI-Typen und ihre OSM-Tags werden in config/poi_types.json gepflegt.

Bei temporären Overpass-Fehlern kann der Aufruf wiederholt werden. Große Regionen können wegen Server-Limits länger dauern oder eine kleinere Auswahl erfordern.
