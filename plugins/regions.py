"""Regions-Plugin für die Initialisierung der Regionsdatenbank."""

import argparse
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PLUGIN_DIR.parent

if str(PLUGIN_DIR) in sys.path:
    sys.path.remove(str(PLUGIN_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

from regions.importer import ensure_schema, import_country


PLUGIN_NAME = "regions"
PLUGIN_DESCRIPTION = "Regionsdatenbank"
PLUGIN_TYPE = "system"
PLUGIN_COMMAND = "regions"


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

def configure_cli(parser: argparse.ArgumentParser) -> None:
    """Registriert die Argumente des Regions-Plugins."""
    actions = parser.add_subparsers(dest="regions_action")
    import_parser = actions.add_parser("import", help="Regionen eines Landes importieren")
    import_parser.add_argument(
        "--country",
        help="Kommagetrennte ISO-Ländercodes, z. B. AT,CH,IT",
    )


def _select_countries_interactive() -> list[str]:
    import sqlite3
    from regions.importer import REGIONS_DB

    with sqlite3.connect(REGIONS_DB) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT country_code, country_name
            FROM admin_level_definitions
            WHERE relevant = 1
            ORDER BY country_name
            """
        ).fetchall()

    if not rows:
        raise RuntimeError("Keine relevanten Länder in admin_level_definitions gefunden.")

    print("\nVerfügbare Länder:\n")
    for number, (country_code, country_name) in enumerate(rows, start=1):
        print(f"  {number:2}: {country_name} ({country_code})")

    while True:
        value = input("\nLänder auswählen (z. B. 1,3,5 oder q zum Beenden): ").strip()
        if value.lower() == "q":
            return []
        try:
            selected = {int(item.strip()) for item in value.split(",") if item.strip()}
            if not selected or any(number < 1 or number > len(rows) for number in selected):
                raise ValueError
            return [rows[number - 1][0].upper() for number in sorted(selected)]
        except ValueError:
            print("Ungültige Auswahl. Bitte Ländernummern eingeben.")


def run_cli(args: argparse.Namespace) -> None:
    """Führt die Regionsverwaltung eigenständig aus."""
    initialize()

    if args.regions_action == "import":
        countries = (
            [country.strip().upper() for country in args.country.split(",") if country.strip()]
            if args.country
            else _select_countries_interactive()
        )
        for country in countries:
            run("import", country)
        return

    from core.cli import select_region_interactive
    select_region_interactive()


def main() -> None:
    parser = argparse.ArgumentParser(description=PLUGIN_DESCRIPTION)
    configure_cli(parser)
    run_cli(parser.parse_args())


if __name__ == "__main__":
    main()
