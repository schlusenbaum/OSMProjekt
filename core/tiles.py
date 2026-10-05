from math import ceil


def create_tiles(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    tile_size: float = 0.10,
) -> list[dict]:
    if min_lat >= max_lat:
        raise ValueError("min_lat muss kleiner als max_lat sein")

    if min_lon >= max_lon:
        raise ValueError("min_lon muss kleiner als max_lon sein")

    if tile_size <= 0:
        raise ValueError("tile_size muss größer als 0 sein")

    lat_count = ceil((max_lat - min_lat) / tile_size - 1e-10)
    lon_count = ceil((max_lon - min_lon) / tile_size - 1e-10)

    tiles = []

    for lat_index in range(lat_count):
        tile_min_lat = min_lat + lat_index * tile_size
        tile_max_lat = min(tile_min_lat + tile_size, max_lat)

        for lon_index in range(lon_count):
            tile_min_lon = min_lon + lon_index * tile_size
            tile_max_lon = min(tile_min_lon + tile_size, max_lon)

            tiles.append(
                {
                    "min_lat": tile_min_lat,
                    "min_lon": tile_min_lon,
                    "max_lat": tile_max_lat,
                    "max_lon": tile_max_lon,
                }
            )

    return tiles
