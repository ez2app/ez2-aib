# SPDX-License-Identifier: Apache-2.0
"""Old project cards (constitution with `andy_rulings` only) are read and migrated; a card that is really broken
says which field is missing instead of "no such card, or malformed".

6ddf walkthrough K1 (10-14): `aib newproj --project-uid` on an early card said it could not find the card.
Standard library only.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "aib" / "src" / "identity"))
sys.path.insert(0, str(ROOT / "aib" / "tools" / "project"))
os.environ["EZ2_AIB_LOCALE"] = "en"

UID = "proj:0123456789abcdef"
RULING = {"at": "2026-10-12 18:31", "by": "owner", "text": "goals approved"}


def legacy_card():
    return {
        "schema": "aib-project-card/v1", "project_uid": UID, "title": "Old project",
        "repo_fingerprints": {"site": "a" * 64},
        "layers": {
            "constitution": {"goals": ["ship"], "red_lines": ["no guessing"], "andy_rulings": [RULING],
                             "approved": True},
            "memory": {"entries": []},
            "work_state": {"now": "", "owners": [], "pending": [], "next_step": ""},
            "handoff": {"persona_uid_used": "", "persona_version": "", "generated_at": "", "ctx_budget_bytes": 0,
                        "text_sha8": ""},
        },
    }


class ProjectCardLegacy(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aib-projcard-"))
        import new_project
        self.np = new_project

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, card):
        path = self.np.card_path_of(self.root, UID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    def test_legacy_card_is_read_and_migrated(self):
        self.write(legacy_card())
        card = self.np.load_card(self.root, UID)
        self.assertIsNotNone(card)
        constitution = card["layers"]["constitution"]
        self.assertEqual(constitution["human_rulings"], [RULING])
        self.assertEqual(constitution["andy_rulings"], [RULING])   # old field kept

    def test_broken_card_names_the_missing_field(self):
        card = copy.deepcopy(legacy_card())
        del card["layers"]["constitution"]["goals"]
        self.write(card)
        self.assertIsNone(self.np.load_card(self.root, UID))
        msg = self.np._uid_refusal(self.root, UID)
        self.assertIn("goals", msg)
        self.assertIn("card.json", msg)
        self.assertNotIn("or the card is malformed", msg)

    def test_missing_card_says_not_found(self):
        msg = self.np._uid_refusal(self.root, UID)
        self.assertIn("no project card with this uid", msg)


if __name__ == "__main__":
    unittest.main()
