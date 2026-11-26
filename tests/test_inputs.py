import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from fagrishot import inputs
from fagrishot.inputs import (InputError, check_executable, check_input_roots, check_local_trial, check_support,
                              inspect_exefs, inspect_inputs, local_trial_receipt, local_trial_scope_matches)


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
    def test_repeat_romfs_verification_detects_nested_replacement_and_preserved_mtime(self):
        nested = self.rom / "nest"
        nested.mkdir()
        leaf = nested / "game-data"
        leaf.write_bytes(b"one")
        receipt = self.root / "verified-romfs.json"
        first = inspect_inputs(self.exe, self.rom, romfs_cache=receipt)
        if not receipt.is_file():
            self.skipTest("This filesystem requires full content verification")
        with patch.object(inputs, "_romfs_file", side_effect=AssertionError("unexpected content read")):
            self.assertEqual(inspect_inputs(self.exe, self.rom, romfs_cache=receipt), first)
            (self.exe / "main").write_bytes(nso(b"different executable"))
            self.assertNotEqual(inspect_inputs(self.exe, self.rom, romfs_cache=receipt)["exefs"], first["exefs"])
        receipt.write_text("incomplete verification receipt")
        with patch.object(inputs, "_romfs_file", wraps=inputs._romfs_file) as read:
            self.assertEqual(inspect_inputs(self.exe, self.rom, romfs_cache=receipt)["romfs"], first["romfs"])
            self.assertGreater(read.call_count, 0)
        saved = leaf.stat()
        leaf.write_bytes(b"two")
        os.utime(leaf, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        updated = inspect_inputs(self.exe, self.rom, romfs_cache=receipt)
        self.assertNotEqual(updated["romfs"], first["romfs"])
        second = nested / "renamed"
        leaf.rename(second)
        self.assertNotEqual(inspect_inputs(self.exe, self.rom, romfs_cache=receipt)["romfs"], updated["romfs"])

    def test_hard_linked_inputs_are_content_verified_on_every_launch(self):
        leaf = self.rom / "asset"
        leaf.write_bytes(b"first")
        alias = self.root / "external-alias"
        os.link(leaf, alias)
        receipt = self.root / "verified-romfs.json"
        before = inspect_inputs(self.exe, self.rom, romfs_cache=receipt)
        self.assertFalse(receipt.exists())
        saved = leaf.stat()
        alias.write_bytes(b"other")
        os.utime(alias, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        self.assertNotEqual(inspect_inputs(self.exe, self.rom, romfs_cache=receipt)["romfs"], before["romfs"])

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
        self.assertEqual({r["candidate"] for r in data["supported"]}, {"fefw-131072"})
        self.assertTrue(all(len(r["romfs_tree_sha256"]) == 64 and len(r["exefs_tree_sha256"]) == 64
                            for r in data["supported"]))
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

    def test_parallel_romfs_keeps_original_digest_with_out_of_order_reads(self):
        first = self.rom / "a"
        first.write_bytes(b"")
        (self.rom / "b").write_bytes(b"second")
        nested = self.rom / "b!"
        nested.mkdir()
        (nested / "é").write_bytes(b"X" * (1024 * 1024 + 1))
        (nested / "empty").mkdir()
        serial = inputs._tree(self.rom, "RomFS")
        second_finished = threading.Event()
        read = inputs._romfs_file

        def reordered(path, listed):
            if Path(path) == first:
                self.assertTrue(second_finished.wait(5))
            result = read(path, listed)
            if Path(path).name == "b":
                second_finished.set()
            return result

        with patch.object(inputs, "_romfs_file", reordered):
            self.assertEqual(inspect_inputs(self.exe, self.rom)["romfs"], serial)

    def test_parallel_romfs_rejects_file_or_directory_changes_during_scan(self):
        read = inputs._romfs_file

        def replace_before_read(path, listed):
            Path(path).write_bytes(b"updated while queued")
            return read(path, listed)

        with patch.object(inputs, "_romfs_file", replace_before_read), self.assertRaisesRegex(InputError, "changed"):
            inspect_inputs(self.exe, self.rom)

        def add_after_read(path, listed):
            result = read(path, listed)
            old = self.rom.stat()
            (self.rom / "new entry").touch()
            # Make the directory change visible even on filesystems with coarse timestamps.
            os.utime(self.rom, ns=(old.st_atime_ns, old.st_mtime_ns + 2_000_000_000))
            return result

        with patch.object(inputs, "_romfs_file", add_after_read), self.assertRaisesRegex(InputError, "changed"):
            inspect_inputs(self.exe, self.rom)

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

    def test_prospective_overlapping_inputs_and_drive_alias(self):
        later = self.root / "not-created"
        check_input_roots(later / "exe", later / "rom")
        for pair in ((later, later), (later, later / "rom"), (later / "exe", later)):
            with self.assertRaisesRegex(InputError, "overlapping"):
                check_input_roots(*pair)
        drive = Path(self.root.anchor)
        alias = drive / self.root.parts[1] / ".."
        with self.assertRaisesRegex(InputError, "inside the drive"):
            check_input_roots(later, alias)

    def test_executable_profile_reports_missing_canonical_main_name(self):
        inventory = inspect_exefs(self.exe)
        inventory["modules"]["MAIN"] = inventory["modules"].pop("main")
        with self.assertRaisesRegex(InputError, "differs at MAIN, main"):
            check_executable(inventory, self.catalog_with([]))

    def test_linked_file_rejected(self):
        try:
            (self.rom / "link").symlink_to(self.base)
        except OSError:
            self.skipTest("Creating symbolic links requires filesystem privileges.")
        with self.assertRaisesRegex(InputError, "link or reparse"):
            inspect_inputs(self.exe, self.rom)

    def trial_scope(self):
        checkout = self.root / "source"
        marker = checkout / "recomp/games/fefw/config.py"
        marker.parent.mkdir(parents=True)
        marker.touch()
        return {"checkout": checkout, "paths": {"exefs": self.exe, "romfs": self.rom},
                "source": {"commit": "d" * 40, "tracked_diff_sha256": "e" * 64}}

    @patch.dict("fagrishot.inputs._TRIAL_FILE_COUNTS", {("000000000000ABCD", 3): 1})
    def test_trial_requires_enrollment_and_remains_outside_public_catalog(self):
        path = self.catalog_with([])
        scope = self.trial_scope()
        with self.assertRaisesRegex(InputError, "no matching local trial"):
            check_local_trial(self.inventory, path, None, **scope)
        receipt = local_trial_receipt(self.inventory, path, **scope)
        saved = json.loads(json.dumps(receipt))
        self.assertEqual(check_local_trial(self.inventory, path, saved, **scope), receipt)
        self.assertTrue(local_trial_scope_matches(saved, **scope))
        self.assertNotIn(self.root.name, json.dumps(receipt))
        self.assertNotIn("private-title-name", json.dumps(receipt))
        with self.assertRaisesRegex(InputError, "no verified"):
            check_support(self.inventory, path)
        self.assertEqual(json.loads(path.read_text())["supported"], [])
        for bad in (None, {}, {**receipt, "romfs": {}}, {**receipt, "kind": "public"}):
            self.assertFalse(local_trial_scope_matches(bad, **scope))

    @patch.dict("fagrishot.inputs._TRIAL_FILE_COUNTS", {("000000000000ABCD", 3): 1})
    def test_trial_binds_locations_checkout_and_tracked_source(self):
        path = self.catalog_with([])
        scope = self.trial_scope()
        receipt = local_trial_receipt(self.inventory, path, **scope)
        other = self.root / "second checkout"
        shutil.copytree(scope["checkout"], other)
        variants = [
            {**scope, "checkout": other},
            {**scope, "paths": {**scope["paths"], "romfs": self.root / "other romfs"}},
            {**scope, "source": {**scope["source"], "tracked_diff_sha256": "1" * 64}},
            {**scope, "source": {**scope["source"], "commit": "2" * 40}},
        ]
        for changed in variants:
            self.assertFalse(local_trial_scope_matches(receipt, **changed))
            with self.assertRaisesRegex(InputError, "no matching local trial"):
                check_local_trial(self.inventory, path, receipt, **changed)
        with self.assertRaisesRegex(InputError, "source checkout"):
            local_trial_receipt(self.inventory, path, **{**scope, "checkout": self.root})

    @patch.dict("fagrishot.inputs._TRIAL_FILE_COUNTS", {("000000000000ABCD", 3): 1})
    def test_local_trial_rechecks_file_bytes_executable_and_metadata(self):
        path, scope = self.catalog_with([]), self.trial_scope()
        original = local_trial_receipt(self.inventory, path, **scope)
        args = {"base_cnmt": self.base, "update_cnmt": self.update}
        with self.assertRaisesRegex(InputError, "no matching local trial"):
            check_local_trial(inspect_inputs(self.exe, self.rom), path, original, **scope)
        self.base.write_bytes(cnmt(application=1))
        with self.assertRaisesRegex(InputError, "different application"):
            check_local_trial(inspect_inputs(self.exe, self.rom, base_cnmt=self.base), path, original, **scope)
        self.base.write_bytes(cnmt())
        asset = self.rom / "private-title-name"
        payload = asset.read_bytes()
        asset.write_bytes(payload[:-1] + b"X")
        with self.assertRaisesRegex(InputError, "no matching local trial"):
            check_local_trial(inspect_inputs(self.exe, self.rom, **args), path, original, **scope)
        asset.write_bytes(payload)
        (self.exe / "extra metadata").write_bytes(b"changed effective executable tree")
        with self.assertRaisesRegex(InputError, "no matching local trial"):
            check_local_trial(inspect_inputs(self.exe, self.rom, **args), path, original, **scope)
        (self.exe / "sdk").write_bytes(nso(b"same build ID, different payload"))
        with self.assertRaisesRegex(InputError, "differs at sdk"):
            local_trial_receipt(inspect_inputs(self.exe, self.rom, **args), path, **scope)

    def test_local_trial_needs_known_complete_count_and_full_inventory(self):
        path, scope = self.catalog_with([]), self.trial_scope()
        with self.assertRaisesRegex(InputError, "no upstream file-count reference"):
            local_trial_receipt(self.inventory, path, **scope)
        with patch.dict("fagrishot.inputs._TRIAL_FILE_COUNTS", {("000000000000ABCD", 3): 252686}):
            with self.assertRaisesRegex(InputError, "252,686 files"):
                local_trial_receipt(self.inventory, path, **scope)
            partial = {**self.inventory, "romfs": {"files": 252686, "bytes": 123}}
            with self.assertRaisesRegex(InputError, "complete inspection"):
                local_trial_receipt(partial, path, **scope)
