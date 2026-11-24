"""Audit local text and artwork coverage. Reports contain IDs, hashes and counts only."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import unicodedata

from .localization import LocalizationError, _ordinary, _separate, _us_english, read_resource


SCHEMA = 1
SOURCE_131072 = "8504750f4bf61634bca56e83e008fab028431c90d03883ba4c191c4566e83974"
ARABIC = re.compile(r"[\u0620-\u063f\u0641-\u064a\u066e-\u06d3\u0750-\u077f]")
LATIN = re.compile(r"[A-Za-z]")
LOCALES = frozenset("CNzh EUde EUen EUes EUfr EUit JPja KRko TWzh USen USes USfr".split())
_ARGUMENT = b"\x0e\x01\x00"
_COLOR = b"\x0e\x00\x00\x03\x00"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _fingerprint(obj):
    return _hash(json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def _hash_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _inline_token(token):
    if token.startswith(_COLOR) or token.startswith(b"\x0f"):
        return "style"
    if token.startswith(_ARGUMENT):
        return "argument"
    if token.startswith(b"\x0e"):
        return "control"
    if token.startswith(b"<"):
        return "directive"
    return "icon"


def bidi_review(parts):
    """Flag mixed runs on one physical line for a segment-by-segment bitmap renderer.

    A color change does not itself display a glyph. A substitute, icon or
    choice does. Each explicit line break starts an independent visual line.
    """
    runs = []
    flagged = set()

    def check_line():
        visible = [kind for kind in runs if kind not in ("style", "directive")]
        if len(visible) > 1 and (len(set(visible)) > 1 or len(runs) > len(visible)):
            flagged.update(kind for kind in runs if kind != "text")
        runs.clear()

    for part in parts:
        if isinstance(part, bytes):
            runs.append(_inline_token(part))
        else:
            for index, line in enumerate(part.split("\n")):
                if index:
                    check_line()
                if line.strip():
                    runs.append("text")
    check_line()
    return sorted(flagged)


def _visible(entry):
    plain = [p for p in entry.parts if isinstance(p, str)]
    choices = entry.choices
    options = [s for pair in choices.values() for s in pair]
    return plain, options, choices


def assess(entry, record, *, table="", label=""):
    """Return structural facts and outstanding issues for one source label."""
    counts = Counter(labels=1)
    issues = set()
    plain, options, choices = _visible(entry)
    source = "".join(plain + options)
    eligible = bool(source.strip())
    counts["eligible"] = int(eligible)
    if not eligible:
        category = "empty" if not entry.parts else "whitespace_format" if plain else "control_only"
        counts[category] += 1
    counts["source_lexical"] = int(any(c.isalpha() for c in source))
    counts["source_ascii_letter"] = int(bool(LATIN.search(source)))
    counts["choice_labels"] = int(bool(choices))
    counts["choice_controls"] = len(choices)
    counts["choice_forms"] = len(options)
    counts["choice_only_content"] = int(bool(options) and not "".join(plain).strip())
    counts["source_mixed_inline"] = int(bool(bidi_review(entry.parts)))
    if record is None:
        if eligible:
            issues.add("missing_record")
        return counts, issues
    counts["catalog_records"] += 1
    if not isinstance(record, dict) or record.get("source_sha256") != entry.source_sha256:
        issues.add("source_fingerprint_mismatch")
        return counts, issues
    try:
        supplied = record.get("choices", {})
        if not isinstance(supplied, dict):
            raise LocalizationError("Bad choices.")
        texts = record["parts"]
        rebuilt = entry.replaced(texts, supplied, record.get("argument_order"))
        # Parse the rebuilt bytes again, including the regenerated choice lengths.
        output = type(entry).parse(rebuilt)
        dst_plain, dst_options, dst_choices = _visible(output)
        if "flow" in record:
            if record["flow"] != "auto" or "argument_order" in record:
                raise LocalizationError("Unsupported inline flow.")
            from .localization_flow import render
            render(entry, texts, supplied)
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        issues.add("invalid_structure")
        return counts, issues
    counts["validated_records"] += 1
    if eligible:
        counts["eligible_with_valid_record"] += 1
    if rebuilt != entry.raw:
        counts["byte_changed"] += 1
    target = "".join(dst_plain + dst_options)
    if eligible and not target.strip():
        issues.add("empty_target")
    if LATIN.search(target):
        counts["target_has_latin"] += 1
    if any(re.search("[A-Za-z]{2,}", before) and before.strip() == after.strip()
           for before, after in zip(plain, texts)):
        issues.add("unchanged_lexical_part_review")
    # Support ranks are displayed as fixed Latin grade symbols throughout the
    # Arabic interface. Only an unchanged, one-byte source at its exact rank
    # label is eligible. Ordinary Latin text is still reported for review.
    preserved_rank = (table == "System.msbt.zst" and target in ("A", "B", "C")
                      and label == "MID_Sys_Reliance_Rank_" + target
                      and entry.raw == target.encode("ascii") + b"\0" and rebuilt == entry.raw)
    if preserved_rank:
        counts["preserved_support_grade_symbols"] += 1
    elif counts["source_lexical"] and not ARABIC.search(target):
        issues.add("lexical_target_without_arabic")
    if any(unicodedata.category(c).startswith("M") for c in target):
        issues.add("combining_mark_bitmap_review")
    for index, original in choices.items():
        explicit = str(index) in supplied or index in supplied
        if not explicit:
            issues.add("missing_choice_translation")
        counts["choice_forms_explicit"] += len(original) if explicit else 0
        # A missing alternative can retain English inside an otherwise Arabic label.
        edited_pair = dst_choices.get(index)
        if edited_pair is None:
            # Empty text parts may disappear after rebuilding, moving token indices.
            original_positions = list(choices)
            edited_starts = list(dst_choices.values())
            if len(edited_starts) == len(original_positions):
                edited_pair = edited_starts[original_positions.index(index)]
        if edited_pair is None or any(any(c.isalpha() for c in a) and not ARABIC.search(b)
                                      for a, b in zip(original, edited_pair)):
            issues.add("choice_form_without_arabic")
    if record.get("flow") == "auto":
        counts["verified_inline_flow"] += 1
    elif ARABIC.search(target) and not record.get("argument_order") and bidi_review(output.parts):
        issues.add("inline_bidi_review")
    if eligible and not issues:
        counts["passed_automated_checks"] += 1
    return counts, issues


def audit_messages(romfs, catalog, campaign=None):
    """Calculate the visible denominator independently of the supplied translation pack."""
    source = _us_english(romfs)
    totals, all_issues = Counter(), Counter()
    tables, source_rows, issues_by_label = {}, {}, {}
    unrecognized = set(catalog)
    if campaign is not None:
        unrecognized.update(campaign.files)
    for path in sorted(source.glob("*.msbt.zst")):
        name = path.name
        unrecognized.discard(name)
        table = read_resource(path)
        source_rows[name] = _hash(table.raw)
        counts, issues = Counter(), Counter()
        reviewed = catalog.get(name, {})
        translations = reviewed if campaign is None else campaign.merged(name, reviewed)
        if not isinstance(translations, dict):
            raise LocalizationError("Expected a mapping from labels to translation records.")
        gaps = {}
        for label in table.labels:
            facts, flags = assess(table.entry(label), translations.get(label), table=name, label=label)
            counts.update(facts)
            if facts["eligible"] and facts["validated_records"]:
                counts["eligible_reviewed_catalog" if label in reviewed else "eligible_campaign_draft"] += 1
            issues.update(flags)
            if flags:
                gaps[label] = sorted(flags)
        for label in translations.keys() - table.labels.keys():
            issues["unknown_translation_label"] += 1
            gaps[label] = ["unknown_translation_label"]
        totals.update(counts)
        all_issues.update(issues)
        tables[name] = {"counts": dict(counts), "issues": dict(issues), "raw_sha256": source_rows[name]}
        if gaps:
            issues_by_label[name] = gaps
    if not tables:
        raise LocalizationError("The source does not contain any USen MSBT resources.")
    fingerprint = _fingerprint(source_rows)
    return {"schema": SCHEMA, "source_manifest_sha256": fingerprint, "matches_update_131072_text": fingerprint == SOURCE_131072,
            "table_count": len(tables),
            "counts": dict(totals), "issues": dict(all_issues), "tables": tables, "issues_by_label": issues_by_label,
            "unknown_tables": sorted(unrecognized)}


def _decompress(path, maximum):
    import zstandard
    buf = _ordinary(path).read_bytes()
    if zstandard.frame_content_size(buf) > maximum:
        raise LocalizationError("Asset exceeds its inspection size limit.")
    return zstandard.ZstdDecompressor().decompress(buf, max_output_size=maximum, allow_extra_data=False)


def _texture_digest(path):
    data = _decompress(path, 64 << 20)
    if data[:4] != b"BNTX" or len(data) < 56:
        raise LocalizationError("Expected a BNTX tutorial image.")
    start = struct.unpack_from("<Q", data, 0x30)[0]
    end = struct.unpack_from("<I", data, 0x18)[0]
    if data[start:start + 4] != b"BRTD" or data[end:end + 4] != b"_RLT" or not start + 16 < end:
        raise LocalizationError("Unexpected BNTX texture data bounds.")
    return _hash(memoryview(data)[start + 16:end])


def _sarc_sections(data):
    if data[:4] != b"SARC" or data[6:8] != b"\xff\xfe":
        raise LocalizationError("Expected a little-endian layout SARC.")
    base = struct.unpack_from("<H", data, 4)[0]
    payload = struct.unpack_from("<I", data, 12)[0]
    count = struct.unpack_from("<H", data, base + 6)[0]
    names = base + 12 + count * 16 + 8
    if data[base:base + 4] != b"SFAT" or data[names - 8:names - 4] != b"SFNT" or names > payload:
        raise LocalizationError("Invalid layout SARC headers.")
    for i in range(count):
        _, attr, start, end = struct.unpack_from("<IIII", data, base + 12 + i * 16)
        ofs = names + (attr & 0xffffff) * 4
        nul = data.find(b"\0", ofs, payload)
        if nul < 0 or not start <= end <= len(data) - payload:
            raise LocalizationError("Invalid layout SARC member.")
        yield data[ofs:nul].decode("utf-8"), data[payload + start:payload + end]


def _layout_panes(data):
    if data[:4] != b"FLYT" or data[4:6] != b"\xff\xfe":
        raise LocalizationError("Unsupported menu layout header.")
    off = struct.unpack_from("<H", data, 6)[0]
    while off < len(data):
        size = struct.unpack_from("<I", data, off + 4)[0]
        if not 8 <= size <= len(data) - off:
            raise LocalizationError("Invalid menu layout section size.")
        if data[off:off + 4] == b"txt1":
            if size < 132:
                raise LocalizationError("Unsupported text pane layout.")
            _, actual = struct.unpack_from("<HH", data, off + 84)
            keyoff = struct.unpack_from("<I", data, off + 128)[0]
            key = ""
            if keyoff:
                end = data.find(b"\0", off + keyoff, off + size)
                if end < 0:
                    raise LocalizationError("Invalid text pane reference.")
                key = data[off + keyoff:end].decode("utf-8")
            yield actual, key
        off += size


def _ebml(data, pos, size=False):
    if pos >= len(data) or not data[pos]:
        raise LocalizationError("Truncated WebM VINT.")
    n = 9 - data[pos].bit_length()
    if pos + n > len(data):
        raise LocalizationError("Truncated WebM VINT.")
    value = int.from_bytes(data[pos:pos + n], "big")
    return (value ^ (1 << (7 * n)) if size else value), pos + n


def _webm_tracks(data):
    def elements(start, end):
        while start < end:
            kind, pos = _ebml(data, start)
            length, pos = _ebml(data, pos, True)
            stop = pos + length
            yield kind, pos, min(end, stop)
            start = stop
    for kind, start, end in elements(0, len(data)):
        if kind != 0x18538067:
            continue
        for code, i, j in elements(start, end):
            if code != 0x1654ae6b:
                continue
            result = []
            for item, x, y in elements(i, j):
                if item == 0xae:
                    track = {k: data[a:b] for k, a, b in elements(x, y)}
                    result.append((int.from_bytes(track[0x83], "big"), track[0x86].decode("ascii")))
            return result
    raise LocalizationError("WebM track metadata absent from bounded header.")


def audit_assets(romfs):
    """Inspect text-adjacent assets in place without exporting media or scripts."""
    root = _ordinary(Path(romfs), True)
    files, families, sounds = Counter(), Counter(), Counter()
    visual_paths, media, arcs = [], [], []
    for base, dirs, names in os.walk(root):
        relative = Path(base).relative_to(root)
        for name in dirs:
            _ordinary(Path(base) / name, True)
        top = relative.parts[0] if relative.parts else "."
        files[top] += len(names)
        if top in {"Movie", "Layout", "Font"}:
            for name in names:
                path = _ordinary(Path(base) / name)
                rel = path.relative_to(root).as_posix()
                if rel.startswith("Movie/Product/") and name.endswith(".webm"):
                    media.append(path)
                elif top == "Layout" and name.endswith(".arc.zst"):
                    arcs.append(path)
                if not rel.startswith("Layout/Textures/Tutorial/"):
                    visual_paths.append(rel)
        if top == "Sound":
            for name in names:
                components = re.split(r"[/_.-]+", (relative / name).as_posix())
                for lang in ("EN", "JP"):
                    if lang in components:
                        sounds[(lang, relative.parts[1] if len(relative.parts) > 1 else "root")] += 1
    tutorial = root / "Layout" / "Textures" / "Tutorial"
    images = {}
    locales = sorted(p for p in tutorial.iterdir() if p.is_dir() and p.name in LOCALES)
    for locale in locales:
        for p in sorted(locale.glob("*.bntx.zst")):
            images.setdefault(p.name, {})[locale.name] = _texture_digest(p)
    art = Counter()
    for versions in images.values():
        if "USen" not in versions:
            art["absent_us_candidate"] += 1
        else:
            art["us_images"] += 1
            art["us_pixels_differ_from_peer"] += int(any(v != versions["USen"] for v in versions.values()))
            art["us_pixels_differ_from_jp"] += int("JPja" in versions and versions["JPja"] != versions["USen"])
    containers, pane_keys = {}, set()
    for p in sorted(arcs):
        counts = Counter()
        for name, blob in _sarc_sections(_decompress(p, 256 << 20)):
            counts["members_" + name.rpartition(".")[2]] += 1
            if name.endswith(".bflyt"):
                for size, key in _layout_panes(blob):
                    counts["text_panes"] += 1
                    counts["panes_with_inline_text_bytes"] += bool(size)
                    counts["panes_with_named_reference"] += bool(key)
                    if key:
                        pane_keys.add(key)
        families.update(counts)
        containers[p.relative_to(root).as_posix()] = {"sha256": _hash_file(p), "counts": dict(counts)}
    videos = {}
    for p in sorted(media):
        with p.open("rb") as stream:
            tracks = _webm_tracks(stream.read(1024 * 1024))
        videos[p.name] = {"bytes": p.stat().st_size, "tracks": tracks, "sha256": _hash_file(p)}
    return {"file_count": sum(files.values()), "roots": dict(files), "tutorial_counts": dict(art),
            "tutorial_locale_payloads": images, "tutorial_manifest_sha256": _fingerprint(images),
            "layout_archives": containers, "layout_counts": dict(families), "layout_reference_ids": sorted(pane_keys),
            "videos": videos, "video_count": len(videos), "videos_with_subtitle_stream": sum(
                any(kind == 17 for kind, _ in v["tracks"]) for v in videos.values()),
            "voice_and_lipsync": {f"{lang}/{area}": n for (lang, area), n in sorted(sounds.items())},
            "other_visual_asset_paths": sorted(visual_paths)}


def audit(romfs, catalog, *, assets=True, campaign=None):
    result = audit_messages(romfs, catalog, campaign)
    if assets:
        result["assets"] = audit_assets(romfs)
    return result


def main():
    from .arabic import catalog_data
    from .arabic_campaign import Campaign
    cmd = argparse.ArgumentParser(description=__doc__)
    cmd.add_argument("--romfs", type=Path, required=True)
    cmd.add_argument("--catalog-dir", type=Path, default=Path(__file__).with_name("arabic_data"))
    cmd.add_argument("--campaign-dir", type=Path, help="A separate staged translation-shard directory")
    cmd.add_argument("--output", type=Path, required=True)
    cmd.add_argument("--messages-only", action="store_true")
    args = cmd.parse_args()
    _separate(args.romfs, args.output)
    catalog = catalog_data(sorted(args.catalog_dir.glob("*.json")))
    campaign = Campaign(args.campaign_dir or args.catalog_dir / "campaign")
    result = audit(args.romfs, catalog, assets=not args.messages_only, campaign=campaign)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False, mode="w", encoding="utf-8") as stream:
        tmp = Path(stream.name)
        try:
            json.dump(result, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            stream.close()
            os.replace(tmp, args.output)
        finally:
            stream.close()
            tmp.unlink(missing_ok=True)
    print(json.dumps({"tables": result["table_count"], "counts": result["counts"], "issues": result["issues"]}))


if __name__ == "__main__":
    main()
