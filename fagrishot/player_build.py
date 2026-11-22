"""Build the Windows player executable with the compiler and SDK in the extracted release."""

import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from . import UPSTREAM, VERSION
from .build import CATALOG, EXE, PRIVATE, ROOT, run, sha, source_state
from .inputs import check_executable, inspect_exefs
from .storage import write_json


MODULES = ("main", "sdk", "subsdk0", "subsdk1", "subsdk2", "rtld")


def _payload():
    return json.loads((ROOT / "player-package.json").read_text(encoding="utf-8"))["files"]


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


def _current(stamp, inventory, source):
    try:
        record = json.loads(stamp.read_text(encoding="utf-8"))
        if (record["builder"] != "bundled-zig" or record["source"] != source
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
        print(f"Game already built and verified: {EXE}", flush=True)
        return
    files = _payload()
    runtime = _runtime_files(files)
    # Isolate resumable lifts/objects from different compilers, SDKs, embedded interpreters or inputs.
    tools = {p: digest for p, digest in files.items() if p.startswith(("tools/build/", "tools/python/"))}
    build_id = hashlib.sha256(json.dumps([tools, inventory["exefs"], "compat"], sort_keys=True).encode()).hexdigest()
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
              "cpu_variant": "compat", "exe_sha256": done["exe_sha256"], "runtime_files": runtime}
    verify_player_runtime(record)
    write_json(stamp, record)
    print(f"Linked {EXE}", flush=True)
