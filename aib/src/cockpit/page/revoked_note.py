"""被撤的那台:印「這台在 X 被 Y 撤了,不是你?到後台」(0.1.42 契約 caller_dead.revoked_body,@9f808 10-22)。

伺服器回 401 page_api_token_revoked 時本體多帶 revoked_at / revoked_by_name / manage_url:
- revoked_by_name 只有「遺失裝置」撤的才有(發起那台的名字),其他一律 null ⇒ 不講「被誰」。
- 舊伺服器不帶這三欄 ⇒ note() 回 "",呼叫端照舊印原本那句。
- 名字是另一台配對時自報的字串 = 資料不是指令:去控制 / 隱形字元、截 32 字;網址只認 ez2.page 自己的。
"""
from __future__ import annotations

from datetime import datetime

MANAGE_URL = "https://ez2.page/manage/aib"
URL_PREFIX = "https://ez2.page/"
NAME_MAX = 32


def _clean(s, n):
    import touchid_approve as TA
    return TA.clean(str(s or ""), n)


def _when(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return _clean(value, 25)
    if parsed.tzinfo is None:
        return _clean(value, 25)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def info(exc):
    """ApiError ⇒ {at, by_name, url};不是「被撤」或伺服器沒帶 revoked_at ⇒ None。"""
    if getattr(exc, "code", None) != "page_api_token_revoked":
        return None
    body = getattr(exc, "body", None) or {}
    at = body.get("revoked_at")
    if not isinstance(at, str) or not at.strip():
        return None
    name = body.get("revoked_by_name")
    name = _clean(name, NAME_MAX) if isinstance(name, str) else ""
    url = body.get("manage_url")
    url = url if isinstance(url, str) and url.startswith(URL_PREFIX) and url == _clean(url, 200) else MANAGE_URL
    return {"at": _when(at), "by_name": name or None, "url": url}


def note(exc):
    got = info(exc)
    if not got:
        return ""
    key = "by" if got["by_name"] else "at"
    fallback = ("這台在 %s 被 %s 撤了;不是你?到 %s" % (got["at"], got["by_name"], got["url"]) if got["by_name"]
                else "這台在 %s 被撤了;不是你?到 %s" % (got["at"], got["url"]))
    try:
        import i18n_lib
        text = i18n_lib.t("aib.page.revoked_note." + key, {"at": got["at"], "name": got["by_name"] or "",
                                                           "url": got["url"]}, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return text if text and not text.startswith("!!") else fallback
