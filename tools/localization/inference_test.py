"""Source-free regressions for the authoring checks and translation boundaries."""

import unittest

from inference import check
from inference_consistency import compile_names, correct
from inference_pack import bitmap_text, formatted


class AuthoringChecks(unittest.TestCase):
    def test_bitmap_text(self):
        self.assertEqual(bitmap_text("ا\u0654ين，يا ولد？"), "أين،يا ولد؟")
        self.assertEqual(bitmap_text("مُهِمّ"), "مهم")

    def test_protected_edges_and_paragraphs(self):
        self.assertEqual(
            formatted(" Open \n\n door ", {"Open": "افتح", "door": "الباب"}),
            " افتح \n\n الباب ",
        )
        self.assertIsNone(formatted("word", {}))

    def test_risk_checks(self):
        self.assertEqual(check("Go now!", "ه" + "ا" * 25), "repeated-letter")
        self.assertEqual(check("Move", "تحرك 漢"), "unexpected-script")
        self.assertEqual(check("Take 23", "خذ ٢٣"), "")

    def test_name_inside_alias(self):
        names = {"Zaven": "زافن"}
        rx = compile_names(names, {"Zaven": ["زافند"]})
        self.assertEqual(correct("Ask Zaven.", "قل لزافند.", names, rx)[0], "قل لزافن.")
        self.assertEqual(correct("Ask him.", "قل لزافند.", names, rx)[0], "قل لزافند.")
        self.assertEqual(
            correct("Ask Zaven.", "زافند وزافن.", names, rx)[0], "زافند وزافن."
        )


if __name__ == "__main__":
    unittest.main()
