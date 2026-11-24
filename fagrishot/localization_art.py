"""Apply Arabic lettering to the player's fingerprinted tutorial illustrations."""

import base64
import hashlib
import json
from pathlib import Path
import re
import struct

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


def _encode_ink(background, coverage, foreground=_GOLD):
    """Encode a 4x4, one-subset BC7 mode-6 block with 4-bit ink coverage."""
    indices = [_INK_INDEX[value] for value in coverage]
    ends = (background, foreground)
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


def _palette_color(block):
    """Representative background for feathering a nameplate into the player's BC7 texture."""
    bits = int.from_bytes(block, "little")
    mode = (bits & -bits).bit_length() - 1
    if not 0 <= mode < 8:
        return (0, 0, 0, 0)
    if mode == 6:
        return _mode6_color(block)
    subsets = (3, 2, 3, 2, 1, 1, 1, 2)[mode]
    component_bits = (4, 6, 5, 7, 5, 7, 7, 5)[mode]
    skip = (4, 6, 6, 6, 3, 2, 0, 6)[mode]
    if mode in (4, 5):
        rotation = (bits >> (mode + 1)) & 3
    else:
        rotation = 0
    bits >>= mode + 1 + skip
    channels = []
    for channel in range(4):
        precision = component_bits if channel < 3 else (6 if mode == 4 else 8 if mode == 5 else 5 if mode == 7 else 0)
        if not precision:
            channels.append(255)
            continue
        maximum = (1 << precision) - 1
        value = 0
        for _ in range(2*subsets):
            value += bits & maximum
            bits >>= precision
        channels.append(round(value * 255 / (2*subsets*maximum)))
    if rotation:
        channels[rotation-1], channels[3] = channels[3], channels[rotation-1]
    return tuple(channels)


_INK_INDEX = tuple(min(range(16), key=lambda i: abs(_WEIGHTS[i] - value)) for value in range(65))
TUTORIAL_RECIPE = Path("fagrishot/arabic_art/tutorials.json.zst")


def _lettering(root):
    import zstandard

    path = Path(root) / TUTORIAL_RECIPE
    if not path.exists():
        return {}
    payload = zstandard.ZstdDecompressor().decompress(_ordinary(path).read_bytes(), max_output_size=48 << 20)
    entries = json.loads(payload)
    for name, entry in entries.items():
        if (not re.fullmatch(r"TUT_[A-Za-z0-9_]+", name)
                or name + ".bntx.zst" == ASSET.name
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or not entry["captions"]):
            raise LocalizationError("Invalid Arabic tutorial lettering recipe.")
    return entries


def tutorial_art_outputs(root):
    return (ASSET.as_posix(), *((ASSET.parent / (name + ".bntx.zst")).as_posix()
                                for name in sorted(_lettering(root))))


def _paint_captions(raw, captions):
    result = bytearray(raw)
    for caption in captions:
        x, y, width, height, bg, fg, encoded, *underlay = caption
        if (min(x, y) < 0 or min(width, height) < 1 or x + width > 200 or y + height > 112
                or max(bg, fg) > 0xffffff or min(bg, fg) < 0):
            raise LocalizationError("Arabic tutorial lettering is outside the image bounds.")
        coverage = base64.b64decode(encoded, validate=True)
        if len(coverage) != width * height * 16 or max(coverage) > 64:
            raise LocalizationError("Invalid Arabic tutorial lettering mask.")
        background = (*bg.to_bytes(3, "big"), 255)
        foreground = (*fg.to_bytes(3, "big"), 255)
        samples = ()
        plate = underlay == ["plate"]
        if underlay and not plate:
            if len(underlay) != 1:
                raise LocalizationError("Invalid tutorial lettering underlay.")
            packed = base64.b64decode(underlay[0], validate=True)
            if len(packed) != width * height * 2:
                raise LocalizationError("Invalid tutorial lettering sample count.")
            samples = struct.unpack(f"<{width*height}H", packed)
            if any(slot >= 200*112 for slot in samples):
                raise LocalizationError("Invalid tutorial lettering sample location.")
        for by in range(height):
            for bx in range(width):
                i = (by * width + bx) * 16
                offset = _offset(x + bx, y + by)
                if offset + 16 > len(raw):
                    raise LocalizationError("The tutorial texture is truncated.")
                ink = coverage[i:i+16]
                fill = background
                if plate:
                    feather = min(1.0, (bx + .5) / 2, (width - bx - .5) / 2,
                                  (by + .5) / 1.5, (height - by - .5) / 1.5)
                    if feather < 1:
                        original = _palette_color(raw[offset:offset+16])
                        fill = tuple(round(a*(1-feather) + b*feather) for a,b in zip(original, background))
                if samples:
                    sy, sx = divmod(samples[by*width+bx], 200)
                    src = _offset(sx, sy)
                    block = raw[src:src+16]
                    if not any(ink):
                        result[offset:offset+16] = block
                        continue
                    if block[0] & 127 == 64:
                        fill = _mode6_color(block)
                result[offset:offset + 16] = _encode_ink(fill, ink, foreground)
    return bytes(result)


def patch_tutorials(romfs, destination, root):
    import zstandard
    from .arabic import assets

    patch_autosave_art(romfs, destination, assets(Path(root))[1])
    src, dst = Path(romfs), Path(destination)
    # patch_autosave_art has already verified and created these same directories.
    for part in ASSET.parts[:-1]:
        src = _ordinary(src / part, True)
        dst = _ordinary(dst / part, True)
    decoder = zstandard.ZstdDecompressor()
    encoder = zstandard.ZstdCompressor(level=8)
    for name, entry in _lettering(root).items():
        filename = name + ".bntx.zst"
        data = _ordinary(src / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise LocalizationError(f"Unsupported tutorial illustration fingerprint for {name}.")
        raw = decoder.decompress(data, max_output_size=4 << 20)
        result = _paint_captions(raw, entry["captions"])
        with (dst / filename).open("xb") as stream:
            stream.write(encoder.compress(result))


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
