# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract the pinned Windows x64 LLVM tools into this FagriShot checkout."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "23.1.5"
# .github/workflows/ci.yml at the foundation revision and LLVM's release asset metadata.
SHA256 = "b2be33385ddc86e83511417a060de927ae8b09d1f3017cc3cdce5c73cd5029d7"
PACKAGE = f"clang+llvm-{VERSION}-x86_64-pc-windows-msvc"
URL = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{VERSION}/{PACKAGE.replace('+', '%2B')}.tar.zst"
WORK = ROOT / "work" / "toolchain"
TOOLS = {"clang", "clang++", "clang-cl", "ld.lld", "lld-link", "llvm-ar", "llvm-lib", "llvm-rc",
         "llvm-mt", "llvm-ranlib", "llvm-objdump", "llvm-objcopy", "llvm-symbolizer", "llvm-dlltool",
         "llvm-nm", "llvm-readobj"}


def download():
    cache = WORK / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"llvm-{VERSION}.tar.zst"
    if not archive.is_file():
        with tempfile.TemporaryDirectory(dir=cache) as tmp:
            partial = Path(tmp) / "download"
            subprocess.run(["curl.exe", "--fail", "--location", "--retry", "2", "--silent",
                            "--show-error", "--output", partial, URL], check=True)
            verify_digest(partial)
            partial.replace(archive)
    else:
        verify_digest(archive)
    return archive


def verify_digest(path):
    with path.open("rb") as f:
        actual = hashlib.file_digest(f, "sha256").hexdigest()
    if actual != SHA256:
        raise SystemExit(f"LLVM archive hash {actual} differs. Replace {path} with the pinned asset.")


def extract(archive, dest):
    import zstandard

    # Upstream uses zstd --long. The ordinary Windows tar default rejects its 1 GiB window.
    decoder = zstandard.ZstdDecompressor(max_window_size=2**31)
    filenames = {t + ".exe" for t in TOOLS}
    with archive.open("rb") as src, decoder.stream_reader(src) as reader:
        with tarfile.open(fileobj=reader, mode="r|", bufsize=1024 * 1024) as tar:
            for entry in tar:
                path = PurePosixPath(entry.name)
                if not entry.isfile() or not path.parts or path.parts[0] != PACKAGE:
                    continue
                relative = path.relative_to(PACKAGE)
                if relative.parts[:2] != ("lib", "clang") and not (
                    relative.parent == PurePosixPath("bin") and relative.name in filenames
                ) and relative.name.lower() not in {"license", "license.txt"}:
                    continue
                target = (dest / relative).resolve()
                if not target.is_relative_to(dest):
                    raise ValueError(f"Invalid compiler archive path {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as member, target.open("xb") as out:
                    shutil.copyfileobj(member, out, length=1024 * 1024)


def verify_install(dest):
    for name in TOOLS:
        if not (dest / "bin" / f"{name}.exe").is_file():
            raise SystemExit(f"Incomplete LLVM installation in {dest}. Missing {name}.exe.")
    ver = subprocess.check_output([dest / "bin" / "clang-cl.exe", "--version"], text=True)
    if f"clang version {VERSION} " not in ver and f"clang version {VERSION}\n" not in ver:
        raise SystemExit(f"Unexpected installed compiler version in {dest}: {ver}")
    if "x86_64-pc-windows-msvc" not in ver:
        raise SystemExit(f"Unexpected compiler target in {dest}: {ver}")
    print(ver, end="")


def main():
    if os.name != "nt":
        raise SystemExit("This compiler package targets Windows x64.")
    try:
        import zstandard  # noqa: F401
    except ImportError:
        raise SystemExit("Run scripts/setup_python.py, then tools/.venv/Scripts/python.exe -I scripts/setup_llvm.py")
    dest = WORK / f"llvm-{VERSION}"
    if dest.exists():
        verify_install(dest)
    else:
        archive = download()
        print(f"Verified LLVM {VERSION} archive, extracting project-local build tools...", flush=True)
        with tempfile.TemporaryDirectory(dir=WORK) as temp:
            staging = Path(temp).resolve() / "compiler"
            staging.mkdir()
            extract(archive, staging)
            verify_install(staging)
            staging.rename(dest)
    print(f"NX_LLVM and NX_LLVM_BIN: {dest / 'bin'}")


if __name__ == "__main__":
    main()
