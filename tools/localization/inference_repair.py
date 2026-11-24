"""Translate mechanically flagged units one clause at a time and preserve original quantities."""

from argparse import ArgumentParser
from collections import defaultdict
import hashlib
import html
import json
import os
from pathlib import Path
import re

from inference import check, connect, editable, file_hash, lexical, rows, sections


BOUNDARY = re.compile(r'(?<=[?!])\s+|(?<=[a-z][.])\s+(?=["(]?[A-Z])')
PUNCT = str.maketrans({"?": "؟", ",": "،", ";": "؛"})
ABBREV = {
    "HP",
    "Str",
    "Mag",
    "Dex",
    "Spd",
    "Lck",
    "Def",
    "Res",
    "Cha",
    "Mov",
    "Atk",
    "Phys",
    "Mys",
    "Spec",
    "Mt",
    "Rec",
    "Pwr",
    "Hit",
    "AS",
    "Avo",
    "Ddg",
    "Crit",
    "Prt",
    "Rsl",
    "Rng",
    "Bld",
    "Exp",
    "Lv",
    "Wt",
}


def scoped_fragment(text, tables, terms):
    ui = terms["ui"]
    if not all(
        t.startswith(("Skill", "System", "Item", "Tutorial", "Roster", "Guide"))
        for t in tables
    ):
        return None
    if text in ui:
        return ui[text]
    parts = re.fullmatch(
        r"(,\s*|[Gg]rants\s+|,\s*grants\s+|,\s*or\s+)?([A-Za-z]+(?:/[A-Za-z]+)*)", text
    )
    if not parts:
        return None
    stats = parts[2].split("/")
    if not all(x in ABBREV and x in ui for x in stats):
        return None
    prefix = {
        ",": "، ",
        "grants": "يمنح ",
        ", grants": "، يمنح ",
        ", or": "، أو ",
        "": "",
    }
    return prefix[(parts[1] or "").strip().lower()] + "/".join(ui[x] for x in stats)


def repair(args):
    if args.cuda_dir:
        dlls = os.add_dll_directory(str(args.cuda_dir.resolve(strict=True)))
        os.environ["PATH"] = (
            str(args.cuda_dir.resolve()) + os.pathsep + os.environ["PATH"]
        )
    import ctranslate2
    import sentencepiece

    source_sp = sentencepiece.SentencePieceProcessor(
        model_file=str(args.model / "source.spm")
    )
    target_sp = sentencepiece.SentencePieceProcessor(
        model_file=str(args.model / "target.spm")
    )
    translator = ctranslate2.Translator(
        str(args.model), device="cuda", compute_type="float16"
    )
    terms = json.loads(args.terms.read_bytes())
    identity = {"base": args.base, "terms": file_hash(args.terms), "recipe": 1}
    profile = (
        "clause-"
        + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    )
    (args.work / "clause-profile.json").write_text(
        json.dumps({"profile": profile, **identity}), encoding="utf-8"
    )
    uses = defaultdict(set)
    for row in rows(args.catalog):
        for part in editable(row):
            for section in sections(part):
                uses[section].add(row["file"])
    with connect(args.work) as db:
        queue = dict(
            db.execute(
                "SELECT source,issue FROM translations WHERE profile=? AND issue<>''",
                (args.base,),
            )
        )
        ready = {}
        for s in queue:
            fixed = scoped_fragment(s, uses[s], terms)
            if fixed:
                ready[s] = fixed
        chunks = {s: BOUNDARY.split(s) for s in queue if s not in ready}
        fragments = sorted(
            {p for parts in chunks.values() for p in parts}, key=lambda x: (len(x), x)
        )
        outputs = {}
        for i in range(0, len(fragments), 128):
            batch = fragments[i : i + 128]
            ids = [source_sp.encode(s, out_type=str) + ["</s>"] for s in batch]
            responses = translator.translate_batch(
                ids,
                beam_size=6,
                num_hypotheses=4,
                disable_unk=True,
                max_input_length=max(map(len, ids)),
                max_decoding_length=min(480, max(24, max(map(len, ids)) * 3 + 10)),
            )
            for source, result in zip(batch, responses, strict=True):
                if not lexical(source):
                    outputs[source] = source.translate(PUNCT)
                    continue
                candidates = []
                for tokens in result.hypotheses:
                    target = html.unescape(target_sp.decode(tokens)).strip()
                    # Each independently decoded question contributes its own question mark.
                    terminal = re.search(r"([!?]+)$", source)
                    if (
                        terminal
                        and "?" in terminal[1]
                        and not re.search(r"[؟?]", target)
                    ):
                        target = target.rstrip("!.؟?… ") + terminal[1].translate(PUNCT)
                    candidates.append(target)
                outputs[source] = next(
                    (t for t in candidates if not check(source, t)), candidates[0]
                )
        for source, parts in chunks.items():
            ready[source] = " ".join(outputs[p] for p in parts)
        records = [(s, profile, a, check(s, a)) for s, a in ready.items()]
        db.executemany("INSERT OR REPLACE INTO translations VALUES(?,?,?,?)", records)
        db.commit()
        print(
            f"{len(records)} candidates; {sum(not r[3] for r in records)} mechanically accepted.",
            flush=True,
        )


if __name__ == "__main__":
    p = ArgumentParser(description=__doc__)
    for key in ("work", "catalog", "terms", "model"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--cuda-dir", type=Path)
    repair(p.parse_args())
