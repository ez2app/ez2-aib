# SPDX-License-Identifier: Apache-2.0
"""席位綁定投影:路由表兩個來源不一致時,別席被安靜丟掉(10-15 艙內實報)。

病況:EZ2_ROUTE_REGISTRY 指七月舊檔(32 條)、EZ2_ROUTE_CONTRACT_SPEC 指 git main(40 條,多 core.site_ops 等)。
任一席綁定/重綁 ⇒ 整艙重投影;沒給 --contract 時投影讀 REGISTRY ⇒ 大腦在 core.site_ops 的席
invalid_route_key 被從 bindings.json 整筆刪掉,沒有一個字;那一席狀態變「缺人格」;
`aib bindme repair` rc=0 說接回了,其實還是不見。

四刀:
  f1 唯一來源      沒給 --contract 一律走 route_register.default_contract_spec;兩個 env 內容不同 ⇒ 出聲、用超集
  f2 不安靜丟      重投影時路由不在表裡 ⇒ 保留原綁定、在 display 標「未解析」、印一行給觸發的那一席看
  f3 repair 驗收    重投影後讀回;那一席沒回來 ⇒ rc≠0 + 原因
  f4 status 說真話  route 未解析的席顯示路由原因,不是「缺人格」

全部在 tmpdir 沙盒:HOME / 狀態根 / 兩份假路由表都是臨時的;不碰真 ~/.ez2-aib、真 vault、tmux。
"""

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
for _p in ("persona_route", "memory", "identity", "cockpit", "ledger"):
    sys.path.insert(0, str(SRC / _p))
PROJECTION = SRC / "memory" / "binding_projection.py"

BASE_ROUTES = ["core.r%02d" % i for i in range(31)] + ["core.handoff"]           # 舊檔 32 條
EXTRA_ROUTES = ["core.site_ops", "core.meeting"] + ["storage.s%d" % i for i in range(6)]  # 正本多 8 條

SEAT_OPS = ("a1b2c3d4", "11111111-1111-4111-8111-11111111aaaa", "core.site_ops")
SEAT_OK = ("b1b2c3d4", "22222222-2222-4222-8222-22222222bbbb", "core.handoff")
CODE_OPS, CODE_OK = "1aaaa", "2bbbb"


def _route(k):
    return {"routeKey": k, "folder": "F/" + k.replace(".", "-"), "dataLevel": "一般", "aiWritable": True}


def _safe(raw):
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:64]
    return "%s-%s.json" % (stem, hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12])


class Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aib-contract-src-")
        t = Path(self._tmp.name)
        self.t = t
        self.stale = t / "stale-contract.json"
        self.fresh = t / "fresh-contract.json"
        self.stale.write_text(json.dumps({"routes": [_route(k) for k in BASE_ROUTES]}), encoding="utf-8")
        self.fresh.write_text(json.dumps({"routes": [_route(k) for k in BASE_ROUTES + EXTRA_ROUTES]}),
                              encoding="utf-8")
        self.state = t / "state"
        for d in ("seats", "identity/sessions", "identity/agents"):
            (self.state / d).mkdir(parents=True)
        cards = {"schema_version": "0.1.0-draft", "cards": {}}
        for sid, uuid, rk in (SEAT_OPS, SEAT_OK):
            (self.state / "seats" / ("w%s.json" % sid)).write_text(
                json.dumps({"seat_id": sid, "session_uuid": uuid}), encoding="utf-8")
            agent = "agent:" + sid
            (self.state / "identity" / "sessions" / _safe(uuid)).write_text(
                json.dumps({"active": {"agent_uid": agent}}), encoding="utf-8")
            (self.state / "identity" / "agents" / _safe(agent)).write_text(
                json.dumps({"persona_card": "p-" + sid, "brain_route_key": rk}), encoding="utf-8")
            cards["cards"]["p-" + sid] = {"persona_uid": "p-" + sid, "route_key": "core.handoff",
                                          "display_name": "P" + sid}
        self.cards = t / "cards.json"
        self.cards.write_text(json.dumps(cards), encoding="utf-8")
        self.bindings = self.state / "identity" / "bindings.json"
        self.display = self.state / "identity" / "seat-display.json"

    def env(self, registry=None, spec=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("EZ2_ROUTE_REGISTRY", "EZ2_ROUTE_CONTRACT_SPEC", "AIB_DM_SOCKET",
                            "EZ2_AIHUB_SOCKET", "EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME")}
        env.update({"HOME": str(self.t), "EZ2_EZ2AI_REPO": str(self.t / "no-ez2-ai"),
                    "EZ2_AIB_LOCALE": "zh-TW", "EZ2_VAULT_DIR": str(self.t / "vault")})
        if registry:
            env["EZ2_ROUTE_REGISTRY"] = str(registry)
        if spec:
            env["EZ2_ROUTE_CONTRACT_SPEC"] = str(spec)
        return env

    def project(self, env, contract=None):
        cmd = [sys.executable, str(PROJECTION), "project", "--state-root", str(self.state),
               "--cards", str(self.cards), "--out", str(self.bindings), "--display-out", str(self.display)]
        if contract:
            cmd += ["--contract", str(contract)]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)

    def bound_keys(self):
        return set(json.loads(self.bindings.read_text(encoding="utf-8"))["bindings"])

    def cleanup(self):
        self._tmp.cleanup()


@contextlib.contextmanager
def patched_env(env):
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


class F1SingleSource(unittest.TestCase):
    """f1:沒給 --contract 的讀者全走 default_contract_spec;兩個 env 不同 ⇒ 出聲 + 用超集。"""

    def setUp(self):
        self.sb = Sandbox()
        import route_register
        self.rr = route_register
        getattr(route_register, "_CONTRACT_WARNED", set()).clear()   # 刀:拔掉修法時這個名字不在

    def tearDown(self):
        self.sb.cleanup()

    def _spec_and_err(self, env):
        err = io.StringIO()
        with patched_env(env), contextlib.redirect_stderr(err):
            spec = self.rr.default_contract_spec()
            contract = self.rr.load_contract(None)
        return spec, contract, err.getvalue()

    def test_spec_newer_registry_stale_uses_spec_and_warns(self):
        spec, contract, err = self._spec_and_err(self.sb.env(registry=self.sb.stale, spec=self.sb.fresh))
        self.assertEqual(spec, str(self.sb.fresh))
        self.assertEqual(len(contract["routes"]), 40, "load_contract(None) 必須與 default_contract_spec 同一份")
        self.assertIn(str(self.sb.stale), err)
        self.assertIn(str(self.sb.fresh), err)
        self.assertIn("40", err)
        self.assertIn("32", err)
        self.assertEqual(len([ln for ln in err.splitlines() if ln.strip()]), 1, "一行,而且同一對來源只講一次")

    def test_registry_superset_wins_over_stale_spec(self):
        spec, contract, err = self._spec_and_err(self.sb.env(registry=self.sb.fresh, spec=self.sb.stale))
        self.assertEqual(spec, str(self.sb.fresh), "不得用缺路由的那份")
        self.assertEqual(len(contract["routes"]), 40)
        self.assertTrue(err.strip())

    def test_same_content_is_silent(self):
        copy = self.sb.t / "fresh-copy.json"
        copy.write_bytes(self.sb.fresh.read_bytes())
        spec, _contract, err = self._spec_and_err(self.sb.env(registry=copy, spec=self.sb.fresh))
        self.assertEqual(spec, str(self.sb.fresh))
        self.assertEqual(err.strip(), "")

    def test_reprojection_without_contract_keeps_site_ops_seat(self):
        sb = self.sb
        r = sb.project(sb.env(registry=sb.fresh))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sb.bound_keys(), {CODE_OPS, CODE_OK})
        # 別席重綁觸發的整艙重投影(沒給 --contract,兩個 env 指不同版本)
        r = sb.project(sb.env(registry=sb.stale, spec=sb.fresh))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(CODE_OPS, sb.bound_keys(), "core.site_ops 那席被舊路由表丟掉了")
        self.assertIn("EZ2_ROUTE_REGISTRY", r.stderr)


class F2NoSilentDrop(unittest.TestCase):
    """f2:路由不在表裡 ⇒ 保留原綁定、display 標未解析、印一行(含 bind_one 觸發的重投影)。"""

    def setUp(self):
        self.sb = Sandbox()
        r = self.sb.project(self.sb.env(registry=self.sb.fresh))
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.sb.cleanup()

    def _display(self):
        return json.loads(self.sb.display.read_text(encoding="utf-8"))["display"]

    def test_stale_contract_keeps_binding_marks_unresolved_and_says_so(self):
        sb = self.sb
        before = json.loads(sb.bindings.read_text(encoding="utf-8"))["bindings"][CODE_OPS]
        r = sb.project(sb.env(registry=sb.stale), contract=sb.stale)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = json.loads(sb.bindings.read_text(encoding="utf-8"))["bindings"]
        self.assertEqual(after.get(CODE_OPS), before, "原綁定被刪了")
        d = self._display()[CODE_OPS]
        self.assertEqual(d["brain_binding_status"], "unresolved")
        self.assertEqual(d["route_unresolved"], "route_not_in_contract")
        self.assertEqual(d["persona_uid"], "p-" + SEAT_OPS[0])
        self.assertIn("@%s 的大腦路由 core.site_ops 不在路由表裡(路由表:%s),保留原綁定、標成未解析"
                      % (CODE_OPS, sb.stale), r.stderr)
        rows = [u for u in json.loads(r.stdout)["unresolved"] if u.get("binding_key") == CODE_OPS]
        self.assertTrue(rows and rows[0]["preserved"])

    def test_no_previous_binding_still_shows_unresolved_in_display(self):
        sb = self.sb
        sb.bindings.unlink()
        r = sb.project(sb.env(registry=sb.stale), contract=sb.stale)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(CODE_OPS, sb.bound_keys())
        self.assertEqual(self._display()[CODE_OPS]["brain_binding_status"], "unresolved")
        self.assertIn("@%s" % CODE_OPS, r.stderr)

    def test_bind_one_reproject_surfaces_notice_for_triggering_seat(self):
        import bind_one
        plan = {}
        with patched_env(self.sb.env(registry=self.sb.stale)):
            bind_one._reproject_into(plan, self.sb.state, self.sb.cards, str(self.sb.stale))
        self.assertEqual(plan["reproject"]["rc"], 0, plan["reproject"]["log_tail"])
        notes = plan.get("route_notices") or []
        self.assertTrue(any(("@%s" % CODE_OPS) in n and "core.site_ops" in n for n in notes), notes)
        self.assertIn(CODE_OPS, self.sb.bound_keys())


def _live_row(code, seat_id):
    return {"code": code, "seat_id": seat_id, "cabin": "inst-abcd", "cabin4": "abcd", "provider": "claude",
            "role": "root", "project_uid": "", "project_title": "", "appointed": False}


class _BindmeCase(unittest.TestCase):
    """bindme 沙盒:狀態根同 Sandbox;live_seats 換成固定兩席(不碰真艙 / tmux)。"""

    def setUp(self):
        self.sb = Sandbox()
        r = self.sb.project(self.sb.env(registry=self.sb.fresh))
        self.assertEqual(r.returncode, 0, r.stderr)
        ident = self.sb.state / "identity"
        (ident / "cards.json").write_text(self.sb.cards.read_text(encoding="utf-8"), encoding="utf-8")
        (ident / "seat-continuity").mkdir()
        for sid, _uuid, _rk in (SEAT_OPS, SEAT_OK):
            (ident / "seat-continuity" / (sid + ".json")).write_text(
                json.dumps({"persona_uid": "p-" + sid}), encoding="utf-8")
        import bindme
        self.bm = bindme
        self._orig_live = bindme.live_seats
        bindme.live_seats = lambda _root: [_live_row(CODE_OPS, SEAT_OPS[0]), _live_row(CODE_OK, SEAT_OK[0])]
        self._env = patched_env(self.sb.env(registry=self.sb.stale))
        self._env.__enter__()

    def tearDown(self):
        self.bm.live_seats = self._orig_live
        self._env.__exit__(None, None, None)
        self.sb.cleanup()

    def stale_reproject(self):
        r = self.sb.project(self.sb.env(registry=self.sb.stale), contract=self.sb.stale)
        self.assertEqual(r.returncode, 0, r.stderr)

    def row(self, code):
        return next(r for r in self.bm.status(str(self.sb.state)) if r["code"] == code)


class F4StatusSaysRouteReason(_BindmeCase):
    """f4:路由未解析的席,status 講路由原因,不是「缺人格」。"""

    def test_unresolved_route_seat_shows_route_reason(self):
        self.stale_reproject()
        r = self.row(CODE_OPS)
        self.assertEqual(r["problem"], "route_unresolved")
        self.assertIn("core.site_ops", r["hint"])
        self.assertIn("route_not_in_contract", r["hint"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.bm.print_status([r])
        self.assertIn("大腦路由未解析", out.getvalue())
        self.assertNotIn("缺人格", out.getvalue())
        self.assertNotEqual(self.row(CODE_OK)["problem"], "route_unresolved")


class F3RepairVerifies(_BindmeCase):
    """f3:repair 跑完讀回;那一席沒回來 ⇒ rc≠0 + 原因(以前 rc=0 說接回了)。"""

    def _drop_ops_from_display(self):
        # 艙內現場:舊投影把 core.site_ops 那席整筆刪掉(bindings 與 display 都沒有)
        for path, field in ((self.sb.display, "display"), (self.sb.bindings, "bindings")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc[field].pop(CODE_OPS, None)
            path.write_text(json.dumps(doc), encoding="utf-8")

    def _main(self, runner_effect):
        calls = []

        def fake_run_aib(args):
            calls.append(args)
            runner_effect()
            return 0, "ok\n"

        orig = self.bm.run_aib
        self.bm.run_aib = fake_run_aib
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = self.bm.main(["--state-root", str(self.sb.state), "repair", "--me", CODE_OK])
        finally:
            self.bm.run_aib = orig
        return rc, out.getvalue(), err.getvalue(), calls

    def test_repair_rc0_but_seat_still_missing_is_failure(self):
        self._drop_ops_from_display()
        self.assertEqual(self.row(CODE_OPS)["problem"], "no_persona")
        rc, out, err, calls = self._main(self._drop_ops_from_display)   # newrole rc=0,重投影又把它丟了
        self.assertEqual(len(calls), 1, out + err)
        self.assertNotEqual(rc, 0, "repair 沒有讀回驗收:席位還是不見卻回 0")
        self.assertIn("@%s" % CODE_OPS, err)
        self.assertIn("沒回來", err)

    def test_repair_persona_back_but_route_unresolved_is_not_done(self):
        self._drop_ops_from_display()
        rc, _out, err, _calls = self._main(self.stale_reproject)
        self.assertNotEqual(rc, 0)
        self.assertIn("core.site_ops", err)

    def test_repair_that_really_restores_returns_zero(self):
        self._drop_ops_from_display()
        sb = self.sb

        def restore():
            r = sb.project(sb.env(registry=sb.fresh), contract=sb.fresh)
            self.assertEqual(r.returncode, 0, r.stderr)

        rc, out, err, calls = self._main(restore)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('"verified": true', out)


if __name__ == "__main__":
    unittest.main()
