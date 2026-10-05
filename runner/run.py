import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent),
)

from core.cache import (
    create_cache_version,
    get_cache_file,
    load_cache,
    save_cache,
)
from core.config import load_config
from core.regions import get_children, get_region
from core.tiles import create_tiles
from plugins.poi.generate import build_query, generate_poi_gpx, load_poi_config
from plugins.routes.registry import get_route_plugin, get_route_plugins


def load_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OSMProjekt"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    regions_parser = subparsers.add_parser(
        "regions",
        help="Region auswählen",
    )

    poi_parser = subparsers.add_parser(
        "poi",
        help="POIs erzeugen",
    )

    poi_parser.add_argument(
        "--type",
        required=True,
        help="Ein oder mehrere POI-Typen, durch Komma getrennt",
    )

    region_or_bbox = poi_parser.add_mutually_exclusive_group(
        required=True,
    )

    region_or_bbox.add_argument(
        "--region",
        action="store_true",
        help="Arbeitsregion interaktiv auswählen",
    )

    region_or_bbox.add_argument(
        "--bbox",
        help="BBOX: min_lat,min_lon,max_lat,max_lon",
    )

    poi_parser.add_argument(
        "--force",
        action="store_true",
        help="Cache ignorieren und Overpass erneut abfragen",
    )

    route_parser = subparsers.add_parser(
        "route",
        help="Routen suchen und erzeugen",
    )

    route_plugins = get_route_plugins()
    route_type_help = "Routentyp: " + ", ".join(
        f"{name} = {module.PLUGIN_DESCRIPTION}"
        for name, module in sorted(route_plugins.items())
    )

    route_parser.add_argument(
        "--type",
        required=True,
        choices=sorted(route_plugins),
        help=route_type_help,
    )

    route_parser.add_argument(
        "--region",
        action="store_true",
        help="Arbeitsregion interaktiv auswählen",
    )

    return parser.parse_args()


def select_region_interactive() -> dict:
    current_region = get_region(1)

    if current_region is None:
        raise RuntimeError(
            "Deutschland wurde in der Regionsdatenbank nicht gefunden."
        )

    region_history = []

    while True:
        print()
        print(
            f"Region: {current_region['name']}"
        )
        print(
            f"Typ: {current_region['type']}"
        )

        children = get_children(current_region["id"])

        if not children:
            print()
            print("Keine weiteren Unterregionen vorhanden.")
            print(
                "Ausgewählte Region: "
                f"{current_region['name']}"
            )
            print(
                "BBox: "
                f"{current_region['min_lat']},"
                f"{current_region['min_lon']},"
                f"{current_region['max_lat']},"
                f"{current_region['max_lon']}"
            )
            return current_region

        print()

        for number, child in enumerate(children, start=1):
            print(
                f"  {number}. {child['name']}"
            )

        print()
        print("  Enter/0. Diese Region verwenden")
        print("  b. Eine Ebene zurück")
        print("  q. Auswahl abbrechen")

        while True:
            value = input("Auswahl: ").strip().lower()

            if value == "q":
                print("Auswahl abgebrochen.")
                raise SystemExit(0)

            if value == "b":
                if region_history:
                    current_region = region_history.pop()
                    break
                print("Bereits auf oberster Ebene.")
                continue

            if value in ("", "0"):
                selection = 0
            else:
                try:
                    selection = int(value)
                except ValueError:
                    print(
                        "Bitte eine Nummer eingeben, "
                        "Enter, b zum Zurückgehen "
                        "oder q zum Abbrechen."
                    )
                    continue

            if selection == 0:
                print()
                print(
                    f"Ausgewählte Region: "
                    f"{current_region['name']}"
                )
                print(
                    "BBox: "
                    f"{current_region['min_lat']},"
                    f"{current_region['min_lon']},"
                    f"{current_region['max_lat']},"
                    f"{current_region['max_lon']}"
                )
                return current_region

            if 1 <= selection <= len(children):
                region_history.append(current_region)
                current_region = children[selection - 1]
                break

            print(
                f"Bitte eine Nummer zwischen 0 und "
                f"{len(children)} eingeben "
                "oder 'q' zum Abbrechen."
            )


def parse_bbox(
    value: str,
) -> tuple[float, float, float, float]:
    parts = value.split(",")

    if len(parts) != 4:
        raise ValueError(
            "BBOX muss aus vier Werten bestehen: "
            "min_lat,min_lon,max_lat,max_lon"
        )

    return tuple(float(part) for part in parts)


def deduplicate_elements(
    elements: list[dict],
) -> list[dict]:
    seen = set()
    unique_elements = []

    for element in elements:
        key = (
            element.get("type"),
            element.get("id"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_elements.append(element)

    return unique_elements


def get_cache_age_text(
    tile: dict,
) -> str:
    cache_file = get_cache_file(tile)

    if not cache_file.exists():
        return "unbekannt"

    modified = datetime.fromtimestamp(
        cache_file.stat().st_mtime,
        tz=timezone.utc,
    )

    age = datetime.now(timezone.utc) - modified

    days = age.days
    hours = age.seconds // 3600

    if days > 0:
        return f"{days} Tag(e), {hours} Stunde(n)"

    return f"{hours} Stunde(n)"


def get_element_coordinates(element: dict) -> tuple[float, float] | None:
    if element.get("type") == "node":
        latitude = element.get("lat")
        longitude = element.get("lon")
    else:
        center = element.get("center", {})
        latitude = center.get("lat")
        longitude = center.get("lon")

    if latitude is None or longitude is None:
        return None

    return float(latitude), float(longitude)


def get_tiles_bbox(tiles: list[dict]) -> tuple[float, float, float, float]:
    return (
        min(tile["min_lat"] for tile in tiles),
        min(tile["min_lon"] for tile in tiles),
        max(tile["max_lat"] for tile in tiles),
        max(tile["max_lon"] for tile in tiles),
    )


def split_tile_group(tiles: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(tiles) < 2:
        raise ValueError(
            "Eine Kachelgruppe mit weniger als zwei Kacheln "
            "kann nicht geteilt werden."
        )

    lat_span = (
        max(tile["max_lat"] for tile in tiles)
        - min(tile["min_lat"] for tile in tiles)
    )

    lon_span = (
        max(tile["max_lon"] for tile in tiles)
        - min(tile["min_lon"] for tile in tiles)
    )

    if lon_span >= lat_span:
        sorted_tiles = sorted(
            tiles,
            key=lambda tile: (
                tile["min_lon"],
                tile["min_lat"],
            ),
        )
    else:
        sorted_tiles = sorted(
            tiles,
            key=lambda tile: (
                tile["min_lat"],
                tile["min_lon"],
            ),
        )

    split_index = len(sorted_tiles) // 2

    return (
        sorted_tiles[:split_index],
        sorted_tiles[split_index:],
    )


def assign_elements_to_tiles(
    elements: list[dict],
    tiles: list[dict],
) -> dict[tuple[float, float], list[dict]]:
    tile_elements = {
        (
            tile["min_lat"],
            tile["min_lon"],
        ): []
        for tile in tiles
    }

    for element in elements:
        coordinates = get_element_coordinates(element)

        if coordinates is None:
            continue

        latitude, longitude = coordinates

        for tile in tiles:
            if (
                tile["min_lat"] <= latitude <= tile["max_lat"]
                and tile["min_lon"] <= longitude <= tile["max_lon"]
            ):
                tile_elements[
                    (
                        tile["min_lat"],
                        tile["min_lon"],
                    )
                ].append(element)

    return tile_elements


CACHE_COORDINATE_TOLERANCE = 0.00001


def get_cache_tile_from_file(path: Path) -> dict | None:
    try:
        min_lat, min_lon, max_lat, max_lon = map(
            float,
            path.stem.split("_"),
        )
    except ValueError:
        return None

    return {
        "min_lat": min_lat,
        "min_lon": min_lon,
        "max_lat": max_lat,
        "max_lon": max_lon,
    }


def tile_is_fully_covered(
    tile: dict,
    source_tiles: list[dict],
) -> bool:
    if not source_tiles:
        return False

    tolerance = CACHE_COORDINATE_TOLERANCE

    lat_values = {
        tile["min_lat"],
        tile["max_lat"],
    }
    lon_values = {
        tile["min_lon"],
        tile["max_lon"],
    }

    for source in source_tiles:
        if (
            source["max_lat"]
            >= tile["min_lat"] - tolerance
            and source["min_lat"]
            <= tile["max_lat"] + tolerance
            and source["max_lon"]
            >= tile["min_lon"] - tolerance
            and source["min_lon"]
            <= tile["max_lon"] + tolerance
        ):
            lat_values.add(
                max(
                    source["min_lat"],
                    tile["min_lat"],
                )
            )
            lat_values.add(
                min(
                    source["max_lat"],
                    tile["max_lat"],
                )
            )
            lon_values.add(
                max(
                    source["min_lon"],
                    tile["min_lon"],
                )
            )
            lon_values.add(
                min(
                    source["max_lon"],
                    tile["max_lon"],
                )
            )

    lat_values = sorted(lat_values)
    lon_values = sorted(lon_values)

    for lat_index in range(len(lat_values) - 1):
        for lon_index in range(len(lon_values) - 1):
            cell_min_lat = lat_values[lat_index]
            cell_max_lat = lat_values[lat_index + 1]
            cell_min_lon = lon_values[lon_index]
            cell_max_lon = lon_values[lon_index + 1]

            if (
                cell_max_lat - cell_min_lat
                <= tolerance
                or
                cell_max_lon - cell_min_lon
                <= tolerance
            ):
                continue

            if (
                cell_min_lat
                < tile["min_lat"] - tolerance
                or
                cell_max_lat
                > tile["max_lat"] + tolerance
                or
                cell_min_lon
                < tile["min_lon"] - tolerance
                or
                cell_max_lon
                > tile["max_lon"] + tolerance
            ):
                continue

            covered = any(
                source["min_lat"]
                <= cell_min_lat + tolerance
                and source["max_lat"]
                >= cell_max_lat - tolerance
                and source["min_lon"]
                <= cell_min_lon + tolerance
                and source["max_lon"]
                >= cell_max_lon - tolerance
                for source in source_tiles
            )

            if not covered:
                return False

    return True


def load_cached_source_tiles(
    target_tile: dict,
) -> list[tuple[dict, dict]]:
    cache_dir = (
        Path(__file__).resolve().parent.parent
        / "cache"
        / "osm"
    )

    candidates = []
    tolerance = CACHE_COORDINATE_TOLERANCE

    for cache_file in cache_dir.glob("*.json"):
        source_tile = get_cache_tile_from_file(
            cache_file
        )

        if source_tile is None:
            continue

        if not (
            source_tile["min_lat"]
            <= target_tile["max_lat"] + tolerance
            and source_tile["max_lat"]
            >= target_tile["min_lat"] - tolerance
            and source_tile["min_lon"]
            <= target_tile["max_lon"] + tolerance
            and source_tile["max_lon"]
            >= target_tile["min_lon"] - tolerance
        ):
            continue

        try:
            with cache_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                cache_data = json.load(file)
        except (
            OSError,
            json.JSONDecodeError,
        ):
            continue

        metadata = cache_data.get("_meta", {})
        source_version = metadata.get(
            "cache_version"
        )

        if not source_version:
            continue

        cached_data = load_cache(
            source_tile,
            source_version,
        )

        if cached_data is None:
            continue

        candidates.append(
            (
                source_tile,
                cached_data,
            )
        )

    return candidates


def build_tile_from_existing_cache(
    target_tile: dict,
) -> dict | None:
    cached_sources = load_cached_source_tiles(
        target_tile
    )

    if not cached_sources:
        return None

    source_tiles = [
        source_tile
        for source_tile, _ in cached_sources
    ]

    if not tile_is_fully_covered(
        target_tile,
        source_tiles,
    ):
        return None

    tolerance = CACHE_COORDINATE_TOLERANCE
    elements_by_id = {}

    for source_tile, cached_data in cached_sources:
        for element in cached_data.get(
            "elements",
            [],
        ):
            coordinates = get_element_coordinates(
                element
            )

            if coordinates is None:
                continue

            latitude, longitude = coordinates

            if not (
                target_tile["min_lat"] - tolerance
                <= latitude
                <= target_tile["max_lat"] + tolerance
                and
                target_tile["min_lon"] - tolerance
                <= longitude
                <= target_tile["max_lon"] + tolerance
            ):
                continue

            key = (
                element.get("type"),
                element.get("id"),
            )

            elements_by_id[key] = element

    data = {
        "elements": list(
            elements_by_id.values()
        )
    }

    query = build_query(
        target_tile["min_lat"],
        target_tile["min_lon"],
        target_tile["max_lat"],
        target_tile["max_lon"],
    )

    cache_version = create_cache_version(query)

    save_cache(
        target_tile,
        data,
        cache_version,
    )

    print(
        "  Aus vorhandenen Cache-Flächen "
        f"lokal zusammengesetzt: "
        f"{len(data['elements'])} OSM-Objekte."
    )

    return data


def query_tile_group(
    tiles: list[dict],
    all_tiles: list[dict],
    force: bool,
) -> tuple[list[dict], list[dict]]:
    if not tiles:
        return [], []

    from core.overpass import query_overpass_adaptive

    print(
        f"  Starte adaptive Overpass-Abfrage "
        f"für {len(tiles)} Kacheln."
    )

    def build_group_query(
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
    ) -> str:
        return build_query(
            min_lat,
            min_lon,
            max_lat,
            max_lon,
        )

    config = load_config()

    successful_groups, failed_tiles = query_overpass_adaptive(
        tiles,
        build_group_query,
    )

    successful_tiles = []

    for group, data in successful_groups:
        elements = data.get("elements", [])

        print(
            f"  Bereich erfolgreich: "
            f"{len(group)} Kacheln, "
            f"{len(elements)} OSM-Objekte."
        )

        tile_elements = assign_elements_to_tiles(
            elements,
            group,
        )

        for tile in group:
            tile_key = (
                tile["min_lat"],
                tile["min_lon"],
            )

            tile_data = {
                "elements": tile_elements[tile_key],
            }

            tile_query = build_query(
                tile["min_lat"],
                tile["min_lon"],
                tile["max_lat"],
                tile["max_lon"],
            )

            tile_cache_version = create_cache_version(
                tile_query
            )

            save_cache(
                tile,
                tile_data,
                tile_cache_version,
            )

            successful_tiles.append(tile)

    return successful_tiles, failed_tiles

def collect_osm_data(
    tiles: list[dict],
    force: bool = False,
) -> list[dict]:
    config = load_config()
    all_elements = []

    tiles_to_query = []
    fallback_tiles = {}
    failed_tiles = []

    for number, tile in enumerate(
        tiles,
        start=1,
    ):
        print(
            f"Kachel {number}/{len(tiles)}: "
            f"{tile['min_lat']},{tile['min_lon']} → "
            f"{tile['max_lat']},{tile['max_lon']}"
        )

        query = build_query(
            tile["min_lat"],
            tile["min_lon"],
            tile["max_lat"],
            tile["max_lon"],
        )

        cache_version = create_cache_version(query)
        cache_file = get_cache_file(tile)

        cached_data = None

        if not force:
            cached_data = load_cache(
                tile,
                cache_version,
            )

        if cached_data is not None:
            print("  Cache verwendet.")

            all_elements.extend(
                cached_data.get("elements", [])
            )

            continue

        if not force:
            reconstructed_data = (
                build_tile_from_existing_cache(tile)
            )

            if reconstructed_data is not None:
                all_elements.extend(
                    reconstructed_data.get(
                        "elements",
                        [],
                    )
                )

                print(
                    "  Cache aus vorhandenen "
                    "Flächen rekonstruiert."
                )

                continue

        if force:
            print(
                "  --force: Cache wird ignoriert – "
                "Overpass wird abgefragt."
            )
        elif not cache_file.exists():
            print(
                "  Kein Cache vorhanden – "
                "Overpass wird abgefragt."
            )
        elif load_cache(
            tile,
            cache_version,
            allow_expired=True,
        ) is not None:
            print(
                "  Cache ist abgelaufen – "
                "Overpass wird abgefragt."
            )
        else:
            print(
                "  Cache ist nicht zur aktuellen "
                "Abfrage passend – "
                "Overpass wird abgefragt."
            )

        if cache_file.exists():
            fallback_data = load_cache(
                tile,
                cache_version,
                allow_expired=True,
            )

            if fallback_data is not None:
                fallback_tiles[
                    (
                        tile["min_lat"],
                        tile["min_lon"],
                    )
                ] = fallback_data

        tiles_to_query.append(tile)

    if tiles_to_query:
        print()
        print(
            f"{len(tiles_to_query)} Kacheln benötigen "
            "eine Overpass-Abfrage."
        )
        print(
            "Versuche zunächst den gesamten Bereich "
            "in einer Anfrage."
        )

        successful_tiles, failed_tiles = query_tile_group(
            tiles_to_query,
            tiles,
            force,
        )

        successful_keys = {
            (
                tile["min_lat"],
                tile["min_lon"],
            )
            for tile in successful_tiles
        }

        for tile in successful_tiles:
            query = build_query(
                tile["min_lat"],
                tile["min_lon"],
                tile["max_lat"],
                tile["max_lon"],
            )

            cache_version = create_cache_version(query)

            cached_data = load_cache(
                tile,
                cache_version,
            )

            if cached_data is not None:
                all_elements.extend(
                    cached_data.get("elements", [])
                )

        for tile in successful_tiles:
            fallback_tiles.pop(
                (
                    tile["min_lat"],
                    tile["min_lon"],
                ),
                None,
            )

        failed_tiles = [
            tile
            for tile in failed_tiles
            if (
                tile["min_lat"],
                tile["min_lon"],
            ) not in successful_keys
        ]

    for tile in failed_tiles:
        fallback_key = (
            tile["min_lat"],
            tile["min_lon"],
        )

        fallback_data = fallback_tiles.get(
            fallback_key
        )

        if fallback_data is None:
            continue

        age_text = get_cache_age_text(tile)

        print(
            "  WARNUNG: Verwende alten Cache als "
            "Fallback für Kachel "
            f"{tile['min_lat']},{tile['min_lon']} → "
            f"{tile['max_lat']},{tile['max_lon']} "
            f"(Alter: {age_text})."
        )

        all_elements.extend(
            fallback_data.get("elements", [])
        )

    if failed_tiles:
        print(
            f"\n{len(failed_tiles)} Kachel(n) "
            "konnten nicht aktuell geladen werden."
        )

        for tile in failed_tiles:
            fallback_key = (
                tile["min_lat"],
                tile["min_lon"],
            )

            if fallback_key in fallback_tiles:
                print(
                    "  Fallback verwendet: "
                    f"{tile['min_lat']},{tile['min_lon']} → "
                    f"{tile['max_lat']},{tile['max_lon']}"
                )
            else:
                print(
                    "  Kein Fallback verfügbar: "
                    f"{tile['min_lat']},{tile['min_lon']} → "
                    f"{tile['max_lat']},{tile['max_lon']}"
                )

    return deduplicate_elements(all_elements)

def main() -> None:
    args = load_arguments()

    if args.command == "regions":
        select_region_interactive()
        return

    if args.command == "route":
        route_plugin = get_route_plugin(args.type)

        if args.type == "bus":
            if not args.region:
                raise ValueError(
                    "Für route bus muss --region angegeben werden."
                )

            while True:
                back_to_region = False

                region = select_region_interactive()

                bbox = (
                    region["min_lat"],
                    region["min_lon"],
                    region["max_lat"],
                    region["max_lon"],
                )

                route_items = route_plugin.discover(
                    bbox,
                    force=getattr(args, "force", False),
                )

                route_items.sort(
                    key=lambda item: item.name.lower(),
                )

                bus_lines = {
                    item.id: item
                    for item in route_items
                }

                line_refs = list(bus_lines)

                print()
                print(
                    f"Gefundene Buslinien: {len(line_refs)}"
                )

                print()
                print(
                    f"{'Nr':>3} | Linie"
                )
                print("-" * 50)

                for number, ref in enumerate(line_refs, 1):
                    route_item = bus_lines[ref]
                    print(
                        f"{number:>3} | "
                        f"{route_item.name:<16} | "
                        f"{len(route_item.data)} Relation(en)"
                    )

                while True:
                    value = input(
                        "\nAuswahl (z.B. 3 oder 3,7-10, b zurück, q zum Abbrechen): "
                    ).strip().lower()

                    if value == "q":
                        print("Auswahl abgebrochen.")
                        return

                    if value == "b":
                        print("Zurück zur Regionenauswahl.")
                        back_to_region = True
                        break

                    selected_numbers = set()

                    try:
                        for part in value.split(","):
                            part = part.strip()

                            if "-" in part:
                                start_number, end_number = part.split("-", 1)
                                start_number = int(start_number)
                                end_number = int(end_number)

                                if start_number > end_number:
                                    raise ValueError

                                selected_numbers.update(
                                    range(start_number, end_number + 1)
                                )
                            else:
                                selected_numbers.add(int(part))

                        if not selected_numbers:
                            raise ValueError

                        if any(
                            number < 1 or number > len(line_refs)
                            for number in selected_numbers
                        ):
                            raise ValueError

                    except ValueError:
                        print(
                            "Ungültige Auswahl. "
                            "Beispiele: 3 oder 3,7-10"
                        )
                        continue

                    selected_refs = [
                        line_refs[number - 1]
                        for number in sorted(selected_numbers)
                    ]

                    print()
                    print(
                        f"{len(selected_refs)} Buslinie(n) ausgewählt:"
                    )

                    for ref in selected_refs:
                        route_item = bus_lines[ref]
                        print(
                            f"  {route_item.name} | "
                            f"{len(route_item.data)} Relation(en)"
                        )

                    print()
                    print("Erzeuge GPX-Dateien...")

                    for ref in selected_refs:
                        output_files = route_plugin.generate(
                            bus_lines[ref],
                            force=getattr(args, "force", False),
                        )

                        if isinstance(output_files, (list, tuple)):
                            for output_file in output_files:
                                print(f"  Erzeugt: {output_file}")
                        else:
                            print(f"  Erzeugt: {output_files}")

                    return

                if back_to_region:
                    continue

        if args.type != "hiking":
            raise ValueError(
                f"Unbekannter Routentyp: {args.type}"
            )

        if not args.region:
            raise ValueError(
                "Für route hiking muss --region angegeben werden."
            )

        while True:
            back_to_region = False

            region = select_region_interactive()

            bbox = (
                region["min_lat"],
                region["min_lon"],
                region["max_lat"],
                region["max_lon"],
            )

            routes = route_plugin.discover(
                bbox,
                force=getattr(args, "force", False),
            )

            print()
            print(
                f"Gefundene Wanderrouten: {len(routes)}"
            )
            category_order = {
                "Fernwanderweg": 0,
                "Hauptwanderweg": 1,
                "Regionaler Wanderweg": 2,
                "Pilgerweg": 3,
                "Etappe": 4,
                "Teil-Etappe": 5,
                "Zuweg": 6,
                "Lokaler Wanderweg": 7,
                "Unbekannt": 8,
            }

            routes.sort(
                key=lambda route: (
                    category_order.get(route.data["category"], 99),
                    route.name.lower(),
                )
            )

            print()
            print(
                f"{'Nr':>3} | "
                f"{'Kategorie':<22} | "
                f"{'Ref':<16} | "
                f"{'Länge':<8} | "
                f"Name"
            )
            print("-" * 110)

            for number, route in enumerate(routes, 1):
                print(
                    f"{number:>3} | "
                    f"{route.data['category']:<22} | "
                    f"{route.data['ref']:<16} | "
                    f"{route.data['distance']:<8} | "
                    f"{route.name}"
                )

            while True:
                value = input(
                    "\nAuswahl (z.B. 33 oder 33,64-67, b zurück, q zum Abbrechen): "
                ).strip().lower()

                if value == "q":
                    print("Auswahl abgebrochen.")
                    return

                if value == "b":
                    print("Zurück zur Regionenauswahl.")
                    back_to_region = True
                    break

                selected_numbers = set()

                try:
                    for part in value.split(","):
                        part = part.strip()

                        if "-" in part:
                            start_number, end_number = part.split("-", 1)
                            start_number = int(start_number)
                            end_number = int(end_number)

                            if start_number > end_number:
                                raise ValueError

                            selected_numbers.update(
                                range(start_number, end_number + 1)
                            )
                        else:
                            selected_numbers.add(int(part))

                    if not selected_numbers:
                        raise ValueError

                    if any(
                        number < 1 or number > len(routes)
                        for number in selected_numbers
                    ):
                        raise ValueError

                except ValueError:
                    print(
                        "Ungültige Auswahl. "
                        "Beispiele: 33 oder 33,64-67"
                    )
                    continue

                selected_routes = [
                    routes[number - 1]
                    for number in sorted(selected_numbers)
                ]

                print()
                print(
                    f"{len(selected_routes)} Route(n) ausgewählt:"
                )

                for route in selected_routes:
                    print(
                        f"  {route.id} | "
                        f"{route.data['ref']} | "
                        f"{route.name}"
                    )

                print()
                print("Erzeuge GPX-Dateien...")

                for route in selected_routes:
                    output_file = route_plugin.generate(
                        route,
                        force=getattr(args, "force", False),
                    )
                    print(f"  Erzeugt: {output_file}")

                return

            if back_to_region:
                continue

    if args.command != "poi":
        raise ValueError(
            f"Unbekannter Befehl: {args.command}"
        )

    if args.region:
        region = select_region_interactive()

        min_lat = region["min_lat"]
        min_lon = region["min_lon"]
        max_lat = region["max_lat"]
        max_lon = region["max_lon"]

    else:
        min_lat, min_lon, max_lat, max_lon = parse_bbox(
            args.bbox
        )

    tiles = create_tiles(
        min_lat,
        min_lon,
        max_lat,
        max_lon,
    )

    print(
        f"{len(tiles)} Kacheln erzeugt."
    )

    elements = collect_osm_data(
        tiles,
        force=args.force,
    )

    print(
        f"\nInsgesamt {len(elements)} "
        "OSM-Objekte aus Cache/Overpass."
    )

    poi_types = [
        poi_type.strip()
        for poi_type in args.type.split(",")
        if poi_type.strip()
    ]

    if not poi_types:
        raise ValueError(
            "Mindestens ein POI-Typ muss angegeben werden."
        )

    region_name = None

    if args.region:
        region_name = region["name"]

    poi_config = load_poi_config()
    poi_type_configs = poi_config["poi_types"]

    for poi_type in poi_types:
        poi_name = poi_type_configs[poi_type]["name"]

        if region_name:
            output_name = (
                f"{poi_name}_{region_name}.gpx"
            )
        else:
            output_name = f"{poi_name}.gpx"

        generate_poi_gpx(
            elements,
            poi_type,
            output_name,
        )


if __name__ == "__main__":
    main()
