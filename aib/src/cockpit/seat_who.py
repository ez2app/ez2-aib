#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib who <五碼>`——「這個代號是誰」的單一入口(the maintainer 08-18 17:49 派工)。

## 由來

08-18 the maintainer 問「root2 是誰」,艦外跑了六個指令、讀四個檔才拼出答案;那份
對照表只存在單一 session 的腦袋裡,一 compact 就沒了。交接單裡最貴的不是
規則,是「這一串代號分別是誰」。本模組把那六步收成一步,且每一欄都標
出處——查得到就給值,查不到給 **UNRESOLVED**(不留空、不猜:「查不到」
與「不存在」處置相反,08-18 當天發作過)。

## 代號的兩種身份(都收)

- **seat_id**:席位檔 `seat_id` 欄(4-8 hex),display/bindings 都用它當鍵。
- **agent_uid**:session_uuid 末五碼(the maintainer 08-15 定),身份層 agents/ 用它。
同一席兩碼常不同(本席 seat_id=8cf60、agent_uid=44444 之類)。輸入哪種都
查:**兩趟**——第一趟全艙只比 seat_id,全空第二趟才比 uuid 末五碼
(dev1 主審退回二:單趟 in(sid,tail) 會讓先掃到的 uuid 末五搶走後面艙的
seat_id 命中);再退身份層 agents。同一趟裡命中多席=**ambiguous**,列全
部候人消歧,不靜默取一(退回一:不猜鐵律)。

## 讀哪裡(全部唯讀;路徑順位=fleet_patrol._seats_dir 同紀律)

- 席位檔:`$EZ2_AIB_HOME`(預設 ~/.ez2-aib)下 `seats/` 與
  `instances/*/seats/`——**不只認環境變數,艙號從路徑導出**(同族 bug
  08-18 一天發作六次,詳 fleet_patrol.py:109 三修註解)。
- 綁定:`<home>/identity/bindings.json` + `seat-display.json`(D lane 投影,
  鍵=seat_id);後備 `identity/agents/<agent_uid>-*.json`(persona_card/
  brain_route_key/created_wall_ms)。
- CTX:`rotation/lifecycle.read_machine_ctx`(唯一合法機器源,
  $EZ2_CTX_DIR/<uuid>.pct);拿不到或 stale → 試 tmux 畫面(source=screen,
  socket=aihub-<艙號>,best-effort 2 秒);再不行 source=none。
- 活否:tmux 該艙 list-windows 含此窗=活;tmux 不可達時退 pid 檢查;都
  拿不到=UNRESOLVED(不是 false——「查不到」≠「死了」)。死席要講出
  「這個號退休了,最後活躍是什麼時候」(last_used_epoch),不假裝還在。

## 掛進 aib(由 a seat 做,本模組不動 aib/dm-ctl):

    aib 裡加:`[ "${1:-}" = "who" ] && { shift; exec "$T_PY3" "$HERE/seat_who.py" "$@"; }`

CLI:
    python3 src/cockpit/seat_who.py <五碼> [--json]
    python3 src/cockpit/seat_who.py --stdin [--json]     # 每行一個代號
rc:0=至少一個代號有解(含退休席) / 3=全部查無 / 2=用法錯
"""
import argparse
import glob

# ★ EH.17 甲:末五碼取法收進 seat_address.uuid_tail5 一處。本檔原本自己算,
#   而三處算法**不等價**(見該函式 docstring)。同目錄兄弟模組,不動 sys.path。
import os as _os_eh17
import sys as _sys_eh17
_HERE_EH17 = _os_eh17.path.dirname(_os_eh17.path.abspath(__file__))
if _HERE_EH17 not in _sys_eh17.path:
    _sys_eh17.path.insert(0, _HERE_EH17)
from seat_address import uuid_tail5  # noqa: E402
import ctx_by_seat as _ctx_by_seat  # noqa: E402  D41:登記五碼綁 /clear 後現任 session
from seats_dir_lib import state_root as _state_root  # noqa: E402  EH.2:跨艙掃描仍共用狀態根
import json
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "rotation"))

UNRESOLVED = "UNRESOLVED"
CODE_RE = re.compile(r"[0-9a-f]{4,8}$")
_CTX_SCREEN_RE = re.compile(r"CTX[^0-9]{0,8}(\d{1,3})%")


def _home():
    return _state_root()[0]


def seats_dirs(home=None):
    """回 [(instance, seats_dir)];instance 由**路徑**導出,主艙=None。
    不只認 env(fleet_patrol 三修同紀律):who 是跨艙查詢,全部都掃。"""
    home = home or _home()
    out = [(None, os.path.join(home, "seats"))]
    for p in sorted(glob.glob(os.path.join(home, "instances", "*", "seats"))):
        inst = os.path.basename(os.path.dirname(p))
        out.append((inst, p))
    return [(i, p) for i, p in out if os.path.isdir(p)]


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _iter_seats(home=None):
    for inst, d in seats_dirs(home):
        for path in sorted(glob.glob(os.path.join(d, "w*.json"))):
            rec = _load(path)
            if isinstance(rec, dict) and rec.get("schema") == "aib-seat/v1":
                yield inst, path, rec


def _uuid_tail5(u):
    """→ seat_address.uuid_tail5(EH.17 甲:單一真相源)。名字留著給既有呼叫端。"""
    return uuid_tail5(u)


def _fmt_ts(epoch_or_ms):
    if not isinstance(epoch_or_ms, (int, float)) or epoch_or_ms <= 0:
        return UNRESOLVED
    v = float(epoch_or_ms)
    if v > 1e12:                     # wall_ms
        v /= 1000.0
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))
    except (OverflowError, OSError, ValueError):
        return UNRESOLVED


_HUB_NAME_CACHE = {}


def _hub_name(inst):
    """socket 名向刀 A 的單源解析器要(`cabin_resolve.sh --hub`),**不在 python 再寫一份規則**。

    ★ 為什麼不直接拼:拼一次就是第二個真相源,而兩個真相源各自都對、合起來才錯,
      沒有任何一邊會紅(R8 掃描守的就是這件事)。
    ★ 一艙查一次就夠,結果進快取:這支是查詢工具,逐次 fork 會被跨艙迴圈放大。
    ★ 解析器不可用時回 None 而**不是**猜一個名字——猜出來的 socket 連得上別人的 server。
    """
    key = inst or ""
    if key in _HUB_NAME_CACHE:
        return _HUB_NAME_CACHE[key]
    resolver = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cabin_resolve.sh")
    argv = ["bash", resolver, "--hub"] + ([str(inst)] if inst else [])
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        name = p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        name = None
    _HUB_NAME_CACHE[key] = name
    return name


def _tmux(inst, *args, timeout=2):
    """best-effort tmux 查詢;回 (rc, stdout) 或 (None, '')=tmux 不可達。
    socket 名由 cabin_resolve.sh 決定(刀 A 單源;與 aib-dm/dm-ctl 同一套命名)。"""
    sock = _hub_name(inst)
    if sock is None:
        return None, ""
    tmux_bin = os.environ.get("EZ2_WHO_TMUX", "tmux")   # 治具口:測試不碰真 socket
    try:
        p = subprocess.run([tmux_bin, "-L", sock] + list(args),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _window_alive(inst, wid):
    """回 True/False/None(None=tmux 不可達,查不到≠死了)。"""
    rc, out = _tmux(inst, "list-windows", "-t", "aihub", "-F", "#{window_id}")
    if rc is None or rc != 0:
        return None
    return wid in out.split()


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return None


def _pane_of(inst, wid):
    """窗的活躍 pane id(%N);tmux 不可達或形狀不合回 None。"""
    rc, out = _tmux(inst, "display-message", "-p", "-t", "aihub:%s.0" % wid,
                    "#{pane_id}")
    if rc != 0:
        return None
    pane = (out or "").strip()
    return pane if re.fullmatch(r"%[0-9]{1,9}", pane) else None


def _ctx_of(inst, wid, uuid, since_epoch=None, seat_cwd=None):
    """回 (pct, source, stale_note)。machine > screen > none;
    stale 的機器值不丟——誠實標記後仍然給人看(fleet_patrol 同哲學)。
    D41(09-06):先問登記五碼的 by-seat 檔——/clear 之後登記 uuid 的 .pct
    不再更新,合法且較新的本艙本窗記錄才改讀,note 標 rebound;沒有就原路。"""
    rebound_note = None
    if wid:
        since_ms = None
        if isinstance(since_epoch, (int, float)) and since_epoch > 0:
            since_ms = float(since_epoch) * 1000.0
        live_uuid, rebound, _n = _ctx_by_seat.resolve_live_session(
            uuid, ctx_root=os.environ.get("EZ2_CTX_DIR"),
            instance=inst or "main", window=wid, since_ms=since_ms)
        if rebound:
            uuid = live_uuid
            rebound_note = "rebound:%s" % uuid_tail5(live_uuid)
    if uuid:
        try:
            import lifecycle
            ctx = lifecycle.read_machine_ctx(uuid)
        except Exception:
            ctx = None
        if ctx is not None:
            note = ("stale:%s" % ctx.get("stale_reason")) if ctx.get("stale") else None
            if rebound_note:
                note = rebound_note if note is None else "%s %s" % (rebound_note, note)
            if not ctx.get("stale"):
                return ctx["pct"], "machine", rebound_note
            machine_stale = (ctx["pct"], note)
        else:
            machine_stale = None
    else:
        machine_stale = None
    rc, out = _tmux(inst, "capture-pane", "-p", "-t", "aihub:%s.0" % wid)
    if rc == 0:
        hits = _CTX_SCREEN_RE.findall(out or "")
        if hits:
            return int(hits[-1]), "screen", None
    if machine_stale is not None:
        return machine_stale[0], "machine", machine_stale[1]
    return UNRESOLVED, "none", None


def _projection_entry(doc_section, code, seat_id):
    """投影查表:**先新鍵(uuid 末五碼)、查不到再退回 seat_id**。

    ★★★ 08-22 修正(a seat 3b-i 複審 r2,判決書 9e61f21):本檔是投影的
      **第二個產品端讀者**,而它的 docstring 逐字寫著「鍵=seat_id」——
      3b-i 把投影換鍵之後,端到端量到新舊鍵**交集 0**(32/32 全換),
      取樣一席用 code 查得到人格、用舊 seat_id 查兩欄全 UNRESOLVED。
      而 `test-seat-who.py` 在那一臂 **rc=0**:治具與壞掉的讀法共用同一個
      錯誤前提,兩邊一起錯,綠得毫無異狀。

    ★★ 失效形狀比「查不到」更糟:查不到會**落到 agents 後備**,吐出一個
      **看起來合理的舊人格**(實測 'P10/DEV-設計' 頂替了真值)。
      一個錯的值比一個空值難抓得多。

    ★ 退回只在新鍵查不到時發生;階段 4 拿掉的就是下面那三行。
      殘留風險與 `seat_persona.display_entry` 同源,理由寫在那裡,不複述。
    """
    if not isinstance(doc_section, dict):
        return None
    for key in (code, seat_id):
        if isinstance(key, str) and key:
            hit = doc_section.get(key)
            if isinstance(hit, dict):
                return hit
    return None


def _identity(home, seat_id, agent_uid):
    """bindings/display 優先(鍵=uuid 末五碼,遷移期退回 seat_id),
    agents(鍵=agent_uid)後備。

    ★ `agent_uid` 與新鍵是**同一個值**(呼叫端傳的都是 `_uuid_tail5(uuid)`),
      但兩者語意不同:一個是投影的鍵、一個是 agents 檔名的前綴。
      這裡不合併成一個參數 —— 合併之後,任一邊改來源時另一邊會靜默跟著改。
    """
    ident = {"persona": UNRESOLVED, "brain_route_key": UNRESOLVED,
             "brain_folder": UNRESOLVED, "bound_at": UNRESOLVED}
    disp = _projection_entry(
        (_load(os.path.join(home, "identity", "seat-display.json"))
         or {}).get("display"), agent_uid, seat_id)
    if isinstance(disp, dict):
        if disp.get("display_name"):
            ident["persona"] = disp["display_name"]
        if disp.get("route_key"):
            ident["brain_route_key"] = disp["route_key"]
        if disp.get("folder"):
            ident["brain_folder"] = disp["folder"]
    binds = _projection_entry(
        (_load(os.path.join(home, "identity", "bindings.json"))
         or {}).get("bindings"), agent_uid, seat_id)
    if isinstance(binds, dict) and ident["brain_route_key"] == UNRESOLVED \
            and binds.get("route_key"):
        ident["brain_route_key"] = binds["route_key"]
    if agent_uid:
        for p in sorted(glob.glob(os.path.join(
                home, "identity", "agents", "%s-*.json" % agent_uid))):
            rec = _load(p)
            if not isinstance(rec, dict):
                continue
            if ident["persona"] == UNRESOLVED and rec.get("persona_card"):
                ident["persona"] = rec["persona_card"]
            if ident["brain_route_key"] == UNRESOLVED and rec.get("brain_route_key"):
                ident["brain_route_key"] = rec["brain_route_key"]
            if rec.get("created_wall_ms"):
                ident["bound_at"] = _fmt_ts(rec["created_wall_ms"])
    return ident


def self_pane_facts():
    """一次問到自己的窗 id 與 socket 路徑。不在 tmux 裡回 (None, None)。

    ★ **必須帶 `-t "$TMUX_PANE"`,不得裸 `display-message`**:裸的回的是
      **client 正在看的那個窗**,不是呼叫者所在的 pane(`dm-ctl.sh:135` 的疤)。
      失效形狀是「某席的紀錄掛到另一席頭上」—— 不報錯,只是掛錯名字。
    ★ 兩個值一次問:分兩次問的話,中間換窗會讓兩個值來自不同的世界。
    """
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None, None
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane,
             "#{window_id}\t#{socket_path}"],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if out.returncode != 0:
        return None, None
    parts = (out.stdout or "").strip().split("\t")
    if len(parts) != 2:
        return None, None
    wid, sock = parts[0].strip(), parts[1].strip()
    return (wid or None), (sock or None)


def instance_of_socket(socket_path):
    """`/private/tmp/tmux-501/aihub-16` → `"16"`;認不出回 None(不猜)。

    ★ `aihub-<N>` 的解析**委派給 `instance_of_hub()`**(10-25 起住 seats_dir_lib;fleet_patrol 留同名綁定),本函式只負責
      把 socket **路徑**縮成 hub **名**(`os.path.basename`)。
      理由是 `a seat` 09:43 數出來的:`aihub-<N>` 這個字面在 `src/` 底下已經有
      **九個獨立的導出點**,而 `instance_of_hub()` 是唯一寫成函式的那一份 ——
      我若自己再導一次就是第十份。
    ★ 今天那九份**都是對的**,所以沒有任何東西會叫。這裡防的不是缺陷是**代價**:
      hub 命名動一次,漏改的那幾份不會報錯,只會安靜地讀到別艙的席位檔 ——
      **那正是我兩分鐘前踩到的那個形狀本身。**
    """
    if not socket_path:
        return None
    try:
        import seats_dir_lib
    except ImportError:
        return None
    return seats_dir_lib.instance_of_hub(os.path.basename(str(socket_path)))


def self_seat_uuid(home=None):
    """我自己的 session_uuid;任何一格取不到就回 None(**不退而求其次**)。

    ★ EX.26(艦長 09:39 裁丙、09:40 補裁三坑)。三個坑各對應下面一段,
      而**第三個坑我第一版就踩了**:第一版只做 `窗id → 席位檔`,沒有指定艙,
      於是掉進 `_seats_dir` 的 env 後備、讀到**主艙**的 `w4.json`,
      回了一個很有把握的錯 uuid(主艙 @4 是 dev2/b288f,而呼叫者是 16 艙的 dev1)。
      **「取得了一個值」不等於「取得了正確的那個值」,而這種錯是靜默的。**

    三格檢查,任何一格不過就是 None:
      1. `TMUX_PANE` 在,且 `display-message` 帶 `-t` 拿到窗 id;
      2. 艙號由 **socket 路徑**導出(不是 env)—— env 在多艙下會指到別艙;
      3. 席位檔**檔名與 `window_id` 交叉驗**(`w<N>.json` ↔ `"@<N>"`),
         schema 也要對。交叉驗不過寧可回 None。
    """
    wid, sock = self_pane_facts()
    return seat_uuid_for_window(wid, sock, home=home)


def seat_uuid_for_window(wid, sock, home=None):
    """給定 (窗 id, hub socket 路徑) ⇒ 那一窗席位檔的 session_uuid;同 self_seat_uuid 的第 2、3 格檢查。
    10-10 拆出來:Codex 席的底部邊框由 tmux 帶 #{window_id}/#{socket_path} 來問(aib now --border)。"""
    rec = seat_record_for_window(wid, sock, home=home)
    uuid = rec.get("session_uuid") if rec else None
    return uuid if isinstance(uuid, str) and uuid.strip() else None


def seat_record_for_window(wid, sock, home=None):
    """同上,回整份席位檔(dict;要 provider 等欄位的呼叫端用);任何一格不過回 None。"""
    if not wid:
        return None
    inst = instance_of_socket(sock)
    if not inst:
        return None
    root = home or os.environ.get("EZ2_AIB_HOME") or os.path.expanduser("~/.ez2-aib")
    seats_dir = os.path.join(root, "instances", inst, "seats")
    if not wid.startswith("@") or not wid[1:].isdigit():
        return None
    path = os.path.join(seats_dir, "w%s.json" % wid[1:])
    rec = _load(path)
    if not isinstance(rec, dict):
        return None
    # 交叉驗:檔名推出的窗號必須與檔裡自稱的一致。
    if str(rec.get("window_id") or "") != wid:
        return None
    return rec


def resolve(code, home=None):
    """回 dossier dict。status ∈ live | retired | not_found;
    查不到的欄=UNRESOLVED(「查不到」與「不存在」處置相反,不合併)。"""
    home = home or _home()
    code = (code or "").strip().lower()
    if not CODE_RE.fullmatch(code):
        return {"code": code, "status": "not_found",
                "note": "代號形狀不合(收 4-8 hex)"}
    sid_hits, tail_hits = [], []
    for inst, path, rec in _iter_seats(home):
        if code == str(rec.get("seat_id") or "").lower():
            sid_hits.append((inst, path, rec))
        elif code == _uuid_tail5(rec.get("session_uuid")):
            tail_hits.append((inst, path, rec))
    hits = sid_hits or tail_hits          # seat_id 全空才輪到 uuid 末五
    matched_by = "seat_id" if sid_hits else "uuid_tail5"
    if len(hits) > 1:
        # 跨艙撞碼:不猜、不取排序第一個——列全部,候人用艙號消歧。
        cands = []
        for inst, path, rec in hits:
            cands.append({"matched_by": matched_by,
                          "instance": inst or "main",
                          "window": str(rec.get("window_id") or UNRESOLVED),
                          "role": rec.get("role") or UNRESOLVED,
                          "session_uuid8": (rec.get("session_uuid") or "")[:8]
                          or UNRESOLVED,
                          "last_active": _fmt_ts(rec.get("last_used_epoch")
                                                 or rec.get("started_epoch")),
                          "seat_file": path})
        return {"code": code, "status": "ambiguous", "ambiguous": True,
                "matched_by": matched_by, "matches": cands,
                "role": UNRESOLVED, "persona": UNRESOLVED,
                "brain_route_key": UNRESOLVED, "brain_folder": UNRESOLVED,
                "instance": UNRESOLVED, "window": UNRESOLVED,
                "ctx_pct": UNRESOLVED, "ctx_source": "none",
                "alive": UNRESOLVED, "bound_at": UNRESOLVED,
                "session_uuid8": UNRESOLVED, "last_active": UNRESOLVED,
                "note": "跨艙撞碼:%d 席同時對到 %s(%s)——不猜,"
                        "請看 matches 用艙號/窗號消歧"
                        % (len(hits), code,
                           "、".join("艙%s%s" % (c["instance"], c["window"])
                                     for c in cands))}
    hit = hits[0] if hits else None
    if hit is None:
        d = {"code": code, "status": "not_found",
             "matched_by": UNRESOLVED, "role": UNRESOLVED,
             "persona": UNRESOLVED, "brain_route_key": UNRESOLVED,
             "brain_folder": UNRESOLVED, "instance": UNRESOLVED,
             "window": UNRESOLVED, "ctx_pct": UNRESOLVED,
             "ctx_source": "none", "alive": UNRESOLVED,
             "bound_at": UNRESOLVED, "session_uuid8": UNRESOLVED,
             "last_active": UNRESOLVED,
             "note": "全部席位檔(主艙+各艙)無此 seat_id 亦無 uuid 末五碼;"
                     "身份層 agents 亦查無" if not glob.glob(os.path.join(
                         home, "identity", "agents", "%s-*.json" % code))
                     else "席位檔查無,但身份層 agents 有殘影(僅代表登記過)"}
        return d
    inst, path, rec = hit
    seat_id = str(rec.get("seat_id") or "").lower()
    uuid = rec.get("session_uuid") if isinstance(rec.get("session_uuid"), str) else None
    wid = str(rec.get("window_id") or "")
    win_alive = _window_alive(inst, wid) if wid else None
    pid_alive = _pid_alive(rec.get("pid"))
    if win_alive is None and pid_alive is None:
        alive = UNRESOLVED
    else:
        alive = bool(win_alive) if win_alive is not None else bool(pid_alive)
    ident = _identity(home, seat_id, _uuid_tail5(uuid))
    pct, src, stale_note = (_ctx_of(inst, wid, uuid, rec.get("started_epoch"),
                                    seat_cwd=rec.get("cwd"))
                            if alive is not False else (UNRESOLVED, "none", None))
    last = rec.get("last_used_epoch") or rec.get("started_epoch")
    d = {"code": code, "status": ("retired" if alive is False else "live"),
         "matched_by": matched_by,
         "role": rec.get("role") or UNRESOLVED,
         "persona": ident["persona"],
         "brain_route_key": ident["brain_route_key"],
         "brain_folder": ident["brain_folder"],
         "instance": inst or "main",
         "window": wid or UNRESOLVED,
         "fleet_id": rec.get("fleet_id") or UNRESOLVED,
         "ctx_pct": pct, "ctx_source": src,
         "alive": alive,
         "bound_at": ident["bound_at"],
         "session_uuid8": (uuid or "")[:8] or UNRESOLVED,
         "last_active": _fmt_ts(last),
         "seat_file": path}
    if stale_note:
        d["ctx_note"] = stale_note
    if alive is False:
        d["note"] = "這個號退休了,最後活躍 %s(窗 %s 已不在 tmux)" % (
            d["last_active"], wid or "?")
    elif alive == UNRESOLVED:
        d["note"] = "tmux 不可達且 pid 無從驗——活否查不到,不等於死了"
    return d


def _render(d):
    lines = ["%s = %s@%s(%s)" % (d["code"], d.get("role", UNRESOLVED),
                                 d["code"], d.get("status"))]
    order = (("persona", "人格"), ("brain_route_key", "大腦 route"),
             ("brain_folder", "落點"), ("instance", "艙"), ("window", "窗"),
             ("fleet_id", "艦隊"), ("ctx_pct", "CTX"), ("ctx_source", "CTX源"),
             ("alive", "活否"), ("bound_at", "綁定時間"),
             ("session_uuid8", "uuid前八"), ("last_active", "最後活躍"))
    for k, label in order:
        if k in d:
            lines.append("  %-8s %s" % (label, d[k]))
    if d.get("ctx_note"):
        lines.append("  %-8s %s" % ("CTX註", d["ctx_note"]))
    for c in d.get("matches") or []:
        lines.append("  候選     艙%-4s %-4s %-6s uuid8=%s 最後活躍 %s"
                     % (c["instance"], c["window"], c["role"],
                        c["session_uuid8"], c["last_active"]))
    if d.get("note"):
        lines.append("  ★ %s" % d["note"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib who",
                                 description="這個代號是誰(單一入口,唯讀)")
    ap.add_argument("codes", nargs="*", help="五碼(seat_id 或 uuid 末五)")
    ap.add_argument("--stdin", action="store_true", help="每行一個代號")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home", help="覆寫 EZ2_AIB_HOME(測試治具用)")
    args = ap.parse_args(argv)
    codes = list(args.codes)
    if args.stdin:
        codes += [ln.strip() for ln in sys.stdin if ln.strip()]
    if not codes:
        ap.print_usage(sys.stderr)
        return 2
    out = [resolve(c, home=args.home) for c in codes]
    if args.json:
        print(json.dumps(out if len(out) > 1 else out[0],
                         ensure_ascii=False, indent=1))
    else:
        print("\n\n".join(_render(d) for d in out))
    return 0 if any(d["status"] != "not_found" for d in out) else 3


if __name__ == "__main__":
    sys.exit(main())
