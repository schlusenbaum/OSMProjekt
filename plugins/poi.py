import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import PROJECT_ROOT, load_config
from core.gpx import add_waypoint, create_gpx, save_gpx
from core.tiles import create_tiles


POI_CONFIG_FILE = PROJECT_ROOT / "config" / "poi_types.json"
OUTPUT_DIR = PROJECT_ROOT / "output" / "gpx" / "poi"
PLUGIN_NAME = "poi"
PLUGIN_DESCRIPTION = "POIs"
PLUGIN_COMMAND = "poi"


def load_poi_config() -> dict:
    with POI_CONFIG_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def matches_tags(tags: dict, query: dict) -> bool:
    for key, value in query.items():
        if tags.get(key) != value:
            return False

    return True


def matches_poi_type(tags: dict, queries: list[dict]) -> bool:
    return any(
        matches_tags(tags, query)
        for query in queries
    )


def get_element_coordinates(
    element: dict,
) -> tuple[float, float] | None:
    if element.get("type") == "node":
        if "lat" in element and "lon" in element:
            return element["lat"], element["lon"]

    if "center" in element:
        center = element["center"]

        if "lat" in center and "lon" in center:
            return center["lat"], center["lon"]

    return None


def build_query(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> str:
    config = load_poi_config()
    poi_types = config["poi_types"]

    overpass_config = load_config()["overpass"]
    query_timeout = overpass_config["query_timeout"]

    query_parts = []

    for poi_type in poi_types.values():
        for query in poi_type["query"]:
            filters = "".join(
                f'["{key}"="{value}"]'
                for key, value in query.items()
            )

            query_parts.append(
                f'nwr{filters}'
                f'({min_lat},{min_lon},{max_lat},{max_lon});'
            )

    if not query_parts:
        raise ValueError(
            "Keine POI-Typen vorhanden."
        )

    return (
        f"[out:json][timeout:{query_timeout}];\n"
        "(\n"
        + "\n".join(query_parts)
        + "\n);\n"
        "out center;"
    )


def generate_poi_gpx(
    elements: list[dict],
    poi_type: str,
    output_name: str,
) -> Path:
    config = load_poi_config()
    poi_types = config["poi_types"]

    if poi_type not in poi_types:
        raise ValueError(
            f"Unbekannter POI-Typ: {poi_type}"
        )

    poi_config = poi_types[poi_type]
    queries = poi_config["query"]

    gpx = create_gpx()
    count = 0

    for element in elements:
        tags = element.get("tags", {})

        if not matches_poi_type(tags, queries):
            continue

        coordinates = get_element_coordinates(element)

        if coordinates is None:
            continue

        latitude, longitude = coordinates
        name = tags.get(
            "name",
            poi_config["name"],
        )

        add_waypoint(
            gpx,
            latitude,
            longitude,
            name,
            poi_config["icon"],
        )

        count += 1

    if count == 0:
        print(
            f"{poi_config['name']}: "
            "0 Objekte – keine GPX-Datei erzeugt."
        )
        return None

    output_file = OUTPUT_DIR / output_name
    save_gpx(gpx, output_file)

    print(
        f"{poi_config['name']}: "
        f"{count} Objekte → {output_file}"
    )

    return output_file

# ---------------------------------------------------------------------------
# Plugin-Schnittstelle
# ---------------------------------------------------------------------------

PLUGIN_DESCRIPTION = "POIs"


def discover(bbox: tuple[float, float, float, float], force: bool = False):
    """Liefert die für die Region verfügbaren POI-Typen."""
    config = load_poi_config()
    return list(config["poi_types"].keys())


def sort_items(items: list[str]) -> list[str]:
    """Sortiert die POI-Typen für die Anzeige."""
    config = load_poi_config()
    poi_types = config["poi_types"]

    return sorted(
        items,
        key=lambda poi_type: poi_types[poi_type]["name"].lower(),
    )


def print_items(items: list[str]) -> None:
    """Zeigt die verfügbaren POI-Typen an."""
    config = load_poi_config()
    poi_types = config["poi_types"]

    print()
    print("Verfügbare POIs:")

    for number, poi_type in enumerate(items, start=1):
        print(
            f"  {number}. "
            f"{poi_types[poi_type]['name']}"
        )


def get_selection_prompt() -> str:
    """Liefert das Auswahlformat für die POI-Auswahl."""
    return "POIs auswählen (z. B. 1,3-5, Enter zum Abbrechen): "


def print_selected(items: list[str]) -> None:
    """Zeigt die ausgewählten POI-Typen an."""
    config = load_poi_config()
    poi_types = config["poi_types"]

    print()
    print("Ausgewählte POIs:")

    for poi_type in items:
        print(
            f"  {poi_types[poi_type]['name']}"
        )


def generate(
    items: list[str],
    elements: list[dict],
    region_name: str | None = None,
) -> list[Path]:
    """Erzeugt die GPX-Dateien für die ausgewählten POI-Typen."""
    config = load_poi_config()
    poi_types = config["poi_types"]
    output_files = []

    for poi_type in items:
        poi_name = poi_types[poi_type]["name"]

        if region_name:
            output_name = (
                f"{poi_name}_{region_name}.gpx"
            )
        else:
            output_name = f"{poi_name}.gpx"

        output_file = generate_poi_gpx(
            elements,
            poi_type,
            output_name,
        )

        if output_file is not None:
            output_files.append(output_file)

    return output_files

def configure_cli(parser: argparse.ArgumentParser) -> None:
    """Registriert die Argumente des POI-Plugins."""
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--region", action="store_true")
    location.add_argument("--bbox", help="BBOX: min_lat,min_lon,max_lat,max_lon")
    parser.add_argument("--force", action="store_true")


def run_cli(args: argparse.Namespace) -> None:
    """Führt die vollständige, eigenständige POI-Auswahl aus."""
    from core.cli import (
        collect_osm_data,
        parse_bbox,
        select_region_interactive,
    )
    region = select_region_interactive() if args.region else None
    bbox = (
        (region["min_lat"], region["min_lon"], region["max_lat"], region["max_lon"])
        if region is not None
        else parse_bbox(args.bbox)
    )
    poi_items = sort_items(discover(bbox, force=args.force))
    if not poi_items:
        raise ValueError("Keine POI-Typen verfügbar.")

    print_items(poi_items)
    while True:
        value = input(get_selection_prompt()).strip().lower()
        if value in {"", "q"}:
            print("Auswahl abgebrochen.")
            return
        try:
            selected_numbers = set()
            for part in value.split(","):
                if "-" in part:
                    start, end = (int(number) for number in part.split("-", 1))
                    if start > end:
                        raise ValueError
                    selected_numbers.update(range(start, end + 1))
                else:
                    selected_numbers.add(int(part))
            if not selected_numbers or any(
                number < 1 or number > len(poi_items)
                for number in selected_numbers
            ):
                raise ValueError
        except ValueError:
            print("Ungültige Auswahl. Bitte das angegebene Format verwenden.")
            continue
        break

    selected_items = [poi_items[number - 1] for number in sorted(selected_numbers)]
    print_selected(selected_items)
    min_lat, min_lon, max_lat, max_lon = bbox
    tiles = create_tiles(min_lat, min_lon, max_lat, max_lon)
    print(f"{len(tiles)} Kacheln erzeugt.")
    elements = collect_osm_data(tiles, force=args.force, build_query=build_query)
    print(f"\nInsgesamt {len(elements)} OSM-Objekte aus Cache/Overpass.")

    for output_file in generate(
        selected_items,
        elements,
        region_name=region["name"] if region else None,
    ):
        print(f"  Erzeugt: {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description=PLUGIN_DESCRIPTION)
    configure_cli(parser)
    run_cli(parser.parse_args())


if __name__ == "__main__":
    main()
