"""Build FagriShot's Windows shader translator and validate original Ampere fixtures."""

import argparse
import base64
import filecmp
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SHADERS = ROOT / "tools/shaders"
BUILD = SHADERS / "build"
PROJECT = SHADERS / "harness/SpirvHarness.csproj"
DLL = BUILD / "bin/Release/net10.0/spirv_harness.dll"
SDK = "10.0.404"
SDK_ARCHIVE = f"dotnet-sdk-{SDK}-win-x64.zip"
SDK_URL = f"https://builds.dotnet.microsoft.com/dotnet/Sdk/{SDK}/{SDK_ARCHIVE}"
SDK_HASH = ("330ddd2665b43cd2e428216a0d8ea1ac7bb19b5434fdc530e62010dc3b52823a"
            "620bf95489d628e1ed6f5122401793ee4887cca4a019122361a1231497f632ee")
RYUBING = "a82350bb774f70fcbd41c9987bf67a3775409963"
REPO = "https://git.ryujinx.app/ryubing/ryujinx.git"
PROJECTS = ["src/Ryujinx.Common", "src/Spv.Generator", "src/Ryujinx.Graphics.Shader"]
FILES = ["Directory.Build.props", "Directory.Packages.props", "global.json", "nuget.config", "LICENSE.txt"]
NUGET = "https://api.nuget.org/v3/index.json"


def digest(path, algorithm):
    with path.open("rb") as stream:
        result = hashlib.new(algorithm)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.digest()


def run(*args, env=None):
    return subprocess.run(args, cwd=ROOT, env=env, check=True, text=True, capture_output=False)


def output(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8").strip()


def sdk(env):
    if shutil.which("dotnet") and subprocess.run(["dotnet", "--version"], cwd=ROOT, env=env, text=True,
                                                  capture_output=True).stdout.strip() == SDK:
        return "dotnet"
    cache = BUILD / "sdk-cache"
    cache.mkdir(exist_ok=True)
    archive, installed = cache / SDK_ARCHIVE, cache / "sdk"
    if not archive.exists():
        print(f"Downloading the Windows x64 .NET SDK {SDK}", flush=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temp:
            download = Path(temp.name)
        try:
            urllib.request.urlretrieve(SDK_URL, download)
            if digest(download, "sha512").hex() != SDK_HASH:
                raise ValueError("Microsoft SDK download failed its SHA-512 check.")
            download.replace(archive)
        finally:
            download.unlink(missing_ok=True)
    if digest(archive, "sha512").hex() != SDK_HASH:
        raise ValueError(f"The SDK archive failed its SHA-512 check. Move {archive} aside and retry.")
    if not installed.exists():
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            extracted = Path(temp) / "sdk"
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
            extracted.rename(installed)
    exe = installed / "dotnet.exe"
    if output(exe, "--version") != SDK:
        raise ValueError(f"The project-local SDK has an unexpected version. Move {installed} aside and retry.")
    return exe


def stage_sources():
    source = BUILD / "sources/ryubing"
    git = ["git", "-c", "http.sslBackend=schannel", "-C", str(source)]
    if not (source / ".git").is_dir():
        run("git", "init", "-q", source)
        run(*git, "sparse-checkout", "init", "--cone")
        run(*git, "sparse-checkout", "set", *PROJECTS)
    if subprocess.run([*git, "cat-file", "-e", RYUBING + "^{commit}"], capture_output=True).returncode:
        run(*git, "fetch", "--depth=1", "--filter=blob:none", REPO, RYUBING)
    if output(*git, "status", "--porcelain"):
        raise ValueError(f"The local shader source checkout has changes. Inspect {source} and retry.")
    run(*git, "checkout", "-q", "--detach", RYUBING)
    if output(*git, "rev-parse", "HEAD") != RYUBING:
        raise ValueError("Ryubing commit verification failed.")
    tracked = output(*git, "ls-files", "-z", "--", *PROJECTS, *FILES).rstrip("\0").split("\0")
    stage = SHADERS / "third_party/ryubing"
    if not stage.exists():
        stage.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=stage.parent) as temp:
            tree = Path(temp) / "ryubing"
            for rel in tracked:
                dest = tree / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / rel, dest)
            tree.rename(stage)
    existing = {p.relative_to(stage).as_posix() for p in stage.rglob("*")
                if p.is_file() and not {"obj", "bin"}.intersection(p.relative_to(stage).parts)}
    if existing != set(tracked) or any(not filecmp.cmp(stage / rel, source / rel, shallow=False) for rel in tracked):
        raise ValueError(f"The staged shader source differs from the pinned checkout. Move {stage} aside and retry.")


def verify_packages():
    assets = json.loads((BUILD / "obj/SpirvHarness/project.assets.json").read_text(encoding="utf-8-sig"))
    expected = json.loads(Path(__file__).with_suffix(".nuget.json").read_text(encoding="utf-8"))
    observed = {}
    for name, entry in assets["libraries"].items():
        if entry["type"] == "package":
            archive = BUILD / "nuget" / entry["path"] / (name.lower().replace("/", ".") + ".nupkg")
            observed[name] = base64.b64encode(digest(archive, "sha512")).decode("ascii")
    if expected != observed:
        raise ValueError("The restored NuGet package versions or archive digests differ from the shader dependency lock.")
    return len(observed)


def fixtures(dotnet, env, report):
    # Encodings come from SassDecoder.Ctrl.cs / DecodeContext.cs. The control layout is read by TranslateNvn2.
    code = struct.pack("<QQQQ", 0x7B1D, 0, 0x794D, 7 << 23)  # @PT BAR.SYNC 0, @PT EXIT PT
    ctrl = bytearray(0xD8)
    struct.pack_into("<II", ctrl, 0, 0x98761234, 201)
    struct.pack_into("<I", ctrl, 0x38, len(code))
    struct.pack_into("<H", ctrl, 0x56, 5)
    struct.pack_into("<III", ctrl, 0x58, 32, 1, 1)
    (report / "barrier-code.bin").write_bytes(code)
    (report / "barrier-ctrl.bin").write_bytes(ctrl)
    encoded = report / "barrier.spv"
    run(dotnet, DLL, report / "barrier-code.bin", report / "barrier-ctrl.bin", "--no-retry", "-o", encoded, env=env)
    assembly = output("spirv-dis", encoded)
    if any(op not in assembly for op in ("OpEntryPoint GLCompute", "LocalSize 32 1 1", "OpControlBarrier")):
        raise ValueError("The encoded barrier fixture lost its compute entry point, workgroup size or synchronization.")
    warp = report / "warp"
    run(dotnet, DLL, "--warp-tests", warp, env=env)
    modules = sorted(warp.glob("*.spv"))
    if len(modules) != 45:
        raise ValueError(f"Expected 45 upstream synthetic warp programs for this revision, got {len(modules)}.")
    checksums = {}
    for module in [encoded, *modules]:
        run("spirv-val", "--target-env", "vulkan1.3", module)
        checksums[module.relative_to(report).as_posix()] = digest(module, "sha256").hex()
    return checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2, help="MSBuild concurrency (default 2)")
    parser.add_argument("--rebuild", action="store_true", help="rerun all project compilation targets")
    args = parser.parse_args()
    if os.name != "nt" or args.jobs < 1 or not all(shutil.which(cmd) for cmd in ("git", "spirv-val", "spirv-dis")):
        parser.error("Use Windows x64 with Git, spirv-val and spirv-dis on PATH and at least one build job.")
    BUILD.mkdir(exist_ok=True)
    env = {**os.environ, "DOTNET_CLI_HOME": str(BUILD / "cli"), "NUGET_PACKAGES": str(BUILD / "nuget"),
           "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
           "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "1"}
    dotnet = sdk(env)
    stage_sources()
    run(dotnet, "restore", PROJECT, "--source", NUGET, env=env)
    packages = verify_packages()
    run(dotnet, "build", PROJECT, "--disable-build-servers", "--no-restore", "-c", "Release", f"-m:{args.jobs}",
        *(["-t:Rebuild"] if args.rebuild else []), env=env)
    runs = BUILD / "verification"
    runs.mkdir(exist_ok=True)
    report = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    checksums = fixtures(dotnet, env, report)
    result = {"sdk": SDK, "ryubing_commit": RYUBING, "nuget_packages": packages,
              "translator_sha256": digest(DLL, "sha256").hex(), "validator": output("spirv-val", "--version").splitlines()[0],
              "target_env": "vulkan1.3", "modules_sha256": checksums}
    (report / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(checksums)} synthetic modules. Evidence: {report / 'verification.json'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
