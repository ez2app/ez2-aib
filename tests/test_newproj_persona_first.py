# SPDX-License-Identifier: Apache-2.0
"""`aib newproj --folder` on a seat with no persona says exactly how to bind one, and following that works.

6ddf walkthrough K5 (10-14): newproj only relayed "this seat has no persona, cannot bind the brain first
(先 [綁定人格])" — a right-click menu label, useless on the command line. Real state tree in a temp HOME (from a real
`aib newrole`) plus one seat file with no persona; standard library only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
PERSONA = "x\n\n## 職責\na\n\n## 紅線\nb\n\n## 知識域\nc\n"
SEAT, ACTOR = "abcde", "root@00000/Claude"


class NewprojPersonaFirst(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-personafirst-"))
        r = self.aib("newrole", "--name", "R", "--prompt", PERSONA)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        seats = self.td / ".ez2-aib" / "seats"
        seats.mkdir(parents=True, exist_ok=True)
        (seats / "w1.json").write_text(json.dumps({"seat_id": SEAT, "session_uuid": str(uuid.uuid4()), "role": "dev"}),
                                       encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def aib(self, *args):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": "en"}
        return subprocess.run(AIB + list(args), cwd=self.td, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=180)

    def newproj(self):
        r = self.aib("newproj", "--title", "T", "--seat", SEAT, "--actor", ACTOR, "--folder", "core.handoff")
        return r, json.loads(r.stdout.strip().splitlines()[-1])

    def test_refusal_gives_the_command_and_following_it_works(self):
        r, out = self.newproj()
        self.assertFalse(out["ok"], r.stdout + r.stderr)
        hint = "aib newrole --name \"<persona name>\" --prompt-file <persona.md> --seat %s --actor %s" % (SEAT, ACTOR)
        self.assertIn(hint, out["error"])
        self.assertNotIn("[綁定人格]", r.stdout + r.stderr)
        # follow the hint (existing persona: --name is enough), then the same newproj goes through
        b = self.aib("newrole", "--name", "R", "--seat", SEAT, "--actor", ACTOR)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        r2, out2 = self.newproj()
        self.assertTrue(out2["ok"], r2.stdout + r2.stderr)

    def test_bind_one_raises_the_dedicated_error(self):
        sys.path.insert(0, str(ROOT / "aib" / "src" / "identity"))
        import bind_one
        with self.assertRaises(bind_one.NoPersonaError) as ctx:
            bind_one.bind_seat(self.td / ".ez2-aib", SEAT, None, bind_one.actor_of(ACTOR), brain="core.handoff",
                               dry_run=True)
        self.assertIsInstance(ctx.exception, bind_one.BindError)   # old callers that catch BindError still do
        self.assertIn("aib newrole", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
