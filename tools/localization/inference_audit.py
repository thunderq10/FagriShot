"""Inventory bilingual review candidates across every table in a sealed local campaign."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from inference import connect, normalized, rows
from inference_pack import bitmap_text


NEG_EN = re.compile(
    r"\b(?:never|no longer|no one|nobody|nothing|without|cannot|can't|won't|shan't|don't|doesn't|didn't|"
    r"isn't|aren't|wasn't|weren't|couldn't|shouldn't|wouldn't|mustn't|haven't|hasn't|hadn't|not)\b",
    re.I,
)
NEG_AR = re.compile(
    r"(?<!\w)[وفب]?(?:لا|لـا|لم|لن|لما|لس[تمن]|لسنا|لستن|ليس\w*|غير|عدم|عديم\w*|دون|بدون|بلا|"
    r"مستحيل\w*|استحال\w*|محال|رفض\w*|امتنع\w*|تعذر|يتعذر|عاجز\w*)\b"
)
TECH = re.compile(r"\b(?:EX-\d{3}|[A-Z]\d{2,3})\b")
AR_WORD = re.compile(r"[\u0621-\u064a]+")
FOLD = str.maketrans("أإآى", "اااي")
AMBIGUOUS = {
    "Long",
    "Jester",
    "Creek",
    "Morrow",
    "Maxim",
    "Gladius",
    "Jasmine",
    "Mars",
    "Castor",
    "Aurora",
}


def audit_campaign(a):
    manifest = json.loads((a.campaign / "manifest.json").read_bytes())
    import zstandard

    draft = {}
    for filename in manifest["files"]:
        packed = (a.campaign / (filename + ".json.zst")).read_bytes()
        draft[filename] = json.loads(zstandard.ZstdDecompressor().decompress(packed))
    names = json.loads(a.terms.read_bytes())["names"]
    pattern = re.compile(
        r"(?<!\w)(?:"
        + "|".join(
            re.escape(s)
            for s in sorted(names.keys() - AMBIGUOUS, key=len, reverse=True)
        )
        + r")(?!\w)"
    )
    with connect(a.work) as db:
        scores = {
            s: (bitmap_text(t), score)
            for s, t, score in db.execute(
                """SELECT a.source,a.output,b.score FROM translations a
            JOIN similarity b ON a.source=b.source AND a.profile=b.profile WHERE a.profile=?""",
                (a.base,),
            )
        }
    a.out.mkdir(parents=True, exist_ok=True)
    counters = Counter()
    route = defaultdict(Counter)
    unitids = set()
    vocabulary = defaultdict(Counter)
    negation_missing = []
    with (a.out / "risks.jsonl").open("w", encoding="utf-8") as log:
        for r in rows(a.catalog):
            hit = draft.get(r["file"], {}).get(r["label"])
            if hit is None:
                continue
            group = r["file"].split(".", 1)[0].split("_", 1)[0]
            route[group]["total"] += 1
            source_parts = [s for s in r["parts"] if isinstance(s, str)]
            pairs = list(zip(source_parts, hit["parts"], strict=True))
            for k, alternatives in r.get("choices", {}).items():
                pairs.extend(zip(alternatives, hit["choices"][k], strict=True))
            for source, arabic in pairs:
                s, t = normalized(source), normalized(arabic)
                if not s:
                    continue
                risks = []
                if any(code not in t for code in TECH.findall(s)):
                    risks.append("technical-identifier")
                if any(ch in t for ch in "，。！？：；"):
                    risks.append("fullwidth-punctuation")
                if s.count("?") > t.count("؟") + t.count("?"):
                    risks.append("missing-question-mark")
                if NEG_EN.search(s) and not NEG_AR.search(t):
                    risks.append("check-source-negation")
                name_hits = sorted(set(pattern.findall(s)))
                unsettled = []
                for name in name_hits:
                    canonical = names[name]
                    if canonical.translate(FOLD) in t.translate(FOLD):
                        continue
                    unsettled.append(name)
                    words = AR_WORD.findall(t)
                    n = len(canonical.split())
                    vocabulary[name].update(
                        " ".join(words[i : i + n]) for i in range(len(words) - n + 1)
                    )
                if unsettled:
                    risks.append("check-name")
                if (
                    name_hits
                    and re.search(r"\byou(?:\b|')", s, re.I)
                    and ("," in s or "!" in s)
                ):
                    risks.append("named-addressee")
                score = None
                if s in scores and normalized(scores[s][0]) == t:
                    score = scores[s][1]
                    if score < a.low_score and len(s) >= 18:
                        risks.append("lower-semantic-similarity")
                if not risks:
                    continue
                unitids.add((r["file"], r["label"]))
                counters.update(risks)
                route[group].update(risks)
                log.write(
                    json.dumps(
                        {
                            "file": r["file"],
                            "label": r["label"],
                            "english": s,
                            "arabic": t,
                            "risks": risks,
                            "names": unsettled,
                            "baseline_similarity": score,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    proposals = []
    for name, words in vocabulary.items():
        canonical = names[name].translate(FOLD)
        candidates = []
        for word, count in words.items():
            if count < 2:
                continue
            norm = word.translate(FOLD)
            similarity = SequenceMatcher(None, canonical, norm).ratio()
            if similarity >= 0.47:
                candidates.append((count, round(similarity, 2), word))
        proposals.append((name, names[name], sorted(candidates, reverse=True)[:12]))
    (a.out / "name-proposals.json").write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {
        "distinct_draft_rows_flagged": len(unitids),
        "features": dict(counters),
        "by_resource_group": dict(route),
    }
    (a.out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"distinct_rows": len(unitids), "counts": dict(counters)}))


if __name__ == "__main__":
    p = ArgumentParser(description=__doc__)
    for n in ("catalog", "campaign", "work", "terms", "out"):
        p.add_argument("--" + n, type=Path, required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--low-score", type=float, default=0.83)
    audit_campaign(p.parse_args())
