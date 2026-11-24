import os
from pathlib import Path
import unittest

from fagrishot import arabic_font as arabic


@unittest.skipUnless(os.name == "nt", "Windows ICU and font APIs")
class ArabicGlyphTests(unittest.TestCase):
    def test_lines_numbers_punctuation_and_lam_alef(self):
        self.assertEqual(arabic.visual_text("لا!\n123\n"), "!\ufefb\n123\n")
        self.assertIn("E34", arabic.visual_text("الدائرة E34"))
        self.assertEqual(arabic.visual_text("؟"), "؟")

    def test_raster_uses_connected_forms_from_packaged_face(self):
        face = Path(__file__).resolve().parents[1] / "runtime/assets/fonts/DejaVuSans.ttf"
        codes = sorted(map(ord, set(arabic.visual_text("بب"))))
        self.assertEqual(codes, [0xfe90, 0xfe91])
        with arabic._private_font(face) as g:
            glyphs = arabic._raster(g, codes, 25, 30, 25)
        for left, width, advance, top, height, pixels in glyphs.values():
            self.assertTrue(0 < advance <= 25 and 0 < width <= 25)
            self.assertGreaterEqual(top, 0)
            self.assertLessEqual(top + height, 30)
            self.assertGreater(max(pixels), 32)
            self.assertEqual(len(pixels), height * ((width + 3) & ~3))

    def test_swizzled_blocks_cover_each_layer_once(self):
        # A full 256x1024 Cyber font layer contains 64 x 256 independent BC4 blocks.
        offsets = {arabic._block_offset(x, y, 256, 16)
                   for y in range(256) for x in range(64)}
        self.assertEqual(offsets, set(range(0, 256 * 1024 // 2, 8)))
        self.assertEqual(arabic._bc4(bytes([64] * 16)), bytes.fromhex("ff00000000000000"))
