import os
import unittest

from fagrishot.arabic_font import visual_text
from fagrishot.localization import LocalizationError, Message
from fagrishot.localization_flow import render, supports
from test_localization import CHOICE, COLOR, ICON, start_tag


ARG = start_tag(1, 0, b"")
RESET = start_tag(0, 3, b"\xff\0")


class InlineFlowTests(unittest.TestCase):
    def test_supports_only_known_commands(self):
        self.assertTrue(supports(["Use ", {"token": ARG.hex()}]))
        self.assertTrue(supports([b"<DLG>", "Use ", ICON, "."]))
        self.assertTrue(supports(["a", start_tag(7, 1, b""), "b"]))
        self.assertFalse(supports(["a", start_tag(7, 9, b""), "b"]))
        self.assertFalse(supports(["a", COLOR, "b"]))
        self.assertTrue(supports(["a", COLOR, COLOR, RESET]))
        self.assertFalse(supports(["a", b"<DLG>", "b"]))

    def test_value_only_formats_keep_their_source_order(self):
        second, third = start_tag(1, 1, b""), start_tag(1, 2, b"")
        for raw, texts in [(ARG + b":" + second, [":"]), (second + b"/" + third + b"/" + ARG, ["/", "/"]),
                           (b"+" + ARG, ["+"]), (ARG + b"%", ["%"]), (ARG + b"/" + second, ["/"])]:
            with self.subTest(format=texts):
                source = Message.parse(raw + b"\0")
                self.assertTrue(supports(source.parts))
                # 12:30 must not become 30:12, nor +5 become 5+.
                self.assertEqual(render(source, texts), source.raw)

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_arabic_label_still_reads_before_its_value(self):
        source = Message.parse(b"Ver. " + ARG + b"\0")
        placed = Message.parse(render(source, ["الإصدار "]))
        self.assertEqual(placed.parts[0], ARG)
        self.assertEqual(placed.parts[1], visual_text("الإصدار "))
        self.assertTrue(placed.parts[1].startswith(" "))

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_mixed_line_matches_complete_icu_paragraph_and_keeps_substitution(self):
        src = Message.parse(b"Select " + ARG + b" here\0")
        text = ["اختر ", " E34 هنا."]
        dst = Message.parse(render(src, text))
        visible = "".join(p if isinstance(p, str) else "\ufffc" for p in dst.parts)
        self.assertEqual(visible, visual_text(text[0] + "\ufffc" + text[1]))
        self.assertEqual([p for p in dst.parts if isinstance(p, bytes)], [ARG])

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_final_lam_alef_keeps_ordinary_spaces_and_leading_substitution(self):
        src = Message.parse(ARG + b" label\0")
        for text in [" طفل ليلى فلا", " لا", " لا قول لا", " لَا", " لآ", " لِإ"]:
            with self.subTest(text=text):
                dst = Message.parse(render(src, [text]))
                visible = "".join(p if isinstance(p, str) else "\ufffc" for p in dst.parts)
                self.assertEqual(visible, visual_text("\ufffc" + text))
                self.assertEqual([p for p in dst.parts if isinstance(p, bytes)], [ARG])

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_word_color_and_directive_follow_their_original_content(self):
        src = Message.parse(b"<DLG>Look " + COLOR + b"here" + RESET + b".\0")
        dst = Message.parse(render(src, ["انظر ", "هناك", "."]))
        self.assertEqual(dst.parts[0], b"<DLG>")
        self.assertEqual(dst.parts[dst.parts.index(COLOR) + 1], visual_text("هناك"))
        self.assertEqual(dst.parts[dst.parts.index(COLOR) + 2], RESET)
        self.assertEqual("".join(p for p in dst.parts if isinstance(p, str)), visual_text("انظر هناك."))

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_reassigned_palette_dates_and_notice_icon(self):
        second = start_tag(0, 3, b"\x02\0")
        month, day = start_tag(7, 4, b"\x03"), start_tag(7, 5, b"\x03")
        icon = start_tag(3, 0, b"\0")
        source = Message.parse(b"Date: " + COLOR + month + b" " + day + second + b" at " + icon + RESET + b".\0")
        dst = Message.parse(render(source, ["الموعد ", " ", " عند ", "."]))
        self.assertEqual("".join(p if isinstance(p, str) else "\ufffc" for p in dst.parts
                                 if p not in (COLOR, second, RESET)), visual_text("الموعد \ufffc \ufffc عند \ufffc."))
        for command in (month, day, icon):
            self.assertEqual(dst.parts.count(command), 1)
        self.assertIn(second, dst.parts)
        self.assertIn(COLOR, dst.parts)

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_choice_payload_is_rebuilt_and_multiline_flow_resets_per_line(self):
        src = Message.parse(b"Item " + CHOICE + b"\npress " + ICON + b".\0")
        edit = Message.parse(render(src, ["عدد ", "\nاضغط ", "."], {"1": ["واحد", "أكثر"]}))
        self.assertEqual(list(edit.choices.values()), [[visual_text("واحد"), visual_text("أكثر")]])
        self.assertEqual([p for p in edit.parts if p == ICON], [ICON])
        self.assertEqual("".join(p if isinstance(p, str) else "\ufffc" for p in edit.parts),
                         visual_text("عدد \ufffc\nاضغط \ufffc."))
        with self.assertRaises(LocalizationError):
            render(Message.parse(b"A" + start_tag(7, 9, b"") + b"B\0"), ["أ", "ب"])

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_lowercase_region_moves_with_both_original_tags(self):
        begin, end = start_tag(8, 4, b""), start_tag(8, 5, b"")
        src = Message.parse(b"Find " + begin + b"Silver Knights" + end + b" here\0")
        edited = render(src, ["هنا ", "فرسان الفضة", " ينتظرون."])
        self.assertIn(begin + visual_text("فرسان الفضة").encode() + end, edited)
        self.assertEqual(edited.count(begin), 1)
        self.assertEqual(edited.count(end), 1)
        self.assertTrue(supports(["a", begin, "b\nc", end]))
        self.assertTrue(supports(["a", begin, ARG, end]))
        self.assertFalse(supports(["a", end]))

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_case_modifier_keeps_its_exact_variable_after_visual_reordering(self):
        cap = start_tag(8, 1, b"")
        lower = start_tag(8, 2, b"")
        other = start_tag(1, 1, b"")
        src = Message.parse(cap + ARG + b" with " + lower + other + b".\0")
        dst = Message.parse(render(src, [" مع ", "."]))
        self.assertEqual([p for p in dst.parts if isinstance(p, bytes)], [lower, other, cap, ARG])
        self.assertEqual("".join(p for p in dst.parts if isinstance(p, str)), visual_text(" مع ."))
        parenthesized = Message.parse(b"say " + lower + b"Item then " + ARG + b"\0")
        moved = render(parenthesized, ["قل ", "(EX) ثم "])
        self.assertIn(lower + b"EX", moved)

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_uncapall_to_end_avoids_casing_an_unrelated_substitution(self):
        lower_all = start_tag(8, 4, b"")
        end = start_tag(8, 5, b"")
        src = Message.parse(ARG + b" sees " + lower_all + b"Silver Knights.\0")
        dst = Message.parse(render(src, [" يرى ", "فرسان الفضة."]))
        self.assertLess(dst.parts.index(ARG), dst.parts.index(lower_all))
        self.assertEqual(dst.parts.count(lower_all), 1)
        self.assertNotIn(end, dst.parts)
        # When two dynamic variables cannot both retain their case after moving
        # an unclosed region, require an authored layout instead of leaking it.
        var2 = start_tag(1, 1, b"")
        conflict = Message.parse(ARG + b" and " + lower_all + var2 + b"\0")
        with self.assertRaisesRegex(LocalizationError, "capitalization"):
            render(conflict, [" مع "])

    @unittest.skipUnless(os.name == "nt", "The player uses Windows ICU.")
    def test_case_span_recognizes_substitutions_colors_and_explicit_line_breaks(self):
        lower_all, end = start_tag(8, 4, b""), start_tag(8, 5, b"")
        src = Message.parse(b"At " + lower_all + COLOR + ARG + RESET + b"\nstreet" + end + b" here.\0")
        dst = Message.parse(render(src, ["عند ", "\nالشمال", " هنا."]))
        at = dst.parts.index(ARG)
        self.assertLess(dst.parts.index(lower_all), at)
        self.assertGreater(dst.parts.index(end), at)
        self.assertLess(dst.parts.index(COLOR), at)
        self.assertGreater(dst.parts.index(RESET), at)


if __name__ == "__main__":
    unittest.main()
