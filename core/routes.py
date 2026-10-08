from dataclasses import dataclass
from typing import Any


@dataclass
class RouteItem:
    """Einheitliche Beschreibung einer auswählbaren Route."""

    id: str
    name: str
    description: str = ""
    data: Any = None
    region: Any = None
