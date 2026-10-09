import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, load_config
from core.overpass import query_overpass_adaptive, query_overpass_retry


ROUTE_CACHE = PROJECT_ROOT / "cache" / "routes"


def get_indexed_route_types() -> list[str]:
    """Liest die gemeinsam abzurufenden OSM-Routentypen."""
    route_types = load_config()["routes"]["index_types"]

    if not route_types:
        raise ValueError("Für den Routenindex sind keine Routentypen konfiguriert.")

    return route_types


def build_route_index_query(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> str:
    """Baut eine gemeinsame, bewusst auf Routentypen begrenzte Indexabfrage."""
    config = load_config()
    route_types = get_indexed_route_types()
    route_filters = "|".join(route_types)

    return f"""
[out:json][timeout:{config["routes"]["query_timeout"]}];
relation["type"="route"]["route"~"^({route_filters})$"]
  ({min_lat},{min_lon},{max_lat},{max_lon});
out tags;
"""


def collect_route_index(
    bbox: tuple[float, float, float, float],
    force: bool = False,
) -> list[dict]:
    """Lädt den gemeinsamen BBox-Routenindex aus Cache/Overpass."""
    south, west, north, east = bbox
    config = load_config()

    ROUTE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = ROUTE_CACHE / (
        f"route_index_{south:.6f}_{west:.6f}_{north:.6f}_{east:.6f}.json"
    )
    ttl_seconds = config["routes"]["index_ttl_days"] * 24 * 60 * 60

    if cache_path.exists() and not force:
        age_seconds = time.time() - cache_path.stat().st_mtime

        if age_seconds < ttl_seconds:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"Routenindex aus Cache geladen: {cache_path.name}")
            return data["relations"]

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
        build_route_index_query,
    )

    if failed_areas:
        raise RuntimeError("Routenindex konnte nicht vollständig geladen werden.")

    relations = {}

    for _, data in successful_groups:
        for element in data.get("elements", []):
            if element.get("type") != "relation":
                continue

            relation_id = element.get("id")
            if relation_id is not None:
                relations[relation_id] = element

    result = [relations[relation_id] for relation_id in sorted(relations)]
    cache_path.write_text(
        json.dumps({"relations": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Routenindex im Cache gespeichert: {cache_path.name}")

    return result


def find_route_relations(
    bbox: tuple[float, float, float, float],
    route_type: str,
    force: bool = False,
) -> list[dict]:
    """Filtert einen Plugin-Routentyp aus dem gemeinsamen Routenindex."""
    return [
        relation
        for relation in collect_route_index(bbox, force=force)
        if relation.get("tags", {}).get("route") == route_type
    ]


def load_route_relation(
    relation_id: int,
    force: bool = False,
) -> tuple[dict, dict[int, dict], dict[int, dict]]:
    """Lädt eine vollständige OSM-Relation über einen gemeinsamen Cache."""
    ROUTE_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = ROUTE_CACHE / f"relation_{relation_id}.json"

    if cache_path.exists() and not force:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"Relations-Cache geladen: {cache_path.name}")
    else:
        config = load_config()
        query = f"""
[out:json][timeout:{config["routes"]["query_timeout"]}];
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


@dataclass
class RouteItem:
    """Einheitliche Beschreibung einer auswählbaren Route."""

    id: str
    name: str
    description: str = ""
    data: Any = None
    region: Any = None
