#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席加 dm ⇒ 打開訊息待審區,而且整台記住(10-29 Andy 選 A:「整個 aib 之後新開的每一席都要開著」)。

    aib <家> <模式> <角色> [艙|new] dm      或  --dm      ⇒ on
    aib <家> <模式> <角色> [艙|new] --no-dm                ⇒ off(只取消整台記憶,已開的不關)
    其餘開席                                                ⇒ auto

待審區要三件都開才畫得出來(每艙一份):dm-queue-policy.json enabled、feature-policy.json pending_panel、
panel-layout.json pending_open(預設開)。新艙這三件沒有檔 = 關,所以以前每開一艙都要各開一次。
- on:記整台偏好(<狀態根>/state/open-dm-panel.json),這一艙三件都打開(明關過的也打開:真人這次明講了)
- auto:整台偏好開著 ⇒ 這一艙「沒設過」的那幾件打開;明關過的不硬開(那一艙真人自己關的)
- off:整台偏好改關;不碰任何一艙
方案不是進階 / 沒裝 pending-review ⇒ 印一行原因,不擋開席(rc 一律 0)。
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import atomic_json as AJ  # noqa: E402
import dm_queue as DQ  # noqa: E402
import feature_policy as FP  # noqa: E402
import license_plan as LP  # noqa: E402
import panel_layout as PL  # noqa: E402
import seats_dir_lib  # noqa: E402

SCHEMA = "aib-open-dm-panel/v1"
PLUGIN = "pending-review"
FEATURE = "pending_panel"
MODES = ("on", "off", "auto")


def pref_path(env=None):
    root, _src = seats_dir_lib.state_root(os.environ if env is None else env)
    return Path(os.path.expanduser(root)) / "state" / "open-dm-panel.json"


def read_pref(env=None):
    try:
        data = json.loads(pref_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("schema") == SCHEMA and data.get("on") is True


def write_pref(on, env=None):
    p = pref_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    AJ.write_json(str(p), {"schema": SCHEMA, "on": bool(on), "at": int(time.time())})


def _raw(path):
    """檔裡明寫的值(dict);沒檔 / 壞檔 ⇒ None(當「沒設過」)。"""
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plugin_present():
    import addon_path
    return addon_path.find(PLUGIN, "pane-pending-review.sh") is not None


def apply(mode, env=None, plugin_present=None):
    """回 {mode, pref, plan, opened:[...], kept_off:[...], why}。why ∈ ok / pref_off / not_advanced / no_cabin_home / plugin_missing。"""
    env = dict(os.environ if env is None else env)
    if mode not in MODES:
        raise ValueError("mode %r" % (mode,))
    out = {"mode": mode, "pref": None, "plan": None, "opened": [], "kept_off": [], "why": "ok"}
    if mode == "off":
        write_pref(False, env)
        out["pref"] = False
        return out
    if mode == "on":
        write_pref(True, env)
    out["pref"] = read_pref(env)
    if not out["pref"]:
        out["why"] = "pref_off"
        return out
    force = mode == "on"
    out["plan"] = LP.license_plan(env)
    if out["plan"] != LP.ADVANCED:
        out["why"] = "not_advanced"
        return out
    if FP.policy_path(env) is None:
        out["why"] = "no_cabin_home"
        return out
    q = _raw(DQ.policy_path(env))
    if force or q is None or not isinstance(q.get("enabled"), bool):
        if not (q or {}).get("enabled"):
            DQ.write_policy(True, env=env)
            out["opened"].append("dm_queue")
    elif q.get("enabled") is False:
        out["kept_off"].append("dm_queue")
    f = (_raw(FP.policy_path(env)) or {}).get("features")
    f = f.get(FEATURE) if isinstance(f, dict) else None      # feature-policy.json = {"schema", "features": {名: bool}}
    if force or not isinstance(f, bool):
        if f is not True:
            FP.write_policy(FEATURE, True, env=env)
            out["opened"].append(FEATURE)
    elif f is False:
        out["kept_off"].append(FEATURE)
    lay = _raw(FP.cabin_policy_path("panel-layout.json", env))
    if lay is not None and lay.get("pending_open") is False:
        if force:
            PL.set_open("pending", True, env=env)
            out["opened"].append("pending_open")
        else:
            out["kept_off"].append("pending_open")
    present = _plugin_present() if plugin_present is None else plugin_present
    if not present:
        out["why"] = "plugin_missing"
    return out


def message(res):
    """給真人看的一行;沒事要說 ⇒ ""。"""
    import i18n_lib
    loc = i18n_lib.resolve_locale()

    def T(k):
        return i18n_lib.t("aib.dmopen." + k, None, locale=loc)
    mode, why = res["mode"], res["why"]
    if mode == "off":
        return T("off")
    if why == "pref_off":
        return ""
    if why == "not_advanced":
        return T("not_advanced")
    if why == "no_cabin_home":
        return T("no_cabin_home") if mode == "on" else ""
    if why == "plugin_missing":
        return T("plugin_missing")
    if mode == "on":
        return T("on")
    if res["opened"]:
        return T("auto")
    return ""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else "auto"
    try:
        res = apply(mode)
        msg = message(res)
    except Exception as e:  # noqa: BLE001  開席不能因為這一步倒
        msg = "aib: dm (%s): %s" % (mode, str(e)[:160])
    if msg:
        print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
