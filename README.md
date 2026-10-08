# OSMProjekt

OSMProjekt lädt OpenStreetMap-Daten über Overpass, verarbeitet sie fachbezogen und erzeugt GPX-Dateien für QMapShack.

Das Projekt besteht aus gemeinsamen Core-Komponenten und eigenständigen Plugins für Buslinien, Wanderrouten, Points of Interest (POIs) sowie die Regionsdatenbank.

## Voraussetzungen

- Python 3.10 oder neuer
- Netzwerkzugriff auf mindestens einen konfigurierten Overpass-Server
- Schreibrechte im Projektverzeichnis für Cache, Datenbank und Ausgabe

Es werden ausschließlich Python-Standardbibliotheken verwendet.

## Schnellstart

Der zentrale Einstiegspunkt:

    python3 runner/run.py --help

Die Plugins können auch direkt gestartet werden:

    python3 plugins/bus.py --help
    python3 plugins/hiking.py --help
    python3 plugins/poi.py --help
    python3 plugins/regions.py --help

Der zentrale Runner entdeckt Plugins dynamisch. Er enthält keine fachbezogenen Imports oder Workflows.

## Verwendung

### POIs

POIs für eine interaktiv ausgewählte Region:

    python3 runner/run.py poi --region

POIs für eine direkte Bounding Box:

    python3 runner/run.py poi --bbox 51.88,8.75,51.98,8.98

Der konkrete POI-Typ wird anschließend interaktiv ausgewählt. Die verfügbaren Typen und OSM-Tags stehen in config/poi_types.json.

Den Cache für einen Lauf ignorieren:

    python3 runner/run.py poi --region --force

### Wanderrouten

    python3 runner/run.py route --type hiking --region

### Buslinien

    python3 runner/run.py route --type bus --region

Bus-GPX-Dateien werden unter output/gpx/routes/bus/ nach der ausgewählten Regionshierarchie abgelegt.

### Regionsdatenbank

Import eines Landes:

    python3 runner/run.py regions import --country PL

Ohne --country zeigt das Plugin eine interaktive Länderauswahl:

    python3 runner/run.py regions import

Die Regionsdatenbank liegt unter regions/regions.db.

## Direkter Plugin-Aufruf

Jedes Plugin besitzt seine eigene CLI und kann unabhängig vom Runner gestartet werden:

    python3 plugins/poi.py --region
    python3 plugins/hiking.py --region
    python3 plugins/bus.py --region
    python3 plugins/regions.py import --country DE

Der Runner ist lediglich eine gemeinsame dynamische Startschicht. Fachliche Abläufe bleiben in den Plugins.

## Projektstruktur

    OSMProjekt/
    ├── config/                 Konfiguration und POI-Typen
    ├── core/                   gemeinsame technische Funktionen
    ├── plugins/                eigenständige Fach-Plugins und Registry
    ├── regions/                Regionsimport und Regionsdatenbank
    ├── runner/                 dynamischer Gesamteinstieg
    ├── cache/                  wiederverwendete Overpass-Daten
    └── output/                 erzeugte GPX-Dateien

Wichtige gemeinsame Komponenten:

- core/overpass.py: Server-Failover, Retries und adaptive Abfragen
- core/cli.py: plugin-neutrale Regions-, BBOX- und Cache-Helfer
- core/gpx.py: GPX-Erzeugung
- core/regions.py: Zugriff auf die Regionsdatenbank

## Konfiguration und Cache

Die zentrale Konfiguration steht in config/config.json. Dort werden unter anderem Overpass-Server, Timeouts, Retry-Runden und Cache-Gültigkeiten festgelegt.

Die POI-Abfragen werden in config/poi_types.json gepflegt.

Cache-Dateien liegen unter cache/. Bereits erfolgreiche adaptive Overpass-Teilbereiche werden unmittelbar zwischengespeichert. Der Cache kann bei Bedarf gelöscht oder über --force für einzelne Aufrufe ignoriert werden.

## Fehler bei Overpass-Abfragen

Overpass-Server können zeitweise mit HTTP 500 oder 504 antworten. Das Projekt versucht die konfigurierten Server parallel und wiederholt fehlgeschlagene Abfragen gemäß retry_rounds und retry_delay_seconds.

Bei großen Bereichen ist ein erneuter Versuch später oder eine kleinere Regionsauswahl sinnvoll.

## Dokumentation

- Bedienungsanleitung: OSMProjekt_Bedienungsanleitung.md
- Projektdokumentation: OSMProjekt_Projektdokumentation.md

## Lizenz

Für dieses Repository ist derzeit keine Lizenzdatei hinterlegt. Vor einer Weiterverwendung sollte eine passende Lizenz ergänzt werden.
