"""Prepare and verify content-addressed local Vulkan shader caches."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import uuid

from .build import EXE, PRIVATE, ROOT, VENV, run, sha
from .storage import write_json


SHADERS = ROOT / "tools/shaders"
EXTRACT = SHADERS / "ampere/extract_corpus.py"
HARNESS = SHADERS / "build/bin/Release/net10.0/spirv_harness.dll"
LOCAL_DOTNET = SHADERS / "build/sdk-cache/sdk/dotnet.exe"
TARGET = "vulkan1.3"
HASH16 = re.compile(r"[0-9a-f]{16}")
FEATURE_ENV = ("FEFW_NO_FLOAT64", "FEFW_NO_DIVERGENT_BARRIERS")


def _validator():
    path = shutil.which("spirv-val")
    if not path:
        raise ValueError("Install the Vulkan SDK with spirv-val on PATH before preparing shaders.")
    return Path(path)


def _identity(inv):
    required = [HARNESS, HARNESS.with_suffix(".deps.json"), HARNESS.with_suffix(".runtimeconfig.json"),
                EXTRACT, ROOT / "scripts/requirements-win64-py313.txt", EXE]
    if not all(p.is_file() for p in required):
        raise ValueError("Build the game and translator first. See docs/shader-verification.md.")
    dependencies = {p.relative_to(HARNESS.parent).as_posix(): sha(p) for p in sorted(HARNESS.parent.rglob("*.dll"))}
    return {"schema": 1, "romfs": inv["romfs"]["tree_sha256"], "exefs": inv["exefs"]["tree_sha256"],
            "game": sha(EXE), "runtime_dlls": {p.name: sha(p) for p in sorted(EXE.parent.glob("*.dll"))},
            "extractor": sha(EXTRACT), "python_packages": sha(required[-2]), "translator_dlls": dependencies,
            "dependency_manifest": sha(required[1]), "dotnet_settings": sha(required[2]),
            "validator": sha(_validator()), "target": TARGET, "host": "desktop",
            "adapter_flags": {k: os.environ.get(k) == "1" for k in FEATURE_ENV}}


def _key(identity):
    data = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def shader_cache(inv, dirs):
    return dirs["caches"] / "shaders" / _key(_identity(inv))


def _rows(path, fields):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
        if not set(fields).issubset(rows.fieldnames or []):
            raise ValueError(f"Unexpected shader table layout in {path.name}.")
        yield from rows


def _corpus(corpus):
    occurrences = Counter()
    for row in _rows(corpus / "manifest.tsv", ["code_sha1_16"]):
        occurrences[row["code_sha1_16"]] += 1
    jobs = {}
    for row in _rows(corpus / "unique.tsv", ["code_sha1_16", "first_ctrl", "first_glslc", "occurrences", "stage"]):
        name = row["code_sha1_16"]
        for folder, key in (("code", name), ("ctrl", row["first_ctrl"]), ("glslc", row["first_glslc"])):
            if key == "-" and folder == "glslc":
                continue
            if not key or not HASH16.fullmatch(key) or not (corpus / folder / (key + ".bin")).is_file():
                raise ValueError("The shader extraction references an invalid or missing binary.")
        if name in jobs or int(row["occurrences"]) != occurrences[name]:
            raise ValueError(f"Shader corpus counts disagree for {name}.")
        jobs[name] = (row["stage"], occurrences[name])
    if not jobs or jobs.keys() != occurrences.keys():
        raise ValueError("The shader corpus is empty or its occurrence and unique tables disagree.")
    return jobs


def _check_outputs(spirv, jobs, validator, workers):
    results = {}
    fields = ["hash", "group", "stage", "stages_using", "status", "spv_bytes", "resources", "error"]
    for row in _rows(spirv / "results.tsv", fields):
        name = row["hash"]
        if name not in jobs or name in results:
            raise ValueError("Shader results include a duplicate or unrequested block.")
        if row["status"] not in {"ok", "ok-retry"} or row["error"].strip():
            raise ValueError(f"Shader {name} returned {row['status']}. Inspect the private results.tsv.")
        if (row["group"], int(row["stages_using"])) != jobs[name]:
            raise ValueError(f"Shader {name} differs from its requested group or occurrence count.")
        path = spirv / (name + ".spv")
        if not path.is_file() or path.stat().st_size < 20 or path.stat().st_size != int(row["spv_bytes"]):
            raise ValueError(f"Shader {name} has a missing or incomplete SPIR-V module.")
        results[name] = path
    if results.keys() != jobs.keys() or {p.stem for p in spirv.glob("*.spv")} != jobs.keys():
        raise ValueError("The corpus, translation results and SPIR-V output sets disagree.")

    def validate(item):
        name, path = item
        result = subprocess.run([validator, "--target-env", TARGET, path], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Shader {name} failed {TARGET} validation. {result.stderr.strip()[:300]}")
        return name, sha(path)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(validate, results.items()))


TABLES = ("corpus/manifest.tsv", "corpus/unique.tsv", "spirv/results.tsv", "programs.bin")


def _ready(cache, identity):
    try:
        marker = json.loads((cache / "ready.json").read_text(encoding="utf-8"))
        if marker["identity"] != identity or marker["tables"] != {rel: sha(cache / rel) for rel in TABLES}:
            raise ValueError
        modules = {p.stem: sha(p) for p in (cache / "spirv").glob("*.spv")}
        if not modules or modules != marker["modules"]:
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError("Run prepare for this game and translator. The validated shader cache is missing or changed.") from None
    return cache


def require_ready(inv, dirs):
    identity = _identity(inv)
    return _ready(dirs["caches"] / "shaders" / _key(identity), identity)


def _dotnet():
    if LOCAL_DOTNET.is_file():
        return LOCAL_DOTNET
    installed = shutil.which("dotnet")
    if installed and re.search(r"^Microsoft\.NETCore\.App 10\.", subprocess.check_output(
            [installed, "--list-runtimes"], text=True), re.MULTILINE):
        return Path(installed)
    raise ValueError("Build the project-local .NET 10 shader tools with python scripts/build_shaders.py.")


def prepare_shaders(paths, inv, dirs, jobs):
    identity = _identity(inv)
    cache = dirs["caches"] / "shaders" / _key(identity)
    if (cache / "ready.json").is_file():
        try:
            return _ready(cache, identity)
        except ValueError:
            print("The cached shader outputs changed. Preparing a fresh copy.", flush=True)
    dotnet, validator = _dotnet(), _validator()
    if not VENV.is_file():
        raise ValueError("Initialize the pinned Python environment with python scripts/setup_python.py.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=cache.name[:12] + "-pending-", dir=cache.parent))
    print(f"Preparing private shaders in {stage}", flush=True)
    corpus, spirv = stage / "corpus", stage / "spirv"
    prepared = PRIVATE / "prepared-exefs"
    prepare_args = [VENV, "-I", EXTRACT, "--romfs", paths["romfs"], "--exefs", prepared, "--main", prepared / "main",
                    "--out", corpus, "--programs-bin", stage / "programs.bin", "--jobs", str(jobs), "--progress"]
    log = stage / "extraction-errors.log"
    with log.open("w", encoding="utf-8") as errors:
        process = subprocess.run(prepare_args, cwd=ROOT, stderr=errors)
    if process.returncode or "left out" in log.read_text(encoding="utf-8", errors="replace"):
        raise ValueError(f"Shader extraction failed or skipped input. See {log}.")
    expected = _corpus(corpus)
    program_list = stage / "programs.bin"
    with program_list.open("rb") as programs:
        header = programs.read(16)
    if len(header) != 16 or header[:8] != b"MPRPRG01" or program_list.stat().st_size != 16 + 24 * struct.unpack_from("<I", header, 8)[0]:
        raise ValueError("Shader extraction produced an incomplete program list.")
    # The batch process can exit successfully after individual failures. Validate every accepted output below.
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    run([dotnet, HARNESS, "--batch", corpus, "--out", spirv, "--jobs", str(jobs), "--target-env", TARGET,
         "--host", "desktop", "--no-val"], env=env)
    modules = _check_outputs(spirv, expected, validator, jobs)
    write_json(stage / "ready.json", {"identity": identity, "modules": modules,
                                     "tables": {rel: sha(stage / rel) for rel in TABLES}})
    # The CLI holds its build lock through preparation and launch. Keep failed attempts for diagnostics.
    previous = None
    if cache.exists():
        previous = cache.with_name(cache.name + "-previous-" + uuid.uuid4().hex[:8])
        cache.rename(previous)
    try:
        stage.rename(cache)
    except OSError:
        if previous is not None and not cache.exists():
            previous.rename(cache)
        raise
    print(f"Validated {len(modules)} SPIR-V modules for {TARGET}.", flush=True)
    return cache
