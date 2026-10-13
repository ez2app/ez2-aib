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
