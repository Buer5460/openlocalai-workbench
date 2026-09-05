"""Entry point used by PyInstaller release builds."""

import sys

from openlocalai.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["desktop"]))
