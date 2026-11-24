"""Fetch pinned public model assets into an explicitly chosen private working folder."""

from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen


MODELS = {
    "opus": (
        "Helsinki-NLP/opus-mt-tc-big-en-ar",
        "ae9f1b2fd512000ca9440eb3b8ec9aa9acb298ac",
        (
            "README.md",
            "config.json",
            "generation_config.json",
            "model.safetensors",
            "source.spm",
            "target.spm",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "vocab.json",
        ),
    ),
    "hy7": (
        "tencent/HY-MT1.5-7B-GGUF",
        "126325496bc8e3575f1d8615b8ca951d8483f206",
        ("HY-MT1.5-7B-Q6_K.gguf",),
    ),
    "qwen14": (
        "Qwen/Qwen3-14B-GGUF",
        "530227a7d994db8eca5ab5ced2fb692b614357fd",
        ("Qwen3-14B-Q4_K_M.gguf",),
    ),
    "e5": (
        "intfloat/multilingual-e5-small",
        "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        (
            "README.md",
            "config.json",
            "model.safetensors",
            "sentencepiece.bpe.model",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
        ),
    ),
}


def fetch(model, folder):
    repo, rev, names = MODELS[model]
    folder.mkdir(parents=True, exist_ok=True)
    with urlopen(
        f"https://huggingface.co/api/models/{repo}/tree/{rev}?expand=true", timeout=60
    ) as response:
        listing = {item["path"]: item for item in json.load(response)}

    def download(name):
        target = folder / name
        stage = folder / (name + ".part")
        info = listing[name]
        path = target if target.exists() else stage
        if not target.exists() and (
            not stage.exists() or stage.stat().st_size != info["size"]
        ):
            size = stage.stat().st_size if stage.exists() else 0
            req = Request(
                f"https://huggingface.co/{repo}/resolve/{rev}/{name}?download=true",
                headers={"Range": f"bytes={size}-"} if size else {},
            )
            with urlopen(req, timeout=180) as remote:
                append = size and remote.status == 206
                if append and not remote.headers.get("Content-Range", "").startswith(
                    f"bytes {size}-"
                ):
                    raise ValueError(f"Incorrect download offset for {name}")
                with stage.open("ab" if append else "wb") as out:
                    while chunk := remote.read(1024 * 1024):
                        out.write(chunk)
        if path.stat().st_size != info["size"]:
            raise ValueError(f"Incomplete model asset {name}")
        sha = hashlib.sha256()
        blob = hashlib.sha1(f"blob {info['size']}\0".encode())
        with path.open("rb") as local:
            while chunk := local.read(1024 * 1024):
                sha.update(chunk)
                blob.update(chunk)
        if "lfs" in info:
            correct = sha.hexdigest() == info["lfs"]["oid"]
        else:
            correct = blob.hexdigest() == info["oid"]
        if not correct:
            raise ValueError(f"Official model digest differs for {name}")
        if path == stage:
            stage.replace(target)
        return name, sha.hexdigest()

    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = dict(pool.map(download, names))
    data = {"repository": repo, "revision": rev, "files": hashes}
    (folder / "source.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Verified {len(hashes)} files from {repo} at {rev}.", flush=True)


if __name__ == "__main__":
    p = ArgumentParser(description=__doc__)
    p.add_argument("model", choices=MODELS)
    p.add_argument("folder", type=Path)
    args = p.parse_args()
    fetch(args.model, args.folder)
