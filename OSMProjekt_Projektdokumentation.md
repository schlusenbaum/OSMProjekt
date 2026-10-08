# OSMProjekt – Projektdokumentation

## 1. Zweck des Projekts

OSMProjekt dient dazu, OpenStreetMap-Daten automatisiert abzurufen, aufzubereiten und für die Verwendung in QMapShack bereitzustellen.

Das Projekt ist modular aufgebaut. Fachliche Funktionen wie Wandern, Buslinien und POIs werden als Plugins behandelt. Der zentrale Runner kennt die einzelnen Plugins nicht direkt, sondern lädt sie dynamisch über die Registry.

## 2. Projektstruktur

```text
OSMProjekt/
├── core/
│   ├── config.py
│   ├── regions.py
│   ├── tiles.py
│   ├── gpx.py
│   └── overpass.py
├── config/
│   ├── config.json
│   └── poi_types.json
├── regions/
│   ├── regions.db
│   └── importer.py
├── plugins/
│   ├── registry.py
│   ├── bus.py
│   ├── hiking.py
│   └── poi.py
├── runner/
│   └── run.py
├── cache/
└── output/
```

Alle Pfade in dieser Dokumentation sind relativ zum Projektverzeichnis `OSMProjekt/`.

## 3. Architektur

Die Anwendung ist in drei wesentliche Bereiche gegliedert:

- `core/`: gemeinsame technische Funktionen
- `plugins/`: fachliche Funktionen
- `runner/`: Startpunkt und Ablaufsteuerung

Die Konfiguration liegt unter `config/`. Regionsdaten werden unter `regions/` verwaltet. Wiederverwendbare Overpass-Daten werden unter `cache/` gespeichert.

## 4. Dynamisches Plugin-System

Das Plugin-System ist so aufgebaut, dass `runner/run.py` keine direkte Kenntnis einzelner Plugins wie `bus.py` oder `hiking.py` benötigt.

Die Registry in `plugins/registry.py` erkennt und lädt die verfügbaren Plugins dynamisch.

Grundprinzip:

```text
Runner
  ↓
Registry
  ↓
Plugins
```

Ein neues Plugin kann dadurch in das Plugin-System integriert werden, ohne den Runner um einen speziellen Import oder eine spezielle Fallunterscheidung für dieses Plugin erweitern zu müssen.

## 5. Core-Komponenten

### `core/config.py`

Lädt und verarbeitet die Projektkonfiguration.

### `core/regions.py`

Stellt Funktionen für die Arbeit mit regionalen Daten bereit.

### `core/tiles.py`

Verwaltet die geografische Aufteilung in Kacheln bzw. Bereiche.

### `core/gpx.py`

Enthält gemeinsame Funktionen für die Verarbeitung bzw. Erzeugung von GPX-Daten.

### `core/overpass.py`

Kapselt die Kommunikation mit den konfigurierten Overpass-Servern.

Dazu gehören insbesondere:

- normale Overpass-Abfragen
- Wiederholungsversuche
- adaptive Aufteilung fehlgeschlagener Bereiche
- Verarbeitung erfolgreicher Teilbereiche

## 6. Overpass-Failover

Die normale Overpass-Abfrage durchläuft die in der Konfiguration angegebenen Server.

Wenn ein Server fehlschlägt, wird der nächste Server versucht. Erst wenn die konfigurierten Möglichkeiten ausgeschöpft sind, gilt die Abfrage als fehlgeschlagen.

## 7. Adaptive Overpass-Abfragen

Große geografische Bereiche können bei Overpass problematisch sein. Deshalb verwendet das Projekt eine adaptive Strategie.

Zunächst wird versucht, den gesamten Bereich mit einer einzigen Abfrage abzurufen.

Wenn die Abfrage scheitert:

1. werden konfigurierte Retries ausgeführt,
2. anschließend wird der Bereich geometrisch geteilt,
3. beide Teilbereiche werden separat abgefragt,
4. schlägt ein Teilbereich erneut fehl, wird dieser weiter geteilt,
5. erfolgreiche Teilbereiche werden unmittelbar weiterverarbeitet.

Beispiel:

```text
810 Kacheln
      │
      ├── 405 Kacheln
      │     ├── ...
      │     └── ...
      │
      └── 405 Kacheln
            ├── ...
            └── ...
```

Dadurch kann ein großer Bereich trotz Overpass-Problemen schrittweise verarbeitet werden.

## 8. Sofortiges Caching erfolgreicher Bereiche

Ein wichtiger Bestandteil der aktuellen Architektur ist die sofortige Speicherung erfolgreicher Gruppen.

Sobald eine adaptive Overpass-Abfrage erfolgreich abgeschlossen wurde, wird die Callback-Funktion aufgerufen.

Der Ablauf ist:

```text
Overpass erfolgreich
       ↓
Callback
       ↓
OSM-Objekte den Kacheln zuordnen
       ↓
Cache-Dateien schreiben
       ↓
nächster Bereich
```

Dadurch gehen bereits erfolgreich verarbeitete Bereiche nicht verloren, wenn die Verarbeitung später unterbrochen wird.

Insbesondere bei einer manuellen Unterbrechung mit `Ctrl+C` bleiben bereits gespeicherte Cache-Daten erhalten.

## 9. Rückwärtskompatibilität

Der Callback ist optional.

Die Funktion `query_overpass_adaptive()` liefert weiterhin die bisherigen Rückgabewerte:

```python
successful_groups, failed_areas
```

Bestehende Aufrufer müssen deshalb nicht zwingend angepasst werden.

## 10. Plugins

### Bus

`plugins/bus.py` verarbeitet den öffentlichen Nahverkehr.

### Hiking

`plugins/hiking.py` verarbeitet Wander- bzw. Hiking-Daten und verwendet die adaptive Overpass-Abfrage.

### POI

`plugins/poi.py` verarbeitet Points of Interest.

Die fachliche Logik bleibt innerhalb der jeweiligen Plugins. Gemeinsame technische Funktionen werden nach Möglichkeit in `core/` gehalten.

Jedes Plugin besitzt außerdem eine eigene CLI und kann unabhängig vom Runner gestartet werden:

    python3 plugins/bus.py --help
    python3 plugins/hiking.py --help
    python3 plugins/poi.py --help
    python3 plugins/regions.py --help

Der Runner stellt nur eine dynamische gemeinsame Startschicht bereit. Er importiert keine einzelnen Plugins und enthält keine fachbezogenen Abläufe.

## 11. Cache

Der Cache dient dazu, bereits erfolgreich geladene Daten wiederzuverwenden.

Dadurch werden:

- unnötige Overpass-Anfragen vermieden,
- Laufzeiten reduziert,
- Server entlastet,
- bereits erfolgreich geladene Bereiche bei einem Abbruch erhalten.

Die konkrete Cache-Gültigkeit und Retry-Einstellungen werden über die Projektkonfiguration gesteuert.

## 12. Konfiguration

Die zentrale Konfiguration befindet sich in:

```text
config/config.json
```

Weitere fachliche POI-Konfigurationen befinden sich in:

```text
config/poi_types.json
```

Konfigurationswerte sollten bevorzugt über diese Dateien gesteuert werden und nicht fest im Python-Code stehen.

## 13. Regionen

Regionale Informationen werden in:

```text
regions/regions.db
```

gespeichert.

Der Import bzw. die Pflege der Regionsdaten erfolgt über:

```text
regions/importer.py
```

## 14. Fehlerbehandlung

Fehler bei Overpass werden nicht sofort als endgültiger Fehler behandelt.

Je nach Situation greifen:

- Server-Failover
- Retries
- adaptive Bereichsteilung
- erneute Abfrage kleinerer Bereiche

Ein Bereich gilt erst dann als endgültig fehlgeschlagen, wenn die vorhandenen Wiederholungsmechanismen ausgeschöpft sind und der Bereich nicht sinnvoll weiter geteilt werden kann.

## 15. Entwicklungsprinzipien

Bei Änderungen am Projekt gelten folgende Grundsätze:

1. Bestehende Funktionalität nicht unnötig verändern.
2. Änderungen klein halten.
3. Nach jeder Änderung testen.
4. Keine unnötigen Umbauten gleichzeitig durchführen.
5. Gemeinsame Funktionen in `core/` halten.
6. Fachliche Logik in Plugins halten.
7. Keine Plugin-spezifische Logik in den Runner einbauen.
8. Konfiguration nicht unnötig im Code duplizieren.

## 16. Aktueller Stand

Die dynamische Plugin-Architektur wurde mit dem Commit

```text
83e290e Refactor plugins to dynamic architecture
```

festgehalten.

Die unmittelbare Speicherung erfolgreicher adaptiver Overpass-Gruppen wurde mit dem Commit

```text
873b24e Cache successful adaptive Overpass groups immediately
```

festgehalten.

Der zweite Commit wurde erstellt, aber nicht automatisch veröffentlicht.

## 17. Wiederherstellung und Tests

Vor Änderungen sollte der aktuelle Git-Status geprüft werden:

```bash
cd OSMProjekt
git status
```

Nach Änderungen sollten Syntax und Funktion getestet werden, bevor weitere Änderungen vorgenommen werden.

Beispiel:

```bash
python3 -m py_compile core/overpass.py
python3 -m py_compile runner/run.py
```

## 18. Zukünftige Erweiterungen

Neue Funktionen sollen möglichst als Plugin umgesetzt werden.

Der gewünschte Ablauf ist:

```text
Neue Fachfunktion
      ↓
neues Plugin
      ↓
Registry erkennt Plugin
      ↓
Runner lädt Plugin dynamisch
```

Der Runner soll dadurch stabil und unabhängig von der Anzahl der Plugins bleiben.
