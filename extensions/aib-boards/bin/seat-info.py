#!/usr/bin/env python3
"""Read-only probe of the AIB seat this Claude session sits in. Prints one JSON line.
argv[1] (optional) = this session's uuid; else $AIB_RESUME_SESSION_UUID.
Resolution order: (1) any instances/*/seats/w*.json whose session_uuid matches (newest started_epoch wins) —
survives cabin moves without restart; (2) fallback: cabin from $TMUX socket name. Unmeasurable fields are null."""
import json, os, glob, re, subprocess, sys
out = {"cabin": None, "cabin_code": None, "role": None, "code": None, "window": None, "seat_id": None,
       "seat_file": None, "seat_file_has_uuid": None, "captain": None, "captain_addr": None,
       "roots": [], "room_installed": False, "unread": None, "unread_list": [], "handoff": None, "dispatch": None, "blocks": [], "roster": [], "fleet": None, "integ": None, "dev": None, "qa": None, "bot": None, "home": None, "mod": None, "room": None, "err": []}
# cockpit:明給的優先;否則 EZ2_AIB_ROOT;都沒有而本檔就在 repo 的 tools/claude-mods/aib-boards/bin 底下(10-14 從 aib-seat 搬來) ⇒ 用那個 repo
_here_repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
cockpit = os.environ.get("EZ2_COCKPIT_DIR") or (os.path.join(os.environ["EZ2_AIB_ROOT"], "src", "cockpit")
          if os.environ.get("EZ2_AIB_ROOT") else os.path.join(_here_repo, "src", "cockpit"))
# mod 以快照安裝時本檔不在 repo 裡、_here_repo 指錯 ⇒ 跟 register.tsx 一樣退回 ~/ez2-ai-b/src/cockpit(只在那裡真的有才換)
if not os.path.isdir(cockpit) and os.path.isdir(os.path.expanduser("~/ez2-ai-b/src/cockpit")):
    cockpit = os.path.expanduser("~/ez2-ai-b/src/cockpit")
try:
    sys.path.insert(0, cockpit)
    from seats_dir_lib import state_root  # 與 aib handoff / dm_queue 同一個狀態根,不另寫一套
    home = state_root()[0]
except Exception:
    home = os.path.expanduser("~/.ez2-aib")   # 與 seats_dir_lib.STATE_ROOT_DEFAULT 相同;舊變數已退場,不再讀
out["home"] = home
FLEET = "--fleet" in sys.argv[1:]   # 艦長視角:逐席量 CTX/未讀/交接/派工(較貴,只有要看時才帶)
INTEG = "--integrator" in sys.argv[1:]   # 整合席視角:待併的包、推包閘門、機器鎖、主線
# captain's room web page (every seat: /aib room needs it; was captain-only before 10-14)
# captain's room web page (tools/captain-room/server.py): where it is, whether it is alive, the latest thumbnail.
# It is the captain-room plugin (in development, not in the public edition): built-in captain_room.py or
# ~/.ez2-aib/plugins/captain-room (same lookup as addon_path.py). Not installed => /aib room only says "in development"
# and the room's state files are not read.
try:
    _ah = os.environ.get("EZ2_AIB_HOME") or os.path.join(os.path.expanduser("~"), ".ez2-aib")
    out["room_installed"] = os.path.isfile(os.path.join(cockpit, "captain_room.py")) or \
        os.path.isfile(os.path.join(_ah, "plugins", "captain-room", "captain_room.py"))
except Exception as e:
    out["room_installed"] = False
    out["err"].append(f"room_installed: {e}")
try:
    if not out["room_installed"]:
        raise FileNotFoundError
    import time as _t
    rdir = os.path.join(home, "state", "captain-room")
    with open(os.path.join(rdir, "server.json"), encoding="utf-8") as fh:
        room = json.load(fh)
    alive = False
    try:
        os.kill(int(room.get("pid") or 0), 0)
        alive = True
    except Exception:
        alive = False
    thumb = os.path.join(rdir, "thumb.png")
    age = int(_t.time() - os.path.getmtime(thumb)) if os.path.isfile(thumb) else None
    # summary.json: the page posts per-floor head counts / waiting-for-you / unread every 8 s (/api/summary);
    # terminals that cannot draw the thumbnail (Terminal.app) show this one line instead
    summ, sfile = None, os.path.join(rdir, "summary.json")
    if os.path.isfile(sfile):
        try:
            with open(sfile, encoding="utf-8") as fh:
                summ = json.load(fh)
            summ = {k: int(summ.get(k) or 0) for k in ("f1", "f2", "f3", "street", "decide", "unread", "online", "at", "hosts", "down")}
            summ["age_s"] = max(0, int(_t.time()) - summ["at"])
        except Exception:
            summ = None
    out["room"] = {"url": room.get("url"), "port": room.get("port"), "cabin": room.get("cabin"), "pid": room.get("pid"),
                   "alive": alive, "script": room.get("script"), "argv": room.get("argv") or [],
                   "thumb": thumb if age is not None else None, "thumb_age_s": age, "summary": summ}
except FileNotFoundError:
    pass
except Exception as e:
    out["err"].append(f"room: {e}")
DEV = "--dev" in sys.argv[1:]            # dev 視角:我的工作樹改了什麼、測試結果
QA = "--qa" in sys.argv[1:]              # QA/驗收視角:交給我驗的包、沒驗的格子、我寫過的審查
BOT = "--bot" in sys.argv[1:]            # bot 席視角:各 bot 席綁的對話、收發佇列、最後收發時間(bot 席自己載不進 mod,只能在別席看)
_args = [a for a in sys.argv[1:] if a not in ("--fleet", "--integrator", "--dev", "--qa", "--bot", "--dm-dir")]
uuid = (_args[0] if _args and _args[0] else "") or os.environ.get("AIB_RESUME_SESSION_UUID") or ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I 不會自動把本檔目錄放進 sys.path
from relay_line import relay_summary  # noqa: E402
try:
    sys.path.insert(0, cockpit)
    from seat_address import uuid_tail5, code_of  # canonical helpers, never u[-5:]
except Exception as e:
    uuid_tail5 = code_of = None; out["err"].append(f"seat_address import: {e}")
# --dm-dir:只印這一席的信件資料夾就走(新信卡片要在開席時登記監看,不能等整份探測)
if "--dm-dir" in sys.argv[1:]:
    try:
        from seat_now import self_code
        _me = self_code()
    except Exception:
        _me = None
    print(os.path.join(home, "state", "dm-queue", _me) if _me else "")
    sys.exit(0)

def load(f):
    try: return json.load(open(f))
    except Exception: return None

# (1) match by session_uuid across all cabins
best = None
if uuid:
    for f in glob.glob(os.path.join(home, "instances", "*", "seats", "w*.json")):
        d = load(f)
        if d and d.get("session_uuid") == uuid:
            k = d.get("started_epoch") or 0
            if best is None or k > best[0]: best = (k, f, d)
# (1b) after /clear the session uuid changes but the seat file keeps the old one; aib handoff logs every
# new session id of a seat in state/handoff/<code>.rounds.jsonl — use that to get back to the seat's code.
if uuid and not best and code_of:
    for rp in glob.glob(os.path.join(home, "state", "handoff", "*.rounds.jsonl")):
        try: hit = any(json.loads(ln).get("session_id") == uuid for ln in open(rp) if ln.strip())
        except Exception: hit = False
        if not hit: continue
        want = os.path.basename(rp).split(".", 1)[0]
        for f in glob.glob(os.path.join(home, "instances", "*", "seats", "w*.json")):
            d = load(f)
            if d and d.get("session_uuid") and code_of(d) == want:
                k = d.get("started_epoch") or 0
                if best is None or k > best[0]: best = (k, f, d)
tmux = os.environ.get("TMUX", "")
sock = tmux.split(",")[0].rsplit("/", 1)[-1] if tmux else ""
if best:
    _, f, d = best
    out.update({"cabin": d.get("cabin_id"), "role": d.get("role"), "window": d.get("window_id"),
                "seat_id": d.get("seat_id"), "seat_file": f, "seat_file_has_uuid": True,
                "code": code_of(d) if code_of else None})
elif sock.startswith("aihub-"):
    # (2) fallback: this cabin, seat file by window may lack session_uuid (未結案 3b)
    out["cabin"] = sock[len("aihub-"):]
    pane_win = None
    try:
        r = subprocess.run([os.environ.get("AIB_TMUX_BIN", "tmux"), "display-message", "-p", "-t", os.environ.get("TMUX_PANE", ""), "#{window_id}"],
                           capture_output=True, text=True, timeout=5)
        pane_win = r.stdout.strip() if r.returncode == 0 else None
    except Exception as e:
        out["err"].append(f"tmux window: {e}")
    for f in glob.glob(os.path.join(home, "instances", out["cabin"], "seats", "w*.json")):
        d = load(f)
        if d and pane_win and d.get("window_id") == pane_win:
            out.update({"role": d.get("role"), "window": pane_win, "seat_id": d.get("seat_id"), "seat_file": f,
                        "seat_file_has_uuid": bool(d.get("session_uuid"))})
            if d.get("session_uuid") and code_of: out["code"] = code_of(d)
            break
    if not out["code"] and uuid and uuid_tail5:
        out["code"] = uuid_tail5(uuid); out["err"].append("seat file matched by window only; session_uuid missing in seat file")
if out["cabin"]:
    out["cabin_code"] = out["cabin"].rsplit("-", 1)[-1]
    # captain = a root-family seat in the same cabin (prefer the one whose seat_id == my reports_to)
    mine = best[2] if best else {}
    cands = []
    for f in glob.glob(os.path.join(home, "instances", out["cabin"], "seats", "w*.json")):
        d = load(f)
        if d and str(d.get("role", "")).startswith("root") and d.get("session_uuid") and code_of:
            rt = mine.get("reports_to")  # 空對空不算對上(沒填 reports_to 的席不能因 root 也沒 seat_id 就配成艦長)
            cands.append((bool(rt) and d.get("seat_id") == rt, d.get("started_epoch") or 0, code_of(d)))
    out["roots"] = sorted(c[2] for c in cands)
    picked = [c for c in cands if c[0]] or (cands if len(cands) == 1 else [])
    if picked:
        out["captain"] = picked[0][2]; out["captain_addr"] = f"{out['cabin_code']}/{picked[0][2]}"
    else:
        # reports_to 空:改用右欄畫 👑 的同一份判定(cooldown.captain_seat 讀 ai-rowmap.json),不另寫一套
        crown = None
        try:
            # 艙已由上面解析出來:指向這一艙的 cockpit 狀態目錄(不是退回主艙的猜測)
            cdir = os.path.join(home, "instances", out["cabin"], "cockpit")
            if not os.environ.get("EZ2_COCKPIT_STATE_DIR") and os.path.isdir(cdir):
                os.environ["EZ2_COCKPIT_STATE_DIR"] = cdir
            from cooldown import captain_seat
            crown = captain_seat()
        except Exception as e:
            out["err"].append(f"captain_seat: {e}")
        if crown:
            out["captain"] = crown; out["captain_addr"] = f"{out['cabin_code']}/{crown}"; out["captain_src"] = "crown"  # 顯示字由 mod 端對照(探測只出機器鍵,i18n 棘輪)
        elif cands:
            out["err"].append(f"captain ambiguous: {len(cands)} root seats, reports_to empty")
# 席碼 → 引擎標誌(the maintainer 10-14 03:1x「所有的 mods 有帶到 aib 席位的,都要帶上他的 logo」;同 render_lib.ENGINE_ICONS)
out["logos"] = {}
try:
    sys.path.insert(0, os.path.join(cockpit, "render"))
    from render_lib import ENGINE_ICONS
    import glob as _glob
    from seat_address import code_of as _code_of
    for _f in _glob.glob(os.path.join(os.path.expanduser(os.environ.get("EZ2_AIB_STATE_ROOT") or "~/.ez2-aib"), "instances", out.get("cabin") or "*", "seats", "w*.json")):
        try:
            _r = json.load(open(_f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        _c, _i = _code_of(_r), ENGINE_ICONS.get(str(_r.get("provider") or "").lower())
        if _c and _i:
            out["logos"][_c] = _i
except Exception as e:
    out["err"].append(f"logos: {e}")
# 標誌原廠色(the maintainer 10-14):同 render_lib.ENGINE_LETTER_COLORS;tmux 裡 Claude Code 自己降 256 色會降歪 ⇒ 給色表指定的 256 號
out["logo_colors"] = {}
try:
    from render_lib import ENGINE_LETTER_COLORS
    _clamp = bool(os.environ.get("TMUX")) and not os.environ.get("CLAUDE_CODE_TMUX_TRUECOLOR")
    for _f in _glob.glob(os.path.join(os.path.expanduser(os.environ.get("EZ2_AIB_STATE_ROOT") or "~/.ez2-aib"), "instances", out.get("cabin") or "*", "seats", "w*.json")):
        try:
            _r = json.load(open(_f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        _cols = ENGINE_LETTER_COLORS.get(str(_r.get("provider") or "").lower()) or ()
        _c = _code_of(_r)
        if _c and _cols:
            out["logo_colors"][_c] = ("ansi256(%d)" % _cols[0][1]) if _clamp else ("#%02x%02x%02x" % tuple(_cols[0][0]))
except Exception as e:
    out["err"].append(f"logo_colors: {e}")
# 席碼 → 人格圖示 / 人格名(新信卡片的大頭像,the maintainer 10-14「彈窗要放大皇冠、⚓、👔、🧞 這種角色 icon 當成圖像」)
#   seat_id → 延續檔 persona_uid(bind_one.read_continuity)→ identity/cards.json 的 emoji / display_name;讀不到的席就不放
out["personas"] = {}
try:
    sys.path.insert(0, os.path.join(cockpit, "..", "identity"))
    import bind_one as _bind_one
    _doc = json.load(open(os.path.join(home, "identity", "cards.json"), encoding="utf-8"))
    _cards = _doc.get("cards") or {}
    for _f in _glob.glob(os.path.join(home, "instances", out.get("cabin") or "*", "seats", "w*.json")):
        try:
            _r = json.load(open(_f, encoding="utf-8"))
            _c = _code_of(_r)
            _uid = ((_bind_one.read_continuity(home, _r.get("seat_id")) or {}).get("persona_uid")) if _r.get("seat_id") else None
        except Exception:
            continue
        _card = (_cards.get(_uid) if _uid else None) or {}
        if _c:
            out["personas"][_c] = {"name": _card.get("display_name") or _r.get("role") or None, "role": _r.get("role") or None}
except Exception as e:
    out["err"].append(f"personas: {e}")
# 席碼 → 右欄 AI 清單那一格的段位圖示(👑 / ⚓ / 🤖 / 👔 / 🧞 …,the maintainer 10-14「右邊的 ai 欄早就訂好所有 icon」):
#   不自己算,直接讀右欄每次畫完發布的 <艙>/cockpit/ai-rowmap.json(detail.label = @席碼、detail.mark = 圖示)
out["marks"] = {}
try:
    if out.get("cabin"):
        for _row in json.load(open(os.path.join(home, "instances", out["cabin"], "cockpit", "ai-rowmap.json"), encoding="utf-8")) or []:
            _d = (_row or {}).get("detail") or {}
            _m = re.fullmatch(r"@([0-9a-f]{5})", str(_d.get("label") or ""))
            if _m and _d.get("mark") and _d.get("kind") == "main":
                out["marks"].setdefault(_m.group(1), _d["mark"])
except Exception as e:
    out["err"].append(f"marks: {e}")
if out["code"]:
    try:
        r = subprocess.run([sys.executable, os.path.join(cockpit, "dm_queue.py"), "list", "--seat", out["code"], "--tab", "waiting"],
                           capture_output=True, text=True, timeout=20)
        rows = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
        unread = [m for m in rows if not m.get("read_at")]
        out["unread"] = len(unread)
        out["unread_list"] = [{"id": m.get("id"), "rev": m.get("rev"), "sender": m.get("sender"), "when": m.get("when"),
                               "sender_seat": m.get("sender_seat"), "ts": m.get("sent_at") or m.get("ts"),
                               "msg": (m.get("msg") or "")[:90]} for m in unread[:5]]
    except Exception as e:
        out["err"].append(f"dm_queue: {e}")
    # 手上派工 = 最新一封「別席寄來、沒結案、沒封存」的 dm(讀過也算:讀了不等於做完)
    try:
        rows = []
        for tab in ("waiting", "read"):  # 沒給 tab 的舊入口只回未讀;已讀未結案在 read 頁
            r = subprocess.run([sys.executable, os.path.join(cockpit, "dm_queue.py"), "list", "--seat", out["code"], "--tab", tab],
                               capture_output=True, text=True, timeout=20)
            rows += json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
        cand = [m for m in rows if m.get("sender_kind") == "seat" and m.get("sender_seat") != out["code"]
                and not m.get("done_at") and not m.get("archived_at") and not m.get("superseded_by")]
        if cand:
            m = max(cand, key=lambda m: m.get("sent_at") or m.get("ts") or 0)
            m = dict(m, msg=relay_summary(m.get("msg") or "", home))
            out["dispatch"] = {"id": m.get("id"), "sender": m.get("sender"), "when": m.get("when"),
                               "age_min": m.get("age_min"), "status": m.get("status"), "msg": (m.get("msg") or "")[:120]}
    except Exception as e:
        out["err"].append(f"dispatch: {e}")
if out["code"]:
    # handoff receipt for THIS round (same rule aib handoff status uses: receipt_between round start and now)
    try:
        import time
        from aib_handoff import status as handoff_status
        st = handoff_status(out["code"])
        r = st.get("this_round_receipt") or {}
        out["handoff"] = {"done": bool(r), "at": time.strftime("%H:%M", time.localtime(r["ts"])) if r else None,
                          "file": r.get("file") if r else None, "steps": st.get("steps") or []}
    except Exception as e:
        out["err"].append(f"handoff: {e}")
# 艙內名冊(雷達掃描圖的光點):每席的席碼、角色、引擎、窗號,以及 tmux 視窗最後活動距今幾秒
if out["cabin"] and code_of:
    try:
        # 艙的 hub socket 名只從環境或 $TMUX 現量(EZ2_AIHUB_SOCKET 是席位殼帶的四元組之一),不自己拼(R8:不得第二處重造艙名)
        hub_sock = os.environ.get("EZ2_AIHUB_SOCKET") or sock
        act = {}
        if hub_sock:
            try:  # tmux 量不到不影響名冊:席還是列出來,只是 idle_s 為 null
                r = subprocess.run([os.environ.get("AIB_TMUX_BIN", "tmux"), "-L", hub_sock, "list-windows", "-a", "-F", "#{window_id} #{window_activity}"],
                                   capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                act = dict(ln.split(" ", 1) for ln in r.stdout.splitlines() if " " in ln) if r.returncode == 0 else {}
            except Exception as e:
                out["err"].append(f"roster tmux: {e}")
        else:
            out["err"].append("roster tmux: no hub socket in env/TMUX (idle_s unmeasured)")
        import time as _t
        now = _t.time()
        for f in sorted(glob.glob(os.path.join(home, "instances", out["cabin"], "seats", "w*.json"))):
            d = load(f)
            if not d or not d.get("session_uuid"):
                continue
            a = act.get(d.get("window_id") or "")
            out["roster"].append({"code": code_of(d), "role": d.get("role"), "provider": d.get("provider"),
                                  "window": d.get("window_id"), "idle_s": round(now - int(a)) if a and a.isdigit() else None})
    except Exception as e:
        out["err"].append(f"roster: {e}")
# 艦長視角:名冊每一席再量 CTX(跟著 /clear 走)、未讀、交接收據、手上派工。全部用 aib 既有的讀法。
if FLEET and out["roster"]:
    fleet = []
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(cockpit), "rotation"))
        sys.path.insert(0, os.path.dirname(cockpit))
        import dm_queue
        from aib_handoff import status as _hs
        # 右欄同一把:ctx_by_seat(依 艙 + 窗號 找 /clear 後的新 session);ctx_by_pane 已停用(會跨艙撞號)
        from ctx_by_seat import resolve_live_session
        try:
            from rotation.lifecycle import read_machine_ctx
        except Exception:
            from lifecycle import read_machine_ctx
        recs = {}
        for f in glob.glob(os.path.join(home, "instances", out["cabin"], "seats", "w*.json")):
            d = load(f)
            if d and d.get("session_uuid"):
                recs[code_of(d)] = d
        for x in out["roster"]:
            d = recs.get(x["code"]) or {}
            row = dict(x, ctx=None, ctx_stale=None, unread=None, handoff=None, dispatch=None)
            try:
                live, _rebound, _n = resolve_live_session(d.get("session_uuid"), ctx_root=os.environ.get("EZ2_CTX_DIR"),
                                                          instance=out["cabin"], window=x["window"],
                                                          since_ms=(d.get("started_epoch") or 0) * 1000 or None)
                c = read_machine_ctx(live)
                if c:
                    row["ctx"], row["ctx_stale"] = c.get("pct"), bool(c.get("stale"))
            except Exception as e:
                out["err"].append(f"fleet ctx {x['code']}: {e}")
            try:
                tabs = dm_queue.tabs_payload(x["code"])["tabs"]
                rows = list((tabs.get("waiting") or {}).get("rows") or []) + list((tabs.get("read") or {}).get("rows") or [])
                row["unread"] = sum(1 for m in rows if not m.get("read_at"))
                cand = [m for m in rows if m.get("sender_kind") == "seat" and m.get("sender_seat") != x["code"]
                        and not m.get("done_at") and not m.get("archived_at") and not m.get("superseded_by")]
                if cand:
                    m = max(cand, key=lambda m: m.get("sent_at") or m.get("ts") or 0)
                    row["dispatch"] = {"sender": m.get("sender"), "age_min": m.get("age_min"),
                                       "msg": relay_summary(m.get("msg") or "", home)[:80]}
            except Exception as e:
                out["err"].append(f"fleet dm {x['code']}: {e}")
            try:
                st = _hs(x["code"])
                row["handoff"] = {"done": bool(st.get("this_round_receipt")), "steps": st.get("steps") or []}
            except Exception as e:
                out["err"].append(f"fleet handoff {x['code']}: {e}")
            fleet.append(row)
        out["fleet"] = fleet
    except Exception as e:
        out["err"].append(f"fleet: {e}")
# 整合席視角:全部用 git / ship 帳本 / 租約檔現量,不猜
if INTEG:
    integ = {"repo": None, "main_remote": None, "main": None, "packages": [], "gate": [], "lease": None, "ship_running": [], "err": []}
    repo = os.path.abspath(os.path.join(cockpit, "..", ".."))
    integ["repo"] = repo
    def _git(cwd, *args, timeout=8):
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout.strip() if r.returncode == 0 else None
    try:
        # 主線在哪個 remote:URL 含 github.com 的那個(~/ez2-ai-b 的 origin 是另一台 Mac,不是主線)
        rem = None
        for ln in (_git(repo, "remote", "-v") or "").splitlines():
            name, _, rest = ln.partition("\t")
            if "github.com" in rest and "(fetch)" in rest:
                rem = name; break
        rem = rem or "origin"
        integ["main_remote"] = rem
        head = _git(repo, "log", "-1", "--format=%h %ct %s", f"{rem}/main")
        if head:
            h, ct, subj = head.split(" ", 2)
            integ["main"] = {"sha": h, "age_s": int(__import__("time").time()) - int(ct), "subject": subj[:80]}
        wts = _git(repo, "worktree", "list", "--porcelain") or ""
        for blk in wts.split("\n\n"):
            path = next((l[9:] for l in blk.splitlines() if l.startswith("worktree ")), None)
            if not path or not os.path.isdir(path):
                continue
            br = _git(path, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
            ahead = _git(path, "rev-list", "--count", f"{rem}/main..HEAD")
            behind = _git(path, "rev-list", "--count", f"HEAD..{rem}/main")
            dirty = len((_git(path, "status", "--short") or "").splitlines())
            last = _git(path, "log", "-1", "--format=%ct %s")
            ahead_n = int(ahead) if ahead and ahead.isdigit() else None
            if (ahead_n or 0) == 0 and dirty == 0:
                continue   # 跟主線一樣又乾淨:不是待併的包
            lt, _, lsub = (last or "0 ").partition(" ")
            integ["packages"].append({"path": path, "branch": br, "ahead": ahead_n,
                                      "behind": int(behind) if behind and behind.isdigit() else None, "dirty": dirty,
                                      "last_age_s": int(__import__("time").time()) - int(lt) if lt.isdigit() else None,
                                      "subject": lsub[:70]})
        integ["packages"].sort(key=lambda p: (-(p["ahead"] or 0), p["path"]))
    except Exception as e:
        integ["err"].append(f"git: {e}")
    try:
        sys.path.insert(0, os.path.join(repo, "tools", "verify"))
        import pushgate
        # 帳本以「倉身份」分列;出貨用的 clone(origin=GitHub)與 ~/ez2-ai-b 是不同身份 ⇒ 取同名專案的全部列
        name = os.path.basename(repo.rstrip("/"))
        rows = [r for r in pushgate.read_stats(None) if r.get("name") == name][-12:]
        for r in rows:
            integ["gate"].append({"time": str(r.get("time", ""))[11:16], "day": str(r.get("time", ""))[5:10], "rc": int(r.get("rc") or 0),
                                  "ran": int(r.get("files_ran") or 0), "total": int(r.get("files_total") or 0),
                                  "wall_s": float(r.get("wall_s") or 0), "early_stop": int(r.get("rc") or 0) == 5})
    except Exception as e:
        integ["err"].append(f"gate stats: {e}")
    try:
        r = subprocess.run([sys.executable, os.path.join(repo, "tools", "governance", "host_lease.py"), "status", "--json"],
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        d = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}   # 被佔用時 rc=4 但 JSON 照印
        lease = d.get("lease") or {}
        integ["lease"] = {"state": d.get("state"), "owner": lease.get("owner"), "what": lease.get("what"),
                          "acquired_at": lease.get("acquired_at"), "pid": lease.get("pid"),
                          "queued": list(dict.fromkeys(q.get("owner") for q in (d.get("queued_recent") or []) if isinstance(q, dict) and q.get("owner")))[:5]}
    except Exception as e:
        integ["err"].append(f"lease: {e}")
    try:
        # macOS 的 ps 沒有 etimes,只有 etime([[dd-]hh:]mm:ss);只算真的 python ship.py 行程,排隊的殼不算
        r = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        def _etime(t):
            d, _, hms = t.rpartition("-")
            parts = [int(x) for x in hms.split(":")]
            sec = parts[-1] + (parts[-2] if len(parts) > 1 else 0) * 60 + (parts[-3] if len(parts) > 2 else 0) * 3600
            return sec + (int(d) * 86400 if d else 0)
        for ln in r.stdout.splitlines():
            parts = ln.split(None, 2)
            if len(parts) != 3 or "ship.py package" not in parts[2] or "/bin/zsh" in parts[2] or "/bin/bash" in parts[2]:
                continue
            cmd = parts[2]
            br = cmd.split("--branch", 1)[1].split()[0] if "--branch" in cmd else None
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            # 測試治具在暫存目錄起的 ship 不算:看行程的 cwd
            cwd = ""
            try:
                lr = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
                cwd = next((l[1:] for l in lr.stdout.splitlines() if l.startswith("n")), "")
            except Exception:
                pass
            if any(cwd.startswith(t) for t in ("/private/tmp", "/private/var/folders", "/var/folders", "/tmp")):
                continue
            integ["ship_running"].append({"pid": pid, "elapsed_s": _etime(parts[1]), "branch": br, "cwd": cwd})
    except Exception as e:
        integ["err"].append(f"ps: {e}")
    out["integ"] = integ
    out["err"] += integ["err"]
# dev 視角:席碼出現在分支名或路徑裡的工作樹 = 我的樹(開席慣例 <類型>/<說明>-<席碼>-<日期>);每棵樹量 diff 與最近一次 aib test
if DEV and out["code"]:
    dev = {"trees": [], "err": []}
    repo = os.path.abspath(os.path.join(cockpit, "..", ".."))
    def _git(cwd, *args, timeout=8):
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout.strip() if r.returncode == 0 else None
    try:
        rem = "origin"
        for ln in (_git(repo, "remote", "-v") or "").splitlines():
            name, _, rest = ln.partition("\t")
            if "github.com" in rest and "(fetch)" in rest:
                rem = name; break
        try:
            sys.path.insert(0, os.path.join(repo, "tools", "verify"))
            import reuse_index, shadow_tracer
            shadow = str(shadow_tracer.shadow_dir_default())
        except Exception as e:
            reuse_index = None; shadow = ""; dev["err"].append(f"shadow: {e}")
        import time as _t
        for blk in (_git(repo, "worktree", "list", "--porcelain") or "").split("\n\n"):
            path = next((l[9:] for l in blk.splitlines() if l.startswith("worktree ")), None)
            if not path or not os.path.isdir(path):
                continue
            br = _git(path, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
            if out["code"] not in br and out["code"] not in path:
                continue
            if br == "HEAD" and not (_git(path, "status", "--short") or "") and (_git(path, "rev-list", "--count", f"{rem}/main..HEAD") or "0") == "0":
                continue   # 只是對照用的乾淨 checkout(detached、跟主線一樣),不是在改的樹
            ahead = _git(path, "rev-list", "--count", f"{rem}/main..HEAD"); behind = _git(path, "rev-list", "--count", f"HEAD..{rem}/main")
            dirty = (_git(path, "status", "--short") or "").splitlines()
            stat = _git(path, "diff", "--shortstat", f"{rem}/main...HEAD") or ""
            import re as _re
            m_f = _re.search(r"(\d+) files? changed", stat); m_i = _re.search(r"(\d+) insertion", stat); m_d = _re.search(r"(\d+) deletion", stat)
            last = _git(path, "log", "-1", "--format=%ct %s") or "0 "
            lt, _, lsub = last.partition(" ")
            # 影子帳本 verify-<倉身份>.rows.jsonl:每列一支測試(repo=哪棵樹、rc、time_utc)。取這棵樹每支最後一次;
            # 比最後一個 commit 舊的算「舊」(測的是改之前的碼),不算綠
            tests = {"green": 0, "red": 0, "stale": 0, "rows": 0, "last": None, "red_names": []}
            if reuse_index and shadow:
                rid = reuse_index.repo_identity(path) or ""
                latest = {}
                try:
                    with open(os.path.join(shadow, f"verify-{rid}.rows.jsonl"), encoding="utf-8") as fh:
                        for ln in fh:
                            try: r = json.loads(ln)
                            except Exception: continue
                            if r.get("repo") == path and r.get("test"):
                                latest[r["test"]] = r
                except OSError:
                    pass
                import calendar as _cal
                def _utc(t):
                    try: return _cal.timegm(_t.strptime(t, "%Y-%m-%dT%H:%M:%SZ"))
                    except Exception: return 0
                commit_t = int(lt) if lt.isdigit() else 0
                for t, r in latest.items():
                    if _utc(r.get("time_utc") or "") < commit_t:
                        tests["stale"] += 1; continue
                    tests["rows"] += 1
                    if r.get("rc") == 0: tests["green"] += 1
                    else:
                        tests["red"] += 1; tests["red_names"].append(t.split("/")[-1])
                tests["red_names"] = tests["red_names"][:5]
                tests["last"] = max((r.get("time") or "" for r in latest.values()), default=None)
            dev["trees"].append({"path": path, "branch": br, "ahead": int(ahead) if ahead and ahead.isdigit() else None,
                                 "behind": int(behind) if behind and behind.isdigit() else None, "dirty": len(dirty),
                                 "dirty_files": [d[3:] for d in dirty[:6]],
                                 "files": int(m_f.group(1)) if m_f else 0, "ins": int(m_i.group(1)) if m_i else 0, "dels": int(m_d.group(1)) if m_d else 0,
                                 "last_age_s": int(_t.time()) - int(lt) if lt.isdigit() else None, "subject": lsub[:70], "tests": tests})
    except Exception as e:
        dev["err"].append(f"dev git: {e}")
    out["dev"] = dev
    out["err"] += dev["err"]
# QA/驗收視角
if QA and out["code"]:
    qa = {"deliveries": [], "unverified": [], "reviews": [], "err": []}
    repo = os.path.abspath(os.path.join(cockpit, "..", ".."))
    import time as _t
    # (1) 交給我驗的:kind=deliver / report、沒結案、沒封存(waiting + read 兩頁)
    try:
        rows = []
        for tab in ("waiting", "read"):
            r = subprocess.run([sys.executable, os.path.join(cockpit, "dm_queue.py"), "list", "--seat", out["code"], "--tab", tab],
                               capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            rows += json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
        for m in rows:
            if m.get("kind") in ("deliver", "report") and not m.get("done_at") and not m.get("archived_at") and not m.get("superseded_by"):
                qa["deliveries"].append({"id": m.get("id"), "kind": m.get("kind"), "sender": m.get("sender"), "when": m.get("when"),
                                         "age_min": m.get("age_min"), "read": bool(m.get("read_at")),
                                         "msg": relay_summary(m.get("msg") or "", home)[:100]})
        qa["deliveries"].sort(key=lambda d: -(d.get("age_min") or 0))
    except Exception as e:
        qa["err"].append(f"qa dm: {e}")
    # (2) 沒驗的格子:影子帳本裡 measured != True 的列(每棵有我席碼的樹、每支取最後一次),以及有 fail_cells 的
    try:
        sys.path.insert(0, os.path.join(repo, "tools", "verify"))
        import reuse_index, shadow_tracer
        shadow = str(shadow_tracer.shadow_dir_default())
        def _git(cwd, *args):
            r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
            return r.stdout.strip() if r.returncode == 0 else None
        for blk in (_git(repo, "worktree", "list", "--porcelain") or "").split("\n\n"):
            path = next((l[9:] for l in blk.splitlines() if l.startswith("worktree ")), None)
            if not path or not os.path.isdir(path):
                continue
            br = _git(path, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
            if out["code"] not in br and out["code"] not in path:
                continue
            if br == "HEAD" and not (_git(path, "status", "--short") or ""):
                continue   # 對照用的乾淨 checkout,不是在驗的包
            rid = reuse_index.repo_identity(path) or ""
            latest = {}
            try:
                with open(os.path.join(shadow, f"verify-{rid}.rows.jsonl"), encoding="utf-8") as fh:
                    for ln in fh:
                        try: r = json.loads(ln)
                        except Exception: continue
                        if r.get("repo") == path and r.get("test"):
                            latest[r["test"]] = r
            except OSError:
                continue
            unm = [t for t, r in latest.items() if r.get("measured") is not True]
            fails = [t for t, r in latest.items() if r.get("fail_cells")]
            qa["unverified"].append({"branch": br, "tests": len(latest), "unmeasured": len(unm), "fail_cells": len(fails),
                                     "unmeasured_names": [t.split("/")[-1] for t in unm[:5]], "fail_names": [t.split("/")[-1] for t in fails[:5]]})
    except Exception as e:
        qa["err"].append(f"qa shadow: {e}")
    # (3) 我寫過的審查/驗收檔:pending/ 裡檔名帶我席碼、名字像 XREVIEW / ACCEPT / REVIEW 的,最近 7 天
    try:
        pend = os.path.join(home, "pending")
        now = _t.time()
        for f in sorted(glob.glob(os.path.join(pend, "*.md")), key=os.path.getmtime, reverse=True):
            b = os.path.basename(f)
            if out["code"] in b and any(k in b.upper() for k in ("XREVIEW", "ACCEPT", "REVIEW", "VERIFY", "QA")):
                age = now - os.path.getmtime(f)
                if age > 7 * 86400:
                    break
                qa["reviews"].append({"file": b, "age_s": int(age)})
            if len(qa["reviews"]) >= 8:
                break
    except Exception as e:
        qa["err"].append(f"qa reviews: {e}")
    out["qa"] = qa
    out["err"] += qa["err"]
# bot 席視角(the maintainer 10-10 13:0x 要做):<狀態根>/bridge/<session>/status.json 是橋自己寫的;
#   收 = inbox/claimed/ 或 claimed/(bot 拿走的信)、送 = outbox/ 與 delivered/(bot 寫回的);
#   卡住 = inbox/*.json 裡最舊那封放多久沒人拿。codexbot 的 status 沒有 code ⇒ 用 uuid_tail5 補。
#   只列 7 天內動過的;本艙的排前面。pid 活著才算真的在跑(橋當掉時 state 可能還寫 running)。
if BOT:
    bot = {"seats": [], "err": []}
    import time as _t
    now = _t.time()
    def _ages(*ds):
        ts = []
        for d in ds:
            try: ts += [os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".json")]
            except OSError: pass
        return (int(now - max(ts)) if ts else None), (int(now - min(ts)) if ts else None), len(ts)
    for sf in glob.glob(os.path.join(home, "bridge", "*", "status.json")):
        d = load(sf)
        if not isinstance(d, dict):
            continue
        upd = d.get("updated_epoch") or os.path.getmtime(sf)
        if now - upd > 7 * 86400:
            continue
        bd = os.path.dirname(sf)
        alive = None
        if d.get("pid"):
            try: os.kill(int(d["pid"]), 0); alive = True
            except ProcessLookupError: alive = False
            except Exception: alive = None
        new_in, _, n_claimed = _ages(os.path.join(bd, "inbox", "claimed"), os.path.join(bd, "claimed"))
        out_d, _, _ = _ages(os.path.join(bd, "delivered"))
        out_o, _, _ = _ages(os.path.join(bd, "outbox"))
        _, wait_oldest, n_wait = _ages(os.path.join(bd, "inbox"))
        last_reply = d.get("last_reply_epoch")
        outs = [a for a in (out_d, out_o, int(now - last_reply) if last_reply else None) if a is not None]
        bot["seats"].append({
            "code": d.get("code") or (uuid_tail5(os.path.basename(bd)) if uuid_tail5 else None), "provider": d.get("provider"), "cabin": d.get("cabin") or None,
            "session": os.path.basename(bd), "state": d.get("state"), "alive": alive,
            "health": d.get("health"), "err_kind": d.get("last_error_kind"),
            "bound": d.get("agent_id") or d.get("desktop_thread_id") or None,
            "bound_title": (d.get("desktop_thread_title") or "")[:60] or None,
            "waiting": n_wait, "wait_oldest_s": wait_oldest, "claimed": n_claimed,
            "last_in_s": new_in, "last_out_s": min(outs) if outs else None, "updated_s": int(now - upd)})
    bot["seats"].sort(key=lambda r: (r["cabin"] != out.get("cabin_code"), r["alive"] is not True, r["updated_s"]))
    out["bot"] = bot
    out["err"] += bot["err"]
# mod 要不要在這一席開(the maintainer 10-09 問「全隊預設套用?還是依角色選?」):
#   <狀態根>/config/aib-seat-mod.json  {"default": "on"|"off", "roles": {"pm": true, "dev": false, ...}}
#   角色沒列 ⇒ 看 default(檔不存在 ⇒ on);環境變數 AIB_SEAT_MOD=0/1 蓋過一切(單席臨時關/開)
try:
    pol = {"default": "on", "roles": {}}
    cfgp = os.path.join(home, "config", "aib-seat-mod.json")
    if os.path.isfile(cfgp):
        pol.update(json.load(open(cfgp, encoding="utf-8")) or {})
    role = out.get("role") or ""
    env = os.environ.get("AIB_SEAT_MOD")
    if env is not None and env.strip() != "":
        enabled, why = env.strip().lower() not in ("0", "false", "no", "off"), "env AIB_SEAT_MOD"
    elif role in (pol.get("roles") or {}):
        enabled, why = bool(pol["roles"][role]), f"config roles.{role}"
    else:
        enabled, why = str(pol.get("default", "on")).lower() != "off", "config default" if os.path.isfile(cfgp) else "no config (on)"
    out["mod"] = {"enabled": enabled, "why": why, "config": cfgp if os.path.isfile(cfgp) else None}
except Exception as e:
    out["err"].append(f"mod policy: {e}")
    out["mod"] = {"enabled": True, "why": "policy unreadable (on)", "config": None}
# aib 守衛擋下的紀錄:回報閘(report-lint-stop)寫 verdict=block,帶 why/findings。session 欄是 uuid 末五碼。
# 只讀檔尾 64KB(檔有上千行),只回本 session 的最近 5 筆;mod 端用 ts 去重,不重報。
if uuid:
    try:
        lp = os.path.join(home, "logs", "report-lint-stop.jsonl")
        with open(lp, "rb") as fh:
            off = max(0, os.path.getsize(lp) - 65536); fh.seek(off)
            tail = fh.read().decode("utf-8", "replace").splitlines()
            if off: tail = tail[1:]  # 從檔中間切進來時第一行是半行;從頭讀時第一行是完整的
        hits = []
        for ln in tail:
            try: d = json.loads(ln)
            except Exception: continue
            sess = str(d.get("session") or "")
            if d.get("verdict") == "block" and sess and (uuid.endswith(sess) or uuid.startswith(sess)):
                hits.append({"ts": d.get("ts"), "gate": "report_lint", "why": d.get("reason") or d.get("why") or "",
                             "first": ((d.get("findings") or [""])[0] or "")[:100], "n": len(d.get("findings") or [])})
        out["blocks"] = hits[-5:]
    except Exception as e:
        out["err"].append(f"blocks: {e}")
print(json.dumps(out, ensure_ascii=False))
