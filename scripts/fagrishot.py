"""Isolated entry point shared by the desktop launcher and local command-line tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fagrishot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
