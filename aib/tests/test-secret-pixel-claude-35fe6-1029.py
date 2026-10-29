#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""像素工坊找參考專用的 claude 長效 token:PIXEL_CLAUDE_OAUTH_TOKEN → pixel-claude.env(10-29 @35fe6;@f5fb8 裁、@aeb40 審)。

不放 api-key-claude.env:那份是 claude-apikey 開關(apiKeyHelper)與計費組席位讀的,OAuth token 帶進席位會讓開著的席換登入。

P1 aib secret ask PIXEL_CLAUDE_OAUTH_TOKEN:輸入框 → Touch ID → 落 pixel-claude.env;檔 0600、secrets 夾 0700;
   值不在輸出;api-key-claude.env 沒被建
P2 沒有席位 env 載入器 / 開關會讀它:
   a. 開席的環境門(identity-env.sh aib_env_door_names)不帶 PIXEL_CLAUDE_OAUTH_TOKEN,也不帶 CLAUDE_CODE_OAUTH_TOKEN
   b. providers.json(開席的金鑰名正本)沒有一家的 api_key_env 是它
   c. 核心 src / bin / tools 裡提到這個名字或 pixel-claude.env 的只有名冊 aib_secrets.py(誰新加讀它的地方這格就紅,審查時要看到)
P3 前綴比對:PIXEL_CLAUDE_ 開頭的一律落 pixel-claude.env,不歸到 api-key-claude.env(CLAUDE_ 較短);反向:ANTHROPIC_API_KEY /
   CLAUDE_CODE_OAUTH_TOKEN 照舊落 api-key-claude.env
P4 不補填(@aeb40 #416、#247 codex M1):檔不存在 / 空值 / 兩次讀中間被清空 ⇒ secret() 只丟 missing_*,補填待辦與請求紀錄都沒新增;
   對照組 CF_API_TOKEN 缺值照舊發補填(證明這格量得到);secret(name, fill=False) 對一般名字也不補填
刀:K1 名冊拿掉這一格 ⇒ P1 / P3 紅;K2 環境門前綴加 PIXEL_ ⇒ P2a 紅;K3 別的核心檔提到 pixel-claude.env ⇒ P2c 紅;
   K4 NO_FILL_NAMES 拿掉它(request_fill 的擋也拿掉)⇒ P4 紅
"""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src" / "cockpit"))
sys.path.insert(0, str(REPO / "src" / "bootstrap"))
import aib_secrets as S  # noqa: E402
import secret_ask as SA  # noqa: E402
import secret_collect as SC  # noqa: E402

NAME = "PIXEL_CLAUDE_OAUTH_TOKEN"
REL = "pixel-claude.env"
VALUE = "sk-ant-oat01-" + "Zq7" * 20
IDENTITY_ENV = REPO / "src" / "cockpit" / "identity-env.sh"
PROVIDERS = REPO / "src" / "cockpit" / "providers.json"


class Fake:
    def __init__(self, values):
        self.values, self.asks, self.touches = list(values), 0, 0

    def ask(self, prompt, title):
        self.asks += 1
        v = self.values.pop(0) if self.values else None
        return {"ok": False, "why": "cancel"} if v is None else {"ok": True, "why": "ok", "value": v}

    def touchid(self, reason):
        self.touches += 1
        return {"ok": True}


class PixelClaudeSecret(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="aib-secret-pixel-claude-")
        root = Path(self.td.name)
        self.sec = root / "secrets"
        self.env = {"HOME": str(root), "EZ2_AIB_STATE_ROOT": str(root / "state"), "EZ2_AIB_SECRETS_DIR": str(self.sec),
                    "EZ2_AIB_LOCALE": "zh-TW"}
        self._old = {k: os.environ.get(k) for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_SECRETS_DIR")}
        os.environ.update({"EZ2_AIB_STATE_ROOT": self.env["EZ2_AIB_STATE_ROOT"], "EZ2_AIB_SECRETS_DIR": str(self.sec)})

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.td.cleanup()

    def test_p1_ask_writes_private_file(self):
        f, out = Fake([VALUE]), io.StringIO()
        rc = SA.ask_cmd([NAME], env=dict(self.env), platform="darwin", ask=f.ask, touchid=f.touchid,
                        touchid_available=lambda: True, out=out, ssh_run=lambda *a, **k: None)
        text = out.getvalue()
        self.assertNotIn(VALUE, text)
        res = json.loads(text.strip().splitlines()[-1])
        self.assertEqual((rc, res["ok"], res["file"], f.asks, f.touches), (0, True, REL, 1, 1), res)
        path = self.sec / REL
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.sec.stat().st_mode), 0o700)
        self.assertNotIn(VALUE, path.read_text(encoding="utf-8"))   # 落地是 enc.v1.
        self.assertEqual(S.secret(NAME, env=dict(self.env)), VALUE)
        self.assertFalse((self.sec / "api-key-claude.env").exists())

    def door_names(self, extra):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.td.name,
               "EZ2_AIB_STATE_ROOT": self.td.name, "EZ2_AIB_HOME": self.td.name}
        env.update(extra)
        r = subprocess.run(["bash", "-c", 'source "$1"; aib_env_door_names', "bash", str(IDENTITY_ENV)], env=env,
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return set(r.stdout.split())

    def test_p2a_seat_env_door_drops_it(self):
        names = self.door_names({NAME: VALUE, "CLAUDE_CODE_OAUTH_TOKEN": VALUE, "EZ2_AIB_LOCALE": "zh-TW"})
        self.assertNotIn(NAME, names)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", names)
        self.assertIn("EZ2_AIB_LOCALE", names)   # 對照:門照常帶自己的

    def test_p2b_no_provider_key_is_it(self):
        data = json.loads(PROVIDERS.read_text(encoding="utf-8"))
        rows = data.get("providers", data) if isinstance(data, dict) else data
        rows = rows.values() if isinstance(rows, dict) else rows
        keys = [r.get("api_key_env") for r in rows if isinstance(r, dict)]
        self.assertTrue(any(keys), "providers.json 格式變了,這格量不到")
        self.assertNotIn(NAME, keys)
        self.assertFalse(any(S.file_for_name(k) == REL for k in keys if k))

    def test_p2c_only_registry_mentions_it(self):
        hits = []
        for top in ("src", "bin", "tools"):
            base = REPO / top
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".png", ".gif", ".jpg", ".pyc"):
                    continue
                try:
                    t = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if NAME in t or REL in t:
                    hits.append(str(p.relative_to(REPO)))
        self.assertEqual(hits, ["src/bootstrap/aib_secrets.py"])

    def fill_traces(self):
        """補填待辦的項目名 + 請求紀錄行數(寫入時 cancel_fill 會建空的待辦檔,那不算請求)。"""
        pend = self.sec / SC.PENDING_NAME
        items = [r.get("name") for r in json.loads(pend.read_text(encoding="utf-8")).get("items", [])] if pend.exists() else []
        req = self.sec / SC.REQUESTS_NAME
        return items, (len(req.read_text(encoding="utf-8").splitlines()) if req.exists() else 0)

    def read_reason(self, name, **kw):
        with self.assertRaises(S.SecretError) as cm:
            S.secret(name, env=dict(self.env), send=lambda payload: self.sent.append(payload), **kw)
        return cm.exception.reason

    def test_p4_no_fill_request(self):
        self.sent = []
        self.sec.mkdir(mode=0o700)
        self.assertIn(NAME, S.NO_FILL_NAMES)
        self.assertEqual(self.read_reason(NAME), S.MISSING_FILE)   # 檔不存在
        (self.sec / REL).write_text(NAME + "=\n", encoding="utf-8")
        os.chmod(self.sec / REL, 0o600)
        self.assertEqual(self.read_reason(NAME), S.MISSING_LINE)   # 空值
        f = Fake([VALUE])
        SA.ask_cmd([NAME], env=dict(self.env), platform="darwin", ask=f.ask, touchid=f.touchid,
                   touchid_available=lambda: True, out=io.StringIO(), ssh_run=lambda *a, **k: None)
        self.assertTrue(S.load_file(REL, dict(self.env)).get(NAME))   # 先查有值(外掛的預查)
        (self.sec / REL).write_text("", encoding="utf-8")            # 兩次讀中間被清空
        self.assertIn(self.read_reason(NAME), (S.MISSING_LINE, S.MISSING_FILE))
        self.assertIsNone(SC.request_fill(NAME, env=dict(self.env)))  # 直接叫補填也不發
        self.assertEqual((self.fill_traces(), self.sent), (([], 0), []))
        self.assertEqual(self.read_reason("CF_API_TOKEN", fill=False), S.MISSING_FILE)   # 呼叫端旗標
        self.assertEqual((self.fill_traces(), self.sent), (([], 0), []))
        self.assertEqual(self.read_reason("CF_API_TOKEN"), SC.NEEDS_FILL)   # 對照:一般名字照舊發補填
        self.assertEqual(self.fill_traces(), (["CF_API_TOKEN"], 1))

    def test_p3_prefix_goes_to_own_file(self):
        for n in (NAME, "PIXEL_CLAUDE_OAUTH_TOKEN_2", "PIXEL_CLAUDE_X"):
            self.assertEqual(S.file_for_name(n), REL, n)
            self.assertNotEqual(S.file_for_name(n), "api-key-claude.env", n)
        for n in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):   # 反向:原本的照舊
            self.assertEqual(S.file_for_name(n), "api-key-claude.env", n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
