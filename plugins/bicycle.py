"""Auswahl ausgewiesener OSM-Fahrradrouten.

Der erste Ausbauschritt beschränkt sich bewusst auf die Suche und
Auswahl von ``type=route``-Relationen mit ``route=bicycle``. Caching,
GPX-Erzeugung und das Zusammenführen von Routenfamilien folgen erst
nach einer getrennten Prüfung.
"""

import argparse
import json
import sys
import time
from pathlib import Path


PLUGIN_NAME = "bicycle"
PLUGIN_DESCRIPTION = "Fahrradrouten"
PLUGIN_COMMAND = "route"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import load_config
from core.overpass import query_overpass_adaptive, query_overpass_retry
from core.routes import RouteItem


ROUTE_CACHE = PROJECT_ROOT / "cache" / "routes"


def route_category(tags: dict) -> str:
    """Ordnet eine Route ausschließlich ihrer OSM-Netzebene zu."""
    categories = {
        "icn": "Internationale Fahrradrouten",
        "ncn": "Nationale Fahrradrouten",
        "rcn": "Regionale Fahrradrouten",
        "lcn": "Lokale Fahrradrouten",
    }

    return categories.get(
        tags.get("network", ""),
        "Nicht klassifizierte Fahrradrouten",
    )


def find_bicycle_routes(
    bbox: tuple[float, float, float, float],
    force: bool = False,
) -> list[dict]:
    """Lädt ausgewiesene OSM-Fahrradrouten im Gebiet mit BBOX-Cache."""
    south, west, north, east = bbox
    config = load_config()

    ROUTE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = ROUTE_CACHE / (
        f"bicycle_{south:.6f}_{west:.6f}_{north:.6f}_{east:.6f}.json"
    )
    ttl_seconds = config["cache"]["bicycle_routes_ttl_days"] * 24 * 60 * 60

    if cache_path.exists() and not force:
        age_seconds = time.time() - cache_path.stat().st_mtime

        if age_seconds < ttl_seconds:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"Fahrradrouten aus Cache geladen: {cache_path.name}")
            return data["routes"]

    areas = [
        {
            "min_lat": south,
            "min_lon": west,
            "max_lat": north,
            "max_lon": east,
        }
    ]

    successful_groups, failed_areas = query_overpass_adaptive(
        areas,
        lambda min_lat, min_lon, max_lat, max_lon: f"""
[out:json][timeout:{config["overpass"]["query_timeout"]}];
relation
  ["type"="route"]
  ["route"="bicycle"]
  ({min_lat},{min_lon},{max_lat},{max_lon});
out tags;
""",
    )

    if failed_areas:
        raise RuntimeError(
            "Fahrradrouten konnten nicht vollständig von Overpass geladen werden."
        )

    routes = []

    for _, data in successful_groups:
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            routes.append(
                {
                    "relation_id": element["id"],
                    "name": tags.get("name", ""),
                    "ref": tags.get("ref", ""),
                    "network": tags.get("network", ""),
                    "category": route_category(tags),
                    "distance": tags.get("distance", ""),
                    "operator": tags.get("operator", ""),
                    "wikidata": tags.get("wikidata", ""),
                    "wikipedia": tags.get("wikipedia", ""),
                }
            )

    routes.sort(
        key=lambda route: (
            route["name"].lower(),
            route["relation_id"],
        )
    )

    cache_path.write_text(
        json.dumps({"routes": routes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Fahrradrouten im Cache gespeichert: {cache_path.name}")

    return routes


def discover(bbox: tuple[float, float, float, float], force: bool = False) -> list[RouteItem]:
    """Stellt Fahrradrouten über die gemeinsame Plugin-Schnittstelle bereit."""
    return [
        RouteItem(
            id=str(route["relation_id"]),
            name=route["name"] or str(route["relation_id"]),
            description=route["category"],
            data=route,
        )
        for route in find_bicycle_routes(bbox, force=force)
    ]


def load_relation(
    relation_id: int,
    force: bool = False,
) -> tuple[dict, dict[int, dict], dict[int, dict]]:
    """Lädt eine ausgewählte Relation samt Ways und Nodes mit Cache."""
    ROUTE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = ROUTE_CACHE / f"bicycle_relation_{relation_id}.json"

    if cache_path.exists() and not force:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"Relations-Cache geladen: {cache_path.name}")
    else:
        config = load_config()
        query = f"""
[out:json][timeout:{config["overpass"]["query_timeout"]}];
relation({relation_id});
(._;>;);
out body;
"""
        data = query_overpass_retry(query)
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Relations-Cache gespeichert: {cache_path.name}")

    elements = data.get("elements", [])

    relation = next(
        (
            element
            for element in elements
            if element.get("type") == "relation"
            and element.get("id") == relation_id
        ),
        None,
    )
    if relation is None:
        raise ValueError(f"Relation {relation_id} wurde nicht geladen.")

    ways = {
        element["id"]: element
        for element in elements
        if element.get("type") == "way"
    }
    nodes = {
        element["id"]: element
        for element in elements
        if element.get("type") == "node"
    }

    return relation, ways, nodes


def geometry_summary(relation: dict, ways: dict[int, dict]) -> dict:
    """Ermittelt eine konservative Übersicht der Relationsgeometrie."""
    node_degree: dict[int, int] = {}
    way_count = 0

    for member in relation.get("members", []):
        if member.get("type") != "way":
            continue

        way = ways.get(member.get("ref"))
        if way is None:
            continue

        way_count += 1
        way_nodes = way.get("nodes", [])

        for index in range(len(way_nodes) - 1):
            node_a = way_nodes[index]
            node_b = way_nodes[index + 1]

            if node_a == node_b:
                continue

            node_degree[node_a] = node_degree.get(node_a, 0) + 1
            node_degree[node_b] = node_degree.get(node_b, 0) + 1

    remaining_nodes = set(node_degree)
    components = 0

    adjacency: dict[int, set[int]] = {}
    for member in relation.get("members", []):
        if member.get("type") != "way":
            continue

        way = ways.get(member.get("ref"))
        if way is None:
            continue

        way_nodes = way.get("nodes", [])
        for index in range(len(way_nodes) - 1):
            node_a = way_nodes[index]
            node_b = way_nodes[index + 1]
            if node_a != node_b:
                adjacency.setdefault(node_a, set()).add(node_b)
                adjacency.setdefault(node_b, set()).add(node_a)

    while remaining_nodes:
        components += 1
        stack = [remaining_nodes.pop()]

        while stack:
            node_id = stack.pop()
            for neighbor in adjacency.get(node_id, set()):
                if neighbor in remaining_nodes:
                    remaining_nodes.remove(neighbor)
                    stack.append(neighbor)

    return {
        "ways": way_count,
        "nodes": len(node_degree),
        "components": components,
        "endpoints": sorted(
            node_id
            for node_id, degree in node_degree.items()
            if degree == 1
        ),
    }


def inspect_selected_route(route_item: RouteItem, force: bool = False) -> None:
    """Lädt und berichtet die Geometrie einer ausgewählten Fahrradroute."""
    relation_id = int(route_item.id)
    print(f"\nLade Relation {relation_id} ...")
    relation, ways, nodes = load_relation(relation_id, force=force)
    summary = geometry_summary(relation, ways)

    print(f"  Ways in Relation: {summary['ways']}")
    print(f"  Geometrie-Nodes: {summary['nodes']}")
    print(f"  Zusammenhangskomponenten: {summary['components']}")
    print(f"  Geometrische Endpunkte: {len(summary['endpoints'])}")
    print(f"  Geladene Nodes: {len(nodes)}")


def sort_items(items: list[RouteItem]) -> list[RouteItem]:
    category_order = {
        "Internationale Fahrradrouten": 0,
        "Nationale Fahrradrouten": 1,
        "Regionale Fahrradrouten": 2,
        "Lokale Fahrradrouten": 3,
        "Nicht klassifizierte Fahrradrouten": 4,
    }

    return sorted(
        items,
        key=lambda item: (
            category_order.get(item.data["category"], 99),
            item.name.lower(),
        ),
    )


def print_items(items: list[RouteItem]) -> None:
    print()
    print(f"Gefundene Fahrradrouten: {len(items)}")
    print()
    print(f'{"Nr":>3} | {"Kategorie":<34} | {"Ref":<16} | Name')
    print("-" * 105)

    for number, item in enumerate(items, 1):
        print(
            f'{number:>3} | '
            f'{item.data["category"]:<34} | '
            f'{item.data["ref"]:<16} | '
            f'{item.name}'
        )


def print_selected(items: list[RouteItem]) -> None:
    print()
    print(f"{len(items)} Fahrradroute(n) ausgewählt:")

    for item in items:
        print(
            f'  {item.id} | '
            f'{item.data["ref"]} | '
            f'{item.name}'
        )


def get_selection_prompt() -> str:
    return "\nAuswahl (z.B. 3 oder 3,7-10, b zurück, q zum Abbrechen): "


def configure_cli(parser: argparse.ArgumentParser) -> None:
    """Registriert die gemeinsame Routen-Auswahl."""
    parser.add_argument(
        "--region",
        action="store_true",
        required=True,
        help="Arbeitsregion interaktiv auswählen",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Für Kompatibilität mit weiteren Routen-Plugins reserviert",
    )


def run_cli(args: argparse.Namespace) -> None:
    """Führt ausschließlich Suche, Sortierung und Auswahl aus."""
    from core.cli import select_region_interactive

    while True:
        region = select_region_interactive()
        bbox = (
            region["min_lat"],
            region["min_lon"],
            region["max_lat"],
            region["max_lon"],
        )

        route_items = discover(bbox, force=args.force)

        for route_item in route_items:
            route_item.region = region

        route_items = sort_items(route_items)
        print_items(route_items)

        while True:
            value = input(get_selection_prompt()).strip().lower()

            if value == "q":
                print("Auswahl abgebrochen.")
                return
            if value == "b":
                print("Zurück zur Regionenauswahl.")
                break

            try:
                selected_numbers = set()
                for part in value.split(","):
                    if "-" in part:
                        start, end = (
                            int(number)
                            for number in part.split("-", 1)
                        )
                        if start > end:
                            raise ValueError
                        selected_numbers.update(range(start, end + 1))
                    else:
                        selected_numbers.add(int(part))

                if not selected_numbers or any(
                    number < 1 or number > len(route_items)
                    for number in selected_numbers
                ):
                    raise ValueError
            except ValueError:
                print("Ungültige Auswahl. Bitte das angegebene Format verwenden.")
                continue

            selected_items = [
                route_items[number - 1]
                for number in sorted(selected_numbers)
            ]
            print_selected(selected_items)

            for route_item in selected_items:
                inspect_selected_route(route_item, force=args.force)

            print("\nGPX-Erzeugung folgt in einem späteren Ausbauschritt.")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=PLUGIN_DESCRIPTION)
    configure_cli(parser)
    run_cli(parser.parse_args())


if __name__ == "__main__":
    main()
