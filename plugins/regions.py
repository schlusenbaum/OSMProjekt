"""Regions-Plugin für die Initialisierung der Regionsdatenbank."""

from regions.importer import ensure_schema, import_country


PLUGIN_NAME = "regions"
PLUGIN_DESCRIPTION = "Regionsdatenbank"
PLUGIN_TYPE = "system"


def initialize() -> None:
    """Stellt die Regionsdatenbank bereit."""
    ensure_schema()


def run(action: str, country_code: str | None = None) -> None:
    """Führt eine Regions-Aktion aus."""
    if action == "import":
        if not country_code:
            raise ValueError("Für den Import ist ein Länder-Code erforderlich.")

        import_country(country_code)
        return

    raise ValueError(f"Unbekannte Regions-Aktion: {action}")
