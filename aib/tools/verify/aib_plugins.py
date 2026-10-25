#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib 外掛插座(10-07,開發檔 §23:「做成開關,程式要可以插拔像 plugin 一樣、可以額外賣」)。

找外掛:倉裡 plugins/<名>/plugin.json、倉外 ~/.ez2-aib/plugins/<名>/plugin.json(倉外的可以單獨發給買的人;同名以倉外為準)。
說明檔:{"name", "version", "module": "<外掛資料夾裡的 .py 檔名>", "provides": ["ship.dispatcher", …], "requires_addon": "farm"(可省)}
開關:.aib/ship.toml 的 [plugins] <名> = true;沒寫 = 關。
授權:requires_addon 有值 ⇒ 授權模組(src/entitlement)判得出有效授權(不是 free)而且授權檔 addons 列了它,才算有授權。
      判不出、壞檔、沒列 ⇒ 沒授權(fail-closed)。
核心只用 get(repo, "ship.dispatcher") 這種方式拿;拿不到 ⇒ 呼叫端照舊做本機的那一套。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _home():
    return Path(os.environ.get("EZ2_AIB_HOME") or (Path.home() / ".ez2-aib"))


def discover(repo, home_only=False):
    """{名字: 說明檔(加上 dir、where)}。倉外的同名蓋過倉裡的。壞的說明檔略過。home_only ⇒ 只看 ~/.ez2-aib/plugins。"""
    out = {}
    bases = (("home", _home() / "plugins"),) if home_only else (("repo", Path(repo) / "plugins"), ("home", _home() / "plugins"))
    for where, base in bases:
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            try:
                m = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(m, dict) or not m.get("name") or not m.get("module"):
                continue
            out[str(m["name"])] = dict(m, dir=str(d), where=where)
    return out


def switch_on(repo, name):
    try:
        sys.path.insert(0, str(ROOT / "tools" / "release"))
        import ship_profile
        cfg = ship_profile.parse_toml((Path(repo) / ".aib" / "ship.toml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 沒有設定檔 / 讀不了 ⇒ 關
        return False
    return (cfg.get("plugins") or {}).get(name) is True


def licensed(manifest, env=None):
    """(有沒有授權, 原因)。不需要加購的外掛一律 True。"""
    addon = manifest.get("requires_addon")
    if not addon:
        return True, "free"
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from entitlement.tier import get_entitlement
        from entitlement.store import read_license_obj
        ent = get_entitlement(env)
        if ent.get("tier") == "free":
            return False, "no-license:%s" % ent.get("reason")
        obj = read_license_obj(env=env) or {}
    except Exception as exc:  # noqa: BLE001 - 授權模組壞 ⇒ 當沒授權
        return False, "license-error:%s" % type(exc).__name__
    addons = obj.get("addons")
    if isinstance(addons, list) and addon in [a for a in addons if isinstance(a, str)]:
        return True, "addon"
    return False, "addon-missing:%s" % addon


def license_check_enforced():
    """授權模組的簽章檢查真的擋得住嗎?拿一個明顯是假的簽章去問:被判 ok ⇒ 還是草稿(擋不住自己寫的授權檔)。
    10-07:src/entitlement/tier.py 的 verify_signature 是草稿(待 the maintainer §108.320);換上真簽章後這裡自動變 True,不用改這支。
    判不出(模組壞、丟例外)⇒ 當擋不住(照實提醒,不假裝安全)。"""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from entitlement.tier import verify_signature
        return verify_signature({"signature": "aib-plugins-probe-not-a-real-signature"}) != "ok"
    except Exception:  # noqa: BLE001
        return False


def find_command(cmd, env=None):
    """外掛新增的子指令(10-08):說明檔 "commands": {"<子指令>": "<外掛資料夾裡的腳本>"}。
    回 (腳本路徑或 None, 外掛名或 None, 原因)。只認裝在 ~/.ez2-aib/plugins 的外掛 —— 專案倉裡的外掛不准加指令
    (不然 clone 一個陌生倉、打 aib 就跑了別人的程式)。腳本必須在外掛自己的資料夾裡;要加購的外掛沒授權 ⇒ 不給。"""
    for name, m in sorted(discover(Path.home(), home_only=True).items()):
        cmds = m.get("commands")
        if not isinstance(cmds, dict) or not isinstance(cmds.get(cmd), str):
            continue
        d = Path(m["dir"]).resolve()
        script = (d / cmds[cmd]).resolve()
        if d not in script.parents or not script.is_file():
            return None, name, "bad-command-path"
        ok, why = licensed(m, env)
        return (script if ok else None), name, why
    return None, None, "no-plugin"


def overrides_builtin(name):
    """外掛說明檔 "overrides_builtin": true ⇒ 它的指令蓋過 aib 內建的同名指令;不能跑時(沒授權)呼叫端照舊跑內建的,
    不擋(10-12:例如 `aib room`,外掛不能跑時內建的 contacts / say 照常可用)。"""
    m = discover(Path.home(), home_only=True).get(name) or {}
    return m.get("overrides_builtin") is True


def status(repo, env=None):
    """每個外掛:{name, version, where, on, licensed, why}(aib ship plugins 用)。"""
    rows = []
    for name, m in sorted(discover(repo).items()):
        ok, why = licensed(m, env)
        rows.append({"name": name, "version": m.get("version", ""), "where": m["where"], "on": switch_on(repo, name),
                     "licensed": ok, "why": why, "provides": list(m.get("provides") or []),
                     "addon": m.get("requires_addon") or None})
    return rows


def load(repo, name, env=None):
    """回 (模組, 原因);不能用 ⇒ (None, 原因:not-installed / off / no-license… / load-error)。"""
    m = discover(repo).get(name)
    if not m:
        return None, "not-installed"
    if not switch_on(repo, name):
        return None, "off"
    ok, why = licensed(m, env)
    if not ok:
        return None, why
    path = Path(m["dir"]) / str(m["module"])
    try:
        spec = importlib.util.spec_from_file_location("aib_plugin_%s" % name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 - 外掛壞掉 ⇒ 當沒有,呼叫端照舊
        return None, "load-error:%s" % type(exc).__name__
    return mod, "ok"


def get(repo, what, env=None):
    """第一個「開著、有授權、提供 what」的外掛模組;回 (模組或 None, 外掛名或 None, 原因)。"""
    why = "none-provides:%s" % what
    for name, m in sorted(discover(repo).items()):
        if what not in (m.get("provides") or []):
            continue
        mod, why = load(repo, name, env)
        if mod is not None:
            return mod, name, "ok"
        return None, name, why
    return None, None, why


MAX_PKG_BYTES = 64 * 1024 * 1024
MAX_UNPACK_BYTES = 2 * MAX_PKG_BYTES   # 解開後總大小上限(gzip 會先解到記憶體;外掛是程式 + 圖,正常遠小於這個)
MAX_UNPACK_MEMBERS = 20000
UNSIGNED_OK = False   # 付費外掛一律要信任鑰匙簽過;沒有 --allow-unsigned 這種門(刀測翻這個)
USAGE = ("usage: aib plugins licensed <addon> [--json]\n       aib plugins install <folder | .tgz | .zip | https://… | name[@version]> [--json]\n"
         "       aib plugins catalog [--json]\n       aib plugins license <addon> [--json]")


def _safe_members(names):
    """壓縮檔裡每個路徑都要是相對、不往上爬、不是絕對;不合 ⇒ 回那個名字。"""
    for n in names:
        p = Path(n)
        if not n or n.startswith(("/", "\\")) or p.is_absolute() or ".." in p.parts or n.startswith("~"):
            return n
    return None


def _gunzip_bounded(data, limit):
    """gzip ⇒ 解開的 bytes,最多 limit;超過 ⇒ None。一次最多吐 1 MiB,壓縮炸彈不會先整包解開。"""
    import zlib
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out, n, buf = [], 0, data
    while not d.eof:
        chunk = d.decompress(buf, 1 << 20)
        buf = d.unconsumed_tail
        n += len(chunk)
        if n > limit:
            return None
        out.append(chunk)
        if not chunk and not buf:
            break
    return b"".join(out)


def _extract(blob, into):
    """.tgz / .tar / .zip ⇒ 解到 into(自己的空資料夾);只解一般檔與資料夾(連結、裝置一律不要)。回 (ok, why)。
    驗簽前就擋壓縮炸彈(10-24 審查員兩輪):gzip 先有上限地解到記憶體(超過 MAX_UNPACK_BYTES ⇒ too-big-unpacked),
    tarfile 讀長檔名 / pax 標頭也就有上限;成員數 ≤ MAX_UNPACK_MEMBERS、宣告的大小總和 ≤ MAX_UNPACK_BYTES;
    size 負數 / offset 沒往後 ⇒ 壞檔(舊 tarfile CVE-2025-8194)。只收 gzip / 不壓縮的 tar / zip;解壓任何例外 ⇒ not-an-archive。"""
    try:
        return _extract_inner(blob, into)
    except Exception:  # noqa: BLE001  壞包讓 zlib / zipfile / tarfile / 檔案系統拋什麼都一樣
        return False, "not-an-archive"


def _extract_inner(blob, into):
    import io
    import tarfile
    import zipfile
    data = blob.read_bytes() if isinstance(blob, Path) else blob
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_UNPACK_MEMBERS or sum(i.file_size for i in infos) > MAX_UNPACK_BYTES:
                return False, "too-big-unpacked"
            bad = _safe_members(z.namelist())
            if bad:
                return False, "unsafe-path:%s" % bad
            for info in infos:
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    return False, "symlink:%s" % info.filename
            z.extractall(into)
        return True, ""
    if data[:2] == b"\x1f\x8b":
        data = _gunzip_bounded(data, MAX_UNPACK_BYTES)
        if data is None:
            return False, "too-big-unpacked"
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    with tf:
        members, total, last = [], 0, -1
        while True:
            m = tf.next()
            if m is None:
                break
            if m.size < 0 or m.offset <= last:
                return False, "not-an-archive"
            last, total = m.offset, total + m.size
            members.append(m)
            if len(members) > MAX_UNPACK_MEMBERS or total > MAX_UNPACK_BYTES:
                return False, "too-big-unpacked"
        bad = _safe_members([m.name for m in members])
        if bad:
            return False, "unsafe-path:%s" % bad
        for m in members:
            if not (m.isfile() or m.isdir()) or m.issparse():   # 稀疏:照 map 寫的量可以遠大於宣告的 size(審查員 10-24 第 3 輪)
                return False, "not-a-file:%s" % m.name
        if hasattr(tarfile, "data_filter"):
            tf.extractall(into, members=members, filter="data")
        else:
            tf.extractall(into, members=members)
    return True, ""


def _fetch(src, work):
    """資料夾 / 壓縮檔 / 網址 ⇒ 裝著 aib-ext.json 的資料夾(在 work 底下或就是那個資料夾)。回 (Path 或 None, why)。"""
    sys.path.insert(0, str(ROOT / "src" / "cockpit"))
    import aib_ext
    src = str(src)
    if src.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        import urllib.request
        try:
            with urllib.request.urlopen(src, timeout=60) as r:
                data = r.read(MAX_PKG_BYTES + 1)
        except Exception as exc:  # noqa: BLE001
            return None, "download-failed:%s" % type(exc).__name__
        if len(data) > MAX_PKG_BYTES:
            return None, "too-big"
    elif src.startswith("http://"):
        return None, "https-only"
    else:
        p = Path(src).expanduser()
        if p.is_dir():
            return (p if (p / aib_ext.MANIFEST).is_file() else None), ("" if (p / aib_ext.MANIFEST).is_file() else "no-manifest")
        if not p.is_file():
            return None, "not-found"
        if p.stat().st_size > MAX_PKG_BYTES:
            return None, "too-big"
        data = p.read_bytes()
    into = Path(work) / "pkg"
    into.mkdir()
    ok, why = _extract(data, into)
    if not ok:
        return None, why
    if (into / aib_ext.MANIFEST).is_file():
        return into, ""
    subs = [d for d in into.iterdir() if d.is_dir() and (d / aib_ext.MANIFEST).is_file()]
    return (subs[0], "") if len(subs) == 1 else (None, "no-manifest")


def install(src, as_json=False, out=sys.stdout):
    """aib plugins install <來源>(10-20 @abb89,會員清單 #9):驗簽章、放到 ~/.ez2-aib/plugins/<名>、順便說有沒有授權。
    驗法一律走 aib_ext.inspect(信任鑰匙簽章 + 檔案雜湊 + requires_aib),不另寫;只收 kind = plugin。
    下載 / 解壓都在自己的暫存夾,驗過才搬;驗不過一個檔都不留。rc:0 裝好;3 驗不過 / 不是外掛;2 來源拿不到。"""
    import shutil
    import tempfile
    sys.path.insert(0, str(ROOT / "src" / "cockpit"))
    import aib_ext
    import ext_kinds as K
    work = tempfile.mkdtemp(prefix="aib-plugin-in-")
    res = {"ok": False, "src": str(src)}
    try:
        folder, why = _fetch(src, work)
        if folder is None:
            res["error"] = why
            print(json.dumps(res, ensure_ascii=False)) if as_json else print("aib plugins install: %s" % why, file=sys.stderr)
            return 2
        m, probs = aib_ext.inspect(folder, allow_unsigned=UNSIGNED_OK)
        if not probs and K.kind_of_label(str(m.get("kind") or "")) != "plugin":
            probs = ["not-a-plugin:%s" % m.get("kind")]
        if probs:
            res["error"] = "rejected"
            res["problems"] = [str(p) for p in probs]
            if as_json:
                print(json.dumps(res, ensure_ascii=False))
            else:
                for p in probs:
                    print("aib plugins install: %s" % p, file=sys.stderr)
            return 3
        rc = aib_ext.install(folder, allow_unsigned=UNSIGNED_OK, out=(io_null() if as_json else out), enable_engines=False)
        if rc:
            res["error"] = "install-rc-%d" % rc
            print(json.dumps(res, ensure_ascii=False)) if as_json else print("aib plugins install: rc %d" % rc, file=sys.stderr)
            return rc
        try:                                                   # requires_addon 在外掛自己的 plugin.json(aib-ext.json 是簽章包的說明)
            pm = json.loads((Path(folder) / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pm = {}
        addon = m.get("requires_addon") or (pm.get("requires_addon") if isinstance(pm, dict) else None)
        ok, lwhy = licensed({"requires_addon": addon}) if addon else (True, "free")
        res.update({"ok": True, "name": m["name"], "version": m.get("version"), "path": str(aib_ext.target("plugin", m["name"])),
                    "addon": addon, "licensed": bool(ok), "why": lwhy})
        try:   # 10-25:開著的艙不用重開就長出待審框(只管 pending-review;失敗不擋安裝)
            import pending_pane_sync
            res["live_cabins"] = pending_pane_sync.after_install(m["name"])
        except Exception:  # noqa: BLE001
            res["live_cabins"] = []
        if as_json:
            print(json.dumps(res, ensure_ascii=False))
        elif addon:
            print("%s: %s (%s)" % (addon, "licensed" if ok else "not licensed", lwhy), file=out)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def install_named(spec, env=None, **kw):
    """aib plugins install <名稱>[@版本](10-24 #20):從 ez2.page 目錄拿已簽版本(plugin_catalog.fetch_signed),驗過才裝。
    回 (回條, rc);回條欄位照契約 plugins.cli_receipt。"""
    import shutil
    import tempfile
    sys.path.insert(0, str(ROOT / "src" / "cockpit"))
    import aib_ext
    import plugin_catalog as PC
    env = dict(os.environ) if env is None else env
    parsed = PC.parse_spec(spec)
    if parsed is None:
        return PC.fail("plugin_name_invalid", 2)
    name, version = parsed
    work = tempfile.mkdtemp(prefix="aib-plugin-dl-")
    try:
        folder, entry, err = PC.fetch_signed(env, name, version, work=work, current=PC.installed_version(name), **kw)
        if err:
            return err
        rc = aib_ext.install(folder, allow_unsigned=UNSIGNED_OK, out=io_null(), enable_engines=False)
        if rc:
            return PC.fail("plugin_rejected", 3, problems=["install-rc-%d" % rc])
        addon = PC.requires_addon(folder)   # 照驗過簽的包說的,不照目錄(目錄沒簽)
        ok, lwhy = licensed({"requires_addon": addon}) if addon else (True, "free")
        return {"ok": True, "detail": "plugin_installed", "name": name, "version": entry["version"],
                "path": str(aib_ext.target("plugin", name)), "addon": addon, "licensed": bool(ok),
                "hint": PC.T("hint.plugin_installed" if ok else "hint.plugin_installed_unlicensed", addon=addon or "")
                or "plugin_installed"}, 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def catalog_cmd(env=None, api=None):
    sys.path.insert(0, str(ROOT / "src" / "cockpit"))
    import plugin_catalog as PC
    entries, err = PC.catalog(dict(os.environ) if env is None else env, api)
    if err:
        return err
    return {"ok": True, "detail": "plugin_catalog", "plugins": entries, "hint": ""}, 0


def _safe(text):
    return text.encode("utf-8", "replace").decode("utf-8")   # 漏網的單獨代理字元換成 ?,印出時不當掉


def _emit(res, rc, as_json=False):
    if as_json:
        print(_safe(json.dumps(res, ensure_ascii=False)))
    elif res.get("detail") == "plugin_catalog":
        for e in res["plugins"]:
            print(_safe("%-24s %-10s %s%s" % (e["name"], e["version"], e["price"] or ("-" if e["addon"] else "free"),
                                              ("  " + e["summary"]) if e["summary"] else "")))
    else:
        print(_safe(str(res.get("hint") or res.get("detail"))), file=sys.stdout if res.get("ok") else sys.stderr)
        for p in res.get("problems") or ():
            print(_safe("  " + str(p)), file=sys.stderr)
    return rc


def io_null():
    import io
    return io.StringIO()


def main(argv=None):
    """aib plugins licensed <外掛名> [--json](10-15:Obsidian 標題外掛等**不在 aib 行程裡**的付費外掛,
    問 aib 這台有沒有授權用的;判準就是 licensed(),不另寫一份)。有授權 ⇒ rc 0;沒有 ⇒ rc 1;用法錯 ⇒ rc 2。
    輸出 {"addon", "licensed", "why", "signature_enforced"};signature_enforced=False 表示授權簽章檢查還是草稿,
    擋不住自己寫的授權檔(照實說,不假裝安全)。
    aib plugins install <來源> [--json](10-20):見 install()。"""
    a = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in a
    a = [x for x in a if x != "--json"]
    if len(a) == 2 and a[0] == "install":
        sys.path.insert(0, str(ROOT / "src" / "cockpit"))
        import plugin_catalog as PC
        if PC.parse_spec(a[1]) is not None and not Path(a[1]).expanduser().exists():   # 長得像名稱、本機又沒有 ⇒ 走目錄;其他照舊
            return _emit(*install_named(a[1]), as_json=as_json)
        return install(a[1], as_json=as_json)
    if a == ["catalog"]:
        return _emit(*catalog_cmd(), as_json=as_json)
    if len(a) == 2 and a[0] == "license":
        import plugin_catalog as PC
        return _emit(*PC.license_get(dict(os.environ), a[1]), as_json=as_json)
    if len(a) != 2 or a[0] != "licensed" or not a[1].replace("-", "").replace("_", "").isalnum():
        print(USAGE, file=sys.stderr)
        return 2
    ok, why = licensed({"requires_addon": a[1]})
    out = {"addon": a[1], "licensed": bool(ok), "why": why, "signature_enforced": license_check_enforced()}
    print(json.dumps(out, ensure_ascii=False) if as_json else ("%s: %s (%s)" % (a[1], "licensed" if ok else "not licensed", why)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
