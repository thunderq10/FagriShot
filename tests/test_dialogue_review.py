import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.localization import dialogue_review as review

AUTHORING = Path(__file__).resolve().parents[1] / "tools/localization"
with patch.object(sys, "path", [str(AUTHORING), *sys.path]):
    from tools.localization import inference_refine as refine
    from tools.localization.inference_pack import tournament_title


class DialogueReviewTests(unittest.TestCase):
    def test_player_title_uses_player_condition_rather_than_speaker(self):
        check = review.player_title_check
        self.assertTrue(check("Promised One!", "أيها الموعود!", ""))
        self.assertTrue(check("Promised One!", "أيها الموعود!", "MS限定_主人公_女性"))
        self.assertFalse(
            check("Promised One!", "أيتها الموعودة!", "MS限定_主人公_女性")
        )
        self.assertFalse(check("Promised One!", "يا أملنا الموعود!", ""))

    def test_tournament_name_preserves_preposition_and_requires_matching_source(self):
        for source, arabic, expected in [
            ("At the Heroic Games.", "بالألعاب البطولية.", "بألعاب الأبطال."),
            ("For the Heroic\nGames.", "للألعاب البطولية.", "لألعاب الأبطال."),
            ("Brave games.", "ألعاب بطولية.", "ألعاب بطولية."),
        ]:
            self.assertEqual(tournament_title(source, arabic), expected)

    def test_arabic_predicate_belongs_to_speaker(self):
        evidence = review.agreement_evidence
        self.assertEqual(
            evidence("I'm sorry.", "Je suis désolée.", "إنني آسف."), ({"f"}, {"m"})
        )
        self.assertEqual(
            evidence("I'm worried.", "Je suis inquiète.", "وأنا قَلِقَة."), ({"f"}, {"f"})
        )
        self.assertEqual(
            evidence(
                "I hear you're proud.", "Je sais que tu es fier.", "أعرف أنك فخور."
            ),
            (set(), set()),
        )
        self.assertEqual(
            evidence("I'm listening.", "Je t’écoute.", "كلي آذان صاغية."),
            (set(), set()),
        )

    def test_recurring_line_does_not_inherit_its_first_speaker(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.jsonl"
            records = [
                {"file": "a", "label": "first_1", "parts": ["My daughter!"]},
                {"file": "a", "label": "first_2", "parts": ["I'm ready."]},
                {"file": "b", "label": "other_1", "parts": ["I'm ready."]},
            ]
            for order in (records, list(reversed(records))):
                path.write_text(
                    "\n".join(json.dumps(r) for r in order), encoding="utf8"
                )
                result = refine.units(path)
                self.assertEqual(result["I'm ready."], ("", (), ""))
                self.assertIn("I'm ready.", result["My daughter!"][1])
                prompt = refine.make_prompt(
                    "I'm ready.", result["I'm ready."], {}, refine.name_matcher({})
                )
                self.assertIn("several cues", prompt)
                self.assertNotIn("daughter", prompt)

    def test_reads_speaker_and_condition_as_distinct_attributes(self):
        fields = bytearray(74)
        pool = 8 + len(fields)
        struct.pack_into("<I", fields, 0, pool)
        struct.pack_into("<I", fields, 12, pool + len(b"player_f\0"))
        sections = {
            b"ATO1": struct.pack("<4i", 0, 4, 8, 12),
            b"ATR1": struct.pack("<II", 1, len(fields))
            + fields
            + b"player_f\0actor_b\0",
        }

        class Table:
            def _section(self, name):
                return self.sections[name]

        table = Table()
        table.sections = sections
        self.assertEqual(review.attribute_reader(table)(0), ("actor_b", "player_f"))
        sections[b"ATO1"] = struct.pack("<4i", 0, 4, 8, 16)
        self.assertIsNone(review.attribute_reader(table))
