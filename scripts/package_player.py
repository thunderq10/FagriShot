"""Build the game-free, self-contained Windows x64 player distribution."""
import argparse
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_shaders
import package_tools

WORK = ROOT / "work"
DEPS = WORK / "player-deps"
REPOSITORY = "https://github.com/thunderq10/FagriShot"
PUBLIC_BASE = "fagrishot-v0.2.0"
ZIG = "zig-x86_64-windows-0.17.0"
DOWNLOADS = {
    "zig-0.17.0.zip": ("https://ziglang.org/download/0.17.0/" + ZIG + ".zip",
        "b5663f69581dcf391293fbf16c06cb80d81d806545ce618b4d0bab7f0eb8c428"),
    "python-3.13.17.zip": ("https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip",
        "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"),
    "unicorn-2.1.4.tar.gz": ("https://files.pythonhosted.org/packages/b2/1b/b4248aa8422e86de690cf8e85cf8feae4c33405a097d1ebe71570bdaa6f5/unicorn-2.1.4.tar.gz",
        "00567a70e323f749b419cd86bee4f9115beab7ebba32194581c090cbb7c59cff"),
    "zbic-1.0.1.post1.tar.gz": ("https://files.pythonhosted.org/packages/51/34/a108004438db175641f13b9d9de8b6b57c96911c582e8f39fc57b277f9d8/zbic-1.0.1.post1.tar.gz",
        "a798c24aea58f81a0b4bb0226f0f383b7e7f07a9a0150732cb45dc65ac541ff3"),
    "Humanizer-license.txt": ("https://raw.githubusercontent.com/Humanizr/Humanizer/90f0cb1d7427635f38b46350bb1dc5fca2164f2d/license.txt",
        "e24ec7f2eea5c5f4b3e2c08308109ba99b49b4b56ea7ff0e0a178f2bdd4bbce8"),
    "RecyclableMemoryStream-license.txt": ("https://raw.githubusercontent.com/microsoft/Microsoft.IO.RecyclableMemoryStream/e29a28387da9018fa9605a1dcb3f7a0435aa9974/LICENSE",
        "7d6aa3550cf7aeb25ef542f616d8ead585633e3b607c65e1db4aa804e4971a91"),
    "MsgPack-license.txt": ("https://raw.githubusercontent.com/msgpack/msgpack-cli/master/LICENSE.txt",
        "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"),
}


def git(*args):
    return subprocess.check_output(["git", "-C", ROOT, *args]).decode("utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(name):
    url, expected = DOWNLOADS[name]
    path = DEPS / name
    if not path.is_file():
        with tempfile.TemporaryDirectory(dir=DEPS) as folder:
            tmp = Path(folder) / name
            with urllib.request.urlopen(url, timeout=90) as incoming, tmp.open("wb") as out:
                shutil.copyfileobj(incoming, out)
            if digest(tmp) != expected:
                raise ValueError(f"Download failed its SHA-256 check: {name}")
            os.replace(tmp, path)
    if digest(path) != expected:
        raise ValueError(f"Cached download differs from its pinned SHA-256: {name}")
    return path


def member_name(name):
    parts = name.split("/")
    forbidden = {"work", "exefs", "romfs", "saves", "backups", "shader_cache", "__pycache__", ".git"}
    if (not name or "\\" in name or ":" in name or any(p in ("", ".", "..") for p in parts)
            or forbidden.intersection(p.lower() for p in parts)
            or parts[-1].lower().endswith((".msbt", ".bffnt", ".bntx", ".msbt.zst", ".bffnt.zst", ".bntx.zst"))
            or parts[-1].lower() in {"fefw.exe", "fagrishot.exe", "fefw.ini", "controls.ini", "local-trial.json"}
            or PurePosixPath(name).suffix.lower() in {".nso", ".nro", ".nca", ".nsp", ".xci", ".nup", ".nsz", ".xcz", ".spv", ".sav", ".keys", ".pdb"}):
        raise ValueError(f"Forbidden or ambiguous player package member: {name}")
    return name


class Payload:
    """Every member has an explicit source. An installed game or a staging directory is never walked."""
    def __init__(self, stack):
        self.stack = stack
        self.members = {}
        self.windows_names = set()

    def add(self, name, source):
        name = member_name(name)
        if name.lower() in self.windows_names:
            raise ValueError(f"Duplicate player archive path: {name}")
        self.windows_names.add(name.lower())
        self.members[name] = source

    def archive(self, path, prefix, strip="", skip=()):
        archive = self.stack.enter_context(zipfile.ZipFile(path))
        for entry in archive.infolist():
            if entry.is_dir() or not entry.filename.startswith(strip):
                continue
            rel = entry.filename[len(strip):]
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            self.add(prefix + "/" + rel, (archive, entry))

    def tracked(self, source, dest):
        paths = git("ls-files", "-z", "--", source).rstrip("\0").split("\0")
        if not paths[0]:
            raise ValueError(f"No tracked package source at {source}")
        for rel in paths:
            suffix = PurePosixPath(rel).relative_to(source)
            name = dest if not suffix.parts else str(PurePosixPath(dest) / suffix)
            self.add(name, ROOT / rel)

    def open(self, name):
        source = self.members[name]
        if isinstance(source, bytes):
            return io.BytesIO(source)
        if isinstance(source, tuple):
            archive, entry = source
            return archive.open(entry)
        return source.open("rb")


def add_python(payload):
    payload.archive(fetch("python-3.13.17.zip"), "tools/python", skip=("python313._pth",))
    payload.add("tools/python/python313._pth", b"python313.zip\n.\nLib/site-packages\n")
    wheels = DEPS / "wheels"
    wheels.mkdir(exist_ok=True)
    requirements = SCRIPTS / "requirements-win64-py313.txt"
    lock = re.findall(r"^(\S+)==(\S+) --hash=sha256:([a-f0-9]{64})$", requirements.read_text(), re.M)
    if not lock:
        raise ValueError("The CPython dependency lock is empty.")
    if any(not list(wheels.glob(f"{name}-{ver}-*.whl")) for name, ver, _ in lock):
        subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-deps",
                        "--only-binary=:all:", "--require-hashes", "-r", requirements, "-d", wheels], check=True)
    for name, version, expected in lock:
        matches = [p for p in wheels.glob(f"{name}-{version}-*.whl") if digest(p) == expected]
        if len(matches) != 1:
            raise ValueError(f"Missing pinned Windows x64 wheel for {name} {version}.")
        payload.archive(matches[0], "tools/python/Lib/site-packages")
    for name in ("unicorn-2.1.4.tar.gz", "zbic-1.0.1.post1.tar.gz"):
        payload.add("sources/" + name, fetch(name))


def add_shaders(payload, reuse):
    publish = DEPS / "shader-publish"
    if not reuse or not (publish / "spirv_harness.exe").is_file():
        subprocess.run([sys.executable, "-I", SCRIPTS / "build_shaders.py", "--rebuild"], cwd=ROOT, check=True)
        env = {**os.environ, "NUGET_PACKAGES": str(build_shaders.BUILD / "nuget"),
               "DOTNET_CLI_HOME": str(build_shaders.BUILD / "cli")}
        with tempfile.TemporaryDirectory(dir=DEPS) as tmp:
            fresh = Path(tmp) / "publish"
            subprocess.run([build_shaders.sdk(env), "publish", ROOT / "tools/shaders/harness/SpirvHarness.csproj",
                            "-c", "Release", "-r", "win-x64", "--self-contained", "true", "-o", fresh,
                            "-p:DebugType=none", "-p:DebugSymbols=false", "-p:Deterministic=true",
                            f"-p:PathMap={ROOT}=."], env=env, cwd=ROOT, check=True)
            # Only the output of the successful current publish goes in the release.
            replace_own_output(fresh, publish)
    config = json.loads((publish / "spirv_harness.runtimeconfig.json").read_text(encoding="utf-8-sig"))
    if not any(f["name"] == "Microsoft.NETCore.App" for f in config["runtimeOptions"].get("includedFrameworks", [])):
        raise ValueError("The shader translator must be a self-contained Windows publish.")
    deps = json.loads((publish / "spirv_harness.deps.json").read_text(encoding="utf-8-sig"))
    target = deps["targets"][deps["runtimeTarget"]["name"]]
    assets = {"spirv_harness.exe", "spirv_harness.runtimeconfig.json", "spirv_harness.deps.json"}
    for library in target.values():
        for family in ("runtime", "native", "resources", "runtimeTargets"):
            for rel, descriptor in library.get(family, {}).items():
                file = PurePosixPath(rel)
                name = file.name
                if family == "resources":
                    name = descriptor.get("locale", file.parent.name) + "/" + name
                assets.add(name)
    for name in sorted(assets):
        payload.add("tools/spirv_harness/" + name, publish / name)
    validator = DEPS / "spirv-static/tools/spirv-val.exe"
    if not reuse or not validator.is_file():
        subprocess.run(["pwsh", "-NoProfile", "-File", SCRIPTS / "build_player_validator.ps1"], check=True)
    payload.add("tools/spirv-val/spirv-val.exe", validator)
    for sub, name in (("", "SPIRV-Tools"), ("external/spirv-headers", "SPIRV-Headers")):
        payload.add(f"licenses/shaders/{name}-LICENSE", DEPS / "spirv-tools" / sub / "LICENSE")
    payload.tracked("tools/shaders/ampere/extract_corpus.py", "tools/shaders/extract_corpus.py")
    payload.tracked("tools/shaders/licenses", "licenses/shaders/third-party")
    nuget = ROOT / "tools/shaders/build/nuget"
    names = json.loads((SCRIPTS / "build_shaders.nuget.json").read_text(encoding="utf-8"))
    payload.add("licenses/shaders/locked-nuget-packages.json", SCRIPTS / "build_shaders.nuget.json")
    runtime = config["runtimeOptions"]["includedFrameworks"][0]["version"]
    packages = [n.lower() for n in names if n.lower().startswith(("gommon/", "system.codedom/"))]
    packages.append("microsoft.netcore.app.runtime.win-x64/" + runtime)
    for item in packages:
        n, v = item.split("/")
        archive = payload.stack.enter_context(zipfile.ZipFile(nuget / item / f"{n}.{v}.nupkg"))
        for entry in archive.infolist():
            if PurePosixPath(entry.filename).name.lower().startswith(("license", "third-party-notices")):
                payload.add(f"licenses/shaders/{n}/" + entry.filename, (archive, entry))
    for name in ("Humanizer-license.txt", "RecyclableMemoryStream-license.txt", "MsgPack-license.txt"):
        payload.add("licenses/shaders/" + name, fetch(name))


def replace_own_output(fresh, target):
    backup = fresh.parent / "previous"
    if target.exists():
        target.rename(backup)
    try:
        fresh.rename(target)
    except BaseException:
        if backup.exists():
            backup.rename(target)
        raise


def native(payload, runtime):
    payload.add("fefw_rt.dll", runtime / "fefw_rt.dll")
    payload.add("tools/build/sdk/fefw_rt.lib", runtime / "fefw_rt.lib")
    payload.add("fonts/DejaVuSans.ttf", runtime / "fonts/DejaVuSans.ttf")
    payload.add("FagriShotLauncher.exe", WORK / "tools/FagriShotLauncher.exe")
    for f in sorted(runtime.glob("astcdec-*-shared.dll")) + sorted(runtime.glob("renhdrum.dll")):
        payload.add(f.name, f)
    for f in ("fefw_rt_api.h", "fefw_stub.cpp", "fefw_fma.c", "fefw_hle.cpp"):
        payload.tracked("game-build/fefw/src/" + f, "tools/build/sdk/" + f)
    for f in ("fefw.rc", "fefw.manifest", "fefw.ico"):
        payload.tracked("game-build/fefw/res/" + f, "tools/build/sdk/" + f)
    payload.tracked("game-build/fefw/third_party/zstd", "tools/build/sdk/zstd")
    for f in ("fefw_build.py", "dump_hashes.json"):
        payload.tracked("game-build/release/" + f, "tools/build/" + f)
    payload.tracked("tools/nso/nso_unpack.py", "tools/build/nso_unpack.py")
    for path in ("LICENSE", "nxrecomp", "include/nxrecomp"):
        payload.tracked("recomp/" + path, "tools/build/recomp/" + path)
    for f in ("CMakeLists.txt", "config.py", "main.sites", "hot.txt", "inline.txt"):
        payload.tracked("recomp/games/fefw/" + f, "tools/build/recomp/games/fefw/" + f)
    for rel in git("ls-files", "--", "locales/*.ini").splitlines():
        payload.tracked(rel, rel)
    payload.archive(fetch("zig-0.17.0.zip"), "tools/build/zig", strip=ZIG + "/", skip=("doc",))


def notices(payload, runtime, cargo):
    for rel in ("runtime/THIRD_PARTY_NOTICES.md", "recomp/THIRD_PARTY.md", "runtime/assets/fonts/LICENSE-DejaVu.txt",
                "runtime/assets/prompts/LICENSE-Kenney-InputPrompts.txt", "runtime/LICENSE", "launcher/LICENSE",
                "game-build/release/BUNDLED_TOOLS.txt"):
        payload.tracked(rel, "licenses/" + rel)
    for rel in git("ls-files", "--", "runtime/third_party", "tools/textures/licenses", "tools/tiling/licenses").splitlines():
        if package_tools.notice(Path(rel)):
            payload.tracked(rel, "licenses/" + rel)
    for name in ("sdl3", "imgui", "volk", "vma", "vulkan_headers", "zstd"):
        src = runtime / "_deps" / (name + "-src")
        files = [p for p in src.iterdir() if p.is_file() and package_tools.notice(p)]
        files.extend((src / "LICENSES").glob("*.txt"))
        if not files:
            raise ValueError(f"Runtime dependency {name} lacks its notices.")
        for file in files:
            payload.add("licenses/runtime-fetched/" + name + "/" + file.relative_to(src).as_posix(), file)
    rust = {}
    package_tools.launcher_licenses(rust, cargo)
    for rel, data in rust.items():
        payload.add(rel, data)


def add_arabic_campaign(payload, directory):
    # The external pack is local build input. Only declared translation JSON frames go into the ZIP.
    sys.path.insert(0, str(ROOT))
    from fagrishot.arabic_campaign import Campaign

    pack = Campaign(directory.resolve(strict=True))
    if not pack.files:
        raise ValueError("The selected Arabic campaign contains no translation tables.")
    prefix = "fagrishot/arabic_data/campaign/"
    for name in list(payload.members):
        if name.startswith(prefix):
            del payload.members[name]
            payload.windows_names.remove(name.lower())
    payload.add(prefix + "manifest.json", pack.manifest)
    for table, entry in pack.files.items():
        name = table + ".json.zst"
        path = pack.folder / name
        if digest(path) != entry["sha256"]:
            raise ValueError(f"Incorrect Arabic campaign table SHA-256: {table}")
        payload.add(prefix + name, path)


def guides(payload):
    names = {"README.md", "README.ar.md", "docs/inputs.md", "docs/storage.md", "docs/status.md",
             "docs/foundation.md", "docs/BUILDING.md", "docs/package.md", "docs/longshot.md",
             "docs/cpu-verification.md", "docs/runtime-verification.md", "docs/shader-verification.md"}
    for name in sorted(names):
        def link(match):
            href = match.group(1)
            if href.startswith(("http:", "https:", "#", "mailto:")):
                return match.group(0)
            target, separator, anchor = href.partition("#")
            rel = ((ROOT / name).parent / target).resolve().relative_to(ROOT).as_posix()
            if rel in names or rel in {"LICENSE", "LEGAL.md"}:
                return match.group(0)
            if not (ROOT / rel).exists():
                raise ValueError(f"Documentation points to missing source: {name} -> {rel}")
            return f"]({REPOSITORY}/blob/{PUBLIC_BASE}/{rel}{separator}{anchor})"
        body = (ROOT / name).read_text(encoding="utf-8")
        payload.add(name, re.sub(r"\]\(([^)]+)\)", link, body).encode("utf-8"))
    guide = """FagriShot for Windows x64

1. Extract the whole ZIP into a writable folder and double-click FagriShotLauncher.exe.
2. In Game setup, select your extracted ExeFS and merged RomFS from update 131072.
3. Click Build game code. Keep the window open while the launcher builds the game and prepares its shaders.
4. Click Play. Confirm the opening Autosave prompt with Enter or Space.

Graphics settings is beside Play. The Arabic text option shows how many entries are reviewed or machine translated
in your installed offline catalog. Its private game-text and font overlays are built from your verified RomFS on the next launch.

See README.md for controls, backup and troubleshooting. Windows 11 x64, WebView2 and a Vulkan 1.3 driver are required.

دليل التشغيل السريع

1. استخرج محتويات الملف المضغوط كاملة في مجلد تستطيع الكتابة فيه، ثم افتح FagriShotLauncher.exe.
2. في Game setup، اختر مجلد ExeFS ومجلد RomFS المدمج من نسختك من التحديث 131072.
3. اضغط Build game code واترك النافذة مفتوحة حتى يكتمل بناء اللعبة وتجهيز برامج التظليل.
4. اضغط Play. عندما تظهر رسالة Autosave، اضغط Enter أو Space للمتابعة.

ستجد Graphics settings بجانب Play. يعرض خيار النص العربي عدد النصوص المراجعة والنصوص المترجمة آليا في الحزمة المثبتة.
ينشئ المشغّل ملفات النصوص والخطوط محليًا عند التشغيل التالي باستخدام ملفات RomFS التي تحقّق منها.

تجد شرح التحكم والنسخ الاحتياطي وحل المشكلات في README.ar.md.
"""
    payload.add("START-HERE.txt", guide.encode("utf-8-sig"))


def zip_info(name):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def write_archive(payload, path, revision, draft):
    files = {}
    source_prefixes = [b"NSO0", b"NRO0", b"NCA3", b"PFS0", b"HFS0", b"BNSH", b"SARC"]
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for name in sorted(payload.members):
            h = hashlib.sha256()
            with payload.open(name) as src, out.open(zip_info(name), "w") as dest:
                first = src.read(1024 * 1024)
                if first[:4] in source_prefixes:
                    raise ValueError(f"Unexpected game-data header in {name}")
                block = first
                while block:
                    h.update(block)
                    dest.write(block)
                    block = src.read(1024 * 1024)
            files[name] = h.hexdigest()
        out.writestr(zip_info("player-package.json"), package_tools.encoded(
            {"schema": 1, "product": "FagriShot", "version": package_tools.VERSION, "revision": revision,
             "draft": draft, "files": files}))
    if path.stat().st_size >= 2 * 1024 ** 3:
        raise ValueError("The player ZIP exceeds the GitHub release asset limit.")
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", type=Path, default=WORK / "runtime-release-player")
    ap.add_argument("--launcher-cache", type=Path, default=WORK / "launcher")
    ap.add_argument("--arabic-campaign", type=Path, metavar="FOLDER",
                    help="bundle a local offline Arabic campaign (manifest.json and its declared translation shards)")
    ap.add_argument("--reuse-compiled-shader-tools", action="store_true", help="use the verified release builder outputs")
    ap.add_argument("--draft", action="store_true", help="preview tracked edits in a clearly marked, non-release ZIP")
    ap.add_argument("--verify-only", type=Path, metavar="ZIP", help="independently verify an existing archive")
    args = ap.parse_args()
    from verify_player import verify
    if args.verify_only:
        print(json.dumps(verify(args.verify_only.resolve()), indent=2))
        return
    revision = git("rev-parse", "HEAD").strip()
    if not args.draft and git("status", "--porcelain", "--untracked-files=normal").strip():
        raise ValueError("Commit the release sources before packaging, or build a --draft preview.")
    DEPS.mkdir(parents=True, exist_ok=True)
    out = WORK / "packages"
    out.mkdir(exist_ok=True)
    name = f"FagriShot-{package_tools.VERSION}-preview-Windows-x64" + ("-draft" if args.draft else "") + ".zip"
    with tempfile.TemporaryDirectory(dir=out) as temp, ExitStack() as stack:
        payload = Payload(stack)
        for f in ("scripts/fagrishot.py", "scripts/requirements-win64-py313.txt", "compatibility/supported.json",
                  "LICENSE", "LEGAL.md"):
            payload.tracked(f, f)
        payload.tracked("fagrishot", "fagrishot")
        if args.arabic_campaign:
            add_arabic_campaign(payload, args.arabic_campaign)
        for p in ("__init__", "__main__", "arabic", "arabic_campaign", "arabic_font", "build", "graphics", "inputs",
                  "localization", "localization_flow", "localization_art", "localization_logo",
                  "player_build", "saves", "shaders", "storage"):
            if "fagrishot/" + p + ".py" not in payload.members:
                raise ValueError(f"Missing package application module {p}. Stage the source in Git.")
        if not any(name.startswith("fagrishot/arabic_data/") and name.endswith(".json") for name in payload.members):
            raise ValueError("The Arabic preview translation catalog is missing.")
        guides(payload)
        payload.add("SOURCE.txt", (f"FagriShot {package_tools.VERSION} local preview\nSource revision: {revision}\n"
            "The accompanying local Git checkout contains the source and build scripts for this preview.\n"
            f"Public dependency archive sources and pins: {REPOSITORY}/blob/{PUBLIC_BASE}/scripts/package_player.py\n"
            "Unicorn and zbic corresponding source tarballs are in sources/. Dependency notices are in licenses/.\n").encode())
        add_python(payload)
        native(payload, args.runtime.resolve())
        add_shaders(payload, args.reuse_compiled_shader_tools)
        notices(payload, args.runtime.resolve(), args.launcher_cache.resolve())
        archive = Path(temp) / name
        write_archive(payload, archive, revision, args.draft)
        report = verify(archive)
        report["filename"] = name
        os.replace(archive, out / name)
        (out / (name + ".verification.json")).write_bytes(package_tools.encoded(report))
    print(out / name, flush=True)


if __name__ == "__main__":
    main()
