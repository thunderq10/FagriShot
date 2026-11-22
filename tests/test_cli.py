import configparser
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagrishot import __main__ as cli


class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dirs = {key: self.root / "user #1" / key for key in
                     ("settings", "saves", "backups", "logs", "mods", "caches")}
        self.dirs["settings"].mkdir(parents=True)
        self.config = self.dirs["settings"] / "launcher.json"
        self.exefs = self.root / "inputs" / "exefs"
        self.romfs = self.root / "game ; final" / "romfs"

    def invoke(self, *args):
        with patch.object(cli, "locations", return_value=self.dirs), redirect_stdout(io.StringIO()) as output:
            cli.execute(cli.parser().parse_args([str(arg) for arg in args]))
        return output.getvalue()

    def test_damaged_settings_recovery_preserves_player_data(self):
        self.dirs["saves"].mkdir()
        slot = self.dirs["saves"] / "slot"
        slot.write_bytes(b"before repair")
        for damaged in (b"{", b'{"romfs":123}', b"\xff"):
            with self.subTest(damaged=damaged):
                self.config.write_bytes(damaged)
                self.assertIn("Select both folders", json.loads(self.invoke("status", "--json"))["settings_warning"])
                self.assertIn("Select both folders", self.invoke("doctor"))
                self.assertTrue(self.invoke("backup").strip())
                with self.assertRaisesRegex(ValueError, "Select both folders"):
                    self.invoke("configure", "--exefs", self.exefs)
                self.assertEqual(self.config.read_bytes(), damaged)
                self.invoke("configure", "--exefs", self.exefs, "--romfs", self.romfs)
                status = json.loads(self.invoke("status", "--json"))
                self.assertIsNone(status["settings_warning"])
                self.assertEqual((status["exefs"], status["romfs"]), (str(self.exefs), str(self.romfs)))
                self.assertEqual(slot.read_bytes(), b"before repair")

    def test_bom_and_newer_settings_version(self):
        self.config.write_text(json.dumps({"exefs": str(self.exefs)}), encoding="utf-8-sig")
        self.assertEqual(json.loads(self.invoke("status", "--json"))["exefs"], str(self.exefs))
        future = b'{"schema":2,"paths":[]}'
        self.config.write_bytes(future)
        with self.assertRaisesRegex(ValueError, "Unsupported settings version"):
            self.invoke("configure", "--exefs", self.exefs, "--romfs", self.romfs)
        self.assertEqual(self.config.read_bytes(), future)
        self.assertIn("No backups", self.invoke("backups"))

    def test_configure_rejects_overlapping_folders_before_writing(self):
        self.invoke("configure", "--exefs", self.exefs, "--romfs", self.romfs)
        before = self.config.read_bytes()
        for nested in (self.exefs, self.exefs / "inner", self.exefs.parent):
            with self.subTest(nested=nested), self.assertRaisesRegex(ValueError, "overlap"):
                self.invoke("configure", "--romfs", nested)
            self.assertEqual(self.config.read_bytes(), before)

    def test_runtime_ini_quotes_paths_and_uses_verified_main(self):
        ini_file = self.dirs["settings"] / "fefw.ini"
        ini_file.write_text("[fs]\nmain = stale.nso\n[custom]\nvalue = keep\n", encoding="utf-8-sig")
        private = self.root / "build #1"
        cache = self.dirs["caches"] / "compiled"
        with (patch.object(cli, "PRIVATE", private),
              patch("fagrishot.shaders.require_ready", return_value=cache),
              patch("fagrishot.saves.snapshot"), patch.object(cli.subprocess, "run") as game):
            cli.launch({"romfs": self.romfs}, {}, self.dirs)
        game.assert_called_once()
        ini = configparser.RawConfigParser()
        ini.read(ini_file, encoding="utf-8")
        for section, key, path in (("fs", "main", private / "prepared-exefs/main"),
                                   ("fs", "romfs", self.romfs), ("fs", "save_dir", self.dirs["saves"]),
                                   ("nvn", "shader_cache", cache / "spirv"),
                                   ("log", "path", self.dirs["logs"] / "FagriShot.log")):
            self.assertEqual(ini.get(section, key), f'"{path.as_posix()}"')
        self.assertEqual(ini.get("custom", "value"), "keep")

    def test_damaged_build_record_keeps_play_unavailable(self):
        private = self.root / "private"
        exe = private / "game/FagriShot.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"incomplete fixture")
        with patch.object(cli, "PRIVATE", private), patch.object(cli, "EXE", exe):
            for invalid in ("{", "null", "[]", '{"product":"FagriShot","input":null}'):
                with self.subTest(invalid=invalid):
                    (private / "linked.json").write_text(invalid, encoding="utf-8")
                    self.assertFalse(json.loads(self.invoke("status", "--json"))["exe_built"])
                    with self.assertRaisesRegex(ValueError, "Run build-code"):
                        cli.ensure_exe({})

    def test_explicit_enrollment_and_inspection_of_private_trial(self):
        private = self.root / "private"
        inv = {"exefs": {"tree_sha256": "e" * 64}, "romfs": {"tree_sha256": "a" * 64}}
        source = {"commit": "a" * 40, "tracked_diff_sha256": "b" * 64}
        receipt = {"kind": "local-play-trial"}
        with (patch.object(cli, "PRIVATE", private), patch.object(cli, "inspect_inputs", return_value=inv),
              patch.object(cli, "check_executable", return_value={"version": "fixture"}),
              patch.object(cli, "ensure_exe", return_value={"source": source}),
              patch.object(cli, "local_trial_receipt", return_value=receipt) as enroll,
              patch.object(cli, "source_state", return_value=source),
              patch.object(cli, "check_support", side_effect=cli.InputError("no public pairing")),
              patch.object(cli, "check_local_trial") as validate):
            with self.assertRaisesRegex(cli.InputError, "no public pairing"):
                self.invoke("inspect")
            self.assertFalse((private / "local-trial.json").exists())
            paths = ["--exefs", self.exefs, "--romfs", self.romfs]
            self.invoke("enroll-local", *paths)
            selected = {"exefs": self.exefs, "romfs": self.romfs}
            enroll.assert_called_once_with(inv, cli.CATALOG, checkout=cli.ROOT, paths=selected, source=source)
            self.assertEqual(json.loads((private / "local-trial.json").read_bytes()), receipt)
            self.assertIn("private local test pairing", self.invoke("inspect", *paths))
            validate.assert_called_once_with(inv, cli.CATALOG, receipt, checkout=cli.ROOT, paths=selected, source=source)
