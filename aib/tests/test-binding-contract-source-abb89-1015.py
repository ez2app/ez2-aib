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


if __name__ == "__main__":
    unittest.main()
