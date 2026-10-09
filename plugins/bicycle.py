"""Auswahl ausgewiesener OSM-Fahrradrouten.

Der erste Ausbauschritt beschränkt sich bewusst auf die Suche und
Auswahl von ``type=route``-Relationen mit ``route=bicycle``. Caching,
GPX-Erzeugung und das Zusammenführen von Routenfamilien folgen erst
nach einer getrennten Prüfung.
"""

import argparse
import re
import sys
from pathlib import Path


PLUGIN_NAME = "bicycle"
PLUGIN_DESCRIPTION = "Fahrradrouten"
PLUGIN_COMMAND = "route"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.routes import RouteItem, find_route_relations, load_route_relation
from core.gpx import add_metadata, add_track, create_gpx, save_gpx
from plugins.hiking import build_main_track_from_graph


GPX_OUTPUT = PROJECT_ROOT / "output" / "gpx" / "routes" / "bicycle"


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
    """Filtert Fahrradrouten aus dem gemeinsamen Routenindex."""

    routes = []

    for element in find_route_relations(bbox, "bicycle", force=force):
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
    relation, ways, nodes = load_route_relation(relation_id, force=force)
    summary = geometry_summary(relation, ways)

    print(f"  Ways in Relation: {summary['ways']}")
    print(f"  Geometrie-Nodes: {summary['nodes']}")
    print(f"  Zusammenhangskomponenten: {summary['components']}")
    print(f"  Geometrische Endpunkte: {len(summary['endpoints'])}")
    print(f"  Geladene Nodes: {len(nodes)}")


def clean_filename(value: str) -> str:
    """Erzeugt einen plattformunabhängig sicheren GPX-Dateinamen."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "Fahrradroute"


def generate_bicycle_gpx(
    relation_id: int,
    force: bool = False,
) -> Path:
    """Erzeugt eine GPX-Datei aus einer ausgewählten Fahrradrelation."""
    relation, ways, nodes = load_route_relation(relation_id, force=force)
    tags = relation.get("tags", {})
    name = tags.get("name") or tags.get("ref") or f"Relation {relation_id}"
    ref = tags.get("ref")

    tracks = build_main_track_from_graph(relation, ways)
    point_tracks = []

    for track_nodes in tracks:
        points = [
            (nodes[node_id]["lat"], nodes[node_id]["lon"])
            for node_id in track_nodes
            if node_id in nodes
        ]
        if len(points) >= 2:
            point_tracks.append(points)

    if not point_tracks:
        raise ValueError(
            f"Relation {relation_id} enthält keine exportierbare Geometrie."
        )

    description_parts = []
    operator = tags.get("operator")
    distance = tags.get("distance")
    network = tags.get("network")

    if operator:
        description_parts.append(f"Betreiber: {operator}")
    if ref:
        description_parts.append(f"Ref: {ref}")
    if distance:
        description_parts.append(f"Länge: {distance} km")
    if network:
        network_names = {
            "icn": "International",
            "ncn": "National",
            "rcn": "Regional",
            "lcn": "Lokal",
        }
        description_parts.append(
            f"Netzwerk: {network_names.get(network, network)}"
        )

    description = "<br />".join(description_parts) or None
    gpx = create_gpx()
    add_metadata(gpx, name=name, description=description, author=operator)

    for index, points in enumerate(point_tracks, start=1):
        track_name = name if len(point_tracks) == 1 else f"{name} – Abschnitt {index}"
        add_track(
            gpx,
            points,
            name=track_name,
            description=description,
            track_type="Cycling",
        )

    output_file = GPX_OUTPUT / f"{clean_filename(name)}_{relation_id}.gpx"
    save_gpx(gpx, output_file)
    return output_file


def generate(route_item: RouteItem, force: bool = False) -> Path:
    """Erzeugt die GPX-Datei für ein einheitliches RouteItem."""
    return generate_bicycle_gpx(int(route_item.id), force=force)


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
    print(
        f'{"Nr":>3} | '
        f'{"Kategorie":<34} | '
        f'{"Ref":<16} | '
        f'{"Länge":<8} | '
        "Name"
    )
    print("-" * 116)

    for number, item in enumerate(items, 1):
        print(
            f'{number:>3} | '
            f'{item.data["category"]:<34} | '
            f'{item.data["ref"]:<16} | '
            f'{item.data["distance"]:<8} | '
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
                output_file = generate(route_item, force=args.force)
                print(f"  Erzeugt: {output_file}")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=PLUGIN_DESCRIPTION)
    configure_cli(parser)
    run_cli(parser.parse_args())


if __name__ == "__main__":
    main()
