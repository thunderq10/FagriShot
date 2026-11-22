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

from .build import EXE, PLAYER_PACKAGE, PRIVATE, ROOT, VENV, run, sha
from .storage import write_json


SHADERS = ROOT / "tools/shaders"
EXTRACT = SHADERS / ("extract_corpus.py" if PLAYER_PACKAGE.is_file() else "ampere/extract_corpus.py")
HARNESS = (ROOT / "tools/spirv_harness/spirv_harness.dll" if PLAYER_PACKAGE.is_file()
           else SHADERS / "build/bin/Release/net10.0/spirv_harness.dll")
LOCAL_DOTNET = SHADERS / "build/sdk-cache/sdk/dotnet.exe"
TARGET = "vulkan1.3"
HASH16 = re.compile(r"[0-9a-f]{16}")
FEATURE_ENV = ("FEFW_NO_FLOAT64", "FEFW_NO_DIVERGENT_BARRIERS")
STAGE_NAMES = {"vs": "vertex", "ps": "fragment", "cs": "compute", "gs": "geometry",
               "tcs": "tessellationcontrol", "tes": "tessellationevaluation"}


def _validator():
    if PLAYER_PACKAGE.is_file():
        path = ROOT / "tools/spirv-val/spirv-val.exe"
        if not path.is_file():
            raise ValueError("The bundled shader validator is missing. Extract the complete FagriShot ZIP again.")
        return path
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
    return {"schema": 2, "shader_policy": "nvn-and-host-dlss-v1",
            "romfs": inv["romfs"]["tree_sha256"], "exefs": inv["exefs"]["tree_sha256"],
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
        for row in rows:
            if any(row[field] is None for field in fields):
                raise ValueError(f"Incomplete shader table row in {path.name} at line {rows.line_num}.")
            yield row


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


def _console_dlss_kernels(corpus):
    # api_dlss.cpp and api_dlss_replay.cpp implement this title's subsdk0 DLSS calls on the host.
    # Only a CUDA kernel identified independently in both extraction tables can use this rule.
    cuda = Counter()
    required = set()
    fields = ["code_sha1_16", "source", "entry_off", "glslc_off", "container", "stage",
              "ctrl_sha1_16", "glslc_sha1_16", "code_size"]
    for row in _rows(corpus / "manifest.tsv", fields):
        name = row["code_sha1_16"]
        if row["source"] != "subsdk0" or row["container"] != "CUBIN":
            required.add(name)
            continue
        ctrl = row["ctrl_sha1_16"]
        if not HASH16.fullmatch(ctrl) or row["stage"] != "cs" or row["glslc_sha1_16"] != "-":
            raise ValueError(f"Invalid subsdk0 CUDA occurrence for {name}.")
        descriptor = (corpus / "ctrl" / (ctrl + ".bin")).read_bytes()
        if len(descriptor) < 68 or hashlib.sha1(descriptor).hexdigest()[:16] != ctrl:
            raise ValueError(f"Invalid subsdk0 CUDA descriptor for {name}.")
        magic, version, sm, size = struct.unpack_from("<4I", descriptor)
        if magic != 0x41445543 or version != 1 or sm // 10 != 8 or size != int(row["code_size"], 16):
            raise ValueError(f"Invalid subsdk0 CUDA descriptor header for {name}.")
        cuda[(name, row["entry_off"], row["glslc_off"], sm)] += 1
    kernels = Counter((row["code_sha1_16"], row["fatbin_off"], row["elf_off"], int(row["sm"]))
                      for row in _rows(corpus / "cuda_kernels.tsv", ["code_sha1_16", "fatbin_off", "elf_off", "sm", "source"])
                      if row["source"] == "subsdk0")
    if kernels != cuda:
        raise ValueError("The subsdk0 CUDA kernel table disagrees with its occurrences.")
    names = {key[0] for key in cuda}
    if names & required:
        raise ValueError("A subsdk0 CUDA block also appears in the runtime shader corpus.")
    return names


def _check_programs(path, jobs, console_cuda):
    data = path.read_bytes()
    if len(data) < 16 or data[:8] != b"MPRPRG01":
        raise ValueError("Shader extraction produced an incomplete program list.")
    count, reserved = struct.unpack_from("<II", data, 8)
    if reserved or len(data) != 16 + 24 * count:
        raise ValueError("Shader extraction produced an invalid program list.")
    for vertex, fragment, geometry in struct.iter_unpack("<QQQ", memoryview(data)[16:]):
        for value, stage in ((vertex, "vs"), (fragment, "ps"), (geometry, "gs")):
            if stage == "gs" and not value:
                continue
            name = f"{value:016x}"
            if name in console_cuda or name not in jobs or jobs[name][0] != stage:
                raise ValueError(f"Program list references a missing or incorrect {stage} shader {name}.")
    return count


def _check_outputs(spirv, jobs, validator, workers, console_cuda=frozenset()):
    results = {}
    reported = set()
    fields = ["hash", "group", "stage", "stages_using", "status", "spv_bytes", "resources", "error"]
    for row in _rows(spirv / "results.tsv", fields):
        name = row["hash"]
        if name not in jobs or name in reported:
            raise ValueError("Shader results include a duplicate or unrequested block.")
        reported.add(name)
        if (row["group"], int(row["stages_using"])) != jobs[name]:
            raise ValueError(f"Shader {name} differs from its requested group or occurrence count.")
        path = spirv / (name + ".spv")
        if name in console_cuda and row["status"] == "unsupported":
            if (row["stage"] != "compute" or row["group"] != "cs" or not row["error"].strip()
                    or int(row["spv_bytes"]) != 0 or path.exists()):
                raise ValueError(f"Inconsistent unsupported subsdk0 CUDA result for {name}.")
            continue
        # The harness retains the first exception in this field when its retry succeeds.
        # The final status and the independent validation below describe the emitted module.
        if row["status"] not in {"ok", "ok-retry"} or (row["status"] == "ok" and row["error"].strip()):
            raise ValueError(f"Shader {name} returned {row['status']}. Inspect the private results.tsv.")
        if STAGE_NAMES.get(row["group"]) != row["stage"]:
            raise ValueError(f"Shader {name} produced the wrong execution stage.")
        if not path.is_file() or path.stat().st_size < 20 or path.stat().st_size != int(row["spv_bytes"]):
            raise ValueError(f"Shader {name} has a missing or incomplete SPIR-V module.")
        results[name] = path
    if reported != jobs.keys() or {p.stem for p in spirv.glob("*.spv")} != results.keys():
        raise ValueError("The corpus, translation results and SPIR-V output sets disagree.")

    def validate(item):
        name, path = item
        result = subprocess.run([validator, "--target-env", TARGET, path], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Shader {name} failed {TARGET} validation. {result.stderr.strip()[:300]}")
        return name, sha(path)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(validate, results.items()))


TABLES = ("corpus/manifest.tsv", "corpus/unique.tsv", "corpus/cuda_kernels.tsv", "spirv/results.tsv",
          "programs.bin", "shader-report.json")


def _ready(cache, identity):
    try:
        marker = json.loads((cache / "ready.json").read_text(encoding="utf-8"))
        if marker["identity"] != identity or marker["tables"] != {rel: sha(cache / rel) for rel in TABLES}:
            raise ValueError
        modules = {p.stem: sha(p) for p in (cache / "spirv").glob("*.spv")}
        if not modules or modules != marker["modules"]:
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError("The validated shader cache is missing or changed. Choose Build game code in the launcher to prepare it again.") from None
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
    harness_cmd = [HARNESS.with_suffix(".exe")] if PLAYER_PACKAGE.is_file() else [_dotnet(), HARNESS]
    validator = _validator()
    if not VENV.is_file():
        raise ValueError("Initialize the pinned Python environment with python scripts/setup_python.py.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=cache.name[:12] + "-pending-", dir=cache.parent))
    print(f"Preparing private shaders in {stage}", flush=True)
    corpus, spirv = stage / "corpus", stage / "spirv"
    prepared = PRIVATE / "prepared-exefs"
    # The extractor writes manifests with Python's default encoding. Its C# reader expects UTF-8.
    prepare_args = [VENV, "-I", "-X", "utf8", EXTRACT, "--romfs", paths["romfs"], "--exefs", prepared,
                    "--main", prepared / "main", "--out", corpus, "--programs-bin", stage / "programs.bin",
                    "--jobs", str(jobs), "--progress"]
    log = stage / "extraction-errors.log"
    with log.open("w", encoding="utf-8") as errors:
        process = subprocess.run(prepare_args, cwd=ROOT, stderr=errors)
    if process.returncode or "left out" in log.read_text(encoding="utf-8", errors="replace"):
        raise ValueError(f"Shader extraction failed or skipped input. See {log}.")
    expected = _corpus(corpus)
    console_cuda = _console_dlss_kernels(corpus)
    if not expected.keys() - console_cuda:
        raise ValueError("The extracted corpus contains no runtime shaders.")
    programs = _check_programs(stage / "programs.bin", expected, console_cuda)
    # The batch process can exit successfully after individual failures. Validate every accepted output below.
    env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    run([*harness_cmd, "--batch", corpus, "--out", spirv, "--jobs", str(jobs), "--target-env", TARGET,
         "--host", "desktop", "--no-val"], env=env)
    modules = _check_outputs(spirv, expected, validator, jobs, console_cuda)
    fields = ["hash", "status", "stages_using", "error"]
    console_results = {row["hash"]: {"status": row["status"], "occurrences": int(row["stages_using"]),
                                      "diagnostic": row["error"]}
                      for row in _rows(spirv / "results.tsv", fields) if row["hash"] in console_cuda}
    runtime_count = len(expected) - len(console_cuda)
    write_json(stage / "shader-report.json", {"runtime_shaders": runtime_count, "programs": programs,
               "validated_spirv": len(modules), "host_replaced_subsdk0_cuda": console_results})
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
    print(f"Validated {len(modules)} SPIR-V modules for {TARGET}, including all {runtime_count} required shaders. "
          f"Host DLSS replaces {len(console_cuda)} console CUDA kernels. See {cache / 'shader-report.json'}.", flush=True)
    return cache
