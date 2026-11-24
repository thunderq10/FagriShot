from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nxrecomp import program  # noqa: E402


class GeneratedStringTests(unittest.TestCase):
    def test_cpp_byte_string_uses_fixed_escapes_for_every_byte(self):
        value = bytes(range(256)).decode("latin-1")
        expected = '"' + ''.join('\\%03o' % byte for byte in range(256)) + '"'
        encoded = program.cpp_byte_string(value)
        self.assertEqual(encoded, expected)
        self.assertTrue(encoded.isascii())
        self.assertNotIn("\n", encoded)
        self.assertNotIn("\r", encoded)

    def test_dynamic_symbol_names_cannot_change_cpp_tokens(self):
        attack = '\\\n"; static_assert(false); //\r'

        class Image:
            name = "main"
            base = 0x7100000000
            nsym = 2
            symtab_is_dynamic = True

            @staticmethod
            def symbols():
                return [(attack, 0, 0, 0, 0, 0), ("ordinary_symbol", 0, 0, 1, 0x20, 0)]

            @staticmethod
            def sym_value(value):
                return value

        env = SimpleNamespace(img=Image(), overrides={}, module_index=0, base=Image.base, text_hi=Image.base + 0x1000)
        with tempfile.TemporaryDirectory() as outdir, patch.object(program, "emit_eh_tables", return_value="nullptr"):
            program.write_module_files(env, outdir, [], [], [])
            generated = (Path(outdir) / "module_main.cpp").read_text(encoding="utf-8")

        self.assertIn('  { %s, 0x0ULL, 0 },' % program.cpp_byte_string(attack), generated)
        self.assertIn('  { %s, 0x7100000020ULL, 1 },' % program.cpp_byte_string("ordinary_symbol"), generated)
        self.assertNotIn("static_assert", generated)
        self.assertNotIn("\r", generated)


if __name__ == "__main__":
    unittest.main()
