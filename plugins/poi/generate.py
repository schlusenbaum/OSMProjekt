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
