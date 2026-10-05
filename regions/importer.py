import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, load_config


REGIONS_DB = PROJECT_ROOT / "regions" / "regions.db"
CACHE_DIR = PROJECT_ROOT / "cache" / "regions"
CACHE_SCHEMA_VERSION = 2


def get_regions_config() -> dict:
    return load_config()["regions"]


def get_cache_file(cache_key: str) -> Path:
    return CACHE_DIR / f"{cache_key}.json"


def create_cache_version(query: str) -> str:
    import hashlib

    return hashlib.sha256(
        query.encode("utf-8")
    ).hexdigest()


def load_cached_query(
    cache_key: str,
    cache_version: str,
) -> dict | None:
    cache_file = get_cache_file(cache_key)

    if not cache_file.exists():
        return None

    try:
        with cache_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None

    metadata = data.get("_meta", {})

    if (
        metadata.get("schema_version") != CACHE_SCHEMA_VERSION
        or metadata.get("cache_version") != cache_version
    ):
        return None

    ttl_days = get_regions_config()["cache_ttl_days"]

    modified = datetime.fromtimestamp(
        cache_file.stat().st_mtime,
        tz=timezone.utc,
    )

    if datetime.now(timezone.utc) >= (
        modified + timedelta(days=ttl_days)
    ):
        return None

    return data


def save_cached_query(
    cache_key: str,
    data: dict,
    cache_version: str,
) -> None:
    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_file = get_cache_file(cache_key)

    cache_data = {
        "_meta": {
            "schema_version": CACHE_SCHEMA_VERSION,
            "cache_version": cache_version,
        },
        "elements": data.get("elements", []),
    }

    with cache_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            cache_data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def query_overpass_regions(
    query: str,
    cache_key: str,
) -> dict:
    cache_version = create_cache_version(query)

    cached_data = load_cached_query(
        cache_key,
        cache_version,
    )

    if cached_data is not None:
        print(f"  Regions-Cache verwendet: {cache_key}")
        return cached_data

    config = get_regions_config()
    servers = load_config()["overpass"]["servers"]
    timeout = config["request_timeout"]
    retry_rounds = config["retry_rounds"]
    retry_delay = config["retry_delay_seconds"]

    last_error = None

    for retry_round in range(1, retry_rounds + 1):
        print(
            f"  Overpass-Versuch "
            f"{retry_round}/{retry_rounds}"
        )

        for server in servers:
            try:
                data = urllib.parse.urlencode(
                    {"data": query}
                ).encode("utf-8")

                request = urllib.request.Request(
                    server,
                    data=data,
                    headers={
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                        "User-Agent":
                            "OSMProjekt/1.0",
                    },
                    method="POST",
                )

                with urllib.request.urlopen(
                    request,
                    timeout=timeout,
                ) as response:
                    response_data = (
                        response.read()
                        .decode("utf-8")
                    )

                result = json.loads(response_data)

                save_cached_query(
                    cache_key,
                    result,
                    cache_version,
                )

                return result

            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                json.JSONDecodeError,
            ) as error:
                last_error = error
                print(
                    f"    Server fehlgeschlagen: "
                    f"{server}"
                )

        if retry_round < retry_rounds:
            time.sleep(retry_delay)

    raise RuntimeError(
        "Alle Regions-Overpass-Versuche "
        f"sind fehlgeschlagen: {last_error}"
    )


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(REGIONS_DB)
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )
    return connection


def clear_regions() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM regions")


def insert_region(
    connection: sqlite3.Connection,
    name: str,
    region_type: str,
    parent_id: int | None,
    osm_type: str,
    osm_id: int,
    min_lat: float | None = None,
    min_lon: float | None = None,
    max_lat: float | None = None,
    max_lon: float | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO regions (
            name,
            type,
            parent_id,
            osm_type,
            osm_id,
            min_lat,
            min_lon,
            max_lat,
            max_lon
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            region_type,
            parent_id,
            osm_type,
            osm_id,
            min_lat,
            min_lon,
            max_lat,
            max_lon,
        ),
    )

    return cursor.lastrowid


def find_relation(
    elements: list[dict],
    osm_id: int,
) -> dict | None:
    for element in elements:
        if (
            element.get("type") == "relation"
            and element.get("id") == osm_id
        ):
            return element

    return None


def build_country_query() -> str:
    return """
[out:json][timeout:60];
relation
    ["boundary"="administrative"]
    ["admin_level"="2"]
    ["ISO3166-1"="DE"];
out tags center bb;
"""


def build_children_query(
    parent_osm_id: int,
    admin_level: int,
) -> str:
    country_filter = ""
    if admin_level == 4:
        country_filter = '["ISO3166-2"~"^DE-"]'

    return f"""
[out:json][timeout:60];
relation({parent_osm_id});
map_to_area->.parent;
relation(area.parent)
    ["boundary"="administrative"]
    ["admin_level"="{admin_level}"]
    {country_filter};
out tags center bb;
"""


def import_germany() -> None:
    print("Suche Deutschland ...")

    country_data = query_overpass_regions(
        build_country_query(),
        "germany",
    )

    countries = country_data.get("elements", [])

    germany = next(
        (
            element
            for element in countries
            if element.get("tags", {}).get("ISO3166-1") == "DE"
        ),
        None,
    )

    if germany is None:
        raise RuntimeError(
            "Deutschland wurde nicht gefunden."
        )

    germany_tags = germany.get("tags", {})

    print(
        f"Deutschland gefunden: "
        f"{germany_tags.get('name')} "
        f"(OSM {germany['id']})"
    )

    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT id
            FROM regions
            WHERE osm_type = ? AND osm_id = ?
            """,
            ("relation", germany["id"]),
        ).fetchone()

        if existing:
            germany_id = existing[0]
        else:
            germany_id = insert_region(
                connection,
                germany_tags.get("name", "Deutschland"),
                "country",
                None,
                "relation",
                germany["id"],
            )
            connection.commit()

    import_children(
        germany["id"],
        germany_id,
        4,
        "state",
    )


def import_state_regions(
    state_osm_id: int,
    state_db_id: int,
) -> None:
    query = f"""
[out:json][timeout:120];
relation({state_osm_id});
map_to_area->.state_area;
rel(area.state_area)
    ["boundary"="administrative"]
    ["admin_level"="6"]
    ->.districts;

foreach.districts->.district(
    .district out tags center bb;
    .district map_to_area -> .district_area;
    rel(area.district_area)
        ["boundary"="administrative"]
        ["admin_level"="8"];
    out tags center bb;
);
"""

    cache_key = f"admin_{state_osm_id}_6"
    data = query_overpass_regions(query, cache_key)
    elements = data.get("elements", [])

    districts = [
        element
        for element in elements
        if (
            element.get("type") == "relation"
            and element.get("tags", {}).get("admin_level") == "6"
        )
    ]

    print(
        f"  Kreise: {len(districts)}"
    )

    current_district_id = None
    municipality_count = 0

    for element in elements:
        if element.get("type") != "relation":
            continue

        tags = element.get("tags", {})
        name = tags.get("name")

        if not name:
            continue

        admin_level = tags.get("admin_level")

        if admin_level == "6":
            current_district_id = None

            with get_connection() as connection:
                existing = connection.execute(
                    """
                    SELECT id
                    FROM regions
                    WHERE osm_type = ? AND osm_id = ?
                    """,
                    ("relation", element["id"]),
                ).fetchone()

                if existing:
                    current_district_id = existing[0]

                    bounds = element.get("bounds", {})

                    connection.execute(
                        """
                        UPDATE regions
                        SET
                            min_lat = ?,
                            min_lon = ?,
                            max_lat = ?,
                            max_lon = ?
                        WHERE id = ?
                        """,
                        (
                            bounds.get("minlat"),
                            bounds.get("minlon"),
                            bounds.get("maxlat"),
                            bounds.get("maxlon"),
                            current_district_id,
                        ),
                    )
                    connection.commit()

                    print(
                        f"  Kreis: {name}"
                    )
                    print(
                        f"    BBox aktualisiert: {name}"
                    )
                else:
                    bounds = element.get("bounds", {})

                    current_district_id = insert_region(
                        connection,
                        name,
                        "district",
                        state_db_id,
                        "relation",
                        element["id"],
                        bounds.get("minlat"),
                        bounds.get("minlon"),
                        bounds.get("maxlat"),
                        bounds.get("maxlon"),
                    )
                    connection.commit()

                    print(
                        f"  Kreis: {name}"
                    )
                    print(
                        f"    Gespeichert: {name}"
                    )

        elif admin_level == "8":
            if current_district_id is None:
                print(
                    f"    WARNUNG: Gemeinde ohne Kreiszuordnung: "
                    f"{name}"
                )
                continue

            with get_connection() as connection:
                existing = connection.execute(
                    """
                    SELECT id
                    FROM regions
                    WHERE osm_type = ? AND osm_id = ?
                    """,
                    ("relation", element["id"]),
                ).fetchone()

                if existing:
                    municipality_id = existing[0]

                    bounds = element.get("bounds", {})

                    connection.execute(
                        """
                        UPDATE regions
                        SET
                            min_lat = ?,
                            min_lon = ?,
                            max_lat = ?,
                            max_lon = ?
                        WHERE id = ?
                        """,
                        (
                            bounds.get("minlat"),
                            bounds.get("minlon"),
                            bounds.get("maxlat"),
                            bounds.get("maxlon"),
                            municipality_id,
                        ),
                    )
                    connection.commit()

                    print(
                        f"    BBox aktualisiert: {name}"
                    )
                else:
                    bounds = element.get("bounds", {})

                    insert_region(
                        connection,
                        name,
                        "municipality",
                        current_district_id,
                        "relation",
                        element["id"],
                        bounds.get("minlat"),
                        bounds.get("minlon"),
                        bounds.get("maxlat"),
                        bounds.get("maxlon"),
                    )
                    connection.commit()

                    municipality_count += 1

                    print(
                        f"    Gespeichert: {name}"
                    )

    print(
        f"  Gemeinden neu gespeichert: "
        f"{municipality_count}"
    )


def import_children(
    parent_osm_id: int,
    parent_db_id: int,
    admin_level: int,
    region_type: str,
    parent_number: int | None = None,
    parent_total: int | None = None,
) -> None:
    data = query_overpass_regions(
        build_children_query(
            parent_osm_id,
            admin_level,
        ),
        f"admin_{parent_osm_id}_{admin_level}",
    )

    elements = data.get("elements", [])

    print(
        f"  admin_level={admin_level}: "
        f"{len(elements)} Regionen"
    )

    for number, element in enumerate(elements, start=1):
        tags = element.get("tags", {})
        name = tags.get("name")

        if not name:
            continue

        if admin_level == 4:
            print(
                f"Bundesland {number}/{len(elements)}: {name}"
            )
        elif admin_level == 6:
            parent_name = "unbekannt"

            with get_connection() as connection:
                parent_row = connection.execute(
                    """
                    SELECT name
                    FROM regions
                    WHERE id = ?
                    """,
                    (parent_db_id,),
                ).fetchone()

            if parent_row is not None:
                parent_name = parent_row[0]

            print(
                f"Bundesland {parent_number}/{parent_total}: "
                f"{parent_name}"
            )
            print(
                f"  Kreis {number}/{len(elements)}: {name}"
            )

        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM regions
                WHERE osm_type = ? AND osm_id = ?
                """,
                ("relation", element["id"]),
            ).fetchone()

            if existing:
                db_id = existing[0]
                print(
                    f"    Bereits vorhanden: {name}"
                )
                if admin_level == 4:
                    print(
                        f"    Lade Kreise und Gemeinden für {name} ..."
                    )
            else:
                bounds = element.get("bounds", {})

                db_id = insert_region(
                    connection,
                    name,
                    region_type,
                    parent_db_id,
                    "relation",
                    element["id"],
                    bounds.get("minlat"),
                    bounds.get("minlon"),
                    bounds.get("maxlat"),
                    bounds.get("maxlon"),
                )
                connection.commit()

                print(
                    f"    Gespeichert: {name}"
                )

        if admin_level == 4:
            print(
                f"    Lade Kreise und Gemeinden für {name} ..."
            )
            import_state_regions(
                element["id"],
                db_id,
            )
        elif admin_level == 6:
            print(
                f"    Lade Gemeinden für {name} ..."
            )
            import_children(
                element["id"],
                db_id,
                8,
                "municipality",
            )
