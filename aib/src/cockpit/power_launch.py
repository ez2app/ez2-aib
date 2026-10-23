#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""艦長動力室「出艦台」的資料(契約 contracts/power.json launch 段)。

  aib power launch --list [--json]     品牌與預設模型、人格清單(含最愛)、第二大腦清單、最近 20 次出艦
  aib power launch <家> --dry-run [--model M] [--persona UID] [--brain 路由代號] [--role R] [--mode safe|dgrs]
                                       只印出艦要跑的指令(開席 → newproj 綁人格 + 第二大腦),不執行
  aib power fav <人格 uid> [--off]      人格加 / 取消最愛

10-24 Andy 01:1x:「選擇 ai 品牌與模型、人格、第二大腦,綁定後登入艦長室開始任務;第二次就可以選之前綁定過的前 20 個記錄,
或最愛的人格」。真正「出艦」(開席 + 綁人格 + 綁大腦)細節待 Andy 確認,這支先出資料;出艦時呼叫 record_launch 記一筆。
- 只回 uid / 名稱 / 代碼,不回路徑。冠位人格不列(同 aib bindme 的清單規矩)。
- 最愛 / 最近紀錄是本機方便用的資料,不是防線。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seats_dir_lib  # noqa: E402

BRANDS = ("claude", "codex", "grok", "copilot", "agy")
RECENT_MAX = 20
DAYS_MAX = 60
_TEXT_MAX = 128


def state_dir():
    return os.path.join(seats_dir_lib.state_root()[0], "state")


def _path():
    return os.path.join(state_dir(), "power-launch.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(obj):
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def _s(v):
    """紀錄裡的字串:不是字串 / 太長 / 有控制字元 ⇒ None(檔是同帳號寫得到的,讀的時候照資料看)。"""
    if not isinstance(v, str) or not v or len(v) > _TEXT_MAX:
        return None
    return None if any(ord(c) < 32 or ord(c) == 127 for c in v) else v


def models():
    """品牌 → 預設模型(providers.json 的 default_requested_model);讀不到 ⇒ None。"""
    out = {b: None for b in BRANDS}
    try:
        with open(os.path.join(HERE, "providers.json"), encoding="utf-8") as f:
            provs = json.load(f).get("providers") or []
    except (OSError, ValueError, AttributeError):
        return out
    for p in provs:
        if isinstance(p, dict) and p.get("id") in out:
            out[p["id"]] = _s(p.get("default_requested_model"))
    return out


def personas(root=None, favs=()):
    """[{uid, name, fav}],最愛在前、再照名稱排。"""
    root = root or seats_dir_lib.state_root()[0]
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "identity"))
        import bindme
        cards = bindme.persona_cards(root)
        crown = bindme.CROWN_PERSONA
    except Exception:  # noqa: BLE001
        cards, crown = {}, None
    rows = [{"uid": u, "name": _s(n) or u, "fav": u in favs} for u, n in cards.items() if _s(u) and u != crown]
    return sorted(rows, key=lambda r: (not r["fav"], r["name"]))


def recent():
    rows = []
    for r in _load().get("recent") or []:
        if not isinstance(r, dict) or r.get("brand") not in BRANDS:
            continue
        at = r.get("at")
        rows.append({"brand": r["brand"], "model": _s(r.get("model")), "persona": _s(r.get("persona")),
                     "brain": _s(r.get("brain")), "at": at if isinstance(at, int) and not isinstance(at, bool) else None})
        if len(rows) >= RECENT_MAX:
            break
    return rows


def favorites():
    return [u for u in (_load().get("favorites") or []) if _s(u)]


def _vault():
    v = os.environ.get("EZ2_VAULT_DIR")
    if v:
        return v
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "persona_route"))
        import route_register
        return route_register.default_vault()
    except Exception:  # noqa: BLE001
        return None


def brains(vault=None):
    """[{route_key, title}]:第二大腦清單只讀資料夾身分索引裡掛了路由代號的夾(folder-add --route-key 寫的那份);
    不掃 Vault 目錄猜。沒有 ⇒ 空清單(頁面給「一鍵建一個」走 newproj)。"""
    vault = vault or _vault()
    if not vault:
        return []
    try:
        with open(os.path.join(vault, ".ez2", "index", "folder-id-index.json"), encoding="utf-8") as f:
            items = json.load(f).get("items") or []
    except (OSError, ValueError, AttributeError):
        return []
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("status", "active") != "active":
            continue
        rk = _s(it.get("routeKey"))
        if rk and rk not in seen and ROUTE_RE.match(rk):
            seen.add(rk)
            out.append({"route_key": rk, "title": _s(it.get("title")) or rk})
    return sorted(out, key=lambda r: r["title"])


def launch_list():
    favs = favorites()
    rec = recent()
    return {"ok": True, "detail": "power_launch_list", "models": models(), "model_choices": model_choices(rec),
            "personas": personas(favs=set(favs)), "brains": brains(), "recent": rec, "favorites": favs}


def model_choices(rec=None):
    """{品牌: [預設模型, 之前出發用過的模型…]}。各家正式模型清單還沒有正本(providers.json 只有預設)⇒ 不自己猜 id。"""
    out = {}
    defaults = models()
    for b in BRANDS:
        seen = [defaults[b]] if defaults[b] else []
        for r in rec if rec is not None else recent():
            if r["brand"] == b and r["model"] and r["model"] not in seen and MODEL_RE.match(r["model"]):
                seen.append(r["model"])
        out[b] = seen
    return out


MODES = ("safe", "dgrs")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,95}$")
ROUTE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def launch_plan(brand, model=None, persona=None, brain=None, role="dev", mode="safe"):
    """回 (回條, rc)。只組指令(乾跑):① aib <家> <safe|dgrs> <角色> new [--model] 開席 ② aib newproj 綁人格 + 第二大腦。
    ② 要等 ① 開出席碼才填得上(<新席>)。真的執行(本人動作、AI 席要擋)等 Andy 挑版時定。"""
    def bad(why):
        return {"ok": False, "detail": "power_launch_bad", "steps": [], "hint": why}, 2
    if brand not in BRANDS:
        return bad("brand")
    if mode not in MODES:
        return bad("mode")
    if not ROLE_RE.match(role or ""):
        return bad("role")
    if model is not None and not MODEL_RE.match(model):
        return bad("model")
    if persona is not None and persona not in {p["uid"] for p in personas()}:
        return bad("persona")
    if brain is not None and brain not in {b["route_key"] for b in brains()}:
        return bad("brain")
    open_argv = ["aib", brand, mode, role, "new"] + (["--model", model] if model else [])
    steps = [{"step": "open", "argv": open_argv}]
    if persona or brain:
        bind = ["aib", "newproj", "--seat", "<new-seat>"]
        if persona:
            bind += ["--persona-uid", persona]
        if brain:
            bind += ["--folder", brain]
        steps.append({"step": "bind", "argv": bind})
    return {"ok": True, "detail": "power_launch_plan", "steps": steps, "hint": None}, 0


def set_fav(uid, on=True):
    """回 (回條, rc)。uid 要在人格清單裡。"""
    if uid not in {p["uid"] for p in personas()}:
        return {"ok": False, "detail": "power_fav_unknown", "uid": _s(uid), "favorites": favorites()}, 2
    d = _load()
    favs = [u for u in (d.get("favorites") or []) if _s(u) and u != uid]
    if on:
        favs.insert(0, uid)
    d["favorites"] = favs
    _save(d)
    return {"ok": True, "detail": "power_fav_set" if on else "power_fav_cleared", "uid": uid, "favorites": favs}, 0


def record_launch(brand, model=None, persona=None, brain=None, now=None):
    """出艦成功時記一筆:同一組(品牌 + 模型 + 人格 + 大腦)移到最前,最多留 RECENT_MAX 筆。"""
    if brand not in BRANDS:
        raise ValueError(brand)
    row = {"brand": brand, "model": _s(model), "persona": _s(persona), "brain": _s(brain),
           "at": int(time.time() if now is None else now)}
    key = (row["brand"], row["model"], row["persona"], row["brain"])
    d = _load()
    old = [r for r in recent() if (r["brand"], r["model"], r["persona"], r["brain"]) != key]
    d["recent"] = ([row] + old)[:RECENT_MAX]
    day = time.strftime("%Y-%m-%d", time.localtime(row["at"]))
    d["days"] = sorted(set(launch_days()) | {day})[-DAYS_MAX:]      # 連續出發天數(任務 SR12 / SSR11 / UR5)
    _save(d)
    return row


def launch_days():
    """出過發的日子('YYYY-MM-DD',最多留 DAYS_MAX 天)。"""
    return [x for x in (_load().get("days") or []) if isinstance(x, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", x)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="aib power")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("launch")
    s.add_argument("brand", nargs="?")
    s.add_argument("--list", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--model")
    s.add_argument("--persona")
    s.add_argument("--brain")
    s.add_argument("--role", default="dev")
    s.add_argument("--mode", default="safe")
    s.add_argument("--json", action="store_true")
    f = sub.add_parser("fav")
    f.add_argument("uid")
    f.add_argument("--off", action="store_true")
    f.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 2
    if a.cmd == "launch" and a.list and not a.brand:
        r, rc = launch_list(), 0
    elif a.cmd == "launch" and a.brand and a.dry_run and not a.list:
        r, rc = launch_plan(a.brand, a.model, a.persona, a.brain, a.role, a.mode)
    elif a.cmd == "launch":
        # 真出艦(依序跑開席 / 綁定、每步看回條、失敗就停)等 Andy 挑版定細節;現在只有 --list 與 --dry-run
        r, rc = {"ok": False, "detail": "power_launch_not_yet", "steps": [], "hint": "--dry-run"}, 2
    else:
        r, rc = set_fav(a.uid, on=not a.off)
    print(json.dumps(r, ensure_ascii=False) if a.json else json.dumps(r, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
