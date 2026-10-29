#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席加 dm ⇒ 打開訊息待審區,而且整台記住(10-29 Andy 選 A:「整個 aib 之後新開的每一席都要開著」)。

    aib <家> <模式> <角色> [艙|new] dm      或  --dm      ⇒ on
    aib <家> <模式> <角色> [艙|new] --no-dm                ⇒ off(只取消整台記憶,已開的不關)
    其餘開席                                                ⇒ auto
    aib <家> <模式> <角色> <N>sits ... dm                    ⇒ on --pref-only(批次:只記整台偏好,子席各自走 auto ⇒ 明關過的艙不開)

待審區要三件都開才畫得出來(每艙一份):dm-queue-policy.json enabled、feature-policy.json pending_panel、
panel-layout.json pending_open(預設開)。新艙這三件沒有檔 = 關,所以以前每開一艙都要各開一次。
- on:記整台偏好(<狀態根>/state/open-dm-panel.json),這一艙三件都打開(明關過的也打開:真人這次明講了)
- auto:整台偏好開著 ⇒ 這一艙「沒設過」的那幾件打開;明關過的不硬開(那一艙真人自己關的)
- auto 只認「沒檔」或「檔讀得懂但沒這個鍵」才開;讀不懂的檔(unreadable)不動、不重建(10-29 #412 R1 M4)
- off:刪掉整台偏好檔(之後開席不再起 python);不碰任何一艙
方案不是進階 / 沒裝 pending-review ⇒ 只在 on 印一行原因;auto 安靜略過(R1 M2)。不擋開席:rc 一律 0、總時限 TIME_LIMIT_S 秒(R1 M3)。
"""
import json
import os
import signal
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
TIME_LIMIT_S = 2.0      # atomic_json 每檔等鎖最多 10 秒 × 3 檔 ⇒ 不設總時限的話,鎖卡住會讓開席停 30 秒


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
    """on ⇒ 寫記憶檔;off ⇒ 刪掉(沒檔 = 關,aib 腳本看到沒檔就不起 python)。"""
    p = pref_path(env)
    if not on:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(p.parent), 0o700)
    AJ.write_json(str(p), {"schema": SCHEMA, "on": True, "at": int(time.time())})


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


def apply(mode, env=None, plugin_present=None, pref_only=False):
    """回 {mode, pref, plan, opened:[...], kept_off:[...], why}。why ∈ ok / pref_off / pref_only / not_advanced / no_cabin_home / plugin_missing。"""
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
    if pref_only:
        out["why"] = "pref_only"
        return out
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
    q, q_how = DQ.load_policy(env)
    if q_how == "ok" and q["enabled"] is True:
        pass
    elif force or q_how == "missing":
        DQ.write_policy(True, env=env)
        out["opened"].append("dm_queue")
    else:                                    # 明關過 / 讀不懂 ⇒ auto 不動(讀不懂的檔不重建)
        out["kept_off"].append("dm_queue")
    f, f_how = FP.load_policy(env)
    rows = (_raw(FP.policy_path(env)) or {}).get("features") if f_how == "ok" else None
    unset = f_how == "missing" or (f_how == "ok" and isinstance(rows, dict) and FEATURE not in rows)
    if f_how == "ok" and f[FEATURE] is True:
        pass
    elif force or unset:
        FP.write_policy(FEATURE, True, env=env)
        out["opened"].append(FEATURE)
    else:
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
    if why in ("pref_off", "pref_only"):
        return T("batch") if why == "pref_only" else ""
    if why in ("not_advanced", "no_cabin_home", "plugin_missing"):
        return T(why) if mode == "on" else ""     # auto 安靜略過:每次開席都印一行會變噪音(#412 R1 M2)
    if mode == "on":
        return T("on")
    if res["opened"]:
        return T("auto")
    return ""


class _TimeUp(Exception):
    pass


def _time_up(_sig, _frame):
    raise _TimeUp("timeout %.0fs" % TIME_LIMIT_S)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else "auto"
    timed = hasattr(signal, "setitimer")
    try:
        if timed:
            signal.signal(signal.SIGALRM, _time_up)
            signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT_S)
        res = apply(mode, pref_only="--pref-only" in argv[1:])
        msg = message(res)
    except Exception as e:  # noqa: BLE001  開席不能因為這一步倒(含逾時)
        msg = "aib: dm (%s): %s" % (mode, str(e)[:160])
    finally:
        if timed:
            signal.setitimer(signal.ITIMER_REAL, 0)
    if msg:
        print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
