#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""會員從 ez2.page 拿外掛與授權(10-24 Andy「#20 修正」;契約 contracts/page-api.json plugins / plugins_*)。

信任:目錄只是清單、不另簽章(@abb89 10-24);信任只靠每個包自己 aib-ext.json 的信任鑰匙簽章 + 目錄給的 sha256 + 大小上限。
fetch_signed() 是「取得已簽版本」的唯一入口:aib plugins install <名稱> 走它,之後自動更新(#273)也接它。
付費包(目錄 addon 非 null)要這台綁 ez2.page 的憑證 + machine_id,帳號買過伺服器才給。
目錄 access(10-25 Andy「綁定會員就能下載」):public 不帶憑證;member 帶憑證、不查購買;paid 帶憑證 + 買過。
舊伺服器沒有 access 欄 ⇒ 照 addon 舊規則(addon 非 null = paid,否則 public)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT / "src" / "cockpit", ROOT / "src" / "cockpit" / "page", ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
VER_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
ADDON_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
ACCESS = ("public", "member", "paid")   # 契約 plugins.catalog_entry.access
CALLER_DEAD = ("page_api_token_revoked", "page_api_token_expired", "page_api_token_invalid", "page_api_token_missing")
SERVER_CODES = ("plugin_unknown", "plugin_license_none", "plugin_license_device_limit", "plugin_plan_required")   # 契約 plugins.codes
PLANS = ("free", "go", "plus", "pro", "ultra")   # 契約 plugins.catalog_entry.plan(可選;10-25 方案五級)
SALES_PLUGIN = "captain-room"   # 銷售總開關住在艦長室外掛的 aib-ext.json settings.sales(簽章範圍內;@abb89 10-26 定)
SALES_SEE_ALL_PLAN = "plus"     # off 時持簽章授權 plan ≥ 這一級的席看全貌(Andy 10-25 23:5x ④)


def sales_on(env=None):
    """銷售總開關(Andy 10-25 23:4x「只用 Free 給新用戶、不要讓他們覺得有商業行為」):回 (on, source)。
    只認裝在這台、簽章驗得過的 captain-room aib-ext.json 的 settings.sales == "on";沒裝 / 沒簽 / 驗不過 / 欄位不是 on|off
    ⇒ off(本機檔、環境變數一律不認;切開關 = 升版 + 簽章時段)。source: signed / missing / unsigned / unset。"""
    try:
        import aib_ext
        folder = aib_ext.target("plugin", SALES_PLUGIN)
        m = json.loads((Path(folder) / aib_ext.MANIFEST).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  沒裝、路徑壞、不是 JSON ⇒ off
        return False, "missing"
    if not isinstance(m, dict) or not aib_ext.signed_ok(m):
        return False, "unsigned"
    st = m.get("settings")
    v = st.get("sales") if isinstance(st, dict) else None
    if v not in ("on", "off"):
        return False, "unset"
    return v == "on", "signed"


def sales_visible(env=None):
    """帶 plan 的目錄條目這台看不看得到:開關 on,或這台的簽章方案授權 ≥ SALES_SEE_ALL_PLAN(entitlement/plan.py,#375;沒有那個模組 = 不例外)。"""
    on, _ = sales_on(env)
    if on:
        return True
    try:
        from entitlement import plan as plan_mod
        cur = plan_mod.current(dict(os.environ) if env is None else env).get("plan")
        return cur in PLANS and PLANS.index(cur) >= PLANS.index(SALES_SEE_ALL_PLAN)
    except Exception:  # noqa: BLE001
        return False
SCOPE_MISSING = "page_api_scope_mismatch"   # 契約 plugins_package / plugins_license 的 scope_missing


def max_pkg_bytes():
    import aib_plugins
    return aib_plugins.MAX_PKG_BYTES


def T(key, **kw):
    try:
        import i18n_lib
        text = i18n_lib.t("aib.plugins." + key, kw or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        text = ""
    return "" if not text or text.startswith("!!") else text


def _clean(s, n):
    """伺服器的字:去控制 / 隱形字元、截長度;單獨代理字元(\\ud800,json.loads 照收)換成 ?,不然印出時當掉。"""
    import touchid_approve as TA
    return TA.clean(str(s or "").encode("utf-8", "replace").decode("utf-8"), n)


def fail(detail, rc, **extra):
    hint = extra.pop("note", None) or (T("hint.server_refused", code=extra.pop("raw")) if "raw" in extra else "")
    hint = hint or T("hint." + detail, **{k: v for k, v in extra.items() if isinstance(v, str)}) or detail
    return dict({"ok": False, "detail": detail, "hint": hint}, **extra), rc


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def parse_spec(spec):
    """'名稱' 或 '名稱@版本' ⇒ (name, version 或 None);不合格式 ⇒ None。"""
    name, _, ver = str(spec).partition("@")
    if not NAME_RE.fullmatch(name) or (ver and not VER_RE.fullmatch(ver)) or (_ and not ver):
        return None
    return name, (ver or None)


def parse_catalog(payload):
    """伺服器目錄 ⇒ [entry];任何一筆不是契約形狀 ⇒ None(整份不信,不挑著用)。"""
    if not isinstance(payload, dict) or payload.get("v") != 1 or not isinstance(payload.get("plugins"), list):
        return None
    out = []
    for e in payload["plugins"]:
        if not isinstance(e, dict):
            return None
        name, ver, sha, size = e.get("name"), e.get("version"), e.get("sha256"), e.get("size")
        addon, req = e.get("addon"), e.get("requires_aib")
        if not (isinstance(name, str) and NAME_RE.fullmatch(name) and isinstance(ver, str) and VER_RE.fullmatch(ver)
                and isinstance(sha, str) and SHA_RE.fullmatch(sha)
                and type(size) is int and 0 < size <= max_pkg_bytes()
                and (addon is None or (isinstance(addon, str) and ADDON_RE.fullmatch(addon)))
                and (req is None or (isinstance(req, str) and VER_RE.fullmatch(req)))):
            return None
        access = e["access"] if "access" in e else ("paid" if addon is not None else "public")   # 舊伺服器沒這欄
        if access not in ACCESS or (access == "paid") != (addon is not None):
            return None
        plan = e.get("plan")   # 可選:這一級以上的方案含在內;public 不准有
        if plan is not None and (plan not in PLANS or access == "public"):
            return None
        price, summary = e.get("price"), e.get("summary")
        out.append({"name": name, "version": ver, "sha256": sha, "size": size, "access": access, "addon": addon, "plan": plan, "requires_aib": req,
                    "price": _clean(price, 40) if isinstance(price, str) else None,
                    "summary": _clean(summary, 120) if isinstance(summary, str) else None})
    return out


def pick(entries, name, version=None):
    rows = [e for e in entries if e["name"] == name and (version is None or e["version"] == version)]
    return max(rows, key=lambda e: vkey(e["version"])) if rows else None


def installed_version(name):
    """這台已裝的版本(讀 aib-ext.json);沒裝 / 讀不到 ⇒ None。"""
    import json
    import aib_ext
    try:
        m = json.loads((aib_ext.target("plugin", name) / aib_ext.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = m.get("version") if isinstance(m, dict) else None
    return v if isinstance(v, str) and VER_RE.fullmatch(v) else None


def requires_addon(folder):
    """包自己(已驗簽)說要哪個加購:aib-ext.json 的 requires_addon,沒有就看 plugin.json;同舊的 aib plugins install。"""
    import json
    import aib_ext
    for f in (aib_ext.MANIFEST, "plugin.json"):
        try:
            m = json.loads((Path(folder) / f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        a = m.get("requires_addon") if isinstance(m, dict) else None
        if a:
            return a if isinstance(a, str) else str(a)
    return None


def already_current(name, version):
    """已裝的就是目錄最新版 ⇒ 不重裝;欄位照契約 install_ok(path / addon / licensed 讀已裝那份)。"""
    import aib_ext
    import aib_plugins
    path = aib_ext.target("plugin", name)
    addon = requires_addon(path)
    ok = aib_plugins.licensed({"requires_addon": addon})[0] if addon else True
    return {"ok": True, "detail": "plugin_already_current", "name": name, "version": version, "path": str(path),
            "addon": addon, "licensed": bool(ok), "hint": T("hint.plugin_already_current") or "plugin_already_current"}


def binding(env):
    """(PageApi 帶憑證, machine_id) 或 (None, None);同 aib create 的判法。"""
    import create_cli
    return create_cli.binding(env)


def server_err(exc):
    """ApiError ⇒ (detail, rc, extra)。契約碼原樣;沒碼 / 不認得的碼收成本機碼,原碼只放 hint。"""
    try:
        return _server_err(exc)
    except Exception:  # noqa: BLE001  例:revoked_note 遇到極端日期 OverflowError
        return "plugin_refused", 3, {}


def _server_err(exc):
    code, http = exc.code if isinstance(exc.code, str) else "", exc.http_status
    if code in CALLER_DEAD:
        import revoked_note
        return code, 3, {"note": revoked_note.note(exc)}
    if code == "plugin_plan_required":   # 方案不到目錄要的級、也沒單買(10-25);回條多帶 plan = 需要的級
        need = exc.details[0] if exc.details and isinstance(exc.details[0], str) and exc.details[0] in PLANS else ""
        return code, 3, ({"plan": need} if need else {})
    if code in SERVER_CODES:
        return code, 4 if http >= 500 else 3, {}
    if http == 403 and code == SCOPE_MISSING:   # 序號綁的憑證沒有 plugins:read(10-25 @abb89)⇒ 指去用配對碼重綁
        return "plugin_scope_missing", 3, {}
    if http in (404, 405):
        return "plugin_unsupported", 3, {}
    if http >= 500:
        return "plugin_unreachable", 4, {}
    return "plugin_refused", 3, {"raw": _clean(code or "http_%s" % http, 60)}


def _public_api(env):
    from api import PageApi
    return PageApi(base_url=env.get("EZ2_PAGE_API_BASE"))


def catalog(env, api=None):
    """回 (entries, None) 或 (None, (回條, rc))。"""
    from api import ApiError, TransportError
    try:
        payload = (api or _public_api(env)).plugins_catalog()
    except ApiError as exc:
        detail, rc, extra = server_err(exc)
        return None, fail(detail, rc, **extra)
    except TransportError:
        return None, fail("plugin_unreachable", 4)
    except Exception:  # noqa: BLE001  伺服器的怪回應讓既有程式拋別的(例:details 是數字、revoked_at 極端日期)⇒ 當連不上
        return None, fail("plugin_unreachable", 4)
    entries = parse_catalog(payload)
    if entries is None:
        return None, fail("plugin_catalog_invalid", 3)
    if not sales_visible(env):   # 銷售總開關 off ⇒ 帶 plan 的條目對這台不存在(目錄 / 安裝 / 自動更新同一條路)
        entries = [e for e in entries if e.get("plan") is None]
    return entries, None


def fetch_signed(env, name, version=None, work=None, api=None, paid_api=None, machine_id=None, current=None):
    """取得已簽版本:目錄 → 挑版本 → 下載 → 比大小 / sha256 → 解壓 → 驗包簽章 + 名稱版本對得上目錄。
    回 (folder, entry, None) 或 (None, entry 或 None, (回條, rc))。
    version 沒給 ⇒ 挑最大;比 current(已裝版本)舊 ⇒ plugin_downgrade(本人明寫 @版本才放行);一樣 ⇒ plugin_already_current。
    api / paid_api / machine_id 只給測試換;沒給付費包就用這台綁的憑證。"""
    from api import ApiError, TransportError
    import aib_ext
    import aib_plugins
    import ext_kinds as K
    entries, err = catalog(env, api)
    if err:
        return None, None, err
    entry = pick(entries, name, version)
    if entry is None:
        return None, None, fail("plugin_not_in_catalog", 2)
    if current and version is None:
        if vkey(entry["version"]) < vkey(current):
            return None, entry, fail("plugin_downgrade", 2, version=entry["version"], current=current)
        if entry["version"] == current:
            return None, entry, (already_current(name, current), 0)
    member = entry["access"] != "public"   # member / paid 都要綁定憑證(伺服器另查 plugins:read;paid 再查購買)
    if member and paid_api is None:
        paid_api, machine_id = binding(env)
        if paid_api is None:
            return None, entry, fail("plugin_not_bound", 2)
    src_api = paid_api if member else (api or _public_api(env))
    try:
        data = src_api.plugins_package(entry["name"], entry["version"], entry["size"],
                                       machine_id=machine_id if member else None)
    except ApiError as exc:
        detail, rc, extra = server_err(exc)
        return None, entry, fail(detail, rc, **extra)
    except TransportError:
        return None, entry, fail("plugin_unreachable", 4)
    except Exception:  # noqa: BLE001
        return None, entry, fail("plugin_unreachable", 4)
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        return None, entry, fail("plugin_download_mismatch", 3)
    into = Path(work) / "pkg"
    into.mkdir()
    ok, why = aib_plugins._extract(data, into)
    if not ok:
        return None, entry, fail("plugin_rejected", 3, problems=[_clean(why, 200)])   # why 帶包內成員名稱 = 伺服器的字
    if (into / aib_ext.MANIFEST).is_file():
        folder = into
    else:
        subs = [d for d in into.iterdir() if d.is_dir() and (d / aib_ext.MANIFEST).is_file()]
        if len(subs) != 1:
            return None, entry, fail("plugin_rejected", 3, problems=["no-manifest"])
        folder = subs[0]
    try:   # aib-ext.json 是伺服器給的、還沒驗:不是物件 / 巢狀太深 / 怪數字 / 代理字元讓 inspect 拋什麼都算驗不過
        m, probs = aib_ext.inspect(folder, allow_unsigned=aib_plugins.UNSIGNED_OK)
        if not probs and K.kind_of_label(str(m.get("kind") or "")) != "plugin":
            probs = ["not-a-plugin:%s" % m.get("kind")]
        if not probs and (m.get("name"), m.get("version")) != (entry["name"], entry["version"]):
            probs = ["catalog-mismatch:%s@%s" % (_clean(m.get("name"), 40), _clean(m.get("version"), 20))]
    except Exception:  # noqa: BLE001
        probs = ["manifest-unreadable"]
    if probs:
        return None, entry, fail("plugin_rejected", 3, problems=[_clean(p, 200) for p in probs])
    return folder, entry, None


def would_drop(obj):
    """這台現有的授權仍有效(簽對、沒過期)而新授權少了它的加購 ⇒ 回少掉的加購(排序);否則 []。
    舊檔讀不到 / 簽不對 / 過期 ⇒ []:沒有值得保住的東西,照常換。"""
    from entitlement import signing
    from entitlement.store import read_license_obj
    from entitlement.tier import _parse_expires_at, _utc_now
    try:
        old = read_license_obj()
        if not isinstance(old, dict) or signing.verify_object(old) != signing.OK:
            return []
        exp = _parse_expires_at(old.get("expires_at"))
        if exp is not None and exp <= _utc_now():
            return []
        have = old.get("addons") if isinstance(old.get("addons"), list) else []
    except Exception:  # noqa: BLE001
        return []
    new = {a for a in obj.get("addons") or [] if isinstance(a, str)}
    return sorted({a for a in have if isinstance(a, str) and ADDON_RE.fullmatch(a) and a not in new})


def license_get(env, addon, api=None, machine_id=None):
    """aib plugins license <加購>:用這台綁的憑證領帳號買過的授權,走 aib license install 同一套驗簽 / 到期 / 0600 / 備份。"""
    from api import ApiError, TransportError
    from entitlement import cli as ECLI
    if not ADDON_RE.fullmatch(str(addon)):
        return fail("plugin_name_invalid", 2)
    if api is None:
        api, machine_id = binding(env)
        if api is None:
            return fail("plugin_not_bound", 2)
    try:
        payload = api.plugins_license(addon, machine_id)
    except ApiError as exc:
        detail, rc, extra = server_err(exc)
        return fail(detail, rc, **extra)
    except TransportError:
        return fail("plugin_unreachable", 4)
    except Exception:  # noqa: BLE001
        return fail("plugin_unreachable", 4)
    obj = payload.get("license")
    addons = obj.get("addons") if isinstance(obj, dict) else None
    if not isinstance(addons, list) or addon not in [a for a in addons if isinstance(a, str)]:
        return fail("plugin_license_invalid", 3)   # 簽對但沒有要的加購 ⇒ 不落檔(不拿小授權蓋掉大的)
    dropped = would_drop(obj)
    if dropped:   # 這台現有的有效授權加購比較多 ⇒ 不蓋(@abb89 #276 親審 Low);要換請本人 aib license install
        return fail("plugin_license_fewer", 3, addons=", ".join(dropped))
    try:
        res, rc = ECLI.install_obj(obj)
    except Exception:  # noqa: BLE001  伺服器給的授權怪到驗簽本身拋(例:代理字元)⇒ 驗不過、不落檔
        return fail("plugin_license_invalid", 3)
    if rc:
        return fail("plugin_license_invalid", 3)
    activation = "needed" if obj.get("activation_url") else None
    return {"ok": True, "detail": "plugin_license_installed", "addon": addon, "expires_at": res.get("expires_at"),
            "activation": activation,
            "hint": T("hint.activation_needed" if activation else "hint.plugin_license_installed") or "plugin_license_installed"}, 0
