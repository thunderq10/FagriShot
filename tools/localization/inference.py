"""Translate a private MSBT catalog offline with a local SentencePiece Marian model.

Only strings and known choice alternatives are sent to the local CTranslate2 GPU.
The SQLite translation memory records complete batches and can resume after a stop.
"""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata


PARAGRAPH = re.compile(r"(\n[ \t]*\n(?:[ \t]*\n)*)")
ARABIC = re.compile(r"[\u0621-\u064a\u066e-\u06d3]")
LATIN = re.compile(r"[A-Za-z]+")
NUMERALS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalized(text):
    return " ".join(text.split())


def sections(text):
    return [normalized(part) for part in PARAGRAPH.split(text)[::2] if normalized(part)]


def editable(row):
    parts = [p for p in row["parts"] if isinstance(p, str)]
    return parts + [p for pair in row.get("choices", {}).values() for p in pair]


def lexical(text):
    return any(c.isalpha() for c in text)


def rows(catalog):
    with catalog.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def check(source, result):
    """Mechanical checks identify candidates for a second decoder or an editor."""
    if (
        not result
        or "\ufffd" in result
        or "<unk>" in result
        or re.search(r"\*{3,}", result)
        or any(unicodedata.category(c) in {"Cc", "Co", "Cf"} for c in result)
    ):
        return "invalid-character"
    if lexical(source) and not ARABIC.search(result):
        return "missing-arabic"
    if re.search(r"([\u0621-\u064a])\1{8,}", result):
        return "repeated-letter"
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", result):
        return "unexpected-script"
    if re.search(
        r"<[^>]+>|\{[^}]*\}|\b(?:translation|assistant)\s*:|(?:المرجع الياباني|النص المجاور|للسياق فقط|"
        r"المصطلح(?:ات)? المعتمد|(?:الترجمة|النص المترجم)\s*[:：]|بدون شرح إضافي|تمت الموافقة على (?:استخدام )?المصطلح)",
        result,
        re.I,
    ):
        return "extra-markup"
    if (
        len(result) > max(30, len(source) * 3.5)
        or len(source) > 30
        and len(result) < len(source) * 0.18
    ):
        return "length"
    if re.search(r"(\S+(?:\s+\S+){0,3})(?:\s+\1){3,}", result) and not re.search(
        r"(\S+(?:\s+\S+){0,3})(?:\s+\1){2,}", source, re.I
    ):
        return "repetition"
    if Counter(re.findall(r"\d+", source.translate(NUMERALS))) != Counter(
        re.findall(r"\d+", result.translate(NUMERALS))
    ):
        return "numerals"
    if source.count("?") > result.count("؟") + result.count("?"):
        return "questions"
    if len(source) > 70 and len(result) < len(source) * 0.44:
        return "short-clause"
    if LATIN.search(result):
        return "latin"
    return ""


def audit(db, profile):
    changed = []
    for source, result, old in db.execute(
        "SELECT source,output,issue FROM translations WHERE profile=?", (profile,)
    ):
        issue = old if old == "incomplete" else check(source, result)
        if issue != old:
            changed.append((issue, source, profile))
    db.executemany(
        "UPDATE translations SET issue=? WHERE source=? AND profile=?", changed
    )
    db.commit()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def connect(work):
    work.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(work / "memory.sqlite", timeout=40)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS translations (
        source TEXT NOT NULL, profile TEXT NOT NULL, output TEXT NOT NULL, issue TEXT NOT NULL,
        PRIMARY KEY (source,profile))""")
    return db


def translate(args):
    # Registering an existing DLL folder affects this process only.
    if args.cuda_dir:
        dll_directory = os.add_dll_directory(str(args.cuda_dir.resolve(strict=True)))
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
    files = {
        n: file_hash(args.model / n) for n in ("model.bin", "source.spm", "target.spm")
    }
    identity = {
        "model": files,
        "beam": args.beam,
        "version": ctranslate2.__version__,
        "recipe": 1,
    }
    profile = (
        "opus-"
        + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    )
    with connect(args.work) as db:
        unique = {
            section
            for row in rows(args.catalog)
            for p in editable(row)
            for section in sections(p)
            if lexical(section)
        }
        previous = {
            r[0]
            for r in db.execute(
                "SELECT source FROM translations WHERE profile=?", (profile,)
            )
        }
        remaining = sorted(unique - previous, key=lambda s: (len(s), s))
        if args.limit:
            remaining = remaining[: args.limit]
        (args.work / "opus-profile.json").write_text(
            json.dumps({"profile": profile, **identity}, indent=2), encoding="utf-8"
        )
        print(
            f"CUDA {translator.compute_type}, {len(unique)} distinct lexical units, {len(previous)} cached, {len(remaining)} queued",
            flush=True,
        )
        start = time.monotonic()
        count = 0
        flagged = 0
        for offset in range(0, len(remaining), args.batch):
            sources = remaining[offset : offset + args.batch]
            inputs = [source_sp.encode(s, out_type=str) + ["</s>"] for s in sources]
            outputs = translator.translate_batch(
                inputs,
                beam_size=args.beam,
                max_batch_size=args.batch,
                max_input_length=max(map(len, inputs)),
                max_decoding_length=min(480, max(24, max(map(len, inputs)) * 3 + 10)),
                repetition_penalty=1.06,
                disable_unk=True,
            )
            records = []
            for s, result in zip(sources, outputs, strict=True):
                value = target_sp.decode(result.hypotheses[0]).strip()
                issue = check(s, value)
                records.append((s, profile, value, issue))
                flagged += bool(issue)
            db.executemany(
                "INSERT OR REPLACE INTO translations VALUES(?,?,?,?)", records
            )
            db.commit()
            count += len(records)
            if (
                offset == 0
                or (offset // args.batch) % 10 == 0
                or count == len(remaining)
            ):
                print(
                    f"{count}/{len(remaining)} committed; {flagged} queued for review; {count / (time.monotonic() - start):.1f} units/s",
                    flush=True,
                )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--cuda-dir", type=Path)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--beam", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    if args.batch < 1 or args.beam < 1 or args.limit < 0:
        p.error("Batch and beam must be positive and limit must be nonnegative.")
    translate(args)
