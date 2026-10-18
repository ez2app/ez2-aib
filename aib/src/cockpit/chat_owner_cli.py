#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib chat owner / join / devices 指令(開發文件第 37.7 節 K2;設計 v0.3 第 14 節)。只用標準庫;簽與發交給 venv 那一半。

  aib chat owner protect           主人金鑰改成密語加密(這台同時列進清單當 host、產機器金鑰,平常名冊改用機器金鑰發)
  aib chat owner status [--json]   主人金鑰在不在、加密了沒、這台是不是 join 進來的
  aib chat join <主人 npub> --name <這台的名字>   新機器:產機器金鑰、發配對請求、印配對碼
  aib chat devices [--json]        機器清單(持主人金鑰那台)
  aib chat devices add <配對碼> [--name <名>]     本人證明 + 密語 ⇒ 加進清單
  aib chat devices revoke <機器碼|指紋>           本人證明 + 密語 ⇒ 停用
  aib chat devices extend          本人證明 + 密語 ⇒ 重簽延長 30 天(每 7 天提醒一次)

本人證明:Mac 只准 Touch ID(不能用就拒);非 Mac 走終端機三條件。之後一律在終端機輸主人密語(關回顯)。
主人私鑰只在這個行程裡解開、從管線交給 venv 簽,盡量縮短在記憶體裡的時間(Python 不能保證清掉)。
"""
import json
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import chat_devices as DV  # noqa: E402
import chat_owner as CO  # noqa: E402
import chat_roster as CR  # noqa: E402
import nostr_keys as NK  # noqa: E402

# 只給測試換掉(行程內;不讀環境變數)
READ_PASS = CO.read_passphrase
PROVE = CO.prove


def _bridge():
    import chat_bridge as CB
    return CB


def _t(key, **kw):
    return _bridge().T(key, **kw)


def _sign(root, env, secret, devices=None, roster=None):
    rc, rec = _bridge()._run_nostr("sign-owner", root, env, timeout=60,
                                   stdin={"secret": secret, "devices": devices, "roster": roster, "relays": CR.relays(env)})
    return rc, rec


def _base_roster(env):
    try:
        return CR.build(env)[0]
    except CR.RosterError:
        return None


def _proof_then_pass(reason, env, root, out, twice=False):
    """本人證明 ⇒ 密語。回密語;不過 ⇒ None(已印原因)。"""
    why = PROVE(reason, env, root=root)
    if why:
        out.write(_t("owner_proof_failed", why=why) + "\n")
        return None
    out.write(_t("owner_pass_hint") + "\n")
    try:
        pw = READ_PASS(_t("owner_pass_ask"))
        if twice and READ_PASS(_t("owner_pass_again")) != pw:
            out.write(_t("owner_pass_mismatch") + "\n")
            return None
    except CO.OwnerError as err:
        out.write(_t("owner_err_" + str(err), n=CO.MIN_PASS) + "\n")
        return None
    return pw


def owner_status(env):
    m = NK.read_map(env)
    o = m.get("owner") or {}
    role = ("remote" if o.get("remote") else "sealed" if CO.is_sealed(env) else "local" if NK.owner_local(env) else "none")
    return {"owner": o.get("npub"), "role": role, "device": (m.get("device") or {}).get("npub")}


def cmd_owner(a, env, out):
    if a.action == "status":
        st = owner_status(env)
        out.write((json.dumps(st) if a.json else _t("owner_role_" + st["role"], owner=st["owner"] or "-")) + "\n")
        return 0
    st = owner_status(env)
    if st["role"] != "local":
        out.write(_t("owner_err_" + ("sealed" if st["role"] == "sealed" else "no_owner"), n=CO.MIN_PASS) + "\n")
        return 2
    CB = _bridge()
    root = CB.bridge_root(env)
    pw = _proof_then_pass(_t("owner_protect_reason"), env, root, out, twice=True)
    if pw is None:
        return 2
    try:
        if len(pw) < CO.MIN_PASS:
            raise CO.OwnerError("short_passphrase")
        CO.check_not_backup_pass(pw, CB.known_backups(env))
        dev = NK.ensure_device(env)
        name = (CR.read_profile(env) or {}).get("display_name") or socket.gethostname()[:32]
        now = int(time.time())
        lst = CO.next_list(CO.read_own_list(root, st["owner"]), now, add={"npub": dev, "name": name, "host": True})
        secret = NK.load_secret("owner", env)
        rc, rec = _sign(root, env, secret, devices=lst, roster=_base_roster(env))
        secret = None
        if rc != 0:
            out.write(_t("owner_sign_failed", why=rec.get("why") or "relays") + "\n")
            return 2
        CO.protect(pw, env, backups=CB.known_backups(env))
        CO.save_own_list(root, lst)
        _republish(root)
    except CO.OwnerError as err:
        out.write(_t("owner_err_" + str(err), n=CO.MIN_PASS) + "\n")
        return 2
    finally:
        pw = None
    out.write(_t("owner_protected", code=DV.device_code(dev)) + "\n")
    return 0


def _republish(root):
    """名冊改用機器金鑰發:清掉「已發」雜湊,轉接程式下一輪就重發。"""
    from atomic_json import write_json
    p = CR.published_path(root)
    try:
        rec = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    rec["hash"] = None
    write_json(str(p), rec)


def cmd_join(a, env, out):
    CB = _bridge()
    root = CB.bridge_root(env)
    try:
        NK.set_remote_owner(a.owner, env)
    except ValueError:
        out.write(_t("join_has_owner") + "\n")
        return 2
    dev = NK.ensure_device(env)
    nonce = CO.new_nonce()
    content = CO.pair_request(a.owner, dev, nonce, a.name or socket.gethostname()[:32])
    rc, rec = CB._run_nostr("pair-send", root, env, timeout=60, stdin={"content": content, "relays": CR.relays(env)})
    if rc != 0:
        out.write(_t("join_send_failed", why=(rec or {}).get("why") or "no_venv") + "\n")
        return 2
    out.write(_t("join_code", code=CO.pair_code(dev, nonce), fp=CO.fingerprint(dev), minutes=CO.PAIR_TTL_S // 60) + "\n")
    return 0


def cmd_devices(a, env, out):
    CB = _bridge()
    root = CB.bridge_root(env)
    st = owner_status(env)
    if a.action in (None, "list"):
        cur = CO.read_own_list(root, st["owner"]) if st["owner"] else {"devices": [], "revoked": [], "seq": 0}
        rows = [dict(d, code=DV.device_code(d["npub"]), fingerprint=CO.fingerprint(d["npub"])) for d in cur["devices"]]
        due = bool(st["owner"]) and CO.extend_due(root, st["owner"], time.time())
        if a.json:
            out.write(json.dumps({"role": st["role"], "owner": st["owner"], "device": st["device"], "seq": cur.get("seq"),
                                  "valid_until": cur.get("valid_until"), "extend_due": due, "devices": rows,
                                  "revoked": cur.get("revoked") or []}, ensure_ascii=False) + "\n")
            return 0
        out.write(_t("owner_role_" + st["role"], owner=st["owner"] or "-") + "\n")
        for r in rows:
            out.write(_t("devices_row", name=r.get("name") or "-", code=r["code"], fp=r["fingerprint"],
                         host=" ★" if r.get("host") else "") + "\n")
        if due:
            out.write(_t("devices_extend_due") + "\n")
        return 0
    if st["role"] != "sealed":
        out.write(_t("devices_need_protect") + "\n")
        return 2
    now = int(time.time())
    cur = CO.read_own_list(root, st["owner"])
    pending = None
    try:
        if a.action == "add":
            rc, rec = CB._run_nostr("pair-fetch", root, env, timeout=60, extra=[st["owner"]])
            if rc != 0:
                out.write(_t("devices_fetch_failed", why=(rec or {}).get("why") or "no_venv") + "\n")
                return 2
            req = CO.check_pair(root, st["owner"], a.target or "", [(r["signer"], r["content"]) for r in rec.get("requests") or []],
                                now, mark=False)
            name = a.name or req.get("name")
            reason = _t("devices_add_reason", name=name or "-", fp=CO.fingerprint(req["device"]))
            nxt = CO.next_list(cur, now, add={"npub": req["device"], "name": name})
            pending = req["nonce_hash"]
        elif a.action == "revoke":
            nxt = CO.next_list(cur, now, revoke=a.target or "")
            reason = _t("devices_revoke_reason", target=a.target)
        else:
            nxt = CO.next_list(cur, now)
            reason = _t("devices_extend_reason")
    except CO.OwnerError as err:
        out.write(_t("owner_err_" + str(err), n=CO.MIN_PASS) + "\n")
        return 2
    pw = _proof_then_pass(reason, env, root, out)
    if pw is None:
        return 2
    try:
        secret = CO.open_owner(pw, env)
    except CO.OwnerError as err:
        out.write(_t("owner_err_" + str(err), n=CO.MIN_PASS) + "\n")
        return 2
    finally:
        pw = None
    rc, rec = _sign(root, env, secret, devices=nxt, roster=_base_roster(env))
    secret = None
    if rc != 0:
        out.write(_t("owner_sign_failed", why=(rec or {}).get("why") or "relays") + "\n")
        return 2
    if pending:
        CO.mark_used(root, pending, now)
    CO.save_own_list(root, nxt)
    out.write(_t("devices_signed", seq=nxt["seq"], n=len(nxt["devices"])) + "\n")
    return 0


def off_owner_roster(env, out):
    """主人金鑰加密後 aib chat off(@abb89 11:32):當場請本人輸密語、重發撤掉的主人名冊;不輸 / 取消 ⇒ 一定提示。"""
    if not CO.is_sealed(env):
        return
    CB = _bridge()
    root = CB.bridge_root(env)
    owner = (NK.read_map(env).get("owner") or {}).get("npub")
    pw = _proof_then_pass(_t("off_owner_reason"), env, root, out)
    ok = False
    if pw is not None:
        try:
            secret = CO.open_owner(pw, env)
            rc, _rec = _sign(root, env, secret, roster=CR.empty(owner, env))
            secret = None
            ok = rc == 0
        except CO.OwnerError as err:
            out.write(_t("owner_err_" + str(err), n=CO.MIN_PASS) + "\n")
        finally:
            pw = None
    out.write(_t("off_owner_roster_" + ("done" if ok else "left")) + "\n")
