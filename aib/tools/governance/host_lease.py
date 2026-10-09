#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""每機執行租約鎖(大綱第 24 項):「這台機器現在誰在跑哪一支考卷」的單一載體。

    python3 tools/governance/host_lease.py acquire --owner <誰> --what <在跑什麼> --run-id <本次 id> [--ttl 秒]
    python3 tools/governance/host_lease.py release --run-id <本次 id>
    python3 tools/governance/host_lease.py renew   --run-id <本次 id> [--ttl 秒]
    python3 tools/governance/host_lease.py status  [--json]

★ 為什麼要有它(09-11/12 兩晚的實例,不是理論):同一台機器同時跑兩份驗證,量到的紅是「機器此刻在跑什麼」,
  不是候選的性質(三次假紅同根)。今晚艦長用 dm 手動排「一次一席」;本檔把那件事機器化——
  拿不到租約就印「機器被 <誰> 佔用(<什麼>,<起>),排隊」並退具名 rc,**不靠信任**。

★ 判準(每條對應一個 rc,獨立取,絕不接管線):
  rc=0  拿到 / 同一 run_id 重入(續期)/ 接管過期租約(留痕)/ 釋放自己的 / status 機器空或租約已過期
  rc=4  被別人持有(訊息具名:owner・what・起・到期)/ release 不是自己的租約(只解自己的)/ status 機器被佔
  rc=3  租約檔**讀不到**(壞 JSON、權限):fail-closed,不當成「沒有租約」——讀不到與沒有,處置相反
  rc=2  用法錯

★ 過期:租約自帶到期時刻(acquire 時 now+ttl),讀取端不另發明上限。過期的可被接管,接管**留痕**
  (ledger 一列 takeover,含原持有者)。「一個解不開的鎖比沒有鎖貴」(release-freeze-lock.sh 同一課)。
★ 「排隊」也留痕:每一次被拒(rc=4)寫一列 denied 到 ledger,任務區第 48 項「誰在排隊」從這裡讀,
  不是從信裡讀。
★ 路徑:AIB_HOST_LEASE_FILE 環境變數可搬落點(治具用;**只搬落點,不開行為旗標**),預設
  ~/.ez2-aib/state/host-lease.json —— 刻意不跟艙狀態根(EZ2_AIB_STATE_ROOT)走:租約是**每機**一把,
  兩個艙各自一把就是兩個人同時以為自己獨佔機器。ledger 在同路徑加 .log。
★ 沒檢查什麼(寫在這裡,不寫在報告裡才會被忘掉):接管是「讀到過期 → 覆寫」兩步,兩個接管者在同一
  毫秒內會後寫者贏,ledger 兩列都在;跨機器(NFS)不保證原子。
"""

import argparse
import datetime
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")
_TR = {}


def T(key, params=None):
    tr = _TR.get(LOC)
    if tr is None:
        tr = _TR[LOC] = i18n_lib.Translator(locale=LOC)
    return tr.t(key, params)


RC_OK = 0
RC_USAGE = 2
RC_UNREADABLE = 3
RC_HELD = 4
SCHEMA = "aib-host-lease/v1"
# ★ 這只是**退路**,不是「發版凍結上限」:第 52 項起上限由實測導出、只會長大(tools/governance/freeze_cap.py),
#   呼叫端要把導出值傳進 --ttl,長跑再配 renew。寫死一個數當上限,失效方向是「持有者還在跑就過期」——危險的那一邊。
DEFAULT_TTL_S = 5400


def lease_path(env=None):
    env = os.environ if env is None else env
    p = (env.get("AIB_HOST_LEASE_FILE") or "").strip()
    if p:
        return pathlib.Path(p)
    return pathlib.Path.home() / ".ez2-aib" / "state" / "host-lease.json"


def ledger_path(env=None):
    return lease_path(env).with_name(lease_path(env).name + ".log")


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def read_lease(path):
    """(dict|None, why)。None+why=None 是「沒有租約」(檔不存在);None+why 是「讀不到」—— 兩者不是同一個值。"""
    if not path.exists():
        return None, None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, T("aib.lease.unreadable", {"path": str(path), "err": e})
    if not isinstance(d, dict) or d.get("schema") != SCHEMA or "expiry_epoch" not in d:
        return None, T("aib.lease.bad_schema", {"path": str(path)})
    return d, None


def write_lease(path, d):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".host-lease.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def log_event(path, event, now, **fields):
    """留痕:一列 JSON。寫不進去要說(stderr),但不讓主流程炸 —— 留痕壞了不該癱瘓租約。"""
    row = {"ts": iso(now), "epoch": int(now), "event": event}
    row.update(fields)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        sys.stderr.write(T("aib.lease.ledger_failed", {"path": str(path), "err": e}) + "\n")


def proc_start(pid):
    """行程起始時刻的字面(`ps -o lstart=`);量不到回 None。
    ★ 為什麼要它(a seat 09-12 洞一):pid 會繞回(本機 99999),持有者的殼死了、pid 被別的行程重用,
      只看 `kill -0` 會把一個陌生行程當成活著的持有者 ⇒ 這把鎖永遠解不開。身份 = (pid, 起始時刻) 兩件一起。"""
    try:
        # ★ LC_ALL 釘 C(a seat 09-12 第三輪):lstart 的字面跟著 locale 變(C「Sat Sep 12 …」/ zh_TW「六 9/12 …」),
        #   拿鎖的殼與檢查的殼 LC_TIME 不同就對不上 ⇒ 活著的持有者被判成 pid 重用而被接管 —— 正是這把鎖要防的事。
        #   寫與讀都經過本函式,釘這一處兩邊同時釘住。
        env = dict(os.environ); env["LC_ALL"] = "C"; env["LANG"] = "C"
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=5, stdin=subprocess.DEVNULL, env=env).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return out or None


def holder_alive(d):
    """持有者行程還在不在:True / False / None(量不到:別台機器、或沒記 pid、或沒記起始時刻)。
    ★ pid 只在**同一台機器**有意義(a seat §5):host 不同就回 None,只看時間 —— 否則共用落點時會把別台的 pid 當自己的。
    ★ True 要兩件同時成立:pid 在 **且** 起始時刻與拿鎖時記的一樣(a seat 洞一:pid 重用)。
      對不上 ⇒ False(那不是持有者,是借用它號碼的別人);沒記起始時刻 ⇒ None(退回只看時間,由 TTL 兜底)。"""
    pid = d.get("pid")
    if not pid or d.get("host") != socket.gethostname():
        return None
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        pass
    except (ProcessLookupError, ValueError, OSError):
        return False
    started = d.get("pid_start")
    if not started:
        return None
    now_started = proc_start(pid)
    if now_started is None:
        return None
    return now_started == started


# ── 推包登記(10-09 a seat D11,a seat 提案 B/C)────────────────────────────────────────────
#   每棵樹(clone / worktree)一個登記資料夾 <git dir>/aib-ship-running/,檔名 = ship.py 的 pid。
#   同 tools/verify/test_entry 的批次登記:只認「pid 活著且起始時刻對得上」的,當掉留下的不算。
#   用途:B) 同一棵樹已有推包在跑就不准再起;C) 租約持有者(多半是外層殼)死了、但那棵樹還有推包在跑 ⇒ 不准接管。
SHIP_REGISTRY = "aib-ship-running"


def _git_dir(repo):
    try:
        p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True,
                           timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    out = p.stdout.strip()
    return pathlib.Path(out) if p.returncode == 0 and out else None


def ship_registry(repo):
    g = _git_dir(repo) if repo else None
    return (g / SHIP_REGISTRY) if g else None


def ship_register(repo, branch, pid=None):
    """登記「這棵樹有推包在跑」;回登記檔路徑(寫不出 ⇒ None,不擋推包)。"""
    d = ship_registry(repo)
    if d is None:
        return None
    pid = int(pid or os.getpid())
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / str(pid)
        f.write_text(json.dumps({"pid": pid, "branch": branch, "pid_start": proc_start(pid), "host": socket.gethostname(),
                                 "at": iso(time.time())}, ensure_ascii=False) + "\n", encoding="utf-8")
        return f
    except OSError:
        return None


def ship_unregister(repo, pid=None):
    d = ship_registry(repo)
    if d is None:
        return
    try:
        (d / str(int(pid or os.getpid()))).unlink()
    except OSError:
        pass


def live_ships(repo, exclude_pid=None):
    """這棵樹上還活著的推包(pid 在且起始時刻對得上、同一台機器);死掉留下的登記順手清掉。"""
    d = ship_registry(repo)
    out = []
    if d is None:
        return out
    try:
        files = sorted(d.iterdir())
    except OSError:
        return out
    for f in files:
        if not f.name.isdigit() or (exclude_pid and int(f.name) == int(exclude_pid)):
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {"pid": int(f.name)}
        alive = holder_alive(dict(rec, pid=int(f.name), host=rec.get("host") or socket.gethostname()))
        if alive is False:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        out.append(dict(rec, pid=int(f.name)))
    return out


def shipping_under(d):
    """租約記的那棵樹(cwd)上還活著的推包;沒記 cwd 或別台機器 ⇒ []。"""
    if not d or not d.get("cwd") or d.get("host") != socket.gethostname():
        return []
    return live_ships(d.get("cwd"))


def is_expired(d, now):
    """可不可以接管。★ 兩個方向都比只看時間安全(a seat §5):
    持有者行程**已死**(同機、pid 不在)⇒ 立刻可接管,不必等 TTL;
    持有者**還活著**(同機、pid 在)⇒ 絕不可接管,即使時間過了 —— 那正是這把鎖要防的事;
    量不到(別台機器 / 沒記 pid)⇒ 只看時間。"""
    alive = holder_alive(d)
    if alive is True:
        return False
    if alive is False:
        # 10-09 D11(a seat 提案 C):持有者多半是外層殼;殼死了(08:28 兩個對話的背景殼同時被外因收掉)而 ship 還在跑
        #   ⇒ 照「持有者死了」接管就會撞上還在跑的閘。那棵樹上還有活的推包 ⇒ 不可接管。
        return not shipping_under(d)
    try:
        return float(now) >= float(d.get("expiry_epoch"))
    except (TypeError, ValueError):
        return True     # 到期時刻壞掉 = 當過期(可接管,留痕);一個解不開的鎖比沒有鎖貴


def takeover_why(cur, now):
    """接管理由三選一:holder_dead(pid 不在)/ pid_reused(pid 在但起始時刻對不上)/ expired(只看時間)。留痕要分得開。"""
    alive = holder_alive(cur)
    if alive is False:
        try:
            os.kill(int(cur.get("pid")), 0)
            return "pid_reused"
        except PermissionError:          # ★ 要排在 OSError 前面:它是 OSError 的子類,排後面就是死分支(a seat 抓到)
            return "pid_reused"          # 別的使用者的行程佔著這個號碼、起始時刻對不上 ⇒ 仍是重用
        except (ProcessLookupError, ValueError, OSError, TypeError):
            return "holder_dead"
    return "expired"


def _say_dead_but_shipping(cur):
    if cur and holder_alive(cur) is False:
        live = shipping_under(cur)
        if live:
            print(T("aib.lease.holder_dead_but_shipping", {"path": cur.get("cwd", "?"),
                                                            "pid": ", ".join(str(r.get("pid")) for r in live)}))


def describe(d):
    return T("aib.lease.holder", {"name": d.get("owner", "?"), "what": d.get("what", "?"),
                                  "since": d.get("acquired_at", "?"), "at": d.get("expiry_at", "?"),
                                  "id": d.get("run_id", "?")})


def cmd_acquire(a, now, path, ledger):
    cur, why = read_lease(path)
    if why:
        print(why); return RC_UNREADABLE
    new = {"schema": SCHEMA, "owner": a.owner, "what": a.what, "run_id": a.run_id,
           # ★ --pid 是**呼叫端**(長跑的那個殼)的 pid;CLI 自己一回來就死了,記它等於每一把鎖都立刻可接管。沒給就 None(只看時間)。
           "pid": a.pid, "host": socket.gethostname(),
           "cwd": getattr(a, "cwd", None) or os.getcwd(),      # D11:持有者死後,看這棵樹上還有沒有推包在跑
           "pid_start": (proc_start(a.pid) if a.pid else None),   # 身份第二件:起始時刻(pid 重用時對不上)
           "acquired_epoch": int(now), "acquired_at": iso(now),
           "expiry_epoch": int(now + a.ttl), "expiry_at": iso(now + a.ttl)}
    if cur is None:
        # 空機:O_EXCL 原子建檔,兩個同時來的只有一個會成功
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            cur, why = read_lease(path)
            if why:
                print(why); return RC_UNREADABLE
            if cur is None:
                print(T("aib.lease.race", {"path": str(path)})); return RC_HELD
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
            log_event(ledger, "acquire", now, owner=a.owner, what=a.what, run_id=a.run_id, expiry=new["expiry_at"])
            print(T("aib.lease.acquired", {"what": a.what, "at": new["expiry_at"]})); return RC_OK
    if cur.get("run_id") == a.run_id:
        new["acquired_epoch"], new["acquired_at"] = cur.get("acquired_epoch"), cur.get("acquired_at")
        write_lease(path, new)
        log_event(ledger, "reenter", now, owner=a.owner, what=a.what, run_id=a.run_id, expiry=new["expiry_at"])
        print(T("aib.lease.reentered", {"id": a.run_id, "at": new["expiry_at"]})); return RC_OK
    if is_expired(cur, now):
        # ★ 24b-8(艦長 09-12 14:01,live31 量到):接管訊息印**真實理由**,與留痕的 why 同源(holder_dead / pid_reused / expired 三選一);
        #   「已到期」只在 expired 那種才印,其他兩種印「原到期 …(未到)」——原本一律印「已於 14:22 到期」而當時 14:00,讀的人會以為時鐘壞了。
        why = takeover_why(cur, now)
        write_lease(path, new)
        log_event(ledger, "takeover", now, owner=a.owner, what=a.what, run_id=a.run_id,
                  prev_owner=cur.get("owner"), prev_what=cur.get("what"), prev_run_id=cur.get("run_id"),
                  prev_expiry=cur.get("expiry_at"), prev_holder_alive=holder_alive(cur), why=why)
        key = "aib.lease.took_over" if why == "expired" else "aib.lease.took_over_live"
        print(T(key, {"why": why, "name": cur.get("owner", "?"), "what": cur.get("what", "?"),
                      "at": cur.get("expiry_at", "?")})); return RC_OK
    _say_dead_but_shipping(cur)
    log_event(ledger, "denied", now, owner=a.owner, what=a.what, run_id=a.run_id,
              holder=cur.get("owner"), holder_what=cur.get("what"), holder_since=cur.get("acquired_at"))
    print(T("aib.lease.busy", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"),
                               "since": cur.get("acquired_at", "?"), "at": cur.get("expiry_at", "?")}))
    return RC_HELD


def cmd_release(a, now, path, ledger):
    cur, why = read_lease(path)
    if why:
        print(why); return RC_UNREADABLE
    if cur is None:
        print(T("aib.lease.nothing_to_release")); return RC_OK
    if cur.get("run_id") != a.run_id:
        print(T("aib.lease.not_yours", {"id": a.run_id, "name": cur.get("owner", "?"), "what": cur.get("what", "?")}))
        return RC_HELD
    try:
        path.unlink()
    except OSError as e:
        print(T("aib.lease.unreadable", {"path": str(path), "err": e})); return RC_UNREADABLE
    log_event(ledger, "release", now, owner=cur.get("owner"), what=cur.get("what"), run_id=a.run_id)
    print(T("aib.lease.released", {"what": cur.get("what", "?")})); return RC_OK


def cmd_renew(a, now, path, ledger):
    cur, why = read_lease(path)
    if why:
        print(why); return RC_UNREADABLE
    if cur is None or cur.get("run_id") != a.run_id:
        print(T("aib.lease.not_yours", {"id": a.run_id, "name": (cur or {}).get("owner", "-"), "what": (cur or {}).get("what", "-")}))
        return RC_HELD
    cur["expiry_epoch"], cur["expiry_at"] = int(now + a.ttl), iso(now + a.ttl)
    write_lease(path, cur)
    # ★ 24b-3(a seat 09-12 14:19 實測):renew 不寫留痕。證詞逐支消費者續期一次,一輪 89–169 列、一天 300–600 KB,
    #   而讀留痕的只有 recent_denied(排隊只看 denied),renew 列對它零貢獻;續期狀態在租約檔的 expiry_at 本身就讀得到。
    print(T("aib.lease.renewed", {"at": cur["expiry_at"]})); return RC_OK


def status(path, now):
    """給 CLI 與任務區(第 48 項)共用:(state, dict|None, why)。state ∈ free / held / expired / unreadable。"""
    cur, why = read_lease(path)
    if why:
        return "unreadable", None, why
    if cur is None:
        return "free", None, None
    if is_expired(cur, now):
        return "expired", cur, None
    return "held", cur, None


def recent_denied(ledger, now, window_s=1800, block=65536):
    """最近 window_s 秒內被拒的(=在排隊的),新到舊。讀不到回 None。
    ★ 24b-3:從檔尾往回讀(留痕只增不刪,而這裡只要最近 30 分鐘;它掛在任務區每次重算的路徑上,整份讀會跟著檔案一起變慢)。
      每次往前多讀 block 位元組,直到緩衝區**位置上最前面**那條完整列的 epoch 已早於窗口、或已讀到檔頭;跨塊的半列下一輪補齊。
    ★ 講死一個假設(a seat 14:54 問):停止判準**信任檔案是時序有序的**——留痕只 append、epoch 來自寫入當下的 time.time(),
      --now 只有治具在用;所以「位置最前面 = 最舊」。結尾的 sort 不是不信任順序,是把 old→new 的檔序翻成呼叫端要的新→舊
      (順手也吸收塊內的小亂序)。亂序跨過一整個 block(約 250 列)的檔,窗口邊界那幾列可能漏,那要嘛時鐘倒退要嘛治具手寫。"""
    if not ledger.exists():
        return []
    cutoff = float(now) - window_s
    out = []
    try:
        size = ledger.stat().st_size
        with open(ledger, "rb") as fh:
            pos = size
            buf = b""
            while True:
                step = min(block, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf          # 往前累積,**不重設**(a seat 14:52:原本每輪只留半列往前接,把已讀的列丟掉,回的是最舊那批)
                if pos == 0:
                    break
                # 緩衝區最舊的一條完整列(第一段可能是半列,跳過)早於窗口就夠了;找不到合法 epoch 就再往前讀
                oldest_epoch = None
                for raw in buf.split(b"\n")[1:]:
                    if not raw.strip():
                        continue
                    try:
                        oldest_epoch = float(json.loads(raw.decode("utf-8", errors="replace")).get("epoch") or 0)
                        break
                    except ValueError:
                        continue
                if oldest_epoch is not None and oldest_epoch < cutoff:
                    break
            rows = buf.split(b"\n") if pos == 0 else buf.split(b"\n")[1:]
            for raw in rows:
                if not raw.strip():
                    continue
                try:
                    r = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if r.get("event") == "denied" and (float(now) - float(r.get("epoch") or 0)) <= window_s:
                    out.append(r)
    except OSError:
        return None
    out.sort(key=lambda r: -float(r.get("epoch") or 0))
    return out


def cmd_status(a, now, path, ledger):
    st, cur, why = status(path, now)
    denied = recent_denied(ledger, now)
    if a.json:
        print(json.dumps({"state": st, "lease": cur, "why": why, "path": str(path),
                          "queued_recent": denied, "measured_at": iso(now)}, ensure_ascii=False, indent=2))
    elif st == "unreadable":
        print(why)
    elif st == "free":
        print(T("aib.lease.free"))
    elif st == "expired":
        print(T("aib.lease.expired", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"), "at": cur.get("expiry_at", "?")}))
    else:
        print(describe(cur))
        _say_dead_but_shipping(cur)
    if not a.json and denied:
        print(T("aib.lease.queued", {"n": len(denied), "name": denied[0].get("owner", "?"),
                                     "what": denied[0].get("what", "?"), "at": denied[0].get("ts", "?")}))
    return {"free": RC_OK, "expired": RC_OK, "held": RC_HELD, "unreadable": RC_UNREADABLE}[st]


def main(argv=None):
    global LOC
    ap = argparse.ArgumentParser(description=T("aib.lease.cli_desc"))
    ap.add_argument("--now", type=float, default=None, help=T("aib.lease.cli_now"))
    ap.add_argument("--locale", default=None)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("acquire"); p.add_argument("--owner", required=True); p.add_argument("--what", required=True)
    p.add_argument("--run-id", required=True); p.add_argument("--ttl", type=int, default=DEFAULT_TTL_S)
    p.add_argument("--pid", type=int, default=None, help=T("aib.lease.cli_pid"))
    p = sub.add_parser("release"); p.add_argument("--run-id", required=True)
    p = sub.add_parser("renew"); p.add_argument("--run-id", required=True); p.add_argument("--ttl", type=int, default=DEFAULT_TTL_S)
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.locale:
        os.environ["EZ2_AIB_LOCALE"] = a.locale
    LOC = i18n_lib.resolve_locale(a.locale)
    if not a.cmd:
        ap.print_usage(sys.stderr); return RC_USAGE
    if getattr(a, "ttl", 1) is not None and getattr(a, "ttl", 1) <= 0:
        sys.stderr.write(T("aib.lease.err_ttl") + "\n"); return RC_USAGE
    now = time.time() if a.now is None else a.now
    path, ledger = lease_path(), ledger_path()
    return {"acquire": cmd_acquire, "release": cmd_release, "renew": cmd_renew, "status": cmd_status}[a.cmd](a, now, path, ledger)


if __name__ == "__main__":
    sys.exit(main())
