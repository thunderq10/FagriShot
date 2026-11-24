"""Compile artist-authored map labels and exact AV1 sample identities. Requires PyAV and Pillow."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct


FILMS = {
    "NR13_00": "95f9ea2a63d0c36d5bf79daba6a94557b8206a2e52ad6adc8d191d72b5ae67bc",
    "NR20_01": "603729c3dd6bb807335c476fbcea972bf3bf2744f4b99e0828868fe670e39f8d",
    "NR20_03": "681a5314068d1d450e29a23bc783b369fdffff37822448e7a3d4ce35448a295c",
    "NR20_04": "142d86d98676db7710fdf88073b55419f4c5be7452fee0fd0eaacd074b155e84",
    "NR20_08": "98ab4a2bd10b0c46570ba99e1240d6bcb4ad7c4208368cffbda1db1a6cee6d25",
}


def signature(data):
    # Reject most packets by byte length and the first 64 bytes. A match also
    # requires the first 128 bits of SHA-256 over the entire compressed sample.
    h = 14695981039346656037
    for byte in data[:64]:
        h = ((h ^ byte) * 1099511628211) & 0xffffffffffffffff
    return len(data), h, hashlib.sha256(data).digest()[:16]


def source_index(movie_root):
    import av

    files = {p.stem: p for p in movie_root.glob("*.webm")}
    if len(files) != 82 or not FILMS.keys() <= files.keys():
        raise ValueError("Expected the complete set of 82 movies from update 131072.")
    samples = []
    seen = set()
    ambiguous = set()
    sizes = set()
    for name in [*FILMS, *sorted(files.keys() - FILMS.keys())]:
        target = name in FILMS
        path = files[name]
        if target:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != FILMS[name]:
                    raise ValueError(f"Movie source mismatch for {name}.")
        with av.open(str(path)) as container:
            video = container.streams.video[0]
            for packet in container.demux(video):
                if not packet.size or packet.pts is None or (not target and packet.size not in sizes):
                    continue
                sig = signature(bytes(packet))
                if target:
                    if sig in seen:
                        ambiguous.add(sig)
                    seen.add(sig)
                    samples.append((sig, name, round(packet.pts * packet.time_base * 1000)))
                    sizes.add(sig[0])
                elif sig in seen:
                    ambiguous.add(sig)
    return [[n, h, d.hex(), name, ms, (n, h, d) not in ambiguous]
            for (n, h, d), name, ms in samples], len(ambiguous)


def compile_recipe(document, base, samples):
    from PIL import Image
    import zstandard

    films = document["films"]
    if not films or set(films) - FILMS.keys():
        raise ValueError("Unrecognized or empty movie recipe.")
    body = bytearray()
    packed_masks = {}
    compress = zstandard.ZstdCompressor(level=9)
    active = {}
    for film_id, name in enumerate(sorted(films)):
        film = films[name]
        if (film["width"], film["height"]) != (2560, 1440):
            raise ValueError(f"Wrong movie dimensions in {name}.")
        tracks = film["labels"]
        if not 0 < len(tracks) <= 512:
            raise ValueError("Invalid number of tracks in movie.")
        body.extend(struct.pack("<HHI", 2560, 1440, len(tracks)))
        ranges = []
        for track in tracks:
            path = (base / track["mask"]).resolve(strict=True)
            if not path.is_relative_to(base.resolve()) or path.suffix.lower() != ".png":
                raise ValueError("Expected an original PNG mask inside the recipe directory.")
            with Image.open(path) as image:
                if image.mode != "RGBA" or not (0 < image.width <= 2048 and 0 < image.height <= 512):
                    raise ValueError("Expected a bounded RGBA mask.")
                keys = track["keys"]
                if (not 1 <= len(keys) <= 20000 or any(len(k) != 5 or any(type(i) is not int for i in k)
                        or not 0 <= k[0] < 3600000 or not all(-8192 <= i <= 8192 for i in k[1:3])
                        or not all(1 <= i <= 8192 for i in k[3:]) for k in keys)
                        or any(a[0] >= b[0] for a, b in zip(keys, keys[1:]))):
                    raise ValueError("Movie keyframes must be strictly increasing [ms,x,y,width,height].")
                if type(track.get("paper", False)) is not bool:
                    raise ValueError("Invalid paper option.")
                body.extend(struct.pack("<HHBI", image.width, image.height, track.get("paper", False), len(keys)))
                for key in keys:
                    body.extend(struct.pack("<Ihhhh", *key))
                if path not in packed_masks:
                    packed_masks[path] = compress.compress(image.tobytes())
                blob = packed_masks[path]
                body.extend(struct.pack("<I", len(blob)))
                body.extend(blob)
                ranges.append((keys[0][0], keys[-1][0]))
        active[name] = (film_id, ranges)
    encoded = []
    counts = Counter()
    for size, fnv, digest, name, ms, unique in samples:
        if name in active:
            encoded.append(struct.pack("<IQ16sHIB", size, fnv, bytes.fromhex(digest), active[name][0], ms, unique))
            counts[name] += any(a <= ms <= b for a, b in active[name][1])
    if counts.keys() != active.keys():
        raise ValueError("One or more movies have no matching samples.")
    return b"FAGMAP01" + struct.pack("<II", len(films), len(encoded)) + body + b"".join(encoded), dict(counts)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--movies", type=Path, required=True)
    cli.add_argument("--labels", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--index-only", type=Path)
    args = cli.parse_args()
    if not args.index_only and (not args.labels or not args.output):
        cli.error("--labels and --output are required.")
    samples, ambiguous = source_index(args.movies)
    if args.index_only:
        args.index_only.write_text(json.dumps(samples, separators=(",", ":")), encoding="utf-8")
        print(f"{len(samples)} source samples, {ambiguous} signatures require a matched preceding packet.")
        return
    data, counts = compile_recipe(json.loads(args.labels.read_text(encoding="utf-8")), args.labels.parent, samples)
    args.output.write_bytes(data)
    print(json.dumps({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "samples_in_track_windows": counts, "signatures_requiring_context": ambiguous}))


if __name__ == "__main__":
    main()
