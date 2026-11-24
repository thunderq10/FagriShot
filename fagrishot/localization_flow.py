"""Arrange supported MSBT dynamic text, controller icons and color spans within an Arabic line."""

from collections import Counter, defaultdict
import ctypes as C
from functools import lru_cache
import unicodedata

from .arabic_font import _icu, visual_text
from .localization import LocalizationError, Message, _choice_texts


_COLOR = bytes.fromhex("0e000003000200")
_RESET = _COLOR + b"\xff\x00"
_OBJECT = "\ufffc"
# Names and zero-parameter layouts come from the owned Msg/USen/Msg.msbp
# Localize group, indices 1, 2, 4, 5. Arabic letters do not change case,
# but substituted character names and Latin identifiers may do so.
_CASE_NEXT = frozenset(bytes.fromhex(s) for s in ("0e080001000000", "0e080002000000"))
_LOWER_ALL = bytes.fromhex("0e080004000000")
_LOWER_END = bytes.fromhex("0e080005000000")
# The owned messages use these commands to insert a name, month or day.
_NAMED_VALUES = frozenset(bytes.fromhex(code) for code in (
    "0e020000000000", "0e020001000000", "0e070000000000", "0e070001000000",
    "0e07000400010000", "0e07000500010000", "0e07000400010003", "0e07000500010003"))
# The original notice format inserts an icon from its parameter slot.
_NOTICE_ICON = bytes.fromhex("0e03000000010000")


def _kind(p):
    if p.startswith(b"<") and p.endswith(b">"):
        return "directive"
    if p.startswith(_COLOR) and len(p) == 9:
        return "reset" if p == _RESET else "color"
    if p in _NAMED_VALUES or p == _NOTICE_ICON:
        return "atom"
    if len(p) == 7 and p[:3] == b"\x0e\x01\x00" and p[5:] == b"\0\0":
        return "atom"
    if p.startswith(b"\x0e") and _choice_texts(p) is not None:
        return "atom"
    if not p.startswith((b"\x0e", b"\x0f")):
        try:
            icon = p.decode("utf-8")
        except UnicodeError:
            return "unknown"
        if len(icon) == 1 and 0xe000 <= ord(icon) <= 0xf8ff:
            return "atom"
    return "unknown"


def supports(parts):
    """True for pure variables/icons/choices and balanced color, optionally after leading event directives."""
    seen = has_object = colored = lower_all = False
    for p in parts:
        if isinstance(p, str):
            seen |= bool(p)
            continue
        if isinstance(p, dict):
            p = bytes.fromhex(p["token"])
        if p in _CASE_NEXT or p == _LOWER_ALL:
            seen = has_object = True
            lower_all |= p == _LOWER_ALL
            continue
        if p == _LOWER_END and lower_all:
            lower_all = False
            continue
        kind = _kind(p)
        if kind == "directive" and not seen:
            continue
        if kind == "unknown" or kind == "directive" or (kind == "reset" and not colored):
            return False
        if kind in ("reset", "color"):
            colored = kind == "color"
        seen = has_object = True
    # UNCAPAll is also used through end of message, without UNCAPAll_End.
    return has_object and not colored


@lru_cache(maxsize=1)
def _mapped_icu():
    lib = _icu()
    lib.ubidi_getVisualMap.argtypes = [C.c_void_p, C.POINTER(C.c_int32), C.POINTER(C.c_int32)]
    lib.ubidi_getVisualMap.restype = None
    return lib


def _mapped_line(chars):
    """Shape the whole logical line and map every visual UTF-16 glyph back to its original atom/style."""
    if not chars:
        return []
    text = "".join(x[0] for x in chars)
    if any(ord(c) > 65535 or unicodedata.bidirectional(c) in
           ("LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI") for c in text):
        raise LocalizationError("This inline Arabic line needs an explicit layout.")
    n = len(chars)
    src = (C.c_uint16 * n).from_buffer_copy(text.encode("utf-16-le"))
    shaped = (C.c_uint16 * n)()
    status = C.c_int32()
    icu = _mapped_icu()
    # Use the same variable-length shaping as visual_text. Fixed-length NEAR
    # padding can move to the beginning when a ligature ends a logical line,
    # shifting every command and style by a cell on Windows ICU.
    actual = icu.u_shapeArabic(src, n, shaped, n, 8, C.byref(status))
    if status.value > 0 or not 0 < actual <= n:
        raise LocalizationError("Unable to map the Arabic glyphs in this line.")
    consumed = set()
    logical = []
    i = 0
    for glyph in shaped[:actual]:
        while i in consumed:
            i += 1
        if i >= n:
            raise LocalizationError("Unexpected Arabic glyph mapping.")
        logical.append(i)
        if 0xfef5 <= glyph <= 0xfefc and chars[i][0] == "\u0644":
            # Lam and one of four alefs form one glyph. Intervening vowel
            # marks still have their own cell and retain their original style.
            j = i + 1
            while j < n and unicodedata.category(chars[j][0]).startswith("M"):
                j += 1
            alef = "\u0622\u0623\u0625\u0627"[(glyph - 0xfef5) // 2]
            if j >= n or chars[j][0] != alef:
                raise LocalizationError("Unexpected Arabic lam-alef mapping.")
            consumed.add(j)
        i += 1
    if len(logical) + len(consumed) != n:
        raise LocalizationError("Unexpected Arabic glyph count.")
    n = actual
    reordered = (C.c_uint16 * n)()
    mapping = (C.c_int32 * n)()
    bidi = icu.ubidi_open()
    if not bidi:
        raise MemoryError("Could not allocate an ICU paragraph.")
    try:
        icu.ubidi_setPara(bidi, shaped, n, 1, None, C.byref(status))
        count = icu.ubidi_writeReordered(bidi, reordered, n, 2, C.byref(status))
        icu.ubidi_getVisualMap(bidi, mapping, C.byref(status))
        if status.value > 0 or count != n or sorted(mapping) != list(range(n)):
            raise LocalizationError("Unable to map the RTL order of this line.")
    finally:
        icu.ubidi_close(bidi)
    result = []
    for glyph, logical_pos in zip(reordered, mapping):
        _, style, atom = chars[logical[logical_pos]]
        result.append((style, atom if atom is not None else chr(glyph).encode("utf-8")))
    return result


def _case_boundaries(visual, scopes):
    """Place each original case-state command once and check all case-sensitive glyphs and atoms."""
    events = defaultdict(list)
    for index, closed in enumerate(scopes):
        members = [i for i, (meta, _) in enumerate(visual) if meta[1] == index]
        first = members[0] if members else len(visual)
        last = members[-1] + 1 if members else first
        if not closed:
            # A lower-to-end region may move physically ahead of an unrelated
            # case-sensitive name. Its case-invariant Arabic cells can sit
            # outside the command. A different cased word cannot.
            for i in range(first, len(visual)):
                meta = visual[i][0]
                if meta[3] and meta[1] is None:
                    first = i + 1
        events[first].append(_LOWER_ALL)
        if closed:
            events[last].append(_LOWER_END)
    lower = False
    for i, (meta, _) in enumerate(visual):
        for command in events.get(i, ()):
            lower = command == _LOWER_ALL
        if meta[3] and lower != (meta[1] is not None):
            raise LocalizationError("This RTL line would apply a capitalization region to a different name.")
    return events


def _visual_parts(parts):
    result = bytearray()
    start = 0
    while start < len(parts) and isinstance(parts[start], bytes) and _kind(parts[start]) == "directive":
        result.extend(parts[start])
        start += 1

    style = active = scope = None
    prefix = b""
    scopes = []
    lines = [[]]
    for p in parts[start:]:
        if p in _CASE_NEXT:
            prefix += p
        elif p == _LOWER_ALL:
            scope = len(scopes)
            scopes.append(False)
        elif p == _LOWER_END:
            scopes[scope] = True
            scope = None
        elif isinstance(p, str):
            for c in p:
                if c == "\n":
                    lines.append([])
                    continue
                if c == _OBJECT:
                    raise LocalizationError("Unexpected inline object character in translated text.")
                # Keep a one-shot case modifier immediately before its original
                # logical character even after the entire line changes direction.
                # Some localized labels put the intended word in parentheses.
                # Keep the case modifier on its letter, not on a bracket whose
                # mirrored visual neighbor could be a different substitution.
                takes_prefix = c.isalnum()
                lines[-1].append((c, (style, scope, prefix if takes_prefix else b"", c.lower() != c.upper()), None))
                if takes_prefix:
                    prefix = b""
        elif p == _RESET:
            style = None
        elif _kind(p) == "color":
            style = p
        else:
            lines[-1].append((_OBJECT, (style, scope, prefix, True), p))
            prefix = b""

    visual = []
    for index, line in enumerate(lines):
        if index:
            # Preserve palette state across explicit line breaks.
            previous = visual[-1][0][0] if visual else None
            visual.append(((previous, None, b"", False), b"\n"))
        visual.extend(_mapped_line(line))
    boundaries = _case_boundaries(visual, scopes)
    for i, ((style, _, modifier, _), data) in enumerate(visual):
        if style != active:
            if active is not None:
                result.extend(_RESET)
            if style is not None:
                result.extend(style)
            active = style
        for command in boundaries.get(i, ()):
            result.extend(command)
        result.extend(modifier)
        result.extend(data)
    for command in boundaries.get(len(visual), ()):
        result.extend(command)
    result.extend(prefix)
    if active is not None:
        result.extend(_RESET)
    return bytes(result)


def render(entry, texts, choices=None):
    if not supports(entry.parts):
        raise LocalizationError("Unsupported inline control requires an authored Arabic layout.")
    options = None
    if choices is not None:
        options = {i: [visual_text(s) for s in pair] for i, pair in choices.items()}
    logical = Message.parse(entry.replaced(texts, options))
    visual = _visual_parts(logical.parts) + entry.ending
    rebuilt = Message.parse(visual)
    # Commands with behavior may never be duplicated, dropped or fabricated.
    # Reapplying a palette color is idempotent when a styled run crosses lines;
    # never introduce a style absent from the translated source.
    src_commands = [p for p in logical.parts if isinstance(p, bytes)]
    dst_commands = [p for p in rebuilt.parts if isinstance(p, bytes)]
    other = lambda commands: Counter(p for p in commands if _kind(p) not in ("color", "reset"))
    if other(src_commands) != other(dst_commands) or not set(dst_commands) <= set(src_commands):
        raise LocalizationError("Arabic layout changed an embedded MSBT command.")
    return visual
