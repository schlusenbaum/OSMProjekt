import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import PROJECT_ROOT, load_config


CACHE_DIR = PROJECT_ROOT / "cache" / "osm"
CACHE_SCHEMA_VERSION = 1


def get_cache_file(tile: dict) -> Path:
    return CACHE_DIR / (
        f"{tile['min_lat']:.5f}_"
        f"{tile['min_lon']:.5f}_"
        f"{tile['max_lat']:.5f}_"
        f"{tile['max_lon']:.5f}.json"
    )


def create_cache_version(query: str) -> str:
    return hashlib.sha256(
        query.encode("utf-8")
    ).hexdigest()


def is_cache_valid(
    cache_file: Path,
    cache_version: str,
) -> bool:
    if not cache_file.exists():
        return False

    config = load_config()
    ttl_days = config["cache"]["ttl_days"]

    modified = datetime.fromtimestamp(
        cache_file.stat().st_mtime,
        tz=timezone.utc,
    )

    expires = modified + timedelta(days=ttl_days)

    if datetime.now(timezone.utc) >= expires:
        return False

    try:
        with cache_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return False

    metadata = data.get("_meta", {})

    return (
        metadata.get("schema_version")
        == CACHE_SCHEMA_VERSION
        and metadata.get("cache_version")
        == cache_version
    )


def load_cache(
    tile: dict,
    cache_version: str,
    allow_expired: bool = False,
) -> dict | None:
    cache_file = get_cache_file(tile)

    if not cache_file.exists():
        return None

    try:
        with cache_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None

    metadata = data.get("_meta", {})

    if (
        metadata.get("schema_version")
        != CACHE_SCHEMA_VERSION
        or metadata.get("cache_version")
        != cache_version
    ):
        return None

    if allow_expired:
        return data

    config = load_config()
    ttl_days = config["cache"]["ttl_days"]

    modified = datetime.fromtimestamp(
        cache_file.stat().st_mtime,
        tz=timezone.utc,
    )

    expires = modified + timedelta(days=ttl_days)

    if datetime.now(timezone.utc) >= expires:
        return None

    return data


def save_cache(
    tile: dict,
    data: dict,
    cache_version: str,
) -> None:
    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_file = get_cache_file(tile)

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
