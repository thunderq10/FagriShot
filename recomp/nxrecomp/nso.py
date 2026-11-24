# SPDX-License-Identifier: GPL-3.0-or-later
"""Switch NSO reading (LZ4 segments) and NSO->ELF conversion; adapted from smo-recomp tools/extract/nso.py (GPL-3.0-or-later), which in turn is adapted from ssbu-recomp tools/extract (nso2elf.py).
"""
import hashlib
import re
import struct

OFF = 0x1000                                      # file offset of the image in the ELF files nso_to_elf writes
NSO_HEADER_SIZE = 0x100
MAX_NSO_SEGMENT_SIZE = 0x20000000                 # 512 MiB; individual decoder/output allocation ceiling
MAX_NSO_SEGMENT_GAP = 0x04000000                  # 64 MiB; normal NSO segments are page-adjacent
MAX_NSO_IMAGE_SIZE = 0x40000000                   # 1 GiB; matches runtime/include/nxrt/module.h


def nso_layout(d):
    """Validated NSO metadata, before any segment decoding or image allocation."""
    if len(d) < NSO_HEADER_SIZE:
        raise ValueError('NSO header is truncated')
    if d[:4] != b'NSO0':
        raise ValueError('not an NSO executable')
    flags = struct.unpack_from('<I', d, 12)[0]
    bss = struct.unpack_from('<I', d, 0x3c)[0]
    segs, file_extents = [], []
    decoded_size = 0
    previous_end = 0
    for i, (ho, cso) in enumerate(((0x10, 0x60), (0x20, 0x64), (0x30, 0x68))):
        foff, moff, size = struct.unpack_from('<III', d, ho)
        csz = struct.unpack_from('<I', d, cso)[0]
        compressed = bool(flags & (1 << i))
        stored_size = csz if compressed else size
        if size > MAX_NSO_SEGMENT_SIZE:
            raise ValueError(f'NSO segment {i} size 0x{size:x} exceeds the limit')
        if stored_size > MAX_NSO_SEGMENT_SIZE:
            raise ValueError(f'NSO segment {i} stored size 0x{stored_size:x} exceeds the limit')
        if bool(size) != bool(stored_size):
            raise ValueError(f'NSO segment {i} has inconsistent stored and memory sizes')
        if stored_size and foff < NSO_HEADER_SIZE:
            raise ValueError(f'NSO segment {i} starts inside the header')
        if foff > len(d) or stored_size > len(d) - foff:
            raise ValueError(f'NSO segment {i} is outside the file')
        end = moff + size
        if moff < previous_end:
            raise ValueError('NSO segments overlap or are out of order in memory')
        if moff - previous_end > MAX_NSO_SEGMENT_GAP:
            raise ValueError(f'NSO segment {i} gap 0x{moff - previous_end:x} exceeds the limit')
        if end > MAX_NSO_IMAGE_SIZE:
            raise ValueError(f'NSO segment {i} end 0x{end:x} exceeds the image limit')
        decoded_size += size
        if decoded_size > MAX_NSO_IMAGE_SIZE:
            raise ValueError('NSO decoded segments exceed the aggregate limit')
        if stored_size:
            file_extents.append((foff, foff + stored_size))
        segs.append({'file_offset': foff, 'vaddr': moff, 'size': size, 'stored_size': stored_size,
                     'compressed': compressed})
        previous_end = end
    file_extents.sort()
    if any(left[1] > right[0] for left, right in zip(file_extents, file_extents[1:])):
        raise ValueError('NSO segments overlap in the file')
    image_size = segs[-1]['vaddr'] + segs[-1]['size']
    if image_size + bss > MAX_NSO_IMAGE_SIZE:
        raise ValueError(f'NSO image and BSS span 0x{image_size + bss:x} exceeds the limit')
    return {'flags': flags, 'segments': segs, 'bss': bss, 'image_size': image_size,
            'build_id': d[0x40:0x60].hex()}


def nso_info(d):
    """Segments (decompressed), build id and integrity of an NSO."""
    layout = nso_layout(d)
    flags = layout['flags']
    if flags & 0x80 and flags & 7:
        raise RuntimeError('ZBIC-compressed NSO: pass the decompressed NSO instead')
    segs = []
    for i, segment in enumerate(layout['segments']):
        foff, size = segment['file_offset'], segment['size']
        try:
            raw = d[foff:foff + segment['stored_size']]
            if segment['compressed']:
                import lz4.block
                raw = lz4.block.decompress(raw, uncompressed_size=size)
        except MemoryError as exc:
            raise ValueError(f'NSO segment {i} could not be allocated within the size limit') from exc
        if len(raw) != size:
            raise ValueError(f'NSO segment {i} decoded to {len(raw)} bytes, expected {size}')
        ok = hashlib.sha256(raw).digest() == d[0xA0 + i * 0x20:0xC0 + i * 0x20]
        segs.append({'vaddr': segment['vaddr'], 'size': size, 'data': raw, 'sha_ok': ok})
    return {**layout, 'segments': segs}


def nso_memory_image(info):
    """Map validated, decoded NSO segments into one bounded memory image."""
    try:
        mem = bytearray(info['image_size'])
        for segment in info['segments']:
            v, raw = segment['vaddr'], segment['data']
            mem[v:v + len(raw)] = raw
        return bytes(mem)
    except MemoryError as exc:
        raise ValueError('NSO memory image could not be allocated within the size limit') from exc


def nso_to_elf(d):
    """NSO -> ELF64 (sections + program headers, image at file offset 0x1000)."""
    info = nso_info(d)
    (tv, text), (rv, ro), (dv, data) = [(s['vaddr'], s['data']) for s in info['segments']]
    bss = info['bss']
    mem = nso_memory_image(info)
    mod = struct.unpack_from('<I', text, 4)[0]
    assert mem[mod:mod + 4] == b'MOD0'
    dyn_off, bss_s, bss_e = struct.unpack_from('<iii', mem, mod + 4)
    dyn = mod + dyn_off
    tags, n = {}, 0
    while True:
        t, v = struct.unpack_from('<qQ', mem, dyn + n * 16)
        n += 1
        if t == 0:
            break
        tags.setdefault(t, v)
    dynsz = n * 16
    symtab, strtab, strsz = tags[6], tags[5], tags[10]
    nsym = struct.unpack_from('<I', mem, tags[4] + 4)[0] if 4 in tags else (strtab - symtab) // 24
    names = [b'\0']
    secs = [bytes(64)]

    def sec(name, typ, fl, addr, off, size, link=0, info_=0, align=1, ent=0):
        ni = sum(len(x) for x in names)
        names.append(name.encode() + b'\0')
        secs.append(struct.pack('<IIQQQQIIQQ', ni, typ, fl, addr, off, size, link, info_, align, ent))
    sec('.text', 1, 6, tv, OFF + tv, len(text), align=16)
    assert rv <= symtab and symtab + nsym * 24 <= strtab
    sec('.rodata', 1, 2, rv, OFF + rv, symtab - rv, align=16)
    sec('.dynsym', 11, 2, symtab, OFF + symtab, nsym * 24, link=4, info_=1, align=8, ent=24)
    sec('.dynstr', 3, 2, strtab, OFF + strtab, strsz)
    r1 = strtab + strsz
    sec('.rodata.1', 1, 2, r1, OFF + r1, rv + len(ro) - r1)
    sec('.data', 1, 3, dv, OFF + dv, dyn - dv, align=16)
    sec('.dynamic', 6, 3, dyn, OFF + dyn, dynsz, link=4, align=8, ent=16)
    d1 = dyn + dynsz
    sec('.data.1', 1, 3, d1, OFF + d1, dv + len(data) - d1)
    sec('.bss', 8, 3, bss_s, OFF + dv + len(data), bss_e - bss_s, align=16)
    shstr_i = len(secs)
    shstr_off = OFF + len(mem)
    sec('.shstrtab', 3, 0, 0, shstr_off, 0)
    blob = b''.join(names)
    secs[-1] = secs[-1][:32] + struct.pack('<Q', len(blob)) + secs[-1][40:]
    shoff = (shstr_off + len(blob) + 7) & ~7

    def phdr(t, fl, v, fsz, msz, al):
        return struct.pack('<IIQQQQQQ', t, fl, OFF + v, v, v, fsz, msz, al)
    ph = (phdr(1, 5, tv, len(text), len(text), 0x1000) + phdr(1, 4, rv, len(ro), len(ro), 0x1000) +
          phdr(1, 6, dv, len(data), max(len(data), bss_e - dv, len(data) + bss), 0x1000) +
          phdr(2, 6, dyn, dynsz, dynsz, 8))
    eh = b'\x7fELF\x02\x01\x01' + bytes(9) + struct.pack('<HHIQQQIHHHHHH', 3, 183, 1, 0, 0x40, shoff, 0, 0x40, 0x38, 4,
                                                          0x40, len(secs), shstr_i)
    try:
        out = bytearray(eh + ph)
        out += bytes(OFF - len(out))
        out += mem
        out += blob
        out += bytes(shoff - len(out))
        out += b''.join(secs)
        out = bytes(out)
    except MemoryError as exc:
        raise ValueError('converted NSO could not be allocated within the size limit') from exc
    meta = {'text': [tv, len(text)], 'rodata': [rv, len(ro)], 'data': [dv, len(data)], 'bss': [bss_s, bss_e - bss_s],
            'dynamic': dyn, 'dynsym': symtab, 'nsym': nsym, 'dynstr': strtab, 'mod0': mod, 'build_id': info['build_id'],
            'segments_sha_ok': all(s['sha_ok'] for s in info['segments']),
            'dt': {str(k): v for k, v in tags.items()}}
    return out, meta


def sdk_version(module):
    """The NintendoSDK a module was built with, from the version its own middleware tags carry
    ("NintendoSDK_<library>-<major>_<minor>_<micro>-Release"), as "7.3.2.0"; None when it carries none."""
    found = {m.groups() for m in re.finditer(rb'NintendoSDK_[A-Za-z0-9]+-(\d+)_(\d+)_(\d+)-Release', module)}
    if len(found) != 1:
        return None
    return '%s.%s.%s.0' % tuple(x.decode() for x in found.pop())
