"""Generischer Starter für alle dynamisch entdeckten Plugins."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.registry import build_parser, execute_plugin


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    execute_plugin(args)


if __name__ == "__main__":
    main()
