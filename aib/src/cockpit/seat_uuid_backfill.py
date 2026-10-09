#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ED.13 回填端:開席漏 `session_uuid` 的**時間窗 / pending 態 / 定期回填**。

## 為什麼要有這支檔(量出來的,不是推的)

開席那一刻能拿到 uuid 的只有 claude(`--session-id` 帶進去)。codex 的
rollout jsonl、agy 的 presence lock 都在開席**之後**才生出來,而生出來要多久
**沒有上限**:

    2026-08-22 aihub-17 開五席,monitor(agy)@7:
      started_epoch 14:10:55 → 席位檔寫入 uuid 14:12:23 = 88 秒,
      `aib 5sits` 回頭看了 8 輪才等到(上限 10 輪)。

今天之前全庫只有兩個回頭看的地方,兩個都有上限、過了就沒有第二次機會:
`aib` 開席尾段 `seat-detect --retry 5 || true`(約 12 秒,失敗被吞),
`seats_batch.sh` 的 settle 迴圈(10 輪)。超過上限的席位檔**永遠**缺 uuid,
seat_status 判 NO_UUID、右欄那席戴 ❓ 壓過角色圖示——而資料其實早就在磁碟上,
只是沒人再看(實測 instance 15 的 codex 席一小時後探針一發就中)。

## 三件事,各自是什麼

1. **時間窗**(`SETTLE_WINDOW_S`,預設 600 秒,`EZ2_AIB_UUID_SETTLE_S` 可調):
   從 `started_epoch` 起算,缺 uuid 在窗內叫 **pending**(還沒到、不是壞),
   過了窗叫 **overdue**(該有而沒有,是缺陷,要吵)。
   ★ 這個狀態**不落檔**——它是時間的函數,寫進檔案那一刻就開始腐爛;
     每次讀的人用 `uuid_phase(rec, now)` 自己算。落檔的只有事實(下一條)。
2. **pending 態的事實**:seat-detect `--apply` 每次對一席量不到時,把
   `uuid_detect = {attempts, first_epoch, last_epoch, last_status, last_reason}`
   寫進席位檔;量到時寫 `session_uuid_source`。沒有這些,「被吞掉的那次
   失敗」與「從來沒人試過」在檔裡長得一模一樣。
3. **定期回填**:`aib idle watch`(lobby 常駐窗,每輪 ≥30 秒)每輪呼叫
   `backfill_round()`——先便宜地讀席位檔,有席缺 uuid 才真的跑 seat-detect
   `--all --apply`;沒有缺的那一輪零子行程。**上限從「10 輪」變成「席活著
   就一直有下一次」**,這才是根治;`seats_batch.sh` 那 10 輪仍然是批次工具
   對自己該負的責任,不被本檔取代。

## 本檔刻意不做的

- 不偵測。偵測邏輯只有一份在 `seat-detect.py`,本檔用子行程呼叫它
  (與 `seats_batch.sh` 同一條路);另寫一份探針就是第二真相源。
- 不改右欄的 ❓。pending 該不該換個顯示,是 design 席/the maintainer 的裁;本檔只把
  「pending vs overdue」算出來給 `seats_report.py` 與 watch 窗印。
- 不覆寫。既存 uuid 一律不動(seat-detect 的紅線,本檔只是呼叫端)。

CLI(人工查/手動回填):
    python3 src/cockpit/seat_uuid_backfill.py              # 只報告
    python3 src/cockpit/seat_uuid_backfill.py --apply      # 真的跑一輪回填
rc:0=沒有 overdue(全有 uuid 或都還在窗內)/ 2=環境錯 / 4=有席 overdue
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import seat_status                                # noqa: E402  uuid 形狀判準單源
import seats_dir_lib                              # noqa: E402

SEAT_DETECT = os.path.join(HERE, "seat-detect.py")
SETTLE_WINDOW_S = 600
SETTLE_ENV = "EZ2_AIB_UUID_SETTLE_S"
DISABLE_ENV = "EZ2_AIB_UUID_BACKFILL"             # "0" = watch 迴圈不回填
DETECT_TIMEOUT_S = 90                             # seat-detect 一輪的硬上限

PHASE_OK = "ok"                 # 有合法 uuid
PHASE_PENDING = "pending"       # 缺 uuid,仍在時間窗內
PHASE_OVERDUE = "overdue"       # 缺 uuid,時間窗已過
PHASE_ILLEGIBLE = "illegible"   # 席位檔認不出來(交給 seat_status 的 NO_SEAT)


def hub_from_env(environ=None):
    """本艙 socket 名(與 seats_dir_lib 同一對 env 鍵);沒有=None,**不猜**。
    收成函式的理由:呼叫端(idle_reaper)不該再抄一份 env 鍵名——全庫對
    「我在哪一艙」的答案已經有 8 份以上各寫各的,這裡不加第 9 份。"""
    environ = os.environ if environ is None else environ
    return environ.get("AIB_DM_SOCKET") or environ.get("EZ2_AIHUB_SOCKET") or None


def settle_window_s(environ=None):
    """時間窗秒數。env 壞值(非正整數)→ 用預設,**不**靜默變成 0。"""
    environ = os.environ if environ is None else environ
    raw = environ.get(SETTLE_ENV)
    if raw is None or raw == "":
        return SETTLE_WINDOW_S
    try:
        val = int(raw)
    except ValueError:
        return SETTLE_WINDOW_S
    return val if val > 0 else SETTLE_WINDOW_S


# ── 09-05:pending 席的**事件驅動**重偵測(the maintainer「新開席老是無法正確辨識」)──────
# 量出來的形狀:codex 的 rollout、agy 的 presence lock、copilot 的 usage 都在**第一次
# 互動之後**才出現;開席鏈的 5 次重試全在那之前,之後只剩 watch 每 300 s 一輪。
# 於是一席從「被派工」到「畫面上認得它」最多要等 5 分鐘,而那 5 分鐘裡它顯示 `-- 💤--`。
# 這裡讓採集端(每秒跑)對缺 uuid 的席**每 30 s 補一次偵測**——一個 lsof -p 幾十毫秒,
# 而缺 uuid 的席同時只會有一兩個。節流戳記住在座艙狀態目錄,與 codex-subs-slow 同形。
REDETECT_ENV = "EZ2_AIB_UUID_REDETECT_S"
REDETECT_MIN_S = 30
_WID_RE = re.compile(r"^@[0-9]{1,10}$")


def redetect_min_s(environ=None):
    """節流秒數。env 壞值(非正整數)→ 用預設,**不**靜默變成 0(0 = 每幀 lsof)。"""
    environ = os.environ if environ is None else environ
    raw = environ.get(REDETECT_ENV)
    if raw is None or raw == "":
        return REDETECT_MIN_S
    try:
        val = int(raw)
    except ValueError:
        return REDETECT_MIN_S
    return val if val > 0 else REDETECT_MIN_S


def redetect_stamp(state_dir, wid):
    """節流戳記路徑;沒有狀態目錄或窗號形狀不對 → None(= 不重偵測,誠實不猜)。"""
    if not state_dir or not _WID_RE.fullmatch(str(wid or "")):
        return None
    return os.path.join(state_dir, "uuid-redetect-%s.stamp" % str(wid).lstrip("@"))


def redetect_due(stamp, now, min_s):
    """戳記缺 → 到期;戳記存在且未滿 min_s → 未到期。"""
    if stamp is None:
        return False
    try:
        return (float(now) - os.stat(stamp).st_mtime) >= float(min_s)
    except OSError:
        return True


def spawn_redetect(hub, wid, py=None, popen=subprocess.Popen):
    """背景跑一次單窗偵測(--apply --retry 1),不等它。回實際的 argv(給臂對帳)。
    ★ 走 seat-detect 這**唯一**探針真相源;這裡不重做任何一條探針。"""
    argv = [py or sys.executable, SEAT_DETECT, "--window", str(wid), "--apply", "--retry", "1"]
    env = dict(os.environ)
    env["EZ2_AIHUB_SOCKET"] = hub
    popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return argv


def maybe_redetect(rec, hub, state_dir, now=None, environ=None, popen=subprocess.Popen):
    """採集端每席叫一次。回 'spawned' / 'cooling' / None(不適用)。

    只對**缺 uuid**的席動作:有合法 uuid 的席一格都不碰(零子行程)。
    """
    if not isinstance(rec, dict) or seat_status.uuid_shape_ok(rec.get("session_uuid")):
        return None
    if not hub:
        return None
    stamp = redetect_stamp(state_dir, rec.get("window_id"))
    if stamp is None:
        return None
    now = time.time() if now is None else now
    if not redetect_due(stamp, now, redetect_min_s(environ)):
        return "cooling"
    try:
        with open(stamp, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % int(now))
        os.utime(stamp, (now, now))
    except OSError:
        return None                       # 寫不了戳記就不重偵測:沒有節流的重試比不重試更糟
    spawn_redetect(hub, rec.get("window_id"), popen=popen)
    return "spawned"


def uuid_phase(rec, now, window_s=None):
    """席位紀錄 → (phase, elapsed_s|None)。純函式。

    ★ `started_epoch` 缺或壞 → 直接 overdue。理由:aib 種檔時一定寫它,
      沒有就代表檔不是 aib 種的或被改過;「不知道開了多久」往吵的方向判,
      不往安靜的方向判(一個格子的失敗要表現成紅,不能表現成綠)。
    """
    if window_s is None:
        window_s = settle_window_s()
    if not isinstance(rec, dict):
        return PHASE_ILLEGIBLE, None
    if seat_status.uuid_shape_ok(rec.get("session_uuid")):
        return PHASE_OK, None
    started = rec.get("started_epoch")
    if not isinstance(started, int) or isinstance(started, bool) or started <= 0:
        return PHASE_OVERDUE, None
    elapsed = int(now) - started
    return (PHASE_PENDING if elapsed < window_s else PHASE_OVERDUE), elapsed


def read_seats(seats_dir):
    """{basename: rec};壞檔不進表(seat-detect 自己會報 bad)。"""
    out = {}
    try:
        names = sorted(os.listdir(seats_dir))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("w") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(seats_dir, name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out[name] = rec
    return out


def missing_uuid(seats):
    """{basename: rec} 中缺合法 uuid 的那些(便宜閘:只讀檔,不碰行程)。"""
    return {n: r for n, r in seats.items()
            if not seat_status.uuid_shape_ok(r.get("session_uuid"))}


def run_detect(hub, apply=True, timeout_s=DETECT_TIMEOUT_S, py=None):
    """跑一次 seat-detect(單一探針真相源)。回 (rc, stdout, stderr)。
    rc=None 表示子行程沒跑完(逾時/起不來)——與 rc=3 分開,別把
    「探測器沒跑」報成「沒量到」。"""
    env = dict(os.environ)
    env["EZ2_AIHUB_SOCKET"] = hub
    argv = [py or sys.executable, SEAT_DETECT, "--all", "--retry", "1"]
    if apply:
        argv.append("--apply")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, "", "seat-detect 沒跑完:%s" % exc.__class__.__name__
    return proc.returncode, proc.stdout, proc.stderr


def parse_detect_rows(stdout):
    """seat-detect 的列 `w7.json none - 理由` → {basename: status}。"""
    out = {}
    for line in (stdout or "").splitlines():
        parts = line.split(" ", 3)
        if len(parts) >= 2 and parts[0].endswith(".json"):
            out[parts[0]] = parts[1]
    return out


def backfill_round(hub, seats_dir, now=None, window_s=None, apply=True,
                   runner=run_detect):
    """一輪:缺 uuid 的席才跑偵測,再依時間窗分 pending/overdue。

    回 dict(全部是當回合量到的,不是推的):
      skipped   True=沒有席缺 uuid,零子行程
      rc        seat-detect 的 rc(None=沒跑完)
      filled    這一輪從缺→有的席 [(basename, role, uuid)]
      pending   仍缺但在窗內 [(basename, role, elapsed_s)]
      overdue   仍缺且過窗   [(basename, role, elapsed_s)]
      dead      窗已不在的席位檔 [(basename, role)]——化石,候人裁,不算 overdue
      detail    seat-detect 的 stdout(人要看時才看)
    """
    now = int(time.time()) if now is None else int(now)
    window_s = settle_window_s() if window_s is None else window_s
    before = read_seats(seats_dir)
    missing = missing_uuid(before)
    result = {"skipped": False, "rc": None, "filled": [], "pending": [],
              "overdue": [], "dead": [], "detail": "", "window_s": window_s}
    if not missing:
        result["skipped"] = True
        return result
    rc, out, err = runner(hub, apply=apply)
    result["rc"] = rc
    result["detail"] = (out or "") + (("\n" + err) if err else "")
    after = read_seats(seats_dir)
    statuses = parse_detect_rows(out)
    for name in sorted(missing):
        rec = after.get(name, missing[name])
        role = rec.get("role") if isinstance(rec.get("role"), str) else "-"
        phase, elapsed = uuid_phase(rec, now, window_s)
        if phase != PHASE_OK and statuses.get(name) == "dead":
            result["dead"].append((name, role))
            continue
        if phase == PHASE_OK:
            result["filled"].append((name, role, rec.get("session_uuid")))
        elif phase == PHASE_PENDING:
            result["pending"].append((name, role, elapsed))
        else:
            result["overdue"].append((name, role, elapsed))
    return result


def summarize(result):
    """一行人話(給 watch 窗/日誌);skipped 回 None(那一輪不值得印)。"""
    if result.get("skipped"):
        return None
    parts = []
    if result["filled"]:
        parts.append("回填 %d 席:%s" % (
            len(result["filled"]),
            " ".join("%s=%s" % (r, (u or "")[:8]) for _, r, u in result["filled"])))
    if result["pending"]:
        parts.append("pending %d 席(窗 %ds 內):%s" % (
            len(result["pending"]), result["window_s"],
            " ".join("%s@%ss" % (r, e) for _, r, e in result["pending"])))
    if result["overdue"]:
        parts.append("★ overdue %d 席(過窗仍缺 uuid):%s" % (
            len(result["overdue"]),
            " ".join("%s@%ss" % (r, e) for _, r, e in result["overdue"])))
    if result["dead"]:
        parts.append("死窗席位檔 %d(候人裁):%s" % (
            len(result["dead"]), " ".join(n for n, _ in result["dead"])))
    if result["rc"] is None:
        parts.append("★ seat-detect 沒跑完")
    return "uuid 回填:" + ";".join(parts) if parts else "uuid 回填:無變化"


def main(argv=None):
    ap = argparse.ArgumentParser(description="ED.13 席位 session_uuid 回填/對帳")
    ap.add_argument("--seats-dir")
    ap.add_argument("--hub", help="艙 socket 名(預設 EZ2_AIHUB_SOCKET)")
    ap.add_argument("--window-s", type=int)
    ap.add_argument("--apply", action="store_true",
                    help="真的跑 seat-detect --apply;預設只讀席位檔報告")
    args = ap.parse_args(argv)
    hub = args.hub or hub_from_env()
    seats_dir = args.seats_dir or seats_dir_lib.seats_dir()
    if not os.path.isdir(seats_dir):
        print("seat_uuid_backfill: 席位目錄不存在:%s" % seats_dir, file=sys.stderr)
        return 2
    now = int(time.time())
    window_s = args.window_s or settle_window_s()
    if args.apply:
        if not hub:
            print("seat_uuid_backfill: --apply 需要 --hub 或 EZ2_AIHUB_SOCKET",
                  file=sys.stderr)
            return 2
        res = backfill_round(hub, seats_dir, now=now, window_s=window_s, apply=True)
        line = summarize(res)
        print(line or "uuid 回填:全席已有 uuid,本輪未跑偵測")
        if res["detail"].strip():
            print(res["detail"].rstrip())
        return 4 if res["overdue"] else 0
    seats = read_seats(seats_dir)
    overdue = 0
    print("席位目錄:%s  時間窗:%ds  now=%d" % (seats_dir, window_s, now))
    for name, rec in seats.items():
        phase, elapsed = uuid_phase(rec, now, window_s)
        facts = rec.get("uuid_detect") if isinstance(rec.get("uuid_detect"), dict) else {}
        print("%-10s %-8s %-8s %-8s %s %s" % (
            name, rec.get("role") or "-", rec.get("provider") or "-", phase,
            "-" if elapsed is None else "%ds" % elapsed,
            ("attempts=%s last=%s" % (facts.get("attempts"), facts.get("last_reason"))
             if facts else "")))
        overdue += phase == PHASE_OVERDUE
    return 4 if overdue else 0


if __name__ == "__main__":
    sys.exit(main())
