# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real NSO preparation CLI and reader using original segment data."""

import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import lz4.block
import zbic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "recomp"))
from nxrecomp.nso import nso_info  # noqa: E402

SEGMENTS = [b"FagriShot synthetic segment" * 85, bytes(range(256)) * 5, bytes(range(67)) * 11]
MODULE_ID = hashlib.sha256(b"FagriShot NSO test only").digest()


def make_fixture(codec):
    # Three original payloads exercise the three segment slots. The middle
    # segment stays plain so the per-segment compression flags differ.
    header = bytearray(256)
    header[:4] = b"NSO0"
    header[0x40:0x60] = MODULE_ID
    flags = 0x38  # Integrity checks on every segment.
    body = bytearray()
    for index, plain in enumerate(SEGMENTS):
        data = plain
        if index != 1 and codec != "plain":
            data = zbic.compress(plain) if codec == "zbic" else lz4.block.compress(plain, store_size=False)
            flags |= 1 << index
        struct.pack_into("<III", header, 0x10 + index * 16, len(header) + len(body), index * 4096, len(plain))
        struct.pack_into("<I", header, 0x60 + index * 4, len(data))
        offset = 0xA0 + index * 32
        header[offset:offset + 32] = hashlib.sha256(plain).digest()
        body.extend(data)
    struct.pack_into("<I", header, 12, flags | (0x80 if codec == "zbic" else 0))
    return bytes(header + body)


def main():
    work = ROOT / "work/cpu"
    work.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="nso-", dir=work))
    plain = make_fixture("plain")
    with (run / "verification.log").open("w", encoding="utf-8") as log:
        for codec in ("plain", "lz4", "zbic", "bad-digest"):
            blob = bytearray(make_fixture("zbic" if codec == "bad-digest" else codec))
            source, prepared = run / f"{codec}.nso", run / f"{codec}.prepared"
            if codec == "bad-digest":
                blob[0xA0 + 32] ^= 1
                prepared.write_bytes(b"previous output")
            source.write_bytes(blob)
            command = [sys.executable, "-I", str(ROOT / "tools/nso/nso_unpack.py"), str(source), str(prepared)]
            result = subprocess.run(command, text=True, capture_output=True, timeout=30)
            log.write(subprocess.list2cmdline(command) + "\n" + result.stdout + result.stderr)
            if source.read_bytes() != blob:
                raise RuntimeError(f"The {codec} input was modified.")
            if codec == "bad-digest":
                if result.returncode == 0 or "segment 1: SHA-256" not in result.stderr:
                    raise RuntimeError("The intentionally damaged digest did not produce the expected rejection.")
                if prepared.read_bytes() != b"previous output":
                    raise RuntimeError("Rejected NSO replaced the previous destination.")
            else:
                if result.returncode != 0 or prepared.read_bytes() != plain:
                    raise RuntimeError(f"{codec} unpack failed to reproduce the original plain NSO: {result.stderr}")
                info = nso_info(prepared.read_bytes())
                if info["build_id"] != MODULE_ID.hex() or len(info["segments"]) != len(SEGMENTS):
                    raise RuntimeError("Module ID or segment count changed.")
                for index, seg in enumerate(info["segments"]):
                    if seg["data"] != SEGMENTS[index] or seg["vaddr"] != index * 4096 or not seg["sha_ok"]:
                        raise RuntimeError(f"{codec} segment {index} differs after preparation and reload.")
            print(f"PASS {codec}")
            log.write(f"PASS {codec}\n")
    print(f"NSO fixture evidence: {run}")


if __name__ == "__main__":
    main()
