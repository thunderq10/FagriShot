"""Apply approved alternate spellings only to segments that explicitly name that character."""

from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
import re

from inference import normalized, rows


def compile_names(official, aliases):
    regex = {}
    canonical = {}
    for name, variants in aliases.items():
        if (
            name not in official
            or not isinstance(variants, list)
            or not variants
            or any(not v.strip() for v in variants)
        ):
            raise ValueError(
                "An approved alias must identify a canonical glossary name."
            )
        choices = "|".join(
            re.escape(v) for v in sorted(variants, key=len, reverse=True)
        )
        regex[name] = re.compile(
            r"(?<![\u0621-\u064a])([وف]?[بكل]?)(" + choices + r")(?![\u0621-\u064a])"
        )
        canonical[name] = re.compile(
            r"(?<![\u0621-\u064a])[وف]?[بكل]?"
            + re.escape(official[name])
            + r"(?![\u0621-\u064a])"
        )
    en = re.compile(
        r"(?<!\w)(?:"
        + "|".join(re.escape(n) for n in sorted(aliases, key=len, reverse=True))
        + r")(?!\w)"
    )
    return en, regex, canonical


def correct(source, target, official, patterns):
    en, regex, canonical = patterns
    named = Counter(en.findall(source))
    counts = Counter()
    for name in sorted(named):
        if (
            len(regex[name].findall(target)) + len(canonical[name].findall(target))
            > named[name]
        ):
            continue
        target, n = regex[name].subn(lambda m: m[1] + official[name], target)
        if n:
            counts[name] += n
    return target, counts


def repair(args):
    official = json.loads(args.terms.read_bytes())["names"]
    aliases = json.loads(args.aliases.read_bytes())
    patterns = compile_names(official, aliases)
    data = {}
    for table in args.pack.glob("*.json"):
        data.update(json.loads(table.read_bytes()))
    memory = {}
    conflicts = set()
    counts = Counter()
    labels = set()
    for row in rows(args.catalog):
        entry = data.get(row["file"], {}).get(row["label"])
        if not entry:
            continue
        pairs = list(
            zip(
                [s for s in row["parts"] if isinstance(s, str)],
                entry["parts"],
                strict=True,
            )
        )
        for k, choices in row.get("choices", {}).items():
            pairs.extend(zip(choices, entry["choices"][k], strict=True))
        for s, old in pairs:
            # A paragraph is an independent translation-memory unit.
            if "\n\n" in s or "\n\n" in old:
                continue
            s = normalized(s)
            t = old.strip()
            t, changes = correct(s, t, official, patterns)
            counts.update(changes)
            if t == old.strip():
                continue
            if s in memory and t != memory[s]:
                conflicts.add(s)
            else:
                memory[s] = t
                labels.add((row["file"], row["label"]))
    for s in conflicts:
        memory.pop(s, None)
    args.output.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "spelling_replacements": sum(counts.values()),
                "labels": len(labels),
                "unambiguous_units": len(memory),
                "conflicting_units_excluded": len(conflicts),
                "by_name": counts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = ArgumentParser(description=__doc__)
    for k in ("pack", "catalog", "terms", "aliases", "output"):
        p.add_argument("--" + k, type=Path, required=True)
    repair(p.parse_args())
