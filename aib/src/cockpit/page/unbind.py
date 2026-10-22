# SPDX-License-Identifier: Apache-2.0
"""aib page unbind:解除這台跟 ez2.page 帳號的綁定(@abb89 10-21 派工 0.1.38;@74e8b 實綁時發現的缺口)。

以前會員要重綁只能去 /manage/aib 後台撤銷 + 手動刪 ~/.ez2-aib/auth/page。現在:
  1. 本人 Touch ID(跟綁定同一種彈窗;視窗字由核心組、帳號名先清過)。不過 ⇒ 什麼都不動。
  2. 先叫伺服器撤銷這台的助理憑證。失敗 ⇒ 不動本機,回伺服器的錯誤碼原樣(detail 是契約;人話另放 hint)。
     伺服器說這把早就撤銷了 ⇒ 當成功(後台先撤過的情況),照樣清本機。
  3. 本機憑證原子刪除:先改名成 .unbind-* 再刪;中途斷掉 ⇒ 重跑時先把殘留的 .unbind-* 清掉。
回 (結果 dict, rc):rc 0 解除了 / 本來就沒綁;1 伺服器失敗;2 本人沒過 / 參數錯。
同帳號 AI:席位叫得到這支,但伺服器那一步要先過系統強制的 Touch ID(視窗寫明要解除哪個帳號);
本機憑證檔 AI 本來就刪得掉,這支沒有讓它多做到什麼。
"""
from __future__ import annotations

import glob
import os
import secrets

import store
from api import ApiError, TransportError

# 契約(@9f808 10-21):POST /api/v1/page/bind/revoke,bearer = 這把、body 空;不帶 machine_id(bearer 就認得是哪台)
#   ⇒ 200 {ok, revoked, token_id, request_id};只撤這一把、伺服器寫稽核。
# 伺服器說「這把已經不能用了」的碼 ⇒ 撤銷這件事已經成立,照樣清本機;invalid = 伺服器上沒這把,清之前多印一行提示
ALREADY_REVOKED = ("page_api_token_revoked", "page_api_token_expired", "page_api_token_invalid")
NOTE_CODES = ("page_api_token_invalid",)
MANAGE_URL = "https://ez2.page/manage/aib"
UNSUPPORTED = "page_unbind_unsupported"
LEFTOVER_PREFIX = ".unbind-"


def _t(key, params=None, fallback=""):
    try:
        import i18n_lib
        text = i18n_lib.t(key, params or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if text and not text.startswith("!!") and text != key else fallback


def reason(slug):
    import touchid_approve as TA
    s = TA.clean(slug, 40)
    return _t("aib.page.touchid_unbind", {"slug": s}, "解除這台跟帳號 %s 的綁定" % s)


def sweep_leftovers(env=None):
    """上次刪到一半留下的 .unbind-*(已改名、還沒刪)⇒ 刪掉。回刪了幾個。"""
    directory, _src = store.token_dir(env)
    n = 0
    for p in glob.glob(os.path.join(glob.escape(directory), LEFTOVER_PREFIX + "*")):
        try:
            os.unlink(p)
            n += 1
        except FileNotFoundError:
            pass
    return n


def remove_local(record, env=None):
    """先改名到同目錄的 .unbind-<名>.<亂碼>(同一個檔案系統,rename 是原子的),再刪。回有沒有刪到。"""
    path, _src = store.token_path(record["tenant_id"], record["page_id"], env)
    tmp = os.path.join(os.path.dirname(path), "%s%s.%s" % (LEFTOVER_PREFIX, os.path.basename(path), secrets.token_hex(4)))
    try:
        os.rename(path, tmp)
    except FileNotFoundError:
        return False
    os.unlink(tmp)
    return True


def _fail(why, rc, **extra):
    out = {"ok": False, "why": why}
    out.update(extra)
    hint = _t("aib.page.unbind_hint." + why, {"url": MANAGE_URL})
    if hint:
        out["hint"] = hint
    return out, rc


def unbind(record, api, env=None, prove=None, local_only=False):
    """record:store 讀出的那一份(None = 這台沒綁)。api:帶這把 token 的 PageApi。prove(reason, env) ⇒ (via, why)。

    local_only:只清本機(@74e8b 10-21 需求)。不叫伺服器、不跳 Touch ID —— 本機憑證檔 AI 本來就刪得掉,
    擋了也不算防線;伺服器上那把還活著 ⇒ 回 server=skipped + manage_url + hint 提醒去後台撤銷。
    """
    import seat_act as SA
    e = dict(os.environ if env is None else env)
    swept = sweep_leftovers(e)
    if not record:
        return {"ok": True, "state": "not_bound", "swept": swept}, 0
    slug = record.get("slug") or record.get("page_id") or ""
    if local_only:
        removed = remove_local(record, e)
        return {"ok": True, "state": "unbound", "slug": slug, "server": "skipped", "manage_url": MANAGE_URL,
                "local": "removed" if removed else "absent", "swept": swept, "detail": None,
                "hint": _t("aib.page.unbind_note.local_only", {"url": MANAGE_URL},
                           "只清了這台;ez2.page 上這台的憑證還有效,請到 %s 撤銷" % MANAGE_URL)}, 0
    _via, why = (prove or SA.prove)(reason(slug), e)
    if why:
        return _fail(why, 2, state="bound", slug=slug)
    server, note = "revoked", None
    revoked_hint = ""
    try:
        api.bind_revoke()
    except ApiError as exc:
        if exc.http_status in (404, 405) and exc.code not in ALREADY_REVOKED:
            # 伺服器還沒有撤銷這支(ez2-ai 排在 #42 → #40 → quote-key 之後)⇒ 明講、不動本機
            return _fail("server_unsupported", 1, state="bound", slug=slug, detail=UNSUPPORTED, manage_url=MANAGE_URL)
        if exc.code not in ALREADY_REVOKED:
            return _fail("server_refused", 1, state="bound", slug=slug, detail=exc.code or "http_%s" % exc.http_status,
                         manage_url=MANAGE_URL)
        server = "already"
        note = exc.code
        import revoked_note
        revoked_hint = revoked_note.note(exc)   # 0.1.42:伺服器說何時被誰撤 ⇒ hint(detail 照舊是原碼)
    except TransportError:
        return _fail("server_unreachable", 1, state="bound", slug=slug, detail="transport", manage_url=MANAGE_URL)
    removed = remove_local(record, e)
    out = {"ok": True, "state": "unbound", "slug": slug, "server": server, "local": "removed" if removed else "absent",
           "swept": swept, "detail": note}
    if note in NOTE_CODES:
        out["hint"] = _t("aib.page.unbind_note." + note, {"url": MANAGE_URL},
                         "ez2.page 上已經沒有這台的憑證;本機這份照樣清掉")
    if revoked_hint:
        out["hint"] = revoked_hint
    return out, 0
