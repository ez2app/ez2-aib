# SPDX-License-Identifier: Apache-2.0
"""`aib lease` shows who holds this machine's test lease, until when, and who is queued; `aib test` points to it.

New-user walkthrough (10-14): big `aib test` batches queue for a machine lease, but nothing told the user who holds
it or until when, and one message said to run `host_lease.py acquire` (a file inside the package nobody knows).
Standard library only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
TOOL = ROOT / "aib" / "tools" / "governance" / "host_lease.py"


class AibLease(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-lease-"))
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td), "EZ2_AIB_LOCALE": "en",
                    "AIB_HOST_LEASE_FILE": str(self.td / "lease.json")}

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def run_(self, cmd):
        return subprocess.run(cmd, env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def hold(self):
        r = self.run_([sys.executable, str(TOOL), "acquire", "--owner", "aib test @12345", "--what", "aib test 40 proj",
                       "--run-id", "r1", "--ttl", "600"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_([sys.executable, str(TOOL), "queue", "add", "--owner", "aib test @54321", "--what",
                       "aib test 35 other", "--run-id", "r2"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_free(self):
        r = self.run_(AIB + ["lease"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("machine free", r.stdout)
        self.assertIn("nobody waiting", r.stdout)

    def test_held_shows_holder_expiry_and_queue_and_writes_nothing(self):
        self.hold()
        before = sorted((p.name, p.read_bytes()) for p in self.td.iterdir() if p.is_file())
        r = self.run_(AIB + ["lease"])
        after = sorted((p.name, p.read_bytes()) for p in self.td.iterdir() if p.is_file())
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        expiry = json.loads((self.td / "lease.json").read_text())["expiry_at"]
        for want in ("aib test @12345", "aib test 40 proj", expiry, "aib test @54321"):
            self.assertIn(want, r.stdout)
        self.assertEqual(before, after)

    def test_aib_test_queue_message_names_expiry_and_aib_lease(self):
        self.hold()
        sys.path.insert(0, str(ROOT / "aib" / "tools" / "verify"))
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            import test_entry
            said = []

            def stop(_s):
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                test_entry.big_batch_lease(999, self.td, said.append, sleep=stop, env=dict(os.environ))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        text = "\n".join(said)
        expiry = json.loads((self.td / "lease.json").read_text())["expiry_at"]
        self.assertIn(expiry, text)
        self.assertIn("`aib lease`", text)

    def test_no_message_sends_users_to_host_lease_py(self):
        for loc in ("en", "zh-TW", "ja", "ko"):
            table = json.loads((ROOT / "aib" / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            for key, value in table.items():
                if key.startswith("aib.test."):
                    self.assertNotIn("host_lease.py", value, (loc, key))


if __name__ == "__main__":
    unittest.main()
