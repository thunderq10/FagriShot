"""Build the six game libraries and the Windows executable via the upstream CMake graph."""
import hashlib
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from . import UPSTREAM, VERSION
from .inputs import check_executable, inspect_exefs
from .storage import write_json

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "compatibility" / "supported.json"
PRIVATE = ROOT / "work" / "private"
EXE = PRIVATE / "game" / "FagriShot.exe"
PLAYER_PACKAGE = ROOT / "player-package.json"
VENV = ROOT / ("tools/python/python.exe" if PLAYER_PACKAGE.is_file() else "tools/.venv/Scripts/python.exe")


def sha(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def player_manifest():
    manifest = json.loads(PLAYER_PACKAGE.read_bytes())
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("product") != "FagriShot"
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("revision")))
            or not isinstance(manifest.get("files"), dict) or not manifest["files"]):
        raise ValueError("The player package manifest is damaged. Extract the complete FagriShot ZIP again.")
    return manifest


def verify_player_installation():
    for name, digest in player_manifest()["files"].items():
        parts = name.split("/")
        if (any(part in {"", ".", ".."} for part in parts) or "\\" in name or ":" in name
                or not re.fullmatch(r"[0-9a-f]{64}", str(digest))):
            raise ValueError("Invalid file entry in the player package. Extract a fresh copy of the ZIP.")
        path = ROOT.joinpath(*parts)
        if not path.is_file() or sha(path) != digest:
            raise ValueError(f"The player package file {name} is missing or changed. Extract a fresh copy of the ZIP.")


def run(args, env=None, log=None):
    print(subprocess.list2cmdline([str(a) for a in args]), flush=True)
    if log is None:
        subprocess.run(args, cwd=ROOT, env=env, check=True)
        return
    with log.open("a", encoding="utf-8") as output:
        print(subprocess.list2cmdline([str(a) for a in args]), file=output, flush=True)
        with subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                print(line, end="", file=output, flush=True)
            if code := process.wait():
                raise ValueError(f"Stage exited with {code}. Full build log: {log}")


def source_state():
    if PLAYER_PACKAGE.is_file():
        manifest = player_manifest()
        return {"commit": manifest["revision"], "tracked_diff_sha256": sha(PLAYER_PACKAGE)}
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "tracked_diff_sha256": hashlib.sha256(git("diff", "--no-ext-diff", "--binary", "HEAD",
                                                      "--", "recomp", "runtime", "tools", "game-build",
                                                      "scripts", "fagrishot")).hexdigest()}


def verify_python():
    lock = ROOT / "scripts/requirements-win64-py313.txt"
    expected = dict(line.split()[0].split("==") for line in lock.read_text().splitlines()
                    if line and not line.startswith("#"))
    probe = ("import importlib.metadata as m, json, struct, sys; "
             "print(json.dumps([sys.version_info[:2],struct.calcsize('P'),"
             "{name:m.version(name) for name in sys.argv[1:]}]))")
    if not VENV.is_file():
        raise ValueError("Initialize the build Python with python scripts/setup_python.py.")
    result = subprocess.run([VENV, "-I", "-c", probe, *expected], capture_output=True, text=True)
    if result.returncode or json.loads(result.stdout) != [[3, 13], 8, expected]:
        raise ValueError("The build Python differs from the lockfile. Run python scripts/setup_python.py.")
    return expected


def build_game(exefs, inventory, jobs):
    candidate = check_executable(inventory, CATALOG)
    if sys.platform != "win32":
        raise ValueError("The game build currently requires Windows x64.")
    if not (ROOT / "recomp/games/fefw/config.py").is_file():
        raise ValueError("Build code from the FagriShot source checkout. This package contains diagnostic tools.")
    source = source_state()
    logs = PRIVATE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / (datetime.datetime.now(datetime.timezone.utc).strftime("build-%Y%m%dT%H%M%S%fZ") + ".log")
    print(f"Private build log: {log}", flush=True)
    prepared = PRIVATE / "prepared-exefs"
    stamp = PRIVATE / "linked.json"
    stamp.unlink(missing_ok=True)
    python_packages = verify_python()
    ps = shutil.which("pwsh") or shutil.which("powershell")
    print("[1/4] Verify and prepare executable modules", flush=True)
    try:
        current = inspect_exefs(prepared)
        check_executable(current, CATALOG)
        reuse = current["modules"]["main"]["sha256"] == candidate["main_plain_sha256"]
    except ValueError:
        reuse = False
    if not reuse:
        run([VENV, "-I", "-X", "utf8", ROOT / "tools/nso/prepare_exefs.py",
             "--exefs", exefs, "--out", prepared], log=log)
        current = inspect_exefs(prepared)
        check_executable(current, CATALOG)
        if current["modules"]["main"]["sha256"] != candidate["main_plain_sha256"]:
            raise ValueError("Prepared main does not match the upstream plain NSO digest.")
    print("[2/4] Verify texture transcoder sources", flush=True)
    run([ps, "-NoProfile", "-File", ROOT / "tools/textures/scripts/fetch_third_party.ps1", "-CppOnly"], log=log)
    print("[3/4] Translate and compile all six modules", flush=True)
    run([ps, "-NoProfile", "-File", ROOT / "scripts/build_game.ps1", "-Stage", "Lift", "-Jobs", str(jobs)], log=log)
    print("[4/4] Link game libraries and runtime", flush=True)
    run([ps, "-NoProfile", "-File", ROOT / "scripts/build_game.ps1", "-Stage", "Link", "-Jobs", str(jobs)], log=log)
    if not EXE.is_file():
        raise ValueError("Link succeeded without the expected private FagriShot.exe.")
    write_json(stamp, {"product": "FagriShot", "version": VERSION, "upstream": UPSTREAM,
                       "source": source, "python_packages": python_packages, "prepared_exefs": current["exefs"],
                       "input": inventory, "applied_mods": [], "cpu_variant": "compat",
                       "exe_sha256": sha(EXE)})
    print(f"Linked {EXE}", flush=True)
