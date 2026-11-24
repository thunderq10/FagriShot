import json
from pathlib import Path
import struct
import tempfile
import unittest

from fagrishot.localization import LocalizationError, Message, MessageTable, read_resource, write_catalog, write_overrides
from fagrishot.localization_art import _encode_ink, _mode6_color, _offset, patch_autosave_art


def resource(entries):
    """Small original fixture with opaque attributes and a section after TXT2."""
    lbl = bytearray(struct.pack("<III", 1, len(entries), 12))
    text = bytearray(struct.pack("<I", len(entries)) + bytes(4 * len(entries)))
    for index, (label, raw) in enumerate(entries):
        lbl.extend(bytes([len(label)]) + label.encode() + struct.pack("<I", index))
        struct.pack_into("<I", text, 4 + index * 4, len(text))
        text.extend(raw)
    sections = [(b"LBL1", bytes(lbl)), (b"ATR1", b"keep attributes"), (b"TXT2", bytes(text)), (b"AOCM", b"opaque")]
    file = bytearray(b"MsgStdBn\xff\xfe\0\0\0\3" + struct.pack("<H", len(sections)) + bytes(16))
    for name, contents in sections:
        file.extend(name + struct.pack("<I", len(contents)) + bytes(8) + contents)
        file.extend(b"\xab" * (-len(contents) % 16))
    struct.pack_into("<I", file, 18, len(file))
    return bytes(file)


def start_tag(group, kind, payload):
    return b"\x0e" + struct.pack("<HHH", group, kind, len(payload)) + payload


COLOR = start_tag(0, 3, b"\0\4")
END = b"\x0f" + struct.pack("<HH", 0, 3)
CHOICE = start_tag(4, 0, b"\x03\x00one\x04\x00many")
ICON = "\ue034".encode()
SAMPLE = resource([("line", b"Press " + ICON + COLOR + b"next\nline" + END + b"<EDH>\0"),
                   ("blank", b"\0" * 20), ("choice", b"Gain " + CHOICE + b".\0")])


class MessageTests(unittest.TestCase):
    def test_multibyte_offsets_and_opaque_sections(self):
        table = MessageTable(SAMPLE)
        message = table.entry("line")
        self.assertEqual(message.parts, ("Press ", ICON, COLOR, "next\nline", END, b"<EDH>"))
        self.assertEqual(table.patch({"line": ["Press ", "next\nline"]}), SAMPLE)
        changed = MessageTable(table.patch({"line": ["اضغط ", "السطر\nالتالي"]}, {"line": message.source_sha256}))
        self.assertEqual(changed.entry("line").parts, ("اضغط ", ICON, COLOR, "السطر\nالتالي", END, b"<EDH>"))
        for name in [b"LBL1", b"ATR1", b"AOCM"]:
            self.assertEqual(changed._section(name), table._section(name))
        for label in ("choice", "blank"):
            self.assertEqual(changed.entry(label).raw, table.entry(label).raw)
        self.assertEqual(changed.entry("blank").parts, ())
        self.assertEqual(changed.entry("blank").ending, bytes(20))

    def test_choice_control_recomputes_utf8_lengths_without_changing_command(self):
        table = MessageTable(SAMPLE)
        original = table.entry("choice")
        self.assertEqual(original.choices, {1: ["one", "many"]})
        changes = {"choice": {"parts": ["عدد ", "."], "choices": {"1": ["واحد", "كثير"]}}}
        edited = MessageTable(table.patch(changes)).entry("choice")
        self.assertEqual(edited.choices, {1: ["واحد", "كثير"]})
        self.assertEqual(edited.parts[1][:5], CHOICE[:5])
        for choices in ({"2": ["a", "b"]}, {"1": ["a"]}, {"1": ["a", "\0"]}):
            with self.subTest(choices=choices), self.assertRaises(LocalizationError):
                original.replaced(["new", "."], choices)

    def test_rejects_invalid_offset_control_count_and_source(self):
        broken = bytearray(SAMPLE)
        start = broken.index(b"TXT2")
        struct.pack_into("<I", broken, start + 20, len(SAMPLE) + 1)
        with self.assertRaises(LocalizationError):
            MessageTable(broken)
        for raw in (b"\x0e\1", b"\x0e\0\0\0\0\xff\xff\0", b"text\0hidden\0", b"no terminator"):
            with self.subTest(raw=raw), self.assertRaises(LocalizationError):
                Message.parse(raw)
        table = MessageTable(SAMPLE)
        for changes in ({"line": ["one"]}, {"missing": []}, {"line": ["button \ue034", "x"]},
                        {"line": ["<EDH>", "next"]}, {"line": ["a", "injected\x0e"]}):
            with self.subTest(changes=changes), self.assertRaises(LocalizationError):
                table.patch(changes)
        with self.assertRaisesRegex(LocalizationError, "fingerprint"):
            table.patch({"line": ["a", "b"]}, {"line": "incorrect"})


class OverlayTests(unittest.TestCase):
    def setUp(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("Private resource I/O requires the player's zstandard wheel.")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.input = self.root / "input"
        self.en = self.input / "Msg" / "USen"
        self.en.mkdir(parents=True)
        self.name = "Test.msbt.zst"
        self.source = zstandard.ZstdCompressor().compress(SAMPLE)
        (self.en / self.name).write_bytes(self.source)
        self.out = self.root / "mods" / "Arabic" / "romfs"
        self.changes = {self.name: {"line": ["اضغط ", "متابعة"]}}

    def test_private_catalog_and_atomic_overlay(self):
        catalog = self.root / "private" / "catalog.jsonl"
        self.assertEqual(write_catalog(self.input, catalog), 3)
        rows = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["parts"][1:3], [{"token": ICON.hex()}, {"token": COLOR.hex()}])
        self.assertEqual(rows[2]["choices"], {"1": ["one", "many"]})
        self.assertEqual(write_overrides(self.input, self.out, self.changes,
                                        source_hashes={self.name: {"line": rows[0]["source_sha256"]}}), 1)
        edited = read_resource(self.out / "Msg" / "USen" / self.name)
        self.assertEqual(edited.entry("line").parts[0], "اضغط ")
        self.assertEqual((self.en / self.name).read_bytes(), self.source)
        with self.assertRaisesRegex(LocalizationError, "already contains"):
            write_overrides(self.input, self.out, self.changes)

    def test_failed_install_cannot_modify_source_or_leave_partial_locale(self):
        for dest in (self.input, self.input / "mods", self.root):
            with self.subTest(dest=dest), self.assertRaises(LocalizationError):
                write_overrides(self.input, dest, self.changes)
        with self.assertRaises(LocalizationError):
            write_overrides(self.input, self.out, {**self.changes, "../other.msbt.zst": {}})
        self.assertEqual(list((self.out / "Msg").iterdir()), [])
        self.assertEqual((self.en / self.name).read_bytes(), self.source)


class TutorialTextureTests(unittest.TestCase):
    def test_mode6_encodes_both_anchor_orders(self):
        dark = (5, 17, 25, 255)
        for coverage, wanted in (([0] * 16, dark), ([64] * 16, (213, 183, 99, 255)),
                                 ([64] * 8 + [0] * 8, (109, 100, 62, 255))):
            with self.subTest(coverage=coverage):
                block = _encode_ink(dark, coverage)
                self.assertEqual(len(block), 16)
                self.assertEqual(block[0] & 127, 64)
                self.assertEqual(_mode6_color(block), wanted)

    def test_switch2_gob_and_all_real_blocks_are_distinct(self):
        self.assertEqual([_offset(0, y) for y in range(5)], [4096, 4112, 4128, 4144, 4224])
        used = {_offset(x, y) for x in range(200) for y in range(113)}
        self.assertEqual(len(used), 200 * 113)
        self.assertLess(max(used) + 15, 4096 + 409600)

    def test_unknown_asset_rejected_before_writing(self):
        from fagrishot.localization_art import ASSET
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            game, out = base / "game", base / "overlay"
            (game / ASSET).parent.mkdir(parents=True)
            (game / ASSET).write_bytes(b"synthetic changed revision")
            with self.assertRaisesRegex(LocalizationError, "fingerprint"):
                patch_autosave_art(game, out, base / "font")
            self.assertFalse(out.exists())
            with self.assertRaises(LocalizationError):
                patch_autosave_art(game, game, base / "font")


if __name__ == "__main__":
    unittest.main()
