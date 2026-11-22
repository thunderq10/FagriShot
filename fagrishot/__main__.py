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
from .build import (CATALOG, EXE, PLAYER_PACKAGE, PRIVATE, ROOT, VENV, build_game, sha,
                    source_state, verify_player_installation)
from .inputs import (InputError, check_executable, check_input_roots, check_local_trial, check_support,
                     inspect_exefs, inspect_inputs, local_trial_receipt, local_trial_scope_matches)
from .storage import build_lock, check_separation, locations, write_json


def selections(opts, dirs):
    config = dirs["settings"] / "launcher.json"
    warning = None
    try:
        settings = json.loads(config.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        settings = {}
    except ValueError:
        settings = None
    if isinstance(settings, dict) and settings.get("schema", 1) != 1:
        raise ValueError(f"Unsupported settings version in {config}. Use the matching launcher version.")
    if not isinstance(settings, dict) or any(
        value is not None and (not isinstance(value, str) or "\0" in value)
        for value in (settings.get("exefs"), settings.get("romfs"))
    ):
        settings = {}
        warning = f"Could not read the saved folder locations in {config}. Select both folders and choose Build game code again."
        can_repair = opts.command in ("configure", "build-and-prepare") and opts.exefs is not None and opts.romfs is not None
        if opts.command not in ("status", "doctor") and not can_repair:
            raise ValueError(warning) from None
    locations_saved = bool(settings.get("exefs") and settings.get("romfs"))
    paths = {k: Path(getattr(opts, k, None) or settings.get(k) or ROOT / k).absolute()
             for k in ("exefs", "romfs")}
    return paths, warning, locations_saved


def parser():
    p = argparse.ArgumentParser(prog="FagriShot", description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status").add_argument("--json", action="store_true")
    commands.add_parser("doctor")
    commands.add_parser("backups")
    commands.add_parser("backup")
    commands.add_parser("restore").add_argument("backup_id")
    for cmd in ("configure", "inspect", "build-code", "enroll-local", "prepare", "play", "build-and-prepare"):
        sub = commands.add_parser(cmd)
        sub.add_argument("--exefs", type=Path)
        sub.add_argument("--romfs", type=Path)
        if cmd in ("inspect", "enroll-local", "prepare", "play", "build-and-prepare"):
            sub.add_argument("--base-cnmt", type=Path)
            sub.add_argument("--update-cnmt", type=Path)
        if cmd in ("build-code", "prepare", "build-and-prepare"):
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
    runtime = ROOT / ("fefw_rt.dll" if PLAYER_PACKAGE.is_file() else "runtime-bin/fefw_rt.dll")
    if not runtime.is_file():
        runtime = ROOT / "work/runtime-release/fefw_rt.dll"
    compiler = (("Bundled Zig compiler", ROOT / "tools/build/zig/zig.exe") if PLAYER_PACKAGE.is_file()
                else ("Local LLVM", ROOT / "work/toolchain/llvm-23.1.5/bin/clang-cl.exe"))
    for label, path in (("Game Python", VENV), ("Runtime DLL", runtime), compiler, ("Game executable", EXE)):
        print(f"{label}: {path if path.is_file() else 'not built or installed here'}")
    print("Updates: manual. Download the Windows player ZIP from GitHub Releases.")


def linked_record():
    stamp = PRIVATE / "linked.json"
    if not EXE.is_file():
        return None
    try:
        record = json.loads(stamp.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if (isinstance(record, dict) and record.get("product") == "FagriShot"
            and isinstance(record.get("input"), dict) and isinstance(record.get("exe_sha256"), str)):
        return record
    return None


def ensure_exe(inv):
    record = linked_record()
    repair = "Choose Build game code in the launcher." if PLAYER_PACKAGE.is_file() else "Run build-code again."
    if record is None:
        raise ValueError(f"A complete game build is required. {repair}")
    if record["input"].get("exefs") != inv["exefs"] or record["exe_sha256"] != sha(EXE):
        raise ValueError(f"The linked game differs from this ExeFS or has changed. {repair}")
    if record.get("source") != source_state() or inspect_exefs(PRIVATE / "prepared-exefs")["exefs"] != record.get("prepared_exefs"):
        raise ValueError(f"The runtime source or prepared module set changed. {repair}")
    if PLAYER_PACKAGE.is_file():
        from .player_build import verify_player_runtime
        verify_player_runtime(record)
    return record


def remember_ready(paths, linked, cache):
    write_json(PRIVATE / "play-ready.json", {
        "paths": {k: str(p.resolve()) for k, p in paths.items()},
        "source": linked["source"], "exe_sha256": linked["exe_sha256"], "cache_key": cache.name})


def play_ready(paths, dirs, linked):
    if not linked or not (paths["exefs"] / "main").is_file() or not paths["romfs"].is_dir():
        return False
    try:
        ready = json.loads((PRIVATE / "play-ready.json").read_bytes())
        key = ready["cache_key"]
        return (isinstance(key, str) and len(key) == 64 and all(c in "0123456789abcdef" for c in key)
                and ready["paths"] == {k: str(p.resolve()) for k, p in paths.items()}
                and ready["exe_sha256"] == linked["exe_sha256"]
                and ready["source"] == linked["source"] == source_state()
                and (dirs["caches"] / "shaders" / key / "ready.json").is_file())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def setup_game(opts, paths, dirs, source_checkout):
    from .shaders import prepare_shaders
    packaged = PLAYER_PACKAGE.is_file()
    if not packaged and not source_checkout:
        raise ValueError("Download and extract the FagriShot Windows player ZIP to build the game.")
    with build_lock(PRIVATE / "build.lock"):
        (PRIVATE / "play-ready.json").unlink(missing_ok=True)
        print("[setup 1/3] Checking game files", flush=True)
        check_input_roots(paths["exefs"], paths["romfs"])
        check_executable(inspect_exefs(paths["exefs"]), CATALOG)
        inv = inspect_inputs(**paths, base_cnmt=opts.base_cnmt, update_cnmt=opts.update_cnmt)
        write_json(dirs["logs"] / "input-inventory.json", inv)
        check_support(inv, CATALOG)
        if packaged:
            print("Verifying the bundled build tools.", flush=True)
            verify_player_installation()
        for directory in dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
        write_json(dirs["settings"] / "launcher.json", {"schema": 1, **{k: str(v) for k, v in paths.items()}})
        print("[setup 2/3] Building game code", flush=True)
        if packaged:
            from .player_build import build_player
            build_player(paths["exefs"], inv, opts.jobs)
        else:
            build_game(paths["exefs"], inv, opts.jobs)
        linked = ensure_exe(inv)
        print("[setup 3/3] Preparing shaders", flush=True)
        cache = prepare_shaders(paths, inv, dirs, opts.jobs)
        remember_ready(paths, linked, cache)
        print("Build complete. Choose Play to start the game.", flush=True)


def read_trial():
    try:
        return json.loads((PRIVATE / "local-trial.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def launch(paths, cache, dirs):
    ini_file = dirs["settings"] / "fefw.ini"
    ini = configparser.RawConfigParser(strict=False)
    if ini_file.exists():
        with ini_file.open(encoding="utf-8-sig") as stream:
            ini.read_file(stream)
    else:
        ini.read_dict({"fefw": {"fps60": "0", "output": "console"}, "boot": {"gfx": "window"}})
    prepared = PRIVATE / "prepared-exefs"
    managed = {"fs": {"exefs": prepared, "main": prepared / "main", "romfs": paths["romfs"],
                      "save_dir": dirs["saves"], "dll_mods": "false", "mods": ""},
               "nvn": {"shader_cache": cache / "spirv", "bc7_cache": cache / "bc7",
                       "pipeline_cache": cache / "pipeline_cache.bin"},
               "log": {"path": dirs["logs"] / "FagriShot.log"}}
    for section, values in managed.items():
        if not ini.has_section(section):
            ini.add_section(section)
        for key, value in values.items():
            # The runtime treats whitespace followed by # or ; as an inline comment outside quotes.
            ini.set(section, key, f'"{value.as_posix()}"' if isinstance(value, Path) else value)
    tmp = ini_file.with_suffix(".new")
    with tmp.open("w", encoding="utf-8") as stream:
        ini.write(stream)
    tmp.replace(ini_file)
    from .saves import snapshot
    snapshot(dirs)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("NXRT_", "FEFW_"))}
    print("Starting FagriShot.", flush=True)
    subprocess.run([EXE, "--ini", ini_file], cwd=dirs["settings"], env=env, check=True)


def inspect_selected(opts, paths, dirs):
    print("Reading the selected ExeFS and RomFS. Fingerprints cover every file.", flush=True)
    inv = inspect_inputs(**paths, base_cnmt=opts.base_cnmt, update_cnmt=opts.update_cnmt)
    write_json(dirs["logs"] / "input-inventory.json", inv)
    candidate = check_executable(inv, CATALOG)
    print(f"Executable modules match {candidate['version']}. Inventory saved to {dirs['logs'] / 'input-inventory.json'}.", flush=True)
    return inv


def supported_inventory(opts, paths, dirs, source_checkout):
    inv = inspect_selected(opts, paths, dirs)
    try:
        check_support(inv, CATALOG)
    except InputError:
        trial = read_trial() if source_checkout else None
        if trial is None:
            raise
        check_local_trial(inv, CATALOG, trial, checkout=ROOT, paths=paths, source=source_state())
        print("The complete input fingerprints match the private local test pairing.", flush=True)
    else:
        print("Executable and content fingerprints match the compatibility record.", flush=True)
    return inv


def execute(opts):
    dirs = locations()
    cmd = opts.command
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
    paths, warning, locations_saved = selections(opts, dirs)
    source_checkout = (ROOT / "recomp/games/fefw/config.py").is_file()
    if cmd == "status":
        from .saves import list_backups
        trial = read_trial() if source_checkout else None
        linked = linked_record()
        result = {"version": VERSION, **{k: str(p) for k,p in paths.items()},
                  "exefs_present": (paths["exefs"] / "main").is_file(), "romfs_present": paths["romfs"].is_dir(),
                  "settings": str(dirs["settings"]), "saves": str(dirs["saves"]),
                  "logs": str(dirs["logs"]), "exe_built": linked is not None,
                  "build_available": source_checkout or PLAYER_PACKAGE.is_file(),
                  "play_ready": play_ready(paths, dirs, linked),
                  "source_checkout": source_checkout, "settings_warning": warning, "locations_saved": locations_saved,
                  "backup_ids": list_backups(dirs),
                  "has_pair": bool(json.loads(CATALOG.read_text("utf-8"))["supported"]),
                  "local_trial": bool(trial and local_trial_scope_matches(
                      trial, checkout=ROOT, paths=paths, source=source_state()))}
        print(json.dumps(result, ensure_ascii=False, indent=None if opts.json else 2))
        return
    if cmd == "doctor":
        if warning:
            print(warning)
        diagnostic(dirs, paths)
        return
    inputs = [paths["exefs"]] if cmd == "build-code" else paths.values()
    check_separation(inputs, [*dirs.values(), ROOT / "work", ROOT / "tools", ROOT / "game-build"])
    if cmd == "build-and-prepare":
        setup_game(opts, paths, dirs, source_checkout)
        return
    if cmd == "configure":
        check_input_roots(paths["exefs"], paths["romfs"])
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
    if cmd == "enroll-local" and not source_checkout:
        raise ValueError("Enroll a local game test from the source checkout containing the private game build.")
    if cmd == "enroll-local":
        inv = inspect_selected(opts, paths, dirs)
        with build_lock(PRIVATE / "build.lock"):
            linked = ensure_exe(inv)
            receipt = local_trial_receipt(inv, CATALOG, checkout=ROOT, paths=paths, source=linked["source"])
            write_json(PRIVATE / "local-trial.json", receipt)
        print("Enrolled these exact folders for a local game test in this checkout.")
        return
    if cmd == "inspect":
        supported_inventory(opts, paths, dirs, source_checkout)
        return
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    with build_lock(PRIVATE / "build.lock"):
        if cmd == "prepare":
            from .shaders import prepare_shaders
            inv = supported_inventory(opts, paths, dirs, source_checkout)
            linked = ensure_exe(inv)
            cache = prepare_shaders(paths, inv, dirs, opts.jobs)
            remember_ready(paths, linked, cache)
        else:
            from .saves import recover
            from .shaders import require_ready
            try:
                inv = supported_inventory(opts, paths, dirs, source_checkout)
                ensure_exe(inv)
                cache = require_ready(inv, dirs)
            except (OSError, ValueError):
                (PRIVATE / "play-ready.json").unlink(missing_ok=True)
                raise
            with build_lock(dirs["settings"] / "save.lock"):
                recover(dirs)
                launch(paths, cache, dirs)


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
