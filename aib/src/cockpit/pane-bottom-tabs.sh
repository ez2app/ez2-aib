#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 底部描述區 tab 分頁:Alt+0 循環切換(只切下方,主畫面不動)
# 遷自 ~/ez2-cockpit-demo/acb-bottom-tabs.sh(E2.1)
# 修正:狀態檔進 state dir;舊大綱硬路徑改 $EZ2_OUTLINE;解析改用 cockpit_lib
. "$(dirname "$0")/cockpit-env.sh"
TABF="$EZ2_COCKPIT_STATE_DIR/bottom-tab"

current_theme_file() {
  local observed=""
  if [ -n "${TMUX_PANE:-}" ]; then
    observed="$(tmux show-environment -t "$EZ2_TMUX_SESSION" \
      EZ2_AIB_THEME_FILE 2>/dev/null || true)"
    case "$observed" in
      EZ2_AIB_THEME_FILE=?*) printf '%s\n' "${observed#EZ2_AIB_THEME_FILE=}"; return ;;
    esac
  fi
  printf '%s\n' "$EZ2_AIB_THEME_FILE"
}

# 標記本面板供滑鼠綁定辨識(E2.13);沒標記的面板滑鼠行為完全不變
[ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane tabs 2>/dev/null

# 分頁列:畫面與可點區域同源產生,spans 落盤供點擊判定(src/cockpit/mouse)
tab_bar() {  # tab_bar <active>
  EZ2_TAB_ACTIVE="$1" EZ2_TAB_NOTIFY_COUNTS="${NOTIFY_COUNTS:-}" "$EZ2_PY3" - <<'PY'
import json, os, sys
cockpit = os.environ["EZ2_COCKPIT_DIR"]
sys.path.insert(0, os.path.join(cockpit, "render"))
sys.path.insert(0, cockpit)
from render_lib import render_tab_bar  # noqa: E402
from i18n_lib import Translator  # noqa: E402

tr = Translator()
mod = os.environ.get("EZ2_MODIFIER_LABEL") or (
    "Option" if sys.platform == "darwin" else "Alt")
# the maintainer 2026-08-17 直令:拿掉「艦隊」分頁,對話之後加「效能優化」
# (idle_reaper,機況清理席交付)。仍四頁,循環狀態機不動。
labels = [tr.t("aib.tabs.command"), tr.t("aib.tabs.progress"),
          tr.t("aib.tabs.chat"), tr.t("aib.tabs.perf")]
try:
    raw_counts = json.loads(os.environ.get("EZ2_TAB_NOTIFY_COUNTS") or "{}")
except (TypeError, ValueError):
    raw_counts = {}
counts = {}
for level in ("red", "yellow", "green", "blue"):
    value = raw_counts.get(level, 0) if isinstance(raw_counts, dict) else 0
    counts[level] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
if any(counts.values()):
    labels[3] += " " + tr.t("aib.notify.badge", counts)
_cabin = (os.environ.get("EZ2_AIB_INSTANCE") or "").strip()
_width = os.environ.get("EZ2_PANE_W")
try:
    _width = int(_width) if _width else None
except (TypeError, ValueError):
    _width = None
text, spans = render_tab_bar(labels, int(os.environ["EZ2_TAB_ACTIVE"]),
                            tr.t("aib.tabs.hint", {"mod": mod}),
                            cabin_id=_cabin or None, width=_width)
state = os.environ["EZ2_COCKPIT_STATE_DIR"]
tmp = os.path.join(state, "tab-spans.json.tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(spans, fh, ensure_ascii=False)
os.replace(tmp, os.path.join(state, "tab-spans.json"))
print(text)
PY
}

notify_counts() {
  local _human_unread
  # 舊測試注入仍可確定性控制；正式路徑一律走共享 classifier CLI。
  if [ -n "${ACB_TEST_NOTIFY_COUNTS:-}" ]; then
    printf '%s\n' "$ACB_TEST_NOTIFY_COUNTS"
    return
  fi
  if [ "${ACB_TEST_HUMAN_UNREAD+x}" = "x" ]; then
    case "$ACB_TEST_HUMAN_UNREAD" in
      ''|*[!0-9]*) _human_unread=0 ;;
      *) _human_unread="$ACB_TEST_HUMAN_UNREAD" ;;
    esac
    printf '{"red":0,"yellow":%s,"green":0,"blue":0}\n' "$_human_unread"
    return
  fi
  # ★★ 2026-08-21:原本呼叫 notify_policy.py count-inbox "$EZ2_ACB_HOME/inbox/pm"。
  #   那個目錄的 *.md 現存 0 檔(ACB 已判廢止),所以這裡**恆回全 0**——
  #   而全 0 在畫面上等於「沒有未讀」,是一個**宣稱**,不是量測結果。
  #   AIB 側目前沒有持久收件匣(大綱 EH.10),所以沒有可換的源。
  #   在 EH.10 落地前,這裡誠實地不查、直接回 0,並且**不再假裝它問過誰**。
  printf '{"red":0,"yellow":0,"green":0,"blue":0}\n'
}

render_frame() {
  local t="$1"
  NOTIFY_COUNTS="$(notify_counts)"
  if [ "$t" = "0" ]; then
    tab_bar 0
    "$EZ2_PY3" - <<'PY'
import glob, json, os, sys
from cockpit_lib import parse_outline_file
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
from i18n_lib import Translator  # noqa: E402
from machine_status_row import DEFAULT_TTL_S  # noqa: E402
from cooldown import notice_is_fresh  # noqa: E402

tr = Translator()

H = os.path.expanduser('~')
runtime = os.environ.get('EZ2_PERSONA_RUNTIME', H + '/.ez2-persona-runtime')
# 等你的事
drafts = 0
try:
    drafts = sum(1 for it in parse_outline_file(os.environ['EZ2_OUTLINE'])
                 if it['approval'] == 'draft')
except Exception:
    pass
staged = 0
try:
    staged = json.load(open(runtime + '/claude-conv-distill-state.json')).get('staged_total', 0)
except Exception:
    pass
# ★ 收件匣改印 '—' 不印 0:AIB 沒有持久收件匣(EH.10),我們**量不到**這個數。
#   0 讀起來是「沒有未讀」,'—' 讀起來是「這裡沒有這個量」——差別在於前者
#   是一個會被相信的宣稱。這正是今天反覆出現的那一族:失效值與安全值同形。
print(f' 等你的事: 候拍板 {drafts} | 收件匣 — | 人格候審 {staged}')
# ── 降溫通知(the maintainer 2026-09-04 令:過熱時自動找解法,並在最下一行說正在解決)──
# ★ 只讀 cooldown.py 落的檔,不在這裡判斷也不取值:判斷只有一份,在 cooldown.py。
# ★ 檔不存在 = 這個機制沒在跑,要**說出來**,不能靜默留白 ——
#   空白與「一切正常」在畫面上逐字相同,而前者是壞掉。
try:
    _cd_state = os.environ.get('EZ2_COCKPIT_STATE_DIR') or (H + '/.ez2-aib/ui-state')
    with open(_cd_state + '/cooldown-notice.json', encoding='utf-8') as _fh:
        _cd = json.load(_fh)
except Exception:
    _cd = None
if _cd is None:
    print(' 降溫: 量不到(通知檔不在)')
elif (_cd.get('load_level') not in ('ok', 'warn', 'hot', 'critical') or
      _cd.get('thermal_level') not in ('ok', 'warn', 'hot', 'critical', 'unknown') or
      not notice_is_fresh(_cd, DEFAULT_TTL_S)):
    # Old notices only carried a combined level.  It can be high because of
    # load, so it cannot truthfully be rendered as a temperature verdict.
    print(' 降溫: ' + tr.t('aib.machine.pending_update'))
else:
    _thermal = _cd['thermal_level']
    _thermal_key = {'ok': 'normal', 'warn': 'warn', 'hot': 'hot',
                    'critical': 'critical', 'unknown': 'unknown'}[_thermal]
    _status = tr.t('aib.machine.thermal.' + _thermal_key)
    if _cd['load_level'] in ('hot', 'critical'):
        _status += ' · ' + tr.t('aib.machine.load.high')
    print(f" 降溫: {_status}")
    for _did in (_cd.get('did') or []):
        print(f"   {tr.t('aib.machine.action.done')}: {_did}")
    for _n in (_cd.get('need') or []):
        print(f"   {tr.t('aib.machine.action.need')}: {_n}")
    for _fyi in (_cd.get('fyi') or []):
        print(f"   {tr.t('aib.machine.action.info')}: {_fyi}")
    if not (_cd.get('did') or _cd.get('need') or _cd.get('fyi')):
        print('   ' + tr.t('aib.machine.action.none'))
print()
# ★★ 2026-08-21:原本讀 $EZ2_ACB_HOME/logs/fleet-sync.jsonl 的最後三筆。
#   實測那三筆的 ts 是 1786119338 前後 = **08-07,十三天前**,而標題寫「最近事件」。
#   ★ 時間其實**有印在畫面上**,但印的是原始 epoch 整數 —— 沒有人會把
#     `1786119338` 讀成日期,它看起來像一個 id。**有標示不等於看得懂。**
#   AIB 側沒有等價來源(E1.2 收集器尚未上線),所以移除讀取、誠實留空。
# ★ 依 DESIGN-SPEC-source-state-vocabulary(a seat)的五態語彙:
#   這一格是 **retired**(源已廢止)不是 empty(源在但沒東西)——
#   兩者的處置相反:empty 要等,retired 永遠不會自己好。
#   ★ retired 是**宣告不是偵測**:ACB 廢止是我們自己的決定,寫死在這裡,
#     不靠「找不到目錄」去推——那會把「使用者磁碟壞了」也講成「已廢止」。
print(' 最近事件(fleet-sync):')
print('  ' + tr.t('aib.source.retired'))
print('  ' + tr.t('aib.source.which', {'name': 'ACB fleet-sync'}))
print('  ' + tr.t('aib.source.pending', {'name': 'E1.2'}))
PY
  elif [ "$t" = "2" ]; then
    tab_bar 2
    "$EZ2_PY3" - <<'PY'
import glob, os, re, sys
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
from i18n_lib import Translator  # noqa: E402

tr = Translator()
# ★★ 2026-08-21:來源 $EZ2_ACB_HOME/inbox/pm 的 *.md 現存 0 檔,ACB 已判廢止。
#   舊碼的失效訊息是「(近 60 則無 [人類] 標籤訊息)」——**它宣稱掃過 60 則,
#   實際上掃過 0 則**。那句話比空白更糟:它讓人以為查過了。
#   五態語彙:這是 retired,不是 empty;而**接手的源還不存在**(EH.10),
#   所以 pending 要照實寫「尚無」,不要編一個看起來快好了的名字。
msgs = []
for p in []:
    fm = {}
    try:
        for ln in open(p, encoding='utf-8'):
            ln = ln.rstrip()
            if ln == '---' and fm:
                break
            m = re.match(r'^(\w+):\s*(.*)$', ln)
            if m:
                fm[m.group(1)] = m.group(2)
    except Exception:
        continue
    msgs.append(fm)
human = [m for m in msgs if '[人類]' in m.get('subject', '')]
if human:
    print(' 真人往來(最近):')
    for m in human[:6]:
        ts = m.get('timestamp', '')[5:16].replace('T', ' ')
        st = '未讀' if m.get('status') == 'unread' else '  '
        print(f"  {ts} {m.get('from','?'):<6.6}{st} {m.get('subject','')[:70]}")
else:
    print(' ' + tr.t('aib.source.retired'))
    print(' ' + tr.t('aib.source.which', {'name': 'ACB inbox/pm'}))
    print(' ' + tr.t('aib.source.pending', {'name': 'EH.10'}))
    # ★ 原本這裡還有一個「其他最新訊息:」標題 + 迴圈。來源退場後迴圈恆空,
    #   標題就變成一個宣稱有內容的空殼 —— 與上面那句「近 60 則」同一個病,
    #   只是更小。標題跟著內容一起走,不留孤兒。
print()
print(' 註:出站訊息不落本機收件匣,雙向完整紀錄候事件帳本(E1.4 解此限制)')
PY
  elif [ "$t" = "1" ]; then
    tab_bar 1
    bash "$EZ2_COCKPIT_DIR/outline-progress.sh"
    "$EZ2_PY3" - <<'PY'
import glob, json, os
from cockpit_lib import parse_outline_file

items = []
try:
    items = parse_outline_file(os.environ['EZ2_OUTLINE'])
except Exception:
    pass
# 各軸 done 走真相源(acceptance+git),分母=大綱該軸葉子——不數板卡(缺卡+
# 狀態值不認會低報,同 outline-progress.sh;E3.2 診斷)
import subprocess, re
repo = os.path.dirname(os.path.dirname(os.environ['EZ2_OUTLINE']))
done_nodes = set(os.path.basename(p)[:-5]
                 for p in glob.glob(os.path.join(repo, 'acceptance', '*.json')))
try:
    _log = subprocess.run(['git', '-C', repo, 'log', '--format=%s', '-400'],
                          capture_output=True, text=True).stdout
    done_nodes |= set(re.findall(
        r'^(?:feat|fix|chore|refactor)\([^)]*\): ([EA-Z]+\d+\.\d+)', _log, re.M))
except Exception:
    pass
leaf_ids = {x['id'] for x in items if x['leaf']}
out = []
for it in items:
    if it['depth'] != 0:
        continue
    oid, ap = it['id'], it['approval']
    kids = [k for k in leaf_ids if k == oid or k.startswith(oid + '.')]
    done = sum(1 for k in kids if k in done_nodes)
    if ap == 'draft':
        out.append(f'{oid}[草]')
    elif kids:
        out.append(f'{oid} {done}/{len(kids)}')
row = ' | '.join(out)
print(' ' + row[:130])
PY
  else
    tab_bar 3
    # 效能優化頁(the maintainer 08-17):idle_reaper 盤點快照;10 秒快取——這頁的
    # 存在理由正是輪詢負載,不能自己每秒掃全機行程樹變成新病灶。
    PERF_CACHE="$EZ2_COCKPIT_STATE_DIR/idle-scan-cache"
    NOW_S=$(date +%s)
    CACHE_M=$(aib_mtime "$PERF_CACHE" 0)
    if [ $((NOW_S - CACHE_M)) -ge 10 ]; then
      # the maintainer 08-17 直令:只要「名稱 | 閒置 | [X]」三欄+頂部全關建議,
      # 判決/艙齡/自動/細節不上畫面(格式器=perf_tab_fmt.py,資料層不動)
      # --spans:點擊座標檔與畫面同一次渲染落盤(E 面板第二片)——
      # cache 與 spans 永遠出自同一幀,不會畫面舊座標新。
      "$EZ2_PY3" "$EZ2_COCKPIT_DIR/idle_reaper.py" scan --json 2>/dev/null \
        | "$EZ2_PY3" "$EZ2_COCKPIT_DIR/perf_tab_fmt.py" \
            --spans "$EZ2_COCKPIT_STATE_DIR/perf-spans.json" \
            >"$PERF_CACHE.tmp" 2>/dev/null \
        && mv "$PERF_CACHE.tmp" "$PERF_CACHE"
    fi
    # 裁到面板寬高:scan 行寬會折行、行數會溢出,把分頁列頂出畫面
    # (08-17 the maintainer 實見「艦隊沒消失、效能沒出現」的第二半:出現了但被擠走)
    PW=$(tmux display -p -t "${TMUX_PANE:-}" '#{pane_width}' 2>/dev/null); PW=${PW:-140}
    PH=$(tmux display -p -t "${TMUX_PANE:-}" '#{pane_height}' 2>/dev/null); PH=${PH:-14}
    # 可見行數落盤:hit_test 拿它擋「檔案裡有、畫面被 head 裁掉」的隱形
    # 點擊區(與 head 同一個算式=同源;printf 整檔單寫,無半寫窗口)。
    VIS=$((PH > 4 ? PH - 3 : 1))
    printf '%s\n' "$VIS" > "$EZ2_COCKPIT_STATE_DIR/perf-visible-lines" 2>/dev/null || true
    if [ -f "$PERF_CACHE" ]; then
      head -$VIS "$PERF_CACHE" | cut -c1-$((PW > 2 ? PW - 1 : 80))
    else
      printf ' (盤點快取尚未生成)
'
    fi
  fi
}

# Native Terminal selection is erased by any cursor-home/full-pane repaint.
# Poll freely, but only write to the terminal when the rendered content, tab,
# or geometry actually changes.  This mirrors the left/right tab containers.
FIRST_PAINT=1
LAST_FRAME=""
LAST_GEOMETRY=""
HUB_WIN=""
# hub 的當前 active window —— 標題「跟著你正在看的那一席」的唯一來源。
# 取不到就回空:切窗偵測自然停用,底欄與艦長列心跳照跑(rc0 紅線)。
hub_active_window() {
  local sock="${EZ2_AIHUB_SOCKET:-}"
  [ -n "$sock" ] || return 0
  env -u TMUX tmux -L "$sock" display-message -p '#{window_id}' 2>/dev/null || true
}
REFRESH_TICKS="${EZ2_BOTTOM_REFRESH_TICKS:-50}"
case "$REFRESH_TICKS" in
  ''|*[!0-9]*|0) REFRESH_TICKS=50 ;;
esac
# 切窗檢查每幾個 tick 問一次 hub(跨 server 只有行程做得到,見下方註解);
# 原本每 tick(0.1s)一次 = 每秒 10 次 exec,§59 改為 3 tick(0.3s)。
HUBWIN_TICKS="${EZ2_BOTTOM_HUBWIN_TICKS:-3}"
case "$HUBWIN_TICKS" in
  ''|*[!0-9]*|0) HUBWIN_TICKS=3 ;;
esac
PANE_SELF_SCRIPT="$EZ2_COCKPIT_DIR/pane-bottom-tabs.sh"   # BF:碼換了自己重跑
. "$EZ2_COCKPIT_DIR/pane-tick.sh"
pane_tick_init
while true; do
  pane_reexec_if_changed                # BF:腳本換了就 exec 自己(每 ~30 秒問一次)
  pane_wait_if_detached && continue     # 沒人看:不渲染,維持運作(§59)
  PANE_GEOM_DIRTY=0
  pane_read_file t "$TABF"
  case "$t" in 1|2|3) ;; *) t=0 ;; esac
  if [ -n "${TMUX_PANE:-}" ]; then
    PANE_GEOMETRY="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-140}" "${LINES:-12}")"
  else
    PANE_GEOMETRY="${COLUMNS:-140} ${LINES:-12}"
  fi
  # Gold is intentionally brighter than the three upper regions. Rebase every
  # ANSI reset onto the contrast selected from theme data so DIM/brand colours
  # cannot leave later text as unreadable grey on gold.
  ACTIVE_THEME_FILE="$(current_theme_file)"
  FRAME="$(render_frame "$t" | "$EZ2_PY3" "$EZ2_COCKPIT_DIR/theme.py" \
    --file "$ACTIVE_THEME_FILE" panel-filter bottom)"
  if [ "$FIRST_PAINT" = "1" ] || [ "$FRAME" != "$LAST_FRAME" ] || \
     [ "$PANE_GEOMETRY" != "$LAST_GEOMETRY" ]; then
    printf '\033[H%s\033[J' "$FRAME"
    LAST_FRAME="$FRAME"
    LAST_GEOMETRY="$PANE_GEOMETRY"
    FIRST_PAINT=0
  fi
  # ★★ 艦長列心跳(2026-08-16)。艦長列=內層 aihub 的 status bar,內容來自
  #   窗選項 @aib_captain*,而那些選項**只在 `captains-ctl.sh --sync` 跑的
  #   時候才會變**。全庫原本只有兩個呼叫點(`aib` 指令執行時、mouse-ctl 某
  #   動作),**沒有任何週期性重跑**——於是讓位/指派/退顧問之後,bar 會停在
  #   舊狀態直到有人剛好跑了 aib。the maintainer 08-16 螢幕實見:讓位好幾小時,bar 上
  #   還掛著舊任第一艦長。
  #
  #   ★ 這個病的形狀:**資料層一直是對的**(席位檔的 advisor/captain_assigned
  #     都寫對了),判定邏輯也是對的——錯的是**沒有人去問**。那種缺陷不會讓
  #     任何測試變紅,因為每一個元件單獨看都正確。
  #
  #   ★★ 為什麼掛在**這一格**:因為這一格是 `cockpit-full.sh` 真的會啟動的。
  #     我第一版把它掛在 `pane-bottom-fleet.sh`,**那個檔全庫沒有任何啟動點**
  #     (只在 README 的搬遷對照表被提到一次)——修法本身犯了它正在修的那個
  #     病:東西寫好了、測試全綠、而它不在任何人會走的路徑上。
  #     `tests/test-cockpit-heartbeat.py` 現在**從 cockpit-full.sh 實際啟動的
  #     清單推導**要驗哪個檔,就是為了讓這個錯不能再犯一次。
  #
  #   失敗一律吞掉:艦長列是輔助資訊,不得擋住底欄渲染(rc0 no-op 紅線)。
  env -u TMUX bash "${AIB_CAPTAINS_CTL:-$EZ2_COCKPIT_DIR/nav/captains-ctl.sh}" \
    --sync >/dev/null 2>&1 || true
  # ★★ 視窗標題心跳(2026-08-17,the maintainer 直令「一進 aib 就該運作」)。
  #   掛在這一格的理由與上面艦長列同一條:**這一格是 cockpit-full.sh 真的會
  #   啟動的**,而 tests/test-cockpit-heartbeat.py 從 `pane_command` 清單機械
  #   推導要驗哪一格 —— 搭上去等於免費繼承「不准掛進沒有啟動點的腳本」那道閘。
  #
  #   為什麼不是純 tmux format(那樣連迴圈都不需要,是更好的解):實測
  #   **format 讀不到別台 server**,而「當下正在看哪一席」這個事實在 **hub**
  #   server(aihub-*)上,標題卻要寫到**外層** session。跨 server 只有行程做得到。
  #   ★ 也不用 hook window-changed:HANDOFF 有殘留 hook 造成 ensure 連環 rc=1 的前科。
  #   改用下面 tick 迴圈的提前中斷,切窗在 0.1s 內就反映,比五秒心跳快也比 hook 安全。
  #
  #   失敗一律吞掉:標題是輔助資訊,不得擋住底欄渲染(rc0 no-op 紅線,同上)。
  env -u TMUX bash "${AIB_TITLE_BEAT:-$EZ2_COCKPIT_DIR/title/title-beat.sh}" \
    --once >/dev/null 2>&1 || true
  HUB_WIN="$(hub_active_window)"
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  for ((tick=0; tick<REFRESH_TICKS; tick++)); do
    pane_tick_sleep 0.1
    [ "$PANE_GEOM_DIRTY" = "1" ] && break          # resize 事件(WINCH),不再每 tick 問 tmux(§59)
    pane_read_file next_t "$TABF"
    case "$next_t" in 1|2|3) ;; *) next_t=0 ;; esac
    [ "$next_t" = "$t" ] || break
    # ★ 切窗提前中斷:標題必須在「切到別席」這個事件上更新,不能只靠五秒心跳
    #   —— 切過去到標題跟上之間的那幾秒,正好是最容易誤導人的視窗(標題說 A,
    #   畫面是 B)。這裡跟既有的 tab / geometry 提前中斷同一招,不另造機制。
    if [ -n "$HUB_WIN" ] && [ $(( (tick + 1) % HUBWIN_TICKS )) -eq 0 ]; then
      CURRENT_HUB_WIN="$(hub_active_window)"
      [ "$CURRENT_HUB_WIN" = "$HUB_WIN" ] || break
    fi
  done
done
