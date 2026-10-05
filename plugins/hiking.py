import json
import sys
import time
from pathlib import Path

PLUGIN_NAME = "hiking"
PLUGIN_DESCRIPTION = "Wanderrouten"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import load_config
from core.gpx import create_gpx, add_metadata, add_track, save_gpx
from core.overpass import query_overpass_adaptive
from core.wikipedia import get_wikipedia_info
ROUTE_CACHE = PROJECT_ROOT / "cache" / "routes"
GPX_OUTPUT = PROJECT_ROOT / "output" / "gpx" / "routes"


def route_category(tags):
    name = tags.get("name", "").lower()
    ref = tags.get("ref", "")
    network = tags.get("network", "")

    if "teil-etappe" in name:
        return "Teil-Etappe"

    if "etappe" in name:
        return "Etappe"

    if "zuweg" in name or "zuweg" in ref.lower():
        return "Zuweg"

    if ref.startswith("PiL") or "pilgern" in name:
        return "Pilgerweg"

    if network == "iwn":
        return "Fernwanderweg"

    if network == "nwn":
        return "Hauptwanderweg"

    if network == "rwn":
        return "Regionaler Wanderweg"

    if network == "lwn":
        return "Lokaler Wanderweg"

    return "Unbekannt"



def route_profile(relation: dict) -> dict:
    tags = relation.get("tags", {})

    return {
        "ref": tags.get("ref"),
        "network": tags.get("network"),
        "route": tags.get("route"),
        "operator": tags.get("operator"),
        "wikidata": tags.get("wikidata"),
        "wikipedia": tags.get("wikipedia"),
        "website": tags.get("website"),
        "osmc_symbol": tags.get("osmc:symbol"),
        "colour": tags.get("colour"),
        "name": tags.get("name"),
    }

def find_hiking_routes(bbox):
    import urllib.parse
    import urllib.request

    south, west, north, east = bbox

    ROUTE_CACHE.mkdir(parents=True, exist_ok=True)

    cache_name = (
        f"hiking_"
        f"{south:.6f}_"
        f"{west:.6f}_"
        f"{north:.6f}_"
        f"{east:.6f}.json"
    )
    cache_path = ROUTE_CACHE / cache_name

    config = load_config()
    ttl_days = config["cache"]["hiking_routes_ttl_days"]
    ttl_seconds = ttl_days * 24 * 60 * 60

    if cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime

        if age_seconds < ttl_seconds:
            data = json.loads(
                cache_path.read_text(encoding="utf-8")
            )
            print(
                f"Hiking-Routen aus Cache geladen: "
                f"{cache_path.name}"
            )
            return data["routes"]

    query = f"""
[out:json][timeout:{config["hiking"]["query_timeout"]}];
relation
  ["type"="route"]
  ["route"="hiking"]
  ({south},{west},{north},{east});
out tags;
"""

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
[out:json][timeout:{config["hiking"]["query_timeout"]}];
relation
  ["type"="route"]
  ["route"="hiking"]
  ({min_lat},{min_lon},{max_lat},{max_lon});
out tags;
""",
    )

    if failed_areas:
        raise RuntimeError(
            "Hiking-Routen konnten nicht von Overpass geladen werden."
        )

    data = {
        "elements": [
            element
            for _, group_data in successful_groups
            for element in group_data.get("elements", [])
        ]
    }

    routes = []

    for element in data.get("elements", []):
        tags = element.get("tags", {})

        routes.append({
            "relation_id": element["id"],
            "name": tags.get("name", ""),
            "ref": tags.get("ref", ""),
            "network": tags.get("network", ""),
            "category": route_category(tags),
            "distance": tags.get("distance", ""),
            "operator": tags.get("operator", ""),
            "wikidata": tags.get("wikidata", ""),
            "wikipedia": tags.get("wikipedia", ""),
        })

    routes.sort(
        key=lambda route: (
            route["name"].lower(),
            route["relation_id"],
        )
    )

    cache_path.write_text(
        json.dumps(
            {"routes": routes},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Hiking-Routen im Cache gespeichert: "
        f"{cache_path.name}"
    )

    return routes


def load_relation(
    relation_id: int,
) -> tuple[dict, dict[int, dict], dict[int, dict]]:
    path = ROUTE_CACHE / f"relation_{relation_id}.json"

    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        from core.overpass import query_overpass_retry

        print(
            f"Kein Relations-Cache für {relation_id} vorhanden. "
            f"Lade Relation von Overpass ..."
        )

        config = load_config()

        query = f"""
[out:json][timeout:{config["hiking"]["query_timeout"]}];
relation({relation_id});
(._;>;);
out body;
"""

        data = query_overpass_retry(query)

        ROUTE_CACHE.mkdir(parents=True, exist_ok=True)

        path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"Relations-Cache gespeichert: {path.name}"
        )

    relation = next(
        element
        for element in data["elements"]
        if element["type"] == "relation"
        and element["id"] == relation_id
    )

    ways = {
        element["id"]: element
        for element in data["elements"]
        if element["type"] == "way"
    }

    nodes = {
        element["id"]: element
        for element in data["elements"]
        if element["type"] == "node"
    }

    return relation, ways, nodes


def calculate_route_length_km(
    route_nodes: list[int],
    nodes: dict[int, dict],
) -> float:
    import math

    earth_radius_km = 6371.0088

    def haversine_km(
        point_a: tuple[float, float],
        point_b: tuple[float, float],
    ) -> float:
        lat1, lon1 = map(math.radians, point_a)
        lat2, lon2 = map(math.radians, point_b)

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        value = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1)
            * math.cos(lat2)
            * math.sin(dlon / 2) ** 2
        )

        return (
            2
            * earth_radius_km
            * math.asin(math.sqrt(value))
        )

    points = []

    for node_id in route_nodes:
        node = nodes.get(node_id)

        if node is None:
            raise ValueError(
                f"Node {node_id} fehlt im Relations-Cache."
            )

        points.append(
            (
                float(node["lat"]),
                float(node["lon"]),
            )
        )

    return sum(
        haversine_km(
            points[index],
            points[index + 1],
        )
        for index in range(len(points) - 1)
    )


def build_main_track_from_graph(
    relation: dict,
    ways: dict[int, dict],
) -> tuple[list[list[int]], dict[int, dict], int]:
    """
    Rekonstruiert komplexe Relationen als geometrische Tracks.

    Wird nur als Fallback für Relationen verwendet, bei denen
    build_route_nodes() nicht genau zwei End-Ways erkennen kann.

    Der längste zusammenhängende Track wird als Hauptstrecke
    zurückgegeben.
    """

    way_ids = []

    for member in relation["members"]:
        if member["type"] != "way":
            continue

        way_id = member["ref"]

        if way_id not in ways:
            continue

        if way_id not in way_ids:
            way_ids.append(way_id)

    if not way_ids:
        raise ValueError(
            "Relation enthält keine Ways."
        )

    edges = []
    adjacency = {}

    for way_id in way_ids:
        way_nodes = ways[way_id].get("nodes", [])

        for index in range(len(way_nodes) - 1):
            node_a = way_nodes[index]
            node_b = way_nodes[index + 1]

            if node_a == node_b:
                continue

            edge_id = len(edges)

            edges.append(
                (
                    node_a,
                    node_b,
                )
            )

            adjacency.setdefault(
                node_a,
                [],
            ).append(edge_id)

            adjacency.setdefault(
                node_b,
                [],
            ).append(edge_id)

    if not edges:
        raise ValueError(
            "Relation enthält keine geometrischen Kanten."
        )

    junctions = {
        node_id
        for node_id, connected_edges in adjacency.items()
        if len(connected_edges) != 2
    }

    visited = set()
    tracks = []

    def walk(start_node: int, start_edge: int) -> list[int]:
        track = [start_node]
        current_node = start_node
        current_edge = start_edge

        while current_edge not in visited:
            visited.add(current_edge)

            node_a, node_b = edges[current_edge]

            if node_a == current_node:
                next_node = node_b
            elif node_b == current_node:
                next_node = node_a
            else:
                raise ValueError(
                    f"Kante {current_edge} passt nicht "
                    f"zu Node {current_node}."
                )

            track.append(next_node)
            current_node = next_node

            if current_node in junctions:
                break

            next_edges = [
                edge_id
                for edge_id in adjacency[current_node]
                if edge_id not in visited
            ]

            if not next_edges:
                break

            if len(next_edges) != 1:
                raise ValueError(
                    f"Mehrdeutige Geometrie bei Node "
                    f"{current_node}: {next_edges}"
                )

            current_edge = next_edges[0]

        return track

    for node_id in adjacency:
        if node_id not in junctions:
            continue

        for edge_id in adjacency[node_id]:
            if edge_id in visited:
                continue

            track = walk(
                node_id,
                edge_id,
            )

            if len(track) >= 2:
                tracks.append(track)

    for edge_id in range(len(edges)):
        if edge_id in visited:
            continue

        node_a, node_b = edges[edge_id]

        track = walk(
            node_a,
            edge_id,
        )

        if len(track) >= 2:
            tracks.append(track)

    if not tracks:
        raise ValueError(
            "Keine Track-Segmente konnten rekonstruiert werden."
        )

    return tracks



def analyze_relation_connection(
    connection: dict,
    cache_dir: Path | None = None,
) -> dict:
    """
    Analysiert eine Verbindung zwischen zwei Relationen.

    Die Funktion bestimmt nur, ob der gemeinsame Node jeweils ein
    geometrischer Endpunkt der beiden Relationen ist.

    Eine absolute Richtung wird hier bewusst nicht festgelegt.
    """

    relation_a = connection["relation_a"]
    relation_b = connection["relation_b"]
    shared_nodes = connection.get("shared_nodes", [])

    if len(shared_nodes) != 1:
        raise ValueError(
            "Verbindung muss genau einen gemeinsamen Node haben: "
            f"{relation_a} <-> {relation_b}"
        )

    connection_node = shared_nodes[0]

    cache_dir = (
        ROUTE_CACHE
        if cache_dir is None
        else Path(cache_dir)
    )

    import json

    def load_relation_data(relation_id: int):
        cache_file = cache_dir / f"relation_{relation_id}.json"

        if not cache_file.exists():
            raise FileNotFoundError(
                f"Relations-Cache fehlt: {cache_file}"
            )

        data = json.loads(cache_file.read_text())
        elements = data.get("elements", [])

        relation = next(
            element
            for element in elements
            if element.get("type") == "relation"
            and element.get("id") == relation_id
        )

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

    def is_endpoint(relation_id: int) -> bool:
        relation, ways, nodes = load_relation_data(relation_id)

        endpoints = get_relation_endpoints(
            relation,
            ways,
            nodes,
        )

        return connection_node in endpoints

    endpoint_a = is_endpoint(relation_a)
    endpoint_b = is_endpoint(relation_b)

    return {
        "relation_a": relation_a,
        "relation_b": relation_b,
        "connection_node": connection_node,
        "relation_a_is_endpoint": endpoint_a,
        "relation_b_is_endpoint": endpoint_b,
    }

def build_route_connection_graph(
    family: list[dict],
    cache_dir: Path | None = None,
) -> list[dict]:
    """
    Baut einen Verbindungsgraphen aus gemeinsamen OSM-Nodes
    zwischen den Relations einer Routenfamilie.

    Jede Verbindung enthält:
      - relation_a
      - relation_b
      - shared_nodes

    Es werden ausschließlich bereits vorhandene Relations-Caches
    verwendet. Es erfolgt keine Overpass-Abfrage.
    """

    if cache_dir is None:
        cache_dir = (
            Path(__file__).resolve().parents[2]
            / "cache"
            / "routes"
        )

    relation_nodes = {}

    for route in family:
        relation_id = route["relation_id"]
        cache_file = cache_dir / f"relation_{relation_id}.json"

        if not cache_file.exists():
            continue

        data = json.loads(
            cache_file.read_text(encoding="utf-8")
        )

        node_ids = set()

        for element in data.get("elements", []):
            if element.get("type") == "way":
                node_ids.update(
                    element.get("nodes", [])
                )

        relation_nodes[relation_id] = node_ids

    relation_ids = list(relation_nodes)
    connections = []

    for index, relation_a in enumerate(relation_ids):
        for relation_b in relation_ids[index + 1:]:
            shared_nodes = (
                relation_nodes[relation_a]
                & relation_nodes[relation_b]
            )

            if shared_nodes:
                connections.append(
                    {
                        "relation_a": relation_a,
                        "relation_b": relation_b,
                        "shared_nodes": sorted(shared_nodes),
                    }
                )

    return connections



def get_relation_endpoints(
    relation: dict,
    ways: list[dict],
    nodes: dict[int, dict],
) -> list[int]:
    """
    Ermittelt die tatsächlichen geometrischen Endpunkte einer Relation.

    Die Ermittlung erfolgt über den vollständigen Node-Graphen der
    Relation-Ways und nicht nur über die ersten/letzten Nodes einzelner
    Ways.

    Erwartet:
      - relation: OSM-Relation
      - ways: Relation-Ways mit node-Listen
      - nodes: OSM-Nodes als {node_id: node}

    Rückgabe:
      Liste der geometrischen End-Node-IDs.

    Normalfall:
      genau zwei Endpunkte.

    Geschlossene Relation:
      leere Liste.

    Bei Verzweigungen:
      mehrere Endpunkte sind möglich.
    """

    if not ways:
        return []

    node_degree: dict[int, int] = {}

    way_list = ways.values() if isinstance(ways, dict) else ways

    for way in way_list:
        way_nodes = way.get("nodes", [])

        if len(way_nodes) < 2:
            continue

        for index in range(len(way_nodes) - 1):
            node_a = way_nodes[index]
            node_b = way_nodes[index + 1]

            if node_a == node_b:
                continue

            node_degree[node_a] = node_degree.get(node_a, 0) + 1
            node_degree[node_b] = node_degree.get(node_b, 0) + 1

    endpoints = sorted(
        node_id
        for node_id, degree in node_degree.items()
        if degree == 1
    )

    return endpoints



def classify_relation_connection(
    connection: dict,
    cache_dir: Path | None = None,
) -> dict:
    """
    Klassifiziert eine topologische Verbindung zweier Relationen.

    normal:
        Genau ein gemeinsamer Node, der Endpunkt beider Relationen ist.

    alternative:
        Mehrere gemeinsame Nodes. Eine Relation endet an diesen Nodes,
        die andere Relation nicht. Das entspricht einer alternativen
        Führung innerhalb der anderen Relation.

    unknown:
        Alle übrigen Fälle.
    """

    relation_a = connection["relation_a"]
    relation_b = connection["relation_b"]
    shared_nodes = connection.get("shared_nodes", [])

    if not shared_nodes:
        return {
            "relation_a": relation_a,
            "relation_b": relation_b,
            "type": "unknown",
            "reason": "Keine gemeinsamen Nodes",
        }

    cache_dir = (
        ROUTE_CACHE
        if cache_dir is None
        else Path(cache_dir)
    )

    import json

    def load_relation_endpoints(relation_id: int) -> set[int]:
        cache_file = cache_dir / f"relation_{relation_id}.json"

        if not cache_file.exists():
            raise FileNotFoundError(
                f"Relations-Cache fehlt: {cache_file}"
            )

        data = json.loads(cache_file.read_text())
        elements = data.get("elements", [])

        relation = next(
            element
            for element in elements
            if element.get("type") == "relation"
            and element.get("id") == relation_id
        )

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

        return set(
            get_relation_endpoints(
                relation,
                ways,
                nodes,
            )
        )

    endpoints_a = load_relation_endpoints(relation_a)
    endpoints_b = load_relation_endpoints(relation_b)

    shared_set = set(shared_nodes)
    shared_endpoints_a = shared_set & endpoints_a
    shared_endpoints_b = shared_set & endpoints_b

    if (
        len(shared_nodes) == 1
        and shared_set <= endpoints_a
        and shared_set <= endpoints_b
    ):
        connection_type = "normal"
        reason = "Ein gemeinsamer Endpunkt"

    elif (
        len(shared_nodes) >= 2
        and shared_set <= endpoints_a
        and not shared_set <= endpoints_b
    ):
        connection_type = "alternative"
        reason = (
            "Relation A endet an mehreren gemeinsamen Nodes, "
            "Relation B nicht"
        )

    elif (
        len(shared_nodes) >= 2
        and shared_set <= endpoints_b
        and not shared_set <= endpoints_a
    ):
        connection_type = "alternative"
        reason = (
            "Relation B endet an mehreren gemeinsamen Nodes, "
            "Relation A nicht"
        )

    else:
        connection_type = "unknown"
        reason = "Topologie entspricht keinem bekannten Verbindungstyp"

    return {
        "relation_a": relation_a,
        "relation_b": relation_b,
        "type": connection_type,
        "reason": reason,
        "shared_nodes": shared_nodes,
        "shared_endpoints_a": sorted(shared_endpoints_a),
        "shared_endpoints_b": sorted(shared_endpoints_b),
    }


def build_route_main_graph(
    family: list[dict],
    cache_dir: Path | None = None,
) -> tuple[dict[int, list[int]], list[dict]]:
    """
    Baut den Hauptgraphen einer Routenfamilie.

    Normale Verbindungen werden als Kanten des Hauptgraphen übernommen.
    Alternative Verbindungen werden separat zurückgegeben.
    """

    graph = {
        int(route["relation_id"]): []
        for route in family
    }

    alternatives = []

    connections = build_route_connection_graph(
        family,
        cache_dir=cache_dir,
    )

    for connection in connections:
        classification = classify_relation_connection(
            connection,
            cache_dir=cache_dir,
        )

        relation_a = int(connection["relation_a"])
        relation_b = int(connection["relation_b"])

        if classification["type"] == "normal":
            graph[relation_a].append(relation_b)
            graph[relation_b].append(relation_a)

        elif classification["type"] == "alternative":
            alternatives.append(classification)

    for relation_id in graph:
        graph[relation_id] = sorted(
            set(graph[relation_id])
        )

    return graph, alternatives

def build_relation_family_graph(
    family: list[dict],
    cache_dir: Path | None = None,
) -> dict[int, list[int]]:
    """
    Baut aus den topologischen Verbindungen einen ungerichteten
    Familiengraphen.

    Rückgabe:
        {
            relation_id: [verbundene_relation_ids, ...]
        }
    """

    graph = {
        route["relation_id"]: []
        for route in family
    }

    connections = build_route_connection_graph(
        family,
        cache_dir=cache_dir,
    )

    for connection in connections:
        relation_a = connection["relation_a"]
        relation_b = connection["relation_b"]

        if relation_b not in graph[relation_a]:
            graph[relation_a].append(relation_b)

        if relation_a not in graph[relation_b]:
            graph[relation_b].append(relation_a)

    for relation_id in graph:
        graph[relation_id].sort()

    return graph

def get_relation_connection_points(
    connection: dict,
    cache_dir: Path | None = None,
) -> dict:
    """
    Ermittelt die gemeinsamen OSM-Nodes einer Relationsverbindung
    und deren Position innerhalb der jeweiligen Relation.

    Die Funktion verwendet ausschließlich vorhandene Relations-Caches.
    """

    if cache_dir is None:
        cache_dir = (
            Path(__file__).resolve().parents[2]
            / "cache"
            / "routes"
        )

    result = {
        "relation_a": connection["relation_a"],
        "relation_b": connection["relation_b"],
        "shared_nodes": [],
    }

    for node_id in connection["shared_nodes"]:
        node_info = {
            "node_id": node_id,
            "relation_a": {},
            "relation_b": {},
        }

        for key, relation_id in (
            ("relation_a", connection["relation_a"]),
            ("relation_b", connection["relation_b"]),
        ):
            cache_file = cache_dir / f"relation_{relation_id}.json"

            if not cache_file.exists():
                continue

            data = json.loads(
                cache_file.read_text(encoding="utf-8")
            )

            relation = next(
                (
                    element
                    for element in data.get("elements", [])
                    if element.get("type") == "relation"
                    and element.get("id") == relation_id
                ),
                None,
            )

            if relation is None:
                continue

            way_positions = []

            for member in relation.get("members", []):
                if member.get("type") != "way":
                    continue

                way_id = member.get("ref")

                way = next(
                    (
                        element
                        for element in data.get("elements", [])
                        if element.get("type") == "way"
                        and element.get("id") == way_id
                    ),
                    None,
                )

                if way is None:
                    continue

                way_nodes = way.get("nodes", [])

                if node_id not in way_nodes:
                    continue

                positions = [
                    index
                    for index, value in enumerate(way_nodes)
                    if value == node_id
                ]

                way_positions.append(
                    {
                        "way_id": way_id,
                        "positions": positions,
                        "way_length": len(way_nodes),
                    }
                )

            node_info[key] = {
                "way_positions": way_positions,
            }

        result["shared_nodes"].append(node_info)

    return result


def discover_route_family(reference: dict) -> list[dict]:
    """Findet die Route-Familie einer Relation über Metadaten."""
    import json
    from pathlib import Path

    from core.config import load_config
    from core.overpass import query_overpass_adaptive

    config = load_config()

    cache_dir = ROUTE_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)

    ref = reference.get("ref", "")
    network = reference.get("network", "")
    wikidata = reference.get("wikidata", "")
    wikipedia = reference.get("wikipedia", "")
    operator = reference.get("operator", "")

    safe_ref = ref.replace("/", "_").replace(";", "_") or "none"
    safe_network = network.replace("/", "_") or "none"

    cache_file = (
        cache_dir
        / f"family_{safe_ref}_{safe_network}.json"
    )

    if cache_file.exists():
        print(f"Familien-Cache geladen: {cache_file}")
        data = json.loads(
            cache_file.read_text(encoding="utf-8")
        )
        return data.get("routes", [])

    query_parts = []

    if ref:
        query_parts.append(
            f'relation["route"="hiking"]["ref"="{ref}"]'
        )

    if wikidata:
        query_parts.append(
            f'relation["route"="hiking"]["wikidata"="{wikidata}"]'
        )

    if wikipedia:
        query_parts.append(
            f'relation["route"="hiking"]["wikipedia"="{wikipedia}"]'
        )

    if operator:
        query_parts.append(
            f'relation["route"="hiking"]["operator"="{operator}"]'
        )

    if not query_parts:
        return [reference]

    def build_query(
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
    ) -> str:
        selectors = []

        for selector in query_parts:
            selectors.append(
                f"{selector}"
                f"({min_lat},{min_lon},{max_lat},{max_lon});"
            )

        return (
              f"[out:json][timeout:{config['hiking']['family_query_timeout']}];\n"
            "(\n"
            + "\n".join(selectors)
            + "\n);\n"
            "out tags;"
        )

    areas = config["hiking"]["family_search_areas"]

    print(
        "Starte adaptive Overpass-Familienabfrage "
        f"für Relation {reference['relation_id']}."
    )

    successful_groups, failed_areas = query_overpass_adaptive(
        areas,
        build_query,
    )

    relations = {}

    for group, result in successful_groups:
        for element in result.get("elements", []):
            if element.get("type") != "relation":
                continue

            relation_id = element.get("id")
            tags = element.get("tags", {})

            if relation_id is None:
                continue

            relations[relation_id] = {
                "relation_id": relation_id,
                "name": tags.get("name", ""),
                "ref": tags.get("ref", ""),
                "network": tags.get("network", ""),
                "route": tags.get("route", ""),
                "distance": tags.get("distance", ""),
                "operator": tags.get("operator", ""),
                "wikidata": tags.get("wikidata", ""),
                "wikipedia": tags.get("wikipedia", ""),
                "website": tags.get("website", ""),
            }

    same_ref_network = [
        route
        for route in relations.values()
        if (
            ref
            and route["ref"] == ref
            and route["network"] == network
            and route["route"] == "hiking"
        )
    ]

    same_wikidata = [
        route
        for route in relations.values()
        if (
            wikidata
            and route["wikidata"] == wikidata
            and route["route"] == "hiking"
        )
    ]

    same_wikipedia = [
        route
        for route in relations.values()
        if (
            wikipedia
            and route["wikipedia"] == wikipedia
            and route["route"] == "hiking"
        )
    ]

    family = {}

    for route in (
        same_ref_network
        + same_wikidata
        + same_wikipedia
    ):
        family[route["relation_id"]] = route

    family[reference["relation_id"]] = reference

    result = sorted(
        family.values(),
        key=lambda route: (
            route.get("name", "").lower(),
            route["relation_id"],
        ),
    )

    cache_file.write_text(
        json.dumps(
            {"routes": result},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Routenfamilie gefunden: {len(result)} Relationen."
    )

    if failed_areas:
        print(
            f"Nicht geladene Bereiche: {len(failed_areas)}"
        )

    return result


def build_route_nodes(
    relation: dict,
    ways: dict[int, dict],
) -> list[int]:
    way_ids = {
        member["ref"]
        for member in relation["members"]
        if member["type"] == "way"
        and member["ref"] in ways
    }

    if not way_ids:
        raise ValueError("Relation enthält keine Ways.")

    node_to_ways = {}

    for way_id in way_ids:
        way_nodes = ways[way_id]["nodes"]

        if not way_nodes:
            continue

        node_to_ways.setdefault(
            way_nodes[0],
            [],
        ).append(way_id)

        node_to_ways.setdefault(
            way_nodes[-1],
            [],
        ).append(way_id)

    neighbors = {}

    for way_id in way_ids:
        way_nodes = ways[way_id]["nodes"]

        connected = set()

        for node_id in (
            way_nodes[0],
            way_nodes[-1],
        ):
            for other_way in node_to_ways.get(
                node_id,
                [],
            ):
                if other_way != way_id:
                    connected.add(other_way)

        neighbors[way_id] = connected

    end_ways = [
        way_id
        for way_id, connected in neighbors.items()
        if len(connected) == 1
    ]

    # Geschlossener Rundweg:
    # Es gibt keine End-Ways. In diesem Fall wird wie im
    # ursprünglichen Algorithmus beim ersten Relation-Way
    # gestartet und die Ways anhand ihrer gemeinsamen End-Nodes
    # verkettet.
    if len(end_ways) == 0:
        first_id = next(iter(way_ids))
        route_nodes = list(ways[first_id]["nodes"])
        used = {first_id}

        while len(used) < len(way_ids):
            current_end = route_nodes[-1]
            found = None

            for way_id in way_ids:
                if way_id in used:
                    continue

                way_nodes = ways[way_id]["nodes"]

                if way_nodes[0] == current_end:
                    found = list(way_nodes)
                    break

                if way_nodes[-1] == current_end:
                    found = list(reversed(way_nodes))
                    break

            if found is None:
                raise ValueError(
                    f"Kein Anschluss für Node {current_end}; "
                    f"noch {len(way_ids) - len(used)} Ways offen."
                )

            route_nodes.extend(found[1:])
            used.add(way_id)

        return route_nodes

    if len(end_ways) != 2:
        return build_main_track_from_graph(
            relation,
            ways,
        )

    ordered_ways = []
    used = set()
    current = end_ways[0]

    while True:
        ordered_ways.append(current)
        used.add(current)

        next_ways = [
            way_id
            for way_id in neighbors[current]
            if way_id not in used
        ]

        if not next_ways:
            break

        if len(next_ways) != 1:
            raise ValueError(
                f"Mehrdeutige Verkettung bei Way "
                f"{current}: {next_ways}"
            )

        current = next_ways[0]

    if len(ordered_ways) != len(way_ids):
        raise ValueError(
            f"Nicht alle Ways konnten verkettet werden: "
            f"{len(ordered_ways)} von {len(way_ids)}."
        )

    first_way = list(
        ways[ordered_ways[0]]["nodes"]
    )

    second_way = ways[ordered_ways[1]]["nodes"]

    common = set(first_way) & set(second_way)

    if len(common) != 1:
        raise ValueError(
            "Die ersten beiden Ways haben nicht "
            "genau einen gemeinsamen Node."
        )

    join_node = next(iter(common))

    if first_way[-1] != join_node:
        if first_way[0] == join_node:
            first_way.reverse()
        else:
            raise ValueError(
                "Der erste Way kann nicht auf "
                "den zweiten Way ausgerichtet werden."
            )

    route_nodes = list(first_way)

    for way_id in ordered_ways[1:]:
        way_nodes = list(
            ways[way_id]["nodes"]
        )

        current_end = route_nodes[-1]

        if way_nodes[0] == current_end:
            route_nodes.extend(
                way_nodes[1:]
            )

        elif way_nodes[-1] == current_end:
            way_nodes.reverse()
            route_nodes.extend(
                way_nodes[1:]
            )

        else:
            raise ValueError(
                f"Way {way_id} passt nicht an "
                f"Routenende {current_end}."
            )

    return route_nodes


def relation_name(relation: dict) -> str:
    tags = relation.get("tags", {})

    name = (
        tags.get("name")
        or tags.get("ref")
        or f"Relation {relation['id']}"
    )

    if " [" in name and name.endswith("]"):
        name = name.rsplit(" [", 1)[0]

    return name



def build_relation_path_between_nodes(
    relation: dict,
    ways,
    start_node: int,
    end_node: int,
) -> list[int]:
    """
    Ermittelt den geometrischen Pfad zwischen zwei Nodes innerhalb
    der vollständigen Way-Geometrie einer Relation.

    Die Funktion arbeitet direkt auf den Way-Node-Sequenzen und
    ist damit unabhängig von einer vorherigen Zerlegung der Relation
    in einzelne geometrische Tracks.
    """
    if start_node == end_node:
        return [start_node]

    if isinstance(ways, dict):
        way_map = ways
    else:
        way_map = {
            int(way["id"]): way
            for way in ways
        }

    relation_way_ids = []

    for member in relation.get("members", []):
        if member.get("type") != "way":
            continue

        way_id = int(member["ref"])

        if way_id in way_map:
            relation_way_ids.append(way_id)

    if not relation_way_ids:
        raise ValueError(
            f"Relation {relation.get('id')} enthält keine Ways."
        )

    adjacency = {}

    for way_id in relation_way_ids:
        way = way_map[way_id]
        node_ids = way.get("nodes", [])

        for index in range(len(node_ids) - 1):
            a = int(node_ids[index])
            b = int(node_ids[index + 1])

            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)

    if start_node not in adjacency:
        raise ValueError(
            f"Start-Node {start_node} kommt in keiner Way-Geometrie vor."
        )

    if end_node not in adjacency:
        raise ValueError(
            f"End-Node {end_node} kommt in keiner Way-Geometrie vor."
        )

    queue = [start_node]
    previous = {
        start_node: None
    }

    while queue:
        current = queue.pop(0)

        if current == end_node:
            break

        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor in previous:
                continue

            previous[neighbor] = current
            queue.append(neighbor)

    if end_node not in previous:
        raise ValueError(
            f"Keine Geometrieverbindung zwischen "
            f"{start_node} und {end_node}."
        )

    path = []
    current = end_node

    while current is not None:
        path.append(current)
        current = previous[current]

    path.reverse()

    return path

def find_track_path_between_nodes(
    tracks: list[list[int]],
    start_node: int,
    end_node: int,
) -> list[list[int]] | None:
    """
    Findet eine zusammenhängende Track-Kette zwischen zwei Knoten.

    Die Tracks werden ausschließlich über ihre Endknoten verbunden.
    Es wird keine routenspezifische Information verwendet.

    Rückgabe:
        Eine geordnete Liste von Tracks, die von ``start_node`` nach
        ``end_node`` führt, oder ``None``, wenn keine Verbindung existiert.
    """
    if start_node == end_node:
        return []

    edges: dict[int, list[tuple[int, int]]] = {}

    for track_index, track in enumerate(tracks):
        if len(track) < 2:
            continue

        start = track[0]
        end = track[-1]

        edges.setdefault(start, []).append((track_index, end))
        edges.setdefault(end, []).append((track_index, start))

    queue: list[tuple[int, list[int]]] = [(start_node, [])]
    visited_nodes = {start_node}

    while queue:
        current_node, path = queue.pop(0)

        if current_node == end_node:
            return [tracks[index] for index in path]

        for track_index, next_node in edges.get(current_node, []):
            if next_node in visited_nodes:
                continue

            visited_nodes.add(next_node)
            queue.append(
                (
                    next_node,
                    path + [track_index],
                )
            )

    return None

def merge_connected_tracks(
    tracks: list[list[int]],
) -> list[int]:
    """
    Fügt eine geordnete Kette zusammenhängender Tracks zu einem
    einzigen Track zusammen.

    Die Orientierung jedes Folgetracks wird anhand der Endknoten
    automatisch bestimmt. Der gemeinsame Verbindungsknoten wird
    beim Zusammenfügen nicht doppelt übernommen.
    """
    if not tracks:
        raise ValueError("Keine Tracks zum Zusammenfügen vorhanden.")

    result = list(tracks[0])

    if not result:
        raise ValueError("Erster Track ist leer.")

    for index, track in enumerate(tracks[1:], start=2):
        if not track:
            raise ValueError(
                f"Track {index} ist leer."
            )

        current_end = result[-1]

        if track[0] == current_end:
            result.extend(track[1:])
            continue

        if track[-1] == current_end:
            result.extend(reversed(track[:-1]))
            continue

        raise ValueError(
            "Tracks sind nicht zusammenhängend: "
            f"Ende des bisherigen Tracks {current_end} "
            f"passt nicht zu Track {index} "
            f"({track[0]} -> {track[-1]})."
        )

    return result


def build_connected_track_chains(
    tracks: list[list[int]],
) -> list[list[int]]:
    """
    Findet lineare, über Track-Endpunkte verbundene Track-Ketten
    und fügt jede solche Kette zu einem Track zusammen.

    Tracks mit Verzweigungen werden nicht künstlich linearisiert.
    Eine Kette mit nur einem Track wird nicht zusätzlich erzeugt.
    """
    if not tracks:
        return []

    endpoint_graph = {
        index: set()
        for index in range(len(tracks))
    }

    endpoint_map = {}

    for index, track in enumerate(tracks):
        if len(track) < 2:
            continue

        endpoint_map.setdefault(track[0], []).append(index)
        endpoint_map.setdefault(track[-1], []).append(index)

    for indices in endpoint_map.values():
        unique_indices = sorted(set(indices))

        for index_a in unique_indices:
            for index_b in unique_indices:
                if index_a != index_b:
                    endpoint_graph[index_a].add(index_b)

    remaining = set(endpoint_graph)
    chains = []

    while remaining:
        component_start = min(remaining)
        component = set()
        stack = [component_start]

        while stack:
            current = stack.pop()

            if current in component:
                continue

            component.add(current)
            remaining.discard(current)

            for neighbor in endpoint_graph[current]:
                if neighbor not in component:
                    stack.append(neighbor)

        if len(component) < 2:
            continue

        degrees = {
            index: len(endpoint_graph[index] & component)
            for index in component
        }

        endpoints = sorted(
            index
            for index, degree in degrees.items()
            if degree == 1
        )

        if len(endpoints) != 2:
            continue

        start_index = endpoints[0]
        next_candidates = [
            index
            for index in endpoint_graph[start_index]
            if index in component
        ]

        if len(next_candidates) != 1:
            continue

        next_index = next_candidates[0]

        shared_endpoints = set(
            (
                tracks[start_index][0],
                tracks[start_index][-1],
            )
        ) & set(
            (
                tracks[next_index][0],
                tracks[next_index][-1],
            )
        )

        if len(shared_endpoints) != 1:
            continue

        shared_node = next(iter(shared_endpoints))

        if tracks[start_index][-1] == shared_node:
            first_track = tracks[start_index]
        else:
            first_track = list(reversed(tracks[start_index]))

        ordered_indices = [start_index, next_index]
        previous = start_index
        current = next_index

        while True:
            next_indices = [
                index
                for index in endpoint_graph[current]
                if index in component
                and index != previous
            ]

            if not next_indices:
                break

            if len(next_indices) != 1:
                ordered_indices = []
                break

            previous, current = current, next_indices[0]
            ordered_indices.append(current)

        if len(ordered_indices) != len(component):
            continue

        ordered_tracks = [tracks[index] for index in ordered_indices]

        if ordered_tracks[0] is not first_track:
            ordered_tracks[0] = first_track

        try:
            chains.append(
                merge_connected_tracks(ordered_tracks)
            )
        except ValueError:
            continue

    return chains


def build_route_family_tracks(
    family: list[dict],
    cache_dir: Path | None = None,
    track_metadata: list[dict] | None = None,
    selected_relation_id: int | None = None,
) -> list[list[int]]:
    """
    Baut die Haupt-Tracks einer Routenfamilie.

    Die Familienreihenfolge wird über den Relationsgraphen bestimmt.
    Eine Relation darf mehrere geometrisch getrennte Komponenten
    enthalten. Diese Komponenten werden nicht künstlich verbunden,
    sondern als getrennte Tracks erhalten.
    """
    if not family:
        raise ValueError("Routenfamilie ist leer.")

    if cache_dir is None:
        cache_dir = ROUTE_CACHE

    graph, alternatives = build_route_main_graph(
        family,
        cache_dir=cache_dir,
    )

    relation_ids = sorted(graph)

    if not relation_ids:
        raise ValueError(
            "Routenfamilie enthält keine Relationen."
        )

    # Hauptkomponenten des Relationsgraphen bestimmen.
    remaining = set(relation_ids)
    components = []

    while remaining:
        start_relation = min(remaining)
        component = []
        stack = [start_relation]

        while stack:
            relation_id = stack.pop()

            if relation_id not in remaining:
                continue

            remaining.remove(relation_id)
            component.append(relation_id)

            for neighbor in graph.get(relation_id, []):
                if neighbor in remaining:
                    stack.append(neighbor)

        components.append(sorted(component))

    components.sort(
        key=lambda component: (
            -len(component),
            component[0],
        )
    )

    if selected_relation_id is not None:
        selected_component = next(
            (
                component
                for component in components
                if selected_relation_id in component
            ),
            None,
        )

        if selected_component is None:
            raise ValueError(
                f"Ausgewählte Relation {selected_relation_id} "
                "ist in keiner Familienkomponente enthalten."
            )

        main_component = selected_component
    else:
        main_component = components[0]

    selected_component_size = len(main_component)

    # Relationsreihenfolge der Hauptkomponente bestimmen.
    if len(main_component) == 1:
        relation_order = main_component
    else:
        degrees = {
            relation_id: len(
                [
                    neighbor
                    for neighbor in graph.get(relation_id, [])
                    if neighbor in main_component
                ]
            )
            for relation_id in main_component
        }

        endpoints = sorted(
            relation_id
            for relation_id, degree in degrees.items()
            if degree == 1
        )

        if len(endpoints) != 2:
            raise ValueError(
                "Hauptkomponente besitzt "
                f"{len(endpoints)} Endrelationen."
            )

        relation_order = []
        previous = None
        current = endpoints[0]

        while current is not None:
            relation_order.append(current)

            next_relations = [
                neighbor
                for neighbor in graph.get(current, [])
                if neighbor in main_component
                and neighbor != previous
            ]

            if not next_relations:
                break

            if len(next_relations) != 1:
                raise ValueError(
                    "Mehrdeutige Hauptrelationskette bei "
                    f"{current}: {next_relations}"
                )

            previous, current = current, next_relations[0]

        if len(relation_order) != len(main_component):
            raise ValueError(
                "Hauptrelationskette ist nicht vollständig."
            )

    # Normale Familienverbindungen mit ihren gemeinsamen Nodes.
    connections = {}

    for connection in build_route_connection_graph(
        family,
        cache_dir=cache_dir,
    ):
        classification = classify_relation_connection(
            connection,
            cache_dir=cache_dir,
        )

        if classification["type"] != "normal":
            continue

        relation_a = classification["relation_a"]
        relation_b = classification["relation_b"]

        if (
            relation_a not in main_component
            or relation_b not in main_component
        ):
            continue

        shared_nodes = classification["shared_nodes"]

        if len(shared_nodes) != 1:
            raise ValueError(
                "Normale Verbindung besitzt nicht genau "
                f"einen gemeinsamen Node: "
                f"{relation_a} <-> {relation_b}"
            )

        node_id = shared_nodes[0]

        connections[(relation_a, relation_b)] = node_id
        connections[(relation_b, relation_a)] = node_id

    result_tracks = []
    family_nodes = {}

    def add_family_track(track: list[int], relation_id: int, relation: dict) -> None:
        result_tracks.append(track)

        if track_metadata is not None:
            track_metadata.append(
                {
                    "relation_id": relation_id,
                    "relation_name": relation.get("tags", {}).get("name", ""),
                }
            )

    for index, relation_id in enumerate(relation_order):
        relation, ways, nodes = load_relation(relation_id)
        family_nodes.update(nodes)

        # Vollständigen Node-Graph der Relation aufbauen.
        adjacency = {}

        for member in relation.get("members", []):
            if member.get("type") != "way":
                continue

            way = ways.get(int(member["ref"]))
            if way is None:
                continue

            way_nodes = [
                int(node_id)
                for node_id in way.get("nodes", [])
            ]

            for pos in range(len(way_nodes) - 1):
                a = way_nodes[pos]
                b = way_nodes[pos + 1]

                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)

        # Zusammenhangskomponenten der Relation bestimmen.
        remaining_nodes = set(adjacency)
        relation_components = []

        while remaining_nodes:
            start_node = next(iter(remaining_nodes))
            component_nodes = {start_node}
            stack = [start_node]

            while stack:
                current = stack.pop()

                for neighbor in adjacency.get(current, ()):
                    if neighbor not in component_nodes:
                        component_nodes.add(neighbor)
                        remaining_nodes.discard(neighbor)
                        stack.append(neighbor)

            remaining_nodes -= component_nodes
            relation_components.append(component_nodes)

        # Familien-Anschlusspunkte dieser Relation.
        connection_nodes = []

        if index > 0:
            entry_node = connections.get(
                (relation_order[index - 1], relation_id)
            )
            if entry_node is not None:
                connection_nodes.append(entry_node)

        if index + 1 < len(relation_order):
            exit_node = connections.get(
                (relation_id, relation_order[index + 1])
            )
            if exit_node is not None:
                connection_nodes.append(exit_node)

        connection_nodes = list(dict.fromkeys(connection_nodes))

        # Komponenten auswählen:
        # Jede Komponente mit einem Familien-Anschlusspunkt bleibt
        # erhalten. Bei einer Relation ohne Anschlusspunkt wird die
        # größte Komponente verwendet.
        selected_components = []

        for component_nodes in relation_components:
            if any(
                node_id in component_nodes
                for node_id in connection_nodes
            ):
                selected_components.append(component_nodes)

        if not selected_components:
            if relation_components:
                selected_components = [
                    max(relation_components, key=len)
                ]
            else:
                raise ValueError(
                    f"Keine Geometrie für Relation {relation_id}."
                )

        # Für jede ausgewählte Komponente die enthaltenen Ways als
        # geometrische Tracks rekonstruieren.
        tracks = build_main_track_from_graph(
            relation,
            ways,
        )

        # Bei genau zwei Familien-Anschlusspunkten den vollständigen
        # geometrischen Pfad zwischen diesen Punkten verwenden.
        # Falls kein solcher Pfad gefunden wird, bleibt die bisherige
        # Auswahl darunter als unveränderter Fallback erhalten.
        if len(connection_nodes) == 2:
            connected_tracks = find_track_path_between_nodes(
                tracks,
                connection_nodes[0],
                connection_nodes[1],
            )

            if connected_tracks:
                for track in connected_tracks:
                    add_family_track(track, relation_id, relation)
                continue

        for track in tracks:
            track_nodes = set(track)

            if any(
                node_id in track_nodes
                for node_id in connection_nodes
            ):
                add_family_track(track, relation_id, relation)

        # Falls eine ausgewählte Komponente durch die Trackzerlegung
        # keinen Track mit Anschlusspunkt ergeben hat, nichts künstlich
        # verbinden. Die Geometrie bleibt damit unverändert.
        if not any(
            any(
                node_id in set(track)
                for node_id in connection_nodes
            )
            for track in tracks
        ):
            if not connection_nodes:
                for track in tracks:
                    add_family_track(track, relation_id, relation)

    if not result_tracks:
        raise ValueError(
            "Keine Geometrie für die Routenfamilie."
        )

    return result_tracks, family_nodes, selected_component_size

def discover(bbox, force=False):
    """Routen für die gemeinsame Plugin-Schnittstelle entdecken."""
    from core.routes import RouteItem

    routes = find_hiking_routes(bbox)

    return [
        RouteItem(
            id=str(route["relation_id"]),
            name=route["name"] or str(route["relation_id"]),
            description=route["category"],
            data=route,
        )
        for route in routes
    ]


def generate(route_item, force=False):
    """RouteItem über die bestehende Hiking-GPX-Erzeugung verarbeiten."""
    return generate_hiking_gpx(int(route_item.id))


def generate_hiking_gpx(relation_id: int) -> Path:
    relation, ways, nodes = load_relation(relation_id)

    tags = relation.get("tags", {})

    reference = {
        "relation_id": relation_id,
        **tags,
    }

    family = discover_route_family(reference)

    family_refs = sorted(
        {
            item.get("ref")
            for item in family
            if item.get("ref")
        }
    )

    family_wikipedia = sorted(
        {
            item.get("wikipedia")
            for item in family
            if item.get("wikipedia")
        }
    )

    family_ref = family_refs[0] if len(family_refs) == 1 else None
    family_wikipedia_name = None

    if len(family_wikipedia) == 1:
        family_wikipedia_name = family_wikipedia[0].split(":", 1)[-1]

    if family_ref and family_wikipedia_name:
        family_name = f"{family_ref} - {family_wikipedia_name}"
    elif family_ref:
        family_name = family_ref
    elif family_wikipedia_name:
        family_name = family_wikipedia_name
    else:
        family_name = reference.get("name", "")

    print("Familienname:", family_name)

    family_chain_tracks = []
    family_individual_tracks = []
    family_component_size = 1

    if len(family) > 1:
        family_track_metadata = []
        family_tracks, family_nodes, family_component_size = build_route_family_tracks(
            family,
            track_metadata=family_track_metadata,
            selected_relation_id=relation_id,
        )
        print("Familien-Tracks:", len(family_tracks))
        for track_index, metadata in enumerate(family_track_metadata, start=1):
            print(
                f"Track {track_index}: "
                f"Relation {metadata["relation_id"]}: "
                f"{metadata["relation_name"]}"
            )

        nodes = family_nodes
        family_chain_tracks = build_connected_track_chains(family_tracks)
        family_individual_tracks = family_tracks
        tracks_to_write = family_individual_tracks

    else:
        try:
            route_nodes = build_route_nodes(
                relation,
                ways,
            )
        except ValueError as exc:
            if "Erwartet 2 End-Ways" not in str(exc):
                raise

            tracks = build_main_track_from_graph(
                relation,
                ways,
            )

            route_nodes = max(
                tracks,
                key=lambda track: calculate_route_length_km(
                    track,
                    nodes,
                ),
            )

        tracks_to_write = [route_nodes]

    tags = relation.get("tags", {})
    name = relation_name(relation)
    ref = tags.get("ref")
    operator = tags.get("operator")
    distance = tags.get("distance")
    network = tags.get("network")
    osmc_symbol = tags.get("osmc:symbol")
    wikidata = tags.get("wikidata")

    description_parts = []

    if operator:
        description_parts.append(f"Betreiber: {operator}")

    if ref:
        description_parts.append(f"Ref: {ref}")

    if distance:
        description_parts.append(f"Länge: {distance} km")

    if network:
        network_names = {
            "iwn": "International",
            "nwn": "National",
            "rwn": "Regional",
            "lwn": "Lokal",
        }
        description_parts.append(
            f"Netzwerk: {network_names.get(network, network)}"
        )

    if osmc_symbol:
        description_parts.append(f"OSMC-Symbol: {osmc_symbol}")

    if wikidata:
        wikipedia_info = get_wikipedia_info(wikidata)

        if wikipedia_info:
            extract = wikipedia_info.get("extract")
            wikipedia_url = wikipedia_info.get("url")

            if extract:
                description_parts.insert(0, extract)

            if wikipedia_url:
                description_parts.append(
                    f"Wikipedia: {wikipedia_url}"
                )

    description = "<br />".join(description_parts) or None

    gpx = create_gpx()

    add_metadata(
        gpx,
        name=name,
        description=description,
        author=operator,
    )

    for index, track_nodes in enumerate(tracks_to_write, start=1):
        points = [
            (
                nodes[node_id]["lat"],
                nodes[node_id]["lon"],
            )
            for node_id in track_nodes
        ]

        if family_component_size > 1:
            track_name = family_track_metadata[index - 1]["relation_name"]
        else:
            track_name = name

        add_track(
            gpx,
            points,
            name=track_name,
            description=description,
            track_type="Hiking",
        )

    if family_component_size > 1:
        output_file = GPX_OUTPUT / f"{family_name}_einzel.gpx"
    else:
        if ref:
            safe_ref = ref.replace("Ⓡ", "R")
            output_file = GPX_OUTPUT / f"{name}_{safe_ref}_{relation_id}.gpx"
        else:
            output_file = GPX_OUTPUT / f"{name}_{relation_id}.gpx"

    save_gpx(gpx, output_file)

    if family_component_size > 1 and family_chain_tracks:
        chain_gpx = create_gpx()

        add_metadata(
            chain_gpx,
            name=f"{ref or name} - zusammenhängende Strecken",
            description=description,
            author=operator,
        )

        for index, track_nodes in enumerate(family_chain_tracks, start=1):
            points = [
                (
                    nodes[node_id]["lat"],
                    nodes[node_id]["lon"],
                )
                for node_id in track_nodes
            ]

            add_track(
                chain_gpx,
                points,
                name=f"{ref or name} - zusammenhängende Strecke {index}",
                description=description,
                track_type="Hiking",
            )

        if family_component_size > 1:
            chain_output_file = (
                GPX_OUTPUT
                / f"{family_name}_gesamt.gpx"
            )
        else:
            if ref:
                safe_ref = ref.replace("Ⓡ", "R")
                chain_output_file = (
                    GPX_OUTPUT
                    / f"{safe_ref}_zusammenhaengende_Strecken_{relation_id}.gpx"
                )
            else:
                chain_output_file = (
                    GPX_OUTPUT
                    / f"{name}_zusammenhaengende_Strecken_{relation_id}.gpx"
                )

        save_gpx(chain_gpx, chain_output_file)

    return output_file


def sort_items(items):
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

    return sorted(
        items,
        key=lambda item: (
            category_order.get(item.data["category"], 99),
            item.name.lower(),
        ),
    )


def print_items(items):
    print()
    print(f"Gefundene Wanderrouten: {len(items)}")
    print()
    print(
        f"{"Nr":>3} | "
        f"{"Kategorie":<22} | "
        f"{"Ref":<16} | "
        f"{"Länge":<8} | "
        f"Name"
    )
    print("-" * 110)

    for number, item in enumerate(items, 1):
        print(
            f"{number:>3} | "
            f"{item.data["category"]:<22} | "
            f"{item.data["ref"]:<16} | "
            f"{item.data["distance"]:<8} | "
            f"{item.name}"
        )


def print_selected(items):
    print()
    print(f"{len(items)} Route(n) ausgewählt:")

    for item in items:
        print(
            f"  {item.id} | "
            f"{item.data["ref"]} | "
            f"{item.name}"
        )


def get_selection_prompt():
    return "\nAuswahl (z.B. 33 oder 33,64-67, b zurück, q zum Abbrechen): "
