# OSMProjekt – Bedienungsanleitung

Diese Anleitung enthält ausschließlich die praktische Verwendung des Programms:
Aufruf, Parameter und Speicherorte der Ergebnisse.

Alle Pfade sind relativ zum Projektverzeichnis `OSMProjekt/`.

---

## 1. Programmaufruf

Das Programm wird über `runner/run.py` gestartet.

```bash
python3 runner/run.py
```

---

## 2. POI-Daten

Für POI-Daten wird der Unterbefehl `poi` verwendet.

### Syntax

```bash
python3 runner/run.py poi --type TYP --region
```

oder:

```bash
python3 runner/run.py poi --type TYP --bbox min_lat,min_lon,max_lat,max_lon
```

### Parameter

#### `poi`

Startet die POI-Verarbeitung.

#### `--type`

Legt fest, welche POI-Kategorie ausgegeben wird.

Mehrere Kategorien können durch Komma getrennt angegeben werden.

Beispiel:

```bash
python3 runner/run.py poi --type bus_stops,hiking_parking --region
```

Aktuell bekannte POI-Typen sind:

```text
bus_stops
hiking_parking
```

#### `--region`

Startet die interaktive Auswahl einer Region.

Beispiel:

```bash
python3 runner/run.py poi --type bus_stops --region
```

#### `--bbox`

Verwendet stattdessen einen direkt angegebenen geografischen Bereich.

Format:

```text
min_lat,min_lon,max_lat,max_lon
```

Beispiel:

```bash
python3 runner/run.py poi --type bus_stops --bbox 51.88,8.75,51.98,8.98
```

`--region` und `--bbox` werden nicht gleichzeitig verwendet.

#### `--force`

Ignoriert vorhandene Cache-Daten und fragt die benötigten Daten erneut bei Overpass ab.

Beispiel:

```bash
python3 runner/run.py poi --type bus_stops --region --force
```

---

## 3. Wanderwege

Für Wander-Routen wird der Unterbefehl `route` verwendet.

### Syntax

```bash
python3 runner/run.py route --type hiking --region
```

### Parameter

#### `route`

Startet die Routenverarbeitung.

#### `--type hiking`

Verarbeitet Wander-Routen.

#### `--region`

Wählt die Region aus, für die die Wander-Routen ermittelt werden.

Beispiel:

```bash
python3 runner/run.py route --type hiking --region
```

---

## 4. Ergebnisse

### POI-GPX-Dateien

Die erzeugten POI-GPX-Dateien befinden sich unter:

```text
output/gpx/poi/
```

Beispiele:

```text
output/gpx/poi/Bushaltestellen_Detmold.gpx
output/gpx/poi/Bushaltestellen_Horn-Bad_Meinberg.gpx
```

Bei direkter BBOX-Auswahl wird kein Regionsname verwendet.

### Wander-Routen

Die erzeugten GPX-Dateien für Wander-Routen befinden sich unter:

```text
output/gpx/routes/
```

---

## 5. Cache

Die von Overpass geladenen OSM-Daten werden im Cache gespeichert.

POI-/OSM-Cache:

```text
cache/osm/
```

Routen-Cache:

```text
cache/routes/
```

Der Cache wird bei späteren Aufrufen wiederverwendet, sofern die Daten noch gültig sind.

Mit `--force` kann der Cache für einen Aufruf ignoriert werden.

---

## 6. Beispiele

### Bushaltestellen einer Region

```bash
python3 runner/run.py poi --type bus_stops --region
```

### Bushaltestellen und Wanderparkplätze

```bash
python3 runner/run.py poi --type bus_stops,hiking_parking --region
```

### POIs für eine direkte BBOX

```bash
python3 runner/run.py poi --type bus_stops --bbox 51.88,8.75,51.98,8.98
```

### POIs ohne Cache

```bash
python3 runner/run.py poi --type bus_stops --region --force
```

### Wander-Routen einer Region

```bash
python3 runner/run.py route --type hiking --region
```
