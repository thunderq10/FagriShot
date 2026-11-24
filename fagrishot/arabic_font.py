"""Arabic display strings and native bitmap font overlays for the verified 131072 assets."""

import ctypes as C
import hashlib
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import struct


_FONT_DIGESTS = {
    "System": "a5dcf44e6e942878a7a529eecfef5555868fedca4ddf20884afa8006468f0855",
    "System_Small": "78a79c5181262072d22fbae47d10e4fe5c7ee0faf05dc4cae6497868d6896375",
    "System_Large": "cece14738bea0b29e170ac98bbc1e3cd197ef5b86596536530e305858ca4f1a4",
    "Info": "ae43259562b3ac12e442f39bd5de8d987f9478b8daafb9e333757d298b717171",
    "FEFont": "afc4400e2c88ae14f82ba4707b7e60be7b837ffc3b08469c95c905a3eda18021",
    "FEFont_Large": "044eccde7bbc7fbd14ab6c7b22bc53827e067c22429a26d29d3ce3390111f71b",
    "Cyber": "50c243467c4a980aa4bf5ab731b353e361bf353eaf3b0589387e1dd1faf51cdc",
}
FONT_NAMES = tuple(f"__{name}.bffnt.zst" for name in _FONT_DIGESTS)
_ptr = C.c_void_p
_int = C.c_int32
_uint = C.c_uint32


def _bind(dll, name, args, result):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result
    return fn


@lru_cache(maxsize=1)
def _icu():
    lib = C.CDLL("icu.dll", winmode=0x800)
    _bind(lib, "u_shapeArabic", [_ptr, _int, _ptr, _int, _uint, _ptr], _int)
    _bind(lib, "ubidi_open", [], _ptr)
    _bind(lib, "ubidi_setPara", [_ptr, _ptr, _int, C.c_uint8, _ptr, _ptr], None)
    _bind(lib, "ubidi_writeReordered", [_ptr, _ptr, _int, C.c_uint16, _ptr], _int)
    _bind(lib, "ubidi_close", [_ptr], None)
    return lib


def _visual_line(line):
    if not line:
        return line
    lib = _icu()
    raw = line.encode("utf-16-le")
    length = len(raw) // 2
    src = (C.c_uint16 * length).from_buffer_copy(raw)
    capacity = 2 * length + 2
    shaped = (C.c_uint16 * capacity)()
    out = (C.c_uint16 * capacity)()
    status = _int()
    count = lib.u_shapeArabic(src, length, shaped, capacity, 8, C.byref(status))
    bidi = lib.ubidi_open()
    if not bidi:
        raise MemoryError("ICU could not create an Arabic paragraph.")
    try:
        lib.ubidi_setPara(bidi, shaped, count, 1, None, C.byref(status))
        count = lib.ubidi_writeReordered(bidi, out, capacity, 2 | 8, C.byref(status))
        if status.value > 0:
            raise ValueError(f"Arabic text shaping failed (ICU {status.value}).")
        return bytes(out)[:count * 2].decode("utf-16-le")
    finally:
        lib.ubidi_close(bidi)


def visual_text(logical):
    """Shape logical Arabic for a bitmap renderer which advances glyphs to the right."""
    return "\n".join(_visual_line(line) for line in logical.split("\n"))


class _Metrics(C.Structure):
    _fields_ = [("width", _uint), ("height", _uint), ("left", _int), ("ascent", _int),
                ("advance", C.c_short), ("advance_y", C.c_short)]


@contextmanager
def _private_font(face):
    g = C.WinDLL("gdi32.dll", winmode=0x800)
    _bind(g, "AddFontResourceExW", [C.c_wchar_p, _uint, _ptr], _int)
    _bind(g, "RemoveFontResourceExW", [C.c_wchar_p, _uint, _ptr], _int)
    _bind(g, "CreateCompatibleDC", [_ptr], _ptr)
    _bind(g, "DeleteDC", [_ptr], _int)
    _bind(g, "CreateFontW", [_int] * 5 + [_uint] * 8 + [C.c_wchar_p], _ptr)
    _bind(g, "SelectObject", [_ptr, _ptr], _ptr)
    _bind(g, "DeleteObject", [_ptr], _int)
    _bind(g, "GetTextFaceW", [_ptr, _int, C.c_wchar_p], _int)
    _bind(g, "GetGlyphIndicesW", [_ptr, C.c_wchar_p, _int, _ptr, _uint], _uint)
    _bind(g, "GetGlyphOutlineW", [_ptr, _uint, _uint, _ptr, _uint, _ptr, _ptr], _uint)
    path = str(Path(face).resolve(strict=True))
    if not g.AddFontResourceExW(path, 16, None):
        raise ValueError("Windows could not load the bundled DejaVu Sans font.")
    try:
        yield g
    finally:
        g.RemoveFontResourceExW(path, 16, None)


def _raster(g, codes, cw, ch, baseline):
    # Select one em size for the whole alphabet so joins share one baseline and scale.
    dc = g.CreateCompatibleDC(None)
    if not dc:
        raise OSError("Cannot create a Windows font device context.")
    matrix = (C.c_uint32 * 4)(65536, 0, 0, 65536)
    try:
        for size in range(round(ch * .78), 4, -1):
            font = g.CreateFontW(-size, 0, 0, 0, 400, 0, 0, 0, 1, 4, 0, 4, 0, "DejaVu Sans")
            if not font:
                raise OSError("Cannot size DejaVu Sans.")
            previous = g.SelectObject(dc, font)
            try:
                selected = C.create_unicode_buffer(64)
                g.GetTextFaceW(dc, len(selected), selected)
                if selected.value != "DejaVu Sans":
                    raise ValueError("Windows selected an unexpected Arabic typeface.")
                glyphs = {}
                for code in codes:
                    index, metric = C.c_uint16(), _Metrics()
                    g.GetGlyphIndicesW(dc, chr(code), 1, C.byref(index), 1)
                    if index.value == 65535:
                        raise ValueError(f"No Arabic font glyph for U+{code:04X}.")
                    args = dc, index.value, 6 | 128, C.byref(metric)
                    length = g.GetGlyphOutlineW(*args, 0, None, matrix)
                    if length == 0xffffffff:
                        raise OSError(f"Cannot rasterize U+{code:04X}.")
                    top = baseline - metric.ascent
                    if (top < 0 or top + metric.height > ch or metric.width > cw
                            or metric.advance > 127):
                        break
                    pixels = (C.c_ubyte * length)()
                    if length and g.GetGlyphOutlineW(*args, length, pixels, matrix) != length:
                        raise OSError("Incomplete Arabic glyph bitmap.")
                    glyphs[code] = (metric.left, metric.width, metric.advance, top,
                                    metric.height, bytes(pixels))
                if len(glyphs) == len(codes):
                    return glyphs
            finally:
                g.SelectObject(dc, previous)
                g.DeleteObject(font)
        raise ValueError("Arabic glyphs do not fit the supported font cells.")
    finally:
        g.DeleteDC(dc)


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _put(buf, off, n):
    struct.pack_into("<I", buf, off, n)


def font_advances(path):
    """Read the actual character advances from our generated Switch bitmap font."""
    import zstandard

    raw = zstandard.ZstdDecompressor().decompress(Path(path).read_bytes(), max_output_size=32 << 20)
    return _advances(raw)


def _advances(raw):
    finf = struct.unpack_from("<H", raw, 6)[0]
    widths, mapping = struct.unpack_from("<II", raw, finf + 24)
    slots = {}
    while widths:
        first, last, nxt = struct.unpack_from("<HHI", raw, widths)
        for slot in range(first, last + 1):
            slots[slot] = raw[widths + 10 + 3 * (slot - first)]
        widths = nxt
    advances = {}
    while mapping:
        first, last, kind, _, nxt = struct.unpack_from("<IIHHI", raw, mapping)
        offset = mapping + 16
        if kind == 0:
            first_slot = struct.unpack_from("<H", raw, offset)[0]
            pairs = ((code, first_slot + code - first) for code in range(first, last + 1))
        elif kind == 1:
            indices = struct.unpack_from(f"<{last - first + 1}H", raw, offset)
            pairs = zip(range(first, last + 1), indices)
        elif kind == 2:
            count = _u32(raw, offset)
            pairs = (struct.unpack_from("<II", raw, offset + 4 + i * 8) for i in range(count))
        else:
            raise ValueError("Unrecognized character map in the generated Arabic font.")
        for code, slot in pairs:
            if slot in slots:
                # Our appended Unicode mappings precede any embedded duplicates.
                advances.setdefault(chr(code), slots[slot])
        mapping = nxt
    return advances


def hidden_glyph(code):
    """Deterministic blank, equal-advance glyphs used by the Arabic dialogue reveal."""
    if 0xfe70 <= code <= 0xfeff:
        return 0xf070 + code - 0xfe70
    if 0x21 <= code <= 0x7e:
        return 0xf121 + code - 0x21
    if 0x0600 <= code <= 0x06ff:
        return 0xf200 + code - 0x0600
    if 0x2000 <= code <= 0x206f:
        return 0xf300 + code - 0x2000
    if 0x00a1 <= code <= 0x00ff:
        return 0xf7a1 + code - 0x00a1
    if 0xfb50 <= code <= 0xfdff:
        return 0xf450 + code - 0xfb50
    if 0xe000 <= code <= 0xe0ff:
        return 0xf800 + code - 0xe000
    if 0xe100 <= code <= 0xe11f:
        return 0xf100 + code - 0xe100
    if code == 0xe200:
        return 0xf700
    return None


def _bc4(block):
    # Fixed endpoints match the eight possible gray levels encoded by this font.
    bits = 0
    for i, level in enumerate(block):
        index = (1, 7, 6, 5, 4, 3, 2, 0)[(level * 7 + 32) // 64]
        bits |= index << (3 * i)
    return b"\xff\x00" + bits.to_bytes(6, "little")


def _block_offset(x, y, width, block_height):
    # Switch 2 GOB layout. Coordinates index 4x4 BC4 blocks of eight bytes each.
    x *= 8
    group = 512 * block_height
    gob = (y // (8 * block_height) * (width // 32) + x // 64) * group
    gob += ((y // 8) % block_height) * 512
    return (gob + (x & 32) * 8 + (y & 4) * 32 + (x & 16) * 4
            + (y & 2) * 16 + (y & 1) * 16 + (x & 15))


def _patch(raw, codes, g):
    data = bytearray(raw)
    finf = struct.unpack_from("<H", data, 6)[0]
    t, w, m = struct.unpack_from("<III", data, finf + 20)
    cw, ch, layers = data[t:t + 3]
    baseline, _, cols, rows, width, height = struct.unpack_from("<6H", data, t + 8)
    original = _advances(raw)
    glyphs = _raster(g, codes, cw, ch, baseline)
    advances = {ord(c): v for c, v in original.items()}
    advances.update({code: glyph[2] for code, glyph in glyphs.items()})
    for code, advance in advances.items():
        hidden = hidden_glyph(code)
        if hidden is not None:
            if chr(hidden) in original or hidden in glyphs:
                raise ValueError("The Arabic reveal glyph range collides with an existing game glyph.")
            # Width and height are zero. No ink is sampled, while the cursor advances by the original metric.
            glyphs[hidden] = (0, 0, advance, 0, 0, b"")
    codes = sorted(glyphs)
    bntx = _u32(data, t + 20)
    image = data.index(b"BRTI", bntx)
    brtd = data.index(b"BRTD", image)
    relocation = bntx + _u32(data, bntx + 24)
    last = struct.unpack_from("<H", data, w + 2)[0]
    per = cols * rows
    # Leave an entire unused row between the original glyphs and our BC4 cells.
    first = min(layers * per, ((last // cols) + 2) * cols)
    needed = max(layers, (first + len(codes) + per - 1) // per)
    stride = width * height // 2
    delta = (needed - layers) * stride
    if relocation != brtd + 16 + layers * stride or _u32(data, w + 4):
        raise ValueError("Unsupported bitmap font layout.")
    if delta:
        data[relocation:relocation] = bytes(delta)
        # Grow the embedded BNTX image, then relocate the FFNT blocks that follow it.
        for off in (bntx + 24, bntx + 28, image + 80, brtd + 8, relocation + delta + 4,
                    relocation + delta + 52, t - 4):
            _put(data, off, _u32(data, off) + delta)
        data[t + 2] = needed
        _put(data, image + 48, needed)
        _put(data, t + 4, _u32(data, bntx + 28) // needed)
        w, m = w + delta, m + delta
        _put(data, finf + 24, w)
        link = m
        while link:
            nxt = _u32(data, link + 12)
            link_new = nxt + delta if nxt else 0
            _put(data, link + 12, link_new)
            link = link_new

    metrics = bytearray()
    blocks = {}
    data[t + 3] = max(data[t + 3], max(glyph[1] for glyph in glyphs.values()))
    for slot, code in enumerate(codes, first):
        left, gw, advance, top, gh, pixels = glyphs[code]
        metrics.extend(struct.pack("<bBB", left, gw, advance))
        layer, cell = divmod(slot, per)
        x = 1 + cell % cols * (cw + 1)
        y = 1 + cell // cols * (ch + 1) + top
        pitch = (gw + 3) & ~3
        for row in range(gh):
            sy = height - 1 - (y + row)
            row_start = row * pitch
            for col, level in enumerate(pixels[row_start:row_start + gw]):
                if level:
                    sx = x + col
                    key = layer, sx // 4, sy // 4
                    block = blocks.get(key)
                    if block is None:
                        block = blocks[key] = bytearray(16)
                    block[(sy % 4) * 4 + sx % 4] = level
    block_height = 1 << (_u32(data, image + 52) & 7)
    for (layer, x, y), block in blocks.items():
        off = brtd + 16 + layer * stride + _block_offset(x, y, width, block_height)
        data[off:off + 8] = _bc4(block)
    metrics.extend(bytes(-len(metrics) % 4))
    _put(data, w + 4, len(data) + 8)
    data.extend(struct.pack("<4sIHHI", b"CWDH", 16 + len(metrics), first,
                            first + len(codes) - 1, 0) + metrics)
    # A direct map per contiguous Unicode run preserves the original cmap's gaps.
    count = 1
    stop = len(codes)
    while stop:
        start = stop - 1
        while start and codes[start - 1] + 1 == codes[start]:
            start -= 1
        offset = len(data) + 8
        data.extend(struct.pack("<4sIIIHHIH2x", b"CMAP", 28, codes[start], codes[stop - 1],
                                0, 0, m, first + start))
        m, stop, count = offset, start, count + 1
    _put(data, finf + 28, m)
    _put(data, 12, len(data))
    struct.pack_into("<H", data, 16, struct.unpack_from("<H", data, 16)[0] + count)
    return data


def patch_fonts(romfs, overlay_romfs, characters, face):
    """Write extended original game atlases exclusively inside a new private overlay."""
    import zstandard
    from .localization import _ordinary, _separate

    _separate(romfs, overlay_romfs)
    output = Path(overlay_romfs) / "Font"
    output.mkdir(parents=True, exist_ok=False)
    source = _ordinary(_ordinary(Path(romfs), True) / "Font", True)
    codes = sorted({ord(c) for c in characters if "\u0600" <= c <= "\u06ff"
                    or "\ufb50" <= c <= "\ufdff" or "\ufe70" <= c <= "\ufefc"})
    if not codes:
        raise ValueError("The translation contains no Arabic glyphs.")
    with _private_font(face) as g:
        for name, digest in _FONT_DIGESTS.items():
            filename = f"__{name}.bffnt.zst"
            path = _ordinary(source / filename)
            if path.stat().st_size > 32 << 20:
                raise ValueError("Unsupported font resource size.")
            packed = path.read_bytes()
            if not 0 < zstandard.frame_content_size(packed) <= 32 << 20:
                raise ValueError("Unsupported font asset frame.")
            raw = zstandard.ZstdDecompressor().decompress(packed, allow_extra_data=False)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(f"The selected game font differs from the supported build ({name}).")
            patched = _patch(raw, codes, g)
            (output / filename).write_bytes(zstandard.ZstdCompressor(level=5).compress(patched))
