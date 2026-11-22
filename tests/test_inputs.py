import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

from fagrishot.inputs import InputError, check_executable, check_support, inspect_exefs, inspect_inputs


PROGRAM = 0xABCD
PATCH = 0x789A
CATALOG = Path(__file__).resolve().parents[1] / "compatibility" / "supported.json"


def nso(payload, *, flags=0):
    head = bytearray(0x100)
    head[:4] = b"NSO0"
    struct.pack_into("<I", head, 0xC, flags)
    struct.pack_into("<III", head, 0x10, 0x100, 0, len(payload))
    head[0x40:0x60] = bytes(range(32))
    struct.pack_into("<I", head, 0x60, len(payload))
    return bytes(head) + payload


def npdm():
    result = bytearray(0xC0)
    result[:4] = b"META"
    struct.pack_into("<I", result, 0x18, 17)
    struct.pack_into("<II", result, 0x70, 0x80, 0x40)
    result[0x80:0x84] = b"ACI0"
    struct.pack_into("<Q", result, 0x90, PROGRAM)
    return result


def cnmt(patch=False, application=PROGRAM):
    extended = 0x18 if patch else 0x10
    data = bytearray(0x20 + extended)
    struct.pack_into("<QIBBH", data, 0, PATCH if patch else application, 3 if patch else 0,
                     0x81 if patch else 0x80, 0, extended)
    struct.pack_into("<Q", data, 0x20, application if patch else PATCH)
    return bytes(data) + hashlib.sha256(data).digest()


class GameInputs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.exe, self.rom = self.root / "exe", self.root / "rom"
        self.exe.mkdir()
        self.rom.mkdir()
        self.main = nso(b"original fixture payload")
        (self.exe / "main").write_bytes(self.main)
        (self.exe / "sdk").write_bytes(nso(b"fixture two"))
        (self.exe / "main.npdm").write_bytes(npdm())
        (self.rom / "private-title-name").write_bytes(b"private fictional content")
        self.base, self.update = self.root / "base.cnmt", self.root / "update.cnmt"
        self.base.write_bytes(cnmt())
        self.update.write_bytes(cnmt(patch=True))
        self.inventory = inspect_inputs(self.exe, self.rom, base_cnmt=self.base, update_cnmt=self.update)
        self.catalog = self.root / "catalog.json"
        modules = self.inventory["modules"]
        self.candidate = {"id": "test-only", "program_id": "000000000000ABCD", "version": "test update 3",
                          "update_version": 3, "main_build_id": bytes(range(20)).hex(),
                          "main_plain_sha256": hashlib.sha256(nso(b"fixture plain")).hexdigest(),
                          "sha256": {name: module["sha256"] for name, module in modules.items()}}
        self.candidate["sha256"]["main.npdm"] = self.inventory["program"]["sha256"]
        self.pair = {"candidate": "test-only", "exefs_tree_sha256": self.inventory["exefs"]["tree_sha256"],
                     "romfs_tree_sha256": self.inventory["romfs"]["tree_sha256"],
                     "base_cnmt_sha256": self.inventory["base_cnmt"]["sha256"],
                     "update_cnmt_sha256": self.inventory["update_cnmt"]["sha256"]}

    def catalog_with(self, records):
        data = {"schema": 1, "candidates": [self.candidate], "supported": records}
        self.catalog.write_text(json.dumps(data), encoding="utf-8")
        return self.catalog

    def test_metadata_and_read_only_inventory(self):
        self.assertEqual(self.inventory["modules"]["main"]["sha256"], hashlib.sha256(self.main).hexdigest())
        self.assertEqual(self.inventory["modules"]["main"]["build_id"], bytes(range(32)).hex())
        self.assertEqual(self.inventory["program"]["raw_npdm_version"], 17)
        self.assertEqual(self.inventory["update_cnmt"]["version"], 3)
        self.assertEqual((self.exe / "main").read_bytes(), self.main)
        serialized = json.dumps(self.inventory)
        for value in (self.root.name, "private-title-name", "private fictional content"):
            self.assertNotIn(value, serialized)

    def test_published_catalog_matches_pinned_upstream(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(data["supported"], [])
        source = CATALOG.parents[1] / "game-build/release/dump_hashes.json"
        upstream = json.loads(source.read_text(encoding="utf-8"))
        for key, value in upstream.items():
            self.assertEqual(data["candidates"][0][key], value, key)
        with self.assertRaisesRegex(InputError, "no FagriShot executable profile"):
            check_support(self.inventory, CATALOG)

    def test_both_exact_trees_required(self):
        path = self.catalog_with([self.pair])
        self.assertEqual(check_support(self.inventory, path), self.pair)
        (self.rom / "private-title-name").rename(self.rom / "new name")
        moved = inspect_inputs(self.exe, self.rom, base_cnmt=self.base, update_cnmt=self.update)
        with self.assertRaisesRegex(InputError, "no verified"):
            check_support(moved, path)
        (self.rom / "new name").rename(self.rom / "private-title-name")
        (self.exe / "extra metadata").write_bytes(b"different effective ExeFS")
        with self.assertRaisesRegex(InputError, "no verified"):
            check_support(inspect_inputs(self.exe, self.rom, base_cnmt=self.base, update_cnmt=self.update), path)

    def test_paired_metadata_checked(self):
        self.base.write_bytes(cnmt(application=1))
        with self.assertRaisesRegex(InputError, "different applications"):
            inspect_inputs(self.exe, self.rom, base_cnmt=self.base, update_cnmt=self.update)
        with self.assertRaisesRegex(InputError, "different application"):
            check_support(inspect_inputs(self.exe, self.rom, base_cnmt=self.base), self.catalog_with([]))
        self.base.write_bytes(cnmt()[:-1] + b"\0")
        with self.assertRaisesRegex(InputError, "digest"):
            inspect_inputs(self.exe, self.rom, base_cnmt=self.base)

    def test_exact_executable_matches_without_content_pair(self):
        path = self.catalog_with([])
        self.assertEqual(check_executable(inspect_exefs(self.exe), path)["id"], "test-only")
        with self.assertRaisesRegex(InputError, "ExeFS matches"):
            check_support(self.inventory, path)
        (self.exe / "main").write_bytes(nso(b"fixture plain"))
        self.assertEqual(check_executable(inspect_exefs(self.exe), path)["id"], "test-only")

    def test_same_build_id_with_bad_module_missing_extra_module(self):
        path = self.catalog_with([])
        (self.exe / "sdk").write_bytes(nso(b"changed"))
        with self.assertRaisesRegex(InputError, "differs at sdk"):
            check_executable(inspect_exefs(self.exe), path)
        (self.exe / "sdk").unlink()
        with self.assertRaisesRegex(InputError, "differs at sdk"):
            check_executable(inspect_exefs(self.exe), path)
        (self.exe / "new").write_bytes(nso(b"unconfigured module"))
        with self.assertRaisesRegex(InputError, "new, sdk"):
            check_executable(inspect_exefs(self.exe), path)

    def test_exefs_without_descriptor_remains_inspectable(self):
        (self.exe / "main.npdm").unlink()
        report = inspect_exefs(self.exe)
        self.assertIsNone(report["program"])
        with self.assertRaisesRegex(InputError, "main.npdm"):
            check_executable(report, self.catalog_with([]))

    def test_hashes_stable_across_copy_and_changed_by_one_byte(self):
        target = self.root / "other-exe"
        shutil.copytree(self.exe, target)
        self.assertEqual(inspect_inputs(target, self.rom)["exefs"], self.inventory["exefs"])
        original = self.rom / "private-title-name"
        original.write_bytes(original.read_bytes()[:-1] + b"x")
        self.assertNotEqual(self.inventory["romfs"]["tree_sha256"], inspect_inputs(target, self.rom)["romfs"]["tree_sha256"])

    def test_invalid_bounds_and_compression_inspection(self):
        payload = nso(b"opaque compressed bytes for inventory only", flags=0x81)
        (self.exe / "main").write_bytes(payload)
        details = inspect_exefs(self.exe)["modules"]["main"]["segments"]
        self.assertEqual(details["text"]["compression"], "ZBIC")
        self.assertEqual(details["rodata"]["compression"], "none")
        (self.exe / "main").write_bytes(payload[:-1])
        with self.assertRaisesRegex(InputError, "extends outside"):
            inspect_exefs(self.exe)
        (self.exe / "main").write_bytes(b"NSO0")
        with self.assertRaisesRegex(InputError, "incomplete"):
            inspect_exefs(self.exe)

    def test_missing_overlapping_empty_and_invalid_directories(self):
        with self.assertRaisesRegex(InputError, "missing"):
            inspect_inputs(self.root / "absent", self.rom)
        with self.assertRaisesRegex(InputError, "overlapping"):
            inspect_inputs(self.exe, self.root)
        (self.rom / "private-title-name").unlink()
        with self.assertRaisesRegex(InputError, "empty"):
            inspect_inputs(self.exe, self.rom)
        (self.exe / "main.npdm").write_bytes(npdm()[:0x80])
        with self.assertRaisesRegex(InputError, "incomplete ACI0"):
            inspect_exefs(self.exe)

    def test_linked_file_rejected(self):
        try:
            (self.rom / "link").symlink_to(self.base)
        except OSError:
            self.skipTest("Creating symbolic links requires filesystem privileges.")
        with self.assertRaisesRegex(InputError, "link or reparse"):
            inspect_inputs(self.exe, self.rom)
