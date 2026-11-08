#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""Hetzner Robot webservice:讓 aib 自己排 Rescue / 查狀態 / 硬體重置(4542e 11-08,#528 M2 進倉)。

用法(預設 plan,只印會做什麼;要真打 API 加 --apply):
  robot.py status  <server#>                                # GET /server、/boot/rescue、/reset(唯讀,不用 Touch ID)
  robot.py keys                                              # GET /key,列 Robot 上的金鑰指紋(唯讀)
  robot.py rescue  <server#> --fingerprint <fp> [--apply]    # POST /boot/<n>/rescue(會改機器 ⇒ 彈窗 + Touch ID)
  robot.py reset   <server#> [--type hw] [--apply]           # POST /reset/<n>(同上;主機還活著請改 ssh reboot)

守則(#528 ebb36 H1 / f1fae 定 b):
- 帳密只從 aib 金鑰櫃讀 HETZNER_ROBOT_USER / HETZNER_ROBOT_PASS(fill=False),不收 argv、不印、不進日誌。
- 同機每個席都讀得到金鑰櫃,所以 **rescue / reset --apply 在讀值之前**先走 approve_gate「cloud_robot.apply」
  (aib 本人核准彈窗 + Touch ID,內容含 server、動作、金鑰指紋);沒過就不讀值、不打 API。status / keys 唯讀不用。
- rescue --apply 沒帶 --fingerprint 直接拒(L1):不然 Hetzner 回一次性 root 密碼,被遮罩後誰都拿不到,Rescue 白排。
- 回覆裡 password 欄一律印成 ***;HTTPError 也走遮罩,Authorization 標頭不印。
- 契約:rescue / reset 都「不支援回滾」;Rescue 只排下一次開機、60 分鐘內要重開、只供一次。
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AIB_SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
for _d in ("bootstrap", "cockpit"):
    _p = os.path.join(AIB_SRC, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

API = os.environ.get("HETZNER_ROBOT_API", "https://robot-ws.your-server.de")
MASK_KEYS = ("password", "pass", "passwd")
NAMES = ("HETZNER_ROBOT_USER", "HETZNER_ROBOT_PASS")
NO_ROLLBACK = "不支援回滾:Rescue 只排下一次開機(60 分鐘內要重開、只供一次);reset 一送就送了。恢復路徑 = 再排一次 / 再重開。"


def creds(env=None):
    """從金鑰櫃讀;缺值只回明確錯誤(名冊已設不補填,不會發 LINE 要密碼)。"""
    import aib_secrets as S
    try:
        return S.secret(NAMES[0], env=env, fill=False), S.secret(NAMES[1], env=env, fill=False)
    except S.SecretError as e:
        raise SystemExit("金鑰櫃沒有 Robot 帳密(%s):請 Andy 跑 aib secret ask HETZNER_ROBOT_USER / HETZNER_ROBOT_PASS" % e.reason)


def mask(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(m in k.lower() for m in MASK_KEYS) else mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(x) for x in obj]
    return obj


def call(method, path, data=None, cred=None, opener=None):
    """一次 API 呼叫。回 (http 狀態, 已遮罩 json)。cred 必須由呼叫端給(apply 要先過閘才拿)。"""
    user, pw = cred
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(API + path, data=body, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode())
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=30) as r:
            return r.status, mask(json.loads(r.read().decode() or "null"))
    except urllib.error.HTTPError as e:
        try:
            err = mask(json.loads(e.read().decode()))
        except Exception:  # noqa: BLE001
            err = {"error": {"status": e.code}}
        return e.code, err
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": {"status": 0, "code": e.__class__.__name__}}


def approve(action, server, fp, env=None, platform=None, touchid=None, touchid_available=None):
    """會改機器的動作先過本人:approve_gate 彈窗 + Touch ID。回 None = 過;否則回原因代碼(不讀值、不打 API)。"""
    import approve_gate as AG
    plat = platform or sys.platform
    off = AG.ready(env, plat, touchid_available)
    if off:
        return "touchid_" + off
    reason = AG.reason("cloud_robot.apply", {"action": action, "server": str(server), "fp": fp or "-"})
    import seats_dir_lib
    root = seats_dir_lib.state_root(env)[0]
    res = AG.ask(reason, root, touchid)
    return None if res["ok"] else "touchid_" + res["why"].split(":")[0]


def cmd_status(a, ctx):
    out = {}
    cred = ctx["creds"]()
    for name, path in (("server", "/server/%s" % a.server), ("rescue", "/boot/%s/rescue" % a.server), ("reset", "/reset/%s" % a.server)):
        st, js = call("GET", path, cred=cred, opener=ctx["opener"])
        out[name] = {"http": st, "body": js}
    ctx["out"](json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all(v["http"] == 200 for v in out.values()) else 1


def cmd_keys(a, ctx):
    st, js = call("GET", "/key", cred=ctx["creds"](), opener=ctx["opener"])
    ctx["out"](json.dumps({"http": st, "keys": [k.get("key", {}) for k in (js or [])] if st == 200 else js}, ensure_ascii=False, indent=1))
    return 0 if st == 200 else 1


def _apply(a, ctx, action, path, data, ok):
    plan = {"POST": path, "data": dict((k, v) for k, v in data if k != "authorized_key[]"),
            "authorized_key[]": [v for k, v in data if k == "authorized_key[]"], "recovery": NO_ROLLBACK}
    if not a.apply:
        ctx["out"](json.dumps({"plan": plan, "apply": False}, ensure_ascii=False, indent=1))
        return 0
    why = ctx["approve"](action, a.server, ",".join(plan["authorized_key[]"]) or None)
    if why:
        ctx["out"](json.dumps({"ok": False, "why": why, "detail": "本人沒核准,沒讀金鑰、沒打 API"}, ensure_ascii=False))
        return 2
    st, js = call("POST", path, data=data, cred=ctx["creds"](), opener=ctx["opener"])
    ctx["out"](json.dumps({"http": st, "body": js, "recovery": NO_ROLLBACK}, ensure_ascii=False, indent=1))
    return 0 if st == 200 and ok(js) else 1


def cmd_rescue(a, ctx):
    fps = a.fingerprint or []
    if a.apply and not fps:
        ctx["out"](json.dumps({"ok": False, "why": "no_fingerprint", "detail": "沒帶 --fingerprint 會拿到一次性 root 密碼(被遮罩,沒人拿得到),Rescue 白排;拒絕"}, ensure_ascii=False))
        return 2
    data = [("os", "linux")] + [("authorized_key[]", f) for f in fps]
    return _apply(a, ctx, "rescue", "/boot/%s/rescue" % a.server, data, lambda js: bool((js or {}).get("rescue", {}).get("active")))


def cmd_reset(a, ctx):
    return _apply(a, ctx, "reset:" + a.type, "/reset/%s" % a.server, [("type", a.type)], lambda js: True)


def build():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("status"); s.add_argument("server"); s.set_defaults(fn=cmd_status)
    k = sp.add_parser("keys"); k.set_defaults(fn=cmd_keys)
    r = sp.add_parser("rescue"); r.add_argument("server"); r.add_argument("--fingerprint", action="append"); r.add_argument("--apply", action="store_true"); r.set_defaults(fn=cmd_rescue)
    t = sp.add_parser("reset"); t.add_argument("server"); t.add_argument("--type", default="hw"); t.add_argument("--apply", action="store_true"); t.set_defaults(fn=cmd_reset)
    return p


def main(argv=None, creds_fn=None, opener=None, approve_fn=None, out=None):
    """creds_fn / opener / approve_fn / out 可注入(測試用假層);正式用金鑰櫃 + urllib + approve_gate。"""
    a = build().parse_args(argv)
    ctx = {"creds": creds_fn or creds, "opener": opener, "approve": approve_fn or approve, "out": out or print}
    return a.fn(a, ctx)


if __name__ == "__main__":
    sys.exit(main())
