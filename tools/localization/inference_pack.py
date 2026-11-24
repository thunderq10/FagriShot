"""Build a private candidate campaign from verified source IDs and accepted translations."""

from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

import zstandard
from inference import PARAGRAPH, connect, editable, lexical, normalized, rows


_GAMES = re.compile(r"\bHeroic\s+Games\b", re.IGNORECASE)
_AR_GAMES = re.compile(
    r"(لل[أا]لعاب|لألعاب|ال[أا]لعاب|(?<!\w)ألعاب)[ \t\n]+(?:البطولية|بطولية|البطولة)(?!\w)"
)


def tournament_title(source, target):
    """Apply the approved event name only where the source names the tournament."""
    if not _GAMES.search(source):
        return target
    return _AR_GAMES.sub(
        lambda m: ("لألعاب" if m[1].startswith("ل") else "ألعاب") + " الأبطال", target
    )


def bitmap_text(text):
    # Arabic vowel signs have no positioning in the guest's advancing bitmap renderer.
    # NFC first retains the hamza on letters written as decomposed base + hamza.
    punctuation = str.maketrans("，。！？：；　", "،.!؟:؛ ")
    return "".join(
        c
        for c in unicodedata.normalize("NFC", text).translate(punctuation)
        if not (unicodedata.category(c) == "Mn" and "ARABIC" in unicodedata.name(c, ""))
    )


def formatted(source, accepted):
    """Keep boundaries next to MSBT tokens and explicit paragraph breaks."""
    result = []
    for i, part in enumerate(PARAGRAPH.split(source)):
        if i % 2 or not lexical(part):
            result.append(part)
            continue
        value = accepted.get(normalized(part))
        if value is None:
            return None
        left = part[: len(part) - len(part.lstrip())]
        right = part[len(part.rstrip()) :]
        result.append(left + tournament_title(part, bitmap_text(value)) + right)
    return "".join(result)


def dump(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def export(args):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fagrishot.localization_flow import supports

    accepted = {}
    with connect(args.work) as db:
        for profile in args.profile:
            accepted.update(
                db.execute(
                    "SELECT source,output FROM translations WHERE profile=? AND issue=''",
                    (profile,),
                )
            )
    for dictionary in args.terms:
        terms = json.loads(dictionary.read_bytes())
        if not isinstance(terms, dict) or any(
            not isinstance(v, str) or not v.strip() for v in terms.values()
        ):
            raise ValueError(
                "An exact translation memory must map text to nonempty strings."
            )
        accepted.update(terms)
    ui_accepted = accepted.copy()
    for dictionary in args.ui_terms:
        records = json.loads(dictionary.read_bytes())
        if not isinstance(records, dict) or any(
            not isinstance(v, str) or not v.strip() for v in records.values()
        ):
            raise ValueError(
                "A UI memory must map exact source units to Arabic strings."
            )
        ui_accepted.update(records)
    reviewed = {}
    for catalog in args.reviewed.glob("*.json"):
        for name, entries in json.loads(catalog.read_bytes()).items():
            reviewed.setdefault(name, {}).update(entries)
    grouped, pending = {}, []
    total = eligible = reviewed_count = 0
    for row in rows(args.catalog):
        total += 1
        name, label = row["file"], row["label"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.msbt\.zst", name):
            raise ValueError("Unsupported resource name")
        if not any(text.strip() for text in editable(row)):
            continue
        eligible += 1
        if label in reviewed.get(name, {}):
            if reviewed[name][label]["source_sha256"] != row["source_sha256"]:
                raise ValueError(f"Reviewed source mismatch in {name}, {label}")
            reviewed_count += 1
            continue
        selected = (
            ui_accepted
            if name.startswith(
                ("System", "Skill", "Item", "Guide", "Tutorial", "Roster")
            )
            else accepted
        )
        parts = [formatted(s, selected) for s in row["parts"] if isinstance(s, str)]
        choices = {
            k: [formatted(s, selected) for s in pair]
            for k, pair in row.get("choices", {}).items()
        }
        if None in parts or any(None in pair for pair in choices.values()):
            pending.append({"file": name, "label": label})
            continue
        record = {"source_sha256": row["source_sha256"], "parts": parts}
        if choices:
            record["choices"] = choices
        if supports(row["parts"]):
            record["flow"] = "auto"
        grouped.setdefault(name, {})[label] = record
    if args.output.exists():
        raise ValueError("Use a fresh output folder for each campaign generation.")
    args.output.mkdir(parents=True)
    pack, campaign = args.output / "pack", args.output / "campaign"
    pack.mkdir()
    campaign.mkdir()
    compressor = zstandard.ZstdCompressor(level=12, write_content_size=True)
    manifest = {
        "schema": 1,
        "source_update": 131072,
        "source_labels": total,
        "eligible_labels": eligible,
        "files": {},
    }
    for name, records in sorted(grouped.items()):
        dump(pack / (name + ".json"), {name: records})
        data = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(data) > 16 * 1024**2:
            raise ValueError(f"Campaign file exceeds size limit: {name}")
        packed = compressor.compress(data)
        (campaign / (name + ".json.zst")).write_bytes(packed)
        manifest["files"][name] = {
            "sha256": hashlib.sha256(packed).hexdigest(),
            "entries": len(records),
        }
    draft = sum(len(v) for v in grouped.values())
    if draft + reviewed_count + len(pending) != eligible:
        raise AssertionError("Campaign count does not reconcile.")
    dump(args.output / "pending.json", pending)
    dump(
        args.output / "coverage.json",
        {
            "source": total,
            "eligible": eligible,
            "reviewed": reviewed_count,
            "draft": draft,
            "pending": len(pending),
        },
    )
    dump(campaign / "manifest.json", manifest)
    print(
        f"{draft} draft, {reviewed_count} reviewed, {len(pending)} pending of {eligible} eligible rows."
    )


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument(
        "--profile",
        action="append",
        required=True,
        help="Later accepted profiles take precedence.",
    )
    parser.add_argument("--terms", type=Path, action="append", default=[])
    parser.add_argument("--ui-terms", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args())
