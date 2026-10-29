#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""本人核准的統一入口(10-28 Andy「Touch ID 核准簽章全部重新統一」;@aeb40 TID-1,規格 SPEC-touchid-window-grants-f5fb8-1028)。

所有 Touch ID / 登入密碼視窗都經這裡:
  - reason(site, facts):在呼叫點那支模組裡組視窗那句話。site = 下面 SITES 登記過的呼叫點代碼;
    類別、句子(i18n key)、每個事實欄位的清法都由這張表定,呼叫端只給事實,不給類別、不給整句。
  - ask(r, env, ...):彈 LAContext 視窗(touchid_approve.ask),整台機器一次一個(one_at_a_time 鎖)。
  - se-sign 那幾支(aib approve / aib quote / cf grant)只拿 reason() 組好的字給 aib-sehelper,指紋由 Secure Enclave 自己彈。

類別判定(說明照實寫):
  - 呼叫端不能自報類別:API 沒有類別參數;reason() 只看直接叫它的那一層(沒有可調的深度參數),
    那支檔 resolve 後要「完全等於」登記的 src/<mod> 或 tools/<mod>(不是路徑尾端相同),
    而且那層的 globals 就是 sys.modules 裡那支模組本身、模組 __file__ 也指向同一支 ⇒ 才給表上的類別,否則「未登記」。
  - 類別 / site / 拒的原因不存在 Reason 上(Reason 沒有可寫的屬性):reason() 組好就記進本模組的登記簿,
    ask() / se_reason() / 之後的時間窗只查登記簿(認的是同一個物件,不是同樣的字)。
  - 沒登記的 site ⇒「未登記」= 高風險(之後的時間窗一律不開);純字串 / 自己造的 Reason 交給 ask() / se_reason() ⇒ 直接拒(reason_refused)。
    coerce() 只給測試用(src / tools 叫它 = 靜態掃描紅)。
  - 這些只防「核心程式裡低類別冒用 / 漏登記 / 寫錯」。同帳號的 AI 改得到這支檔本身、也能直接動登記簿
    (GUIDE-same-user-ai-threat-model),不算防線;真正的防線仍是指紋 / 登入密碼本身,一次一按。
清字(TA.clean 同一套:控制字元、方向控制、零寬字):
  - 欄位標數字 = 截到那個長度;標 FULL = 關鍵資訊(指紋、真主機、版號頭碼、原話…)不截,清完超過 FULL_MAX ⇒ 不彈窗、直接拒。
失敗 / 取消 / 逾時 / 叫不出 / 不是 Mac / AIB_TOUCHID=0 ⇒ 一律拒,不退回比較弱的方式。這一版仍一次一按(時間窗是 TID-2)。
"""
import os
import sys
import threading
from pathlib import Path

import touchid_approve as TA

FULL = "full"
FULL_MAX = 320                   # 不截欄位的上限(清完碼位數;跟 aib-sehelper maxSignReason 同)
# 下游視窗的實際上限(整句,清完碼位數):超過就拒、不截 —— 截掉的那段可能正是警告或真主機(@aeb40 #404 R1 Medium)
SE_SIGN_MAX = 320                # aib-sehelper se-sign(maxSignReason)
UNSEAL_MAX = 200                 # aib-sehelper unseal(maxUnsealReason)
LA_MAX = 160                     # LAContext 視窗(touchid_approve.ask;跟 consent.REASON_MAX 同):整句超過就拒

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
    "auto_update":    {"high_risk": True,  "max_window": 0},
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
    # TID-1b:條數 + 修訂增減 + 內容碼不截(本人拿來跟全文窗對);標題只是名字,截 40 —— 內容碼才是那一份
    # (uid 拿掉:放不下 LA_MAX,全文窗第一行有 uid)
    "newproj.approve": _s("project/new_project.py", "project_ruling", "aib.newproj.approve_touchid_reason",
                          {"title": 40, "goals": FULL, "red_lines": FULL, "code": FULL}),
    "newproj.approve_amend": _s("project/new_project.py", "project_ruling", "aib.newproj.approve_touchid_reason_amend",
                                {"title": 40, "goals": FULL, "red_lines": FULL, "rl": FULL,
                                 "add": FULL, "rm": FULL, "code": FULL}),
    # #420 r1 M1:艙名(20 字)/ 角色不截 ⇒ 人格 / 專案各截 36,en 整句最長 153 ≤ LA_MAX(截 40 時 en 是 161,會被 ask 拒)
    "newproj.boot_confirm": _s("project/seat_bootstrap.py", "seat_input", "aib.newproj.boot_confirm",
                               {"cabin": FULL, "role": FULL, "persona": 36, "project": 36}),
    "chat_cast.issue": _s("cockpit/chat_cast.py", "trusted_device", "aib.chat.cast.touchid_issue",
                          {"who": FULL, "hours": FULL, "until": FULL},
                          "Let {who} see your captain's room screen for {hours} h, until {until}",
                          suffix={"pin": ("aib.chat.cast.touchid_pin", {}, " ")}),
    # 10-29 TID-2a(@aeb40 L2 Q4):原本交純字串的兩處補登記,都列高風險(一次一按)
    "auto_update.exclude": _s("cockpit/auto_update.py", "auto_update", "aib.auto_update.exclude.touchid_reason",
                              {"name": FULL}, "Allow aib to auto-update the plugin {name}"),
    "chat_phone_forward.loosen": _s("cockpit/chat_phone_forward.py", "phone_pair", "aib.chat.phone_forward.touchid_reason",
                                    {"minutes": FULL, "per_hour": FULL},
                                    "Turn on BB Call phone forwarding: after {minutes} minutes, send \"N new letters\" "
                                    "to your paired phones, at most {per_hour} per hour"),
    "release.sign_approve": _s("release/sign_approve.py", "release_sign", "aib.release.sign_approve_reason",
                               {"plugin": FULL, "ver": FULL, "pin": FULL},
                               "Sign and release {plugin} {ver} · head {pin}"),
}


# 登記簿:id(Reason) ⇒ (那個物件本身, site, category, refused)。存物件本身 ⇒ 活著就不會撞 id;查的時候比 is。
_MINTED = {}
_MINTED_MAX = 4096               # 超過就丟最舊的(丟掉的那句再交給 ask ⇒ 拒;寧可多按一次不放行)
_MINT_LOCK = threading.Lock()    # 多執行緒同時 mint:丟最舊那段不准交錯(@aeb40 #413 r1 Low-1);本模組不在 fork 前 mint
UNMINTED = "unminted"


class Reason(str):
    """視窗那句話(就是 str,舊的比字照過)。site / category / refused 是唯讀的,查登記簿;只有 reason() 記得進去。"""
    __slots__ = ()

    @property
    def site(self):
        e = _lookup(self)
        return e[1] if e else None

    @property
    def category(self):
        e = _lookup(self)
        return e[2] if e else UNREGISTERED

    @property
    def refused(self):
        """None = 可以彈窗;否則是拒的原因(例 "fact_too_long";沒登記過的物件 = "unminted")。"""
        e = _lookup(self)
        return e[3] if e else UNMINTED

    @property
    def high_risk(self):
        return CATEGORIES.get(self.category, CATEGORIES[UNREGISTERED])["high_risk"]


def _lookup(r):
    if type(r) is not Reason:
        return None
    e = _MINTED.get(id(r))
    return e if e is not None and e[0] is r else None


def _mint(text, site, category, refused=None):
    r = Reason(text)
    with _MINT_LOCK:
        while len(_MINTED) >= _MINTED_MAX:
            _MINTED.pop(next(iter(_MINTED)))
        _MINTED[id(r)] = (r, site, category, refused)
    return r


def refusal(r):
    """不准彈窗的原因;None = 可以彈。純字串 / 沒登記過的物件 ⇒ "unminted";組不出字 ⇒ "no_text"。"""
    e = _lookup(r)
    if e is None:
        return UNMINTED
    return e[3] or (None if str(r) else "no_text")


def clean_full(text):
    """TA.clean 同一套,但不截(關鍵資訊)。"""
    return TA._CTRL.sub(" ", TA._INVISIBLE.sub("", str("" if text is None else text))).strip()


AIB_SRC = Path(__file__).resolve().parents[1]           # cockpit/* 在 aib/src
AIB_TOOLS = Path(__file__).resolve().parents[2] / "tools"   # project/*、release/* 在 aib/tools


def _resolve(path):
    try:
        return Path(path).resolve() if path else None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def module_paths(mod):
    """登記的 mod 能對到的真檔(resolve 過;src / tools 只會有一支存在)。"""
    return {p for p in (_resolve(AIB_SRC / mod), _resolve(AIB_TOOLS / mod)) if p is not None and p.is_file()}


def category_of(site, frame):
    """核心依呼叫點定類別:site 沒登記、叫的那層不是登記的那支真檔、或那層不是 sys.modules 裡那支模組在跑 ⇒ 未登記(高風險)。"""
    spec = SITES.get(site)
    if not spec or frame is None:
        return UNREGISTERED
    here = _resolve(frame.f_code.co_filename)
    if here is None or here not in module_paths(spec["mod"]):
        return UNREGISTERED
    g = frame.f_globals
    name = g.get("__name__")
    mod = sys.modules.get(name) if isinstance(name, str) else None
    if mod is None or getattr(mod, "__dict__", None) is not g:
        return UNREGISTERED
    if _resolve(getattr(mod, "__file__", None)) != here:
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


def reason(site, facts=None, suffix=(), env=None):
    """組視窗那句話。回 Reason(refused 有值 = 不准彈窗,呼叫端當沒過)。env 只用來定語系。
    類別看的是直接叫 reason() 的那一層;要包一層 helper ⇒ 把 helper 那支登記成 mod,不開深度參數。"""
    spec = SITES.get(site)
    try:
        caller = sys._getframe(1)
    except ValueError:
        caller = None
    cat = category_of(site, caller)
    del caller
    if not spec:
        return _mint(_render("aib.approve_gate.unregistered", {"site": TA.clean(site, 40)},
                             "Approve an unregistered action: {site}", env), site, UNREGISTERED)
    params, why = _clean_facts(spec["facts"], facts)
    if why:
        return _mint("", site, cat, why)
    text = _render(spec["key"], params, spec["fallback"], env)
    for name in suffix or ():
        if name not in spec["suffix"]:
            return _mint("", site, cat, "bad_suffix:" + TA.clean(name, 24))
        skey, sfacts, joiner = spec["suffix"][name]
        sp, why = _clean_facts(sfacts, facts)
        if why:
            return _mint("", site, cat, why)
        text += joiner + _render(skey, sp, None, env)
    if not text:
        return _mint("", site, cat, "no_text")
    text = clean_full(text)
    if spec["total"]:
        text = TA.clean(text, spec["total"])
    return _mint(text, site, cat)


def coerce(r, total=160):
    """只給測試:純字串 ⇒ 記成未登記(高風險)的 Reason,字照樣清過。src / tools 不准叫(靜態掃描紅);
    正式路徑交純字串給 ask() / se_reason() 一律拒。"""
    if _lookup(r) is not None:
        return r
    return _mint(TA.clean(r, total), None, UNREGISTERED)


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


def too_long_hint(res, env=None):
    """ask() 因整句超過 LA_MAX 被拒時給一句人話(代碼照舊是 refused / detail too_long);其他情況回 None。"""
    if not isinstance(res, dict) or res.get("detail") != "too_long":
        return None
    try:
        import i18n_lib
        return i18n_lib.t("aib.approve_gate.too_long_hint", None, locale=i18n_lib.resolve_locale(env=env))
    except Exception:  # noqa: BLE001  語系壞掉 ⇒ 備用句
        return "The confirmation text is too long for the Touch ID window, so it was not shown. Shorten the name and try again."


def ask(r, lock_root, touchid=None, before=None):
    """彈 Touch ID / 登入密碼視窗。回 {"ok", "why", "category", "site"};why 是 touchid_approve 的原因或 busy / refused。
    lock_root = 放 locks/touchid-approve.lock 的那層(各支照原本的位置)。呼叫端先跑 ready()。
    before():拿到整台鎖之後、彈指紋之前先跑(例:全文確認窗);回的不是 {"ok": True} ⇒ 不彈指紋,
    回 {"ok": False, "why": 它的 why, "stage": "before"}。全文窗和指紋同一把鎖,中間插不進別的視窗。
    整句超過 LA_MAX ⇒ 拒(不交給視窗去截;各支不用再自己擋)。"""
    e = _lookup(r)
    out = {"ok": False, "why": "refused", "category": e[2] if e else UNREGISTERED, "site": e[1] if e else None}
    no = refusal(r) or (None if len(str(r)) <= LA_MAX else "too_long")
    if no:                       # 純字串 / 自己造的 Reason / 欄位超長 / 組不出字 / 視窗放不下 ⇒ 不彈窗
        out["detail"] = no
        return out
    with TA.one_at_a_time(lock_path(lock_root)) as gate:
        if not gate.held:
            res = {"ok": False, "why": "busy"}
        else:
            pre = before() if before else {"ok": True}
            if not (isinstance(pre, dict) and pre.get("ok") is True):
                out.update(why=str((pre or {}).get("why") if isinstance(pre, dict) else "") or "failed", stage="before")
                return out
            res = (touchid or TA.ask)(r)
    res = res if isinstance(res, dict) else {}
    out["ok"] = res.get("ok") is True
    out["why"] = "ok" if out["ok"] else str(res.get("why") or "failed")
    return out


def se_reason(r, limit=SE_SIGN_MAX):
    """se-sign / unseal 用(以及自己拿著鎖的 consent):回能交給視窗的字;拒 ⇒ None。
    limit = 下游 helper 的整句上限(unseal 傳 UNSEAL_MAX);整句超過就拒,不交給 helper 去截。"""
    return None if refusal(r) or len(str(r)) > limit else str(r)


def category_name(cat, locale=None):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.approve_gate.cat." + cat, None, locale=locale or i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if isinstance(text, str) and text and not text.startswith("!!") else cat
