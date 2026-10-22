#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib device list / lost:遺失裝置一次撤銷(Andy 10-22「全照建議」;契約 contracts/page-api.json #261)。

  aib device list [--json]                          這個帳號的機器(聊天清單 + ez2.page)
  aib device lost [--chat <機器碼>] [--page <fp>] [--json]
                                                    一次本人證明(Mac = Touch ID,視窗字核心組、寫明撤哪台)⇒
                                                    ① 聊天:主人簽新清單把那台 / 那支手機撤掉(密語照問)
                                                    ② ez2.page:POST /machines/{fp}/revoke,每把 token 各回一筆
裁示:範圍先聊天 + page(quote 等 register 做完再加);只從電腦發起(這支不在遙控白名單);兩步結果分開回。
同帳號 AI(契約 threat_model):本人證明只擋這個指令;page bearer 在本機檔,AI 可直接打 revoke = 阻斷服務、非提權,
伺服器每撤一台都通知 Email + LINE。聊天那步真正的防線是主人密語。
rc:0 要的步驟都成了 / 1 有一步失敗(看各步 why)/ 2 參數錯、本人沒過(什麼都沒動)。
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "page"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FP_RE = re.compile(r"[0-9a-f]{64}")
FP_PREFIX_RE = re.compile(r"[0-9a-f]{8,64}")
CODE_RE = re.compile(r"[02-9ac-hj-np-z]{4,8}")
MANAGE_URL = "https://ez2.page/manage/aib"
UNSUPPORTED = "page_device_revoke_unsupported"
CALLER_DEAD = ("page_api_token_revoked", "page_api_token_expired", "page_api_token_invalid", "page_api_token_missing")


def T(key, **kw):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.device." + key, kw or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return "" if not text or text.startswith("!!") else text


def _clean(s, n):
    import touchid_approve as TA
    return TA.clean(str(s or ""), n)


# ── 聊天:主人簽的裝置清單 ──
def chat_devices(env):
    """(devices, why):[{code, name, role, npub, this}];主人金鑰不在這台 ⇒ why。只讀本機上次簽出的那份,不連網。"""
    import chat_owner as CO
    import chat_owner_cli as OC
    import chat_devices as DV
    import nostr_keys as NK
    st = OC.owner_status(env)
    if st["role"] not in ("sealed", "both"):
        return [], "chat_no_owner"
    import chat_bridge as CB
    own = CO.read_own_list(CB.bridge_root(env), st["owner"])
    me = (NK.read_map(env).get("device") or {}).get("npub")
    return [{"code": DV.device_code(d["npub"]), "name": _clean(d.get("name"), 32), "role": d.get("role") or "machine",
             "npub": d["npub"], "this": d["npub"] == me} for d in own.get("devices") or []], None


def _pick_chat(devs, code):
    hit = [d for d in devs if d["code"].startswith(code)]
    if len(hit) != 1:
        return None, "chat_not_found" if not hit else "chat_ambiguous"
    if hit[0]["this"]:
        return None, "chat_is_self"
    return hit[0], None


def _chat_revoke(env, out, dev, reason, sign=None):
    import chat_owner as CO
    import chat_owner_cli as OC

    def mutate(cur, now):
        return CO.next_list(cur, now, revoke=dev["npub"])
    rc, _nxt = (sign or OC.sign_list_change)(env, out, reason, mutate, proved=True)
    if rc != 0:
        return {"state": "failed", "why": "chat_sign_failed", "code": dev["code"]}
    if dev["role"] == "phone":
        import chat_pair as CP
        try:
            CP.revoke(dev["npub"], env)
        except CP.PairError:
            pass
        CP.hold_queued_from(dev["npub"], env)
    return {"state": "revoked", "code": dev["code"], "name": dev["name"]}


# ── ez2.page ──
def page_api(env):
    """(PageApi 或 None, why)。用這台綁的那一把;沒綁 ⇒ page_not_bound。"""
    import store
    from api import PageApi
    try:
        rec = store.only_token(env)
    except store.StoreError:
        return None, "page_ambiguous"
    if not rec:
        return None, "page_not_bound"
    return PageApi(base_url=env.get("EZ2_PAGE_API_BASE"), token=rec.get("assistant_token")), None


def _page_err(exc):
    """ApiError ⇒ (why, detail):detail 是伺服器原碼(契約),人話另放 hint。"""
    code, http = exc.code, exc.http_status
    if code == "page_device_unknown":
        return "page_unknown", code
    if code == "page_device_revoke_self":
        return "page_is_self", code
    if code == "page_device_revoke_rate_limited" or http == 429:
        return "page_rate_limited", code or "http_429"
    if code in CALLER_DEAD:
        import revoked_note   # 0.1.42:這台自己被撤 ⇒ 第三格帶「何時被誰撤」(舊伺服器 = "")
        return "page_caller_dead", code, revoked_note.note(exc)
    if http in (404, 405):
        return "page_unsupported", UNSUPPORTED
    if http == 503:
        return "page_audit_failed", code or "http_503"       # 契約:撤銷已成立,重打會全是 already_revoked
    return "page_refused", code or "http_%s" % http


def page_machines(api):
    from api import ApiError, TransportError
    try:
        res = api.machines_list()
    except ApiError as exc:
        return None, _page_err(exc)
    except TransportError:
        return None, ("page_unreachable", "transport")
    out = []
    for m in (res or {}).get("machines") or []:
        fp = str(m.get("fp") or "")
        if FP_RE.fullmatch(fp):
            out.append({"fp": fp, "name": _clean(m.get("name"), 32), "last_used_at": m.get("last_used_at"),
                        "live_tokens": m.get("live_tokens"), "this": m.get("this") is True})
    return out, None


def _pick_page(api, fp):
    if FP_RE.fullmatch(fp):
        ms, _err = page_machines(api)
        hit = [m for m in ms or [] if m["fp"] == fp]
        return (hit[0] if hit else {"fp": fp, "name": "", "this": False}), None
    ms, err = page_machines(api)
    if err:
        return None, err
    hit = [m for m in ms if m["fp"].startswith(fp)]
    if len(hit) != 1:
        return None, ("page_unknown" if not hit else "page_ambiguous", None)
    return hit[0], None


def _page_revoke(api, m):
    from api import ApiError, TransportError
    if m.get("this"):
        return {"state": "failed", "why": "page_is_self", "detail": "page_device_revoke_self", "fp": m["fp"]}
    try:
        res = api.machine_revoke(m["fp"])
    except ApiError as exc:
        why, detail, *rest = _page_err(exc)
        step = {"state": "unsupported" if why == "page_unsupported" else "failed", "why": why, "detail": detail,
                "fp": m["fp"]}
        if rest and rest[0]:
            step["revoked_note"] = rest[0]
        return step
    except TransportError:
        return {"state": "failed", "why": "page_unreachable", "detail": "transport", "fp": m["fp"]}
    results = [{"kind": str(r.get("kind") or ""), "id": str(r.get("id") or ""), "outcome": str(r.get("outcome") or "")}
               for r in (res or {}).get("results") or [] if isinstance(r, dict)]   # 不認得的 kind 照列、不當失敗
    return {"state": "revoked", "fp": m["fp"], "name": m.get("name") or "", "results": results}


def _hint(step):
    why = step.get("why")
    if step.get("revoked_note"):
        return step["revoked_note"]
    return T("hint." + why, url=MANAGE_URL) if why else ""


def _day(at):
    return str(at or "")[:10] or "-"


def lost(env, chat=None, page=None, prove=None, api=None, sign=None, out=None, as_json=False):
    """回 (結果 dict, rc)。prove(reason, env) ⇒ None 過 / 原因碼。api / sign 只給測試換。
    as_json:艦長室卡片走這條,沒有終端機 ⇒ 聊天那步(要互動輸主人密語)一律先回 chat_needs_terminal、什麼都不動。"""
    import chat_owner as CO
    out = out or sys.stdout
    chat, page = (chat or "").strip().lower(), (page or "").strip().lower()
    if not chat and not page:
        return {"ok": False, "why": "nothing_to_revoke"}, 2
    if chat and not CODE_RE.fullmatch(chat):
        return {"ok": False, "why": "chat_bad_code"}, 2
    if page and not FP_PREFIX_RE.fullmatch(page):
        return {"ok": False, "why": "page_bad_fp"}, 2
    if chat and as_json:
        res = {"ok": False, "why": "chat_needs_terminal"}
        res["hint"] = _hint(res)
        return res, 2
    dev = m = None
    if chat:
        devs, why = chat_devices(env)
        dev, why = (None, why) if why else _pick_chat(devs, chat)
        if why:
            return {"ok": False, "why": why}, 2
    if page:
        if api is None:
            api, why = page_api(env)
            if why:
                return {"ok": False, "why": why}, 2
        m, err = _pick_page(api, page)
        if err:
            res = {"ok": False, "why": err[0], "detail": err[1]}
            if len(err) > 2 and err[2]:
                res["revoked_note"] = err[2]
            res["hint"] = _hint(res)
            return res, 2
    items, targets = [], []
    if dev:   # 視窗字 / 列表用到的名稱在這裡再清一次(來源不管是聊天清單還是伺服器回的)
        name = _clean(dev["name"], 32) or "-"
        targets.append({"kind": "chat", "code": dev["code"], "name": name})
        items.append(T("item_chat", name=name, code=dev["code"]) or "chat %s" % dev["code"])
    if m:
        name = _clean(m.get("name"), 32) or "-"
        targets.append({"kind": "page", "fp": m["fp"][:8], "name": name, "last_used_at": m.get("last_used_at")})
        items.append(T("item_page", name=name, fp=m["fp"][:8], at=_day(m.get("last_used_at"))) or "page %s" % m["fp"][:8])
    if not as_json:   # 提案:撤之前印出名稱 + 最後上線,本人看過才按
        out.write(T("targets_head") + "\n")
        for t in targets:
            out.write(T("target_" + t["kind"], name=t["name"], code=t.get("code") or "", fp=t.get("fp") or "",
                        at=_day(t.get("last_used_at"))) + "\n")
        out.flush()
    reason = T("touchid_reason", items="、".join(items)) or "撤銷遺失的裝置:" + "、".join(items)
    why = (prove or (lambda r, e: CO.prove(r, e)))(reason, env)
    if why:
        res = {"ok": False, "why": why, "targets": targets, "chat": {"state": "skipped"}, "page": {"state": "skipped"}}
        res["hint"] = _hint(res)
        return res, 2
    res = {"ok": True, "targets": targets}
    res["chat"] = _chat_revoke(env, out, dev, reason, sign=sign) if dev else {"state": "skipped"}
    res["page"] = _page_revoke(api, m) if m else {"state": "skipped"}
    for k in ("chat", "page"):
        h = _hint(res[k])
        if h:
            res[k]["hint"] = h
    res["ok"] = all(res[k]["state"] in ("revoked", "skipped") for k in ("chat", "page"))
    return res, 0 if res["ok"] else 1


def list_all(env, api=None):
    devs, cwhy = chat_devices(env)
    res = {"chat": {"devices": [{k: d[k] for k in ("code", "name", "role", "this")} for d in devs], "why": cwhy}}
    if api is None:
        api, pwhy = page_api(env)
    else:
        pwhy = None
    if pwhy:
        res["page"] = {"machines": [], "why": pwhy}
    else:
        ms, err = page_machines(api)
        res["page"] = {"machines": ms or [], "why": err[0] if err else None, "detail": err[1] if err else None}
        if err and len(err) > 2 and err[2]:
            res["page"]["hint"] = err[2]
    return res


def _print_list(res, out):
    out.write(T("list_chat_head") + "\n")
    for d in res["chat"]["devices"]:
        out.write(T("list_chat_row", code=d["code"], name=d["name"] or "-", role=d["role"],
                    this=T("this") if d["this"] else "") + "\n")
    if res["chat"]["why"]:
        out.write("  " + (T("hint." + res["chat"]["why"], url=MANAGE_URL) or res["chat"]["why"]) + "\n")
    out.write(T("list_page_head") + "\n")
    for m in res["page"]["machines"]:
        out.write(T("list_page_row", fp=m["fp"][:12], name=m["name"] or "-", at=m.get("last_used_at") or "-",
                    this=T("this") if m["this"] else "") + "\n")
    if res["page"]["why"]:
        out.write("  " + (res["page"].get("hint") or T("hint." + res["page"]["why"], url=MANAGE_URL)
                          or res["page"]["why"]) + "\n")


def main(argv=None, env=None, out=None):
    env = dict(os.environ if env is None else env)
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib device", description=T("help.usage"))
    sub = ap.add_subparsers(dest="action")
    p_list = sub.add_parser("list", help=T("help.list"))
    p_list.add_argument("--json", action="store_true")
    p_lost = sub.add_parser("lost", help=T("help.lost"))
    p_lost.add_argument("--chat", help=T("help.chat"))
    p_lost.add_argument("--page", help=T("help.page"))
    p_lost.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "list":
        res = list_all(env)
        if a.json:
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
        else:
            _print_list(res, out)
        return 0
    if a.action == "lost":
        res, rc = lost(env, chat=a.chat, page=a.page, out=out, as_json=a.json)
        if a.json:
            out.write(json.dumps(res, ensure_ascii=False) + "\n")
            return rc
        if res.get("why"):
            out.write((res.get("hint") or T("hint." + res["why"], url=MANAGE_URL) or res["why"]) + "\n")
            return rc
        for k in ("chat", "page"):
            st = res[k]
            if st["state"] == "skipped":
                continue
            out.write(T("step_" + k + "_" + st["state"], code=st.get("code") or "", fp=(st.get("fp") or "")[:8],
                        n=len(st.get("results") or [])) + "\n")
            if st.get("hint"):
                out.write("  " + st["hint"] + "\n")
        return rc
    ap.print_help(out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
