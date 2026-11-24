"""Compare two private draft translations clause by clause before promoting a revision."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re

from inference import check, connect, file_hash
from inference_audit import NEG_AR, NEG_EN, TECH
from inference_pack import bitmap_text
from inference_repair import BOUNDARY


def read_map(path):
    return json.loads(path.read_bytes())


def compare(a):
    if a.cuda_dir:
        cuda = a.cuda_dir.resolve(strict=True)
        dlls = os.add_dll_directory(str(cuda))
        os.environ["PATH"] = str(cuda) + os.pathsep + os.environ["PATH"]
    import ctranslate2
    import numpy as np
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(a.encoder / "tokenizer.json"))
    tok.no_padding()
    tok.enable_truncation(512)
    model = ctranslate2.Encoder(
        str(a.encoder),
        device=a.device,
        compute_type="float16" if a.device == "cuda" else "int8",
        intra_threads=2,
    )
    risks = read_map(a.risks)
    excluded = set()
    for exact in a.exclude:
        excluded.update(read_map(exact))
    names = read_map(a.terms)["names"]
    aliases = read_map(a.aliases)
    from inference_consistency import compile_names, correct

    patterns = compile_names(names, aliases)
    hypothesis = {}
    with connect(a.work) as db:
        for s, t in db.execute(
            "SELECT source,output FROM translations WHERE profile=? AND issue=''",
            (a.profile,),
        ):
            if s not in risks or s in excluded or check(s, t):
                continue
            # Only substitute spellings of characters actually named by this source.
            t = bitmap_text(t)
            t, _ = correct(s, t, names, patterns)
            base = risks[s]["arabic"]
            if t == base or any(
                n in base and n not in t
                for e, n in names.items()
                if re.search(r"(?<!\w)" + re.escape(e) + r"(?!\w)", s)
            ):
                continue
            if any(code not in t for code in TECH.findall(s)):
                continue
            hypothesis[s] = t
    features = {}
    texts = set()
    for s, t in hypothesis.items():
        base = risks[s]["arabic"]
        clauses = [p for p in BOUNDARY.split(s) if len(p.split()) >= 3]
        features[s] = clauses
        texts.update([s, base, t, *clauses])
    texts = sorted(texts, key=lambda x: (len(x), x))
    vectors = {}
    for i in range(0, len(texts), 128):
        batch = texts[i : i + 128]
        tokens = [x.ids for x in tok.encode_batch(["query: " + text for text in batch])]
        state = np.asarray(
            model.forward_batch(tokens).last_hidden_state.to_device(
                ctranslate2.Device.cpu
            )
        )
        for j, (text, ids) in enumerate(zip(batch, tokens, strict=True)):
            vec = state[j, : len(ids)].mean(axis=0, dtype=np.float32)
            vectors[text] = vec / max(float(np.linalg.norm(vec)), 1e-12)
    output, audit = {}, []
    for s, candidate in hypothesis.items():
        risk = risks[s]
        old = risk["arabic"]
        pairs = [
            (float(vectors[t] @ vectors[old]), float(vectors[t] @ vectors[candidate]))
            for t in [s, *features[s]]
        ]
        full_gain = pairs[0][1] - pairs[0][0]
        clauses = pairs[1:]
        weakest = min(clauses, key=lambda p: p[0]) if clauses else pairs[0]
        recovered = weakest[1] - weakest[0]
        flags = risk["flags"]
        why = []
        if (
            "short-multi-sentence" in flags
            and len(candidate.split()) >= len(old.split()) + 2
            and clauses
            and full_gain >= 0.012
            and recovered >= 0.028
        ):
            why.append("recovered-clause")
        if (
            "check-negation" in flags
            and NEG_EN.search(s)
            and not NEG_AR.search(old)
            and NEG_AR.search(candidate)
            and full_gain >= 0.018
        ):
            why.append("restored-negation")
        if "semantic-score" in flags and full_gain >= 0.045 and pairs[0][1] >= 0.86:
            why.append("higher-bilingual-agreement")
        if any(new < old_score - 0.045 for old_score, new in clauses):
            why = []
        if why:
            output[s] = candidate
        audit.append(
            {
                "source": s,
                "original": old,
                "candidate": candidate,
                "full_gain": round(full_gain, 4),
                "weakest_clause_gain": round(recovered, 4),
                "full_score": round(pairs[0][1], 4),
                "reasons": why,
            }
        )
    a.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    a.report.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Compared {len(audit)} valid changed candidates. Promoted {len(output)}.",
        dict(Counter(x for row in audit for x in row["reasons"])),
        "Encoder",
        file_hash(a.encoder / "model.bin"),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for k in ("work", "risks", "encoder", "output", "report", "terms", "aliases"):
        p.add_argument("--" + k, type=Path, required=True)
    p.add_argument("--exclude", type=Path, action="append", default=[])
    p.add_argument("--profile", required=True)
    p.add_argument("--cuda-dir", type=Path)
    p.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    compare(p.parse_args())
