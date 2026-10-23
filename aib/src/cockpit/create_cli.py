#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib create submit / status / list:把外掛 / skill 做好的作品提交包送審、查狀態(契約 contracts/page-api.json creator)。

  aib create submit <包.tar> [--json]     本機重驗包 ⇒ 一次本人證明(Mac = Touch ID)⇒ 上傳 ⇒ 回編號
  aib create status <編號> [--json]        查一件(唯讀,不用本人證明)
  aib create list [--json]                 查自己送過的

只收做好的 tar(不收資料夾);本機檢查只當方便,伺服器一律重驗。不另簽包(@abb89 10-23 裁:綁定那台沒有私鑰,
同帳號 AI 模型下再簽一層不多擋);本人證明只擋這個指令 —— 同帳號 AI 直接打 API 繞得過,最壞多一件待審(契約 auth_note)。
回條(契約 creator.cli_receipt):{ok, detail, submission, hint};detail 是契約代碼、不換人話,人話只在 hint。
rc:0 成功 / 2 本機擋(什麼都沒送)/ 3 伺服器拒 / 4 網路或伺服器錯 / 5 本人證明沒過(什麼都沒送)。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE / "page"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ID_RE = re.compile(r"sub_[A-Za-z0-9]{12,40}")
SEG_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
ITEM_RE = re.compile(r"[a-z0-9_]{1,40}")
TOP_FILES = ("creator.json", "MANIFEST.json", "GATE.json")
EXT = {"assets": (".png", ".json", ".ogg", ".txt"), "prompts": (".txt",)}
MAX_DEPTH = 4
MAX_MEMBERS = 10000
CALLER_DEAD = ("page_api_token_revoked", "page_api_token_expired", "page_api_token_invalid", "page_api_token_missing")
# 契約 creator.codes 裡伺服器會回的碼(http 不是 null);不在這裡的碼 ⇒ creator_refused / creator_unreachable,detail 不帶契約外的字
SERVER_CODES = ("creator_unknown_field", "creator_schema_unsupported", "creator_category_closed", "creator_manifest_mismatch",
                "creator_path_rejected", "creator_file_type_rejected", "creator_too_large", "creator_quota_daily",
                "creator_quota_total", "creator_machine_revoked", "creator_attest_required", "creator_submission_unknown",
                "creator_submission_not_editable", "creator_revision_conflict", "creator_kind_mixed", "creator_item_unknown",
                "creator_media_type_rejected", "creator_intake_paused")
BARE_HTTP = {413: "creator_too_large", 415: "creator_media_type_rejected"}   # 前面的 CF / nginx 回純文字時沒有碼


def T(key, **kw):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.create." + key, kw or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return "" if not text or text.startswith("!!") else text


def _clean(s, n):
    import touchid_approve as TA
    return TA.clean(str(s or ""), n)


def _fail(detail, rc, **kw):
    return {"ok": False, "detail": detail, "hint": T("hint." + detail, **kw) or detail}, rc


# ── 本機重驗(只當方便;伺服器照樣重驗)──
def _magic_ok(name, data):
    if name.endswith(".png"):
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if name.endswith(".ogg"):
        return data[:4] == b"OggS"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if name.endswith(".json"):
        try:
            json.loads(text)
        except ValueError:
            return False
    return True


def _path_ok(name):
    if name.startswith("/") or "\\" in name:
        return False
    segs = name.split("/")
    return len(segs) <= MAX_DEPTH and all(SEG_RE.fullmatch(s) and s not in (".", "..") for s in segs)


def read_package(tar_bytes):
    """回 (pkg, None) 或 (None, 代碼)。pkg = {creator, files{名: bytes}, manifest_sha256, kind, items, rev, submission_id}。
    壞包讓 tarfile 拋什麼都一樣(例:壞的 GNU sparse 表拋 ValueError)⇒ creator_package_unreadable,不帶 traceback 結束。"""
    try:
        return _read_package(tar_bytes)
    except Exception:  # noqa: BLE001
        return None, "creator_package_unreadable"


def _read_package(tar_bytes):
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:")   # 只收沒壓縮的 tar
    except (tarfile.TarError, OSError, EOFError):
        return None, "creator_package_unreadable"
    files = {}
    with tf:
        members, last = [], -1
        try:
            while True:
                m = tf.next()
                if m is None:
                    break
                # 標頭 size 寫負數 ⇒ 沒修 CVE-2025-8194 的 tarfile(/usr/bin/python3 3.9)讀回同一個標頭、無限迴圈吃記憶體
                if m.size < 0 or m.offset <= last or len(members) >= MAX_MEMBERS:
                    return None, "creator_package_unreadable"
                last = m.offset
                members.append(m)
        except (tarfile.TarError, OSError, EOFError):
            return None, "creator_package_unreadable"
        for m in members:
            name = m.name[2:] if m.name.startswith("./") else m.name
            name = name.rstrip("/") if m.isdir() else name
            if not name or not _path_ok(name):
                return None, "creator_path_rejected"
            if m.isdir():
                continue
            if m.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or m.issparse() or name in files:
                return None, "creator_path_rejected"   # 連結 / 裝置檔 / 重名 / 稀疏成員(讀出來會補零 = 小包吃爆記憶體)一律不收
            top = name.split("/")[0]
            if "/" not in name:
                if name not in TOP_FILES:
                    return None, "creator_path_rejected"
            elif top not in EXT:
                return None, "creator_path_rejected"
            elif not name.lower().endswith(EXT[top]):
                return None, "creator_file_type_rejected"
            f = tf.extractfile(m)
            data = f.read() if f else b""
            if "/" in name and not _magic_ok(name.lower(), data):
                return None, "creator_file_type_rejected"
            files[name] = data
    if "creator.json" not in files or "MANIFEST.json" not in files:
        return None, "creator_manifest_mismatch"
    try:
        creator = json.loads(files["creator.json"].decode("utf-8"))
        manifest = json.loads(files["MANIFEST.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "creator_schema_unsupported"
    if not isinstance(creator, dict) or creator.get("schema") != "aib-creation/v1":
        return None, "creator_schema_unsupported"
    if not isinstance(manifest, dict) or manifest.get("schema") != "aib-creation-manifest/v1":
        return None, "creator_schema_unsupported"
    if "author" in creator:                              # 作者由伺服器依登入帳號蓋章
        return None, "creator_unknown_field"
    listed = manifest.get("files")
    if not isinstance(listed, dict) or set(listed) != set(files) - {"MANIFEST.json"}:
        return None, "creator_manifest_mismatch"
    for name, digest in listed.items():
        if not isinstance(digest, str) or hashlib.sha256(files[name]).hexdigest() != digest.lower():
            return None, "creator_manifest_mismatch"
    kind = creator.get("kind")
    items = creator.get("items")
    if not isinstance(kind, str) or not kind or not isinstance(items, list) or not items:
        return None, "creator_schema_unsupported"
    seen = set()
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("item_id"), str) or not ITEM_RE.fullmatch(it["item_id"]):
            return None, "creator_schema_unsupported"
        if it["item_id"] in seen:
            return None, "creator_schema_unsupported"
        seen.add(it["item_id"])
        if it.get("kind", kind) != kind:
            return None, "creator_kind_mixed"
    rev, sid = creator.get("rev"), creator.get("submission_id")
    if type(rev) is not int or rev < 1:
        return None, "creator_schema_unsupported"
    if sid is not None and not (isinstance(sid, str) and ID_RE.fullmatch(sid)):
        return None, "creator_id_invalid"
    if (sid is None) != (rev == 1):
        return None, "creator_schema_unsupported"
    return {"creator": creator, "files": files, "kind": kind, "items": [it["item_id"] for it in items], "rev": rev,
            "submission_id": sid, "manifest_sha256": hashlib.sha256(files["MANIFEST.json"]).hexdigest()}, None


# ── 綁定與伺服器 ──
def binding(env):
    """(PageApi, machine_id) 或 (None, None)。沒綁 / 綁了不只一頁 / 憑證沒帶 machine_id ⇒ 不送。"""
    import store
    from api import PageApi
    try:
        rec = store.only_token(env)
    except store.StoreError:
        return None, None
    if not rec or not rec.get("machine_id") or not rec.get("assistant_token"):
        return None, None
    return PageApi(base_url=env.get("EZ2_PAGE_API_BASE"), token=rec["assistant_token"]), rec["machine_id"]


def _server_err(exc):
    """ApiError ⇒ (detail, rc, 額外)。契約裡的碼原樣帶出;沒碼 / 不認得的碼收成本機碼,原碼只放 hint。"""
    code, http = exc.code or "", exc.http_status
    if code in CALLER_DEAD:
        import revoked_note
        return code, 3, {"note": revoked_note.note(exc)}
    if code in SERVER_CODES:
        return code, 4 if http >= 500 else 3, {}
    if http in BARE_HTTP:
        return BARE_HTTP[http], 3, {}
    if http in (404, 405):
        return "creator_unsupported", 3, {}
    if http >= 500:
        return "creator_unreachable", 4, {}
    return "creator_refused", 3, {"raw": _clean(code or "http_%s" % http, 60)}


def _submission(payload):
    sub = (payload or {}).get("submission")
    if not isinstance(sub, dict) or not isinstance(sub.get("id"), str) or not ID_RE.fullmatch(sub["id"]):
        return None
    return sub


def _hint_for(detail, extra):
    if extra.get("note"):
        return extra["note"]
    if extra.get("raw"):
        return T("hint.server_refused", code=extra["raw"]) or extra["raw"]
    return T("hint." + detail) or detail


def submit(env, tar_path, prove=None, api=None, machine_id=None):
    """回 (回條, rc)。prove(reason, env) ⇒ None 過 / 原因碼;api / machine_id 只給測試換。"""
    import chat_owner as CO
    try:
        data = Path(tar_path).read_bytes()
    except (OSError, ValueError):
        return _fail("creator_package_unreadable", 2)
    pkg, code = read_package(data)
    if code:
        return _fail(code, 2)
    if api is None:
        api, machine_id = binding(env)
    if api is None or not machine_id:
        return _fail("creator_not_bound", 2)
    title = pkg["creator"].get("title")
    title = next(iter(title.values()), "") if isinstance(title, dict) and title else ""
    reason = (T("touchid_reason", kind=_clean(pkg["kind"], 24), n=len(pkg["items"]), title=_clean(title, 40))
              or "送審作品:%s %d 件" % (_clean(pkg["kind"], 24), len(pkg["items"])))
    why = (prove or (lambda r, e: CO.prove(r, e)))(reason, env)
    if why:
        res, rc = _fail("creator_attest_cancelled", 5)
        res["hint"] = (T("hint.creator_attest_cancelled") or "creator_attest_cancelled") + "(" + str(why) + ")"
        return res, rc
    from api import ApiError, TransportError
    path = "/creator/submissions" + ("/%s/resubmit" % pkg["submission_id"] if pkg["submission_id"] else "")
    try:
        status, payload = api.creator_upload(path, data, machine_id, pkg["manifest_sha256"], pkg["rev"])
    except TransportError:
        return _fail("creator_unreachable", 4)
    except ApiError as exc:
        detail, rc, extra = _server_err(exc)
        return {"ok": False, "detail": detail, "hint": _hint_for(detail, extra)}, rc
    sub = _submission(payload)
    if sub is None or status not in (200, 201, 202):
        return _fail("creator_unreachable", 4)
    detail = "creator_submitted_existing" if status == 200 else "creator_submitted"
    return {"ok": True, "detail": detail, "submission": sub,
            "hint": T("hint." + detail, id=sub["id"], n=len(sub.get("items") or [])) or detail}, 0


def status(env, submission_id, api=None):
    if not isinstance(submission_id, str) or not ID_RE.fullmatch(submission_id):
        return _fail("creator_id_invalid", 2)
    if api is None:
        api, _mid = binding(env)
    if api is None:
        return _fail("creator_not_bound", 2)
    from api import ApiError, TransportError
    try:
        payload = api.creator_status(submission_id)
    except TransportError:
        return _fail("creator_unreachable", 4)
    except ApiError as exc:
        detail, rc, extra = _server_err(exc)
        return {"ok": False, "detail": detail, "hint": _hint_for(detail, extra)}, rc
    sub = _submission(payload)
    if sub is None:
        return _fail("creator_unreachable", 4)
    return {"ok": True, "detail": "creator_status", "submission": sub, "hint": ""}, 0


def list_mine(env, cursor=None, api=None):
    if api is None:
        api, _mid = binding(env)
    if api is None:
        return _fail("creator_not_bound", 2)
    from api import ApiError, TransportError
    try:
        payload = api.creator_list(cursor=cursor)
    except TransportError:
        return _fail("creator_unreachable", 4)
    except ApiError as exc:
        detail, rc, extra = _server_err(exc)
        return {"ok": False, "detail": detail, "hint": _hint_for(detail, extra)}, rc
    raw = payload.get("submissions")
    subs = [s for s in (raw if isinstance(raw, list) else []) if _submission({"submission": s})]
    nxt = payload.get("next_cursor")
    return {"ok": True, "detail": "creator_list", "submissions": subs,
            "next_cursor": nxt if isinstance(nxt, str) else None, "hint": ""}, 0


# ── 終端機輸出(伺服器給的字是資料:一律先清控制 / 隱形字元)──
def _print_sub(sub, out):
    out.write(T("line_head", id=sub["id"], state=_clean(sub.get("state"), 20), kind=_clean(sub.get("kind"), 24)) + "\n")
    for it in sub.get("items") if isinstance(sub.get("items"), list) else []:
        if isinstance(it, dict):
            out.write("  " + T("line_item", item=_clean(it.get("item_id"), 40), state=_clean(it.get("state"), 20),
                                reasons=",".join(_clean(r, 40) for r in (it.get("reasons") if isinstance(it.get("reasons"), list) else [])
                                                 if isinstance(r, str)))
                      + "\n")
            if it.get("hint"):
                out.write("    " + _clean(it["hint"], 200) + "\n")
    if sub.get("hint"):
        out.write("  " + _clean(sub["hint"], 200) + "\n")
    url = sub.get("detail_url")
    if isinstance(url, str) and url.startswith("https://ez2.page/") and _clean(url, 300) == url:
        out.write("  " + url + "\n")


def main(argv=None, env=None, out=None):
    env = dict(os.environ if env is None else env)
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="aib create", description=T("help.usage"))
    sub = ap.add_subparsers(dest="action")
    p_sub = sub.add_parser("submit", help=T("help.submit"))
    p_sub.add_argument("tar")
    p_sub.add_argument("--json", action="store_true")
    p_st = sub.add_parser("status", help=T("help.status"))
    p_st.add_argument("id")
    p_st.add_argument("--json", action="store_true")
    p_ls = sub.add_parser("list", help=T("help.list"))
    p_ls.add_argument("--cursor")
    p_ls.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "submit":
        res, rc = submit(env, a.tar)
    elif a.action == "status":
        res, rc = status(env, a.id)
    elif a.action == "list":
        res, rc = list_mine(env, cursor=a.cursor)
    else:
        ap.print_help(out)
        return 2
    if a.json:
        out.write(json.dumps(res, ensure_ascii=False) + "\n")
        return rc
    if not res["ok"]:
        out.write(_clean(res["hint"], 300) + "\n")
        return rc
    if res.get("hint"):
        out.write(_clean(res["hint"], 300) + "\n")
    for s in ([res["submission"]] if res.get("submission") else res.get("submissions") or []):
        _print_sub(s, out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
