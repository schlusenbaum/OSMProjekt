"""Dynamische Plugin-Entdeckung und generische CLI-Weiterleitung."""

from collections import defaultdict
from importlib import import_module
from pathlib import Path
import argparse


PLUGINS_DIR = Path(__file__).resolve().parent


def get_plugins() -> dict[str, object]:
    """Entdeckt alle Plugins anhand ihrer Metadaten."""
    plugins = {}

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "registry.py":
            continue

        module = import_module(f"plugins.{path.stem}")
        name = getattr(module, "PLUGIN_NAME", path.stem)
        plugins[name] = module

    return plugins


def build_parser() -> argparse.ArgumentParser:
    """Erzeugt die CLI ausschließlich aus den Plugin-Metadaten."""
    parser = argparse.ArgumentParser(description="OSMProjekt")
    subparsers = parser.add_subparsers(dest="command", required=True)
    commands: dict[str, list[object]] = defaultdict(list)

    for plugin in get_plugins().values():
        commands[getattr(plugin, "PLUGIN_COMMAND")].append(plugin)

    for command, plugins in sorted(commands.items()):
        command_parser = subparsers.add_parser(
            command,
            help=", ".join(
                plugin.PLUGIN_DESCRIPTION
                for plugin in plugins
            ),
        )
        command_parser.set_defaults(_plugin_command=command)

        if len(plugins) > 1:
            command_parser.add_argument(
                "--type",
                required=True,
                choices=sorted(plugin.PLUGIN_NAME for plugin in plugins),
                help="Plugin-Typ",
            )

        plugins[0].configure_cli(command_parser)

    return parser


def execute_plugin(args: argparse.Namespace) -> None:
    """Leitet die geparsten Argumente an genau ein Plugin weiter."""
    command = args._plugin_command
    candidates = [
        plugin
        for plugin in get_plugins().values()
        if plugin.PLUGIN_COMMAND == command
    ]
    plugin_name = getattr(args, "type", None)

    if plugin_name is not None:
        candidates = [
            plugin for plugin in candidates
            if plugin.PLUGIN_NAME == plugin_name
        ]

    if len(candidates) != 1:
        raise ValueError(f"Plugin für '{command}' ist nicht eindeutig.")

    candidates[0].run_cli(args)
