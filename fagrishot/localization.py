"""Read UTF-8 MSBT v3 resources and build private US-English RomFS overrides."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile


MAX_RESOURCE = 32 * 1024 * 1024
_RESOURCE_NAME = re.compile(r"[A-Za-z0-9_-]+\.msbt\.zst")
# In this title, ASCII event directives and private-use button icons are data
# embedded in ordinary text. Keep them just as immutable as binary MSBT tags.
_TEXT_TOKEN = re.compile(r"(<[A-Z][A-Z0-9_]*>|[\ue000-\uf8ff])")
_CONTROL = re.compile(rb"[\x00\x0e\x0f]")
_CHOICE_TYPES = {(4, 0), (4, 1), (5, 0), (8, 0)}


class LocalizationError(ValueError):
    """An unsupported resource, translation, or destination."""


def _u32(data, offset):
    if offset + 4 > len(data):
        raise LocalizationError("Truncated MSBT integer.")
    return struct.unpack_from("<I", data, offset)[0]


def _ordinary(path, directory=False):
    info = path.lstat()
    if (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise LocalizationError(f"Expected an ordinary {'directory' if directory else 'file'} at {path.name}.")
    return path


def _choice_texts(token):
    if not token.startswith(b"\x0e") or struct.unpack_from("<HH", token, 1) not in _CHOICE_TYPES:
        return None
    pos = 7
    choices = []
    while pos + 2 <= len(token):
        size = int.from_bytes(token[pos:pos + 2], "little")
        start, pos = pos + 2, pos + 2 + size
        if pos > len(token):
            break
        choices.append(token[start:pos].decode("utf-8"))
    if pos != len(token) or len(choices) != 2:
        raise LocalizationError("Unsupported MSBT choice control layout.")
    return choices


def _replacement(text):
    if (not isinstance(text, str) or any(ord(c) < 32 and c != "\n" for c in text)
            or _TEXT_TOKEN.search(text)):
        raise LocalizationError("Editable MSBT text contains a control, event directive, or button icon.")
    return text.encode("utf-8")


@dataclass(frozen=True)
class Message:
    raw: bytes
    parts: tuple
    ending: bytes

    @classmethod
    def parse(cls, raw):
        parts = []
        position = 0
        while position < len(raw):
            match = _CONTROL.search(raw, position)
            if match is None:
                break
            start = match.start()
            if start > position:
                try:
                    pieces = _TEXT_TOKEN.split(raw[position:start].decode("utf-8"))
                except UnicodeDecodeError as exc:
                    raise LocalizationError("Invalid UTF-8 in MSBT text.") from exc
                parts.extend(s.encode("utf-8") if i % 2 else s for i, s in enumerate(pieces) if s)
            tag = raw[start]
            if tag == 0:
                if any(raw[start:]):
                    raise LocalizationError("Unexpected MSBT content following its terminator.")
                return cls(raw, tuple(parts), raw[start:])
            if tag == 0x0E:
                if start + 7 > len(raw):
                    raise LocalizationError("Truncated MSBT start tag.")
                end = start + 7 + int.from_bytes(raw[start + 5:start + 7], "little")
            else:
                end = start + 5
            if end > len(raw):
                raise LocalizationError("Truncated MSBT control payload.")
            parts.append(raw[start:end])
            position = end
        raise LocalizationError("Missing MSBT text terminator.")

    @property
    def source_sha256(self):
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def choices(self):
        """Map part index to visible strings embedded in known choice controls."""
        found = {}
        for index, part in enumerate(self.parts):
            if isinstance(part, bytes):
                choices = _choice_texts(part)
                if choices is not None:
                    found[index] = choices
        return found

    def replaced(self, texts, choices=None, argument_order=None):
        if not isinstance(texts, (list, tuple)) or len(texts) != sum(isinstance(p, str) for p in self.parts):
            raise LocalizationError("Provide one replacement for each editable text part.")
        replacements = iter(texts)
        parts = [_replacement(next(replacements)) if isinstance(part, str) else part for part in self.parts]
        if argument_order is not None:
            # Only bare, indexed string substitutions in a punctuation-only
            # format. Formatting commands, inline styles and icons stay fixed.
            args = self.parts[::2]
            if (not isinstance(argument_order, list) or not 2 <= len(args) <= 3
                    or len(self.parts) != 2 * len(args) - 1
                    or any(type(index) is not int for index in argument_order)
                    or sorted(argument_order) != list(range(len(args)))
                    or any(arg != b"\x0e" + struct.pack("<HHH", 1, i, 0) for i, arg in enumerate(args))
                    or any(separator not in (" ", ": ") for separator in self.parts[1::2])):
                raise LocalizationError("MSBT argument order requires a complete indexed heading format.")
            parts[::2] = [args[index] for index in argument_order]
        if choices is not None:
            available = {str(index): options for index, options in self.choices.items()}
            for key, options in choices.items():
                if str(key) not in available:
                    raise LocalizationError("Unknown MSBT choice control index.")
                index = int(key)
                if not isinstance(options, (tuple, list)) or len(options) != len(available[str(key)]):
                    raise LocalizationError("Provide the original number of MSBT alternatives.")
                encoded = [_replacement(option) for option in options]
                if sum(len(item) + 2 for item in encoded) > 65535:
                    raise LocalizationError("MSBT choice payload exceeds the length limit.")
                payload = b"".join(struct.pack("<H", len(item)) + item for item in encoded)
                parts[index] = parts[index][:5] + struct.pack("<H", len(payload)) + payload
        return b"".join(parts) + self.ending


class MessageTable:
    def __init__(self, raw):
        raw = bytes(raw)
        if (len(raw) < 32 or len(raw) > MAX_RESOURCE or raw[:10] != b"MsgStdBn\xff\xfe"
                or raw[12:14] != b"\x00\x03" or _u32(raw, 18) != len(raw)):
            raise LocalizationError("Expected a little-endian UTF-8 MSBT v3 resource.")
        self.raw = raw
        self.sections = {}
        pos = 32
        for _ in range(int.from_bytes(raw[14:16], "little")):
            size = _u32(raw, pos + 4)
            end = pos + 16 + size
            padded = (end + 15) & ~15
            name = raw[pos:pos + 4]
            if padded > len(raw) or name in self.sections:
                raise LocalizationError("Invalid MSBT section size or duplicate section.")
            self.sections[name] = (pos, end, padded)
            pos = padded
        if pos != len(raw) or not {b"LBL1", b"TXT2"} <= self.sections.keys():
            raise LocalizationError("Missing or inconsistent MSBT sections.")
        self.txt = self._section(b"TXT2")
        count = _u32(self.txt, 0)
        base = 4 + count * 4
        if base > len(self.txt):
            raise LocalizationError("Invalid MSBT text table.")
        self.offsets = (*struct.unpack_from(f"<{count}I", self.txt, 4), len(self.txt))
        if self.offsets[0] != base or any(a >= b for a, b in zip(self.offsets, self.offsets[1:])):
            raise LocalizationError("Invalid MSBT text offsets.")
        self.labels = self._labels(count)

    def _section(self, name):
        start, end, _ = self.sections[name]
        return self.raw[start + 16:end]

    def _labels(self, count):
        data = self._section(b"LBL1")
        groups = _u32(data, 0)
        base = 4 + 8 * groups
        if base > len(data):
            raise LocalizationError("Invalid MSBT label table.")
        labels = {}
        for group in range(groups):
            entries, pos = struct.unpack_from("<II", data, 4 + group * 8)
            if not base <= pos <= len(data):
                raise LocalizationError("Invalid MSBT label offset.")
            for _ in range(entries):
                if pos >= len(data):
                    raise LocalizationError("Truncated MSBT label.")
                end = pos + 1 + data[pos]
                index = _u32(data, end)
                label = data[pos + 1:end].decode("utf-8")
                if index >= count or label in labels:
                    raise LocalizationError("Invalid or duplicate MSBT label.")
                labels[label] = index
                pos = end + 4
        return labels

    def entry(self, label):
        if label not in self.labels:
            raise LocalizationError(f"Unknown MSBT message label: {label}")
        index = self.labels[label]
        return Message.parse(self.txt[self.offsets[index]:self.offsets[index + 1]])

    def patch(self, changes, expected=None):
        """Rebuild TXT2 only. Controls, attributes, events and other messages stay byte-identical."""
        replacements = {}
        touched = set()
        for label, change in changes.items():
            entry = self.entry(label)
            if expected is not None and expected.get(label) != entry.source_sha256:
                raise LocalizationError(f"MSBT source fingerprint differs for {label}.")
            if isinstance(change, dict):
                if "parts" not in change or set(change) - {"parts", "choices", "argument_order", "flow"}:
                    raise LocalizationError("Unexpected MSBT translation fields.")
                if "flow" in change:
                    if change["flow"] != "auto" or "argument_order" in change:
                        raise LocalizationError("Invalid automatic MSBT layout.")
                    from .localization_flow import render
                    new = render(entry, change["parts"], change.get("choices"))
                else:
                    new = entry.replaced(change["parts"], change.get("choices"), change.get("argument_order"))
            else:
                new = entry.replaced(change)
            index = self.labels[label]
            if index in touched:
                raise LocalizationError("Two translated labels refer to the same MSBT entry.")
            touched.add(index)
            if new != entry.raw:
                replacements[index] = new
        if not replacements:
            return self.raw
        count = len(self.offsets) - 1
        text = bytearray(self.txt[:4 + count * 4])
        for index, (start, end) in enumerate(zip(self.offsets, self.offsets[1:])):
            struct.pack_into("<I", text, 4 + index * 4, len(text))
            text.extend(replacements[index] if index in replacements else self.txt[start:end])
        start, end, padded = self.sections[b"TXT2"]
        header = bytearray(self.raw[start:start + 16])
        struct.pack_into("<I", header, 4, len(text))
        padding = (self.raw[end:end + 1] if end < padded else b"\xab") * (-len(text) % 16)
        result = bytearray(self.raw[:start] + header + text + padding + self.raw[padded:])
        if len(result) > MAX_RESOURCE:
            raise LocalizationError("Patched MSBT resource exceeds the size limit.")
        struct.pack_into("<I", result, 18, len(result))
        return bytes(result)


def read_resource(path):
    import zstandard

    path = _ordinary(Path(path))
    if path.stat().st_size > MAX_RESOURCE:
        raise LocalizationError("Compressed MSBT resource exceeds the size limit.")
    data = path.read_bytes()
    try:
        size = zstandard.frame_content_size(data)
        if size > MAX_RESOURCE:
            raise LocalizationError("MSBT resource exceeds the size limit.")
        raw = zstandard.ZstdDecompressor().decompress(data, max_output_size=MAX_RESOURCE, allow_extra_data=False)
    except zstandard.ZstdError as exc:
        raise LocalizationError("Invalid Zstandard MSBT resource.") from exc
    return MessageTable(raw)


def _us_english(romfs):
    return _ordinary(_ordinary(_ordinary(Path(romfs), True) / "Msg", True) / "USen", True)


def _separate(romfs, output):
    source, target = Path(romfs).resolve(), Path(output).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise LocalizationError("Choose a private output separate from the original RomFS.")


def write_overrides(romfs, output_romfs, translations, *, source_hashes=None):
    """Create Msg/USen beneath a private overlay. An existing locale is never replaced.

    translations maps a resource basename to {label: [text for each string part]}.
    Optional source_hashes has the same file and label keys, with entry SHA-256 values.
    Returns the number of changed resources. The caller may add unrelated font assets.
    """
    return write_override_tables(romfs, output_romfs, (
        (name, changes, None if source_hashes is None else source_hashes.get(name, {}))
        for name, changes in translations.items()
    ))


def write_override_tables(romfs, output_romfs, tables, *, prepare_entries=None, include_unchanged=False):
    """Publish an iterable of (basename, changes, source hashes) as one atomic locale."""
    import zstandard

    source = _us_english(romfs)
    root = Path(output_romfs)
    _separate(romfs, root)
    root.mkdir(parents=True, exist_ok=True)
    message_dir = _ordinary(root, True) / "Msg"
    message_dir.mkdir(exist_ok=True)
    destination = _ordinary(message_dir, True) / "USen"
    if os.path.lexists(destination):
        raise LocalizationError("The output already contains a USen directory. Build a fresh private overlay.")
    stage = Path(tempfile.mkdtemp(prefix=".msbt-", dir=message_dir))
    try:
        changed = 0
        compressor = zstandard.ZstdCompressor(level=9)
        for name, changes, expected in tables:
            if not _RESOURCE_NAME.fullmatch(name):
                raise LocalizationError("Invalid MSBT resource basename.")
            table = read_resource(source / name)
            if prepare_entries is not None:
                changes = prepare_entries(name, table, changes)
            patched = table.patch(changes, expected)
            if include_unchanged or patched != table.raw:
                (stage / name).write_bytes(compressor.compress(patched))
                changed += 1
        stage.rename(destination)
        return changed
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def write_catalog(romfs, output):
    """Write private JSON Lines for editors. Original game text stays in the chosen local catalog."""
    source = _us_english(romfs)
    output = Path(output)
    _separate(romfs, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.NamedTemporaryFile("w", dir=output.parent, delete=False, encoding="utf-8", newline="\n") as stream:
        staging = Path(stream.name)
        try:
            for path in sorted(source.glob("*.msbt.zst")):
                table = read_resource(path)
                for label in sorted(table.labels, key=table.labels.get):
                    entry = table.entry(label)
                    row = {"file": path.name, "label": label, "source_sha256": entry.source_sha256,
                           "parts": [p if isinstance(p, str) else {"token": p.hex()} for p in entry.parts]}
                    choices = entry.choices
                    if choices:
                        row["choices"] = choices
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
        except BaseException:
            stream.close()
            staging.unlink(missing_ok=True)
            raise
    try:
        os.replace(staging, output)
    finally:
        staging.unlink(missing_ok=True)
    return count
