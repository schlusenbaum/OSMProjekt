import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, load_config
from core.overpass import query_overpass


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

    print("  Overpass: Regions-Abfrage wird zentral ausgeführt.")

    data = query_overpass(query)

    save_cached_query(
        cache_key,
        data,
        cache_version,
    )

    return data


def ensure_schema() -> None:
    """Stellt sicher, dass die Regionsdatenbank vollständig initialisiert ist."""
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS regions (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                parent_id INTEGER,
                osm_type TEXT,
                osm_id INTEGER,
                min_lat REAL,
                min_lon REAL,
                max_lat REAL,
                max_lon REAL,
                enabled INTEGER NOT NULL DEFAULT 0,
                children_imported INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (parent_id) REFERENCES regions(id)
            )
            """
        )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_regions_name "
            "ON regions(name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_regions_osm "
            "ON regions(osm_type, osm_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_regions_parent "
            "ON regions(parent_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_regions_type "
            "ON regions(type)"
        )

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(regions)"
            )
        }

        if "children_imported" not in columns:
            connection.execute(
                """
                ALTER TABLE regions
                ADD COLUMN children_imported INTEGER NOT NULL DEFAULT 0
                """
            )
            print("children_imported wurde zur bestehenden DB hinzugefügt.")


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(REGIONS_DB)
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )
    return connection


def get_relevant_admin_levels(country_code: str) -> list[int]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT admin_level
            FROM admin_level_definitions
            WHERE country_code = ?
              AND relevant = 1
            ORDER BY admin_level
            """,
            (country_code.upper(),),
        ).fetchall()

    return [row[0] for row in rows]


def get_next_relevant_admin_level(
    country_code: str,
    current_admin_level: int,
) -> int | None:
    levels = get_relevant_admin_levels(country_code)

    for level in levels:
        if level > current_admin_level:
            return level

    return None


def get_region_type(admin_level: int) -> str:
    if admin_level == 2:
        return "country"

    return f"admin_level_{admin_level}"


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


def build_country_query(country_code: str) -> str:
    return f"""
[out:json][timeout:60];
relation
    ["boundary"="administrative"]
    ["admin_level"="2"]
    ["ISO3166-1"="{country_code}"];
out tags center bb;
"""


def build_children_query(
    parent_osm_id: int,
    admin_level: int,
    country_code: str,
) -> str:
    country_filter = ""
    if admin_level == 4:
        country_filter = f'["ISO3166-2"~"^{country_code}-"]'

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


def build_combined_children_query(
    parent_osm_id: int,
    admin_levels: list[int],
    country_code: str,
) -> str:
    if len(admin_levels) < 1:
        raise ValueError("Keine relevanten admin_level vorhanden.")

    first_level = admin_levels[0]

    country_filter = ""
    if first_level == 4:
        country_filter = (
            f'["ISO3166-2"~"^{country_code.upper()}-"]'
        )

    query = f"""
[out:json][timeout:120];
relation({parent_osm_id});
map_to_area->.parent_area;

rel(area.parent_area)
    ["boundary"="administrative"]
    ["admin_level"="{first_level}"]
    {country_filter}
    ->.level_{first_level};

foreach.level_{first_level}->.parent_relation(
    .parent_relation out tags center bb;
"""

    def append_children(level_index: int, indent: str = "    ") -> str:
        if level_index >= len(admin_levels):
            return ""

        current_level = admin_levels[level_index]

        result = f"""
{indent}.parent_relation map_to_area ->.parent_area;
{indent}rel(area.parent_area)
{indent}    ["boundary"="administrative"]
{indent}    ["admin_level"="{current_level}"]
{indent}    ->.level_{current_level};
"""

        if level_index == len(admin_levels) - 1:
            result += f"""
{indent}.level_{current_level} out tags center bb;
"""
            return result

        result += f"""
{indent}foreach.level_{current_level}->.parent_relation(
{indent}    .parent_relation out tags center bb;
"""

        result += append_children(
            level_index + 1,
            indent + "    ",
        )

        result += f"""
{indent});
"""

        return result

    query += append_children(1)

    query += """
);
"""

    return query


def import_country(country_code: str) -> None:
    print(f"Suche Land {country_code.upper()} ...")

    country_data = query_overpass_regions(
        build_country_query(country_code),
        f"country_{country_code.lower()}",
    )

    countries = country_data.get("elements", [])

    country = next(
        (
            element
            for element in countries
            if element.get("tags", {}).get("ISO3166-1") == country_code.upper()
        ),
        None,
    )

    if country is None:
        raise RuntimeError(
            f"Land mit ISO-Code {country_code.upper()} wurde nicht gefunden."
        )

    country_tags = country.get("tags", {})

    print(
        f"{country_tags.get('name', country_code.upper())} gefunden: "
        f"(OSM {country['id']})"
    )

    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT id
            FROM regions
            WHERE osm_type = ? AND osm_id = ?
            """,
            ("relation", country["id"]),
        ).fetchone()

        if existing:
            country_id = existing[0]
        else:
            country_id = insert_region(
                connection,
                country_tags.get("name", country_code.upper()),
                "country",
                None,
                "relation",
                country["id"],
            )
            connection.commit()

    admin_levels = get_relevant_admin_levels(country_code)

    if not admin_levels:
        raise RuntimeError(
            f"Keine relevanten admin_level für {country_code.upper()} gefunden."
        )

    first_admin_level = admin_levels[0]

    print(
        f"Starte Import mit admin_level={first_admin_level}"
    )

    import_children(
        country["id"],
        country_id,
        first_admin_level,
        get_region_type(first_admin_level),
        country_code=country_code,
    )


def import_state_regions(
    state_osm_id: int,
    state_db_id: int,
    country_code: str = "DE",
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
    country_code: str = "DE",
) -> None:
    admin_levels = get_relevant_admin_levels(country_code)

    if admin_level not in admin_levels:
        raise RuntimeError(
            f"admin_level={admin_level} ist für "
            f"{country_code.upper()} nicht relevant."
        )

    start_index = admin_levels.index(admin_level)
    first_level = admin_levels[start_index]
    remaining_levels = admin_levels[start_index + 1:]

    query = build_children_query(
        parent_osm_id,
        first_level,
        country_code,
    )

    cache_key = f"admin_{parent_osm_id}_{first_level}"

    try:
        data = query_overpass_regions(
            query,
            cache_key,
        )
    except RuntimeError as error:
        print(
            f"  Überspringe Regionsimport für "
            f"OSM {parent_osm_id}: {error}"
        )
        return

    elements = data.get("elements", [])

    print(
        f"  admin_level={first_level}: "
        f"{len(elements)} Regionen"
    )

    for number, element in enumerate(elements, start=1):
        if element.get("type") != "relation":
            continue

        tags = element.get("tags", {})
        name = tags.get("name")

        if not name:
            continue

        bounds = element.get("bounds", {})

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

                connection.execute(
                    """
                    UPDATE regions
                    SET
                        name = ?,
                        type = ?,
                        parent_id = ?,
                        min_lat = ?,
                        min_lon = ?,
                        max_lat = ?,
                        max_lon = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        region_type,
                        parent_db_id,
                        bounds.get("minlat"),
                        bounds.get("minlon"),
                        bounds.get("maxlat"),
                        bounds.get("maxlon"),
                        db_id,
                    ),
                )
                connection.commit()

                print(
                    f"  Bereits vorhanden: {name}"
                )
            else:
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
                    f"  Gespeichert: {name}"
                )

        if not remaining_levels:
            continue

        query = build_combined_children_query(
            element["id"],
            remaining_levels,
            country_code,
        )

        cache_key = (
            f"admin_{element['id']}_{remaining_levels[0]}"
        )

        try:
            nested_data = query_overpass_regions(
                query,
                cache_key,
            )
        except RuntimeError as error:
            print(
                f"  Überspringe Unterregionen für "
                f"{name}: {error}"
            )
            continue

        nested_elements = nested_data.get("elements", [])

        print(
            f"  {name}: kombinierte Abfrage "
            f"{remaining_levels} → "
            f"{len(nested_elements)} Elemente"
        )

        current_parent_ids = {
            first_level: db_id
        }

        for nested_element in nested_elements:
            if nested_element.get("type") != "relation":
                continue

            nested_tags = nested_element.get("tags", {})
            nested_name = nested_tags.get("name")
            nested_admin_level = nested_tags.get("admin_level")

            if not nested_name or not nested_admin_level:
                continue

            try:
                current_level = int(nested_admin_level)
            except ValueError:
                continue

            if current_level not in remaining_levels:
                continue

            level_index = remaining_levels.index(current_level)

            if level_index == 0:
                nested_parent_id = db_id
            else:
                parent_level = remaining_levels[level_index - 1]
                nested_parent_id = current_parent_ids.get(parent_level)

            if nested_parent_id is None:
                print(
                    f"  WARNUNG: Elternregion für "
                    f"{nested_name} "
                    f"(admin_level={current_level}) "
                    f"nicht gefunden."
                )
                continue

            nested_region_type = get_region_type(current_level)
            nested_bounds = nested_element.get("bounds", {})

            with get_connection() as connection:
                existing = connection.execute(
                    """
                    SELECT id
                    FROM regions
                    WHERE osm_type = ? AND osm_id = ?
                    """,
                    ("relation", nested_element["id"]),
                ).fetchone()

                if existing:
                    nested_db_id = existing[0]

                    connection.execute(
                        """
                        UPDATE regions
                        SET
                            name = ?,
                            type = ?,
                            parent_id = ?,
                            min_lat = ?,
                            min_lon = ?,
                            max_lat = ?,
                            max_lon = ?
                        WHERE id = ?
                        """,
                        (
                            nested_name,
                            nested_region_type,
                            nested_parent_id,
                            nested_bounds.get("minlat"),
                            nested_bounds.get("minlon"),
                            nested_bounds.get("maxlat"),
                            nested_bounds.get("maxlon"),
                            nested_db_id,
                        ),
                    )
                    connection.commit()
                else:
                    nested_db_id = insert_region(
                        connection,
                        nested_name,
                        nested_region_type,
                        nested_parent_id,
                        "relation",
                        nested_element["id"],
                        nested_bounds.get("minlat"),
                        nested_bounds.get("minlon"),
                        nested_bounds.get("maxlat"),
                        nested_bounds.get("maxlon"),
                    )
                    connection.commit()

            current_parent_ids[current_level] = nested_db_id

            for deeper_level in remaining_levels[level_index + 1:]:
                current_parent_ids.pop(deeper_level, None)
