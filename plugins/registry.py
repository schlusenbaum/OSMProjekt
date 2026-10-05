from importlib import import_module
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parent


def get_plugins() -> dict[str, object]:
    """Alle verfügbaren Plugins dynamisch entdecken."""
    plugins = {}

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "registry.py":
            continue

        module_name = f"plugins.{path.stem}"
        module = import_module(module_name)
        plugins[path.stem] = module

    return plugins


def get_plugin(name: str):
    """Ein Plugin anhand seines Namens laden."""
    plugins = get_plugins()

    try:
        return plugins[name]
    except KeyError:
        available = ", ".join(sorted(plugins))
        raise ValueError(
            f"Unbekanntes Plugin: {name}. "
            f"Verfügbar: {available}"
        ) from None
