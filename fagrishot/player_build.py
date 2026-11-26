"""Build the Windows player executable with the compiler and SDK in the extracted release."""

import datetime
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from . import UPSTREAM, VERSION
from .build import CATALOG, EXE, PRIVATE, ROOT, run, sha, source_state
from .inputs import check_executable, inspect_exefs
from .storage import OperationBusy, build_lock, write_json


MODULES = ("main", "sdk", "subsdk0", "subsdk1", "subsdk2", "rtld")


def _payload():
    return json.loads((ROOT / "player-package.json").read_text(encoding="utf-8"))["files"]


def _build_id(files, exefs):
    tools = {p: digest for p, digest in files.items() if p.startswith(("tools/build/", "tools/python/"))}
    return hashlib.sha256(json.dumps([tools, exefs, "compat"], sort_keys=True).encode()).hexdigest()


def _matching_compiler(record, private, build_id):
    if record.get("builder") != "bundled-zig" or record.get("cpu_variant") != "compat":
        return False
    if "build_id" in record:
        return record["build_id"] == build_id
    # Older packages stored the compiler identity with the output of the link step.
    stage = private / "player-build" / build_id[:24]
    try:
        link = json.loads((stage / "verified-link.json").read_bytes())
        return (link["build_id"] == build_id and link["exe_sha256"] == record["exe_sha256"]
                and link["sites_sha256"] == record["runtime_files"]["fefw.sites"]
                and link["prepared_exefs"] == record["prepared_exefs"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def compatible_link(record):
    """A launcher-only update can use a native build made with identical compiler inputs and runtime files."""
    try:
        if record.get("source") == source_state():
            return True
        files = _payload()
        runtime = _runtime_files(files)
        return (_matching_compiler(record, PRIVATE, _build_id(files, record["input"]["exefs"]))
                and record["runtime_files"].keys() == runtime.keys() | {"fefw.sites"}
                and all(record["runtime_files"][name] == digest for name, digest in runtime.items()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _runtime_files(files):
    paths = {"fefw_rt.dll", "fonts/DejaVuSans.ttf"}
    for name in files:
        path = PurePosixPath(name)
        if (path.parent == PurePosixPath("locales") and path.suffix == ".ini"
                or path.parent == PurePosixPath(".") and (name == "renhdrum.dll" or path.match("astcdec-*-shared.dll"))):
            paths.add(name)
    try:
        return {name: files[name] for name in sorted(paths)}
    except KeyError as exc:
        raise ValueError(f"Required runtime file is missing from the player manifest: {exc.args[0]}") from None


def verify_player_runtime(record):
    """Verify the DLL and companion files the private executable actually loads."""
    expected = _runtime_files(_payload())
    installed = record.get("runtime_files", {})
    if not isinstance(installed, dict) or installed.keys() != expected.keys() | {"fefw.sites"}:
        raise ValueError("The linked runtime receipt is incomplete. Run Build game again.")
    for name, digest in installed.items():
        path = EXE.parent / name
        if (name in expected and expected[name] != digest) or not path.is_file() or sha(path) != digest:
            raise ValueError(f"The installed game runtime file changed: {name}. Run Build game again.")


def _install(source, dest, digest):
    """Publish a verified file by rename, keeping an existing destination intact on copy failure."""
    if dest.is_file() and sha(dest) == digest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        shutil.copyfile(source, temp)
        if sha(temp) != digest:
            raise ValueError(f"File changed while installing {dest.name}. Re-extract the player package and retry.")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def _legacy_builds(settings):
    yield ROOT / "work/private"
    # Older releases kept the last successful Play's prepared folder in the user's INI.
    from .graphics import Ini
    try:
        prepared = Path(Ini(settings / "fefw.ini").get("fs", "exefs"))
        if (prepared.is_absolute() and prepared.name == "prepared-exefs"
                and prepared.parent.name == "private" and prepared.parent.parent.name == "work"
                and prepared.parent != ROOT / "work/private"
                and (prepared.parents[2] / "player-package.json").is_file()):
            yield prepared.parent
    except (OSError, ValueError):
        return


def _adopt(legacy):
    """Import previously installed, compatible outputs. Retain the original build for its old launcher."""
    record = json.loads((legacy / "linked.json").read_bytes())
    files = _payload()
    build_id = _build_id(files, record["input"]["exefs"])
    if not _matching_compiler(record, legacy, build_id):
        return False
    plain = legacy / "prepared-exefs"
    prepared = inspect_exefs(plain)
    profile = check_executable(prepared, CATALOG)
    if (prepared["exefs"] != record["prepared_exefs"] or prepared["modules"]["main"]["sha256"] != profile["main_plain_sha256"]
            or check_executable(record["input"], CATALOG)["id"] != profile["id"]):
        return False
    game = legacy / "game"
    sites = record["runtime_files"]["fefw.sites"]
    if sha(game / "FagriShot.exe") != record["exe_sha256"] or sha(game / "fefw.sites") != sites:
        return False
    runtime = _runtime_files(files)
    for name, digest in runtime.items():
        _install(ROOT / name, EXE.parent / name, digest)
    _install(game / "fefw.sites", EXE.parent / "fefw.sites", sites)
    _install(game / "FagriShot.exe", EXE, record["exe_sha256"])
    for name in (*MODULES, "main.npdm"):
        digest = prepared["program"]["sha256"] if name == "main.npdm" else prepared["modules"][name]["sha256"]
        _install(plain / name, PRIVATE / "prepared-exefs" / name, digest)
    runtime["fefw.sites"] = sites
    if record["runtime_files"] == runtime:
        try:
            ready = json.loads((legacy / "play-ready.json").read_bytes())
            if ready["source"] == record["source"] and ready["exe_sha256"] == record["exe_sha256"]:
                write_json(PRIVATE / "play-ready.json", ready)
        except (ValueError, OSError, KeyError, TypeError):
            pass  # A verified build can still be recovered without an old Play receipt.
    write_json(PRIVATE / "linked.json", {**record, "build_id": build_id, "runtime_files": runtime})
    return True


def _install_launcher():
    # Directly opening the built game delegates to its adjacent launcher. The copy uses
    # this locator to start the tools from the most recently opened installation.
    locator = EXE.parent / "player-root.txt"
    _install(ROOT / "FagriShotLauncher.exe", EXE.parent / "FagriShotLauncher.exe", _payload()["FagriShotLauncher.exe"])
    try:
        if locator.read_text(encoding="utf-8") == str(ROOT):
            return
    except FileNotFoundError:
        pass
    with tempfile.NamedTemporaryFile(dir=EXE.parent, mode="w", encoding="utf-8", delete=False) as out:
        temp = Path(out.name)
        out.write(str(ROOT))
    try:
        os.replace(temp, locator)
    finally:
        temp.unlink(missing_ok=True)


def open_player_build(settings):
    stamp = PRIVATE / "linked.json"
    candidates = [] if stamp.is_file() else [p for p in _legacy_builds(settings) if (p / "linked.json").is_file()]
    if not candidates and not EXE.is_file():
        return
    try:
        with build_lock(PRIVATE / "build.lock"):
            if not stamp.is_file():
                for legacy in candidates:
                    try:
                        if _adopt(legacy):
                            break
                    except OSError as exc:
                        if exc.errno in (errno.ENOSPC, errno.EROFS):
                            raise  # The selected storage needs attention before a build can be installed.
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue  # Damaged or incomplete earlier installation; first-time setup remains available.
            if EXE.is_file() and stamp.is_file():
                _install_launcher()
    except OperationBusy:
        pass  # Another build/Play owns the same profile. Its completed files remain authoritative.


def _current(stamp, inventory, source):
    try:
        record = json.loads(stamp.read_text(encoding="utf-8"))
        if (record["builder"] != "bundled-zig" or (record["source"] != source and not compatible_link(record))
                or record["input"]["exefs"] != inventory["exefs"] or record["exe_sha256"] != sha(EXE)
                or record["prepared_exefs"] != inspect_exefs(PRIVATE / "prepared-exefs")["exefs"]):
            return False
        verify_player_runtime(record)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _reusable_link(stage, build_id):
    try:
        previous = json.loads((stage / "verified-link.json").read_bytes())
        return (previous["build_id"] == build_id
                and previous["done_sha256"] == sha(stage / "build/done.json")
                and previous["exe_sha256"] == sha(stage / "fefw.exe")
                and previous["sites_sha256"] == sha(stage / "fefw.sites")
                and previous["prepared_exefs"] == inspect_exefs(stage / "exefs")["exefs"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def build_player(exefs, inventory, jobs):
    """Called under the CLI build lock after verification of the release payload."""
    profile = check_executable(inventory, CATALOG)
    source = source_state()
    stamp = PRIVATE / "linked.json"
    if _current(stamp, inventory, source):
        _install_launcher()
        print(f"Game already built and verified: {EXE}", flush=True)
        return
    files = _payload()
    runtime = _runtime_files(files)
    # Isolate resumable lifts/objects from different compilers, SDKs, embedded interpreters or inputs.
    build_id = _build_id(files, inventory["exefs"])
    stage = PRIVATE / "player-build" / build_id[:24]
    if (stage / "mods").exists() or (stage / "mods").is_symlink():
        raise ValueError(f"Move the mods directory out of the private compiler workspace {stage} and retry.")
    logs = PRIVATE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = logs / f"player-build-{now}.log"
    if _reusable_link(stage, build_id):
        print("Reinstalling the verified private link from this package's compiler and inputs.", flush=True)
    else:
        print(f"Private build log: {log}", flush=True)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ZIG_", "NXRECOMP_", "FEFW_"))}
        run([ROOT / "tools/python/python.exe", "-I", "-X", "utf8", ROOT / "tools/build/fefw_build.py",
             "--exefs", exefs, "--game", stage, "--cpu", "compat", "--jobs", str(jobs)], env=env, log=log)
    done_path = stage / "build/done.json"
    done = json.loads(done_path.read_bytes())
    built = stage / "fefw.exe"
    prepared = inspect_exefs(stage / "exefs")
    if (check_executable(prepared, CATALOG)["id"] != profile["id"]
            or prepared["modules"]["main"]["sha256"] != profile["main_plain_sha256"]
            or done.get("sha256") != profile["sha256"] or done.get("main_build_id") != profile["main_build_id"]
            or done["exe_sha256"] != sha(built) or done["tools"]["cpu"] != "compat"
            or tuple(row[0] for row in done["tools"]["modules"]) != MODULES):
        raise ValueError("The portable linker outputs do not match the verified input and build receipt.")

    sites_digest = sha(stage / "fefw.sites")
    write_json(stage / "verified-link.json", {"build_id": build_id, "done_sha256": sha(done_path),
               "exe_sha256": done["exe_sha256"], "sites_sha256": sites_digest, "prepared_exefs": prepared["exefs"]})

    # The receipt becomes visible only after both the executable and everything it maps have been installed.
    stamp.unlink(missing_ok=True)
    for name, digest in runtime.items():
        _install(ROOT / name, EXE.parent / name, digest)
    runtime["fefw.sites"] = sites_digest
    _install(stage / "fefw.sites", EXE.parent / "fefw.sites", runtime["fefw.sites"])
    dest = PRIVATE / "prepared-exefs"
    for name in (*MODULES, "main.npdm"):
        _install(stage / "exefs" / name, dest / name,
                 prepared["program"]["sha256"] if name == "main.npdm" else prepared["modules"][name]["sha256"])
    installed = inspect_exefs(dest)
    if installed["exefs"] != prepared["exefs"]:
        raise ValueError(f"The private prepared ExeFS contains unexpected files. Inspect {dest} and retry.")
    _install(built, EXE, done["exe_sha256"])
    record = {"product": "FagriShot", "version": VERSION, "upstream": UPSTREAM, "builder": "bundled-zig",
              "source": source, "prepared_exefs": installed["exefs"], "input": inventory, "applied_mods": [],
              "cpu_variant": "compat", "build_id": build_id, "exe_sha256": done["exe_sha256"], "runtime_files": runtime}
    verify_player_runtime(record)
    _install_launcher()
    write_json(stamp, record)
    print(f"Linked {EXE}", flush=True)
