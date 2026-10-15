#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""任命唯一入口(the maintainer 2026-08-18 直令:所有任命/指派只能這樣做,不這麼做不能指派)。

## 為什麼要這支

08-18 我(艦外指揮)宣布「dev4 升第二艦長」——發了 dm、通知了艦長、寫進回報,
**但系統完全不知道**:席位檔沒有任何欄位改變,右欄照舊顯示 👤。the maintainer 問「root2
為什麼不是第二艦長圖示」時才發現,那個人事案只存在於對話裡。

這是本 repo 一直在消滅的形態(自報 vs 機器可驗)的第三次發作(前兩次:我立了
seat-gate 卻自己開席沒過閘、裁契約正本時只查檔案系統沒對 git)。三次形狀相同:
**下了規則,然後在自己執行時跳過驗證那一步**。

所以規則升級為機器閘:**任命=寫席位檔的動作,沒有這個動作就沒有任命**。
口頭、dm、回報、commit 訊息都不構成任命。

## 三種任命與各自的正當入口

| 任命 | 落點 | 入口 |
|---|---|---|
| 受派艦長(現任) | 席位檔 `captain_assigned: true` | `appoint.py captain <wid>` → 委派 captains-ctl |
| 顧問 | 席位檔 `advisor: true` | `appoint.py advisor <wid>` → 委派 captains-ctl |
| 繼任者(儲君) | `rotation/<seat>/scout.jsonl` | `appoint.py successor <seat> --to <role>` |
| 艦隊編組(F1) | 席位檔 `fleet_id: "<id>"` | `appoint.py fleet <wid> --to <fleet_id>` |

★ **受派艦長的語意是「取代現任」不是「增加一位」**(pane-right-ai.sh:631 既有裁決:
顯式指派勝過 started 預設,否則指派等於沒有)。要「第二艦長」不需要這個欄位——
同 role 的第二席自然排在 ⚓;誤用會把現任擠下去(我 08-18 親自踩過,已撤銷)。
本工具對此**主動警告**,不靜默執行。

## 閘

- `verify <wid>`:席位檔是否真的有該欄位;沒有=rc3(「你宣布過但沒做」)
- 每次任命寫 `~/.ez2-aib/logs/appointments.jsonl`(誰、何時、據何授權)
- **繼任者只能是主管角色**(閘已在 rotation 側,本工具先擋一次並給出人話原因)

CLI:
    appoint.py captain <wid> --by <actor> --reason "..."
    appoint.py advisor <wid> --by <actor> --reason "..."
    appoint.py successor <seat> --to <role> --by <actor> --reason "..."
    appoint.py fleet <wid> --to <fleet_id> --by <actor> --reason "..."
    appoint.py verify <wid>
rc:0 成立 / 2 用法或環境錯 / 3 未任命(verify)或被閘擋下
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ED.1:狀態根可注入(原為純硬編:注入暫存根後這一支仍寫進真 HOME)。
# ★ ED.1 順位(root 17:53 採納):EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib,
#   走 seats_dir_lib.state_root() 單源,不在這裡再抄一份 env 鏈。
sys.path.insert(0, str(HERE))
from seats_dir_lib import state_root as _state_root   # noqa: E402
LOG = os.path.join(_state_root()[0], "logs", "appointments.jsonl")

# 繼任者角色白名單=policy/lead_roles **正本**(艦外 21:33 單一真相源刀:
# 入口與驗證端同一份,誰都不得比誰窄)。lead 字面已刪(全庫零席零引用,
# 出處=本檔初版 bd1da58 手寫;root2 主審若裁留,正本加回一字即可)。
# ★ 這裡再擋一次不是重造判準,是為了給人話原因——rotation 的 rc2 只說「拒絕」,
#   而使用者需要知道「因為 dev 席不能當繼任者」。真正的執法仍在 rotation。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "policy"))
from lead_roles import LEAD_ROLES as SUCCESSOR_ROLES  # noqa: E402
from lead_roles import is_eligible_lead  # noqa: E402


def seats_dir():
    """席位目錄。★ 08-18 艦長/dev4 抓到的假陰性:`aib appoint` 在 aib:124 就
    `exec`,而 cockpit-env.sh 到 :243 才 source ——**子命令永遠拿不到
    EZ2_AIB_INSTANCE_HOME**,於是 verify 讀預設 ~/.ez2-aib/seats,對艙內
    真有任命的席回 rc3「無任命紀錄」。假陰性比假陽性更毒:它會讓一個做對的
    人被判成沒做,然後有人去「補做」一次。
    修法:本工具自己解析 instance,不依賴被 source(與 dm-ctl 自解 hub 同紀律)。
    順位:顯式 env > --instance 旗標 > EZ2_AIB_INSTANCE 推導 > 預設。"""
    # EH.2:HUB 導出排最前(單一真相源 seats_dir_lib);HUB 說不出艙時,
    # 保留本工具原本的 --instance / EZ2_AIB_INSTANCE 推導(那是 08-18 的修法,
    # 理由在上面 docstring,不可拿掉)。
    hub = os.environ.get("AIB_DM_SOCKET") or os.environ.get("EZ2_AIHUB_SOCKET")
    if hub and hub.startswith("aihub-") and hub[len("aihub-"):]:
        from seats_dir_lib import seats_dir as _sd   # noqa: E402
        return Path(_sd())
    root = _state_root()[0]
    home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
    if not home:
        inst = os.environ.get("EZ2_AIB_INSTANCE")
        if inst:
            cand = os.path.join(root, "instances", str(inst))
            if os.path.isdir(cand):
                home = cand
    return Path(home or root) / "seats"


def seat_file(wid):
    return seats_dir() / ("w%s.json" % str(wid).lstrip("@"))


def read_seat(wid):
    try:
        with open(seat_file(wid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def log_appointment(kind, target, by, reason, ok, detail=""):
    rec = {"at_ms": int(time.time() * 1000), "kind": kind, "target": str(target),
           "by": by, "reason": reason, "ok": bool(ok), "detail": detail,
           "rule": "human-0818-appointment-must-be-machine-recorded"}
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_captains_ctl(flag, wid, *extra):
    script = HERE / "nav" / "captains-ctl.sh"
    p = subprocess.run(["bash", str(script), flag, str(wid), *extra],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, (p.stdout + p.stderr)[:400]


def appoint_flag(kind, wid, by, reason):
    """captain/advisor:委派 captains-ctl 寫席位檔,寫完**回讀驗證**。"""
    rec = read_seat(wid)
    if rec is None:
        print("appoint: " + _ui("aib.appoint.no_seat_file", path=seat_file(wid)),
              file=sys.stderr)
        log_appointment(kind, wid, by, reason, False, "seat_not_found")
        return 2
    field = "captain_assigned" if kind == "captain" else "advisor"
    if kind == "captain":
        print(_ui("aib.appoint.captain_warning"), file=sys.stderr)
    flag = "--assign" if kind == "captain" else "--advisor"
    extra = () if kind == "captain" else ("on",)
    rc, out = run_captains_ctl(flag, wid, *extra)
    after = read_seat(wid) or {}
    ok = after.get(field) is True
    # ★ 寫完必回讀:工具 rc=0 不等於欄位真的落檔(本 repo 記過的「訊息自證清白」)
    log_appointment(kind, wid, by, reason, ok, "ctl_rc=%d" % rc)
    if not ok:
        print("appoint: " + _ui("aib.appoint.not_saved", name=field, rc=rc, detail=out),
              file=sys.stderr)
        return 3
    print(_ui("aib.appoint.appointed", kind=kind, window=wid, name=field, log=LOG))
    return 0


# F1(the maintainer 08-18 方案 B):fleet_id 形狀=與 aib role 同族的小寫短 token。
# 顯示端(pane-right-ai clean_token)是超集,這裡故意收得更窄:寫入口寬、
# 讀出口窄=寫得進顯示不出來,兩端都窄才不會出這種暗溝。
FLEET_RE = re.compile(r"[0-9a-z][0-9a-z_-]{0,15}$")


def appoint_fleet(wid, to, by, reason):
    """艦隊編組:寫席位檔 optional 欄 fleet_id(缺欄=預設第一艦隊,顯示端
    退回 provider——所以「取消編組」=把值設回預設,不是刪欄,見下)。
    編組也是一種指派(任命唯一入口鐵則),不得手改席位檔。"""
    if not isinstance(to, str) or not FLEET_RE.fullmatch(to)             or "verified" in to.lower():
        print("appoint: " + _ui("aib.appoint.bad_fleet_id", text="[0-9a-z][0-9a-z_-]{0,15}", value=repr(to)), file=sys.stderr)
        log_appointment("fleet", wid, by, reason, False, "bad_fleet_id:%r" % (to,))
        return 2
    rec = read_seat(wid)
    if rec is None:
        print("appoint: " + _ui("aib.appoint.no_seat_file_fleet", path=seat_file(wid)), file=sys.stderr)
        log_appointment("fleet", wid, by, reason, False, "seat_not_found")
        return 2
    # ★席位檔是多寫者(開席寫+captains-ctl 5 秒心跳)。裸 read-modify-write
    #   的競態窗會讓後寫安靜地贏(atomic_json.py 檔頭記的正是這型事故),
    #   一律走 update_json(目錄鎖+O_EXCL+fsync+寫後驗證),不自己 rename。
    sys.path.insert(0, str(HERE))
    from atomic_json import update_json, AtomicWriteError

    def _mutate(cur):
        # update_json 讀不到檔會給 {} ——那是「席位檔在檢查與上鎖之間消失」
        # 的競態,寫下去會生出一個只有 fleet_id 的殘檔冒充席位。驗 schema,
        # 不是完整席位就整筆拒。
        if not isinstance(cur, dict) or cur.get("schema") != "aib-seat/v1":
            raise AtomicWriteError("seat file vanished or not aib-seat/v1")
        cur["fleet_id"] = to
        return cur

    try:
        update_json(str(seat_file(wid)), _mutate)
    except (AtomicWriteError, OSError) as e:
        print("appoint: " + _ui("aib.appoint.write_fail", err=e), file=sys.stderr)
        log_appointment("fleet", wid, by, reason, False, "write_failed:%s" % e)
        return 3
    # ★ 寫完必回讀(與 captain/advisor 同紀律):工具沒炸不等於欄位落檔
    after = read_seat(wid) or {}
    ok = after.get("fleet_id") == to
    log_appointment("fleet", wid, by, reason, ok, "to=%s" % to)
    if not ok:
        print("appoint: " + _ui("aib.appoint.fleet_not_saved", got=repr(after.get("fleet_id")), value=repr(to)), file=sys.stderr)
        return 3
    print(_ui("aib.appoint.fleeted", window=wid, value=to, log=LOG))
    return 0


def appoint_successor(seat, to_role, by, reason):
    if not is_eligible_lead(to_role)[0]:
        print("appoint: " + _ui("aib.appoint.successor_role", items="/".join(SUCCESSOR_ROLES), value=repr(to_role)), file=sys.stderr)
        log_appointment("successor", seat, by, reason, False,
                        "role_not_allowed:%s" % to_role)
        return 3
    print("appoint: " + _ui("aib.appoint.successor_self"), file=sys.stderr)
    log_appointment("successor", seat, by, reason, True, "precheck_passed")
    return 0


def verify(wid):
    """『你宣布過但做了嗎』——沒有落檔的任命=沒有任命。"""
    rec = read_seat(wid)
    if rec is None:
        print("verify: " + _ui("aib.appoint.verify_no_seat", path=seat_file(wid)), file=sys.stderr)
        return 2
    cap = rec.get("captain_assigned") is True
    adv = rec.get("advisor") is True
    print(json.dumps({"window": str(wid), "role": rec.get("role"),
                      "seat_id": rec.get("seat_id"),
                      "fleet_id": rec.get("fleet_id"),
                      "captain_assigned": cap, "advisor": adv,
                      "verdict": ("有機器可驗的任命" if (cap or adv)
                                  else "無任命紀錄——口頭/dm 宣布不算")},
                     ensure_ascii=False, indent=2))
    return 0 if (cap or adv) else 3


_UI_TR = {}


def _ui(key, **params):
    """畫面字走四語鍵(語系盤查,10-14);每次照 i18n_lib.resolve_locale() 取(長駐程式切語系也跟得上),讀不到印鍵名。"""
    try:
        _cockpit = os.path.dirname(os.path.abspath(__file__))
        if _cockpit not in sys.path:
            sys.path.insert(0, _cockpit)
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _UI_TR.get(loc)
        if tr is None:
            tr = _UI_TR[loc] = i18n_lib.Translator(loc)
        return tr.t(key, params or None)
    except Exception:  # noqa: BLE001 —— 印不出字不能讓程式停
        return key


def main(argv=None):
    def _h(key):   # argparse 說明走四語鍵 aib.appoint.help.*;% 要跳脫
        return _ui("aib.appoint.help." + key).replace("%", "%%")

    ap = argparse.ArgumentParser(prog="aib appoint", description=_h("description"))
    ap.add_argument("command",
                    choices=["captain", "advisor", "successor", "fleet",
                             "verify"], help=_h("command"))
    ap.add_argument("target", help=_h("target"))
    ap.add_argument("--to", help=_h("to"))
    ap.add_argument("--by", default="", help=_h("by"))
    ap.add_argument("--reason", default="", help=_h("reason"))
    ap.add_argument("--instance", help=_h("instance"))
    args = ap.parse_args(argv)
    if getattr(args, "instance", None):
        os.environ["EZ2_AIB_INSTANCE"] = args.instance
        os.environ.pop("EZ2_AIB_INSTANCE_HOME", None)

    if args.command == "verify":
        return verify(args.target)
    if not args.by:
        print("appoint: " + _ui("aib.appoint.need_by"),
              file=sys.stderr)
        return 2
    if args.command == "fleet":
        if not args.to:
            print("appoint: " + _ui("aib.appoint.need_fleet_to"), file=sys.stderr)
            return 2
        return appoint_fleet(args.target, args.to, args.by, args.reason)
    if args.command == "successor":
        if not args.to:
            print("appoint: " + _ui("aib.appoint.need_successor_to"), file=sys.stderr)
            return 2
        return appoint_successor(args.target, args.to, args.by, args.reason)
    return appoint_flag(args.command, args.target, args.by, args.reason)


if __name__ == "__main__":
    sys.exit(main())
