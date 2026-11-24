"""Assemble and execute-check the private FagriShot Windows tooling preview."""

import argparse
import ctypes
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
OUT = WORK / "packages"
sys.path.insert(0, str(ROOT))
from fagrishot import UPSTREAM, VERSION


def git(*args, binary=False):
    return subprocess.check_output(["git", "-C", ROOT, *args], text=not binary)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(data):
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def notice(path):
    return path.name.lower().startswith(("license", "licence", "copying", "copyright", "notice", "unlicense"))


def external_text(name, url, digest):
    # Some released Rust crates omit their repository-level text. Only individually pinned notices may be cached.
    path = WORK / "package-licenses" / digest
    if not path.exists():
        with urllib.request.urlopen(url, timeout=30) as stream:
            data = stream.read()
        if name == "WebView2Loader":
            match = re.search(r'<pre\b[^>]*license-file-contents[^>]*>(.*?)</pre>', data.decode(), re.S)
            if not match:
                raise ValueError("The NuGet WebView2 license page changed.")
            data = html.unescape(match[1]).replace("\r\n", "\n").strip().encode() + b"\n"
        if sha(data) != digest:
            raise ValueError(f"Downloaded license for {name} differs from its pinned SHA-256.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = path.read_bytes()
    if sha(data) != digest:
        raise ValueError(f"Cached license for {name} differs from its pinned SHA-256.")
    return data


def launcher_licenses(payload, base=None):
    base = WORK / "launcher" if base is None else Path(base)
    rust = base / "rust-1.100.0"
    env = {**os.environ, "CARGO_HOME": str(base / "cargo"), "CARGO_TARGET_DIR": str(base / "target"),
           "PATH": str(rust / "bin") + os.pathsep + os.environ["PATH"]}
    meta = json.loads(subprocess.check_output([rust / "bin/cargo.exe", "metadata", "--locked", "--offline",
        "--format-version", "1", "--filter-platform", "x86_64-pc-windows-msvc", "--manifest-path",
        ROOT / "launcher/src-tauri/Cargo.toml"], env=env, cwd=ROOT))
    lock = tomllib.loads((ROOT / "launcher/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    hashes = {(p["name"], p["version"]): p["checksum"] for p in lock["package"] if "checksum" in p}
    rust_notices = {}
    summary = []
    for package in sorted(meta["packages"], key=lambda p: (p["name"], p["version"])):
        if not package["source"]:
            continue
        name, version = package["name"], package["version"]
        slug = f"{name}-{version}"
        src = Path(package["manifest_path"]).parent
        crate = base / "cargo/registry/cache" / src.parent.name / (slug + ".crate")
        if sha(crate.read_bytes()) != hashes[name, version]:
            raise ValueError(f"Cargo registry archive for {slug} differs from Cargo.lock.")
        copies = {}
        with tarfile.open(crate) as archive:
            for member in archive.getmembers():
                p = Path(member.name)
                if not member.isfile() or len(p.parts) < 2 or p.parts[0] != slug or ".." in p.parts:
                    continue
                if ((len(p.parts) == 2 and notice(p)) or p.parts[1].lower() in {"licenses", "licences"}
                        or str(Path(*p.parts[1:])) == package.get("license_file")):
                    copies[Path(*p.parts[1:]).as_posix()] = archive.extractfile(member).read()
        rust_notices[name, version] = copies
        summary.append({"package": name, "version": version, "license": package.get("license"),
                        "source": package.get("repository"), "crate_sha256": hashes[name, version]})
    github = "https://raw.githubusercontent.com/"
    for (name, version), copies in rust_notices.items():
        if not copies and name.startswith("webview2-com"):
            copies["LICENSE"] = external_text(name, github + "wravery/webview2-rs/edc2caf886175ccaebe86078c9cfe1ae2a187328/LICENSE",
                                               "0dcf41516e608bbcb6cdc5229feb7b86fe4a643b85e7df251133c93408fdac73")
        if not copies and name == "alloc-stdlib":
            copies["LICENSE"] = external_text(name, github + "dropbox/rust-alloc-no-stdlib/0a81fd6928ea3b33c8cd484aa4575d50ffb98012/LICENSE",
                                               "c0c56f26d9c051cac4d200c34c84e7ae9aaa853e01a982a1df08b09931e518ae")
        if not copies and name == "selectors":
            copies["MPL-2.0"] = rust_notices["cssparser", "0.37.0"]["LICENSE"]
        if not copies and name == "tauri-plugin":
            prefix = github + "tauri-apps/tauri/59b63ab500da9859f12d8888c3298bc29bba0b92/"
            for file, digest in (("LICENSE-MIT", "9dd42ea92cff2ede5cd477cbfcce051b2d0115c0ac7f368ee88cb545055dff1d"),
                                 ("LICENSE-APACHE-2.0", "0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594")):
                copies[file] = external_text(name, prefix + file, digest)
        if not copies:
            raise ValueError(f"Missing a license notice for {name} {version}.")
        for file, data in copies.items():
            payload[f"licenses/cargo/{name}-{version}/{file}"] = data
    payload["licenses/cargo/packages.json"] = encoded(summary)
    payload["licenses/microsoft/WebView2Loader-1.0.3800.47.txt"] = external_text("WebView2Loader",
        "https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3800.47/License",
        "324083bf015092054be9d5851dafa1b79cbfab3331ff983476d502839515d8bb")
    rust_doc = rust / "share/doc/rust"
    payload["licenses/rust/COPYRIGHT-library.html"] = (rust_doc / "COPYRIGHT-library.html").read_bytes()
    for p in (rust_doc / "licenses").glob("*.txt"):
        payload["licenses/rust/licenses/" + p.name] = p.read_bytes()
    npm = json.loads((ROOT / "launcher/package-lock.json").read_text(encoding="utf-8"))
    js = []
    for name in ("@tauri-apps/api", "lucide"):
        prefix = "node_modules/" + name
        for path in (ROOT / "launcher" / prefix).iterdir():
            if path.is_file() and notice(path):
                payload["licenses/npm/" + name + "/" + path.name] = path.read_bytes()
        js.append({"package": name, **{k: npm["packages"][prefix][k] for k in ("version", "integrity", "resolved")}})
    payload["licenses/npm/packages.json"] = encoded(js)


def contents(revision, draft):
    payload = {}

    def source(name, dest=None):
        payload[dest or name] = (ROOT / name).read_bytes() if draft else git("show", revision + ":" + name, binary=True)

    # Every working-tree input to a draft has an explicit name or is a tracked license path.
    for file in ("__init__", "__main__", "arabic", "arabic_font", "build", "graphics", "inputs", "localization",
                 "localization_art", "player_build", "saves", "shaders", "storage"):
        source(f"fagrishot/{file}.py")
    for name in git("ls-files", "--", "fagrishot/arabic_data/*.json").splitlines():
        source(name)
    for file in ("scripts/fagrishot.py", "compatibility/supported.json", "docs/package.md", "docs/inputs.md", "docs/storage.md", "docs/status.md",
                 "docs/foundation.md", "docs/longshot.md", "docs/cpu-verification.md", "docs/runtime-verification.md",
                 "docs/shader-verification.md", "LICENSE"):
        source(file)
    for file in ("runtime/THIRD_PARTY_NOTICES.md", "recomp/THIRD_PARTY.md", "runtime/assets/fonts/LICENSE-DejaVu.txt",
                 "runtime/assets/prompts/LICENSE-Kenney-InputPrompts.txt", "runtime/LICENSE", "launcher/LICENSE",
                 "recomp/LICENSE", "game-build/fefw/third_party/zstd/LICENSE"):
        source(file, "licenses/" + file)
    for file in git("ls-tree", "-r", "--name-only", revision, "--", "runtime/third_party", "tools/textures/licenses",
                    "tools/tiling/licenses").splitlines():
        if notice(Path(file)):
            source(file, "licenses/" + file)
    runtime = WORK / "runtime-release"
    for dep in ("sdl3", "imgui", "volk", "vma", "vulkan_headers", "zstd"):
        folder = runtime / "_deps" / (dep + "-src")
        notices = [p for p in folder.iterdir() if p.is_file() and notice(p)]
        notices.extend((folder / "LICENSES").glob("*.txt"))
        if not notices:
            raise ValueError(f"Missing runtime notices for {dep}. Rebuild the pinned runtime.")
        for file in notices:
            payload["licenses/runtime-fetched/" + dep + "/" + file.relative_to(folder).as_posix()] = file.read_bytes()
    for name in ("fefw_rt.dll", "fefw_rt.lib", "fonts/DejaVuSans.ttf"):
        payload["runtime-bin/" + name] = (runtime / name).read_bytes()
    payload["FagriShotLauncher.exe"] = (WORK / "tools/FagriShotLauncher.exe").read_bytes()
    launcher_licenses(payload)
    payload["SOURCE.txt"] = (f"FagriShot {VERSION} private Windows x64 tooling preview\n"
        f"Companion source: maintainer's local FagriShot Git checkout\nRevision: {revision}\n"
        f"Working tree inputs included: {draft}\nPinned upstream: {UPSTREAM}\n"
        "Build instructions: docs/runtime-verification.md and scripts/build_launcher.py in the companion checkout.\n"
        "Runtime dependencies: runtime/cmake/gfx_deps.cmake and tools/textures/scripts/fetch_third_party.ps1.\n"
        "Launcher dependencies: launcher/src-tauri/Cargo.lock and launcher/package-lock.json.\n"
        "The Python entry point runs the adjacent FagriShot tools. This preview requires system Python 3.13 x64.\n"
        "Retain this checkout and its pinned build inputs with the private archive.\n").encode()
    payload["manifest.json"] = encoded({"product": "FagriShot", "version": VERSION, "target": "Windows-x64",
        "type": "local-tooling-preview", "revision": revision, "draft": draft, "upstream": UPSTREAM,
        "files": {k: {"bytes": len(v), "sha256": sha(v)} for k, v in payload.items()}})
    return payload


def verify(archive):
    with tempfile.TemporaryDirectory(prefix="FagriShot-package-") as temp:
        area = Path(temp)
        install = area / "FagriShot \u65e5\u672c\u8a9e"
        with zipfile.ZipFile(archive) as bundle:
            if corrupt := bundle.testzip():
                raise ValueError(f"Archive CRC failure at {corrupt}.")
            manifest = json.loads(bundle.read("manifest.json"))
            names = bundle.namelist()
            if len(names) != len(set(names)) or set(names) != {"manifest.json", *manifest["files"]}:
                raise ValueError("Unexpected archive entry.")
            for name, record in manifest["files"].items():
                data = bundle.read(name)
                if len(data) != record["bytes"] or sha(data) != record["sha256"]:
                    raise ValueError("Payload hash mismatch at " + name)
            bundle.extractall(install)
        env = {**os.environ, "LOCALAPPDATA": str(area / "state")}
        cli = [sys.executable, "-I", "-X", "utf8", str(install / "scripts/fagrishot.py")]
        status = json.loads(subprocess.check_output([*cli, "status", "--json"], cwd=area, env=env))
        launcher = json.loads(subprocess.check_output([install / "FagriShotLauncher.exe", "status", "--json"], cwd=area, env=env))
        if status != launcher or status["version"] != VERSION or Path(status["exefs"]).parent != install:
            raise ValueError("The extracted launcher did not resolve the adjacent packaged CLI.")
        packaged_exe = install / "FagriShotLauncher.exe"
        def action(*args):
            return subprocess.check_output([packaged_exe, *args], cwd=area, env=env, text=True, encoding="utf-8")
        doctor = action("doctor")
        if str(install / "runtime-bin/fefw_rt.dll") not in doctor:
            raise ValueError("Packaged diagnostics missed the distributed runtime DLL.")
        game = area / "synthetic #1 \u0627\u062e\u062a\u0628\u0627\u0631"
        action("configure", "--exefs", str(game / "exefs"), "--romfs", str(game / "romfs"))
        selected = json.loads(action("status", "--json"))
        if Path(selected["exefs"]) != game / "exefs" or Path(selected["romfs"]) != game / "romfs":
            raise ValueError("The extracted setup did not persist its selected paths.")
        rejected = subprocess.run([packaged_exe, "inspect"], cwd=area, env=env, capture_output=True, encoding="utf-8")
        if rejected.returncode == 0 or "missing or unreadable" not in rejected.stderr:
            raise ValueError("The extracted input checker did not diagnose absent source data.")
        saves = Path(selected["saves"])
        if not saves.resolve().is_relative_to((area / "state").resolve()):
            raise ValueError("The package did not isolate its save tree during verification.")
        (saves / "fixture").write_bytes(b"FagriShot original save-tool fixture")
        backup_id = action("backup").strip()
        if backup_id not in action("backups").splitlines():
            raise ValueError("The extracted save backup is missing from the list.")
        (saves / "fixture").write_bytes(b"second state")
        action("restore", backup_id)
        if (saves / "fixture").read_bytes() != b"FagriShot original save-tool fixture":
            raise ValueError("Packaged restore did not recover the original synthetic bytes.")
        library = ctypes.WinDLL(str(install / "runtime-bin/fefw_rt.dll"))
        definition = (ROOT / "game-build/fefw/src/fefw_rt.def").read_text(encoding="utf-8")
        exports = []
        from _ctypes import FreeLibrary
        try:
            for line in definition.split("EXPORTS", 1)[1].splitlines():
                tokens = line.partition(";")[0].split()
                if tokens:
                    getattr(library, tokens[0])
                    exports.append(tokens[0])
        finally:
            FreeLibrary(library._handle)
        return {"manifest_entries": len(manifest["files"]), "launcher_status_matches_adjacent_cli": True,
                "runtime_exports_resolved": exports, "diagnostics_located_packaged_runtime": True,
                "selected_inputs_persisted": True, "absent_input_rejected": True, "synthetic_backup_roundtrip": True,
                "source_revision": manifest["revision"], "draft": manifest["draft"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize < 2**32:
        parser.error("Use 64-bit Windows to verify the launcher and the native runtime.")
    revision = git("rev-parse", "HEAD").strip()
    if not args.draft and git("status", "--porcelain").strip():
        parser.error("Commit the reviewed source or use --draft to label a working-tree build.")
    if not args.draft:
        subprocess.run([sys.executable, ROOT / "scripts/build_launcher.py"], cwd=ROOT, check=True)
        subprocess.run(["pwsh", "-NoProfile", "-File", ROOT / "scripts/build_runtime.ps1"], cwd=ROOT, check=True)
        if git("rev-parse", "HEAD").strip() != revision or git("status", "--porcelain").strip():
            raise ValueError("The checkout changed during the build. Package the intended revision again.")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"FagriShot-{VERSION}-Windows-x64.zip"
    payload = contents(revision, args.draft)
    with tempfile.NamedTemporaryFile(dir=OUT, suffix=".zip", delete=False) as out:
        staged = Path(out.name)
    try:
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, data in sorted(payload.items()):
                member = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                member.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(member, data, compresslevel=9)
        record = verify(staged)
        record["archive_sha256"] = sha(staged.read_bytes())
        staged.replace(dest)
        dest.with_suffix(".verification.json").write_bytes(encoded(record))
    finally:
        staged.unlink(missing_ok=True)
    print(f"{dest}\n{record['archive_sha256']}\n{len(record['runtime_exports_resolved'])} runtime exports and adjacent launcher CLI verified.")


if __name__ == "__main__":
    main()
