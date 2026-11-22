"""Build the Windows launcher from the locked Tauri and Vite source trees."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "launcher"
VERSION = "1.100.0"
TARGET = "x86_64-pc-windows-msvc"
RUST = {
    "rustc": "4f1f70145ab94e1036b6a9fb0a1fa1393352999701699aedab4dd2724047149e",
    "cargo": "efb63c35df751dbd58b7aa73d6a43d75b00bd356bfdb925901e85379d0de108a",
    "rust-std": "24ad2f28791e08a573b9c7df5efa3ace884eecd1fa7e7f5b62c65f4b49bb4532",
}


def rust_toolchain():
    prefix = WORK / ("rust-" + VERSION)
    prefix.mkdir(parents=True, exist_ok=True)
    for name, digest in RUST.items():
        stem = f"{name}-{VERSION}-{TARGET}"
        archive = WORK / (stem + ".tar.xz")
        if not archive.is_file():
            partial = archive.with_suffix(".part")
            url = "https://static.rust-lang.org/dist/2026-11-12/" + archive.name
            print("Fetching", name, flush=True)
            with urllib.request.urlopen(url, timeout=90) as src, partial.open("wb") as out:
                shutil.copyfileobj(src, out)
            partial.replace(archive)
        with archive.open("rb") as src:
            if hashlib.file_digest(src, "sha256").hexdigest() != digest:
                raise SystemExit(f"Hash mismatch in {archive}. Remove that archive and retry.")
        component = name if name != "rust-std" else f"{name}-{TARGET}"
        subprocess.run(["tar", "-xf", str(archive), "--strip-components", "2", "-C",
                        str(prefix), stem + "/" + component], check=True)
    return prefix / "bin"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="install pinned Rust tools under work/launcher")
    parser.add_argument("--deps-only", action="store_true", help="fetch locked dependencies without compiling")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This target requires Windows x64.")
    env = os.environ.copy()
    local_bin = WORK / ("rust-" + VERSION) / "bin"
    if args.bootstrap:
        rust_toolchain()
    if (local_bin / "cargo.exe").is_file():
        env["PATH"] = str(local_bin) + os.pathsep + env["PATH"]
    cargo = shutil.which("cargo", path=env["PATH"])
    if not cargo:
        raise SystemExit("Rust is missing. Run this command with --bootstrap.")
    env["NODE_USE_SYSTEM_CA"] = "1"
    env.update(CARGO_HOME=str(WORK / "cargo"), CARGO_TARGET_DIR=str(WORK / "target"),
               CARGO_BUILD_JOBS=str(min(8, os.cpu_count() or 1)))
    source = ROOT / "launcher"
    def run(command, cwd):
        subprocess.run(command, cwd=cwd, env=env, check=True)
    run(["npm.cmd", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], source)
    run([cargo, "fetch", "--locked", "--target", TARGET], source / "src-tauri")
    if args.deps_only:
        return
    encoded = env.get("CARGO_ENCODED_RUSTFLAGS")
    flags = encoded.split("\x1f") if encoded else env.get("RUSTFLAGS", "").split()
    # Rust's file!() and panic locations otherwise contain absolute build paths.
    for prefix, replacement in [(Path.home(), "build-home"), (Path(cargo).parent.parent, "rust"),
                                (ROOT, "."), (WORK / "cargo", "cargo")]:
        flags.append(f"--remap-path-prefix={prefix}={replacement}")
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    run(["npm.cmd", "run", "build"], source)
    run([cargo, "build", "--release", "--locked", "--features", "tauri/custom-protocol"],
        source / "src-tauri")
    output = ROOT / "work" / "tools" / "FagriShotLauncher.exe"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORK / "target" / "release" / output.name, output)
    print(output)


if __name__ == "__main__":
    main()
