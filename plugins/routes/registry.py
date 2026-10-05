from importlib import import_module
from pathlib import Path


ROUTES_DIR = Path(__file__).resolve().parent


def get_route_plugins() -> dict[str, object]:
    """Alle verfügbaren Route-Plugins dynamisch entdecken."""
    plugins = {}

    for path in sorted(ROUTES_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "registry.py":
            continue

        module_name = f"plugins.routes.{path.stem}"
        module = import_module(module_name)
        plugins[path.stem] = module

    return plugins


def get_route_plugin(name: str):
    """Ein Route-Plugin anhand seines Namens laden."""
    plugins = get_route_plugins()

    try:
        return plugins[name]
    except KeyError:
        available = ", ".join(sorted(plugins))
        raise ValueError(
            f"Unbekanntes Route-Plugin: {name}. "
            f"Verfügbar: {available}"
        ) from None
