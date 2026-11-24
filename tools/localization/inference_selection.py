"""Select actual incomplete, corrupt or semantically suspect units for a local second decoder."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from inference import normalized, rows, sections
from inference_repair import BOUNDARY


ELONGATION = re.compile(r"([\u0621-\u064a])\1{8,}")
NEGATIVE = re.compile(
    r"\b(?:not|never|nothing|nobody|nowhere|without|cannot)\b|n't\b", re.I
)
SENTENCE_END = re.compile(r"[؟?!.…]+")


def select(args):
    risks = {}
    for r in rows(args.risks):
        risks[r["english"]] = r
    already = set()
    for file in args.exclude:
        already.update(json.loads(file.read_bytes()))
    cache, queue = {}, {}
    for row in rows(args.catalog):
        name = row["file"]
        if name not in cache:
            table = args.pack / (name + ".json")
            cache[name] = json.loads(table.read_bytes())[name] if table.exists() else {}
        record = cache[name].get(row["label"])
        if not record:
            continue
        pairs = list(
            zip(
                (s for s in row["parts"] if isinstance(s, str)),
                record["parts"],
                strict=True,
            )
        )
        for key, originals in row.get("choices", {}).items():
            pairs.extend(zip(originals, record["choices"][key], strict=True))
        for original, output in pairs:
            source, target = normalized(original), normalized(output)
            if source in already or len(sections(original)) != 1 or len(source) > 400:
                continue
            words = len(source.split())
            fragments = BOUNDARY.split(source)
            flags = []
            if ELONGATION.search(target):
                flags.append("repeated-letter")
            if len(fragments) >= 2 and words >= 9:
                if len(target.split()) < words * 0.55 and len(
                    SENTENCE_END.findall(target)
                ) < len(fragments):
                    flags.append("short-multi-sentence")
            risk = risks.get(source, {})
            score = risk.get("baseline_similarity")
            if score is not None and score < 0.825 and words >= 5:
                flags.append("semantic-score")
            if (
                (score is None or score < 0.89)
                and NEGATIVE.search(source)
                and "check-source-negation" in risk.get("risks", [])
                and words >= 5
            ):
                flags.append("check-negation")
            if flags:
                queue.setdefault(
                    source,
                    {
                        "table": name,
                        "label": row["label"],
                        "arabic": target,
                        "flags": flags,
                        "score": score,
                    },
                )
    priority = {
        "repeated-letter": 12,
        "short-multi-sentence": 6,
        "check-negation": 4,
        "semantic-score": 3,
    }
    items = sorted(
        queue,
        key=lambda s: (
            -sum(priority[f] for f in queue[s]["flags"]),
            queue[s]["score"] or 1,
            s,
        ),
    )
    args.report.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    args.output.write_text(
        json.dumps(items[: args.limit], ensure_ascii=False), encoding="utf-8"
    )
    print(
        len(queue),
        Counter(f for q in queue.values() for f in q["flags"]),
        len(items[: args.limit]),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("risks", "pack", "catalog", "report", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--exclude", type=Path, action="append", default=[])
    p.add_argument("--limit", type=int, default=1200)
    select(p.parse_args())
