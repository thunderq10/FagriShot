# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile and exercise the direct game-entry helper on Windows without game modules."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work/cpu"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix="entry-", dir=WORK))
    root = build / "example checkout with spaces"
    private = root / "work/private/game"
    tools = root / "work/tools"
    decoy = build / "other directory"
    for folder in (private, tools, decoy, root / "compatibility", root / "fagrishot"):
        folder.mkdir(parents=True)
    fixture = private / "entry_probe.exe"
    compiler = Path(os.environ.get("NX_LLVM", ROOT / "work/toolchain/llvm-23.1.5/bin")) / "clang++.exe"
    diag = ROOT / "runtime/src/diag"
    cmd = [compiler, "-std=c++20", "-O1", "-D_CRT_SECURE_NO_WARNINGS", "-I",
           ROOT / "runtime/include", "-I", ROOT / "game-build/fefw/src", diag / "config.cpp",
           diag / "ini_merge.cpp", diag / "log.cpp",
           ROOT / "tests/cpu/entry_probe.cpp", "-o", fixture]
    subprocess.run(cmd, check=True, cwd=build)
    environment = {**os.environ, "NXRT_FS_SAVE_DIR": str(decoy), "PATH": str(decoy) + ";" + os.environ["PATH"]}
    log = []

    def run(args, code, contains=None):
        result = subprocess.run([fixture, *args], env=environment, cwd=decoy, text=True, capture_output=True, timeout=60)
        output = (result.stdout + result.stderr).strip()
        log.append(f"{args} -> {result.returncode} {output}")
        if result.returncode != code or (contains and contains not in output):
            raise RuntimeError(log[-1])

    def check_start(folder):
        receipt = folder / "launcher-started.txt"
        for _ in range(100):
            if receipt.is_file() and receipt.read_text() == "--play-game":
                return
            time.sleep(0.05)
        raise RuntimeError(f"Expected the launcher to run in {folder}")

    name = "FagriShotLauncher.exe"
    shutil.copyfile(fixture, decoy / name)
    shutil.copyfile(fixture, tools / name)
    run([], 2, "FagriShotLauncher.exe")  # Unrelated trees and PATH cannot supply the launcher.
    (root / "compatibility/supported.json").write_text("{}")
    (root / "fagrishot/__main__.py").write_text("")
    run([], 0)
    check_start(tools)
    shutil.copyfile(fixture, root / name)
    (root / "player-package.json").write_text('{"schema":1,"product":"FagriShot"}')
    run([], 0)
    check_start(root)
    shutil.copyfile(fixture, private / name)
    run([], 0)
    check_start(private)
    if (decoy / "launcher-started.txt").exists():
        raise RuntimeError("Used the working directory or PATH to locate the launcher.")
    ini = build / "diagnostic.ini"
    run(["ini", ini], 2, "readable")
    for contents in ("[fs]\n", "[fs]\nsave_dir=\n", "[fs]\nsave_dir=relative/saves\n"):
        ini.write_text(contents)
        run(["ini", ini], 2, "absolute")
    saves = build / "isolated saves"
    ini.write_text(f"[fs]\nsave_dir={saves}\n", encoding="utf-8")
    run(["ini", ini], 0, str(saves))
    log.append("PASS direct entry and explicit save isolation")
    (build / "verification.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log))
    print(f"Entry test evidence: {build}")


if __name__ == "__main__":
    main()
