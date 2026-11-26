import json
import hashlib
from pathlib import Path
import tempfile
import unicodedata
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


class PhotographedScreenTests(unittest.TestCase):
    """The title footer and the Threat Range tutorial, as stored in the reviewed catalog."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.data = arabic.catalog_data(arabic.assets(root)[0])

    def test_no_reviewed_entry_has_a_doubled_ta_marbuta(self):
        # A repeated mechanical edit once turned «جندية» into «جنديةة». No Arabic word doubles this letter.
        for table, entries in self.data.items():
            for label, record in entries.items():
                written = record["parts"] + [s for pair in record.get("choices", {}).values() for s in pair]
                self.assertFalse(any("ةة" in text for text in written), (table, label))

    def test_title_footer_keeps_the_notice_and_translates_the_version_label(self):
        system = self.data["System.msbt.zst"]
        notice = system["MID_Title_Previlefe"]
        self.assertEqual(notice["parts"][0][0], "©")
        self.assertNotIn("flow", notice)
        self.assertIsNone(arabic.ARABIC_SCRIPT.search(notice["parts"][0]))
        changes, _, _ = arabic.translate({"System.msbt.zst": {"MID_Title_Previlefe": notice}}, lambda text: text[::-1])
        self.assertEqual(changes["System.msbt.zst"]["MID_Title_Previlefe"]["parts"], notice["parts"])
        version = system["MID_Title_Version"]
        self.assertEqual(version.get("flow"), "auto")
        self.assertRegex(version["parts"][0], r"^[ء-ي]+ $")
        self.assertIn("__Number.bffnt.zst", FONT_NAMES)

    def test_threat_range_pages_keep_both_highlights_and_one_enemy_apart_from_all(self):
        tutorial = self.data["Tutorial.msbt.zst"]
        one = tutorial["MID_TUT_Bmap_HazardRange_m01_002"]
        every = tutorial["MID_TUT_Bmap_HazardRange_m01_003"]
        self.assertEqual((len(one["parts"]), one.get("flow")), (5, "auto"))
        always, press = one["parts"][1], one["parts"][3]
        self.assertTrue(always.strip() and press.strip())
        self.assertTrue(press.startswith("اضغط") and press.endswith(" "), "The icon follows its verb.")
        self.assertEqual("".join(one["parts"]).count("\n"), 1)
        self.assertIn("\n(", one["parts"][4], "The second press is a sentence of its own.")
        self.assertIn("هذا العدو", one["parts"][0])
        self.assertIn("قوات العدو", every["parts"][3])
        for label in ("MID_TUT_Bmap_HazardRange_t", "MID_TUT_Bmap_HazardRange_m01_001",
                      "MID_TUT_Bmap_HazardRange_m01_002", "MID_TUT_Bmap_HazardRange_m01_003"):
            text = "".join(tutorial[label]["parts"])
            self.assertIn("نطاق", text)
            self.assertFalse(any(unicodedata.category(c) == "Mn" for c in text), "The bitmap font has no marks.")
            self.assertLessEqual(max(map(len, text.split("\n"))), 56)


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
        for name in ("arabic.py", "arabic_campaign.py", "arabic_font.py", "localization.py", "localization_flow.py",
                     "localization_art.py", "localization_logo.py"):
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
        self.assertEqual((state["covered"], state["reviewed"], state["machine_draft"]), (2, 2, 0))
        self.assertTrue(arabic.set_enabled(self.settings, self.root, True)["enabled"])
        self.assertFalse(arabic.set_enabled(self.settings, self.root, False)["enabled"])
        (self.catalogs / "duplicate.json").write_text(json.dumps({"System.msbt.zst": {"one": self.entry}}))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            arabic.status(self.settings, self.root)

    def test_immutable_overlay_reuse_and_recovery_when_file_changes(self):
        romfs, private = self.root / "input", self.root / "private"
        def messages(source, output, tables, *, prepare_entries, include_unchanged):
            [(name, rows, hashes)] = list(tables)
            self.assertEqual(set(prepare_entries(name, None, rows)), {"one", "two"})
            self.assertEqual(hashes["one"], "1"*64)
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
        with (patch.object(arabic, "write_override_tables", side_effect=messages) as generate,
              patch("fagrishot.arabic_font.visual_text", side_effect=lambda s: s),
              patch("fagrishot.arabic_font.patch_fonts", side_effect=fonts),
              patch("fagrishot.localization_art.patch_tutorials", side_effect=artwork)):
            first = arabic.prepare(romfs, private, "verified", self.root)
            self.assertEqual(first, arabic.prepare(romfs, private, "verified", self.root))
            self.assertEqual(generate.call_count, 1)
            (first / "Arabic/romfs/Font" / FONT_NAMES[0]).write_bytes(b"damaged")
            second = arabic.prepare(romfs, private, "verified", self.root)
            self.assertNotEqual(first, second)
            self.assertEqual(generate.call_count, 2)

    def test_offline_draft_integrity_and_reviewed_precedence(self):
        import zstandard
        from fagrishot.arabic_campaign import Campaign, ELIGIBLE_LABELS, SOURCE_LABELS

        pkg = self.catalogs / "campaign"
        pkg.mkdir()
        name = "System.msbt.zst"
        older = {**self.entry, "parts": ["نص آلي"]}
        compressed = zstandard.ZstdCompressor().compress(json.dumps({"one": older, "three": older}).encode())
        file = pkg / (name + ".json.zst")
        file.write_bytes(compressed)
        (pkg / "manifest.json").write_text(json.dumps({
            "schema": 1, "source_update": 131072, "source_labels": SOURCE_LABELS, "eligible_labels": ELIGIBLE_LABELS,
            "files": {name: {"entries": 2, "sha256": hashlib.sha256(compressed).hexdigest()}}}))
        state = arabic.status(self.settings, self.root)
        self.assertEqual((state["covered"], state["reviewed"], state["machine_draft"]), (3, 2, 1))
        campaign = Campaign(pkg)
        self.assertEqual(campaign.merged(name, {"one": self.entry})["one"], self.entry)
        with self.assertRaisesRegex(ValueError, "sources disagree"):
            campaign.merged(name, {"one": {**self.entry, "source_sha256": "2"*64}})
        file.write_bytes(compressed + b"damaged")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            campaign.entries(name)

    def test_source_widths_precede_shaping_and_preserve_paragraphs(self):
        def wrap(text, original):
            return arabic.wrap_draft(text, original, {"?": 1, "و": 2}, lambda value: value)
        self.assertEqual(wrap("  اا وو اا\n\nج د  ", "aaaaa\nbbb\n\nx y"), "  اا\nوو\nاا\n\nج د  ")
        self.assertEqual(wrap("اسم اللاعب", "Name"), "اسم اللاعب")
        self.assertEqual(wrap("كلمةطويلةجدا", "aa\nbbb"), "كلمةطويلةجدا")
        with self.assertRaisesRegex(ValueError, "paragraph breaks"):
            wrap("الجملة الأولى ثم الثانية", "One\n\nTwo")

    def test_whole_line_budget_spans_emphasis_icon_and_closing_remark(self):
        # Same shape as a tutorial page: two highlighted spans, a button icon and a remark in brackets.
        color, reset = bytes.fromhex("0e0000030002000400"), bytes.fromhex("0e000003000200ff00")
        icon = "".encode()
        source = ("aaaa bbbb cccc\ndddd ", color, "eeee ffff", reset, " gg ", color, "hhhh ", icon, reset,
                  ".\n(iiii jjjj kkkk llll)")
        draft = ["اااا بببب جججج دددد ", "هههه وووو", " زز ", "حححح ", "(طططط يييي كككك لللل مممم)"]
        advances = {"?": 1, "0": 1, "": 3}
        same = lambda value: value
        original = [p for p in source if isinstance(p, str)]
        budget = max(arabic.line_widths(source, original, advances, same))
        self.assertEqual(budget, 27)
        # Each part fits the lines of its own source part. Side by side they leave the panel.
        apart = [arabic.wrap_draft(t, s, advances, same) for t, s in zip(draft, original)]
        self.assertGreater(max(arabic.line_widths(source, apart, advances, same)), budget * 3 // 2)
        wrapped = arabic.wrap_message(source, draft, advances, same)
        widths = arabic.line_widths(source, wrapped, advances, same)
        self.assertLessEqual(max(widths), budget)
        self.assertEqual(len(widths), 3)
        self.assertEqual("".join(wrapped).split(), "".join(draft).split())
        # A wider line that the same window is known to show removes a needless third line.
        roomy = arabic.wrap_message(source, draft, advances, same, floor=40)
        self.assertEqual(len(arabic.line_widths(source, roomy, advances, same)), 2)
        # The verb and its button icon stay on one line at every width that can hold them.
        for limit in range(8, 60):
            placed = arabic.wrap_message(source, draft, advances, same, floor=limit)
            self.assertEqual(placed[3], "حححح ", limit)

    def test_whole_line_wrap_keeps_structure(self):
        advances = {"?": 1, "0": 1}
        same = lambda value: value
        arg = bytes.fromhex("0e010000000000")
        self.assertEqual(arabic.wrap_message(("Name ", arg), ["اسم طويل جدا "], advances, same), ["اسم طويل جدا "])
        # An English line break is not kept. A blank line is.
        self.assertEqual(arabic.wrap_message(("aaaa bbbb\ncc\n\nddddd\nee",), ["اا\nبب جج\n\nدد هه وو"], advances, same),
                         ["اا بب جج\n\nدد هه\nوو"])
        with self.assertRaisesRegex(ValueError, "paragraph breaks"):
            arabic.wrap_message(("One\n\nTwo",), ["واحد اثنان"], advances, same)
        with self.assertRaisesRegex(ValueError, "text parts"):
            arabic.wrap_message(("One\nTwo",), ["واحد", "اثنان"], advances, same)
        # A substituted value takes room beside the word it follows. A particle is not left at a line end.
        self.assertEqual(arabic.wrap_message(("aaaaaaaa\nbb ", arg), ["اااا بب جج "], advances, same), ["اااا بب\nجج "])
        self.assertEqual(arabic.wrap_message(("aaaaaaaaaa\nbbbbbbbbbb",), ["اااا ببب في جججج"], advances, same),
                         ["اااا ببب\nفي جججج"])

    def test_latin_notice_signs_and_numbers_are_not_mirrored(self):
        marked = lambda text: "<" + text + ">"
        entry = lambda parts, **more: {"source_sha256": "1" * 64, "parts": parts, **more}
        changes, _, glyphs = arabic.translate({"System.msbt.zst": {
            "notice": entry(["©Maker / STUDIO NAME"]), "level": entry(["+1"]), "year": entry(["14--"]),
            "label": entry(["الإصدار "]), "heading": entry([": "], argument_order=[1, 0]),
            "pause": entry(["، "])}}, marked)
        rows = {label: row["parts"] for label, row in changes["System.msbt.zst"].items()}
        self.assertEqual(rows["notice"], ["©Maker / STUDIO NAME"])
        self.assertEqual((rows["level"], rows["year"]), (["+1"], ["14--"]))
        self.assertEqual((rows["label"], rows["heading"], rows["pause"]), (["<الإصدار >"], ["<: >"], ["<، >"]))
        self.assertIn("ص", glyphs)

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
