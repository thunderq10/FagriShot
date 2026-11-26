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
from .arabic_font import ARABIC_SCRIPT
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
            written = entry["parts"] + [s for choices in entry.get("choices", {}).values() for s in choices]
            # A notice, grade, sign or number kept in Latin is not an Arabic sentence. Setting it
            # as a right-to-left paragraph would move its symbols to the other end of the line.
            keep = "argument_order" not in entry and not any(map(ARABIC_SCRIPT.search, written))
            place = (lambda segment: segment) if keep else shape
            change = {"parts": [place(s) for s in entry["parts"]]}
            if "choices" in entry:
                change["choices"] = {k: [place(s) for s in choices] for k, choices in entry["choices"].items()}
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


_OBJECT = "\ufffc"
_BREAK = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)*")
# A function word left at the end of a line is separated from the word it governs.
_LEADING_WORDS = frozenset(
    "و ف ثم أو أم بل لكن في من عن إلى على مع عند بين حتى منذ قبل بعد دون غير أن إن لأن كأن لا ما لم لن قد هل يا كل أي "
    "هذا هذه ذلك تلك الذي التي إذا لو كي".split())


def _flatten(source_parts, texts, advances, visual):
    """Join a message into one logical string and record the width of each inline object.

    A palette change draws nothing and does not interrupt joining. A controller
    icon, a substituted value and a singular/plural choice each occupy the line.
    Returns the string, the (part, offset) owner of each character, the object
    widths in logical order and the measuring function.
    """
    from .localization import _choice_texts
    from .localization_flow import _kind

    default = advances.get("?", 1)

    def width(string):
        return sum(advances.get(c, default) for c in string)

    nominal = 4 * advances.get("0", default)
    flat, owners, objects = [], [], []
    replacements = iter(texts)
    index = -1
    for part in source_parts:
        if isinstance(part, str):
            index += 1
            text = next(replacements)
            if _OBJECT in text:
                raise ValueError("Unexpected inline object character in translated text.")
            flat.extend(text)
            owners.extend((index, offset) for offset in range(len(text)))
            continue
        control = part.startswith((b"\x0e", b"\x0f"))
        kind = _kind(part)
        if kind in ("directive", "color", "reset") or (kind == "unknown" and control):
            continue
        options = _choice_texts(part) if control else None
        if options is not None:
            amount = max(width(visual(option)) for option in options)
        elif control:
            amount = nominal
        else:
            amount = width(part.decode("utf-8", "replace"))
        flat.append(_OBJECT)
        owners.append(None)
        objects.append(amount)
    return "".join(flat), owners, objects, width


def _measure(text, objects, width, visual):
    """Displayed width of a fragment. Its inline objects are consumed in logical order."""
    total = 0
    for i, run in enumerate(text.split(_OBJECT)):
        if i:
            total += objects.pop(0)
        if run:
            total += width(visual(run))
    return total


def line_widths(source_parts, texts, advances, visual):
    """Width of every displayed line, measured across styled runs, icons and substituted values."""
    flat, _, objects, width = _flatten(source_parts, texts, advances, visual)
    return [_measure(line, objects, width, visual) for line in flat.split("\n")]


def wrap_message(source_parts, texts, advances, visual, floor=0):
    """Set a translated message again within the line width of its own source.

    The whole displayed line shares one budget: each text part between two
    controls, every controller icon and every substituted value. Paragraph
    breaks are kept. A line break inside a paragraph belongs to the English
    wording, so the Arabic paragraph is wrapped from its first word. floor is a
    wider line which the same widget is known to display.
    """
    texts = list(texts)
    original = [p for p in source_parts if isinstance(p, str)]
    if len(original) != len(texts):
        raise ValueError("An Arabic campaign entry changed its number of text parts.")
    plain = lambda value: value
    source, _, source_objects, width = _flatten(source_parts, original, advances, plain)
    if "\n" not in source or not "".join(texts).strip():
        return texts
    flat, owners, objects, _ = _flatten(source_parts, texts, advances, visual)
    pieces, translated = _BREAK.split(source), _BREAK.split(flat)
    if len(pieces) != len(translated):
        raise ValueError("An Arabic campaign entry changed its original paragraph breaks.")
    budgets = [max(floor, *(_measure(line, source_objects, width, plain) for line in piece.split("\n")))
               for piece in pieces]
    edits = {}
    start = 0
    for piece, budget in zip(translated, budgets):
        words = list(re.finditer(r"\S+", piece))
        sizes = [_measure(word.group(), objects, width, visual) for word in words]
        gaps = [piece[a.end():b.start()] for a, b in zip(words, words[1:])]
        spaces = [width(gap.replace("\n", "")) or width(" ") for gap in gaps]
        # An icon or a substituted value stays on the line of the word it follows, as in «اضغط» and its button.
        firsts = [0] + [i for i in range(1, len(words)) if not (words[i].group().startswith(_OBJECT)
                                                               and words[i - 1].group()[-1] not in ".!?:؟،؛")]
        groups = list(zip(firsts, firsts[1:] + [len(words)]))
        sizes = [sum(sizes[a:b]) + sum(spaces[a:b - 1]) for a, b in groups]
        spaces = [spaces[a - 1] for a, _ in groups[1:]]
        single = [words[a].group() if b - a == 1 else "" for a, b in groups]

        def breaks_within(limit):
            found, used = [], 0
            for i, size in enumerate(sizes):
                if i and used + spaces[i - 1] + size > limit:
                    found.append(i)
                    used = size
                else:
                    used += (spaces[i - 1] if i else 0) + size
            return found

        breaks = breaks_within(budget)
        if breaks:
            # Fill the same number of lines evenly instead of leaving one word under a full line.
            low, high = max(sizes), budget
            while low < high:
                middle = (low + high) // 2
                if len(breaks_within(middle)) <= len(breaks):
                    high = middle
                else:
                    low = middle + 1
            breaks = breaks_within(high)
            for n, at in enumerate(breaks):
                previous = breaks[n - 1] if n else 0
                following = breaks[n + 1] if n + 1 < len(breaks) else len(sizes)
                moved = sum(sizes[at - 1:following]) + sum(spaces[at - 1:following - 1])
                # Carry a stranded particle down when the next line can hold it.
                if at - 1 > previous and single[at - 1] in _LEADING_WORDS and moved <= budget:
                    breaks[n] = at - 1
        chosen = {firsts[group] for group in breaks}
        for i, (a, b) in enumerate(zip(words, words[1:]), 1):
            gap = range(start + a.end(), start + b.start())
            if i in chosen or "\n" in piece[a.end():b.start()]:
                edits[gap[0]] = "\n" if i in chosen else " "
                edits.update((position, "") for position in gap[1:])
        start += len(piece)
        separator = _BREAK.match(flat, start)
        if separator:
            start = separator.end()
    result = [[] for _ in texts]
    for position, (char, owner) in enumerate(zip(flat, owners)):
        if owner is not None:
            result[owner[0]].append(edits.get(position, char))
    return ["".join(chars) for chars in result]


def widget_width(table, advances):
    """A line width that the multi-line messages of one table are known to display.

    Tables for one kind of window have a narrow spread of line widths. Mixed
    tables return zero so that each message keeps its own budget.
    """
    plain = lambda value: value
    widths = []
    for label in table.labels:
        entry = table.entry(label)
        original = [p for p in entry.parts if isinstance(p, str)]
        if any("\n" in part for part in original):
            widths.extend(line_widths(entry.parts, original, advances, plain))
    if len(widths) < 40:
        return 0
    widths.sort()
    median, common = widths[len(widths) // 2], widths[len(widths) * 19 // 20]
    return common if 3 * common <= 4 * median else 0


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
        advances = {}
        if campaign.files:
            advances = font_advances(overlay / "Font/__System.bffnt.zst")
            # Controller icons live in the companion faces of the same complex font.
            for name in ("__nintendo_ext_003.bffnt.zst", "__System_Ext.bffnt.zst"):
                for char, advance in font_advances(Path(romfs) / "Font" / name).items():
                    advances.setdefault(char, advance)

        def tables():
            for i, (name, records) in enumerate(rows(), 1):
                if len(names) > 50 and (i == 1 or i % 100 == 0 or i == len(names)):
                    print(f"Preparing Arabic message table {i:,} of {len(names):,}.", flush=True)
                yield name, records, {label: entry["source_sha256"] for label, entry in records.items()}

        def shape_table(name, table, records):
            widest = []
            for label, record in records.items():
                if name not in campaign.files or label in catalog.get(name, {}):
                    continue
                source = table.entry(label)
                original = [p for p in source.parts if isinstance(p, str)]
                if len(original) != len(record["parts"]):
                    raise ValueError(f"The Arabic translation changed the number of text parts: {name} / {label}.")
                wrapped = wrap_message(source.parts, record["parts"], advances, cached_visual)
                if "".join(wrapped).count("\n") > "".join(original).count("\n"):
                    # The Arabic needs another line at the width of this one message. Use the
                    # line its widget is known to display before adding to the height.
                    if not widest:
                        widest.append(widget_width(table, advances))
                    wrapped = wrap_message(source.parts, record["parts"], advances, cached_visual, widest[0])
                record["parts"] = wrapped
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
