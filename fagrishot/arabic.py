"""Stage the opt-in Arabic text preview from the player's verified US-English resources."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import stat
import tempfile
import unicodedata

from .build import sha
from .arabic_campaign import Campaign, ELIGIBLE_LABELS, FORMAT_ONLY, SOURCE_LABELS
from .localization import write_override_tables
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
    from .localization_art import TUTORIAL_RECIPE, tutorial_art_outputs
    from .localization_logo import logo_outputs

    catalogs, face = assets(root)
    reviewed = catalog_data(catalogs)
    format_only = sum(len(entries.keys() & FORMAT_ONLY.get(name, set())) for name, entries in reviewed.items())
    count = sum(map(len, reviewed.values())) - format_only
    campaign = Campaign(root / "fagrishot/arabic_data/campaign")
    drafted = sum(e["entries"] for e in campaign.files.values())
    for name in reviewed.keys() & campaign.files.keys():
        merged = campaign.merged(name, reviewed[name])
        drafted -= len(reviewed[name]) + campaign.files[name]["entries"] - len(merged)
    total = count + drafted
    if total > ELIGIBLE_LABELS:
        raise ValueError("Arabic text coverage exceeds the verified game inventory.")
    builders = ("arabic_font.py", "localization.py", "localization_flow.py", "localization_art.py", "localization_logo.py")
    available = bool(total and face.is_file() and all((root / "fagrishot" / p).is_file() for p in builders))
    illustrations = len(tutorial_art_outputs(root)) if (root / TUTORIAL_RECIPE).is_file() else 1
    active, warning = _preference(settings)
    return {"enabled": active, "available": available, "source_labels": SOURCE_LABELS, "eligible": ELIGIBLE_LABELS,
            "covered": total, "reviewed": count, "machine_draft": drafted, "remaining": ELIGIBLE_LABELS - total,
            "reviewed_dynamic_formats": format_only,
            "tutorial_images": illustrations, "title_logo": bool(logo_outputs(root)),
            "coverage": "Play with Arabic text throughout the game (Experimental feature)",
            "message": warning if available else "This installation is missing the Arabic preview catalog, font or font builder."}


def set_enabled(settings, root, value):
    with build_lock(settings / "save.lock"):
        if value and not status(settings, root)["available"]:
            raise ValueError("The Arabic preview files are unavailable in this installation.")
        write_json(settings / "arabic.json", {"schema": 1, "enabled": value})
        return status(settings, root)


def translate(catalog, visual_text):
    changes, hashes, glyphs = {}, {}, set()
    contextual = set()
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
            if "argument_order" in entry:
                change["argument_order"] = entry["argument_order"]
            if entry.get("flow") == "auto":
                # Collect glyphs above. The source-aware formatter shapes the whole template after placing its controls.
                change["flow"] = "auto"
                change["parts"] = entry["parts"]
                if "choices" in entry:
                    change["choices"] = entry["choices"]
                for segment in entry["parts"]:
                    contextual.update(c for c in segment if "\u0621" <= c <= "\u06ff" and unicodedata.category(c) == "Lo")
            changes[name][label] = change
    if contextual:
        # Inline color changes can split letters that still join visually when the guest renders the line.
        for c in contextual:
            shape(f"{c} ب{c} {c}ب ب{c}ب")
        if "ل" in contextual:
            for alef in contextual.intersection("آأإا"):
                shape(f"ل{alef} بل{alef}")
    return changes, hashes, glyphs


def wrap_draft(text, source, advances, visual):
    """Wrap generated prose to its original multiline width before applying bidirectional layout."""
    if "\n" not in source or not text.strip():
        return text
    default = advances.get("?", 1)
    def width(string):
        return sum(advances.get(c, default) for c in string)
    def paragraph(original, translated):
        budget = max((width(line) for line in original.splitlines()), default=0)
        words = list(re.finditer(r"\S+", translated))
        if not budget or len(words) < 2:
            return translated
        # Keep whitespace at control boundaries and never split a name or join Arabic across a line break.
        result = [translated[:words[0].start()], words[0].group()]
        used = width(visual(words[0].group()))
        for last, word in zip(words, words[1:]):
            between = translated[last.end():word.start()]
            amount = width(visual(word.group()))
            if "\n" in between or used + width(between) + amount > budget:
                result.extend(("\n", word.group()))
                used = amount
            else:
                result.extend((between, word.group()))
                used += width(between) + amount
        result.append(translated[words[-1].end():])
        return "".join(result)

    # The generator preserves paragraph separators. Each paragraph has its own source budget.
    split = r"(\n[ \t]*\n(?:[ \t]*\n)*)"
    originals, translations = re.split(split, source), re.split(split, text)
    if len(originals) != len(translations):
        raise ValueError("An Arabic campaign entry changed its original paragraph breaks.")
    return "".join(t if i % 2 else paragraph(originals[i], t) for i, t in enumerate(translations))


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
    from .arabic_font import FONT_NAMES, font_advances, patch_fonts, visual_text
    from .localization_art import TUTORIAL_RECIPE, patch_tutorials, tutorial_art_outputs
    from .localization_logo import LOGO_RECIPE, logo_outputs, patch_logos
    catalogs, face = assets(root)
    campaign = Campaign(root / "fagrishot/arabic_data/campaign")
    if not (catalogs or campaign.files) or not face.is_file():
        raise ValueError("Re-extract the complete player to restore the Arabic preview files.")
    catalog = catalog_data(catalogs)
    names = sorted(catalog.keys() | campaign.files.keys())
    if not names:
        raise ValueError("The Arabic preview catalog is empty.")
    # input_key is the validated shader cache's complete ExeFS/RomFS identity at Play.
    artwork = [root / path for path in (TUTORIAL_RECIPE, LOGO_RECIPE)]
    identity = [input_key, sha(face), campaign.digest, *(sha(p) if p.is_file() else "" for p in artwork),
                *(sha(p) for p in catalogs),
                *(sha(root / "fagrishot" / p) for p in ("arabic.py", "arabic_campaign.py", "arabic_font.py",
                  "localization.py", "localization_flow.py", "localization_art.py", "localization_logo.py"))]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    expected = {"Arabic/romfs/Msg/USen/" + name for name in names}
    expected.update("Arabic/romfs/Font/" + name for name in FONT_NAMES)
    expected.update("Arabic/romfs/" + name for name in tutorial_art_outputs(root))
    expected.update("Arabic/romfs/" + name for name in logo_outputs(root))
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
    cached_visual = lru_cache(maxsize=8192)(visual_text)
    glyphs = set()
    def rows():
        for name in names:
            yield name, campaign.merged(name, catalog.get(name, {}))
    with tempfile.TemporaryDirectory(prefix=".building-", dir=cache) as staging:
        folder = Path(staging)
        mods = folder / "mods"
        overlay = mods / "Arabic" / "romfs"
        overlay.mkdir(parents=True)
        for name, records in rows():
            _, _, table_glyphs = translate({name: records}, cached_visual)
            glyphs.update(table_glyphs)
        patch_fonts(romfs, overlay, glyphs, face)
        advances = font_advances(overlay / "Font/__System.bffnt.zst") if campaign.files else {}

        def tables():
            for i, (name, records) in enumerate(rows(), 1):
                if len(names) > 50 and (i == 1 or i % 100 == 0 or i == len(names)):
                    print(f"Preparing Arabic message table {i:,} of {len(names):,}.", flush=True)
                yield name, records, {label: entry["source_sha256"] for label, entry in records.items()}

        def shape_table(name, table, records):
            for label, record in records.items():
                if name not in campaign.files or label in catalog.get(name, {}):
                    continue
                source = table.entry(label)
                original = [p for p in source.parts if isinstance(p, str)]
                if len(original) != len(record["parts"]):
                    raise ValueError(f"The Arabic translation changed the number of text parts: {name} / {label}.")
                record["parts"] = [wrap_draft(t, s, advances, cached_visual) for t, s in zip(record["parts"], original)]
                if "choices" in record:
                    for index, options in record["choices"].items():
                        sources = source.choices.get(int(index), ())
                        if len(sources) != len(options):
                            raise ValueError(f"The Arabic translation changed the number of choices: {name} / {label}.")
                        record["choices"][index] = [wrap_draft(t, s, advances, cached_visual) for t, s in zip(options, sources)]
            changes, _, _ = translate({name: records}, cached_visual)
            return changes[name]

        if write_override_tables(romfs, overlay, tables(), prepare_entries=shape_table, include_unchanged=True) != len(names):
            raise ValueError("One or more Arabic resources did not produce an override.")
        patch_tutorials(romfs, overlay, root)
        patch_logos(romfs, overlay, root)
        outputs = file_hashes(mods)
        if set(outputs) != expected:
            raise ValueError("The Arabic overlay is missing translated messages or font files.")
        final = cache / ("overlay-" + folder.name.removeprefix(".building-"))
        final.mkdir()
        mods.rename(final / "mods")
        write_json(receipt, {"generation": final.name, "files": outputs})
        return final / "mods"
