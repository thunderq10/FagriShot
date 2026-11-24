"""Flag Arabic dialogue for bilingual review using the owner's matching game tables."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import struct
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fagrishot.arabic import catalog_data
from fagrishot.arabic_campaign import Campaign
from fagrishot.localization import _separate, read_resource


# An explicit first-person predicate is stronger evidence than an isolated word.
# Only list forms where agreement is audible or visible without vowel marks.
AR_PAIRS = [
    ("آسف", "آسفة"),
    ("سعيد", "سعيدة"),
    ("مسرور", "مسرورة"),
    ("ممتن", "ممتنة"),
    ("قلق", "قلقة"),
    ("مستعد", "مستعدة"),
    ("جاهز", "جاهزة"),
    ("متأكد", "متأكدة"),
    ("مهتم", "مهتمة"),
    ("فخور", "فخورة"),
    ("واثق", "واثقة"),
    ("قادر", "قادرة"),
    ("خائف", "خائفة"),
    ("محظوظ", "محظوظة"),
    ("مخطئ", "مخطئة"),
    ("مدين", "مدينة"),
    ("مضطر", "مضطرة"),
    ("حزين", "حزينة"),
    ("متعب", "متعبة"),
    ("وحيد", "وحيدة"),
    ("شاكر", "شاكرة"),
    ("مندهش", "مندهشة"),
    ("معجب", "معجبة"),
    ("مصاب", "مصابة"),
]
FR_PAIRS = [
    ("désolé", "désolée"),
    ("heureux", "heureuse"),
    ("prêt", "prête"),
    ("ravi", "ravie"),
    ("inquiet", "inquiète"),
    ("fier", "fière"),
    ("sûr", "sûre"),
    ("reconnaissant", "reconnaissante"),
    ("certain", "certaine"),
    ("obligé", "obligée"),
    ("surpris", "surprise"),
    ("chanceux", "chanceuse"),
    ("seul", "seule"),
    ("fatigué", "fatiguée"),
    ("convaincu", "convaincue"),
    ("blessé", "blessée"),
    ("content", "contente"),
    ("curieux", "curieuse"),
    ("étonné", "étonnée"),
]
AR = {
    word: form
    for m, f in AR_PAIRS
    for word, form in ((m, "m"), (m + "ا", "m"), (f, "f"))
}
FR = {word: form for m, f in FR_PAIRS for word, form in ((m, "m"), (f, "f"))}


def alternatives(words):
    return "|".join(map(re.escape, sorted(words, key=len, reverse=True)))


AR_SELF = re.compile(
    r"(?<!\w)[وف]?(?:أنا|إنني|إني|أنني|أني|لكنني|لكنني أنا|لست|لأنني)\s+"
    r"(?:(?:حقا|الآن|أيضا|جد|غير|بالطبع)\s+){0,2}(" + alternatives(AR) + r")(?!\w)"
)
FR_SELF = re.compile(
    r"\b(?:je (?:ne )?suis|j['’]étais)\s+"
    r"(?:(?:pas|plus|bien|vraiment|très|si|encore|toujours|assez)\s+){0,3}("
    + alternatives(FR)
    + r")\b",
    re.I,
)
EN_SELF = re.compile(r"\bI\b", re.I)
SENSES = (
    ("musical epithet", r"Siren of the Six.String", r"صفارة|الإنذار"),
    (
        "battlefield encouragement",
        r"^(?!.*\b(?:groundwater|soil|shovel)\b).*\bdig deep\b",
        r"حفر|تحفر",
    ),
    (
        "military range",
        r"range of (?:the |your )?(?:nearest )?(?:foes|enemies)",
        r"مجموعة",
    ),
    ("healing item", r"\bvulnerar(?:y|ies)\b", r"القهر|الضعفاء"),
    (
        "joust or tournament name",
        r"\bHeroic Games\b",
        r"الألعاب البطولية|الألعاب الأولمبية",
    ),
    (
        "mercenary company",
        r"\bcompan(?:y|ies) of mercenaries\b|\bmercenary compan(?:y|ies)\b",
        r"شرك[ةت]",
    ),
    ("mounted unit", r"\b(?:his|her|your|their) mount\b", r"جبل|تركيب"),
)
SENSES = [(label, re.compile(en, re.I), re.compile(ar)) for label, en, ar in SENSES]


def attribute_reader(table):
    """Resolve this table's field layout once. Other schemas have no speaker evidence."""
    if not {b"ATO1", b"ATR1"} <= table.sections.keys():
        return None
    offsets, data = table._section(b"ATO1"), table._section(b"ATR1")
    if len(offsets) < 16 or len(data) < 8:
        return None
    condition, _, _, speaker = struct.unpack_from("<4i", offsets)
    count, size = struct.unpack_from("<II", data)
    pool = 8 + count * size
    if size != 74 or (condition, speaker) != (0, 12) or pool > len(data):
        return None

    def read(index):
        if not 0 <= index < count:
            raise ValueError("Message index outside its attribute table")

        def field(offset):
            (ptr,) = struct.unpack_from("<I", data, 8 + index * size + offset)
            end = data.find(b"\0", ptr) if pool <= ptr < len(data) else -1
            if end < 0:
                raise ValueError("Invalid dialogue attribute string")
            return data[ptr:end].decode("utf8")

        return field(speaker), field(condition)

    return read


def text(parts):
    return " ".join(" ".join(p.split()) for p in parts if isinstance(p, str)).strip()


def agreement_evidence(en, fr, ar):
    if not EN_SELF.search(en):
        return set(), set()
    ar = "".join(
        c for c in unicodedata.normalize("NFC", ar) if unicodedata.category(c) != "Mn"
    )
    return {FR[m[1].lower()] for m in FR_SELF.finditer(fr)}, {
        AR[m[1]] for m in AR_SELF.finditer(ar)
    }


def player_title_check(source, target, condition):
    if not re.search(r"\bPromised One\b", source, re.IGNORECASE):
        return False
    for address, gate in (
        ("أيها الموعود", "MS限定_主人公_男性"),
        ("أيتها الموعودة", "MS限定_主人公_女性"),
    ):
        if address in target and condition != gate:
            return True
    return False


def audit(romfs, reviewed, campaign):
    en_dir, fr_dir = (romfs / "Msg" / lang for lang in ("USen", "EUfr"))
    stats = Counter()
    issues = []
    votes = defaultdict(lambda: defaultdict(set))
    candidates = []
    for name in sorted(campaign.files.keys() | reviewed.keys()):
        en = read_resource(en_dir / name)
        fr = read_resource(fr_dir / name) if (fr_dir / name).is_file() else None
        attrs = attribute_reader(en)
        for label, record in campaign.merged(name, reviewed.get(name, {})).items():
            entry = en.entry(label)
            if record["source_sha256"] != entry.source_sha256:
                raise ValueError(f"Stale translation in {name}/{label}")
            source, target = text(entry.parts), text(record["parts"])
            reference = text(fr.entry(label).parts) if fr and label in fr.labels else ""
            speaker, condition = attrs(en.labels[label]) if attrs else ("", "")
            stats["entries"] += 1
            stats["named_speaker"] += bool(speaker)
            facts, forms = agreement_evidence(source, reference, target)
            if speaker and len(facts) == 1 and not condition:
                votes[speaker][next(iter(facts))].add(name)
            reasons = [
                desc
                for desc, en_pattern, ar_pattern in SENSES
                if en_pattern.search(source) and ar_pattern.search(target)
            ]
            if player_title_check(source, target, condition):
                reasons.append(
                    "gendered address to the protagonist outside its matching player condition"
                )
            if forms or reasons:
                candidates.append(
                    (name, label, speaker, condition, facts, forms, reasons)
                )
    for name, label, speaker, condition, facts, forms, reasons in candidates:
        if len(facts) == 1 and len(forms) == 1 and facts != forms:
            reasons.append("first-person forms disagree across languages")
        elif forms and not facts and speaker and not condition:
            corroboration = votes[speaker]
            if len(corroboration) == 1:
                sex, examples = next(iter(corroboration.items()))
                if len(examples) >= 2 and forms == ({"m", "f"} - {sex}):
                    reasons.append(
                        "first-person form differs from speaker's other scenes"
                    )
        if reasons:
            issues.append(
                {
                    "table": name,
                    "label": label,
                    "speaker": speaker,
                    "condition": condition,
                    "checks": reasons,
                }
            )
    return {"counts": dict(stats), "flags": len(issues), "review": issues}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--romfs", type=Path, required=True)
    cli.add_argument("--reviewed", type=Path, required=True)
    cli.add_argument("--campaign", type=Path, required=True)
    cli.add_argument("--report", type=Path, required=True)
    args = cli.parse_args()
    _separate(args.romfs, args.report)
    if not args.reviewed.is_dir() or not (args.campaign / "manifest.json").is_file():
        cli.error(
            "Provide the reviewed catalog directory and a campaign with a manifest."
        )
    report = audit(
        args.romfs,
        catalog_data(sorted(args.reviewed.glob("*.json"))),
        Campaign(args.campaign),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    print(
        json.dumps({"counts": report["counts"], "flagged_for_review": report["flags"]})
    )
