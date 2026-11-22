# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and run the FagriShot synthetic ARM64 checks on Windows x64."""

import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "cpu"
TESTS = ROOT / "tests" / "cpu"
RECOMP = ROOT / "recomp"
ORACLE = RECOMP / "oracle" / "e2e"


def bootstrap():
    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 13):
        raise SystemExit("Run with 64-bit CPython 3.13 on Windows.")
    WORK.mkdir(parents=True, exist_ok=True)
    python = WORK / "venv" / "Scripts" / "python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-I", "-m", "venv", python.parents[1]], check=True)
    subprocess.run([python, "-I", "-m", "pip", "--disable-pip-version-check", "install",
                    "--only-binary=:all:", "--require-hashes", "--no-deps", "-r",
                    TESTS / "requirements-windows-x64.txt"], check=True)
    return subprocess.call([python, "-I", __file__, "--worker"])


def toolchain_environment():
    env = os.environ.copy()
    for key in ("NXRECOMP_OPT", "NX_POKE", "CL", "_CL_", "LINK", "_LINK_"):
        env.pop(key, None)
    # Clang 20 supports the VS 2022 v143 standard library. If both VS 2022 and
    # 2026 are installed, Clang's default 2026 STL requires a newer compiler.
    if not env.get("VCTOOLSINSTALLDIR"):
        vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
        if vswhere.is_file():
            query = [vswhere, "-latest", "-products", "*", "-version", "[17,18)", "-requires",
                     "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"]
            install = subprocess.check_output(query, text=True).strip()
            if install:
                vc = Path(install) / "VC"
                tools_version = (vc / "Auxiliary/Build/Microsoft.VCToolsVersion.default.txt").read_text().strip()
                env["VCTOOLSINSTALLDIR"] = str(vc / "Tools/MSVC" / tools_version)
    return env


def verify(build, log):
    os.environ.pop("NXRECOMP_OPT", None)
    sys.path[:0] = [str(RECOMP), str(ORACLE)]
    from nxrecomp.program import lift_module
    from nxrecomp.image import Image
    import run_e2e
    import unicorn_side

    env = toolchain_environment()
    local = ROOT / "work/toolchain/llvm-23.1.5/bin/clang++.exe"
    clang = Path(os.environ["NX_LLVM"]) / "clang++.exe" if os.environ.get("NX_LLVM") else (
        local if local.is_file() else shutil.which("clang++")
    )
    if clang is None:
        raise RuntimeError("Add the LLVM bin folder containing clang++, clang and ld.lld to PATH.")
    llvm = Path(clang).parent

    def run(*args, expected=0):
        cmd = [str(arg) for arg in args]
        log(subprocess.list2cmdline(cmd))
        result = subprocess.run(cmd, cwd=build, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=180)
        if result.stdout:
            log(result.stdout.rstrip())
        if result.returncode != expected:
            raise RuntimeError(f"Exit {result.returncode}, expected {expected} from {Path(cmd[0]).name}")
        return result.stdout

    def native(exe, sources):
        run(clang, "-std=c++20", "-O1", "-mcx16", "-ffp-contract=off", "-fno-math-errno",
            "-fno-strict-aliasing", "-D_CRT_SECURE_NO_WARNINGS", "-I", RECOMP / "include",
            *sources, "-o", exe)
        with exe.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError(f"Missing DOS header in {exe.name}")
            stream.seek(0x3C)
            offset, = struct.unpack("<I", stream.read(4))
            stream.seek(offset)
            if stream.read(6) != b"PE\0\0\x64\x86":
                raise RuntimeError(f"{exe.name} is not a Windows AMD64 PE")

    log(f"Python {sys.version.splitlines()[0]}, Unicorn {version('unicorn')}, Capstone {version('capstone')}")
    log(f"MSVC tools {env.get('VCTOOLSINSTALLDIR', '(compiler default)')}")
    run(clang, "--version")
    if shutil.which("git"):
        run("git", "-C", ROOT, "rev-parse", "HEAD")
    flags = build / "nxflags_test.exe"
    native(flags, [RECOMP / "tests/nxflags_test.cpp"])
    run(flags)

    for name in ("original", "unsupported"):
        folder = build / name
        folder.mkdir()
        elf = folder / "guest.elf"
        gen = folder / "generated"
        run(llvm / "clang.exe", "--target=aarch64-linux-gnu", "-nostdlib", "-static", "-fuse-ld=lld",
            "-Wl,--image-base=0x7100000000,-e,test_main,--build-id=none,-z,norelro",
            TESTS / f"{name}.S", "-o", elf)
        log(f"{name} ELF SHA-256 {hashlib.sha256(elf.read_bytes()).hexdigest()}")
        disassembly = run(llvm / "llvm-objdump.exe", "-d", elf)
        (folder / "disassembly.txt").write_text(disassembly, encoding="utf-8")
        spec = {"kind": "static_elf", "path": str(elf), "name": "main", "game": "generic"}
        report = lift_module(spec, str(gen), jobs=1, shards=1, log=log)
        expected_bad = 1 if name == "unsupported" else 0
        if report["not_translated"] != expected_bad or report["instructions"] < 2:
            raise RuntimeError(f"Unexpected {name} translation counts: {report}")
        img = Image.from_static_elf(str(elf))
        entry = img.meta["entry"]
        log(f"{name} module main, entry 0x{entry:x}")
        exe = folder / "cpu_fixture.exe"
        native(exe, [ORACLE / "rt/x64_runner.cpp", ORACLE / "rt/eh_rt.cpp", *sorted(gen.glob("*.cpp"))])
        prefix = str(folder / "native-state")
        if name == "unsupported":
            instruction = img.word(entry)
            diagnostic = run(exe, elf, prefix, expected=11)
            if f"guest_unimpl at {entry:x}: {instruction:08x}" not in diagnostic:
                raise RuntimeError("The runner did not identify the unsupported word and its guest PC.")
            continue
        run(exe, elf, prefix)
        reference = unicorn_side.run(str(elf), "a57")
        if not reference["finished"] or reference["x"][9] != 0x44 or reference["x"][2] != 0x13:
            raise RuntimeError("Unicorn did not reach the fixture's expected return and register state.")
        regions = unicorn_side.rw_regions(img)
        byte_count = sum(size for _, _, size in regions)
        if Path(prefix + ".bin").stat().st_size != byte_count:
            raise RuntimeError("The native state dump omitted or added writable memory.")
        regs, memory = run_e2e.read_x64(prefix, img)
        diffs = run_e2e.compare(reference, regs, memory)
        (folder / "unicorn-state.json").write_text(json.dumps(reference, indent=2), encoding="utf-8")
        if diffs:
            raise RuntimeError("Native/Unicorn differences: " + ", ".join(diffs))
        log(f"MATCH x0..x30, SP, v0..v31, NZCV and {byte_count:,} bytes in {len(regions)} writable regions")
    log("PASS synthetic CPU verification")


def worker():
    build = Path(tempfile.mkdtemp(prefix="run-", dir=WORK))
    with (build / "verification.log").open("w", encoding="utf-8") as output:
        def log(message):
            print(message, flush=True)
            print(message, file=output, flush=True)
        log(f"CPU verification artifacts: {build}")
        try:
            verify(build, log)
        except Exception as error:
            log(f"FAIL {error}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(worker() if sys.argv[1:] == ["--worker"] else bootstrap())
