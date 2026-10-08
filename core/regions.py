import sqlite3
from pathlib import Path

from .config import PROJECT_ROOT


REGIONS_DB = PROJECT_ROOT / "regions" / "regions.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(REGIONS_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_region(region_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM regions
            WHERE id = ?
            """,
            (region_id,),
        ).fetchone()

    return dict(row) if row else None


def find_regions(
    name: str | None = None,
    region_type: str | None = None,
    parent_id: int | None = None,
    enabled_only: bool = False,
) -> list[dict]:
    query = "SELECT * FROM regions WHERE 1=1"
    parameters = []

    if name is not None:
        query += " AND name = ?"
        parameters.append(name)

    if region_type is not None:
        query += " AND type = ?"
        parameters.append(region_type)

    if parent_id is not None:
        query += " AND parent_id = ?"
        parameters.append(parent_id)

    if enabled_only:
        query += " AND enabled = 1"

    query += " ORDER BY name"

    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [dict(row) for row in rows]


def get_children(parent_id: int) -> list[dict]:
    return find_regions(parent_id=parent_id)

def get_region_path(region_id: int) -> list[dict]:
    """Liefert die Region und alle Elternregionen bis zum obersten Eintrag."""
    path = []
    current_id = region_id

    while current_id is not None:
        region = get_region(current_id)
        if region is None:
            break

        path.append(region)
        current_id = region["parent_id"]

    path.reverse()
    return path


def get_enabled_regions() -> list[dict]:
    return find_regions(enabled_only=True)
