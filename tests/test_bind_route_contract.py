# SPDX-License-Identifier: Apache-2.0
"""Binding uses the same route contract as the cockpit, and says so loudly when the projection drops the seat.

6ddf walkthrough K2 (10-14): `aib newrole` bound a persona via bind_one without --contract, so bind_one checked and
re-projected with a different contract (EZ2_ROUTE_REGISTRY or the bundled one) than the cockpit / newproj use. A seat
already bound to a project route that only the cockpit's contract has was dropped from the projection, and
`aib bindme status` reported "missing persona". Standard library only.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONA = "x\n\n## 職責\na\n\n## 紅線\nb\n\n## 知識域\nc\n"


class Sentinel(Exception):
    pass


class BindRouteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = Path(tempfile.mkdtemp(prefix="aib-bindcontract-"))
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(cls.td), "EZ2_AIB_LOCALE": "en"}
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "aib"), "newrole", "--name", "R", "--prompt", PERSONA],
                           cwd=cls.td, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr          # a real state tree: ledger + cards.json
        sys.path.insert(0, str(ROOT / "aib" / "src" / "identity"))
        import bind_one
        cls.BO = bind_one

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_no_contract_given_uses_the_cockpit_default(self):
        BO, seen = self.BO, []
        saved = (BO.default_contract_spec, BO.load_contract)
        BO.default_contract_spec = lambda *a, **k: "git:/somewhere/ez2-ai#main:contract.json"

        def record(spec):
            seen.append(spec)
            raise Sentinel()
        BO.load_contract = record
        try:
            with self.assertRaises(Sentinel):
                BO.bind_seat(self.td / ".ez2-aib", "seat1", "R", "root@54321/Claude", contract_spec=None)
        finally:
            BO.default_contract_spec, BO.load_contract = saved
        self.assertEqual(seen, ["git:/somewhere/ez2-ai#main:contract.json"])

    def test_dropped_seat_is_reported_loudly(self):
        BO = self.BO
        plan = {"seat_id": "seat1", "reproject": {"rc": 0, "log_tail": "", "contract_spec": "C1",
                "seat_unresolved": [{"reason": "invalid_route_key", "detail": "route_key core.site_ops 未過契約"}]}}
        saved = BO.bind_seat
        BO.bind_seat = lambda *a, **k: plan
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = BO.main(["bind", "--seat", "seat1", "--card", "R", "--actor", "root@54321/Claude",
                              "--state-root", str(self.td / ".ez2-aib")])
        finally:
            BO.bind_seat = saved
        self.assertEqual(rc, 1)
        for want in ("invalid_route_key", "core.site_ops", "C1"):
            self.assertIn(want, err.getvalue())

    def test_projection_verdict_for_this_seat_is_parsed(self):
        out = ('{"unresolved": [{"seat_id": "a", "reason": "invalid_route_key", "detail": "d1"},'
               ' {"seat_id": "b", "reason": "invalid_route_key", "detail": "d2"}]}')
        self.assertEqual(self.BO._seat_unresolved(out, "a"), [{"reason": "invalid_route_key", "detail": "d1"}])
        self.assertEqual(self.BO._seat_unresolved("not json", "a"), [])

    def test_normal_states_are_not_reported_as_dropped(self):
        # After a persona unbind the seat has no persona card; with a persona but no brain it has no route (and is
        # still shown). Neither is "dropped": reporting them made every normal unbind exit 1 (review catch).
        for reason in ("missing_persona_card_binding", "missing_brain_route"):
            with self.subTest(reason=reason):
                out = '{"unresolved": [{"seat_id": "a", "reason": "%s", "detail": "x"}]}' % reason
                self.assertEqual(self.BO._seat_unresolved(out, "a"), [])


if __name__ == "__main__":
    unittest.main()
