#!/bin/bash
# aib 艙際歸隊 —— 把別艙的 AI 席位拉回你坐的這一艙(同碼復活優先)
# 正本說明:同資料夾的 SKILL.md。2026-10-09 實跑過 9 席。
# 用法:regroup.sh check|attach|plan|apply [--only 碼,碼] [--include-desktop-bot] [--wait-seconds N] [--even-alive] [--dead-within 小時]
#   check:只跑第一段「艙是不是真的死」,活的艙印出接回來的指令就結束(什麼都不動)
#   attach:第一段之後直接接回來——替「沒有人接著」的活艙開一個新的 Terminal 視窗跑
#           tmux attach -d -t aib-<艙id>(the maintainer 10-09 19:1x:能拉回來就用這個拉回來)。
#           已經有人接著的艙預設不碰(-d 會把那個視窗踢掉);要硬接用 --only 點名艙碼(例 --only 4f8d)。
#   apply 也會先做 attach 這一步,再處理真的死掉的艙。
#   plan/apply 也一定先跑第一段;座艙還活著的艙**不搬**,要硬搬加 --even-alive
#   hub 已經死掉的艙(AI 行程全沒了)也算進來——只認「最近 --dead-within 小時內(預設 24)死的、
#   退出原因是 pid-gone/window-gone/unknown(不是自己關的)、同碼沒在別艙活著」的席。
#   為什麼要這段:10-10 艙 4f8d 的 hub 死了,watch 每 300 秒掃一次把席位檔都補了 exited_at_epoch=pid-gone,
#   原本「有退出痕就當收工」的規則讓 plan/apply 一席都列不出來,只好一席一席手動復活。
set -uo pipefail

MODE="${1:-plan}"; shift || true
ONLY=""; INCLUDE_DESKTOP=0; WAIT_SECONDS=30; ALL_CABINS=0; EVEN_ALIVE=0; DEAD_WITHIN=24
while [ "$#" -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    --only=*) ONLY="${1#--only=}"; shift ;;
    --include-desktop-bot) INCLUDE_DESKTOP=1; shift ;;
    --all-cabins) ALL_CABINS=1; shift ;;
    --even-alive) EVEN_ALIVE=1; shift ;;
    --wait-seconds) WAIT_SECONDS="${2:-30}"; shift 2 ;;
    --dead-within) DEAD_WITHIN="${2:-24}"; shift 2 ;;
    --dead-within=*) DEAD_WITHIN="${1#--dead-within=}"; shift ;;
    *) echo "不認得的參數:$1" >&2; exit 2 ;;
  esac
done
case "$MODE" in check|attach|plan|apply) ;; *) echo "用法:regroup.sh check|attach|plan|apply [--only 碼,碼] [--include-desktop-bot] [--all-cabins] [--even-alive] [--dead-within 小時]" >&2; exit 2 ;; esac

AIB_HOME="${EZ2_AIB_HOME:-$HOME/.ez2-aib}"
REPO="${EZ2_AIB_ROOT:-}"
[ -n "$REPO" ] || REPO="$(cd "${EZ2_COCKPIT_DIR:-$HOME/ez2-ai-b/src/cockpit}/../.." 2>/dev/null && pwd)"
COCKPIT="$REPO/src/cockpit"
PY="${EZ2_PY3:-python3}"
[ -f "$COCKPIT/seat_liveness.py" ] || { echo "找不到 $COCKPIT/seat_liveness.py——設 EZ2_AIB_ROOT 指向正本樹" >&2; exit 2; }

# 本艙:環境優先,否則從 $TMUX 的 socket 路徑取
HUB="${EZ2_AIHUB_SOCKET:-}"
if [ -z "$HUB" ] && [ -n "${TMUX:-}" ]; then HUB="$(basename "${TMUX%%,*}")"; fi
case "$HUB" in
  aihub-*) ;;
  *) echo "量不到你坐在哪一艙(EZ2_AIHUB_SOCKET 沒設、\$TMUX 也導不出 aihub-*)。不猜,停手。" >&2; exit 2 ;;
esac
CABIN="${HUB#aihub-}"
echo "本艙:$CABIN(hub $HUB) · 模式:$MODE"

AIB_BIN="$(command -v aib || true)"
[ -n "$AIB_BIN" ] || AIB_BIN="bash $COCKPIT/aib"

# ── 母體:只認 hub 伺服器還活著的艙。
#   為什麼不是「所有 instances/」:2026-10-09 實測,狀態根底下有 26 個艙目錄,
#   其中 22 個是 10-04~10-07 早就收掉的,它們的席位檔多半沒有 exited_at_epoch
#   (舊版沒有留痕那一步),於是「所有還沒標退出的席」會抓出幾十個墓碑,
#   而那份名單在版面上跟正確答案長得一模一樣。活 hub 才是「艙還在」的判準。
LIVE_CABINS=""
for s_ in $(tmux -L "$HUB" list-sessions >/dev/null 2>&1 && true; ls "${TMUX_TMPDIR:-/private/tmp}/tmux-$(id -u)" 2>/dev/null | grep '^aihub-' || true); do
  c_="${s_#aihub-}"
  [ "$c_" = "$CABIN" ] && continue
  if tmux -L "$s_" list-windows -a -F '#{window_id}' >/dev/null 2>&1; then
    LIVE_CABINS="$LIVE_CABINS $c_"
  fi
done
if [ "$ALL_CABINS" = "1" ]; then
  echo "⚠ --all-cabins:連 hub 已經不在的舊艙殘留席位檔也算進來。那些多半是墓碑,逐席自己看。"
  LIVE_CABINS="ALL"
else
  echo "活著的別艙:${LIVE_CABINS:- (沒有)}"
fi

# ── 最近死掉的艙:hub 不在、席位檔是「被掃到死」(pid-gone/window-gone/unknown)或根本沒留痕,
#   而且是 --dead-within 小時內的事、同碼沒在別艙活著。10-04~10-07 的墓碑超過時窗,自然落在外面。
#   TSV:艙 席數 最後時刻 退出原因 席碼們
DEAD_ROWS=""
if [ "$LIVE_CABINS" != "ALL" ]; then
DEAD_ROWS="$("$PY" - "$AIB_HOME" "$CABIN" "$LIVE_CABINS" "$DEAD_WITHIN" <<'PYDEAD'
import json, os, sys, glob, time
home, cur = sys.argv[1], sys.argv[2]
live = set((sys.argv[3] or "").split())
try: within = float(sys.argv[4]) * 3600
except Exception: within = 24 * 3600
now = time.time()
CRASH = {"pid-gone", "window-gone", "unknown"}
def seats(cab):
    out = []
    for f in sorted(glob.glob(os.path.join(home, "instances", cab, "seats", "w*.json"))):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        if d.get("session_uuid"): out.append((f, d))
    return out
alive = set()                                   # 同碼在活艙(含本艙)還沒退出 ⇒ 不是要救的
for cab in list(live) + [cur]:
    for _, d in seats(cab):
        if not d.get("exited_at_epoch"): alive.add(d["session_uuid"])
for path in sorted(glob.glob(os.path.join(home, "instances", "*"))):
    cab = os.path.basename(path)
    if cab == cur or cab in live: continue
    codes, reasons, last = [], set(), 0
    for f, d in seats(cab):
        u = d["session_uuid"]
        if u in alive: continue
        ex = d.get("exited_at_epoch") or 0
        if ex:
            if (d.get("exit_reason") or "unknown") not in CRASH: continue   # 自己關的不算
            t = ex
        else:
            t = os.path.getmtime(f)
        if now - t > within: continue
        codes.append(u[-5:]); reasons.add(d.get("exit_reason") or "沒留痕"); last = max(last, t)
    if codes:
        print("\t".join([cab, str(len(codes)), time.strftime("%m-%d %H:%M", time.localtime(last)),
                         ",".join(sorted(reasons)), ",".join(codes)]))
PYDEAD
)"
fi
DEAD_CABINS=""
if [ -n "$DEAD_ROWS" ]; then
  echo; echo "── 最近 ${DEAD_WITHIN} 小時內死掉的艙(hub 不在,AI 行程沒了)──"
  while IFS=$'\t' read -r dc_ dn_ dl_ dr_ dcodes_; do
    [ -n "$dc_" ] || continue
    extra_=""
    tmux -L default has-session -t "aib-$dc_" 2>/dev/null && extra_=";外層座艙 aib-$dc_ 還掛著(裡面已經沒有 AI),復活完可 tmux -L default kill-session -t aib-$dc_"
    echo "💀 艙 $dc_:${dn_} 席,最後見於 ${dl_},退出原因 ${dr_},席碼 ${dcodes_}${extra_}"
    echo "   ⇒ 接不回來,要同碼復活:regroup.sh plan 看名單,regroup.sh apply 真搬"
    DEAD_CABINS="$DEAD_CABINS $dc_"
  done <<< "$DEAD_ROWS"
else
  [ "$LIVE_CABINS" = "ALL" ] || echo "最近 ${DEAD_WITHIN} 小時內死掉的艙:(沒有)"
fi

# ══ 第一段:先驗「艙是不是真的死」——能接回來就接回來,不要搬(the maintainer 2026-10-09 18:5x 直令)
#   實例:10-09 18:47 艙 4f8d 的畫面整頁重複印狀態列「[Ez2 AIB v0:cockpit* 1:war-room」、
#   右欄時鐘停住,看起來像艙關掉了。實查:外層座艙 session aib-…-4f8d 還在、hub 13 窗全活、
#   ae3cf/3d156/2a900/021c2 行程全活;壞的只是 Terminal.app 那個視窗的顯示。
#   從別艙的席上跑 `tmux ls` 只看得到自己 hub 的 socket,會誤判舊艙已死,然後走到
#   下面的殺窗+resume。所以這裡先分三層驗,再決定要不要搬:
#     ① 外層座艙 session(預設 socket 的 aib-<艙id>)在不在
#     ② hub(aihub-<艙id>)的窗在不在(上面 LIVE_CABINS 已驗)
#     ③ 有沒有人接著(list-clients)
#   ①在 ⇒ 艙活著,只是畫面卡:新開一個 Terminal 視窗 `tmux attach -d -t aib-<艙id>`(-d 踢掉卡住的舊客戶端)。
#          `tmux refresh-client` 10-09 實測對卡住的視窗沒用,要 -d。這種艙**不搬**。
#     ①不在、②在 ⇒ 座艙死了、AI 還在:在該艙開任一席(寫完整艙號)會跑 cockpit-full.sh --ensure 重組座艙,也不用搬。
#     ②也不在 ⇒ 才輪到下面的同碼復活。
ATTACHABLE=""; HEADLESS=""; NOCLIENT=""
if [ "$LIVE_CABINS" != "ALL" ] && [ -n "$LIVE_CABINS" ]; then
  echo; echo "── 第一段:艙是不是真的死 ──"
  for c_ in $LIVE_CABINS; do
    if tmux -L default has-session -t "aib-$c_" 2>/dev/null; then
      cl_="$(tmux -L default list-clients -t "aib-$c_" -F '#{client_tty}' 2>/dev/null | tr '\n' ' ')"
      nw_="$(tmux -L "aihub-$c_" list-windows -a -F '#{window_id}' 2>/dev/null | wc -l | tr -d ' ')"
      echo "✅ 艙 $c_ 還活著:座艙 session aib-$c_ 在、hub ${nw_} 窗、客戶端:${cl_:-(沒有人接著)}"
      echo "   ⇒ 不搬。接回來:開一個新的 Terminal 視窗打   tmux attach -d -t aib-$c_"
      ATTACHABLE="$ATTACHABLE $c_"
      [ -n "$cl_" ] || NOCLIENT="$NOCLIENT $c_"
    else
      echo "⚠ 艙 $c_:座艙 session aib-$c_ 不在,但 hub 還活(AI 行程還在)"
      echo "   ⇒ 先重組座艙,不用搬:在該艙開任一席寫完整艙號(例 aib claude dgrs root $c_)會跑 cockpit-full.sh --ensure"
      HEADLESS="$HEADLESS $c_"
    fi
  done
  if [ "$MODE" = "check" ]; then
    echo; echo "(check 模式,什麼都沒動。要直接接回來:regroup.sh attach。hub 也不在的艙列在上面 💀 那段${DEAD_CABINS:-(這次沒有)};那種才需要 plan/apply 同碼復活)"
    exit 0
  fi
  # ── 接回來:替活艙開新的 Terminal 視窗跑 tmux attach -d(attach / apply 才做;plan 只印)
  #   目標:沒點名 ⇒ 只接「沒有人接著」的艙;--only 點名 ⇒ 只接點到的(有人接著也接,-d 會踢掉舊視窗)。
  TARGETS_=""
  if [ -n "$ONLY" ] && [ "$MODE" = "attach" ]; then
    for c_ in $ATTACHABLE; do
      for o_ in $(echo "$ONLY" | tr ',' ' '); do case "$c_" in *"$o_") TARGETS_="$TARGETS_ $c_" ;; esac; done
    done
  else
    TARGETS_="$NOCLIENT"
  fi
  if [ "$MODE" = "attach" ] || [ "$MODE" = "apply" ] || [ "$MODE" = "plan" ]; then
    echo; echo "── 接回來 ──"
    [ -n "$TARGETS_" ] || echo "(沒有要接的艙:活艙都已經有人接著${ONLY:+,或 --only 沒點中活艙})"
    TMUX_BIN_="$(command -v tmux)"
    for c_ in $TARGETS_; do
      cmd_="$TMUX_BIN_ -L default attach -d -t aib-$c_"
      if [ "$MODE" = "plan" ]; then echo "會開新 Terminal 視窗:$cmd_"; continue; fi
      if osascript -e "tell application \"Terminal\" to do script \"$cmd_\"" -e 'tell application "Terminal" to activate' >/dev/null 2>&1; then
        ok_=""
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          sleep 0.5
          ok_="$(tmux -L default list-clients -t "aib-$c_" -F '#{client_tty}' 2>/dev/null | tr '\n' ' ')"
          [ -n "$ok_" ] && break
        done
        if [ -n "$ok_" ]; then echo "✅ 艙 $c_ 接回來了:新 Terminal 視窗 ${ok_}"
        else echo "✗ 艙 $c_:開了 Terminal 視窗,但 5 秒內沒看到它接上。自己打:$cmd_"; fi
      else
        echo "✗ 艙 $c_:叫不動 Terminal(osascript 失敗,可能是沒給自動化權限)。自己開視窗打:$cmd_"
      fi
    done
    [ "$MODE" = "attach" ] && exit 0
  fi
  if [ -n "$ATTACHABLE" ] && [ "$EVEN_ALIVE" != "1" ]; then
    echo "   ※ 以上「還活著」的艙從名單拿掉(要硬搬:--even-alive)"
    keep_=""
    for c_ in $LIVE_CABINS; do case " $ATTACHABLE " in *" $c_ "*) ;; *) keep_="$keep_ $c_" ;; esac; done
    LIVE_CABINS="$keep_"
    [ -n "$LIVE_CABINS$DEAD_CABINS" ] || { echo "別艙都還活著,沒有人要搬。先把它們接回來。"; exit 0; }
  fi
elif [ "$MODE" = "check" ] || [ "$MODE" = "attach" ]; then
  echo "($MODE 模式:沒有活著的別艙可驗,或你用了 --all-cabins${DEAD_CABINS:+;死掉的艙見上面 💀,要 plan/apply})"; exit 0
fi
# 死掉的艙併進要盤點的母體(它們的席位檔有退出痕,下面的盤點對這些艙放行)
[ "$LIVE_CABINS" = "ALL" ] || LIVE_CABINS="$LIVE_CABINS $DEAD_CABINS"

# ── 盤點別艙的席(TSV:艙 窗 provider mode role model uuid 已退出 對話檔 人格 路由 桌面bot)
ROWS="$("$PY" - "$AIB_HOME" "$CABIN" "$LIVE_CABINS" "$COCKPIT" "$DEAD_CABINS" <<'PY'
import json, os, sys, glob, re, pathlib
from urllib.parse import quote
home, cur = sys.argv[1], sys.argv[2]
live = (sys.argv[3] or "").split()
all_cabins = live == ["ALL"]
dead = set((sys.argv[5] or "").split())
CRASH = {"pid-gone", "window-gone", "unknown"}
alive_uuids = set()      # 同碼已經在活艙(含本艙)坐著 ⇒ 死艙裡那份舊席位檔只是搬家前的殘影,不是人
for f in glob.glob(os.path.join(home, "instances", "*", "seats", "w*.json")):
    cab_ = f.split(os.sep)[-3]
    if cab_ in dead: continue
    if cab_ != cur and not all_cabins and cab_ not in live: continue
    try: d_ = json.load(open(f, encoding="utf-8"))
    except Exception: continue
    if d_.get("session_uuid") and not d_.get("exited_at_epoch"): alive_uuids.add(d_["session_uuid"])
sys.path.insert(0, sys.argv[4])
try:
    import bot_caps
except Exception:
    bot_caps = None
def bot_binding(prov, code):
    """機器人席的綁定值住在 bridge/<席碼>/status.json 的 agent_id(10-09 實量:grokbot 每席都有)。
    ★ 要在關舊窗、復活**之前**讀:不帶值復活會讓橋把這一欄重寫成空的(10-09 19474 實踩)。"""
    env = ""
    if bot_caps is not None:
        try:
            env = bot_caps.caps(prov).bind_env or ""
        except Exception:
            env = ""
    if not env:
        return "-", "-"
    try:
        st = json.loads((pathlib.Path(home) / "bridge" / code / "status.json").read_text(encoding="utf-8"))
        val = st.get("agent_id") or ""
    except Exception:
        val = ""
    return env, (val or "-")
H = pathlib.Path.home()
def binding(code):
    idd = pathlib.Path(home) / "identity"
    def load(p):
        try: return json.loads((idd / p).read_text(encoding="utf-8"))
        except Exception: return {}
    b = (load("bindings.json").get("bindings") or {}).get(code) or {}
    if not b.get("persona_uid"):
        b = (load("seat-display.json").get("display") or {}).get(code) or {}
    if not isinstance(b, dict): b = {}
    return b.get("persona_uid") or "", b.get("route_key") or ""
def transcript(prov, uuid, cwd):
    if prov == "claude":
        slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
        return (H / ".claude" / "projects" / slug / (uuid + ".jsonl")).exists()
    if prov == "grok":
        return (H / ".grok" / "sessions" / quote(cwd, safe="") / uuid).exists()
    if prov == "codex":
        return bool(glob.glob(str(H / ".codex" / "sessions" / "**" / ("*" + uuid + "*")), recursive=True))
    return True          # bot 家與 API 家沒有本機對話檔的概念,照 resumable 處理
for f in sorted(glob.glob(os.path.join(home, "instances", "*", "seats", "w*.json"))):
    cab = f.split(os.sep)[-3]
    if cab == cur: continue
    if not all_cabins and cab not in live: continue
    try: d = json.load(open(f, encoding="utf-8"))
    except Exception: continue
    u = d.get("session_uuid") or ""
    if not u: continue
    if d.get("exited_at_epoch"):
        # 已經收過工的舊紀錄不算人——除非這艙是「最近死掉的艙」而且退出痕是被掃到死(不是自己關的)
        if cab not in dead or (d.get("exit_reason") or "unknown") not in CRASH: continue
        if u in alive_uuids: continue                # 已在別艙同碼復活過了
    prov, cwd = d.get("provider") or "", d.get("cwd") or ""
    p, r = binding(u[-5:])
    desk = 1 if "AIB Desktop Bot" in cwd else 0
    benv, bval = bot_binding(prov, u[-5:])
    # 空欄一律印 "-":bash 的 read 把連續 tab 當一個分隔,空欄會讓後面整排欄位左移(10-11 測試實踩:席碼變空、對話檔變無)
    print("\t".join([cab, d.get("window_id") or "-", prov, d.get("mode") or "dgrs",
                     d.get("role") or "root", d.get("model_requested") or "-", u,
                     "0", "1" if transcript(prov, u, cwd) else "0", p or "-", r or "-", str(desk), benv, bval]))
PY
)"

[ -n "$ROWS" ] || { echo "別艙沒有還在編制的席位——沒有人要搬。"; exit 0; }

printf '\n%-6s %-5s %-8s %-5s %-6s %-5s %s\n' 席碼 窗 provider role 對話檔 人格 走哪條
echo "────────────────────────────────────────────────────────────────────"
PLAN=""
while IFS=$'\t' read -r cab win prov mode role model uuid ex tr persona route desk benv bval; do
  [ -n "$uuid" ] || continue
  code="${uuid: -5}"
  if [ -n "$ONLY" ] && [[ ",$ONLY," != *",$code,"* ]]; then continue; fi
  if [ "$desk" = "1" ] && [ "$INCLUDE_DESKTOP" != "1" ]; then
    printf '%-6s %-5s %-8s %-5s %-6s %-5s %s\n' "$code" "$win" "$prov" "$role" "-" "$persona" "跳過(桌面機器人自己的席)"
    continue
  fi
  if [ "$tr" = "1" ]; then path="同碼復活"; else path="新席+移植人格($persona)"; fi
  case " $DEAD_CABINS " in *" $cab "*) path="$path(艙已死)" ;; esac
  if [ "$benv" != "-" ]; then
    if [ "$bval" != "-" ]; then path="$path,帶回綁定 ${bval:0:8}…"; else path="$path,⚠ 機器人綁定量不到(要問人)"; fi
  fi
  printf '%-6s %-5s %-8s %-5s %-6s %-5s %s\n' "$code" "$win" "$prov" "$role" "$([ "$tr" = 1 ] && echo 有 || echo 無)" "$persona" "$path"
  PLAN="${PLAN}${cab}	${win}	${prov}	${mode}	${role}	${model}	${uuid}	${tr}	${persona}	${route}	${benv}	${bval}
"
done <<< "$ROWS"
echo "────────────────────────────────────────────────────────────────────"
[ "$MODE" = "plan" ] && { echo "(plan 模式,什麼都沒動。要真搬:apply)"; exit 0; }
[ -n "$PLAN" ] || { echo "沒有要搬的席。"; exit 0; }

# ── 等某個 uuid 的持有者清空
wait_free() {
  local uuid="$1" i=0 d
  while [ "$i" -lt "$WAIT_SECONDS" ]; do
    d="$("$PY" "$COCKPIT/seat_liveness.py" holders "$uuid" 2>/dev/null | "$PY" -c 'import json,sys
try: print(json.load(sys.stdin).get("describe") or "")
except Exception: print("?")' )"
    [ -z "$d" ] && return 0
    [ "$d" = "?" ] && return 1
    sleep 2; i=$((i+2))
  done
  return 1
}

OK=0; BAD=0
while IFS=$'\t' read -r cab win prov mode role model uuid tr persona route benv bval; do
  [ -n "$uuid" ] || continue
  code="${uuid: -5}"
  echo; echo "▶ $code($prov/$role,舊艙 ${cab: -4} $win)"

  # ① 關舊窗(窗還在才關;舊艙伺服器已經不在就跳過這步)
  if tmux -L "aihub-$cab" list-windows -a -F '#{window_id}' 2>/dev/null | grep -qx "$win"; then
    tmux -L "aihub-$cab" kill-window -t "$win" 2>/dev/null && echo "  ① 關掉舊窗 $win"
  else
    echo "  ① 舊窗已經不在,跳過"
  fi
  # ② 留退出痕
  "$PY" "$COCKPIT/seat_exit.py" sweep --instance-home "$AIB_HOME/instances/$cab" --hub "aihub-$cab" 2>&1 | tail -1 | sed 's/^/  ② /'
  # ③ 等死透(這一步沒過就不要動,否則守衛會拒絕,而它的拒絕訊息在 macOS 上是壞的)
  if wait_free "$uuid"; then echo "  ③ 持有者已清空"
  else echo "  ③ 🔴 等了 ${WAIT_SECONDS}s 持有者還沒清空,這一席跳過(不硬來)"; BAD=$((BAD+1)); continue; fi

  # ④ 復活 / 開新席
  AGAIN=()
  if tmux -L "$HUB" list-windows -a -F '#{window_name}' 2>/dev/null | grep -qE "^${role}[0-9]*-${prov}$"; then AGAIN=(--again); fi
  MODELARG=(); [ -n "$model" ] && [ "$model" != "-" ] && [ "$model" != "$prov" ] && MODELARG=(--model "$model")
  if [ "$tr" = "1" ]; then
    BINDENV=()
    if [ "$benv" != "-" ] && [ "$bval" != "-" ]; then BINDENV=("$benv=$bval"); fi
    # 10-10:同碼復活**不帶** --model——舊席位檔的 model_requested 多半只是開席當天的預設,席內 /model 換過的
    #   只有對話紀錄知道。新版 aib(seat_fork.revive_model)會自己走三層:對話紀錄 → 舊席位檔 → providers.json 預設,
    #   並在 /tmp/regroup-<碼>.log 印出用了哪一層。舊版 aib 沒這段時等於退回席位檔/預設,行為同以前,不會更差。
    env "${BINDENV[@]}" AIB_REVIVE_SESSION_UUID="$uuid" EZ2_AIHUB_SOCKET="$HUB" \
      timeout 180 $AIB_BIN "$prov" "$mode" "$role" "${AGAIN[@]}" </dev/null >/tmp/regroup-$code.log 2>&1
    rc=$?
    echo "  ④ 同碼復活 rc=$rc(紀錄 /tmp/regroup-$code.log)"
  else
    EZ2_AIHUB_SOCKET="$HUB" timeout 180 $AIB_BIN "$prov" "$mode" "$role" "${MODELARG[@]}" "${AGAIN[@]}" \
      </dev/null >/tmp/regroup-$code.log 2>&1
    rc=$?
    echo "  ④ 開新席(對話救不回來)rc=$rc"
  fi
  [ "$rc" = "0" ] || { echo "  🔴 開席失敗,看 /tmp/regroup-$code.log"; BAD=$((BAD+1)); continue; }
  sleep 6

  # ⑤ codex 的席碼不會自己登記(AIB_SESSION_ID_PROVIDERS 沒有它)
  if [ "$prov" = "codex" ]; then
    nw="$(tmux -L "$HUB" list-windows -a -F '#{window_id} #{window_name}' 2>/dev/null | awk '$2 ~ /-codex$/{print $1}' | tail -1)"
    if [ -n "$nw" ]; then
      EZ2_AIHUB_SOCKET="$HUB" EZ2_AIB_INSTANCE="$CABIN" EZ2_AIB_INSTANCE_HOME="$AIB_HOME/instances/$CABIN" \
        "$PY" "$COCKPIT/seat-detect.py" --window "$nw" --apply 2>&1 | tail -1 | sed 's/^/  ⑤ 補登記 /'
    fi
  fi
  # ⑥ 新席路徑:移植人格與大腦(唯一合法寫入器 bind_one.py)
  if [ "$tr" != "1" ] && [ "$persona" != "-" ] && [ -n "$persona" ]; then
    nid="$("$PY" - "$AIB_HOME/instances/$CABIN/seats" "$prov" "$role" <<'PY'
import json,glob,sys,os
seats,prov,role=sys.argv[1],sys.argv[2],sys.argv[3]
best=None
for f in glob.glob(os.path.join(seats,'w*.json')):
    try: d=json.load(open(f,encoding='utf-8'))
    except Exception: continue
    if d.get('provider')==prov and d.get('role')==role and not d.get('exited_at_epoch'):
        if best is None or (d.get('started_epoch') or 0)>(best.get('started_epoch') or 0): best=d
print((best or {}).get('seat_id') or '')
PY
)"
    if [ -n "$nid" ]; then
      BR=(); [ "$route" != "-" ] && [ -n "$route" ] && BR=(--brain "$route")
      "$PY" "$REPO/src/identity/bind_one.py" bind --seat "$nid" --card "$persona" \
        "${BR[@]}" --actor "regroup@$code/aib" --actor-kind system --state-root "$AIB_HOME" 2>&1 | tail -1 | sed 's/^/  ⑥ 移植人格 /'
    else
      echo "  ⑥ 🔴 量不到新席 seat_id,人格沒移植(手動:bind_one.py bind --seat <id> --card $persona)"
    fi
  fi
  OK=$((OK+1))
done <<< "$PLAN"

echo; echo "════ 收尾驗證 ════"
echo "成功 $OK 席,出問題 $BAD 席"
echo "--- 本艙視窗 ---"; tmux -L "$HUB" list-windows -a -F '#{window_id} #{window_name}' 2>/dev/null | sort -t@ -k2 -n
echo "--- 全機同碼雙活(必須是 []) ---"; "$PY" "$COCKPIT/seat_liveness.py" dups 2>&1 | head -3
echo
echo "機器人席:看上表「帶回綁定」那一欄;標「⚠ 量不到」的才需要問人要編號(不要猜)。"
echo "  驗法:~/.ez2-aib/bridge/<席碼>/status.json 的 agent_id 要等於帶回去的值。"
[ "$BAD" = "0" ] || exit 1
