import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
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

    def write_row(self, state, size=20, error=""):
        row = f"{self.hash}\tcs\tcompute\t1\t{state}\t{size}\t\t{error}\n"
        self.results.write_text(self.fields + row, encoding="utf-8")

    def test_batch_success_with_any_rejected_status_blocks_cache(self):
        for state in ("unsupported", "invalid", "crash", "no-output", "exception"):
            with self.subTest(state=state):
                self.write_row(state)
                with self.assertRaisesRegex(ValueError, state):
                    shaders._check_outputs(self.root, self.requested, "unused", 1)

    def test_only_verified_subsdk0_cuda_can_report_unsupported_without_output(self):
        self.write_row("unsupported", size=0, error="unsupported instruction")
        modules = shaders._check_outputs(self.root, self.requested, "unused", 1, {self.hash})
        self.assertEqual(modules, {})
        for source in ("another-module", "subsdk0/path", "Subsdk0"):
            with self.subTest(source=source):
                names = self.cuda_classification(source)
                self.assertEqual(names, set())
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    shaders._check_outputs(self.root, self.requested, "unused", 1, names)
        self.write_row("invalid", size=0, error="bad module")
        with self.assertRaisesRegex(ValueError, "invalid"):
            shaders._check_outputs(self.root, self.requested, "unused", 1, {self.hash})
        self.write_row("unsupported", size=0, error="unsupported instruction")
        (self.root / (self.hash + ".spv")).write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            shaders._check_outputs(self.root, self.requested, "unused", 1, {self.hash})

    def cuda_classification(self, source="subsdk0", *, overlap=False, kernel_offset="0x20", magic=0x41445543):
        descriptor = struct.pack("<17I", magic, 1, 88, 16, *([0] * 13))
        ctrl = hashlib.sha1(descriptor).hexdigest()[:16]
        (self.root / "ctrl").mkdir(exist_ok=True)
        (self.root / "ctrl" / (ctrl + ".bin")).write_bytes(descriptor)
        header = "code_sha1_16\tsource\tentry_off\tglslc_off\tcontainer\tstage\tctrl_sha1_16\tglslc_sha1_16\tcode_size\n"
        row = f"{self.hash}\t{source}\t0x10\t0x20\tCUBIN\tcs\t{ctrl}\t-\t0x10\n"
        if overlap:
            row += f"{self.hash}\tother.bnsh\t0x10\t0x20\tBNSH\tcs\t{ctrl}\t-\t0x10\n"
        (self.root / "manifest.tsv").write_text(header + row, encoding="utf-8")
        (self.root / "cuda_kernels.tsv").write_text("code_sha1_16\tsource\tfatbin_off\telf_off\tsm\n" +
            f"{self.hash}\t{source}\t0x10\t{kernel_offset}\t88\n", encoding="utf-8")
        return shaders._console_dlss_kernels(self.root)

    def test_cuda_classification_requires_exact_occurrences_and_descriptor(self):
        self.assertEqual(self.cuda_classification(), {self.hash})
        with self.assertRaisesRegex(ValueError, "kernel table disagrees"):
            self.cuda_classification(kernel_offset="0x30")
        with self.assertRaisesRegex(ValueError, "descriptor header"):
            self.cuda_classification(magic=0x98761234)
        with self.assertRaisesRegex(ValueError, "also appears"):
            self.cuda_classification(overlap=True)

    def test_program_list_checks_each_referenced_runtime_stage(self):
        path = self.root / "programs.bin"
        path.write_bytes(b"MPRPRG01" + struct.pack("<IIQQQ", 1, 0, 1, 2, 3))
        jobs = {f"{i:016x}": (stage, 1) for i, stage in enumerate(("vs", "ps", "gs"), 1)}
        self.assertEqual(shaders._check_programs(path, jobs, set()), 1)
        with self.assertRaisesRegex(ValueError, "incorrect ps"):
            shaders._check_programs(path, jobs, {f"{2:016x}"})
        jobs[f"{3:016x}"] = ("cs", 1)
        with self.assertRaisesRegex(ValueError, "incorrect gs"):
            shaders._check_programs(path, jobs, set())
        del jobs[f"{1:016x}"]
        with self.assertRaisesRegex(ValueError, "missing or incorrect vs"):
            shaders._check_programs(path, jobs, set())

    def test_missing_rows_stale_and_truncated_outputs(self):
        self.results.write_text(self.fields + f"{self.hash}\tcs\tcompute\t1\tok", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Incomplete shader table row"):
            shaders._check_outputs(self.root, self.requested, "unused", 1)
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

    @unittest.skipUnless(shutil.which("spirv-as") and shutil.which("spirv-val"), "SPIRV-Tools is required.")
    def test_validated_retry_may_retain_first_attempt_diagnostic(self):
        module = self.root / (self.hash + ".spv")
        instructions = """OpCapability Shader
OpMemoryModel Logical GLSL450
OpEntryPoint GLCompute %main "main"
OpExecutionMode %main LocalSize 1 1 1
%void = OpTypeVoid
%func = OpTypeFunction %void
%main = OpFunction %void None %func
%label = OpLabel
OpReturn
OpFunctionEnd
"""
        subprocess.run([shutil.which("spirv-as"), "--target-env", shaders.TARGET, "-o", module, "-"],
                       input=instructions, text=True, check=True)
        self.write_row("ok-retry", size=module.stat().st_size, error="Exception from the first translation attempt")
        self.assertEqual(shaders._check_outputs(self.root, self.requested, shaders._validator(), 1),
                         {self.hash: hashlib.sha256(module.read_bytes()).hexdigest()})
        module.write_bytes(bytes(module.stat().st_size))
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
        asset_dir = romfs / "motor_asset_re"
        asset_dir.mkdir(parents=True)
        payload = b"_S2G" + blob
        entry = bytearray(0x30)
        entry[:8] = b"IDRK0000"
        struct.pack_into("<QQQ", entry, 8, len(entry) + len(payload), len(payload), len(payload))
        asset = asset_dir / "音声-é.fdata"
        asset.write_bytes(entry + payload)
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
            self.assertEqual(marker["identity"]["schema"], 2)
            report_file = cache / "shader-report.json"
            report = json.loads(report_file.read_text(encoding="utf-8"))
            self.assertEqual(report["runtime_shaders"], 1)
            self.assertEqual(report["host_replaced_subsdk0_cuda"], {})
            self.assertEqual(marker["tables"]["shader-report.json"], hashlib.sha256(report_file.read_bytes()).hexdigest())
            manifest = (cache / "corpus/manifest.tsv").read_text(encoding="utf-8")
            self.assertIn(asset.name, manifest)
            next((cache / "spirv").glob("*.spv")).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                shaders.require_ready(inv, dirs)
            self.assertEqual(cache, shaders.prepare_shaders({"romfs": romfs}, inv, dirs, 1))
            self.assertEqual(cache, shaders.require_ready(inv, dirs))
            exe.write_bytes(b"new ABI marker")
            self.assertNotEqual(cache, shaders.shader_cache(inv, dirs))
            with self.assertRaisesRegex(ValueError, "missing or changed"):
                shaders.require_ready(inv, dirs)
