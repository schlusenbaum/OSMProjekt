import json
import urllib.error
import urllib.parse
import urllib.request

from .config import load_config


def query_overpass(query: str) -> dict:
    config = load_config()

    servers = config["overpass"]["servers"]
    timeout = config["overpass"]["request_timeout"]

    last_error = None

    for server in servers:
        try:
            data = urllib.parse.urlencode(
                {"data": query}
            ).encode("utf-8")

            request = urllib.request.Request(
                server,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "OSMProjekt/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                response_data = response.read().decode("utf-8")

            return json.loads(response_data)

        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as error:
            last_error = error
            continue

    raise RuntimeError(
        f"Alle Overpass-Server sind fehlgeschlagen: {last_error}"
    )

def query_overpass_retry(
    query: str,
    retry_rounds: int | None = None,
    retry_delay_seconds: int | None = None,
) -> dict:
    """
    Führt eine nicht räumlich teilbare Overpass-Abfrage aus.

    Bei einem Fehler werden die konfigurierten Overpass-Server
    erneut durchlaufen. Nach einer fehlgeschlagenen Runde wird
    optional gewartet und die komplette Serverliste erneut versucht.
    """
    import time

    config = load_config()

    if retry_rounds is None:
        retry_rounds = config["overpass"]["retry_rounds"]

    if retry_delay_seconds is None:
        retry_delay_seconds = config["overpass"]["retry_delay_seconds"]

    last_error = None

    for attempt in range(retry_rounds):
        try:
            return query_overpass(query)
        except RuntimeError as error:
            last_error = error

            if attempt < retry_rounds - 1:
                print(
                    f"  Overpass-Abfrage fehlgeschlagen "
                    f"(Versuch {attempt + 1}/{retry_rounds}). "
                    f"Neuer Versuch in {retry_delay_seconds} Sekunden."
                )
                time.sleep(retry_delay_seconds)

    raise RuntimeError(
        f"Overpass-Abfrage nach {retry_rounds} Versuchen "
        f"fehlgeschlagen: {last_error}"
    )


def query_overpass_adaptive(
    areas: list[dict],
    build_query,
) -> tuple[list[tuple[list[dict], dict]], list[dict]]:
    """
    Führt Overpass-Abfragen adaptiv aus.

    Ein Bereich wird bei einem Fehler so lange geometrisch geteilt,
    bis die Teilbereiche erfolgreich abgefragt werden oder ein
    nicht weiter teilbarer Bereich endgültig fehlschlägt.

    Rückgabe:
        (
            erfolgreiche Gruppen als [(areas, data), ...],
            endgültig fehlgeschlagene Bereiche als [area, ...],
        )
    """

    def get_bbox(
        group: list[dict],
    ) -> tuple[float, float, float, float]:
        return (
            min(area["min_lat"] for area in group),
            min(area["min_lon"] for area in group),
            max(area["max_lat"] for area in group),
            max(area["max_lon"] for area in group),
        )

    def split_group(
        group: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        min_lat, min_lon, max_lat, max_lon = get_bbox(group)

        lat_size = max_lat - min_lat
        lon_size = max_lon - min_lon

        if len(group) == 1:
            area = group[0]

            if lat_size >= lon_size:
                middle = (
                    area["min_lat"] + area["max_lat"]
                ) / 2

                return (
                    [
                        {
                            "min_lat": area["min_lat"],
                            "min_lon": area["min_lon"],
                            "max_lat": middle,
                            "max_lon": area["max_lon"],
                        }
                    ],
                    [
                        {
                            "min_lat": middle,
                            "min_lon": area["min_lon"],
                            "max_lat": area["max_lat"],
                            "max_lon": area["max_lon"],
                        }
                    ],
                )

            middle = (
                area["min_lon"] + area["max_lon"]
            ) / 2

            return (
                [
                    {
                        "min_lat": area["min_lat"],
                        "min_lon": area["min_lon"],
                        "max_lat": area["max_lat"],
                        "max_lon": middle,
                    }
                ],
                [
                    {
                        "min_lat": area["min_lat"],
                        "min_lon": middle,
                        "max_lat": area["max_lat"],
                        "max_lon": area["max_lon"],
                    }
                ],
            )

        if lat_size >= lon_size:
            group = sorted(
                group,
                key=lambda area: (
                    area["min_lat"],
                    area["min_lon"],
                ),
            )
        else:
            group = sorted(
                group,
                key=lambda area: (
                    area["min_lon"],
                    area["min_lat"],
                ),
            )

        middle = len(group) // 2

        return group[:middle], group[middle:]

    def query_group(
        group: list[dict],
    ) -> tuple[list[tuple[list[dict], dict]], list[dict]]:
        if not group:
            return [], []

        min_lat, min_lon, max_lat, max_lon = get_bbox(group)

        query = build_query(
            min_lat,
            min_lon,
            max_lat,
            max_lon,
        )

        try:
            data = query_overpass(query)

            return [
                (group, data),
            ], []

        except RuntimeError as error:
            import time

            retry_enabled = config["cache"]["retry_failed_tiles"]
            retry_rounds = config["overpass"]["retry_rounds"]
            retry_delay = config["overpass"]["retry_delay_seconds"]

            if retry_enabled:
                for retry_round in range(1, retry_rounds + 1):
                    print(
                        f"  Overpass-Gruppe fehlgeschlagen. "
                        f"Retry {retry_round}/{retry_rounds}."
                    )

                    if retry_delay > 0:
                        time.sleep(retry_delay)

                    try:
                        data = query_overpass(query)

                        return [
                            (group, data),
                        ], []

                    except RuntimeError as retry_error:
                        error = retry_error

            min_lat, min_lon, max_lat, max_lon = get_bbox(group)

            if len(group) == 1:
                lat_size = max_lat - min_lat
                lon_size = max_lon - min_lon

                if lat_size <= 0 or lon_size <= 0:
                    print(
                        f"  Overpass-Bereich endgültig fehlgeschlagen: "
                        f"{error}"
                    )
                    return [], group

            first_group, second_group = split_group(group)

            successful_first, failed_first = query_group(
                first_group
            )

            successful_second, failed_second = query_group(
                second_group
            )

            return (
                successful_first + successful_second,
                failed_first + failed_second,
            )

    config = load_config()
    retry_enabled = config["cache"]["retry_failed_tiles"]
    retry_rounds = config["overpass"]["retry_rounds"]
    retry_delay = config["overpass"]["retry_delay_seconds"]

    successful_groups, failed_areas = query_group(areas)

    return successful_groups, failed_areas

