"""Rank private English/Arabic candidate pairs for review using multilingual E5 on the GPU."""

from argparse import ArgumentParser
import os
from pathlib import Path

from inference import connect, file_hash


def score(args):
    if args.cuda_dir:
        dlls = os.add_dll_directory(str(args.cuda_dir.resolve(strict=True)))
        os.environ["PATH"] = (
            str(args.cuda_dir.resolve()) + os.pathsep + os.environ["PATH"]
        )
    import ctranslate2
    import numpy as np
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(args.model / "tokenizer.json"))
    tok.no_padding()
    tok.enable_truncation(512)
    encoder = ctranslate2.Encoder(
        str(args.model), device="cuda", compute_type="float16"
    )
    identity = file_hash(args.model / "model.bin")

    def encode(texts):
        ids = [x.ids for x in tok.encode_batch(["query: " + text for text in texts])]
        state = np.asarray(
            encoder.forward_batch(ids).last_hidden_state.to_device(
                ctranslate2.Device.cpu
            )
        )
        pooled = np.asarray(
            [
                state[i, : len(t)].mean(axis=0, dtype=np.float32)
                for i, t in enumerate(ids)
            ]
        )
        return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)

    with connect(args.work) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS similarity (source TEXT, profile TEXT, encoder TEXT, score REAL,
                   PRIMARY KEY(source,profile,encoder))""")
        for profile in args.profile:
            pairs = list(
                db.execute(
                    """SELECT t.source,t.output FROM translations t
                WHERE t.profile=? AND NOT EXISTS (SELECT 1 FROM similarity s
                WHERE t.source=s.source AND t.profile=s.profile AND s.encoder=?) ORDER BY length(t.source)""",
                    (profile, identity),
                )
            )
            for i in range(0, len(pairs), args.batch):
                batch = pairs[i : i + args.batch]
                a = encode([s for s, _ in batch])
                b = encode([t for _, t in batch])
                cosines = (a * b).sum(axis=1)
                db.executemany(
                    "INSERT INTO similarity VALUES(?,?,?,?)",
                    [
                        (s, profile, identity, float(c))
                        for (s, _), c in zip(batch, cosines, strict=True)
                    ],
                )
                db.commit()
            print(f"Scored {len(pairs)} candidate pairs for {profile}.", flush=True)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--cuda-dir", type=Path)
    parser.add_argument("--batch", type=int, default=160)
    args = parser.parse_args()
    if args.batch < 1:
        parser.error("Batch must be positive.")
    score(args)
