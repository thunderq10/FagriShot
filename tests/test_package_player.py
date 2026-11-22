import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_player as player
import verify_player


class PlayerDistribution(unittest.TestCase):
    def test_names_reject_private_inputs_traversal_and_case_collisions(self):
        with ExitStack() as stack:
            payload = player.Payload(stack)
            for path in ("../outside", "C:/state", "tools\\file", "a//b", "work/private/game.exe",
                         "bin/FagriShot.exe", "data/romfs/file", "tools/game.spv", "saves/system"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    payload.add(path, b"x")
            payload.add("tools/Tool.exe", b"fixture")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                payload.add("Tools/tool.exe", b"other")

    def test_archive_is_deterministic_and_hashes_every_payload_file(self):
        with tempfile.TemporaryDirectory() as area, ExitStack() as stack:
            path = Path(area)
            payload = player.Payload(stack)
            payload.add("test/a.txt", b"original save-tool fixture")
            payload.add("START-HERE.txt", "العربية".encode("utf-8"))
            first, second = path / "one.zip", path / "two.zip"
            for target in (first, second):
                player.write_archive(payload, target, "a" * 40, False)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                record = json.loads(archive.read("player-package.json"))
                self.assertEqual(set(archive.namelist()), {"player-package.json", *record["files"]})
                for name, expected in record["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            payload.add("assets/apparently-innocent-file", b"NSO0" + bytes(64))
            with self.assertRaisesRegex(ValueError, "game-data header"):
                player.write_archive(payload, second, "a" * 40, False)

    def test_developer_dll_must_resolve_inside_application(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area)
            (root / "helper.exe").write_bytes(b"PE fixture")
            with patch.object(verify_player, "imports", return_value={"kernel32.dll", "msvcp140.dll"}):
                with self.assertRaisesRegex(ValueError, "msvcp140.dll"):
                    verify_player.verify_imports(root, ["helper.exe"])
                (root / "msvcp140.dll").touch()
                self.assertEqual(verify_player.verify_imports(root, ["helper.exe"]), (1, []))
