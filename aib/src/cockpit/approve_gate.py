#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""本人核准的統一入口(10-28 Andy「Touch ID 核准簽章全部重新統一」;@aeb40 TID-1,規格 SPEC-touchid-window-grants-f5fb8-1028)。

所有 Touch ID / 登入密碼視窗都經這裡:
  - reason(site, facts):在呼叫點那支模組裡組視窗那句話。site = 下面 SITES 登記過的呼叫點代碼;
    類別、句子(i18n key)、每個事實欄位的清法都由這張表定,呼叫端只給事實,不給類別、不給整句。
  - ask(r, env, ...):彈 LAContext 視窗(touchid_approve.ask),整台機器一次一個(one_at_a_time 鎖)。
  - se-sign 那幾支(aib approve / aib quote / cf grant)只拿 reason() 組好的字給 aib-sehelper,指紋由 Secure Enclave 自己彈。

類別判定(說明照實寫):
  - 呼叫端不能自報類別:API 沒有類別參數;reason() 查叫它的那支檔案,跟 SITES 登記的模組不同 ⇒ 當「未登記」。
  - 沒登記的 site、或直接把純字串交給 ask() ⇒ 「未登記」= 高風險(之後的時間窗一律不開)。
  - 這兩條只防「核心程式裡低類別冒用 / 漏登記」。同帳號的 AI 改得到這支檔本身(GUIDE-same-user-ai-threat-model),
    真正的防線仍是指紋 / 登入密碼本身,一次一按。
清字(TA.clean 同一套:控制字元、方向控制、零寬字):
  - 欄位標數字 = 截到那個長度;標 FULL = 關鍵資訊(指紋、真主機、版號頭碼、原話…)不截,清完超過 FULL_MAX ⇒ 不彈窗、直接拒。
失敗 / 取消 / 逾時 / 叫不出 / 不是 Mac / AIB_TOUCHID=0 ⇒ 一律拒,不退回比較弱的方式。這一版仍一次一按(時間窗是 TID-2)。
"""
import os
import sys
from pathlib import Path

import touchid_approve as TA

FULL = "full"
FULL_MAX = 320                   # 不截欄位的上限(清完碼位數;跟 aib-sehelper maxSignReason 同)
# 下游視窗的實際上限(整句,清完碼位數):超過就拒、不截 —— 截掉的那段可能正是警告或真主機(@aeb40 #404 R1 Medium)
SE_SIGN_MAX = 320                # aib-sehelper se-sign(maxSignReason)
UNSEAL_MAX = 200                 # aib-sehelper unseal(maxUnsealReason)

H = 3600
# 類別表:id ⇒ high_risk(不開時間窗)、max_window(秒;高風險 = 0)。四語名稱在 i18n「aib.approve_gate.cat.<id>」。
CATEGORIES = {
    "release_sign":   {"high_risk": True,  "max_window": 0},
    "payment":        {"high_risk": True,  "max_window": 0},
    "delete_data":    {"high_risk": True,  "max_window": 0},
    "trusted_device": {"high_risk": True,  "max_window": 0},
    "wp_login":       {"high_risk": True,  "max_window": 0},
    "quote":          {"high_risk": True,  "max_window": 0},
    "secret":         {"high_risk": True,  "max_window": 0},
    "cf_token":       {"high_risk": True,  "max_window": 0},
    "page_bind":      {"high_risk": True,  "max_window": 0},
    "phone_pair":     {"high_risk": True,  "max_window": 0},
    "project_ruling": {"high_risk": True,  "max_window": 0},
    "unregistered":   {"high_risk": True,  "max_window": 0},
    "seat_input":     {"high_risk": False, "max_window": 24 * H},
    "outbound":       {"high_risk": False, "max_window": 3 * H},
    "command":        {"high_risk": False, "max_window": 3 * H},
}
UNREGISTERED = "unregistered"


def _s(mod, cat, key, facts, fallback=None, suffix=None, total=None):
    return {"mod": mod, "cat": cat, "key": key, "facts": facts, "fallback": fallback, "suffix": suffix or {}, "total": total}


_SHARE = {"who": 32, "level": FULL, "space": FULL}
# 呼叫點登記表:site ⇒ 哪支模組(路徑尾端)、類別、i18n key、事實欄位清法、語系壞掉時的備用句。
SITES = {
    "chat_human.send": _s("cockpit/chat_human.py", "outbound", "aib.chat.send_touchid_reason", {"who": 32, "text": 40}),
    "chat_bridge.connect": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_connect", _SHARE),
    "chat_bridge.invite": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_invite", {}),
    "chat_bridge.share": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_share", _SHARE),
    "chat_bridge.share_default": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_share_default", _SHARE),
    "chat_bridge.share_space": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_share_space", _SHARE),
    "chat_bridge.share_space_default": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_share_space_default", _SHARE),
    "chat_bridge.space": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_space", _SHARE),
    "chat_bridge.space_default": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_space_default", _SHARE),
    "chat_bridge.open": _s("cockpit/chat_bridge.py", "outbound", "aib.chat.touchid_open", _SHARE),
    "chat_telegram.send": _s("cockpit/chat_telegram.py", "outbound", "aib.chat.telegram.touchid_send", {"who": 24, "text": 40},
                             "Send a Telegram message to {who}: {text}"),
    "chat_telegram.on": _s("cockpit/chat_telegram.py", "outbound", "aib.chat.telegram.touchid_on", {"seat": 5},
                           "Turn on the Telegram relay (into captain seat @{seat})"),
    "chat_pair.start": _s("cockpit/chat_pair.py", "phone_pair", "aib.chat.pair_reason_start", {"seat": FULL}),
    "chat_pair.confirm": _s("cockpit/chat_pair.py", "phone_pair", "aib.chat.pair_reason_confirm", {"phone": FULL}),
    "chat_pair.revoke": _s("cockpit/chat_pair.py", "phone_pair", "aib.chat.pair_reason_revoke", {"target": 24}),
    "chat_owner_cli.list_change": _s("cockpit/chat_owner_cli.py", "trusted_device", None, {"base": FULL, "diff": FULL},
                                     "{base} {diff}"),
    "chat_owner_cli.protect": _s("cockpit/chat_owner_cli.py", "trusted_device", "aib.chat.owner_protect_reason", {}),
    "chat_owner_cli.repair": _s("cockpit/chat_owner_cli.py", "trusted_device", "aib.chat.owner_repair_reason", {}),
    "chat_owner_cli.off_owner": _s("cockpit/chat_owner_cli.py", "trusted_device", "aib.chat.off_owner_reason", {}),
    "create_cli.submit": _s("cockpit/create_cli.py", "outbound", "aib.create.touchid_reason", {"kind": 24, "n": FULL, "title": 40},
                            "Submit work: {kind} x{n} {title}"),
    "device_lost.revoke": _s("cockpit/device_lost.py", "trusted_device", "aib.device.touchid_reason", {"items": FULL},
                             "Revoke lost devices: {items}"),
    "seat_act.input": _s("cockpit/seat_act.py", "seat_input", "aib.seat.panel.touchid_input", {"seat": FULL, "text": 40},
                         suffix={"tail": ("aib.seat.panel.touchid_unlock_tail", {"min": FULL}, "")}),
    "seat_act.unlock": _s("cockpit/seat_act.py", "seat_input", "aib.seat.panel.touchid_unlock", {"seat": FULL, "min": FULL}),
    "seat_act.answer": _s("cockpit/seat_act.py", "seat_input", "aib.seat.panel.touchid_answer", {"seat": FULL, "text": 40}),
    "seat_act.stop": _s("cockpit/seat_act.py", "seat_input", "aib.seat.panel.touchid_stop", {"seat": FULL, "text": 40}),
    "seat_act.stop_hard": _s("cockpit/seat_act.py", "seat_input", "aib.seat.panel.touchid_stop_hard", {"seat": FULL, "text": 40}),
    "seat_act.remote_on": _s("cockpit/seat_act.py", "trusted_device", "aib.seat.panel.touchid_remote_on", {}),
    "secret_ask.ask": _s("cockpit/secret_ask.py", "secret", "aib.secret.touchid_reason", {"name": 64, "dest": FULL}),
    "cf_grant.grant": _s("cockpit/cf_grant.py", "cf_token", "aib.cf.touchid_reason",
                         {"zone": 64, "perms": FULL, "ttl": FULL, "host": FULL},
                         suffix={"dns_write": ("aib.cf.touchid_risk.dns_write", {}, " "),
                                 "zone_settings_write": ("aib.cf.touchid_risk.zone_settings_write", {}, " ")}),
    "aib_approve.yes": _s("cockpit/aib_approve.py", "wp_login", "aib.approve.touchid_reason",
                          {"code": FULL, "site": FULL, "user": FULL, "ip": FULL},
                          "Code {code} · {site}: approve {user} signing in · IP {ip}"),
    "aib_approve.enroll": _s("cockpit/aib_approve.py", "trusted_device", "aib.approve.touchid_enroll",
                             {"kid": FULL, "site": FULL, "host": FULL, "path": FULL},
                             "kid {kid} · publish the AIB approval key to {site} ({host}) · {path}"),
    "aib_approve.publish": _s("cockpit/aib_approve.py", "trusted_device", "aib.approve.touchid_publish", {"kid": FULL},
                              "Publish the AIB approval key · kid {kid} (only publish when you are sure this Mac is untouched; "
                              "write the kid down somewhere off this Mac)"),
    "aib_quote.add": _s("cockpit/aib_quote.py", "quote", "aib.quote.touchid_reason", {"text": FULL}),
    "page_bind.bind": _s("cockpit/page/bind_bg.py", "page_bind", "aib.page.touchid_bind", {"slug": 40},
                         "Bind this Mac to account {slug}"),
    "page_unbind.unbind": _s("cockpit/page/unbind.py", "page_bind", "aib.page.touchid_unbind", {"slug": 40},
                             "Unbind this Mac from account {slug}"),
    "consent.command": _s("cockpit/consent.py", "command", "aib.consent.touchid_reason", {"who": FULL, "cmd": 160},
                          "AIB consent · {who} wants to run: {cmd}", total=160),
    "consent.sign_script": _s("cockpit/consent.py", "release_sign", "aib.consent.touchid_reason", {"who": FULL, "cmd": 160},
                              "AIB consent · {who} wants to run: {cmd}", total=160),
    "newproj.approve": _s("project/new_project.py", "project_ruling", "aib.newproj.approve_touchid_reason",
                          {"title": 60, "uid": FULL, "goals": FULL, "red_lines": FULL, "rl": FULL}),
    "newproj.approve_amend": _s("project/new_project.py", "project_ruling", "aib.newproj.approve_touchid_reason_amend",
                                {"title": 60, "uid": FULL, "goals": FULL, "red_lines": FULL, "rl": FULL}),
    "newproj.boot_confirm": _s("project/seat_bootstrap.py", "seat_input", "aib.newproj.boot_confirm",
                               {"cabin": FULL, "role": FULL, "persona": 80, "project": 80}),
    "chat_cast.issue": _s("cockpit/chat_cast.py", "trusted_device", "aib.chat.cast.touchid_issue",
                          {"who": FULL, "hours": FULL, "until": FULL},
                          "Let {who} see your captain's room screen for {hours} h, until {until}",
                          suffix={"pin": ("aib.chat.cast.touchid_pin", {}, " ")}),
    "release.sign_approve": _s("release/sign_approve.py", "release_sign", "aib.release.sign_approve_reason",
                               {"plugin": FULL, "ver": FULL, "pin": FULL},
                               "Sign and release {plugin} {ver} · head {pin}"),
}


class Reason(str):
    """視窗那句話(就是 str,舊的比字照過),多帶 site / category / refused。只由 reason() 造。"""
    site = None
    category = UNREGISTERED
    refused = None               # None = 可以彈窗;否則是拒的原因(例 "fact_too_long")

    @property
    def high_risk(self):
        return CATEGORIES.get(self.category, CATEGORIES[UNREGISTERED])["high_risk"]


def _make(text, site, category, refused=None):
    r = Reason(text)
    r.site, r.category, r.refused = site, category, refused
    return r


def clean_full(text):
    """TA.clean 同一套,但不截(關鍵資訊)。"""
    return TA._CTRL.sub(" ", TA._INVISIBLE.sub("", str("" if text is None else text))).strip()


def _caller_file(depth):
    try:
        return Path(sys._getframe(depth + 1).f_code.co_filename).as_posix()
    except (ValueError, AttributeError):
        return ""


def category_of(site, caller_file):
    """核心依呼叫點定類別:site 沒登記、或叫的檔不是登記的那支 ⇒ 未登記(高風險)。"""
    spec = SITES.get(site)
    if not spec or not str(caller_file).endswith("/" + spec["mod"]):
        return UNREGISTERED
    return spec["cat"]


def _clean_facts(spec_facts, facts):
    out = {}
    for name, how in spec_facts.items():
        v = clean_full((facts or {}).get(name, ""))
        if how == FULL:
            if len(v) > FULL_MAX:
                return None, "fact_too_long:" + name
        else:
            v = TA.clean(v, how)
        out[name] = v
    return out, None


def _render(key, params, fallback, env=None):
    text = ""
    if key:
        try:
            import i18n_lib
            text = i18n_lib.t(key, params or None, locale=i18n_lib.resolve_locale(env=env))
        except Exception:  # noqa: BLE001
            text = ""
        if not isinstance(text, str) or not text or text.startswith("!!") or text == key:
            text = ""
    if not text and fallback:
        try:
            text = fallback.format(**params)
        except (KeyError, IndexError, ValueError):
            text = ""
    return text


def reason(site, facts=None, suffix=(), env=None, _depth=1):
    """組視窗那句話。回 Reason(refused 有值 = 不准彈窗,呼叫端當沒過)。env 只用來定語系。"""
    spec = SITES.get(site)
    cat = category_of(site, _caller_file(_depth))
    if not spec:
        return _make(_render("aib.approve_gate.unregistered", {"site": TA.clean(site, 40)},
                             "Approve an unregistered action: {site}", env), site, UNREGISTERED)
    params, why = _clean_facts(spec["facts"], facts)
    if why:
        return _make("", site, cat, why)
    text = _render(spec["key"], params, spec["fallback"], env)
    for name in suffix or ():
        if name not in spec["suffix"]:
            return _make("", site, cat, "bad_suffix:" + TA.clean(name, 24))
        skey, sfacts, joiner = spec["suffix"][name]
        sp, why = _clean_facts(sfacts, facts)
        if why:
            return _make("", site, cat, why)
        text += joiner + _render(skey, sp, None, env)
    if not text:
        return _make("", site, cat, "no_text")
    text = clean_full(text)
    if spec["total"]:
        text = TA.clean(text, spec["total"])
    return _make(text, site, cat)


def coerce(r, total=160):
    """純字串(沒走 reason())⇒ 未登記 = 高風險,字照樣清過。"""
    if isinstance(r, Reason):
        return r
    return _make(TA.clean(r, total), None, UNREGISTERED)


def ready(env=None, platform=None, available=None):
    """能不能彈:None = 能;否則 "off" / "unavailable"。不是 Mac 這條路不存在(各支照原本 fallback)。"""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return "unavailable"
    if str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return "off"
    can = available() if available else TA.available(platform=platform, environ=env)
    return None if can else "unavailable"


def lock_path(root):
    return Path(root) / "locks" / "touchid-approve.lock"


def state_root_default(env=None):
    """沒有自己狀態根的呼叫點用這個放鎖(同單源順位:EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)。"""
    import seats_dir_lib
    return seats_dir_lib.state_root(env)[0]


def ask(r, lock_root, touchid=None, total=160):
    """彈 Touch ID / 登入密碼視窗。回 {"ok", "why", "category", "site"};why 是 touchid_approve 的原因或 busy / refused。
    lock_root = 放 locks/touchid-approve.lock 的那層(各支照原本的位置)。呼叫端先跑 ready()。"""
    r = coerce(r, total)
    out = {"ok": False, "why": "refused", "category": r.category, "site": r.site}
    if r.refused or not str(r):
        out["detail"] = r.refused or "no_text"
        return out
    with TA.one_at_a_time(lock_path(lock_root)) as gate:
        res = (touchid or TA.ask)(r) if gate.held else {"ok": False, "why": "busy"}
    res = res if isinstance(res, dict) else {}
    out["ok"] = res.get("ok") is True
    out["why"] = "ok" if out["ok"] else str(res.get("why") or "failed")
    return out


def se_reason(r, limit=SE_SIGN_MAX):
    """se-sign / unseal 用(以及自己拿著鎖的 consent):回能交給視窗的字;拒 ⇒ None。
    limit = 下游 helper 的整句上限(unseal 傳 UNSEAL_MAX);整句超過就拒,不交給 helper 去截。"""
    r = coerce(r)
    return None if r.refused or not str(r) or len(str(r)) > limit else str(r)


def category_name(cat, locale=None):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.approve_gate.cat." + cat, None, locale=locale or i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if isinstance(text, str) and text and not text.startswith("!!") else cat
