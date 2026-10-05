import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def create_gpx() -> ET.Element:
    return ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "OSMProjekt",
            "xmlns": GPX_NAMESPACE,
        },
    )


def add_waypoint(
    gpx: ET.Element,
    latitude: float,
    longitude: float,
    name: str | None = None,
    icon: str | None = None,
) -> ET.Element:
    waypoint = ET.SubElement(
        gpx,
        "wpt",
        {
            "lat": str(latitude),
            "lon": str(longitude),
        },
    )

    if name:
        name_element = ET.SubElement(waypoint, "name")
        name_element.text = name

    if icon:
        sym_element = ET.SubElement(waypoint, "sym")
        sym_element.text = icon

    return waypoint


def add_metadata(
    gpx: ET.Element,
    name: str | None = None,
    description: str | None = None,
    author: str | None = None,
) -> ET.Element:
    metadata = ET.SubElement(gpx, "metadata")

    if name:
        name_element = ET.SubElement(metadata, "name")
        name_element.text = name

    if description:
        description_element = ET.SubElement(metadata, "desc")
        description_element.text = description

    if author:
        author_element = ET.SubElement(metadata, "author")
        author_name = ET.SubElement(author_element, "name")
        author_name.text = author

    time_element = ET.SubElement(metadata, "time")
    time_element.text = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    return metadata


def add_track(
    gpx: ET.Element,
    points: list[tuple[float, float]],
    name: str | None = None,
    description: str | None = None,
    track_type: str | None = None,
) -> ET.Element:
    track = ET.SubElement(gpx, "trk")

    if name:
        name_element = ET.SubElement(track, "name")
        name_element.text = name

    if description:
        description_element = ET.SubElement(track, "desc")
        description_element.text = description

    if track_type:
        type_element = ET.SubElement(track, "type")
        type_element.text = track_type

    segment = ET.SubElement(track, "trkseg")

    for latitude, longitude in points:
        ET.SubElement(
            segment,
            "trkpt",
            {
                "lat": str(latitude),
                "lon": str(longitude),
            },
        )

    return track


def save_gpx(gpx: ET.Element, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(gpx)

    ET.indent(tree, space="  ")

    tree.write(
        output_file,
        encoding="utf-8",
        xml_declaration=True,
    )
