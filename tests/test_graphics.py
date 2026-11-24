from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fagrishot.graphics import Ini, STREAMLINE, preferences
from fagrishot.storage import build_lock


class GraphicsPreferences(unittest.TestCase):
    def setUp(self):
        area = TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        self.settings = self.root / "profile"
        self.game = self.root / "work/private/game"
        self.settings.mkdir()
        self.ini = self.settings / "fefw.ini"

    def graphics(self, **changes):
        return preferences(self.settings, self.game, self.root, changes)

    def test_preserves_existing_settings_comments_and_fixed_render(self):
        self.ini.write_bytes(b'; keep\r\n[FEFW]\r\nOutput = "4k" ; television\r\nRender = 800x600\r\n'
                             b'[custom]\r\npath = "folder #1"\r\n[FeFw]\r\nFPS120 = true\r\n')
        data = self.graphics()
        self.assertEqual((data["output"], data["fps"], data["fixed_render"]), ("2160p", "120", "800x600"))
        self.graphics(output="1440p", fps="60")
        self.assertIn(b'Output = 1440p ; television\r\n', self.ini.read_bytes())
        self.assertIn(b'path = "folder #1"\r\n', self.ini.read_bytes())
        self.assertEqual(self.graphics()["fixed_render"], "800x600")
        self.graphics(scale="1.0")
        self.assertEqual((self.graphics()["scale"], self.graphics()["fixed_render"]), ("1.0", ""))
        self.assertEqual(Ini(self.ini).get("fefw", "fps120"), "0")

    def test_vendor_discovery_matches_runtime_search_order(self):
        folder = self.root / "upscalers/streamline"
        folder.mkdir(parents=True)
        for dll in STREAMLINE:
            (folder / dll).touch()
        self.assertTrue(self.graphics(upscaler="dlss")["installed"]["dlss"])
        nearer = self.game / "upscalers/streamline"
        nearer.mkdir(parents=True)
        (nearer / STREAMLINE[0]).touch()
        self.assertFalse(self.graphics()["installed"]["dlss"])
        previous = self.ini.read_bytes()
        with self.assertRaisesRegex(ValueError, "DLSS files are missing"):
            self.graphics(output="2160p", upscaler="dlss")
        self.assertEqual(self.ini.read_bytes(), previous)
        (self.settings / "controls.ini").write_text(f'[upscale]\ndlss_dll = "{folder / STREAMLINE[0]}"\n')
        self.assertTrue(self.graphics()["installed"]["dlss"])

    def test_no_read_side_effects_and_no_write_while_playing(self):
        self.assertEqual(self.graphics()["fps"], "30")
        self.assertFalse(self.ini.exists())
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.graphics(scale="9.0")
        with build_lock(self.settings / "save.lock"), self.assertRaisesRegex(ValueError, "Another FagriShot"):
            self.graphics(fps="60")
        self.assertFalse(self.ini.exists())

    def test_custom_output_and_scale_survive_changes_to_another_setting(self):
        self.ini.write_text("[fefw]\noutput = 2304x1296\nrender_ratio = 0.88\n")
        selected = self.graphics(reflections="off")
        self.assertEqual(selected["output"], "2304x1296")
        self.assertEqual(selected["scale"], "0.88")
        self.assertEqual(selected["reflections"], "off")

    def test_experimental_ray_queries_and_original_reflections_are_independent(self):
        self.assertEqual(self.graphics()["ray_reflections"], "off")
        selected = self.graphics(reflections="reduced", ray_reflections="on")
        self.assertEqual(selected["reflections"], "reduced")
        self.assertEqual(selected["ray_reflections"], "on")
        self.assertEqual(Ini(self.ini).get("experimental", "rt_visible_reflections"), "true")
        self.assertEqual(self.graphics(ray_reflections="off")["reflections"], "reduced")
