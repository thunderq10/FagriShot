import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from fagrishot.saves import list_backups, recover, restore, snapshot
from fagrishot.storage import build_lock, check_separation, locations, write_json


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dirs = {"saves": self.root / "saves", "backups": self.root / "backups"}
        self.saves = self.dirs["saves"]
        self.saves.mkdir()
        self.slot = self.saves / "slot"
        self.slot.write_bytes(b"original synthetic save")

    def test_roundtrip_and_previous_destination_backup(self):
        key = snapshot(self.dirs)
        self.slot.write_bytes(b"second synthetic save")
        restore(self.dirs, key)
        self.assertEqual(self.slot.read_bytes(), b"original synthetic save")
        saved = list_backups(self.dirs)
        self.assertEqual(len(saved), 2)
        self.assertEqual((self.dirs["backups"] / saved[-1] / "data/slot").read_bytes(), b"second synthetic save")

    def test_automatic_backup_reuses_only_an_intact_snapshot_of_current_saves(self):
        first = snapshot(self.dirs, reuse_latest=True)
        self.assertEqual(snapshot(self.dirs, reuse_latest=True), first)
        (self.dirs["backups"] / first / "data/slot").write_bytes(b"damaged")
        second = snapshot(self.dirs, reuse_latest=True)
        self.assertNotEqual(second, first)
        self.slot.write_bytes(b"new save")
        third = snapshot(self.dirs, reuse_latest=True)
        self.assertNotEqual(third, second)
        self.assertEqual((self.dirs["backups"] / third / "data/slot").read_bytes(), b"new save")

    def test_corruption_rejected_before_existing_saves_touched(self):
        key = snapshot(self.dirs)
        (self.dirs["backups"] / key / "data/slot").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            restore(self.dirs, key)
        self.assertEqual(self.slot.read_bytes(), b"original synthetic save")

    def test_interrupted_directory_swap(self):
        key = snapshot(self.dirs)
        write_json(self.root / ".fagrishot-restore.json", {"previous": key})
        self.saves.rename(self.root / ".saves-before-restore")
        recover(self.dirs)
        self.assertTrue(self.slot.is_file())

    def test_unrelated_directory_is_never_removed_during_recovery(self):
        old = self.root / ".saves-before-restore"
        old.mkdir()
        (old / "unrelated").write_bytes(b"keep")
        recover(self.dirs)
        with self.assertRaisesRegex(ValueError, "already exists"):
            restore(self.dirs, snapshot(self.dirs))
        self.assertEqual((old / "unrelated").read_bytes(), b"keep")

    def test_retention_and_unrelated_directory(self):
        backups = self.dirs["backups"]
        backups.mkdir()
        keep = backups / "personal"
        keep.mkdir()
        for n in range(12):
            self.slot.write_bytes(str(n).encode())
            snapshot(self.dirs)
        self.assertEqual(len(list_backups(self.dirs)), 10)
        self.assertTrue(keep.is_dir())

    def test_retention_preserves_extra_files_in_completed_backup(self):
        with patch("fagrishot.saves.KEEP", 1):
            key = snapshot(self.dirs)
            note = self.dirs["backups"] / key / "player notes.txt"
            note.write_bytes(b"unrelated")
            with redirect_stderr(io.StringIO()) as warning:
                snapshot(self.dirs)
            self.assertIn("additional files", warning.getvalue())
            self.assertEqual(note.read_bytes(), b"unrelated")

    def test_corrupt_newer_backup_does_not_evict_last_good_copy(self):
        with patch("fagrishot.saves.KEEP", 2):
            first = snapshot(self.dirs)
            corrupt = snapshot(self.dirs)
            (self.dirs["backups"] / corrupt / "data/slot").write_bytes(b"damaged")
            with redirect_stderr(io.StringIO()) as warning:
                latest = snapshot(self.dirs)
            self.assertIn(corrupt, warning.getvalue())
            self.assertEqual(set(list_backups(self.dirs)), {first, corrupt, latest})

    def test_failed_restore_of_oldest_backup_remains_retryable(self):
        with patch("fagrishot.saves.KEEP", 1):
            key = snapshot(self.dirs)
            self.slot.write_bytes(b"current state")
            rename = Path.rename

            def fail_install(path, target):
                if path.name.startswith(".incoming-"):
                    raise OSError("fixture failed to install")
                return rename(path, target)

            with patch.object(Path, "rename", fail_install):
                with self.assertRaisesRegex(OSError, "failed to install"):
                    restore(self.dirs, key)
            self.assertEqual(self.slot.read_bytes(), b"current state")
            self.assertIn(key, list_backups(self.dirs))
            restore(self.dirs, key)
            self.assertEqual(self.slot.read_bytes(), b"original synthetic save")

    def test_retry_after_interrupted_old_tree_cleanup(self):
        (self.saves / "other").write_bytes(b"previous file")
        previous = snapshot(self.dirs)
        journal = self.root / ".fagrishot-restore.json"
        write_json(journal, {"previous": previous})
        old = self.root / ".saves-before-restore"
        self.saves.rename(old)
        self.saves.mkdir()
        self.slot.write_bytes(b"restored state")
        (old / "other").unlink()
        recover(self.dirs)
        recover(self.dirs)
        self.assertFalse(old.exists())
        self.assertFalse(journal.exists())
        self.assertEqual(self.slot.read_bytes(), b"restored state")
        self.assertEqual((self.dirs["backups"] / previous / "data/other").read_bytes(), b"previous file")

    def test_recovery_preserves_unrecognized_or_changed_previous_tree(self):
        previous = snapshot(self.dirs)
        journal = self.root / ".fagrishot-restore.json"
        write_json(journal, {"previous": previous})
        old = self.root / ".saves-before-restore"
        self.saves.rename(old)
        (old / "slot").write_bytes(b"edited old state")
        with self.assertRaisesRegex(ValueError, "differ"):
            recover(self.dirs)
        self.saves.mkdir()
        self.slot.write_bytes(b"published")
        with self.assertRaisesRegex(ValueError, "changed"):
            recover(self.dirs)
        self.assertTrue(journal.exists())
        self.assertEqual((old / "slot").read_bytes(), b"edited old state")
        self.assertEqual(self.slot.read_bytes(), b"published")

    def test_recovery_reinstates_verified_backup_when_both_save_trees_are_missing(self):
        previous = snapshot(self.dirs)
        write_json(self.root / ".fagrishot-restore.json", {"previous": previous})
        self.saves.rename(self.root / "fixture-set-aside")
        recover(self.dirs)
        self.assertEqual(self.slot.read_bytes(), b"original synthetic save")
        self.assertFalse((self.root / ".fagrishot-restore.json").exists())

    def test_invalid_journal_returns_a_recoverable_error(self):
        journal = self.root / ".fagrishot-restore.json"
        write_json(journal, [])
        with self.assertRaisesRegex(ValueError, "journal is invalid"):
            recover(self.dirs)
        self.assertTrue(journal.exists())

    def test_exefs_preparation_rejects_an_input_as_output(self):
        tool = Path(__file__).resolve().parents[1] / "tools/nso/prepare_exefs.py"
        before = self.slot.read_bytes()
        for target in (self.saves, self.saves / "nested", self.root):
            result = subprocess.run([sys.executable, "-I", tool, "--exefs", self.saves, "--out", target],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("separate directories", result.stderr)
        self.assertEqual(self.slot.read_bytes(), before)

    def test_path_guards_and_namespace(self):
        for src, dest in [(self.root,self.slot),(self.slot,self.root),(self.root,self.root)]:
            with self.assertRaisesRegex(ValueError,"overlap"):
                check_separation([src],[dest])
        with self.assertRaisesRegex(ValueError, "backup ID"):
            restore(self.dirs, "../another-app")
        with patch("sys.platform","win32"), patch.dict("os.environ",{"LOCALAPPDATA":str(self.root)}):
            self.assertEqual(locations()["settings"], self.root / "FagriShot/settings")
            self.assertEqual(locations()["build"], self.root / "FagriShot/build")
            self.assertTrue(all(path.is_dir() for path in locations().values()))

    def test_namespace_requires_absolute_windows_location_and_checks_each_input(self):
        with patch("sys.platform", "win32"):
            for location in ("", "relative"):
                with patch.dict(os.environ, {"LOCALAPPDATA": location}):
                    with self.assertRaisesRegex(ValueError, "absolute"):
                        locations()
        outputs = iter([self.dirs["backups"] / "output"])
        with self.assertRaisesRegex(ValueError, "overlap"):
            check_separation([self.saves, self.dirs["backups"]], outputs)

    @unittest.skipUnless(sys.platform == "win32", "Windows redirected output")
    def test_cli_preserves_unicode_paths_with_legacy_windows_encoding(self):
        tool = Path(__file__).resolve().parents[1] / "scripts/fagrishot.py"
        command = [sys.executable, "-I", "-X", "utf8=0", tool]
        env = {**os.environ, "LOCALAPPDATA": str(self.root / "profile")}
        folder = self.root / "\u65e5\u672c\u8a9e \u0627\u062e\u062a\u0628\u0627\u0631 \u00e9"
        exefs, romfs = folder / "exefs", folder / "romfs"

        def run(*args):
            return subprocess.run([*command, *args], env=env, capture_output=True, encoding="utf-8")

        saved = run("configure", "--exefs", exefs, "--romfs", romfs)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        status = run("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["exefs"], str(exefs))
        doctor = run("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn(str(romfs), doctor.stdout)
        overlapping = Path(env["LOCALAPPDATA"]) / "FagriShot" / "settings" / folder.name
        rejected = run("configure", "--exefs", overlapping, "--romfs", romfs)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn(str(overlapping), rejected.stderr)

    def test_config_replacement_and_exclusive_build(self):
        cfg = self.root / "settings/config.json"
        write_json(cfg, {"version":1})
        write_json(cfg, {"version":2})
        self.assertEqual(json.loads(cfg.read_text()),{"version":2})
        key = self.root / "task.lock"
        with build_lock(key):
            with self.assertRaisesRegex(ValueError, "Another"):
                with build_lock(key):
                    self.fail("Concurrent operation acquired lock")
        with build_lock(key):
            pass
