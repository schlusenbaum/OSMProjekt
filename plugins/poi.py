import json
from pathlib import Path

from core.config import PROJECT_ROOT, load_config
from core.gpx import add_waypoint, create_gpx, save_gpx


POI_CONFIG_FILE = PROJECT_ROOT / "config" / "poi_types.json"
OUTPUT_DIR = PROJECT_ROOT / "output" / "gpx" / "poi"


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
