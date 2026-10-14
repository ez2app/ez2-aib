#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.2 標題接軌:把 title-beat 與信任紅燈掛上 tmux 標題。
#
#   title-beat.sh --once [--print]     取樣一次(--print 只印,不碰 tmux)
#   title-beat.sh --loop               常駐取樣(預設 5 秒一拍)
#   title-beat.sh --install [--dry-run] 設 tmux set-titles 讓標題吃我們的字串
#
# 為什麼要有節拍:靜止的標題和當掉的更新器長得一模一樣。節拍每拍前進一格,
# 標題凍住就是更新器死了 —— 讓「還活著」變成看得見的事實,而不是假設。
#
# 為什麼判定不在這支腳本裡:紅黃綠的規則是純函數(title_beat.py),
# shell 只負責取樣與貼字串。規則有測試,取樣沒有,兩者分開才驗得動。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE="once"
PRINT_ONLY=0
INTERVAL="${EZ2_TITLE_INTERVAL:-5}"
BEAT_FILE="$EZ2_COCKPIT_STATE_DIR/title-beat-seq"

for a in "$@"; do
  case "$a" in
    --once) MODE="once" ;;
    --loop) MODE="loop" ;;
    --install) MODE="install" ;;
    --print) PRINT_ONLY=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install title-beat.sh "$HERE" || exit $?
  else
    # 報錯走 aib/i18n 四語鍵;字典或 python 出事就印鍵名,報錯不能因翻譯失敗而消失。
    { python3 "$COCKPIT/i18n_lib.py" aib.install_guard.missing --param script=title-beat.sh \
        --param "path=$COCKPIT/install-guard.sh" 2>/dev/null || printf '%s\n' aib.install_guard.missing; } >&2
    exit 6
  fi
fi

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

# 08-14:原 read-modify-write 無鎖+寫失敗 `|| true` 吞掉,改走 aib_state_transition
# (flock+temp+rename,與 depth/tab 類寫者同一套)。寫不進=stderr 留痕+節拍凍在
# 檔值——凍住是看得見的事實(見檔頭),但標題本體與信任燈不陪葬,rc 仍 0。
. "$COCKPIT/state-transition.sh"

_advance_beat() {
  local n="$1"
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s\n' $(( (n + 1) % 4 ))
}

next_beat() {
  local n
  if ! aib_state_transition "$BEAT_FILE" _advance_beat; then
    echo "title-beat: beat 狀態寫入失敗($BEAT_FILE),節拍凍結" >&2
  fi
  n=$(cat "$BEAT_FILE" 2>/dev/null || echo 0)
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
  printf '%s' "$n"
}

# ★★ 2026-08-17:定址修正。這支腳本原本的兩半**打到不同的 tmux server**:
#   寫入用裸 `tmux`(吃 $TMUX,從席位跑就是**內層 hub**),
#   窗號探針用 `env -u TMUX tmux`(→ **外層** default server)。
#   後果實測(instance 8):
#     窗號探針回 @0 —— 那是外層某個 session 的窗,而席位檔的鍵是**hub 的窗號**
#       (@2/@3/@6/@7 → w2/w3/w6/w7.json),於是永遠查不到席位,印 `unbound`
#     寫入落在 aihub-8,而那台 `set-titles=off`、`status-right` 還是原廠值
#       —— **那一格連讀者都沒有**
#   ★ `title_beat.py` 的判定邏輯**沒有壞**:餵對窗號就對
#       EZ2_TB_WINDOW=@3 → `◐ 🎓 a seat/Claude … | 1d5h`(advisor 圖示與 uptime 都正確)
#     所以這一刀只改**去處**,不動那個純函數。看到 `unbound` 不要去改 title_beat.py。
#
# 顯示哪一席:the maintainer 2026-08-17 裁「顯示你當下正在看的那一席」——
#   中欄切到哪一席標題就跟到哪一席(直令原文「方便所有 AI 彼此定位」=
#   標題要隨時回答「我現在在跟誰說話」)。所以來源是 **hub 的 active window**,
#   不是艦長席。★ 順帶好處:它不依賴「誰是艦長」那個會飄的來源。
hub_active_window() {
  # tmux 的 format 讀不到別台 server,shell 可以 —— 這是這一格必須是行程而不是
  # format 的唯一理由。取不到就回空:席位那條路自然走不通而退回原本的燈,
  # **不編一個窗號出來**(同下面 EZ2_TB_WINDOW 的既有紀律)。
  local sock="${EZ2_AIHUB_SOCKET:-}"
  [ -n "$sock" ] || return 0
  env -u TMUX tmux -L "$sock" display-message -p '#{window_id}' 2>/dev/null || true
}

# 標題寫在哪:**外層 session 層**,不是 `-g`。
#   tmux 的 session 層設定蓋過全域,而 cockpit-full.sh:417 寫的正是 session 層。
#   所以原本的 `set -g @aib_title` 就算被正確呼叫、算得完全正確,也**永遠不會
#   上畫面** —— 而任何以「@aib_title 有沒有被寫」為判準的測試都會通過,
#   因為那個值真的被寫進去了,只是寫在沒有讀者的那一層。
#   ★ 判準只能是「讀的那一端拿到了什麼」:`display-message -p '#{E:@aib_title}'`。
write_title() {
  local title="$1" s="${EZ2_TMUX_SESSION:-}"
  if [ -z "$s" ]; then
    echo "title-beat: EZ2_TMUX_SESSION 未設,不知道要寫到哪個 session" >&2
    return 0     # rc0 紅線:標題寫不進去不得擋住呼叫者
  fi
  run env -u TMUX tmux set-option -t "$s" @aib_title "$title" 2>/dev/null || true
  # 版號與更新狀態餵給狀態列(顏色只在這一層可能——標題那層只吃純文字)。
  # 兩個都是**純值**,樣式由 status-left 的格式字串決定;把樣式寫進值裡會
  # 讓「值」與「怎麼畫」混在一起,而下一個人改配色時得去改產生器。
  local vf="$EZ2_COCKPIT_STATE_DIR/title-version.json"
  if [ -f "$vf" ]; then
    local ver state
    ver="$("$EZ2_PY3" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("ver") or "")' "$vf" 2>/dev/null || true)"
    state="$("$EZ2_PY3" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state") or "unknown")' "$vf" 2>/dev/null || true)"
    [ -n "$ver" ] && run env -u TMUX tmux set-option -t "$s" @aib_ver "$ver" 2>/dev/null || true
    [ -n "$state" ] && run env -u TMUX tmux set-option -t "$s" @aib_ver_state "$state" 2>/dev/null || true
  fi
}

sample() {  # 取樣一次 -> 印出標題字串
  local uuid="${EZ2_SESSION_UUID:-${ACB_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}}"
  local beat; beat="$(next_beat)"
  # EZ2_TB_WINDOW:席位檔的檔名綁窗號(w<N>.json)。取不到就留空,
  # 席位檔那條路自然走不通而退回原本的紅燈——**不編一個窗號出來**。
  # ★ `--print` 的契約是**零接觸 tmux**(test_print_mode_never_touches_tmux),
  #   所以只有在非 print 模式、且環境沒給窗號時才去問 tmux。
  #   第一版我無條件問,當場弄紅那支既有測試——**它是對的,我改壞了契約**。
  local wid="${EZ2_TB_WINDOW:-}"
  if [ -z "$wid" ] && [ "$PRINT_ONLY" != "1" ]; then
    wid="$(hub_active_window)"
  fi
  EZ2_TB_UUID="$uuid" EZ2_TB_BEAT="$beat" EZ2_TB_COLOR="${1:-0}" EZ2_TB_PRINT_ONLY="$PRINT_ONLY" \
  EZ2_TB_WINDOW="$wid" "$EZ2_PY3" - <<'PY'
import json, os, re, sys, time
sys.path.insert(0, os.path.join(os.environ["EZ2_COCKPIT_DIR"], "title"))
sys.path.insert(0, os.path.join(os.environ["EZ2_COCKPIT_DIR"], "..", "binding"))
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])          # dm_header.local_addr(IP 與 dm 抬頭同源)
from title_beat import (trust_light, title_string, cabin_label,  # noqa: E402
                        seat_record, seat_light, cabin_since, uptime_str,
                        version_segment, version_state, short_version,
                        model_label)

uuid = os.environ.get("EZ2_TB_UUID") or ""

# ★★ 2026-08-21(車道 E,ACB 完全移除):**ACB registry 那條路已整條移除**。
#   移除前的實測(a seat):偽造一筆 sessions/by-id/<uuid>.json 進去,
#   標題的署名段整個被它接管(`NAILSIG@zzzzz/Nail` 蓋掉 `a seat/Claude`)
#   —— 所以它是**活碼不是死碼**,拆掉是行為改變,不是 no-op。
#   ★ 08-16 那句「指到空目錄顯示的字一模一樣」是對的量測、過度推廣的結論:
#     兩端都空當然一樣,那一發沒有「必須紅」的那一端。
#   拆掉仍然安全的理由是**母體**:現存 52 席有 uuid,ACB registry 517 筆,
#   **交集 0**;且該目錄最新一筆停在 08-11、不會再有新的。
#   自此席位檔是唯一來源(上限黃燈,理由見 title_beat.seat_record 的 docstring)。
seat_based = None
# ★ 這四行原本在解釋「ACB 那條路走得通時 seat 會 NameError」——**那條路已不存在**,
#   註解跟著改而不是留著:留著會讓下一個人去找一條找不到的分支。
#   保留的結論:seat / seat_based 要先有定義,下面版號段會引用。
seat = None
# EH.2:席位目錄一律走單一真相源 seat_onboard.seats_dir()
#   (順位 HUB 導出 > EZ2_AIB_INSTANCE_HOME > 主艙,理由見該函式的 docstring)。
# ★ 刻意**不在這裡再寫一份**:全庫已經有五份各自實作的 seats_dir(),
#   而 tests/test-seats-dir-resolution.py 的 14 支債就是這樣長出來的。
#   複製一份能讓那支測試轉綠,但那是讓判準過關,不是把問題修好。
# ★ 取不到就讓 seat 留 None:**主艙不是回退來源,是衝突**。
#   顯示「未綁定」比顯示別艙的殘檔誠實 —— 08-21 五席全部印錯 seat id
#   就是回退成功造成的,而**成功不會叫**。
seats = os.environ.get("EZ2_SEATS_DIR")
if not seats:
    try:
        sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
        from seat_onboard import seats_dir as _seats_dir
        seats = _seats_dir()
    except Exception:
        seats = None
wid = os.environ.get("EZ2_TB_WINDOW") or ""
if seats:
    try:
        with open(os.path.join(seats, "w%s.json" % wid.lstrip("@")),
                  encoding="utf-8") as fh:
            seat = json.load(fh)
    except (OSError, ValueError):
        seat = None
if seat:
    # 活性兩個獨立訊號,都在**這裡**量(判定是純函數,不碰系統):
    #   ① pid 還在不在 —— 直接問作業系統,比讀心跳檔可靠
    #   ② transcript 多久沒動 —— 席位在工作的獨立證據
    alive = False
    try:
        pid = int(seat.get("pid") or 0)
        if pid > 0:
            os.kill(pid, 0)          # 不送訊號,只問「這個 pid 在嗎」
            alive = True
    except (OSError, ValueError, TypeError):
        alive = False
    age = None
    model = None
    # ★★ 2026-08-21 修(既有缺陷,不是本次引入):transcript 要用**被顯示
    #   的那一席自己的** session_uuid,不是執行本腳本那個行程的環境變數。
    #   席位是用**窗號**選的(EZ2_TB_WINDOW),而 uuid 來自
    #   EZ2_SESSION_UUID / CLAUDE_CODE_SESSION_ID —— 兩者不同源。
    #   實測:EZ2_TB_WINDOW=@3 顯示 `a seat`,而讀的是
    #   CLAUDE_CODE_SESSION_ID=8864a1c7(root 席)的 transcript,
    #   pm 席位檔登記的是 efc0612e。
    #   後果:標題上任何一席的「transcript 多久沒動」都是**執行者自己的**
    #   活性,不是那一席的。加上模型之後同一條路會把別人的模型畫到這一席
    #   頭上,更明顯。
    #   ★ 今天兩邊剛好都是 claude-opus-5,所以模型看起來是對的——
    #     **一個巧合遮住了缺陷**。取不到席位 uuid 才退回環境變數。
    _tuuid = None
    if isinstance(seat.get("session_uuid"), str):
        _tuuid = seat["session_uuid"]
    uuid_for_transcript = _tuuid or uuid
    if uuid_for_transcript:
        for base in (os.path.expanduser("~/.claude/projects"),):
            try:
                for d in os.listdir(base):
                    p = os.path.join(base, d,
                                     uuid_for_transcript + ".jsonl")
                    if os.path.exists(p):
                        age = time.time() - os.path.getmtime(p)
                        # 模型:讀 transcript **尾端固定一段**,不整檔掃。
                        # ★ 成本是硬約束:標題每 5 秒一拍、十六個 instance
                        #   各一份,而 transcript 會長到幾千行。所以只 seek
                        #   到檔尾 64KB 抓最後一次出現的 model。
                        # ★ 只有 claude 走得到這裡(它的 transcript 用
                        #   session uuid 當檔名,可直接定位)。**codex /
                        #   agy / copilot 今天取不到,那些席會退回兩段
                        #   sig,不是壞掉。**
                        #   codex 的模型在 rollout meta 首行的
                        #   `payload.base_instructions.provenance.model`
                        #   (實查 gpt-5.6-sol),但檔名帶的是 thread id
                        #   不是 session id,配對要掃目錄——每拍掃一次太貴。
                        #   欄位路徑記在這裡,接手的人不必重查。
                        try:
                            sz = os.path.getsize(p)
                            with open(p, "rb") as fh:
                                if sz > 65536:
                                    fh.seek(sz - 65536)
                                tail = fh.read().decode("utf-8", "ignore")
                            hits = re.findall(r'"model"\s*:\s*"([^"]+)"',
                                              tail)
                            if hits:
                                model = hits[-1]
                        except (OSError, ValueError):
                            model = None
                        break
            except OSError:
                pass
    # 人格:走**右欄同一支** seat_persona.persona_of(),不另做取值——
    # 它已經擋掉 `@` 與 `|`(假人格@他人五碼會在畫面上冒充別席身分)。
    # 取不到就傳 None,seat_record 會退回 role,畫面不會空掉。
    persona = None
    try:
        sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
        from seat_persona import persona_of, load_display
        try:
            persona = persona_of(seat, load_display()) or None
        except TypeError:            # 舊簽名相容
            persona = persona_of(seat) or None
    except Exception:
        persona = None
    # 段位圖示:讀右欄**算完的結果**,不在這裡重算一次。
    # ★ 為什麼:👑 的真規則(全域唯一、只給第一艦隊首位、未編制永不
    #   戴冠)住在 pane-right-ai.sh,而本檔看不到別席。在此之前標題自
    #   己判,判出來的是「root 一律 ⚓」——艙裡只有一個艦長時那是錯的
    #   (⚓ 語意=第二位起),the maintainer 08-21 實見。抄一份規則進來只會讓兩
    #   個畫面繼續各說各話,所以改成消費它的產物。
    # ★ 比對優先用 seat_id 不用窗號:窗號會被重用(死窗重生仍是同號),
    #   而五碼綁的是這一席。取不到就傳 None,由純函數退回原本的自判。
    rank_hint = None
    try:
        _rm = os.path.join(os.environ["EZ2_COCKPIT_STATE_DIR"],
                           "ai-rowmap.json")
        _sid = seat.get("seat_id")
        with open(_rm, encoding="utf-8") as fh:
            for _row in json.load(fh):
                _d = (_row or {}).get("detail") or {}
                if _sid and _d.get("seat") == _sid and _d.get("mark"):
                    rank_hint = _d["mark"]
                    break
    except Exception:
        rank_hint = None
    seat_based = seat_record(seat, pid_alive=alive, transcript_age_s=age,
                             now_s=time.time(), persona=persona,
                             rank_hint=rank_hint, model=model)

bind_item = None
try:
    from bind_queue import list_items
    for it in list_items():
        if it.get("requested", {}).get("session_uuid") == uuid:
            bind_item = it
            break
except Exception:
    bind_item = None

now_ms = int(time.time() * 1000)          # 時鐘只在這裡讀,判定本身是純函數
if seat_based:
    # 席位檔這條路的上限是**黃燈**:trust 永遠是 self_reported,
    # 三項證據齊全只證明「活著、身份可讀」,不證明有第二方背書。
    light = seat_light(seat_based, now_ms)
    sig = seat_based.get("sig") or "unbound"
else:
    light = trust_light(None, bind_item, now_ms)
    sig = "unbound"
role = os.environ.get("EZ2_SESSION_ROLE")
# 艙位碼:哪一艙。10-07 the maintainer:`<IP>:[<艙位碼四碼>]`,與 dm 地址同一組碼(以前是 session 名·token 八碼,開艙時間印兩次)。
cabin = cabin_label(os.environ.get("EZ2_AIB_INSTANCE"))
# IP 與 dm 抬頭同一個來源(dm_header.local_addr:AIB_DM_ADDR > Tailscale > 區網 > 127.0.0.1)。
# probe 要跑子行程(tailscale/ipconfig),這支每拍起一個 python,所以把結果快取 10 分鐘在狀態目錄;
# 取不到就 addr 留空 ⇒ 標題只印 `[艙位碼]`,不印假 IP。
addr = ""
_addr_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR") or os.environ.get("EZ2_AIB_INSTANCE_HOME") or ""
_addr_file = os.path.join(_addr_dir, "title-addr.json") if _addr_dir else ""
try:
    if _addr_file:
        with open(_addr_file, encoding="utf-8") as fh:
            _c = json.load(fh)
        if isinstance(_c, dict) and _c.get("addr") and time.time() - float(_c.get("epoch") or 0) < 600:
            addr = str(_c["addr"])
except (OSError, ValueError):
    addr = ""
if not addr:
    try:
        from dm_header import local_addr as _local_addr   # noqa: E402
        addr = _local_addr()
        if addr and _addr_file:
            _tmp = _addr_file + ".tmp.%d" % os.getpid()
            with open(_tmp, "w", encoding="utf-8") as fh:
                json.dump({"addr": addr, "epoch": time.time()}, fh)
            os.replace(_tmp, _addr_file)
    except Exception:  # noqa: BLE001 —— 取不到 IP 不擋標題
        addr = ""
# 10-08 the maintainer:外網 IP 排在區網 IP 前面(`AIB <外網IP> | <區網IP>:[艙位碼]`)。查詢要上網 ⇒ 只讀快取(30 分鐘),
#   過期就背景更新(public_ip.spawn_refresh,不等它),這一拍照用舊值;取不到就不印外網段。AIB_PUBLIC_IP_LOOKUP=0 關。
public = ""
if cabin and cabin != "no-cabin" and _addr_dir and os.environ.get("AIB_PUBLIC_IP_LOOKUP") != "0":   # 沒有艙段就不印外網段,也不查
    try:
        import public_ip as _pub   # noqa: E402
        _pub_file = os.path.join(_addr_dir, "title-public-ip.json")
        public, _stale = _pub.read_cache(_pub_file)
        if _stale and os.environ.get("EZ2_TB_PRINT_ONLY") != "1" and os.environ.get("EZ2_ONESHOT") != "1":
            # --print(測試與除錯)只讀快取、不上網;真正在跑的節拍才背景更新。
            # 10-08:單幀模式(EZ2_ONESHOT=1,治具/截幀;pane-right-tabs.sh 畫完就走)也不起 —— 脫離的查詢行程在面板
            #   結束後才把快取寫進治具目錄,撞上 tearDown 刪目錄(推送前 test-tree-rails-ar-0902 Errno 66),而且測試真的上網
            _pub.spawn_refresh(_pub_file)
    except Exception:  # noqa: BLE001 —— 外網 IP 取不到不擋標題
        public = ""
# 這一艙是**什麼時候啟用的**(the maintainer 08-16:尾段改顯示開艙時間)。
# 來源=instance-token 檔的建立時間:它是艙身份的實體,艙在它才在。
# ★ 不用席位檔的 started_epoch —— 那是「這一**席**開的時間」,不是艙的
#   (實測差了 17 小時:席 08/16 12:41,艙 08/15 19:38)。
# ★ 佐證:tmux session_created 與它只差 1 秒,兩個獨立來源互相對得上。
since = ""
_home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
if _home:
    try:
        # E2.2 純度:時鐘/時區只在這一端(採集端)讀,注入給純函式。
        since = cabin_since(os.path.getctime(os.path.join(_home,
                                                          "instance-token")),
                            localtime=time.localtime)
    except OSError:
        since = ""
# 已執行多久(the maintainer 08-16:尾段再加一格)。同一個來源,不另取時間。
up = ""
if _home:
    try:
        up = uptime_str(os.path.getctime(os.path.join(_home, "instance-token")),
                        time.time())
    except OSError:
        up = ""
# ---- 版號與更新偵測(the maintainer 08-21)-------------------------------------------
# ★★ 硬約束:**標題每秒重跑一次**。所以這一段不准碰網路、不准起 git 子行程
#   ——十六個 instance 每秒各跑一次 `git ls-remote` 是災難。兩個訊號都只做
#   **純檔案讀取**:
#     ↑  本地 main 的 ref 內容 ≠ origin/main 的 ref 內容(有東西還沒合)
#     ⟳  本地 main 的 ref **mtime 晚於這一席開席時間**(這席跑的是舊碼)
#
# ★ `↑` 的誠實邊界:它反映的是**上一次 fetch 之後**的狀態。沒有人 fetch,
#   它就不會亮——這不是 bug 是這個做法的代價,換來的是每秒零網路。要讓它
#   永遠準,得有東西定期 fetch(那是另一條線,不在標題裡做)。
#
# ★ `⟳` 用**席位**的 started_epoch 不用艙的:問題是「這一席載入的碼有沒有
#   過期」,而席與艙可以差很多(本檔上面那段實測差過 17 小時)。
#
# ★★ 三態不是兩態:讀不到判準來源時傳 None(畫 `?`),**不是傳 False**。
#   ref 可能被 pack 進 packed-refs 而讓 refs/heads/main 這個檔不存在;
#   那時把「讀不到」當成「沒有新版本」,這個指示器的失效值就恰好等於它表示
#   安全的那個值——壞掉時長得跟一切正常一模一樣。
version_seg = ""
_repo = os.environ.get("EZ2_AIB_ROOT")
if _repo:
    _ver = None
    try:
        with open(os.path.join(_repo, "VERSION"), encoding="utf-8") as fh:
            _ver = fh.read().strip()
    except OSError:
        _ver = None
    if _ver:
        _behind = None
        _changed = None
        _local = os.path.join(_repo, ".git", "refs", "heads", "main")
        _remote = os.path.join(_repo, ".git", "refs", "remotes", "origin",
                               "main")
        try:
            with open(_local, encoding="utf-8") as fh:
                _lsha = fh.read().strip()
            with open(_remote, encoding="utf-8") as fh:
                _rsha = fh.read().strip()
            if _lsha and _rsha:
                _behind = (_lsha != _rsha)
        except OSError:
            _behind = None                    # packed-refs / 非 git 樹:不明
        try:
            _moved = os.path.getmtime(_local)
            _born = float((seat or {}).get("started_epoch") or 0)
            if _born > 0:
                _changed = (_moved > _born)
        except (OSError, TypeError, ValueError):
            _changed = None
        version_seg = version_segment(_ver, behind=_behind,
                                      code_changed=_changed)
        # ★ 狀態另外落一個小檔給狀態列用。**標題與狀態列是兩個顯示層**:
        #   標題只吃純文字(終端機 OSC 標題通道沒有樣式),狀態列吃得下顏色。
        #   判準只算一次,兩層各自決定畫成形狀還是顏色。
        # ★ 為什麼用檔案而不是多印一行:`--print` 的契約是**零接觸 tmux**
        #   (test_print_mode_never_touches_tmux),而標題字串本身是 stdout
        #   的唯一內容(黃金幀比對逐字)。多印一行會同時弄壞這兩件事。
        try:
            _sd = os.environ.get("EZ2_COCKPIT_STATE_DIR")
            if _sd:
                _tmp = os.path.join(_sd, ".title-version.tmp.%d" % os.getpid())
                with open(_tmp, "w", encoding="utf-8") as fh:
                    json.dump({"ver": short_version(_ver),
                               "state": version_state(_behind, _changed)}, fh)
                os.replace(_tmp, os.path.join(_sd, "title-version.json"))
        except OSError:
            pass

print(title_string(sig, light, int(os.environ.get("EZ2_TB_BEAT") or 0), role,
                   color=os.environ.get("EZ2_TB_COLOR") == "1", cabin=cabin,
                   since=since, uptime=up, version=version_seg, addr=addr, public=public or None))
PY
}

case "$MODE" in
  install)
    # 讓 tmux 的視窗標題吃我們的字串(#{@aib_title} 由 --once 寫入)。
    # ★ 與 @aib_title 同層:寫 session 層,否則 cockpit-full.sh 的 session 層
    #   會蓋掉這裡設的全域值(見 write_title 的註解)。
    if [ -n "${EZ2_TMUX_SESSION:-}" ]; then
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" set-titles on
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" set-titles-string '#{@aib_title}'
      # ---- 狀態列:版號 + **有顏色的**更新狀態 -----------------------------
      # ★ 這是顏色唯一能成立的地方。視窗標題走的是終端機的 OSC 標題通道,
      #   那個通道只送一串純文字——實測 macOS Terminal 讀回來就是字串,
      #   沒有樣式可言(title_beat.py:49 早就記著這件事,當初改用形狀
      #   ○◐● 而不是顏色就是為了它)。
      # ★ 樣式寫在**格式字串裡**(字面),不是從變數展開進來:後者要靠
      #   tmux 對展開值再處理一次樣式,那條路我沒有辦法在這裡實測(顏色
      #   只有人眼看得到),所以選確定會成立的寫法。
      # ★ status-left-length 一起設:全域預設是 **10**,而這串遠超過——
      #   在此之前 `[Ez2 AIB v0.42.1-alpha.1]` 實際只顯示到 `[Ez2 AIB v`,
      #   版號一直是被截掉的。**那是既有缺陷,不是本次改動造成的。**
      # 10-10(the maintainer 眼驗令):這一行開頭先放給使用者的提示(四語字典 aib.cockpit.footer_hint,與 cockpit-full.sh 同一句,
      #   前補 2 格與 Claude 輸入框下方那三行對齊),帶顏色的版號更新狀態接在後面——那是更新提示(↑ ⟳ ?)的唯一彩色出口,不拿掉。
      #   長度 40 → 80 裝得下兩段;字典讀不到(!!key!! 或空)退回中文字面。
      _tb_hint="$("$EZ2_PY3" "$HERE/../i18n_lib.py" aib.cockpit.footer_hint 2>/dev/null || true)"
      case "$_tb_hint" in ''|'!!'*) _tb_hint='若有任何想法,請訪問:aib.ez2.app' ;; esac
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-left-length 80
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-left \
        "  $_tb_hint  "'#{?#{==:#{@aib_ver_state},ok},#[fg=colour71][AIB v#{@aib_ver}]#[default] ,#{?#{==:#{@aib_ver_state},behind},#[fg=colour178][AIB v#{@aib_ver} ↑]#[default] ,#{?#{==:#{@aib_ver_state},stale},#[fg=colour208][AIB v#{@aib_ver} ⟳]#[default] ,#[fg=colour167][AIB v#{@aib_ver} ?]#[default] }}}'
      # 狀態列右側也掛一份:標題在某些終端機看不到(tmux 內、或標題被 shell 蓋掉),
      # 狀態列是艙內永遠看得見的那一行。兩處同源,都讀 @aib_title。
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-right '#{@aib_title}  '
      run env -u TMUX tmux set-option -t "$EZ2_TMUX_SESSION" status-right-length 80
    else
      echo "title-beat: EZ2_TMUX_SESSION 未設,--install 無對象" >&2
    fi
    ;;
  once)
    TITLE="$(sample 0)"
    if [ "$PRINT_ONLY" = "1" ]; then
      printf '%s\n' "$TITLE"
    else
      write_title "$TITLE"
      printf '%s\n' "$TITLE"
    fi
    ;;
  loop)
    # ── BL(2026-09-04):常駐迴圈也要自癒 ─────────────────────────────────
    # 根因與面板三支同一個(見 pane-tick.sh 的 BF 段):bash 的 while 在啟動時就把
    # 整份解析完,檔案後來怎麼改都不影響已經在跑的那一份 —— 落地的修法永遠到不了
    # 活著的那個行程,而畫面完全正常。
    # ★ 這裡**不能**直接呼叫 pane_reexec_if_changed:刀三(k3)要砍的是本支自己的
    #   指紋比對錨;且歷史上 title-beat 借取法不借判斷(見 test-longrun-reexec-bl
    #   RestoreKnife 說明)。模式住在 argv(`--loop`),exec 必須帶 "$@"。
    # ★ 2026-09-20:指紋檢查必須在 sample/write **之前**。sample 在 B8 高壓下
    #   可能卡住數十秒;若先 sample 再檢查,測例等 30 秒會誤判「改檔不重跑」。
    # 失效側與 BF 同:指紋量不到一律當沒變(舊碼繼續跑是貴,exec 進不存在的檔是死)。
    PANE_SELF_SCRIPT="$HERE/title-beat.sh"
    . "$COCKPIT/pane-tick.sh"
    _bl_fp=""
    _bl_next=0
    while true; do
      if [ "$SECONDS" -ge "$_bl_next" ]; then
        _bl_next=$(( SECONDS + PANE_REEXEC_INTERVAL ))
        _bl_now="$(pane_script_fingerprint)"
        if [ -n "$_bl_now" ]; then
          if [ -z "$_bl_fp" ]; then
            _bl_fp="$_bl_now"
          elif [ "$_bl_now" != "$_bl_fp" ] && [ -f "$PANE_SELF_SCRIPT" ]; then
            pane_reexec_log "$PANE_SELF_SCRIPT" "$_bl_fp" "$_bl_now"
            printf 'title-beat: 腳本已更新,重跑自己(%s)\n' "$PANE_SELF_SCRIPT" >&2
            exec bash "$PANE_SELF_SCRIPT" "$@"
          fi
        fi
      fi
      pane_wait_if_composing && continue    # 終端機組字中:不改標題(改標題也是送資料給 Terminal;10-10,見 pane-tick.sh)
      TITLE="$(sample 0)"
      write_title "$TITLE"
      sleep "$INTERVAL"
    done
    ;;
esac
