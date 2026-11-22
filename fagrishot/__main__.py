"""FagriShot local input, build and launch commands."""
import argparse
import configparser
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from . import UPSTREAM, VERSION
from .build import CATALOG, EXE, PRIVATE, ROOT, VENV, build_game, sha, source_state
from .inputs import check_executable, check_support, inspect_exefs, inspect_inputs
from .storage import build_lock, check_separation, locations, write_json


def selections(opts, dirs):
    config = dirs["settings"] / "launcher.json"
    settings = json.loads(config.read_text("utf-8")) if config.exists() else {}
    if not isinstance(settings, dict) or settings.get("schema", 1) != 1:
        raise ValueError(f"Unsupported settings format in {config}.")
    return {k: Path(getattr(opts, k, None) or settings.get(k) or ROOT / k).absolute()
            for k in ("exefs", "romfs")}


def parser():
    p = argparse.ArgumentParser(prog="FagriShot", description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status").add_argument("--json", action="store_true")
    commands.add_parser("doctor")
    commands.add_parser("backups")
    commands.add_parser("backup")
    commands.add_parser("restore").add_argument("backup_id")
    for cmd in ("configure", "inspect", "build-code", "prepare", "play"):
        sub = commands.add_parser(cmd)
        sub.add_argument("--exefs", type=Path)
        sub.add_argument("--romfs", type=Path)
        if cmd in ("inspect", "prepare", "play"):
            sub.add_argument("--base-cnmt", type=Path)
            sub.add_argument("--update-cnmt", type=Path)
        if cmd in ("build-code", "prepare"):
            sub.add_argument("--jobs", type=int, choices=range(1,33), metavar="1..32",
                             default=min(8, os.cpu_count() or 1))
    return p


def diagnostic(dirs, paths):
    print(f"FagriShot {VERSION} | {platform.platform()} | {platform.machine()}")
    print(f"Python {platform.python_version()} | {os.cpu_count()} logical processors")
    print(f"Engine {UPSTREAM}")
    for name, path in paths.items():
        print(f"{name}: {path} ({'directory present' if path.is_dir() else 'missing'})")
    for name, path in dirs.items():
        print(f"{name}: {path}")
    runtime = ROOT / "runtime-bin/fefw_rt.dll"
    if not runtime.is_file():
        runtime = ROOT / "work/runtime-release/fefw_rt.dll"
    for label, path in (("Game Python", VENV), ("Runtime DLL", runtime),
                        ("Local LLVM", ROOT/"work/toolchain/llvm-23.1.5/bin/clang-cl.exe"),
                        ("Game executable", EXE)):
        print(f"{label}: {path if path.is_file() else 'not built or installed here'}")
    print("Updates: manual. Online application updates are unavailable.")


def ensure_exe(inv):
    stamp = PRIVATE / "linked.json"
    if not stamp.is_file() or not EXE.is_file():
        raise ValueError("Build the six game modules with build-code first.")
    record = json.loads(stamp.read_text("utf-8"))
    if record.get("input", {}).get("exefs") != inv["exefs"] or record.get("exe_sha256") != sha(EXE):
        raise ValueError("The linked game differs from this ExeFS or has changed. Run build-code again.")
    if record.get("source") != source_state() or inspect_exefs(PRIVATE / "prepared-exefs")["exefs"] != record.get("prepared_exefs"):
        raise ValueError("The runtime source or prepared module set changed. Run build-code again.")
    return record


def launch(paths, inv, dirs):
    from .shaders import require_ready
    cache = require_ready(inv, dirs)
    ini_file = dirs["settings"] / "fefw.ini"
    ini = configparser.RawConfigParser()
    if ini_file.exists():
        with ini_file.open(encoding="utf-8") as stream:
            ini.read_file(stream)
    else:
        ini.read_dict({"fefw": {"fps60": "0", "output": "console"}, "boot": {"gfx": "window"}})
    managed = {"fs": {"exefs": PRIVATE / "prepared-exefs", "romfs": paths["romfs"],
                      "save_dir": dirs["saves"], "dll_mods": "false", "mods": ""},
               "nvn": {"shader_cache": cache / "spirv", "bc7_cache": cache / "bc7",
                       "pipeline_cache": cache / "pipeline_cache.bin"},
               "log": {"path": dirs["logs"] / "FagriShot.log"}}
    for section, values in managed.items():
        if not ini.has_section(section):
            ini.add_section(section)
        for key, value in values.items():
            ini.set(section, key, value.as_posix() if isinstance(value, Path) else value)
    tmp = ini_file.with_suffix(".new")
    with tmp.open("w", encoding="utf-8") as stream:
        ini.write(stream)
    tmp.replace(ini_file)
    from .saves import snapshot
    snapshot(dirs)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("NXRT_", "FEFW_"))}
    subprocess.run([EXE, "--ini", ini_file], cwd=dirs["settings"], env=env, check=True)


def execute(opts):
    dirs = locations()
    paths = selections(opts, dirs)
    cmd = opts.command
    source_checkout = (ROOT / "recomp/games/fefw/config.py").is_file()
    if cmd == "status":
        from .saves import list_backups
        result = {"version": VERSION, **{k: str(p) for k,p in paths.items()},
                  "exefs_present": (paths["exefs"] / "main").is_file(), "romfs_present": paths["romfs"].is_dir(),
                  "settings": str(dirs["settings"]), "saves": str(dirs["saves"]),
                  "logs": str(dirs["logs"]), "exe_built": EXE.is_file() and (PRIVATE / "linked.json").is_file(),
                  "source_checkout": source_checkout, "backup_ids": list_backups(dirs),
                  "has_pair": bool(json.loads(CATALOG.read_text("utf-8"))["supported"])}
        print(json.dumps(result, ensure_ascii=False, indent=None if opts.json else 2))
        return
    if cmd == "doctor":
        diagnostic(dirs, paths)
        return
    if cmd in ("backup", "backups", "restore"):
        from .saves import list_backups, recover, restore, snapshot
        with build_lock(dirs["settings"] / "save.lock"):
            recover(dirs)
            if cmd == "backups":
                print("\n".join(list_backups(dirs)) or "No backups yet.")
            elif cmd == "backup":
                print(snapshot(dirs) or "There are no FagriShot saves to back up.")
            else:
                restore(dirs, opts.backup_id)
                print("Restored the verified snapshot.")
        return
    check_separation(paths.values(), [*dirs.values(), ROOT / "work", ROOT / "tools", ROOT / "game-build"])
    if cmd == "configure":
        for directory in dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
        write_json(dirs["settings"] / "launcher.json", {"schema": 1, **{k: str(v) for k,v in paths.items()}})
        print("Saved the two input locations.")
        return
    if cmd == "build-code":
        if not source_checkout:
            raise ValueError("Build game code from the FagriShot source checkout. See SOURCE.txt in this tools package.")
        print("Checking the ExeFS against update 131072.", flush=True)
        inv = inspect_exefs(paths["exefs"])
        check_executable(inv, CATALOG)
        with build_lock(PRIVATE / "build.lock"):
            build_game(paths["exefs"], inv, opts.jobs)
        return
    print("Reading the selected ExeFS and RomFS. Fingerprints cover every file.", flush=True)
    inv = inspect_inputs(**paths, base_cnmt=opts.base_cnmt, update_cnmt=opts.update_cnmt)
    write_json(dirs["logs"] / "input-inventory.json", inv)
    candidate = check_executable(inv, CATALOG)
    print(f"Executable modules match {candidate['version']}. Inventory saved to {dirs['logs'] / 'input-inventory.json'}.", flush=True)
    check_support(inv, CATALOG)
    if cmd == "inspect":
        print("Executable and content fingerprints match the compatibility record.")
        return
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    with build_lock(PRIVATE / "build.lock"):
        ensure_exe(inv)
        if cmd == "prepare":
            from .shaders import prepare_shaders
            prepare_shaders(paths, inv, dirs, opts.jobs)
        else:
            from .saves import recover
            with build_lock(dirs["settings"] / "save.lock"):
                recover(dirs)
                launch(paths, inv, dirs)


def main():
    if sys.platform == "win32":
        # Pipes and redirected files otherwise use the legacy Windows code page.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure") and not stream.isatty():
                stream.reconfigure(encoding="utf-8")
    try:
        execute(parser().parse_args())
        return 0
    except KeyboardInterrupt:
        print("Cancelled. Rerun the interrupted stage.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, configparser.Error, subprocess.CalledProcessError) as exc:
        print(f"FagriShot: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
