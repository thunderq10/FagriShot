# SPDX-License-Identifier: GPL-3.0-or-later
"""Static fixtures for the shared Python NSO resource boundary."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'recomp'))

from nxrecomp import nso  # noqa: E402
from tools.nso import nso_unpack  # noqa: E402
from tools.shaders.ampere import extract_corpus  # noqa: E402


def plain_nso():
    payloads = (b'text', b'read-only', b'data')
    header = bytearray(nso.NSO_HEADER_SIZE)
    header[:4] = b'NSO0'
    body = bytearray()
    for i, payload in enumerate(payloads):
        file_offset = len(header) + len(body)
        memory_offset = i * 0x1000
        struct.pack_into('<III', header, 0x10 + i * 0x10, file_offset, memory_offset, len(payload))
        struct.pack_into('<I', header, 0x60 + i * 4, len(payload))
        header[0xA0 + i * 0x20:0xC0 + i * 0x20] = hashlib.sha256(payload).digest()
        body += payload
    return bytes(header + body), payloads


class NsoBoundsTests(unittest.TestCase):
    def test_valid_plain_segments_are_preserved(self):
        blob, payloads = plain_nso()
        info = nso.nso_info(blob)
        self.assertEqual([segment['data'] for segment in info['segments']], list(payloads))
        self.assertEqual(nso_unpack.unpack(blob)[0], blob)
        image = extract_corpus.nso_image(blob)
        self.assertEqual([image[i * 0x1000:i * 0x1000 + len(p)] for i, p in enumerate(payloads)], list(payloads))

    def test_oversize_segment_is_rejected_everywhere(self):
        blob, _ = plain_nso()
        hostile = bytearray(blob)
        struct.pack_into('<I', hostile, 0x18, nso.MAX_NSO_SEGMENT_SIZE + 1)
        for consume in (nso.nso_info, lambda data: nso_unpack.unpack(data)[0], extract_corpus.nso_image):
            with self.subTest(consumer=consume):
                with self.assertRaisesRegex(ValueError, 'segment 0 size .* exceeds the limit'):
                    consume(hostile)

    def test_sparse_and_overlapping_layouts_are_rejected(self):
        blob, _ = plain_nso()
        sparse = bytearray(blob)
        struct.pack_into('<I', sparse, 0x34, 0x1000 + len(b'read-only') + nso.MAX_NSO_SEGMENT_GAP + 1)
        with self.assertRaisesRegex(ValueError, 'segment 2 gap .* exceeds the limit'):
            nso.nso_layout(sparse)
        overlap = bytearray(blob)
        struct.pack_into('<I', overlap, 0x24, 2)
        with self.assertRaisesRegex(ValueError, 'overlap or are out of order'):
            nso.nso_layout(overlap)

    def test_file_extents_and_bss_are_bounded(self):
        blob, _ = plain_nso()
        outside = bytearray(blob)
        struct.pack_into('<I', outside, 0x10, len(blob))
        with self.assertRaisesRegex(ValueError, 'segment 0 is outside the file'):
            nso.nso_layout(outside)
        overlap = bytearray(blob)
        struct.pack_into('<I', overlap, 0x20, nso.NSO_HEADER_SIZE)
        with self.assertRaisesRegex(ValueError, 'segments overlap in the file'):
            nso.nso_layout(overlap)
        excessive_bss = bytearray(blob)
        struct.pack_into('<I', excessive_bss, 0x3C, nso.MAX_NSO_IMAGE_SIZE)
        with self.assertRaisesRegex(ValueError, 'image and BSS span .* exceeds the limit'):
            nso.nso_layout(excessive_bss)

    def test_zbic_frame_size_precedes_decoder(self):
        fake_zbic = mock.Mock()
        fake_zstandard = mock.Mock()
        fake_zstandard.ZstdError = RuntimeError
        fake_zstandard.frame_content_size.return_value = 5
        with mock.patch.dict(sys.modules, {'zbic': fake_zbic, 'zstandard': fake_zstandard}):
            with self.assertRaisesRegex(ValueError, 'frame declares 5 bytes'):
                nso_unpack._decompress(b'frame', 4, True)
        fake_zbic.decompress.assert_not_called()


if __name__ == '__main__':
    unittest.main()
