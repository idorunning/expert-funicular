"""Entry point: python -m brokerledger."""
from __future__ import annotations

import sys

from .cli import run_cli


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"--demo", "--cli", "--bootstrap"}:
        return run_cli(sys.argv[1:])
    # GUI is the default mode.
    from .app import run
    return run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
