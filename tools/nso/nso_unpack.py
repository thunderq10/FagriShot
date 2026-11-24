# SPDX-License-Identifier: GPL-3.0-or-later
"""Write an uncompressed copy of a Switch / Switch 2 NSO.

    python tools/nso/nso_unpack.py <exefs>/main <out>/main

The console's ExeFS `main` of some Switch 2 games is ZBIC-compressed (NSO flag bit 7: a zstd variant with its own entropy-table coding).
Neither the lifter (recomp/nxrecomp/nso.py) nor the runtime's NSO loader reads ZBIC, so setup writes a plain copy and both read that.
ZBIC needs the `zbic` package (https://github.com/kinnay/zbic, `pip install zbic`); LZ4 segments need the `lz4` package.

The output is the same NSO with every segment stored uncompressed: the header is kept (flags keep only the hash bits 3-5), the segments
follow the 0x100-byte header back to back, and each segment's stored size equals its memory size. The segment SHA-256 hashes in the
header are checked against the decompressed data when the flags say they are present. Nothing in the input is changed.
"""
import argparse
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _recomp in (os.path.join(HERE, 'recomp'), os.path.join(HERE, '..', '..', 'recomp')):
    if os.path.isdir(os.path.join(_recomp, 'nxrecomp')):
        sys.path.insert(0, os.path.abspath(_recomp))
        break
else:
    raise ImportError('nso_unpack: cannot find the bundled nxrecomp package')

from nxrecomp.nso import NSO_HEADER_SIZE, nso_layout  # noqa: E402

SEG_HDR = (0x10, 0x20, 0x30)        # (file offset, memory offset, size) of text, ro, data
SEG_FILE_SIZE = (0x60, 0x64, 0x68)  # stored size of each segment
SEG_HASH = 0xA0                     # 3 x SHA-256
FLAG_ZBIC = 0x80
ZSTD_FRAME_HEADER_SIZE_MAX = 18


def _decompress(blob, size, zbic_flag):
    try:
        if zbic_flag:
            try:
                import zbic
                import zstandard
            except ImportError:
                sys.exit('nso_unpack: this NSO is ZBIC-compressed; install the zbic and zstandard packages')
            if blob[:4] != b'ZBIC':
                raise ValueError('ZBIC segment has an invalid frame signature')
            # zbic 1.0.1.post1 uses Zstandard's frame header with a different magic.
            # Inspect only the header; the ZBIC decoder receives the original frame.
            header = zstandard.FRAME_HEADER + blob[4:ZSTD_FRAME_HEADER_SIZE_MAX]
            try:
                frame_size = zstandard.frame_content_size(header)
            except zstandard.ZstdError as exc:
                raise ValueError('ZBIC segment has an invalid frame header') from exc
            if frame_size != size:
                raise ValueError(f'ZBIC segment frame declares {frame_size} bytes, the NSO header says {size}')
            out = zbic.decompress(blob)
        else:
            try:
                import lz4.block
            except ImportError:
                sys.exit('nso_unpack: this NSO is LZ4-compressed; install the lz4 package (pip install lz4)')
            out = lz4.block.decompress(blob, uncompressed_size=size)
    except MemoryError as exc:
        raise ValueError('segment could not be allocated within the NSO size limit') from exc
    if len(out) != size:
        raise ValueError(f'segment decompressed to {len(out)} bytes, the header says {size}')
    return out


def unpack(data):
    layout = nso_layout(data)
    hdr = bytearray(data[:NSO_HEADER_SIZE])
    flags = layout['flags']
    segs = []
    for i, segment in enumerate(layout['segments']):
        foff, size = segment['file_offset'], segment['size']
        try:
            blob = data[foff:foff + segment['stored_size']]
            seg = _decompress(blob, size, flags & FLAG_ZBIC) if segment['compressed'] else bytes(blob)
        except MemoryError as exc:
            raise ValueError(f'segment {i} could not be allocated within the NSO size limit') from exc
        if len(seg) != size:
            raise ValueError(f'segment {i}: {len(seg)} bytes stored, {size} expected')
        if flags & (8 << i):
            want = data[SEG_HASH + 32 * i:SEG_HASH + 32 * (i + 1)]
            if hashlib.sha256(seg).digest() != want:
                raise ValueError(f'segment {i}: SHA-256 does not match the header (a damaged or different dump)')
        segs.append(seg)
    off = NSO_HEADER_SIZE
    for i, seg in enumerate(segs):
        _, moff, size = struct.unpack_from('<III', hdr, SEG_HDR[i])
        struct.pack_into('<III', hdr, SEG_HDR[i], off, moff, size)
        struct.pack_into('<I', hdr, SEG_FILE_SIZE[i], size)
        off += size
    struct.pack_into('<I', hdr, 0x0C, flags & 0x38)
    try:
        return b''.join([bytes(hdr), *segs]), flags
    except MemoryError as exc:
        raise ValueError('uncompressed NSO could not be allocated within the size limit') from exc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('input', help='the NSO to read (for example <dump>/exefs/main)')
    ap.add_argument('output', help='where to write the uncompressed NSO')
    a = ap.parse_args()
    with open(a.input, 'rb') as f:
        data = f.read()
    out, flags = unpack(data)
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    tmp = a.output + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(out)
    os.replace(tmp, a.output)
    kind = 'ZBIC' if flags & FLAG_ZBIC else ('LZ4' if flags & 7 else 'uncompressed')
    print(f'{a.input}: {kind}, {len(data)} -> {len(out)} bytes, sha256 {hashlib.sha256(out).hexdigest()}')


if __name__ == '__main__':
    main()
