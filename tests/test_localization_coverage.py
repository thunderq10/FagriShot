import json
import struct
import tempfile
import unittest
from pathlib import Path

from fagrishot.localization import LocalizationError, Message
from fagrishot.localization_coverage import _ebml, _webm_tracks, assess, audit_messages, bidi_review
from test_localization import CHOICE, COLOR, ICON, resource, start_tag


class CoverageTests(unittest.TestCase):
    def test_only_exact_unchanged_support_grades_are_language_neutral(self):
        source = Message.parse(b"B\0")
        record = {"source_sha256": source.source_sha256, "parts": ["B"]}
        label = "MID_Sys_Reliance_Rank_B"
        counts, flags = assess(source, record, table="System.msbt.zst", label=label)
        self.assertFalse(flags)
        self.assertEqual(counts["preserved_support_grade_symbols"], 1)
        for table, key in [("Other.msbt.zst", label), ("System.msbt.zst", "MID_Other")]:
            self.assertIn("lexical_target_without_arabic", assess(source, record, table=table, label=key)[1])
        record["parts"] = ["A"]
        self.assertIn("lexical_target_without_arabic", assess(source, record, table="System.msbt.zst", label=label)[1])
        # The highest grade is a symbol like the other three.
        top = Message.parse(b"S\0")
        kept = {"source_sha256": top.source_sha256, "parts": ["S"]}
        counts, flags = assess(top, kept, table="System.msbt.zst", label="MID_Sys_Reliance_Rank_S")
        self.assertFalse(flags)
        self.assertEqual(counts["preserved_support_grade_symbols"], 1)

    def test_only_the_unchanged_title_notice_may_stay_in_latin(self):
        source = Message.parse("©Maker / STUDIO NAME".encode() + b"\0")
        record = {"source_sha256": source.source_sha256, "parts": ["©Maker / STUDIO NAME"]}
        counts, flags = assess(source, record, table="System.msbt.zst", label="MID_Title_Previlefe")
        self.assertFalse(flags)
        self.assertEqual(counts["preserved_legal_notices"], 1)
        self.assertEqual(counts["passed_automated_checks"], 1)
        _, flags = assess(source, record, table="System.msbt.zst", label="MID_Title_Other")
        self.assertEqual(flags, {"lexical_target_without_arabic", "unchanged_lexical_part_review"})
        record["parts"] = ["Maker / STUDIO NAME©"]
        _, flags = assess(source, record, table="System.msbt.zst", label="MID_Title_Previlefe")
        self.assertIn("lexical_target_without_arabic", flags)

    def test_choice_only_and_incomplete_choice_have_distinct_issues(self):
        entry = Message.parse(b" " + CHOICE + b"\0")
        counts, issues = assess(entry, None)
        self.assertEqual((counts["eligible"], counts["choice_only_content"], counts["choice_forms"]), (1, 1, 2))
        self.assertEqual(issues, {"missing_record"})
        record = {"source_sha256": entry.source_sha256, "parts": [" "]}
        _, issues = assess(entry, record)
        self.assertIn("missing_choice_translation", issues)
        self.assertIn("choice_form_without_arabic", issues)
        record["choices"] = {"1": ["واحد", "أكثر"]}
        counts, issues = assess(entry, record)
        self.assertFalse(issues)
        self.assertEqual(counts["choice_forms_explicit"], 2)

    def test_inline_review_and_language_neutral_denominator(self):
        self.assertEqual(bidi_review(("اضغط ", ICON, " للانتقال")), ["icon"])
        self.assertEqual(bidi_review(("سطر كامل", COLOR, "\nسطر آخر")), [])
        self.assertEqual(bidi_review(("نص", COLOR, "آخر")), ["style"])
        self.assertEqual(bidi_review((b"<TH>", "كلمة")), [])
        for raw, category in [(b"\0", "empty"), (ICON + b"\0", "control_only"), (b" \0", "whitespace_format")]:
            counts, _ = assess(Message.parse(raw), None)
            self.assertEqual((counts[category], counts["eligible"]), (1, 0))
        counts, _ = assess(Message.parse(b"123\0"), None)
        self.assertEqual((counts["eligible"], counts["source_lexical"]), (1, 0))

    def test_stale_unchanged_and_untranslated_english_checked(self):
        entry = Message.parse(b"Hello\0")
        _, issues = assess(entry, {"parts": ["ترحيب"], "source_sha256": "old"})
        self.assertEqual(issues, {"source_fingerprint_mismatch"})
        record = {"parts": ["Hello"], "source_sha256": entry.source_sha256}
        counts, issues = assess(entry, record)
        self.assertNotIn("byte_changed", counts)
        self.assertIn("lexical_target_without_arabic", issues)
        record["parts"] = [""]
        _, issues = assess(entry, record)
        self.assertIn("empty_target", issues)

    def test_table_fingerprint_source_denominator_and_no_text_leak(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest("Player's zstandard wheel required for local I/O.")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "romfs"
            directory = root / "Msg" / "USen"
            directory.mkdir(parents=True)
            name = "GameMenu.msbt.zst"
            raw = resource([("first", b"private source words\0"), ("empty", b"\0")])
            dst = directory / name
            dst.write_bytes(zstandard.ZstdCompressor(level=1).compress(raw))
            blank = audit_messages(root, {})
            self.assertEqual(blank["counts"]["eligible"], 1)
            self.assertFalse(blank["matches_update_131072_text"])
            self.assertNotIn("private source", json.dumps(blank))
            entry = Message.parse(b"private source words\0")
            dst.write_bytes(zstandard.ZstdCompressor(level=12).compress(raw))
            good = audit_messages(root, {name: {"first": {"source_sha256": entry.source_sha256, "parts": ["مثال"]}},
                                         "other.msbt.zst": {}})
            self.assertEqual(blank["source_manifest_sha256"], good["source_manifest_sha256"])
            self.assertEqual(good["counts"]["passed_automated_checks"], 1)
            self.assertEqual(good["unknown_tables"], ["other.msbt.zst"])

    def test_ebml_track_classes_and_truncation(self):
        def elem(tag, data):
            return bytes.fromhex(tag) + bytes([128 + len(data)]) + data
        def track(kind, codec):
            return elem("ae", elem("83", bytes([kind])) + elem("86", codec))
        content = elem("18538067", elem("1654ae6b", track(1, b"V_AV1") + track(17, b"S_TEXT/UTF8")))
        self.assertEqual(_webm_tracks(content), [(1, "V_AV1"), (17, "S_TEXT/UTF8")])
        for data in (b"", b"\x00", b"\x01"):
            with self.assertRaises(LocalizationError):
                _ebml(data, 0)


if __name__ == "__main__":
    unittest.main()
