#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席退役(09-14,pm 163「席退役時佇列要跟著收」;the maintainer 19:5x 點 [X] 關席):
關掉一個席之後,把它留在磁碟上的兩樣東西**搬走、不刪**:
  1. 席位檔  instances/<艙>/seats/wN.json  → seats/retired/wN-<五碼>-<epoch>.json
  2. 佇列目錄 state/dm-queue/<五碼>/        → state/dm-queue/retired/<五碼>-<epoch>/(audit 先補一行)
  3. bot 席的 dm socket(09-25 a seat #25 / a seat 派):codexbot-bindings/<桌面對話>.dm.sock、
     grokbot-bindings/<session>.dm.sock。橋被 kill -9 或關窗收掉時 finally 沒跑,socket 檔留著,
     之後的 aib_dm MCP 看到檔在就去連、連不上。**只刪沒人在聽的**:connect 被拒才刪;連得上(同對話有新席在用)不動。
     socket 檔沒有內容,刪掉不丟東西,所以這一項是刪不是搬。
不搬的樣子(今天 d1dd @5/@6、b556 @3/@4/@6 實見):右欄不畫它了,但席位檔還在、佇列還在,
巡查一直叫「積壓」,下一席拿到同窗號時才被覆蓋。

用法:seat_retire.py --window @N [--seats DIR] [--state-root DIR] [--by WHO] [--dry-run]
rc:0 已搬(或本來就沒有);2 參數錯。只搬「窗已不在 hub」不是這裡的判準——呼叫端(關窗的人)負責。
"""
import argparse
import json
import os
import re
import socket
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_WID_RE = re.compile(r"^@\d+$")


def _seats_dir(explicit=None):
    if explicit:
        return explicit
    # 09-15(v0.62.0 證詞 live49 test-seats-dir-resolution 紅):直接走單源 seats_dir_lib(HUB 導出 >
    #   EZ2_AIB_INSTANCE_HOME > 狀態根,與 seat_onboard.seats_dir 同一順位)。原本 import 失敗時靜默退回
    #   ~/.ez2-aib/seats —— 多艙環境下拿到主艙、格式正確的錯答案;import 失敗現在直接拋出,不猜。
    import seats_dir_lib
    return seats_dir_lib.seats_dir()


def _state_root(explicit=None):
    return explicit or os.environ.get("EZ2_AIB_STATE_ROOT") or os.path.expanduser("~/.ez2-aib/state")


def _bindings_base(explicit=None):
    """bot 綁定目錄的根。★ 跟 dm-queue 的狀態根不同:沒設 env 時這裡是 ~/.ez2-aib,dm-queue 是 ~/.ez2-aib/state。
    走 grokbot_threads._state_root(codexbot_dm_socket / aib_dm_mcp 用同一順位),不自己再寫一份。"""
    if explicit:
        return explicit
    import grokbot_threads
    return str(grokbot_threads._state_root())


def _dm_socket_of(rec, bindings=None):
    """這一席的 bot dm socket 路徑;不是 bot 席、或鍵欄位缺就回 None(不是 bot 席就不碰 bindings 根)。"""
    if not isinstance(rec, dict):
        return None
    prov = rec.get("provider")
    if prov == "codexbot":
        # 10-12 第 15 件第 2 步:socket 用橋現在綁的那條命名(席上 /bind 後席位檔還是開席那條)
        import bot_binding
        base = _bindings_base(bindings)
        tid = bot_binding.bound_id("codexbot", rec.get("session_uuid"), rec, base=base)[0]
        return os.path.join(base, "codexbot-bindings", "%s.dm.sock" % tid) if tid else None
    if prov in ("grokbot", "claudebot", "musebot") and rec.get("session_uuid"):
        return os.path.join(_bindings_base(bindings), "%s-bindings" % prov, "%s.dm.sock" % rec["session_uuid"])
    return None


def _socket_state(path):
    """absent / dead(是 socket 檔且 connect 被拒)/ live(連得上)/ unknown(不是 socket、路徑太長等量不到的)。
    只有 dead 會被刪;unknown 寧可留檔。"""
    try:
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            return "unknown"
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(path)
    except ConnectionRefusedError:
        return "dead"
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"
    finally:
        s.close()
    return "live"


def retire(window, seats=None, state_root=None, by="mouse-ctl", dry_run=False, now=None, bindings=None,
           keep_queue=False):
    """回 {"seat_file": 新路徑|None, "queue_dir": 新路徑|None, "code": 五碼|None, "dm_socket": 見下}。
    dm_socket:None=不是 bot 席;{"path", "action": removed|kept_live|kept_unknown|absent}。
    keep_queue=True:佇列不搬(09-30 右欄「離艙」頁:同一段對話在新艙續開時五碼相同,
    舊艙那一席的佇列目錄其實是活席正在用的,搬走會讓活席收不到信)。"""
    now = int(now or time.time())
    out = {"seat_file": None, "queue_dir": None, "code": None, "dm_socket": None}
    rec = None
    sdir = _seats_dir(seats)
    src = os.path.join(sdir, "w%s.json" % window.lstrip("@"))
    code = None
    if os.path.isfile(src):
        try:
            with open(src, encoding="utf-8") as fh:
                rec = json.load(fh)
            try:
                import seat_address
                code = seat_address.code_of(rec)
            except Exception:
                code = None
            if not code and isinstance(rec.get("seat_id"), str):
                code = rec["seat_id"]
        except (OSError, ValueError):
            rec, code = None, None
        dest_dir = os.path.join(sdir, "retired")
        dest = os.path.join(dest_dir, "w%s-%s-%d.json" % (window.lstrip("@"), code or "nocode", now))
        if not dry_run:
            os.makedirs(dest_dir, exist_ok=True)
            os.replace(src, dest)
        out["seat_file"] = dest
    out["code"] = code
    sock = _dm_socket_of(rec, bindings)
    if sock:
        state = _socket_state(sock)
        action = {"absent": "absent", "live": "kept_live", "unknown": "kept_unknown"}.get(state, "removed")
        if state == "dead" and not dry_run:
            try:
                os.unlink(sock)
            except FileNotFoundError:
                pass
        out["dm_socket"] = {"path": sock, "action": action}
    if code and not keep_queue:
        qdir = os.path.join(_state_root(state_root), "dm-queue", code)
        if os.path.isdir(qdir):
            dest_q = os.path.join(_state_root(state_root), "dm-queue", "retired", "%s-%d" % (code, now))
            if not dry_run:
                try:
                    with open(os.path.join(qdir, "audit.jsonl"), "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"actor": by, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                             "from": "*", "to": "retired", "ok": True,
                                             "reason": "席 %s(%s)已關;佇列整目錄搬到 %s,不 rm" % (window, code, dest_q)},
                                            ensure_ascii=False) + "\n")
                except OSError:
                    pass
                os.makedirs(os.path.dirname(dest_q), exist_ok=True)
                os.replace(qdir, dest_q)
            out["queue_dir"] = dest_q
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", required=True)
    ap.add_argument("--seats")
    ap.add_argument("--state-root")
    ap.add_argument("--bindings-root", help="bot 綁定目錄的根(預設同 aib_dm MCP:EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib)")
    ap.add_argument("--by", default="mouse-ctl")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not _WID_RE.fullmatch(a.window):
        print("seat_retire: --window 形狀不合:%r" % a.window, file=sys.stderr)
        return 2
    res = retire(a.window, a.seats, a.state_root, a.by, a.dry_run, bindings=a.bindings_root)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
