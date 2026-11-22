# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the local CPython environment accepted by the upstream game build."""

import os
from pathlib import Path
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / "tools" / ".venv"


def main():
    if os.name != "nt" or sys.version_info[:2] != (3, 13) or sysconfig.get_platform() != "win-amd64":
        raise SystemExit("Run this setup with Windows x64 CPython 3.13.")
    python = VENV / "Scripts" / "python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-I", "-m", "venv", VENV], check=True)
    manifest = Path(__file__).with_name("requirements-win64-py313.txt")
    subprocess.run([python, "-I", "-m", "pip", "--disable-pip-version-check", "install",
                    "--no-deps", "--only-binary=:all:", "--require-hashes", "-r", manifest], check=True)
    subprocess.run([python, "-I", "-m", "pip", "check"], check=True)
    subprocess.run([python, "-I", "-c",
                    "import capstone, elftools, lz4.block, unicorn, zbic, zstandard; "
                    "from importlib.metadata import version; "
                    "print('  '.join(n + ' ' + version(n) for n in "
                    "('capstone', 'pyelftools', 'lz4', 'unicorn', 'zbic', 'zstandard')))"], check=True)
    print(f"Game build interpreter: {python}")


if __name__ == "__main__":
    main()
