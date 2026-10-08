from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.gpx import add_metadata, add_track, create_gpx, save_gpx
from core.overpass import query_overpass_retry


PLUGIN_NAME = "bus"
PLUGIN_DESCRIPTION = "Buslinien"
PLUGIN_COMMAND = "route"

BUS_CACHE = PROJECT_ROOT / "cache" / "transit"
GPX_OUTPUT = PROJECT_ROOT / "output" / "gpx" / "routes" / "bus"


def clean_filename(name: str) -> str:
    """Sicheren Dateinamen erzeugen."""

    result = []

    for char in str(name):
        if char.isalnum() or char in "._-":
            result.append(char)
        else:
            result.append("_")

    return "".join(result).strip("_")


def _bbox_cache_name(
    prefix: str,
    bbox: tuple[float, float, float, float],
) -> str:
    """Einen eindeutigen Cache-Dateinamen aus einer BBOX erzeugen."""

    south, west, north, east = bbox

    return (
        f"{prefix}_"
        f"{south:.6f}_"
        f"{west:.6f}_"
        f"{north:.6f}_"
        f"{east:.6f}.json"
    )


def discover_bus_lines(
    bbox: tuple[float, float, float, float],
    force: bool = False,
) -> dict:
    """
    Buslinien innerhalb einer BBOX ermitteln.

    Die Linien werden nach ref gruppiert.

    Der Discovery-Cache ist BBOX-spezifisch.
    """

    BUS_CACHE.mkdir(parents=True, exist_ok=True)

    discovery_cache = BUS_CACHE / _bbox_cache_name(
        "bus_discovery",
        bbox,
    )

    if discovery_cache.exists() and not force:
        print()
        print("Discovery-Cache vorhanden:")
        print(f"  {discovery_cache}")
        print("  Keine Overpass-Abfrage erforderlich.")

        with discovery_cache.open(
            "r",
            encoding="utf-8",
        ) as file:
            lines = json.load(file)

        print()
        print(f"Buslinien gefunden: {len(lines)}")

        return lines

    south, west, north, east = bbox

    query = f"""
[out:json][timeout:60];

relation["type"="route"]["route"="bus"](
    {south},
    {west},
    {north},
    {east}
);

out tags;
"""

    print()
    print("==============================================")
    print("Buslinien suchen")
    print("==============================================")
    print()
    print("Buslinien werden von Overpass ermittelt.")

    try:
        data = query_overpass_retry(query)

    except Exception as error:
        print()
        print("  Overpass-Liniensuche fehlgeschlagen.")
        print(f"  {error}")
        print()
        print("  Suche vorhandene OSM-JSON-Dateien ...")

        lines = {}

        for json_filename in sorted(
            BUS_CACHE.glob("linie_*_osm.json")
        ):
            try:
                with json_filename.open(
                    "r",
                    encoding="utf-8",
                ) as file:
                    cached_data = json.load(file)

            except Exception as cache_error:
                print(
                    f"  WARNUNG: {json_filename} "
                    f"konnte nicht gelesen werden: {cache_error}"
                )
                continue

            for element in cached_data.get("elements", []):
                if element.get("type") != "relation":
                    continue

                relation_id = element.get("id")
                tags = element.get("tags", {})
                ref = tags.get("ref")

                if relation_id is None or not ref:
                    continue

                ref = ref.strip()

                if not ref:
                    continue

                lines.setdefault(ref, []).append(
                    {
                        "id": relation_id,
                        "tags": tags,
                    }
                )

        if not lines:
            raise

        print()
        print(
            f"  Aus vorhandenen OSM-JSON-Dateien "
            f"gefunden: {len(lines)} Buslinien"
        )

        return lines

    lines = {}

    for element in data.get("elements", []):
        if element.get("type") != "relation":
            continue

        relation_id = element.get("id")
        tags = element.get("tags", {})

        ref = tags.get("ref")

        if relation_id is None or not ref:
            continue

        ref = ref.strip()

        if not ref:
            continue

        lines.setdefault(ref, []).append(
            {
                "id": relation_id,
                "tags": tags,
            }
        )

    with discovery_cache.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            lines,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print("Discovery-Cache gespeichert:")
    print(f"  {discovery_cache}")

    print()
    print(f"Buslinien gefunden: {len(lines)}")

    return lines


def parse_line_data(data: dict) -> tuple[dict, dict, dict]:
    """Overpass-Daten in Relationen, Ways und Nodes aufteilen."""

    relations = {}
    ways = {}
    nodes = {}

    for element in data.get("elements", []):
        element_type = element.get("type")
        element_id = element.get("id")

        if element_type == "relation":
            relations[element_id] = element

        elif element_type == "way":
            ways[element_id] = element

        elif element_type == "node":
            nodes[element_id] = element

    print()
    print(f"  Relationen: {len(relations)}")
    print(f"  Ways:       {len(ways)}")
    print(f"  Nodes:      {len(nodes)}")

    return relations, ways, nodes


def load_line_data(
    relation_ids: list[int],
    cache_path: Path,
    force: bool = False,
) -> tuple[dict, dict, dict]:
    """Daten einer oder mehrerer Busrelationen laden."""

    if cache_path.exists() and not force:
        print()
        print("  OSM-JSON vorhanden - keine Overpass-Abfrage:")
        print(f"  {cache_path}")

        with cache_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return parse_line_data(data)

    id_list = ",".join(
        str(relation_id)
        for relation_id in relation_ids
    )

    query = f"""
[out:json][timeout:90];

relation(id:{id_list});

out body;

>;

out body;
"""

    print()
    print(
        f"  Lade OSM-Daten für "
        f"{len(relation_ids)} Relationen."
    )

    data = query_overpass_retry(query)

    cache_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("  OSM-JSON gespeichert:")
    print(f"  {cache_path}")

    return parse_line_data(data)


def build_relation_tracks(
    relation: dict,
    ways: dict,
    nodes: dict,
) -> list[list[dict]]:
    """
    Baut einen Track aus den Way-Mitgliedern einer OSM-Relation.

    Als mögliche Haltestellen werden die Rollen
    stop, forward_stop und backward_stop berücksichtigt.

    Es werden nur Haltestellen verwendet, deren Node tatsächlich
    Bestandteil der Way-Geometrie ist.

    Der eigentliche Routenverlauf wird anschließend als Graph
    zwischen dem ersten und letzten geeigneten Haltestellen-Node
    gesucht.
    """

    members = relation.get("members", [])

    member_way_ids = []

    for member in members:
        if member.get("type") != "way":
            continue

        if member.get("role", "") != "":
            continue

        way_id = member.get("ref")

        if way_id not in ways:
            continue

        member_way_ids.append(way_id)

    if not member_way_ids:
        return []

    geometry_nodes = set()

    for way_id in member_way_ids:
        way_nodes = ways[way_id].get("nodes", [])
        geometry_nodes.update(way_nodes)

    stop_nodes = []

    for member in members:
        if member.get("type") != "node":
            continue

        role = member.get("role", "")

        if role not in (
            "stop",
            "forward_stop",
            "backward_stop",
        ):
            continue

        node_id = member.get("ref")

        if node_id not in nodes:
            continue

        if node_id not in geometry_nodes:
            continue

        if node_id in stop_nodes:
            continue

        stop_nodes.append(node_id)

    if len(stop_nodes) < 2:
        return []

    start_node = stop_nodes[0]
    target_node = stop_nodes[-1]

    graph = {}

    for way_id in member_way_ids:
        way = ways[way_id]
        way_nodes = way.get("nodes", [])

        for node_a, node_b in zip(
            way_nodes,
            way_nodes[1:],
        ):
            graph.setdefault(
                node_a,
                set(),
            ).add(node_b)

            graph.setdefault(
                node_b,
                set(),
            ).add(node_a)

    if start_node not in graph:
        return []

    queue = [start_node]

    previous = {
        start_node: None
    }

    queue_index = 0

    while queue_index < len(queue):
        current = queue[queue_index]
        queue_index += 1

        if current == target_node:
            break

        for neighbour in graph.get(
            current,
            set(),
        ):
            if neighbour in previous:
                continue

            previous[neighbour] = current
            queue.append(neighbour)

    if target_node not in previous:
        print(
            f"    WARNUNG: Kein Pfad zwischen "
            f"Stop {start_node} und Stop {target_node}."
        )

        return []

    path = []

    current = target_node

    while current is not None:
        path.append(current)
        current = previous[current]

    path.reverse()

    route = []

    for node_id in path:
        node = nodes.get(node_id)

        if node is None:
            continue

        lat = node.get("lat")
        lon = node.get("lon")

        if lat is None or lon is None:
            continue

        route.append(
            {
                "id": node_id,
                "lat": lat,
                "lon": lon,
            }
        )

    if len(route) < 2:
        return []

    return [route]


def generate_bus_gpx(
    ref: str,
    discovered_relations: list[dict],
    region=None,
    force: bool = False,
) -> Path | None:
    """
    Eine komplette Buslinie als GPX erzeugen.

    Jede OSM-Relation der Linie wird als eigener Track übernommen.
    """

    BUS_CACHE.mkdir(
        parents=True,
        exist_ok=True,
    )

    GPX_OUTPUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    if region is not None:
        from core.regions import get_region_path

        region_path = get_region_path(region["id"])
        output_dir = GPX_OUTPUT

        for path_region in region_path:
            output_dir = output_dir / clean_filename(path_region["name"])
    else:
        output_dir = GPX_OUTPUT

    output_file = (
        output_dir
        / f"{clean_filename(ref)}.gpx"
    )

    if output_file.exists() and not force:
        print()
        print(f"Linie {ref}")
        print("  GPX existiert bereits - übersprungen.")
        print(f"  {output_file}")

        return output_file

    print()
    print("----------------------------------------------")
    print(f"Linie {ref}")
    print(
        f"  Relationen entdeckt: "
        f"{len(discovered_relations)}"
    )

    relation_ids = [
        item["id"]
        for item in discovered_relations
    ]

    cache_path = (
        BUS_CACHE
        / (
            "bus_line_"
            + clean_filename(ref)
            + ".json"
        )
    )

    try:
        relations, ways, nodes = load_line_data(
            relation_ids,
            cache_path,
            force=force,
        )

    except Exception as error:
        print()
        print(
            f"  FEHLER: Linie {ref} "
            "konnte nicht geladen werden."
        )
        print(f"  {error}")
        print("  Linie wird übersprungen.")

        return None

    tracks = []

    for relation_info in discovered_relations:
        relation_id = relation_info["id"]

        relation = relations.get(
            relation_id
        )

        if relation is None:
            print(
                f"  WARNUNG: Relation "
                f"{relation_id} fehlt."
            )
            continue

        tags = relation.get(
            "tags",
            {},
        )

        relation_name = (
            tags.get("name")
            or tags.get("description")
            or f"Relation {relation_id}"
        )

        print()
        print(
            f"  Relation {relation_id}:"
        )
        print(
            f"    {relation_name}"
        )

        components = build_relation_tracks(
            relation,
            ways,
            nodes,
        )

        if not components:
            print(
                "    WARNUNG: keine Geometrie gefunden"
            )
            continue

        if len(components) > 1:
            print(
                f"    WARNUNG: {len(components)} "
                "getrennte Komponenten gefunden"
            )

        for component_index, points in enumerate(
            components,
            1,
        ):
            if len(components) == 1:
                track_name = relation_name
            else:
                track_name = (
                    f"{relation_name} "
                    f"(Abschnitt {component_index})"
                )

            track_points = [
                (
                    point["lat"],
                    point["lon"],
                )
                for point in points
            ]

            tracks.append(
                (
                    track_name,
                    track_points,
                )
            )

            print(
                f"    Track: {track_name}"
            )
            print(
                f"      Punkte: {len(points)}"
            )

    if not tracks:
        print()
        print(
            f"  FEHLER: Linie {ref} "
            "enthält keine verwendbaren Tracks."
        )

        return None

    gpx = create_gpx()

    add_metadata(
        gpx,
        name=f"Buslinie {ref}",
        description=f"OSM-Buslinie {ref}",
    )

    for track_name, points in tracks:
        add_track(
            gpx,
            points,
            name=track_name,
            track_type="Bus",
        )

    save_gpx(
        gpx,
        output_file,
    )

    print()
    print(
        f"  => gespeichert: {output_file}"
    )
    print(
        f"  => {len(tracks)} Tracks"
    )

    return output_file


def discover(bbox, region=None, force=False):
    """Routen für die gemeinsame Plugin-Schnittstelle entdecken."""
    from core.routes import RouteItem

    bus_lines = discover_bus_lines(bbox, force=force)

    return [
        RouteItem(
            id=str(ref),
            name=str(ref),
            description=f"{len(relations)} Relation(en)",
            data=relations,
            region=region,
        )
        for ref, relations in sorted(
            bus_lines.items(),
            key=lambda item: item[0].lower(),
        )
    ]


def generate(route_item, force=False):
    """RouteItem über die bestehende Bus-GPX-Erzeugung verarbeiten."""
    return generate_bus_gpx(
        route_item.name,
        route_item.data,
        region=route_item.region,
        force=force,
    )


def sort_items(items):
    return sorted(
        items,
        key=lambda item: item.name.lower(),
    )


def print_items(items):
    print()
    print(f"Gefundene Buslinien: {len(items)}")
    print()
    print(f"{"Nr":>3} | Linie")
    print("-" * 50)

    for number, item in enumerate(items, 1):
        print(
            f"{number:>3} | "
            f"{item.name:<16} | "
            f"{len(item.data)} Relation(en)"
        )


def print_selected(items):
    print()
    print(f"{len(items)} Buslinie(n) ausgewählt:")

    for item in items:
        print(
            f"  {item.name} | "
            f"{len(item.data)} Relation(en)"
        )


def get_selection_prompt():
    return "\nAuswahl (z.B. 3 oder 3,7-10, b zurück, q zum Abbrechen): "

def configure_cli(parser: argparse.ArgumentParser) -> None:
    """Registriert die Argumente des Routen-Plugins."""
    parser.add_argument(
        "--region",
        action="store_true",
        required=True,
        help="Arbeitsregion interaktiv auswählen",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Cache ignorieren und Overpass erneut abfragen",
    )


def run_cli(args: argparse.Namespace) -> None:
    """Führt die vollständige, eigenständige Routen-Auswahl aus."""
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
                        start, end = (int(number) for number in part.split("-", 1))
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
                route_items[number - 1] for number in sorted(selected_numbers)
            ]
            print_selected(selected_items)
            print("\nErzeuge GPX-Dateien...")

            for route_item in selected_items:
                output_files = generate(route_item, force=args.force)
                for output_file in (
                    output_files
                    if isinstance(output_files, (list, tuple))
                    else [output_files]
                ):
                    print(f"  Erzeugt: {output_file}")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=PLUGIN_DESCRIPTION)
    configure_cli(parser)
    run_cli(parser.parse_args())


if __name__ == "__main__":
    main()
