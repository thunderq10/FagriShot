"""Relabel the owned update's Autosave tutorial illustration in a private overlay."""

import hashlib
from pathlib import Path

from .arabic_font import _private_font, _raster, visual_text
from .localization import LocalizationError, _ordinary, _separate


ASSET = Path("Layout/Textures/Tutorial/USen/TUT_Hub_AutoSave_001.bntx.zst")
_SHA256 = "1b5e004dcecd845af164b8532d7aa274e06d319b63fb175a2e5bcbf93552ecb7"
_GOLD = (212, 182, 99, 255)
_WEIGHTS = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)


def _offset(bx, by):
    # The verified Ounc BNTX is one 800x450 sRGB BC7 level, 50 GOBs
    # across and 16 high per group. T239 interleaves y0, y1, x4, y2, x5.
    x = bx * 16
    gob = (by // 128 * 50 + x // 64) * 8192 + (by // 8 % 16) * 512
    return 4096 + gob + (x & 32) * 8 + (by & 4) * 32 + (x & 16) * 4 + (by & 3) * 16


def _mode6_color(block):
    """Mean colour of a clean mode-6 block sampled near the new lettering."""
    bits = int.from_bytes(block, "little") >> 7
    endpoints = []
    for _ in range(8):
        endpoints.append((bits & 127) * 2)
        bits >>= 7
    for i in range(8):
        endpoints[i] |= (bits >> (i % 2)) & 1
    bits >>= 2
    weight = _WEIGHTS[bits & 7]
    bits >>= 3
    for _ in range(15):
        weight += _WEIGHTS[bits & 15]
        bits >>= 4
    return tuple((endpoints[c] * (1024 - weight) + endpoints[c + 1] * weight + 512) // 1024
                 for c in range(0, 8, 2))


def _encode_ink(background, coverage):
    """Encode a 4x4, one-subset BC7 mode-6 block with 4-bit ink coverage."""
    indices = [min(range(16), key=lambda i: abs(_WEIGHTS[i] - value)) for value in coverage]
    ends = (background, _GOLD)
    if indices[0] >= 8:  # The anchor index has only three bits.
        ends = tuple(reversed(ends))
        indices = [15 - i for i in indices]
    bits = 1 << 6
    shift = 7
    for channel in range(4):
        for end in ends:
            bits |= (end[channel] >> 1) << shift
            shift += 7
    for end in ends:
        bits |= (end[3] & 1) << shift
        shift += 1
    for i, value in enumerate(indices):
        bits |= value << shift
        shift += 3 if i == 0 else 4
    return bits.to_bytes(16, "little")


def _caption(face):
    text = visual_text("يجري الحفظ...")
    with _private_font(face) as gdi:
        glyphs = _raster(gdi, set(map(ord, text)), 48, 43, 33)
    width = sum(glyphs[ord(c)][2] for c in text)
    cursor = 478 - width // 2
    ink = {}
    for c in text:
        left, width, advance, top, height, pixels = glyphs[ord(c)]
        stride = (width + 3) & ~3
        for y in range(height):
            for x, value in enumerate(pixels[stride * y:stride * y + width]):
                if value:
                    pos = cursor + left + x, 302 + top + y
                    ink[pos] = max(ink.get(pos, 0), value)
        cursor += advance
    return ink


def _paint(raw, ink):
    out = bytearray(raw)
    left, right, top, bottom = 102, 137, 77, 87
    # Estimate the dark brushwork behind the original letters using nearby
    # unlettered blocks from the user's own image. Keep the orbit and glow.
    samples = []
    for by in range(72, 93):
        for bx in range(90, 150):
            if left <= bx < right and top <= by < bottom:
                continue
            pos = _offset(bx, by)
            block = out[pos:pos + 16]
            if block[0] & 127 == 64:
                color = _mode6_color(block)
                if max(color[:3]) < 49 and color[3] >= 250:
                    samples.append((bx, by, color))
    if not samples or not ink:
        raise LocalizationError("Cannot paint the expected Autosave label.")
    touched = {(bx, by) for bx in range(left, right) for by in range(top, bottom)}
    touched.update((x // 4, y // 4) for x, y in ink)
    for bx, by in touched:
        if not (90 <= bx < 150 and 70 <= by < 92):
            raise LocalizationError("Arabic lettering exceeds its tutorial caption area.")
        neighbors = sorted((((x - bx) ** 2 + (y - by) ** 2, c) for x, y, c in samples),
                           key=lambda data: data[0])[:10]
        if neighbors[0][0] == 0:
            color = neighbors[0][1]
        else:
            weights = [1 / (distance ** 1.5) for distance, _ in neighbors]
            total = sum(weights)
            color = tuple(round(sum(w * c[ch] for w, (_, c) in zip(weights, neighbors)) / total)
                          for ch in range(4))
        coverage = [ink.get((4 * bx + x, 4 * by + y), 0) for y in range(4) for x in range(4)]
        pos = _offset(bx, by)
        out[pos:pos + 16] = _encode_ink(color, coverage)
    return bytes(out)


def patch_autosave_art(romfs, output_romfs, font_path):
    """Create only the supported tutorial image. No proprietary pixels ship with the tool."""
    source, dest = Path(romfs), Path(output_romfs)
    _separate(source, dest)
    _ordinary(source, True)
    for part in ASSET.parts[:-1]:
        source = _ordinary(source / part, True)
    data = _ordinary(source / ASSET.name).read_bytes()
    if hashlib.sha256(data).hexdigest() != _SHA256:
        raise LocalizationError("Unsupported source fingerprint for the Autosave tutorial illustration.")
    import zstandard

    raw = zstandard.ZstdDecompressor().decompress(data)
    result = zstandard.ZstdCompressor(level=9).compress(_paint(raw, _caption(font_path)))
    dest.mkdir(parents=True, exist_ok=True)
    _ordinary(dest, True)
    for part in ASSET.parts[:-1]:
        dest = dest / part
        dest.mkdir(exist_ok=True)
        _ordinary(dest, True)
    with (dest / ASSET.name).open("xb") as stream:
        stream.write(result)
