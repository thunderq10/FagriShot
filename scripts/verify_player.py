"""Extract and exercise the player ZIP with the Windows system directory alone on PATH."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile


# Windows 11 desktop components, plus the Vulkan loader supplied by the display driver.
# A developer runtime installed in System32 is deliberately insufficient here.
WINDOWS = set("""
advapi32 avrt bcrypt bcryptprimitives cfgmgr32 combase comctl32 comdlg32 crypt32 cryptbase
d2d1 d3d11 d3d12 d3dcompiler_47 dcomp dbghelp dhcpcsvc dnsapi dwmapi dwrite dxgi
gdi32 hid imm32 iphlpapi kernel32 kernelbase mf mfplat mfreadwrite mscoree msvcrt
ncrypt netapi32 normaliz ntdll ole32 oleacc oleaut32 powrprof profapi propsys
psapi rpcrt4 secur32 setupapi shell32 shcore shlwapi sspicli ucrtbase urlmon
user32 userenv usp10 uxtheme version vulkan-1 windowscodecs winhttp wininet winmm
winspool.drv wintrust ws2_32 wtsapi32
""".split())


def imports(data):
    """Read normal and delay-load PE import names directly from the packaged binary."""
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid Windows PE signature.")
    sections = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    wide = struct.unpack_from("<H", data, opt)[0] == 0x20b
    directories = opt + (112 if wide else 96)
    base = struct.unpack_from("<Q" if wide else "<I", data, opt + (24 if wide else 28))[0]
    ranges = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(sections)]

    def offset(rva):
        for vsize, start, rawsize, raw in ranges:
            if start <= rva < start + max(vsize, rawsize):
                return raw + rva - start
        raise ValueError("Invalid PE import address.")

    names = set()
    for ix, fields, name_field in ((1, 5, 3), (13, 8, 1)):
        table, size = struct.unpack_from("<II", data, directories + ix * 8)
        if not table:
            continue
        start = offset(table)
        for pos in range(start, start + size, fields * 4):
            row = struct.unpack_from("<" + "I" * fields, data, pos)
            if not any(row):
                break
            address = row[name_field] - (base if ix == 13 and not row[0] & 1 else 0)
            p = offset(address)
            names.add(data[p:data.index(b"\0", p)].decode("ascii").lower())
    return names


def verify_imports(root, paths, *, draft=False):
    count = 0
    private_paths = []
    user = os.environ.get("USERPROFILE", "")
    prefixes = [path.lower().encode(encoding) for path in (user, user.replace("\\", "/"))
                for encoding in ("utf-8", "utf-16le") if len(path) > 6]
    for name in paths:
        file = root / name
        if file.suffix.lower() not in {".exe", ".dll", ".pyd"}:
            continue
        if name.startswith("tools/build/zig/lib/"):
            continue
        count += 1
        search = [file.parent]
        if name.startswith("tools/python/"):
            search.append(root / "tools/python")
        data = file.read_bytes()
        lower = data.lower()
        if any(prefix in lower for prefix in prefixes):
            private_paths.append(name)
        for dep in imports(data):
            if (dep.startswith(("api-ms-", "ext-ms-")) or dep.removesuffix(".dll") in WINDOWS
                    or any((p / dep).is_file() for p in search)):
                continue
            raise ValueError(f"{name} imports {dep} outside its bundled dependency directories.")
    if private_paths and not draft:
        raise ValueError("Remove build-machine paths from the release binaries: " + ", ".join(private_paths))
    return count, private_paths


def verify(path):
    from package_player import digest, member_name
    with tempfile.TemporaryDirectory(prefix="FagriShot player QA ") as temp:
        area = Path(temp).resolve()
        install = area / "Player Ü"
        with zipfile.ZipFile(path) as archive:
            names = [member_name(n) for n in archive.namelist()]
            if len({n.lower() for n in names}) != len(names):
                raise ValueError("The player archive has duplicate or case-colliding paths.")
            marker = json.loads(archive.read("player-package.json"))
            files = marker["files"]
            if (marker["schema"] != 1 or marker["product"] != "FagriShot" or marker["version"] != "0.2.0"
                    or set(files) != set(names) - {"player-package.json"}):
                raise ValueError("The player manifest does not cover the exact archive payload.")
            archive.extractall(install)
        for name, expected in files.items():
            if digest(install / name) != expected:
                raise ValueError(f"Player file has the wrong SHA-256: {name}")
        pe_count, private_paths = verify_imports(install, files, draft=marker["draft"])
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
            ("PYTHON", "DOTNET_", "ZIG_", "FEFW_", "NXRT_", "VULKAN_", "VSINSTALL", "VCINSTALL"))}
        for var, value in (("PATH", Path(os.environ["SystemRoot"]) / "System32"),
                           ("LOCALAPPDATA", area / "state"), ("APPDATA", area / "roaming"),
                           ("TEMP", area / "tmp"), ("TMP", area / "tmp"),
                           ("ZIG_GLOBAL_CACHE_DIR", area / "zig-global"), ("ZIG_LOCAL_CACHE_DIR", area / "zig-local")):
            value.mkdir(exist_ok=True)
            env[var] = str(value)
        env["DOTNET_MULTILEVEL_LOOKUP"] = "0"
        env["DOTNET_ROOT"] = str(area / "no-dotnet-installation")
        checks = []

        def run(label, args, ok=True, timeout=240):
            proc = subprocess.run(args, cwd=area, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
            if (proc.returncode == 0) != ok:
                raise ValueError(f"Player smoke test {label} returned {proc.returncode}: {(proc.stdout + proc.stderr)[-2000:]}")
            checks.append(label)
            return proc.stdout + proc.stderr

        launcher = install / "FagriShotLauncher.exe"
        exe = install / "tools/python/python.exe"
        cfg = json.loads(run("launcher status", [launcher, "status", "--json"]))
        if not Path(cfg["saves"]).resolve().is_relative_to(area):
            raise ValueError("The clean-install player's save location escaped its test profile.")
        run("embedded Python libraries", [exe, "-I", "-c",
            "import capstone,elftools,lz4.block,unicorn,zbic,zstandard"])
        run("persistent folder selection", [launcher, "configure", "--exefs", area / "dump/exefs", "--romfs", area / "dump/romfs"])
        failure = run("actionable missing-content diagnostic", [launcher, "build-and-prepare"], ok=False)
        if "missing or unreadable" not in failure.lower():
            raise ValueError(f"Unexpected setup diagnostic: {failure[-1000:]}")
        run("portable lifter CLI", [exe, "-I", install / "tools/build/fefw_build.py", "--help"])
        src = area / "compiler.cpp"
        src.write_text('#include <cstdio>\nint main(){std::puts("Player compiler OK");}\n', encoding="ascii")
        compiled = area / "compiler.exe"
        run("bundled C++ compiler and standard library", [install / "tools/build/zig/zig.exe", "c++",
            "-target", "x86_64-windows-gnu", "-O0", src, "-o", compiled], timeout=420)
        if "Player compiler OK" not in run("execute fresh native output", [compiled]):
            raise ValueError("The portable C++ compiler output did not run.")
        code, ctrl, spv = area / "code.bin", area / "ctrl.bin", area / "test.spv"
        code.write_bytes(struct.pack("<QQQQ", 0x7B1D, 0, 0x794D, 7 << 23))
        desc = bytearray(0xD8)
        struct.pack_into("<II", desc, 0, 0x98761234, 201)
        struct.pack_into("<I", desc, 0x38, 32)
        struct.pack_into("<HIII", desc, 0x56, 5, 32, 1, 1)
        ctrl.write_bytes(desc)
        run("self-contained shader translator", [install / "tools/spirv_harness/spirv_harness.exe", code, ctrl,
                                                "--no-retry", "-o", spv])
        run("app-local SPIR-V 1.3 validation", [install / "tools/spirv-val/spirv-val.exe", "--target-env", "vulkan1.3", spv])
        if spv.read_bytes()[:4] != bytes.fromhex("03022307"):
            raise ValueError("The isolated shader fixture has no SPIR-V header.")
        return {"source_revision": marker["revision"], "draft": marker["draft"], "sha256": digest(path),
                "size": path.stat().st_size, "payload_files": len(files), "pe_import_closures": pe_count,
                "binaries_with_builder_paths": private_paths, "checks": checks, "synthetic_spirv_sha256": digest(spv)}
