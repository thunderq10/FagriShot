import base64
import struct
import unittest

from fagrishot.localization import LocalizationError
from fagrishot.localization_art import _encode_ink, _offset, _paint_captions, _palette_color


class TutorialLetteringTests(unittest.TestCase):
    def test_sampling_uses_the_original_block_and_changes_only_the_label(self):
        data = bytearray(4096 + 8192)
        donor = _encode_ink((43, 81, 19, 255), bytes(16))
        data[_offset(0, 0):_offset(0, 0)+16] = donor
        blank = base64.b64encode(bytes(16)).decode()
        refs = base64.b64encode(struct.pack("<H", 0)).decode()
        output = _paint_captions(bytes(data), [[1, 0, 1, 1, 0, 0xffffff, blank, refs]])
        offset = _offset(1, 0)
        self.assertEqual(output, data[:offset] + donor + data[offset+16:])
        self.assertTrue(all(abs(a-b) < 2 for a, b in zip(_palette_color(donor), (43, 81, 19, 255))))
        with self.assertRaisesRegex(LocalizationError, "sample location"):
            _paint_captions(data, [[1, 0, 1, 1, 0, 0xffffff, blank, base64.b64encode(b"\xff\xff").decode()]])
