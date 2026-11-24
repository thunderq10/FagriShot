"""Install the Arabic title in the US-English logo slot of the owned update."""

import hashlib
from pathlib import Path
import struct

from .localization import LocalizationError, _ordinary, _separate


LOGO_RECIPE = Path("fagrishot/arabic_art/title.bc3.zst")
_ARCHIVE = Path("Layout/Textures/Common.arc.zst")
_SOURCE = "c4665d2c8238cef63a9fcc37c2c1daccdea002e27770cd90f3f4e52e9cad6ef8"


def logo_outputs(root):
    return (_ARCHIVE.as_posix(),) if (Path(root) / LOGO_RECIPE).is_file() else ()


def _block(x, y):
    # BC3 occupies 16 bytes per 4x4 block. The title surface has 88 GOBs per row.
    return ((y // 128 * 88 + x // 4) * 8192 + (y // 8 % 16) * 512
            + (x & 2) * 128 + (y & 4) * 32 + (x & 1) * 64 + (y & 3) * 16)


def _replace(raw, pixels):
    # Verified SARC contains a single timg/__Combined.bntx member.
    bntx = struct.unpack_from("<I", raw, 12)[0]
    header = bntx + 177008
    if (raw[:4] != b"SARC" or raw[bntx:bntx + 4] != b"BNTX" or raw[header:header + 4] != b"BRTI"
            or struct.unpack_from("<I", raw, header + 28)[0] != 0x1c06
            or struct.unpack_from("<II", raw, header + 36) != (1400, 560)):
        raise LocalizationError("Unexpected layout for the English title logo.")
    name = bntx + struct.unpack_from("<Q", raw, header + 96)[0] + 2
    if not raw.startswith(b"Title_TitleLogo_USen^q\0", name):
        raise LocalizationError("The English title-logo slot could not be verified.")
    mip_table = bntx + struct.unpack_from("<Q", raw, header + 112)[0]
    start = bntx + struct.unpack_from("<Q", raw, mip_table)[0]
    if len(pixels) != 350 * 140 * 16 or start + _block(349, 139) + 16 > len(raw):
        raise LocalizationError("Unexpected Arabic title-logo dimensions.")
    result = bytearray(raw)
    for y in range(140):
        for x in range(350):
            src = (y * 350 + x) * 16
            dest = start + _block(x, y)
            result[dest:dest + 16] = pixels[src:src + 16]
    # The trademark remains part of the player's own title texture.
    for dy in range(5):
        for dx in range(9):
            src = start + _block(304 + dx, 29 + dy)
            dest = start + _block(331 + dx, 22 + dy)
            result[dest:dest + 16] = raw[src:src + 16]
    return result


def patch_logos(romfs, destination, root):
    if not logo_outputs(root):
        return
    import zstandard

    src, dst = Path(romfs), Path(destination)
    _separate(src, dst)
    _ordinary(src, True)
    dst.mkdir(parents=True, exist_ok=True)
    _ordinary(dst, True)
    for part in _ARCHIVE.parts[:-1]:
        src = _ordinary(src / part, True)
        dst = dst / part
        dst.mkdir(exist_ok=True)
        _ordinary(dst, True)
    source = _ordinary(src / _ARCHIVE.name).read_bytes()
    if hashlib.sha256(source).hexdigest() != _SOURCE:
        raise LocalizationError("Unsupported source fingerprint for the title logo.")
    decode = zstandard.ZstdDecompressor()
    raw = decode.decompress(source, max_output_size=40 << 20)
    pixels = decode.decompress(_ordinary(Path(root) / LOGO_RECIPE).read_bytes(), max_output_size=1 << 20)
    localized = zstandard.ZstdCompressor(level=9).compress(_replace(raw, pixels))
    with (dst / _ARCHIVE.name).open("xb") as stream:
        stream.write(localized)
