import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fagrishot import player_build as player
from fagrishot.inputs import inspect_exefs
from test_inputs import npdm, nso


class PortableBuild(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        private = self.root / "work/private"
        self.exe = private / "game/FagriShot.exe"
        self.stamp = private / "linked.json"
        self.original = self.root / "my game"
        self.original.mkdir()
        for name in player.MODULES:
            (self.original / name).write_bytes(nso(name.encode()))
        (self.original / "main.npdm").write_bytes(npdm())
        self.input = inspect_exefs(self.original)
        hashes = {name: row["sha256"] for name, row in self.input["modules"].items()}
        hashes["main.npdm"] = self.input["program"]["sha256"]
        self.profile = {"id": "fixture", "program_id": self.input["program"]["program_id"],
                        "version": "fixture", "update_version": 1, "main_build_id": bytes(range(20)).hex(),
                        "main_plain_sha256": hashes["main"], "sha256": hashes}
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"schema": 1, "candidates": [self.profile], "supported": []}))
        self.enterContext(patch.multiple(player, ROOT=self.root, PRIVATE=private, EXE=self.exe, CATALOG=catalog))
        self.revision = self.enterContext(patch.object(player, "source_state", return_value={"commit": "fixture"}))
        self.backend = self.enterContext(patch.object(player, "run", side_effect=self.compile))
        self.resources = {"fefw_rt.dll": b"runtime", "fonts/DejaVuSans.ttf": b"font", "locales/en.ini": b"locale",
                          "tools/build/fefw_build.py": b"driver", "tools/build/sdk/fefw_rt.lib": b"import",
                          "tools/build/zig/zig.exe": b"compiler", "tools/python/python.exe": b"python"}
        self.write_package()

    def write_package(self):
        files = {}
        for name, data in self.resources.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[name] = hashlib.sha256(data).hexdigest()
        (self.root / "player-package.json").write_text(json.dumps({"files": files}))

    def compile(self, args, env, log):
        self.assertEqual(args[:5], [self.root / "tools/python/python.exe", "-I", "-X", "utf8",
                                     self.root / "tools/build/fefw_build.py"])
        self.assertEqual(args[-4:], ["--cpu", "compat", "--jobs", "2"])
        self.stage = Path(args[args.index("--game") + 1])
        shutil.copytree(self.original, self.stage / "exefs", dirs_exist_ok=True)
        (self.stage / "build").mkdir(exist_ok=True)
        exe = self.stage / "fefw.exe"
        exe.write_bytes(b"original test executable")
        (self.stage / "fefw.sites").write_bytes(b"original test sites")
        done = {"exe_sha256": player.sha(exe), "main_build_id": self.profile["main_build_id"],
                "sha256": self.profile["sha256"], "tools": {"cpu": "compat", "modules": [[n] for n in player.MODULES]}}
        (self.stage / "build/done.json").write_text(json.dumps(done))
        log.write_text("synthetic compiler")

    def build(self):
        player.build_player(self.original, self.input, 2)

    def test_install_and_repair_from_verified_link(self):
        self.build()
        receipt = json.loads(self.stamp.read_bytes())
        self.assertEqual(receipt["input"], self.input)
        self.assertEqual(receipt["prepared_exefs"], self.input["exefs"])
        self.assertEqual(receipt["exe_sha256"], player.sha(self.exe))
        self.assertEqual(set(receipt["runtime_files"]), {"fefw_rt.dll", "fefw.sites", "fonts/DejaVuSans.ttf", "locales/en.ini"})
        self.build()
        (self.exe.parent / "fefw_rt.dll").write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "fefw_rt.dll"):
            player.verify_player_runtime(receipt)
        self.build()
        self.backend.assert_called_once()
        player.verify_player_runtime(json.loads(self.stamp.read_bytes()))
        self.assertEqual(inspect_exefs(self.original), self.input)

    def test_compiler_change_gets_new_workspace_but_frontend_change_reuses_link(self):
        self.build()
        old_stage = self.stage
        self.revision.return_value = {"commit": "frontend-change"}
        self.build()
        self.backend.assert_called_once()
        self.assertEqual(json.loads(self.stamp.read_bytes())["source"], self.revision.return_value)
        self.revision.return_value = {"commit": "compiler-change"}
        self.resources["tools/build/zig/zig.exe"] = b"new compiler"
        self.write_package()
        self.build()
        self.assertEqual(self.backend.call_count, 2)
        self.assertNotEqual(old_stage, self.stage)

    def test_changed_cache_is_recompiled_before_repair(self):
        self.build()
        (self.stage / "fefw.sites").write_bytes(b"stale")
        self.exe.unlink()
        self.build()
        self.assertEqual(self.backend.call_count, 2)
        self.assertEqual((self.exe.parent / "fefw.sites").read_bytes(), b"original test sites")

    def test_failed_or_wrong_link_is_never_published(self):
        self.backend.side_effect = ValueError("compiler failed")
        with self.assertRaisesRegex(ValueError, "compiler failed"):
            self.build()
        self.assertFalse(self.stamp.exists())
        def wrong_compile(*args, **kwargs):
            self.compile(*args, **kwargs)
            (self.stage / "fefw.exe").write_bytes(b"wrong output")
        self.backend.side_effect = wrong_compile
        with self.assertRaisesRegex(ValueError, "linker outputs"):
            self.build()
        self.assertFalse(self.stamp.exists())
        self.assertFalse(self.exe.exists())
