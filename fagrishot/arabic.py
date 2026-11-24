"""Stage the opt-in Arabic text preview from the player's verified US-English resources."""
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
import unicodedata

from .build import sha
from .localization import write_overrides
from .storage import build_lock, check_separation, write_json


def assets(root):
    package = root / "fagrishot"
    catalogs = sorted((package / "arabic_data").glob("*.json"))
    faces = [root / path for path in ("fonts/DejaVuSans.ttf", "runtime/assets/fonts/DejaVuSans.ttf",
                                      "runtime-bin/fonts/DejaVuSans.ttf")]
    face = next((p for p in faces if p.is_file()), faces[0])
    return catalogs, face


def catalog_data(paths):
    result = {}
    for path in paths:
        document = json.loads(path.read_bytes())
        for name, entries in document.items():
            grouped = result.setdefault(name, {})
            if grouped.keys() & entries.keys():
                raise ValueError(f"The Arabic preview contains duplicate labels in {name}.")
            grouped.update(entries)
    return result


def _preference(settings):
    try:
        data = json.loads((settings / "arabic.json").read_bytes())
    except FileNotFoundError:
        return False, ""
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("schema") != 1 or type(data.get("enabled")) is not bool:
        return False, "Could not read your Arabic preview preference. The preview is off."
    return data["enabled"], ""


def enabled(settings):
    return _preference(settings)[0]


def status(settings, root):
    catalogs, face = assets(root)
    count = sum(len(entries) for entries in catalog_data(catalogs).values())
    builders = ("arabic_font.py", "localization.py", "localization_art.py")
    available = bool(count and face.is_file() and all((root / "fagrishot" / p).is_file() for p in builders))
    active, warning = _preference(settings)
    return {"enabled": active, "available": available,
            "coverage": f"Arabic preview covers {count} selected text entries and the opening Autosave graphic. Other text uses US English. Audio stays in the game's voice language.",
            "message": warning if available else "This installation is missing the Arabic preview catalog, font or font builder."}


def set_enabled(settings, root, value):
    with build_lock(settings / "save.lock"):
        if value and not status(settings, root)["available"]:
            raise ValueError("The Arabic preview files are unavailable in this installation.")
        write_json(settings / "arabic.json", {"schema": 1, "enabled": value})
        return status(settings, root)


def translate(catalog, visual_text):
    changes, hashes, glyphs = {}, {}, set()
    def shape(segment):
        visual = visual_text(segment)
        glyphs.update(c for c in visual if ord(c) > 127 and unicodedata.name(c, "").startswith("ARABIC"))
        return visual
    for name, entries in catalog.items():
        changes[name], hashes[name] = {}, {}
        for label, entry in entries.items():
            hashes[name][label] = entry["source_sha256"]
            change = {"parts": [shape(s) for s in entry["parts"]]}
            if "choices" in entry:
                change["choices"] = {k: [shape(s) for s in choices] for k, choices in entry["choices"].items()}
            changes[name][label] = change
    return changes, hashes, glyphs


def file_hashes(folder):
    files = {}
    for path in folder.rglob("*"):
        attributes = path.lstat()
        if getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or path.is_symlink():
            raise ValueError("The private Arabic overlay contains a redirected path.")
        if path.is_file():
            files[path.relative_to(folder).as_posix()] = sha(path)
    return files


def prepare(romfs, private, input_key, root):
    from .arabic_font import FONT_NAMES, patch_fonts, visual_text
    from .localization_art import patch_autosave_art
    catalogs, face = assets(root)
    if not catalogs or not face.is_file():
        raise ValueError("Re-extract the complete player to restore the Arabic preview files.")
    catalog = catalog_data(catalogs)
    if not catalog:
        raise ValueError("The Arabic preview catalog is empty.")
    # input_key is the validated shader cache's complete ExeFS/RomFS identity at Play.
    identity = [input_key, sha(face), *(sha(p) for p in catalogs),
                *(sha(root / "fagrishot" / p) for p in ("arabic.py", "arabic_font.py", "localization.py", "localization_art.py"))]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    expected = {"Arabic/romfs/Msg/USen/" + name for name in catalog}
    expected.update("Arabic/romfs/Font/" + name for name in FONT_NAMES)
    expected.add("Arabic/romfs/Layout/Textures/Tutorial/USen/TUT_Hub_AutoSave_001.bntx.zst")
    cache = private / "arabic"
    check_separation([romfs], [cache])
    cache.mkdir(parents=True, exist_ok=True)
    receipt = cache / (key + ".json")
    try:
        record = json.loads(receipt.read_bytes())
        generation = record["generation"]
        if re.fullmatch(r"overlay-[a-z0-9_]+", generation):
            previous = cache / generation / "mods"
            if (previous.is_dir() and previous.resolve() == previous.absolute()
                    and set(record["files"]) == expected and record["files"] == file_hashes(previous)):
                return previous
    except (OSError, ValueError, TypeError, KeyError):
        pass
    print("Preparing the private Arabic text and font overlay.", flush=True)
    changes, hashes, glyphs = translate(catalog, visual_text)
    with tempfile.TemporaryDirectory(prefix=".building-", dir=cache) as staging:
        folder = Path(staging)
        mods = folder / "mods"
        overlay = mods / "Arabic" / "romfs"
        if write_overrides(romfs, overlay, changes, source_hashes=hashes) != len(changes):
            raise ValueError("One or more Arabic resources did not produce an override.")
        patch_fonts(romfs, overlay, glyphs, face)
        patch_autosave_art(romfs, overlay, face)
        outputs = file_hashes(mods)
        if set(outputs) != expected:
            raise ValueError("The Arabic overlay is missing translated messages or font files.")
        final = cache / ("overlay-" + folder.name.removeprefix(".building-"))
        final.mkdir()
        mods.rename(final / "mods")
        write_json(receipt, {"generation": final.name, "files": outputs})
        return final / "mods"
