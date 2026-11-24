import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagrishot import arabic
from fagrishot import __main__ as cli
from fagrishot.arabic_font import FONT_NAMES


class OpeningCoverageTests(unittest.TestCase):
    def test_first_dialogue_is_covered_in_each_presentation(self):
        root = Path(__file__).resolve().parents[1]
        catalogs, _ = arabic.assets(root)
        data = arabic.catalog_data(catalogs)
        # The same opening moves between a dialogue box, a world map and a movie.
        expected = {
            "0_ISM_c01.msbt.zst": ["MID_EV00_000_m01_001", "MID_EV00_001_m01_026"],
            "LG_0_XXX_cAssist.msbt.zst": ["MID_LGV_0XXX_c01_GM00_003_m01_001",
                                          "MID_LGV_0XXX_c01_GM00_003_m01_002"],
            "MV00_05.msbt.zst": ["MID_MV00_05_m001", "MID_MV00_05_m002", "MID_MV00_05_m003"],
        }
        for filename, labels in expected.items():
            for label in labels:
                with self.subTest(file=filename, label=label):
                    entry = data[filename][label]
                    self.assertRegex("".join(entry["parts"]), r"[\u0621-\u064a]")
                    self.assertRegex(entry["source_sha256"], r"^[0-9a-f]{64}$")


class ArabicPreviewTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.settings = self.root / "profile"
        self.catalogs = self.root / "fagrishot/arabic_data"
        self.catalogs.mkdir(parents=True)
        (self.root / "fonts").mkdir()
        (self.root / "fonts/DejaVuSans.ttf").write_bytes(b"font fixture")
        for name in ("arabic.py", "arabic_font.py", "localization.py", "localization_art.py"):
            (self.root / "fagrishot" / name).touch()
        self.entry = {"source_sha256": "1"*64, "parts": ["اختبار"]}
        (self.catalogs / "first.json").write_text(json.dumps({"System.msbt.zst": {"one": self.entry}}))
        (self.catalogs / "second.json").write_text(json.dumps({"System.msbt.zst": {"two": self.entry}}))

    def test_shared_catalog_and_corrupt_preference_recovery(self):
        self.settings.mkdir()
        (self.settings / "arabic.json").write_text("{")
        state = arabic.status(self.settings, self.root)
        self.assertTrue(state["available"])
        self.assertFalse(state["enabled"])
        self.assertIn("2 selected", state["coverage"])
        self.assertTrue(arabic.set_enabled(self.settings, self.root, True)["enabled"])
        self.assertFalse(arabic.set_enabled(self.settings, self.root, False)["enabled"])
        (self.catalogs / "duplicate.json").write_text(json.dumps({"System.msbt.zst": {"one": self.entry}}))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            arabic.status(self.settings, self.root)

    def test_immutable_overlay_reuse_and_recovery_when_file_changes(self):
        romfs, private = self.root / "input", self.root / "private"
        def messages(source, output, changes, *, source_hashes):
            self.assertEqual(set(changes["System.msbt.zst"]), {"one", "two"})
            self.assertEqual(source_hashes["System.msbt.zst"]["one"], "1"*64)
            folder = output / "Msg/USen"
            folder.mkdir(parents=True)
            (folder / "System.msbt.zst").write_bytes(b"synthetic message")
            return 1
        def fonts(source, output, characters, face):
            self.assertEqual(characters, set("اختبار"))
            folder = output / "Font"
            folder.mkdir()
            for name in FONT_NAMES:
                (folder / name).write_bytes(b"synthetic font")
        def artwork(source, output, face):
            target = output / "Layout/Textures/Tutorial/USen/TUT_Hub_AutoSave_001.bntx.zst"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"synthetic autosave graphic")
        with (patch.object(arabic, "write_overrides", side_effect=messages) as generate,
              patch("fagrishot.arabic_font.visual_text", side_effect=lambda s: s),
              patch("fagrishot.arabic_font.patch_fonts", side_effect=fonts),
              patch("fagrishot.localization_art.patch_autosave_art", side_effect=artwork)):
            first = arabic.prepare(romfs, private, "verified", self.root)
            self.assertEqual(first, arabic.prepare(romfs, private, "verified", self.root))
            self.assertEqual(generate.call_count, 1)
            (first / "Arabic/romfs/Font" / FONT_NAMES[0]).write_bytes(b"damaged")
            second = arabic.prepare(romfs, private, "verified", self.root)
            self.assertNotEqual(first, second)
            self.assertEqual(generate.call_count, 2)

    def test_player_switch_changes_only_the_runtime_environment(self):
        dirs = {key: self.root / key for key in ("settings", "saves", "logs")}
        dirs["settings"].mkdir()
        ini = dirs["settings"] / "fefw.ini"
        ini.write_text("[system]\nlanguage = French\n")
        mods = self.root / "private/overlay"
        with (patch.object(cli, "ROOT", self.root), patch.object(arabic, "prepare", return_value=mods) as build,
              patch("fagrishot.saves.snapshot"), patch.object(cli.subprocess, "run") as game):
            for active in (True, False):
                arabic.set_enabled(dirs["settings"], self.root, active)
                cli.launch({"romfs": self.root / "input"}, self.root / "cache", dirs)
                env = game.call_args.kwargs["env"]
                self.assertEqual(env.get("NXRT_SYSTEM_LANGUAGE"), "AmericanEnglish" if active else None)
                self.assertEqual(env.get("NXRT_FS_MODS"), str(mods) if active else None)
                self.assertIn("language = French", ini.read_text())
            build.assert_called_once()
