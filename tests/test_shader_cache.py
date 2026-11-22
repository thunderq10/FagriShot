import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from fagrishot import shaders


class ShaderCache(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.hash = "0123456789abcdef"
        self.results = self.root / "results.tsv"
        self.fields = "hash\tgroup\tstage\tstages_using\tstatus\tspv_bytes\tresources\terror\n"
        self.requested = {self.hash: ("cs", 1)}

    def write_row(self, state):
        row = f"{self.hash}\tcs\tcompute\t1\t{state}\t20\t\t\n"
        self.results.write_text(self.fields + row, encoding="utf-8")

    def test_batch_success_with_any_rejected_status_blocks_cache(self):
        for state in ("unsupported", "invalid", "crash", "no-output", "exception"):
            with self.subTest(state=state):
                self.write_row(state)
                with self.assertRaisesRegex(ValueError, state):
                    shaders._check_outputs(self.root, self.requested, "unused", 1)

    def test_missing_rows_stale_and_truncated_outputs(self):
        self.results.write_text(self.fields, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sets disagree"):
            shaders._check_outputs(self.root, self.requested, "unused", 1)
        self.write_row("ok")
        module = self.root / (self.hash + ".spv")
        module.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            shaders._check_outputs(self.root, self.requested, "unused", 1)
        module.write_bytes(bytes(20))
        (self.root / "stale.spv").write_bytes(bytes(20))
        with self.assertRaisesRegex(ValueError, "sets disagree"):
            shaders._check_outputs(self.root, self.requested, "unused", 1)

    @unittest.skipUnless(shutil.which("spirv-val"), "Windows or Linux SPIRV-Tools is required.")
    def test_native_validator_rejects_invalid_module_despite_ok_row(self):
        self.write_row("ok")
        (self.root / (self.hash + ".spv")).write_bytes(bytes(20))
        with self.assertRaisesRegex(ValueError, "failed vulkan1.3 validation"):
            shaders._check_outputs(self.root, self.requested, shaders._validator(), 1)

    @unittest.skipUnless(shaders.HARNESS.is_file() and shaders.VENV.is_file() and shutil.which("spirv-val"),
                         "Build the private Python environment and shader harness to exercise the full local toolchain.")
    def test_real_extractor_batch_shader_validation_and_repeat(self):
        # A synthetic NVN2 control and GLSLC container, matching extract_corpus.glslc_outputs and TranslateNvn2.
        code = struct.pack("<QQQQ", 0x7B1D, 0, 0x794D, 7 << 23)
        ctrl = bytearray(0xD8)
        struct.pack_into("<II", ctrl, 0, 0x98761234, 201)
        struct.pack_into("<I", ctrl, 0x38, len(code))
        struct.pack_into("<H", ctrl, 0x56, 5)
        struct.pack_into("<III", ctrl, 0x58, 32, 1, 1)
        prefix = 0xA8 + 0x98
        blob = bytearray(prefix) + ctrl + code
        struct.pack_into("<I", blob, 0, 0x19866891)
        struct.pack_into("<I", blob, 0x5C, len(blob))
        struct.pack_into("<I", blob, 0x64, 1)
        struct.pack_into("<III", blob, 0xA8, len(code) + len(ctrl), prefix, 0)
        struct.pack_into("<IIII", blob, 0xA8 + 0x30, 0, len(ctrl), len(code), len(ctrl))
        prepared = self.root / "prepared-exefs"
        prepared.mkdir()
        main = bytearray(0x100) + blob
        main[:4] = b"NSO0"
        struct.pack_into("<III", main, 0x10, 0x100, 0, len(blob))
        (prepared / "main").write_bytes(main)
        romfs = self.root / "romfs"
        romfs.mkdir()
        exe = self.root / "synthetic-host-marker"
        exe.write_bytes(b"stand-in cache identity only")
        dirs = {"caches": self.root / "cache"}
        inv = {k: {"tree_sha256": hashlib.sha256(k.encode()).hexdigest()} for k in ("exefs", "romfs")}
        with patch.object(shaders, "EXE", exe), patch.object(shaders, "PRIVATE", self.root):
            cache = shaders.prepare_shaders({"romfs": romfs}, inv, dirs, 1)
            self.assertEqual(cache, shaders.require_ready(inv, dirs))
            self.assertEqual(cache, shaders.prepare_shaders({"romfs": romfs}, inv, dirs, 1))
            marker = json.loads((cache / "ready.json").read_text(encoding="utf-8"))
            self.assertEqual(set(marker["modules"]), {hashlib.sha1(code).hexdigest()[:16]})
            next((cache / "spirv").glob("*.spv")).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                shaders.require_ready(inv, dirs)
            self.assertEqual(cache, shaders.prepare_shaders({"romfs": romfs}, inv, dirs, 1))
            self.assertEqual(cache, shaders.require_ready(inv, dirs))
            exe.write_bytes(b"new ABI marker")
            self.assertNotEqual(cache, shaders.shader_cache(inv, dirs))
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                shaders.require_ready(inv, dirs)
