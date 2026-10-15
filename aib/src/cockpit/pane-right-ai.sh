#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 右欄:主AI 即時真實艦隊看板(E2.19 續,2026-08-13 the maintainer 直令)
# 遷自 ~/ez2-cockpit-demo/acb-right-ai.sh(E2.1;路徑全改 env)
# E2.13:採集與畫面分離 —— 這裡只採集,畫面交 render_lib.render_ai_list_with_map,
#        同時把「哪一行是哪個 AI」落盤供滑鼠點擊判定(畫面與落點同源)。
#
# E2.19 資料源規格更正(入冊):原規格要求 registry_verified,該狀態隨 ACB
# 拆除永久消失——舊版此處讀 $EZ2_ACB_HOME/sessions registry(6h 心跳過濾把
# 真席位全濾光)並呼叫已死的 acb verifier,右欄實質恆空。繼任=ED.11 直起的
# 席位檔 $seats/w<id>.json(對齊 captains-ctl 先例):
#   - 活性=hub tmux 活窗判定,不靠心跳欄(status 欄不承重教訓);
#   - trust 恆 self_reported,顯示層 ~ 前綴誠實標記,verified 字樣在 EH grant
#     模型落地前不可能出現(紅線);
#   - 窗集合與 rowmap 跳轉目標取自同一次 list-windows(畫面與落點同源)。
. "$(dirname "$0")/cockpit-env.sh"
# 畫面字走四語字典(語系盤查第 14 條,10-14):_rai <鍵> [名=值 …];每次照 i18n_lib.resolve_locale() 取。
#   python 起不來 ⇒ 印鍵名、rc 照 0(set -e 底下也不會中斷)。
_rai() {
  local key="$1"; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  "${EZ2_PY3:-python3}" "$EZ2_COCKPIT_DIR/i18n_lib.py" ${args[@]+"${args[@]}"} "$key" 2>/dev/null || printf '%s\n' "$key"
}

set -o pipefail   # 三輪審中3:輸出管線任一段死都算 render 失敗,不發布 rowmap

# ★ 讀端也由 HUB 導出(第七個呼叫點)。寫端 aib 改成 hub 導出而讀端留 env-only,
#   兩邊就會在同一台機器上解到**不同艙的登記簿**——右欄會畫別艙的人,而且不會報錯。
#   tests/test-abs-path-guard.py 的 WriterReaderExpressionTest 釘的就是這件事。
_EZ2_SEATS_ROOT="${EZ2_AIB_HOME:-$HOME/.ez2-aib}"
case "${EZ2_AIHUB_SOCKET:-}" in aihub-*) _EZ2_SEATS_INST="${EZ2_AIHUB_SOCKET#aihub-}" ;; *) _EZ2_SEATS_INST="" ;; esac
case "$_EZ2_SEATS_INST" in *[!0-9a-zA-Z-]*) _EZ2_SEATS_INST="" ;; esac
if [ -n "$_EZ2_SEATS_INST" ]; then
  EZ2_SEATS_DIR="$_EZ2_SEATS_ROOT/instances/$_EZ2_SEATS_INST/seats"
else
  EZ2_SEATS_DIR="${EZ2_AIB_INSTANCE_HOME:-$_EZ2_SEATS_ROOT}/seats"
fi
export EZ2_SEATS_DIR

# 二輪審中6 原意:啟動先清舊 rowmap 再掛 marker——上一世代的座標檔在重啟窗口內
# 仍可點,清掉之前 marker 不得先活。**原意保留,載體改掉**:
# ★★ H3(判準 docs/criteria/CRITERIA-H3-rowmap-publish-never-delete-4e623-0831.md):
#   用「檔案不存在」去表達「現在不可點」,代價是**每次重啟造一個空窗**。
#   實測(5ms 取樣):刪除 → 約 730ms → 新 inode 重發,週期約 5.6 秒;
#   艙 4901 有 19.69% 的時間那個檔不在,最長空窗 945ms。讀端(aib-hdr)看到的是
#   `mark_src=none` / `row_why=rowmap_missing` —— 一個**假的缺值**,而且
#   它分不出「面板沒在跑」與「面板正在重啟」。
#   ⇒ 不刪。「不可點」由 marker 承擔(它本來就是那件事的載體):**啟動先撤下 marker**,
#     由發布成功那一步重掛(見下方發布時序)。舊 rowmap 留在原地,發布時被 `mv -f` 原子覆蓋;
#     它在這段期間是「舊但在」——讀端有 ROWMAP_FRESH_SEC 判齡,**可歸因**;
#     「檔不見」不可歸因。
if [ "${EZ2_RIGHT_VIEW:-}" = "usage" ]; then
  # the maintainer 09-22: 用量頁重繪期間勿撤 marker——wham／多帳探測可卡住數十秒，清空會整頁點不到／選不到
  [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
else
  [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane "" 2>/dev/null || true
fi

# 三輪審高3:kill/崩潰/關 pane 不得殘留可點座標——EXIT 統一清 rowmap+撤
# marker;訊號轉標準退出碼讓 EXIT trap 必跑。ONESHOT(測試/探針)不掛:
# 測試要在行程結束後驗 rowmap 產物。
# ★ E2.19 codex 審「高-1」(2026-08-22):rowmap 沒有擁有者——舊、新面板重疊(重啟窗口內)時,
#   舊程序退出的 EXIT trap 會把**新程序剛發布的** ai-rowmap.json 刪掉;新 pane 的 marker 還掛著,
#   下一幀之前點擊=空表 passthrough,或更糟:舊程序最後一幀蓋掉新表後才退出。
#   修法:發布時同目錄寫 `ai-rowmap.owner`(內容=本程序 PID,與 rowmap 同一次發布),
#   清理只在 owner==$$ 時刪 rowmap;不是自己的就只撤自己 pane 的 marker。啟動時的 `rm -f`
#   保留(那是刻意「先清舊再掛 marker」),但一併清 owner 檔,讓舊程序看得出表已不是它的。
_ROWMAP_OWNER="$EZ2_COCKPIT_STATE_DIR/ai-rowmap.owner"
# ★ H3:owner 也不刪 —— 它與 rowmap 同一次發布原子寫入(見發布時序),
#   舊值只會被新值覆蓋。刪掉它會讓下面 cleanup 的 `-z` 分支變成
#   **「判不出擁有者就刪」**,而那正是守衛的失效值等於破壞性動作。
if [ "${EZ2_ONESHOT:-0}" != "1" ]; then
  cleanup_clicks() {
    # ★★ H3:**退出時不刪 rowmap/owner,只撤自己 pane 的 marker。**
    #   原本的刪除有兩個壞法,而兩個都不需要靠刪來解決:
    #   (一)舊程序退出會刪掉**新程序剛發布的**那一份(:42 自陳的 E2.19「高-1」);
    #        owner 守衛擋得住 `_own = 新 pid` 那一路,但擋不住 `-z` 那一路 ——
    #        **判不出擁有者時它選擇刪**,失效值等於破壞性動作。
    #   (二)正常退出也留下一個空窗,直到下一個世代發布為止。
    #   要守的性質是「kill/崩潰後不得殘留**可點**座標」,而可點與否的載體是 marker。
    #   撤下 marker 就達成了,且不需要知道那份 rowmap 是誰的 —— 這一格因此不再需要 owner 判定。
    if [ "${EZ2_RIGHT_VIEW:-}" = "usage" ]; then
      # 用量頁：EXIT 若撤 marker，父層 tabs 還來不及釘回時整頁假死
      [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null
    else
      [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane "" 2>/dev/null
    fi
    return 0
  }
  trap cleanup_clicks EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi

# stderr 節流狀態:同一失敗簽名連續 poll 只在首見/變化時印一次——
# 真障礙時 5 秒一則會把 stderr 洗版(CI 紅 31704985907 的直接根因之一)。
_last_fail_sig=""

# ★ 2026-09-01 修(the maintainer 問「何時可以顯示出來給我看」時量到):徽章 icon 從 R6 上線起
#   **一次都沒顯示過**,而畫面上與「字型沒裝所以退回字母」逐字相同。
#   根因:原本寫成 `EZ2_AIB_BADGE_TTY="$([ -t 1 ] && printf 1 || printf 0)"`——
#   **命令替換的 fd 1 是捕捉管線,不是終端**,所以 `[ -t 1 ]` 在 `$( )` 裡面**恆為假**,
#   這個變數永遠是 0,採集端永遠判 letters。實測:外層 `[ -t 1 ]` 為真的同一個 shell 裡,
#   `v=$([ -t 1 ] && printf 1 || printf 0)` 量到的 v=0。
#   ⇒ 判斷必須在**任何命令替換之外**做一次,而且要在 while 之前(pane 的 fd1 不會中途變)。
#   ★ 這一族=「探測器答的是另一個問題」:它量的是管線,不是終端,而兩個答案都是合法的布林。
# 呼叫端(pane-right-tabs.sh)已在**它自己的 tty 那一層**量好並匯出時,一律採用它——
# 本檔在真實右欄裡永遠住在 `$(render_frame ...)` 之內,自己量必定得到 0。
# 沒有繼承值時才自己量(本檔被直接執行、或被測試以 pty 直起的路徑)。
if [ -n "${EZ2_AIB_BADGE_TTY:-}" ]; then
  _BADGE_TTY="$EZ2_AIB_BADGE_TTY"
elif [ -t 1 ]; then
  _BADGE_TTY=1
else
  _BADGE_TTY=0
fi
export _BADGE_TTY
_usage_scroll_seen=""

# 沒有子選單的右欄頁（現在是 1.AI）。全量繪製寫 .right-page-<id>.json；
# 點大頁時只貼這份。有子選單的頁（用量/Codex）不走這裡。
# 新頁：全量繪製寫同一格式，並把頁簽值對到這個 id。寬或上方列數變了就改走全量。
_right_page_paint() {
  local page="${1:-}"
  case "$page" in
    ''|*[!a-z0-9_-]*) return 1 ;;
  esac
  local cache="$EZ2_COCKPIT_STATE_DIR/.right-page-${page}.json"
  [ -f "$cache" ] || return 1
  local out="$EZ2_COCKPIT_STATE_DIR/.ai-list.out.$$"
  local pending="$EZ2_COCKPIT_STATE_DIR/.ai-rowmap.pending.$$"
  local widthp="$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width.pending.$$"
  if ! EZ2_RIGHT_PAGE="$page" \
      EZ2_ROWMAP_PENDING="$pending" EZ2_RIGHT_WIDTH_PENDING="$widthp" \
      "$EZ2_PY3" - > "$out" <<'PY'
import json, os, sys
state_dir = os.environ["EZ2_COCKPIT_STATE_DIR"]
page = os.environ.get("EZ2_RIGHT_PAGE") or ""
path = os.path.join(state_dir, ".right-page-%s.json" % page)
with open(path, encoding="utf-8") as fh:
    cache = json.load(fh)
width = int(os.environ.get("EZ2_PANE_W") or 0) - 1
pane_h = int(os.environ.get("EZ2_PANE_H") or 0)
row_off = int(os.environ.get("EZ2_ROW_OFFSET") or 0)
if width < 2 or int(cache.get("width") or 0) != width:
    sys.exit(2)
if int(cache.get("pane_h") or -1) != pane_h or int(cache.get("row_offset") or -1) != row_off:
    sys.exit(2)
lines = cache.get("lines") if isinstance(cache.get("lines"), list) else None
rowmap = cache.get("rowmap") if isinstance(cache.get("rowmap"), list) else None
if lines is None or rowmap is None:
    sys.exit(2)
with open(os.environ["EZ2_ROWMAP_PENDING"], "w", encoding="utf-8") as fh:
    json.dump(rowmap, fh, ensure_ascii=False)
need = int(cache.get("required_width") or 40)
with open(os.environ["EZ2_RIGHT_WIDTH_PENDING"], "w", encoding="utf-8") as fh:
    fh.write("%d\n" % (need if need <= 40 else 40))
for ln in lines:
    print(ln)
PY
  then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  local fit
  if ! fit=$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/pane-fit.py" < "$out"); then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  if ! printf '%s\n' "$fit" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'; then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  mv -f "$pending" "$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json" 2>/dev/null || {
    rm -f "$out" "$pending" "$widthp"
    return 1
  }
  mv -f "$widthp" "$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width" 2>/dev/null || true
  rm -f "$out"
  return 0
}

# 用量頁捲動不必等 5 秒，也不必把整欄重查一遍。
# 完整那一幀把還沒裁切的畫面存下來；滾輪只改位置時，用那份重切。
_usage_scroll_paint() {
  [ "${EZ2_RIGHT_VIEW:-}" = "usage" ] || return 1
  # swap=點了用量/Codex。scroll=只改捲動位置，一律用已經畫好的那頁，不整頁重查。
  local reason="${1:-}"
  case "$reason" in
    swap|scroll) ;;
    *) reason="" ;;
  esac
  if [ -z "$reason" ] && [ -z "${_usage_scroll_seen:-}" ]; then
    return 1
  fi
  local cache="$EZ2_COCKPIT_STATE_DIR/.usage-frame-cache.json"
  local scroll="$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
  [ -f "$cache" ] && [ -f "$scroll" ] || return 1
  local sm cm now age
  sm=$(aib_mtime "$scroll" 0)
  local acc am want=0
  acc="$EZ2_COCKPIT_STATE_DIR/usage-accounts.json"
  am=0
  if [ -f "$acc" ]; then
    am=$(aib_mtime "$acc" 0)
  fi
  if [ -z "$reason" ]; then
    if [ -n "${_usage_scroll_seen:-}" ] && [ "$sm" != "$_usage_scroll_seen" ]; then
      want=1
    fi
    if [ -n "${_usage_accounts_seen:-}" ] && [ "$am" != "$_usage_accounts_seen" ]; then
      want=1
    fi
    [ "$want" = 1 ] || return 1
    cm=$(aib_mtime "$cache" 0)
    now=$(date +%s)
    age=$((now - cm))
    # 只是捲動、而且畫面超過 8 秒：改走完整重畫，時鐘才會動。
    if [ "$am" = "${_usage_accounts_seen:-}" ] && { [ "$age" -lt 0 ] || [ "$age" -ge 8 ]; }; then
      return 1
    fi
  fi
  local out="$EZ2_COCKPIT_STATE_DIR/.ai-list.out.$$"
  local pending="$EZ2_COCKPIT_STATE_DIR/.ai-rowmap.pending.$$"
  local widthp="$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width.pending.$$"
  if ! EZ2_USAGE_FAST_REASON="$reason" \
      EZ2_ROWMAP_PENDING="$pending" EZ2_RIGHT_WIDTH_PENDING="$widthp" \
      "$EZ2_PY3" - > "$out" <<'PY'
import json, os, sys
cockpit = os.environ["EZ2_COCKPIT_DIR"]
sys.path.insert(0, os.path.join(cockpit, "render"))
sys.path.insert(0, cockpit)
import usage_subtabs as ust
from render_lib import fit_usage_viewport
from right_fold_api import viewport
state_dir = os.environ["EZ2_COCKPIT_STATE_DIR"]
with open(os.path.join(state_dir, ".usage-frame-cache.json"), encoding="utf-8") as fh:
    cache = json.load(fh)
width = int(os.environ.get("EZ2_PANE_W") or 0) - 1
if width < 2 or int(cache.get("width") or 0) != width:
    sys.exit(2)
try:
    with open(os.path.join(state_dir, "usage-accounts.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
except (OSError, ValueError):
    sys.exit(2)
reason = os.environ.get("EZ2_USAGE_FAST_REASON") or ""
if reason != "scroll" and cache.get("sig") != ust.accounts_sig(doc):
    sys.exit(2)
sub = ""
try:
    with open(os.path.join(state_dir, "usage-subtab"), encoding="utf-8") as fh:
        sub = fh.read().strip()
except OSError:
    sub = ""
if sub not in ("usage", "codex"):
    sub = str(doc.get("subtab") or "usage")
if sub not in ("usage", "codex"):
    sub = "usage"
frames = cache.get("frames") if isinstance(cache.get("frames"), dict) else {}
frame = frames.get(sub)
if not isinstance(frame, dict):
    sys.exit(2)
total = int(os.environ.get("EZ2_PANE_H") or 0) - int(os.environ.get("EZ2_H_OFFSET") or 0) - 1
if total < 1:
    sys.exit(2)
try:
    with open(os.path.join(state_dir, "right-scroll.json"), encoding="utf-8") as fh:
        scroll = json.load(fh)
except (OSError, ValueError):
    scroll = {}
offset = viewport.tab_offset(scroll, "usage")
# 捲動不能把位置歸零。換頁才歸零，並記下現在畫的是哪一頁。
if reason != "scroll" and sub != str(cache.get("shown") or ""):
    offset = 0
    try:
        fresh = viewport.with_tab(scroll if isinstance(scroll, dict) else {}, "usage", 0)
        tmp = os.path.join(state_dir, "right-scroll.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh)
            fh.write("\n")
        os.replace(tmp, os.path.join(state_dir, "right-scroll.json"))
    except OSError:
        pass
    try:
        cache["shown"] = sub
        cpath = os.path.join(state_dir, ".usage-frame-cache.json")
        tmp = cpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, cpath)
    except OSError:
        pass
header_n = int(frame.get("header") or 0)
body_n = max(0, len(frame.get("lines") or []) - header_n)
room = total - header_n
limit = max(0, body_n - room) if room > 0 else 0
try:
    lpath = os.path.join(state_dir, ".right-scroll-limit.json")
    try:
        with open(lpath, encoding="utf-8") as fh:
            limits = json.load(fh)
    except (OSError, ValueError):
        limits = {}
    if not isinstance(limits, dict):
        limits = {}
    limits["usage"] = limit
    tmp = lpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(limits, fh)
        fh.write("\n")
    os.replace(tmp, lpath)
except OSError:
    pass
lines, rowmap = fit_usage_viewport(
    frame.get("lines") or [], frame.get("rowmap") or [],
    header_rows=header_n,
    total_rows=total, offset=offset, width=width,
    bar_arrows=True)
off = int(os.environ.get("EZ2_ROW_OFFSET") or 0)
if off:
    for row in rowmap:
        if isinstance(row, dict) and isinstance(row.get("line"), int):
            row["line"] += off
with open(os.environ["EZ2_ROWMAP_PENDING"], "w", encoding="utf-8") as fh:
    json.dump(rowmap, fh, ensure_ascii=False)
need = int(frame.get("required_width") or cache.get("required_width") or 40)
with open(os.environ["EZ2_RIGHT_WIDTH_PENDING"], "w", encoding="utf-8") as fh:
    fh.write("%d\n" % (need if need <= 40 else 40))
for ln in lines:
    print(ln)
PY
  then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  local fit
  if ! fit=$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/pane-fit.py" < "$out"); then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  if ! printf '%s\n' "$fit" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}'; then
    rm -f "$out" "$pending" "$widthp"
    return 1
  fi
  mv -f "$pending" "$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json" 2>/dev/null || {
    rm -f "$out" "$pending" "$widthp"
    return 1
  }
  mv -f "$widthp" "$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width" 2>/dev/null || true
  { printf '%s\n' "$$" > "$_ROWMAP_OWNER.$$" && mv -f "$_ROWMAP_OWNER.$$" "$_ROWMAP_OWNER"; } 2>/dev/null || true
  rm -f "$out"
  _usage_scroll_seen=$(aib_mtime "$scroll" "$sm")
  if [ -f "$acc" ]; then
    _usage_accounts_seen="$am"
  fi
  return 0
}

_usage_wait_next() {
  local i=0 scroll sm acc am
  scroll="$EZ2_COCKPIT_STATE_DIR/right-scroll.json"
  acc="$EZ2_COCKPIT_STATE_DIR/usage-accounts.json"
  while [ "$i" -lt 50 ]; do
    sleep 0.1
    i=$((i + 1))
    [ "${EZ2_RIGHT_VIEW:-}" = "usage" ] || continue
    if [ -f "$acc" ]; then
      am=$(aib_mtime "$acc" 0)
      if [ -n "${_usage_accounts_seen:-}" ] && [ "$am" != "$_usage_accounts_seen" ]; then
        return 0
      fi
    fi
    [ -f "$scroll" ] || continue
    sm=$(aib_mtime "$scroll" 0)
    if [ -n "${_usage_scroll_seen:-}" ] && [ "$sm" != "$_usage_scroll_seen" ]; then
      sleep 0.08
      return 0
    fi
  done
}

while true; do
  # 外層每次只叫一輪。點子頁或捲動才貼預載；定時那一輪仍整頁重查。
  _fast="${EZ2_USAGE_FAST_REASON:-}"
  if [ "${EZ2_ONESHOT:-0}" = "1" ] && { [ "$_fast" = "swap" ] || [ "$_fast" = "scroll" ]; }; then
    if _usage_scroll_paint "$_fast"; then
      exit 0
    fi
  fi
  # 1.AI（以及以後沒有子選單的右欄頁）點頁簽時貼上次畫好的那頁。
  if [ "${EZ2_ONESHOT:-0}" = "1" ] && [ "${EZ2_RIGHT_FAST_REASON:-}" = "tab" ] \
     && [ "${EZ2_RIGHT_VIEW:-}" != "usage" ]; then
    if _right_page_paint "${EZ2_RIGHT_PAGE:-ai}"; then
      [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
      exit 0
    fi
  fi
  if _usage_scroll_paint; then
    [ "${EZ2_ONESHOT:-0}" = "1" ] && break
    _usage_wait_next
    continue
  fi
  [ "${EZ2_ONESHOT:-0}" = "1" ] || clear
  # 活窗清單。★治具入口只在 EZ2_ONESHOT 測試模式承認(一輪審中7);
  # 正式路徑=兩段式(三輪審高1):ids-only 快照(@數字無注入面)+逐窗查
  # @aib_role 整值驗證——單次 list-windows 拼欄位時,role/窗名值內的 \n
  # 與 | 可偽造他窗條目。rc 不吞(二輪審中4:採集失敗≠空艦隊)。
  _hub_rc=0
  if [ "${EZ2_ONESHOT:-0}" = "1" ] && [ -n "${AIB_FAKE_WINDOWS+x}" ]; then
    EZ2_HUB_WINDOWS="$AIB_FAKE_WINDOWS"
    EZ2_HUB_PANES="${AIB_FAKE_PANES:-}"      # BD 治具:'@1|%1' 逐行,與活窗清單同形
  else
    # 用量頁不畫席位樹。逐窗問 role 會讓點 Codex 多等一整圈。
    if [ "${EZ2_RIGHT_VIEW:-}" = "usage" ]; then
      EZ2_HUB_WINDOWS=""
      EZ2_HUB_PANES=""
      EZ2_HUB_DEAD_WINDOWS=""
      _hub_rc=0
    else
    # BD(09-03):同一次快照多帶 #{pane_id}(窗的活躍 pane;席位窗只有一個 pane)。
    # 兩個都是 tmux 自產的 id(@N / %N),下面各自整值驗證,不是注入面。
    # 09-11(@15 c0317):第三欄帶 #{pane_dead}。CLI 自己退出後 pane 還在、席位檔還在,
    # 右欄若只看開席碼表與回執帳本,會把「視窗已死」畫成「等待逾時 · 重試 aib seat onboard」
    # ——重投開席單救不回一個死掉的 pane。死窗清單交給下面的 python 判失敗原因。
    _hub_ids="$(env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" list-windows -t aihub \
      -F '#{window_id}|#{pane_id}|#{pane_dead}' 2>/dev/null)" || _hub_rc=$?
    EZ2_HUB_WINDOWS=""
    EZ2_HUB_PANES=""
    EZ2_HUB_DEAD_WINDOWS=""
    if [ "$_hub_rc" -eq 0 ]; then
      while IFS= read -r _wline; do
        [ -n "$_wline" ] || continue
        _wdead=""
        case "$_wline" in
          *\|*\|*) _wid="${_wline%%|*}"; _wrest="${_wline#*|}"; _wpane="${_wrest%%|*}"; _wdead="${_wrest#*|}" ;;
          *\|*)    _wid="${_wline%%|*}"; _wpane="${_wline#*|}" ;;
          *)      _wid="$_wline"; _wpane="" ;;
        esac
        [ "$_wdead" = "1" ] && EZ2_HUB_DEAD_WINDOWS="${EZ2_HUB_DEAD_WINDOWS}${_wid} "
        case "$_wpane" in
          %[0-9]*) case "$_wpane" in *[!%0-9]*) _wpane="" ;; esac ;;
          *) _wpane="" ;;
        esac
        [ -n "$_wpane" ] && EZ2_HUB_PANES="${EZ2_HUB_PANES}${_wid}|${_wpane}
"
        _wrole="$(env -u TMUX tmux -L "$EZ2_AIHUB_SOCKET" show-options -wqv \
          -t "$_wid" @aib_role 2>/dev/null || true)"
        # 整值驗證(含 \n 與 |):髒 role=該窗整筆拒,不進活集合(fail-visible)
        case "$_wrole" in
          *[!0-9A-Za-z@._-]*)
            echo "pane-right-ai: window $_wid role rejected (unlisted chars)" >&2
            continue
            ;;
        esac
        EZ2_HUB_WINDOWS="${EZ2_HUB_WINDOWS}${_wid}|${_wrole}
"
      done <<EOF
$_hub_ids
EOF
    fi
    fi
  fi
  export EZ2_HUB_WINDOWS EZ2_HUB_PANES EZ2_HUB_DEAD_WINDOWS
  # F-d:巡查器是綁定提醒的唯一判準；右欄只同步它產出的完整快照。
  # 取值或驗證失敗時不清舊檔，避免把「量不到」畫成「已綁好」。
  # AIB_BIND_NOTICES 是尺的注入點(與 AIB_TITLE_BEAT 同形)。上下文探針只量讀取,
  # 不在這一幀真的去跑巡查。沒設就走下面的正式路徑。
  if [ -n "${AIB_BIND_NOTICES:-}" ]; then
    bash "$AIB_BIND_NOTICES" || true
  else
    # tip: bind-notices must not block paint (was pegging CPU → clicks feel dead, 1.AI tab stalls).
  # Reuse cache if <30s old; otherwise run with 0.4s wall timeout and refresh cache on success.
  _bind_notice_cache="$EZ2_COCKPIT_STATE_DIR/.bind-notices.cache"
  _bind_notice_snapshot="$EZ2_COCKPIT_STATE_DIR/.bind-notices.$$"
  if [ "$_hub_rc" -eq 0 ] \
      && [ -f "$EZ2_COCKPIT_DIR/fleet_patrol.py" ] \
      && [ -f "$EZ2_COCKPIT_DIR/composer_notice.py" ]; then
    _bn_fresh=0
    if [ -f "$_bind_notice_cache" ]; then
      _bn_age=$(( $(date +%s) - $(aib_mtime "$_bind_notice_cache" 0) ))
      [ "$_bn_age" -ge 0 ] && [ "$_bn_age" -lt 30 ] && _bn_fresh=1
    fi
    # BIND_CACHE_INPUTS: 席位投影或機器值變新時,不得沿用未綁快取。
    # bash 3.2 -nt 只比到秒;同一秒用 st_mtime_ns,顯示檔>=快取即作廢。
    if [ "$_bn_fresh" = "1" ]; then
      if ! "$EZ2_PY3" -c 'import os,sys
c=os.stat(sys.argv[2]).st_mtime_ns
d,x=sys.argv[1],sys.argv[3]
if d and os.path.isfile(d) and os.stat(d).st_mtime_ns>=c: raise SystemExit(1)
if x and os.path.isdir(x):
  for n in os.listdir(x):
    if n.endswith(".pct"):
      try:
        if os.stat(os.path.join(x,n)).st_mtime_ns>=c: raise SystemExit(1)
      except OSError: pass
' "${EZ2_AIB_SEAT_DISPLAY:-}" "$_bind_notice_cache" "${EZ2_CTX_DIR:-}"; then
        _bn_fresh=0
      fi
    fi
    if [ "$_bn_fresh" = "1" ]; then
      "$EZ2_PY3" "$EZ2_COCKPIT_DIR/composer_notice.py" sync-bind \
        --snapshot "$_bind_notice_cache" 2>/dev/null || true
    else
      # 10-11:牆鐘上限可由 AIB_BIND_NOTICES_TIMEOUT 調(預設照舊 0.4 秒)。機器忙時 python 起不來 0.4 秒就逾時,
      #   第一幀沒有提醒 ⇒ test-attention-on-screen-fd 時紅時綠;治具放寬,產品預設不變。
      if "$EZ2_PY3" -c 'import subprocess,sys; r=subprocess.run(sys.argv[2:],timeout=float(sys.argv[1])); raise SystemExit(r.returncode)' \
          "${AIB_BIND_NOTICES_TIMEOUT:-0.4}" "$EZ2_PY3" "$EZ2_COCKPIT_DIR/fleet_patrol.py" bind-notices \
          --hub "$EZ2_AIHUB_SOCKET" --json > "$_bind_notice_snapshot" 2>/dev/null; then
        cp -f "$_bind_notice_snapshot" "$_bind_notice_cache" 2>/dev/null || true
        "$EZ2_PY3" "$EZ2_COCKPIT_DIR/composer_notice.py" sync-bind \
          --snapshot "$_bind_notice_snapshot" 2>/dev/null || true
      elif [ -f "$_bind_notice_cache" ]; then
        "$EZ2_PY3" "$EZ2_COCKPIT_DIR/composer_notice.py" sync-bind \
          --snapshot "$_bind_notice_cache" 2>/dev/null || true
      fi
      rm -f -- "$_bind_notice_snapshot"
    fi
  fi
  fi
  if [ -n "${TMUX_PANE:-}" ]; then
    read -r EZ2_PANE_W EZ2_PANE_H <<EOF
$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{pane_height}' 2>/dev/null || printf '%s %s\n' "${COLUMNS:-44}" "${LINES:-30}")
EOF
    export EZ2_PANE_W EZ2_PANE_H
  fi
  # renderer 失敗時必須讓 rowmap 失效(codex 審中級:失敗會保留舊 rowmap
  # 與 ai-list marker,滑鼠仍會依陳舊座標跳轉到「剛好還在」的視窗)。
  # 用管線就拿不到 python 的 rc,所以先落檔取真 rc 再輸出。
  _ai_out="$EZ2_COCKPIT_STATE_DIR/.ai-list.out.$$"
  # 二輪審中5:rowmap 先寫進 per-PID pending,畫面成功輸出後才發布——
  # 早發布會有「畫面舊、映射新」的窗口,點 A 跳 B 破壞同源紀律。
  _rowmap_pending="$EZ2_COCKPIT_STATE_DIR/.ai-rowmap.pending.$$"
  _right_width_pending="$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width.pending.$$"
  # ── L8b(cap2 09:35 裁定一:R1 宿主=本採集端)────────────────────────────
  # 讀「本席的輸入框被別人的稿卡住」通知。**只讀不寫、不碰任何輸入框。**
  # 09-05 那件:被擋席 4.5 小時零訊號,而以「他自己 dm 別人」為觸發的提醒
  # 在那種故障裡剛好不會開火 —— 所以要掛在**每輪都跑**的這裡。
  # ★ 這裡只**取字**並 export,不自己往畫面加行:rowmap 與畫面同源,
  #   採集端多印一行會讓每一列的點擊座標位移(點 A 跳 B)。放哪一列由
  #   渲染器決定 —— 見交件說明「R1 落點候裁」。
  EZ2_AIB_COMPOSER_NOTICE=""
  if [ -n "${EZ2_AIB_SELF_CODE:-}" ] && [ -f "$EZ2_COCKPIT_DIR/composer_notice.py" ]; then
    EZ2_AIB_COMPOSER_NOTICE="$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/composer_notice.py" \
        render --seat "$EZ2_AIB_SELF_CODE" 2>/dev/null || true)"
  fi
  export EZ2_AIB_COMPOSER_NOTICE
  export EZ2_ROWMAP_PENDING="$_rowmap_pending"
  export EZ2_RIGHT_WIDTH_PENDING="$_right_width_pending"
  _render_failed=0
  _fail_sig=""
  if [ "$_hub_rc" -ne 0 ]; then
    printf 'pane-right-ai: hub list-windows failed (rc=%s)\n' "$_hub_rc" > "$_ai_out.err"
    _render_failed=1
    _fail_sig="hub:$_hub_rc"
  elif EZ2_AIB_BADGE_TTY="$_BADGE_TTY" \
       "$EZ2_PY3" - > "$_ai_out" 2>"$_ai_out.err" <<'PY'
import datetime, glob, json, math, os, re, subprocess, sys, time

cockpit = os.environ["EZ2_COCKPIT_DIR"]
sys.path.insert(0, os.path.join(cockpit, "render"))
sys.path.insert(0, cockpit)
import seat_address  # noqa: E402
from render_lib import (  # noqa: E402
    ENGINE_ICONS, ENGINE_LETTERS, DEFAULT_PANE_W, render_ai_list_with_map, required_ai_pane_width,
    render_usage_view_with_map, required_usage_pane_width, fit_usage_viewport,  # 右欄第 2 頁「AI用量」
    onboard_wait_state)  # C1.3:等回執那一格的三態,純函式住渲染器,文字在這裡配 i18n
from engine_badge_mode import badge_inputs  # noqa: E402  R6:三態與字型能力判在採集端,注入渲染器
import ai_view  # noqa: E402  右欄兩行的顯示開關:讀檔在採集端,渲染器不讀檔
import dm_queue  # noqa: E402  排序暫存:採集端讀全文,渲染器只裁一列
from cockpit_lib import mark_colliding_labels  # noqa: E402
from i18n_lib import Translator  # noqa: E402

now = time.time()
try:
    _q_by = dm_queue.by_seat_for_render()
except Exception:
    _q_by = {}

# 席位檔欄位是外部輸入。三輪審高1:白名單「整筆拒收」不洗字元——
# 刪字元語意會讓 'p#m' 洗成 'pm' 冒充別的角色;含白名單外字元=該席不顯示。
WID_RE = re.compile(r'@[0-9]{1,10}$')
TOKEN_RE = re.compile(r'[0-9A-Za-z@._-]{1,32}$')

def clean_token(text):
    """回驗證過的字串;None=整筆拒(含白名單外字元/超長/非字串,或語意
    denylist)。六輪審高2:字元白名單擋不住語意——role/provider/seat_id
    塞 "verified"/"registry_verified"(純英數)會打穿「全輸出無 verified
    字樣」紅線,含 verified 子串一律整筆拒。"""
    if not isinstance(text, str) or not TOKEN_RE.fullmatch(text):
        return None
    if 'verified' in text.lower():
        return None
    return text

def note(msg):
    # fail-visible(一輪審中6):壞形狀跳過要留痕,不留痕的跳過=靜默資料消失
    print('pane-right-ai: %s' % msg, file=sys.stderr)


def note_log(msg):
    """兩路併走的留痕:note() + 側 log。

    ★ 為什麼不能只有 note():本檔的 stderr 被導進暫存檔(`2>"$_ai_out.err"`),
      而那個檔**只有渲染失敗時**才被 relay 出來,成功時直接刪掉——也就是說
      **渲染成功時 note() 印的東西沒有人看得到**,而下面兩個呼叫點正好都
      發生在畫面好好的時候。成功時把 stderr 放出來不是解(每幀重畫會把噪音
      噴進 AI pane,那正是當初導進暫存檔的原因)。
    ★ 用它的判準是「這件事異常嗎」,不是「這件事重要嗎」:**常態不要留痕**。
      每幀都在寫的日誌兩週後沒有人會再看——一個永遠在響的鈴等於沒有鈴。
    (本函式是把下方「全席未編制」那則既有的兩路寫法抽出來,不是新發明;
     抽出來的理由是同一個病已經有第二個呼叫點,兩份實作遲早分岔。)
    """
    note(msg)
    try:
        _sdir = os.environ.get('EZ2_COCKPIT_STATE_DIR') or ''
        if _sdir:
            with open(os.path.join(_sdir, 'ai-notes.log'), 'a',
                      encoding='utf-8') as _fh:
                _fh.write('%s pane-right-ai: %s\n'
                          % (time.strftime('%F %T'), msg))
    except OSError:
        pass      # 留痕失敗不得擋畫面(rc0 no-op 紅線)

def finite_epoch(value, fallback):
    """三輪審中1:epoch 必須是有限數——NaN/±Infinity 會炸排序與 int()。
    回 (ok, float);ok=False 表示該欄不可信。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False, fallback
    if not math.isfinite(f):
        return False, fallback
    return True, f


# ---- E2.19 第四刀:席位↔session 對映與子代理真相源(2026-08-14) ----------
# 真相源=Claude projects dir 的 session 目錄(唯讀):
#   <projects>/<uuid>/subagents/agent-*.{jsonl,meta.json}
# 席位五碼=uuid 頭或尾(整合席艙外實測:root 37a0b=頭、pm b3124=尾)。
# 最終批增量:席位檔 optional 欄 session_uuid(完整 uuid)——真機殘餘現形
# (合成雜湊 seat_id 頭尾對映不到),有合法值=直接綁免猜;壞值=note 忽略欄
# 退 fallback;缺欄=頭尾對映原樣(w1/w2 零影響)。寫端(record_seat)屬另刀。
_UUID_FULL_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
# 七輪審高2:C1 區(U+0080-009F)含單字元 CSI(9B)/OSC(9D)形,同屬注入面
_CTRL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')

# clean_text 與人格取值搬到 seat_persona.py:艦長列也要顯示人格,兩處各留
# 一份等值判斷不算同源(交接單第三節 (B) 硬性條件)。此處只 import,不重寫。
from seat_persona import (  # noqa: E402
    clean_text, display_entry, folder_of, load_display, persona_of,
    project_title_of)
# ❓未編制判定(the maintainer 08-15 直令):判準全在 seat_status,本檔只呼叫不重寫
# ——守衛(seat_guard.py)與本面板共用同一份,否則遲早分岔成「面板說未編制、
# 守衛說沒事」(與 activity_probe 單一真相源同一條紀律)。
from seat_status import (  # noqa: E402
    ONBOARD_MARK, ONBOARD_NO_ACK, ONBOARD_STALE, UNASSIGNED_MARK,
    onboard_name_suffix,
    onboard_reason, should_mark_onboard, unassigned_reason, identity_pending,
    opening_attempt_state, load_opening_attempts, load_ack_fails)
# 開席單回執(A,08-16):採集在 seat_onboard,判準在 seat_status——本檔
# 與 `aib seat onboard --missing` 讀的是同一份索引與同一個判準函式。
from seat_onboard import (  # noqa: E402
    current_template_sha, default_ledger_path, load_index, seat_of_correlation)

# 每幀重讀:帳本是 append-only 且以事件計小(真機 117 行/114KB),而快取
# 一份索引就要處理失效——右欄的既有哲學是每幀重新採集,不留狀態。
_ONBOARD_SHA = current_template_sha()
_ONBOARD_LEDGER = default_ledger_path()
_ONBOARD_INDEX = load_index(_ONBOARD_LEDGER)[0] if _ONBOARD_SHA else {}
_OPENING_ATTEMPTS = load_opening_attempts(os.environ.get('EZ2_AIB_INSTANCE_HOME'))
_ACK_FAILS = load_ack_fails(_ONBOARD_LEDGER)


def load_onboard_sent_ms(ledger_path, sha):
    """C1.3:帳本 → {seat_id: 現行模板最近一次 outbound 的 hlc.wall_ms}。

    只認 message.outbound + 現行模板 sha(舊模板的投遞不算「這一次」);
    沒 hlc、非數字、壞行一律跳過 ⇒ 那一席查不到 ⇒ 顯示層印「投遞時間量不到」,
    不拿席位檔 started_epoch 頂替(那是開席時間,不是投遞時間)。"""
    out = {}
    if not sha or not ledger_path or not os.path.exists(ledger_path):
        return out
    try:
        with open(ledger_path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict) or ev.get('kind') != 'message.outbound':
                    continue
                ref = ((ev.get('body') or {}).get('data_ref') or {})
                if ref.get('sha256') != sha:
                    continue
                cid = (ev.get('causation') or {}).get('correlation_id')
                seat = seat_of_correlation(cid) if isinstance(cid, str) else None
                if not seat:
                    continue
                try:
                    wall = int((ev.get('hlc') or {}).get('wall_ms'))
                except (TypeError, ValueError):
                    continue
                if wall > out.get(seat, -1):
                    out[seat] = wall
    except OSError:
        return out
    return out


_ONBOARD_SENT_MS = load_onboard_sent_ms(_ONBOARD_LEDGER, _ONBOARD_SHA)
_DEAD_WINDOWS = frozenset(w for w in (os.environ.get('EZ2_HUB_DEAD_WINDOWS') or '').split() if w.startswith('@'))


SUB_FRESH_S = int(os.environ.get('EZ2_AIB_SUB_FRESH_S') or 120)
# the maintainer 08-14 拍板:剛收工顯示 ⚫ 五分鐘——活窗(≤FRESH)之後再留 DONE_S
# 的「⚫ 收工」窗,之後才撤(仍無殭屍列表,只是收工有告別式)。
SUB_DONE_S = int(os.environ.get('EZ2_AIB_SUB_DONE_S') or 300)

def collect_subagents(proj, uuid):
    """meta=agentType/description;活性=同名 .jsonl mtime 新鮮度:
    ≤SUB_FRESH_S=活(🟢/🟡);FRESH~FRESH+DONE_S=⚫ 收工;更久=不顯示。唯讀。"""
    subs = []
    for mp in sorted(glob.glob(os.path.join(proj, uuid, 'subagents',
                                            'agent-*.meta.json'))):
        jl = mp[:-len('.meta.json')] + '.jsonl'
        try:
            age = now - os.path.getmtime(jl)
        except OSError:
            # 08-21:原本這裡是**靜默** continue,而同一個函式另外兩條拒收
            #   (meta 讀不到、檔名不合)都有 note()。同一種事三個出口兩種
            #   待遇,那一條就是將來沒有人查得到的那條。
            #   實測生產側 214 份 meta 缺同名 jsonl = 0 → 留痕不會變噪音。
            note_log('subagent transcript missing skipped: %s'
                     % os.path.basename(jl))
            continue
        if age < 0:
            # 時鐘倒退(或檔案時戳在未來)。原本它與「過期」共用一條 continue
            #   ——兩種成因一個出口,而**它們的處置相反**:過期是常態(子 AI
            #   本來就會做完),時戳在未來是機器有問題。分開,而且訊息不同字。
            note_log('subagent transcript timestamp in the future skipped: '
                     '%s (age=%ds)' % (os.path.basename(jl), int(age)))
            continue
        if age > SUB_FRESH_S + SUB_DONE_S:
            continue          # 過期=常態,刻意不留痕(見 note_log 檔頭第二條)
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except Exception:
            note('subagent meta unreadable skipped: %s' % os.path.basename(mp))
            continue
        desc = clean_text(meta.get('description'), 24)
        # the maintainer 08-14 三裁:列上=圓點+uuid末五+運作時間,任務文字也不入列
        # (顏色即狀態、末五即身份;任務描述進詳情卡)。型名 i18n 詞條
        # aib.subagent.type.* 同樣保留給詳情卡,逐步更新。
        stem = os.path.basename(mp)[:-len('.meta.json')]
        # 十五輪審高1+十六輪審2:檔名是注入面——tail5 直上畫面、stem 直入
        # rowmap uuid 欄,驗**完整 stem** ^agent-[0-9a-f]{4,}$(只驗尾五時
        # 「惡意前綴+合法尾五」仍走私進 rowmap);verified 不可能過純 hex
        # 白名單,denylist 由 regex 收編。不合=跳過該子(fail-visible)
        if not re.fullmatch(r'agent-[0-9a-f]{4,}', stem):
            note('subagent filename rejected: %s' % os.path.basename(mp))
            continue
        tail5 = stem[-5:]
        sub = {
            'age_minutes': int(age // 60),
            'label': tail5,
            'desc': desc,             # 詳情卡用;列上不顯示
            'uuid': stem,
        }
        if age > SUB_FRESH_S:
            sub['activity'] = 'done'
        subs.append(sub)
    return subs


# ---- codex 席的第三層(車道 D,08-21)--------------------------------------
# `subagent_collector.collect_codex_subagents` 從落地起就有 6 支測試全綠,
# **而生產側從來沒有人呼叫它**——於是 codex 席在右欄永遠長不出第三層,
# 測試也永遠不會紅。★ 這個形狀比「壞掉」更難發現:**有些席看得到、有些看不到**
# (同一個形狀 08-16 咬過一次:艦長席 cwd 不在 repo,子代理一個都沒顯示)。
#
# 根目錄取 `EZ2_CODEX_SESSIONS_DIR`,與 `activity_probe.py:115` **逐字相同**——
# 兩者看同一個抽屜是硬要求:一個說「這席有活動」另一個說「找不到 thread」
# 會是最難查的那種矛盾。
# ★ 已知缺陷(不在本車道,另行回報):`cockpit-env.sh:147` 匯出的是
#   `EZ2_CODEX_SESSIONS_ROOT`,而既有探針讀的是 `..._DIR`——**兩個名字指同一件
#   事,今天把 ROOT 指到別處,探針不會跟著走而且不報錯。**
from subagent_collector import (  # noqa: E402
    UUID_RE as CODEX_UUID_RE, collect_codex_subagents)

CODEX_SESSIONS_ROOT = (os.environ.get('EZ2_CODEX_SESSIONS_DIR')
                       or os.path.expanduser('~/.codex/sessions'))


# ★ 為什麼下面要有時間閘(實測,不是預防性設計):
#   採集成本由**子 thread 數**決定,不是父檔大小。真機量到:
#     5.4MB 活躍父檔 0 子 → 39ms      20MB 對照 0 子 → 108ms
#     207MB 父檔 157 子   → **37,391ms**(每個子各一次遞迴 glob + 整檔讀)
#   右欄每幾秒重畫一次;一個子很多的 codex 席會讓整個面板停住幾十秒。
#   ★★ 我的治具(三個小檔)**永遠不會抓到這件事**——是拿真 rollout 量才看見的。
#   本檔是顯示端,不能改採集器(`subagent_collector.py` 不在我的擁有權表上),
#   所以閘設在**呼叫邊界**:超過預算就記一筆並讓那一席冷卻一段時間,
#   面板不再被它拖住。冷卻期間該席沒有第三層——**這是已知的降級,不是修好**,
#   後續項見 docs/reports/REVIEW-PREREG-D-codex-subagents.md 追記一。
CODEX_SUB_BUDGET_S = float(os.environ.get('EZ2_AIB_CODEX_SUB_BUDGET_S') or 1.0)
CODEX_SUB_COOLDOWN_S = float(
    os.environ.get('EZ2_AIB_CODEX_SUB_COOLDOWN_S') or 600)


def _codex_slow_marker(uuid):
    """冷卻標記路徑;沒有狀態目錄就沒有冷卻(退回每幀都採,誠實)。"""
    _sdir = os.environ.get('EZ2_COCKPIT_STATE_DIR') or ''
    if not _sdir or CODEX_UUID_RE.fullmatch(str(uuid or '')) is None:
        return None
    return os.path.join(_sdir, 'codex-subs-slow-%s.stamp' % uuid)


def collect_codex_subs(uuid):
    """codex 席的子 AI,回傳與 collect_subagents **同契約**的 dict 串。

    顯示窗沿用 claude 那條的 `SUB_FRESH_S + SUB_DONE_S`,不另立一套:
    第三層的語意是「最近這幾分鐘誰在動」,那條規矩對兩個引擎必須一致。
    ★ 後果要寫下來:collector 的 `offline`(≥60 分)因此**在生產上永遠不上畫面**。
      那不是漏掉,是同一條窗的必然結果——寫在這裡,免得日後有人把它當 bug 修。
    """
    _marker = _codex_slow_marker(uuid)
    if _marker:
        try:
            if time.time() - os.path.getmtime(_marker) < CODEX_SUB_COOLDOWN_S:
                return []          # 冷卻中:標記檔本身就是留痕(建立時已記一筆)
            os.unlink(_marker)     # 冷卻到期,再給它一次機會
        except OSError:
            pass
    _t0 = time.time()
    try:
        kids = collect_codex_subagents(uuid, CODEX_SESSIONS_ROOT, now=now)
    except Exception as exc:      # 採集不得殺面板(rc0 no-op 紅線)
        note_log('codex subagent collection failed for %s: %s'
                 % (str(uuid)[-5:], exc))
        return []
    _elapsed = time.time() - _t0
    if _elapsed > CODEX_SUB_BUDGET_S and _marker:
        # 這一幀已經付掉了,擋的是**下一幀**。記一筆(只在建立標記時記,
        # 不是每幀記——每幀記的日誌沒有人會再看)。
        note_log('codex subagent collection slow for %s: %.1fs > %.1fs budget; '
                 'third layer suppressed for %ds'
                 % (str(uuid)[-5:], _elapsed, CODEX_SUB_BUDGET_S,
                    int(CODEX_SUB_COOLDOWN_S)))
        try:
            with open(_marker, 'w', encoding='utf-8') as _fh:
                _fh.write('%.3f %.3f\n' % (time.time(), _elapsed))
        except OSError:
            pass
    window_m = (SUB_FRESH_S + SUB_DONE_S) / 60.0
    subs = []
    for kid in kids:
        cid = str(kid.get('uuid') or '')
        # 顯示邊界重驗:uuid 會直接進 rowmap 與畫面。用 collector 自己的
        # 正則(讀真來源,不在這裡再寫一條——兩條正則遲早只有一條被更新)。
        if CODEX_UUID_RE.fullmatch(cid) is None:
            note_log('codex subagent uuid rejected: %r' % cid[:40])
            continue
        age_m = kid.get('age_minutes')
        if not isinstance(age_m, int) or age_m < 0 or age_m > window_m:
            continue
        subs.append({
            'age_minutes': age_m,
            'label': cid[-5:],        # 末五碼,與 claude 那條同慣例
            'uuid': cid,
            'activity': kid.get('activity'),
        })
    return subs


# ---- grok 席的第三層(2026-09-02,量測 MEASURE-groksubs-4e401-0902)--------
# 兩個獨立成因都要修才會有東西,留痕也必須分開,不能寫成一句「找不到」:
#   成因一 掃錯樹:grok 子代理住在 ~/.grok/sessions/<encoded-cwd>/<uuid>/subagents/,
#         不是 claude 的 projects 目錄。掉進 else 去 collect_subagents = 永遠空陣列,
#         與「沒派過」逐字相同。分派見下方 `elif provider == 'grok'`。
#   成因二 檔名形狀:grok 是 <child-uuid>/meta.json,不是 agent-*.meta.json。
#         掃到 claude 形檔名要點名拒收(本函式開頭那圈 glob),不得當 grok 子畫出。
#
# 活性:meta.status + started_at/completed_at(ISO UTC)。**禁止**用父 session
# 最後一筆 / 父 updates.jsonl mtime——那是整席時間,不是子代理活性。
# 顯示窗沿用 SUB_FRESH_S + SUB_DONE_S,與 claude/codex 同一條契約。
def _grok_iso_epoch(value):
    """量到的形:`2026-09-01T18:14:30.564770Z`。無時區=量不到,不假設本地。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def collect_grok_subs(uuid, cwd=None):
    """grok 席的子 AI,回傳與 collect_subagents **同契約**的 dict 串。

    樹與檔名都走量到的實形;沒有 `subagents/` = 沒派過 = 空陣列不留錯誤痕。
    """
    folder = grok_session_dir(uuid, cwd=cwd)
    if not folder:
        return []          # 找不到 session 目錄:沒有子代理證據,不是讀失敗
    sub_root = os.path.join(folder, 'subagents')
    # 成因二:先把 claude 式檔名點名拒收,再用 grok 實形 */meta.json。
    for mp in sorted(glob.glob(os.path.join(sub_root, 'agent-*.meta.json'))):
        note_log('grok subagent claude-shaped filename skipped: %s'
                 % os.path.basename(mp))
    subs = []
    window_s = SUB_FRESH_S + SUB_DONE_S
    for mp in sorted(glob.glob(os.path.join(sub_root, '*', 'meta.json'))):
        child_dir = os.path.basename(os.path.dirname(mp))
        if _UUID_FULL_RE.fullmatch(child_dir) is None:
            note_log('grok subagent id rejected: %r' % child_dir[:40])
            continue
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except Exception:
            note_log('grok subagent meta unreadable skipped: %s'
                     % os.path.basename(os.path.dirname(mp)))
            continue
        if not isinstance(meta, dict):
            note_log('grok subagent meta unreadable skipped: %s' % child_dir)
            continue
        sid = str(meta.get('subagent_id') or '')
        cid = str(meta.get('child_session_id') or '')
        parent = str(meta.get('parent_session_id') or '')
        if (sid != child_dir or cid != child_dir
                or parent != str(uuid)
                or _UUID_FULL_RE.fullmatch(sid) is None):
            note_log('grok subagent id rejected: %r' % child_dir[:40])
            continue
        status = meta.get('status')
        if status == 'running':
            epoch = _grok_iso_epoch(meta.get('started_at'))
            if epoch is None:
                note_log('grok subagent activity unmeasurable skipped: %s '
                         '(status=%r)' % (child_dir[-5:], status))
                continue
            if epoch > now:
                note_log('grok subagent timestamp in the future skipped: %s'
                         % child_dir[-5:])
                continue
            age = now - epoch
            # running 是一等活性:即使跑超過 FRESH+DONE 仍顯示,不拿年齡藏活著的子
            sub = {
                'age_minutes': int(age // 60),
                'label': child_dir[-5:],
                'desc': clean_text(meta.get('description'), 24),
                'uuid': child_dir,
            }
            # running 不標 done:開始超過新鮮窗仍是活著,圓點走年齡契約
            subs.append(sub)
            continue
        if status == 'completed':
            epoch = _grok_iso_epoch(meta.get('completed_at'))
            if epoch is None:
                note_log('grok subagent activity unmeasurable skipped: %s '
                         '(status=%r)' % (child_dir[-5:], status))
                continue
            if epoch > now:
                note_log('grok subagent timestamp in the future skipped: %s'
                         % child_dir[-5:])
                continue
            age = now - epoch
            if age > window_s:
                continue          # 過期=常態,刻意不留痕
            sub = {
                'age_minutes': int(age // 60),
                'label': child_dir[-5:],
                'desc': clean_text(meta.get('description'), 24),
                'uuid': child_dir,
            }
            if age > SUB_FRESH_S:
                sub['activity'] = 'done'
            subs.append(sub)
            continue
        note_log('grok subagent activity unmeasurable skipped: %s (status=%r)'
                 % (child_dir[-5:], status))
    return subs


# ---- 多引擎真活動探針:實作在 src/cockpit/activity_probe.py --------------
# **單一真相源**(第三艦長硬性條件):右欄畫 [X] 與 mouse-ctl 殺窗前重驗閒置
# 共用同一份;同一個問題兩份實作遲早分岔成「面板說閒置、殺前說不閒置」,
# 那比沒有重驗更糟。三引擎探針表、誤綠踩坑史(agy presence lock / .db 本體
# WAL 落後)、為何不用 tmux #{window_activity},全記在該模組檔頭。
from activity_probe import (  # noqa: E402
    activity_age, project_dir_for, projects_dir, resolve_session,
    session_dirs)
from grok_ctx_probe import (  # noqa: E402
    refresh as grok_ctx_refresh, session_dir as grok_session_dir)
from usage_probe import (  # noqa: E402  09-05:週用量區塊,渲染器不讀檔
    refresh_all as usage_refresh_all, rows_for_render as usage_rows_for_render,
    rows_when_collect_failed as usage_rows_when_collect_failed)
# D41(09-06):/clear 換 session 後,以登記五碼的 by-seat 記錄找現任 session。
from ctx_by_seat import resolve_live_session, bridge_present  # noqa: E402
from model_label import (  # noqa: E402
    display_model, model_drift_state, DRIFT_DIFFERENT)
from copilot_ctx_probe import refresh as copilot_ctx_refresh  # noqa: E402  09-05:copilot 沒有 statusline 寫端
from codex_ctx_probe import refresh as codex_ctx_refresh
from agy_model_probe import refresh as agy_model_refresh  # noqa: E402  10-01:agy 換模型後右欄要跟著變
from seat_uuid_backfill import maybe_redetect  # noqa: E402  09-05:pending 席事件驅動重偵測


def _codex_quota_line(rec):
    """席位檔 codex_quota(@00000 #11 寫:{state:"exhausted", line:"額度用完,HH:MM 重置", reset_clock, at_ms})→ 列上那句;
    只認 state=exhausted、line 是短字串、沒有控制字元;其餘一律 ''(不畫,不猜)。"""
    q = rec.get('codex_quota') if isinstance(rec, dict) else None
    if not isinstance(q, dict) or q.get('state') != 'exhausted':
        return ''
    line = q.get('line')
    if not isinstance(line, str):
        return ''
    line = line.strip()
    if not line or len(line) > 40 or any(ord(ch) < 32 or ord(ch) == 127 for ch in line):
        return ''
    return line


def _codex_quota_clock(rec):
    """codex_quota.reset_clock 是 HH:MM 才回它;其餘 ''。"""
    q = rec.get('codex_quota') if isinstance(rec, dict) else None
    c = q.get('reset_clock') if isinstance(q, dict) else None
    return c if isinstance(c, str) and re.fullmatch(r'[0-2]\d:[0-5]\d', c) else ''


def _claudebot_session_of(rec):
    """claudebot 席綁的 Claude 對話 id:橋的 status.json agent_id 為準 → 橋還沒寫出狀態時退回席位檔 claude_session_id。
    非 claudebot 回 None;是 claudebot 但量不到回 ''(= 沒綁)。
    10-09 @00000:以前席位檔優先。席位檔只在開席那一刻寫,席上 /unbind、/bind(10-07 起)只改 status.json
    ⇒ 開席就綁的席解綁或換綁後,右欄一直印舊那條。status.json 有 agent_id 這一欄(值可為 None = 解綁了)就信它。"""
    if not isinstance(rec, dict) or (rec.get('provider') or '') != 'claudebot':
        return None
    # 10-11 a seat(第 15 件第 1 步):判準搬到 bot_binding.bound_id,四家讀綁定走同一支(判法不變)
    try:
        import bot_binding
        return bot_binding.bound_id('claudebot', str(rec.get('session_uuid') or ''), rec)[0]
    except Exception:  # noqa: BLE001 —— 右欄量不到就當沒綁,不讓整欄掛
        return ''



# 10-07 a seat:橋接席「連著但跑不動」要在右欄看得見。橋把 health/last_error_kind 寫進自己的 status.json
#   (codexbot_bridge;state 仍 connected 是真的、連線確實在),右欄以前只看席位檔,於是 401、server 目錄不見、
#   鎖被握著的席整列全綠(10-07 00:0x 真機)。這裡只讀、只畫短字,判準在橋那一端(classify_error),不抄第二份。
_BRIDGE_HEALTH_SHORT = {
    'auth': '登入失效', 'server-cwd': 'server壞', 'server-down': 'server壞', 'disconnected': '斷線',
    'lock-held': '對話被占', 'thread-status': '對話未載入',
    'unbound': '沒綁對話', 'doorbell-expired': '門鈴過期',   # 10-07 @00000:claudebot/grokbot 沒綁對話(sand_bridge status_payload)
    'wake-failed': '門鈴失敗', 'not-collecting': '沒來收信',   # 10-07 @00000:grokbot 門鈴打不通、grokbot/musebot 久久不收信
    'no-doorbell': '沒接門鈴', 'no-hook': '沒裝hook', 'agent-missing': '名冊沒這隻',   # 10-11 @00000 P2:bot_ready 就緒檢查
    'no-mail-tool': '沒收信工具',
}
_BRIDGE_PROVIDERS = ('codexbot', 'sand', 'grokbot', 'claudebot', 'musebot')
_HEARTBEAT_PROVIDERS = ('sand', 'grokbot', 'claudebot', 'musebot')   # sand_bridge 每 2 秒寫一次心跳(HEARTBEAT_WRITE_S)
_HEARTBEAT_STALE_S = 120


def _health_word(kind, zh):
    """健康短字查四語鍵 aib.rightai.health.<kind>(語系盤查第 21 條);i18n_lib 載不到(例:測試把這一段單獨抽出來跑)
    ⇒ 用原本的繁中字。"""
    try:
        import i18n_lib as _i18n
        _v = _i18n.t('aib.rightai.health.' + kind.replace('-', '_'))
        return zh if _v.startswith('!!') else _v
    except Exception:
        return zh


def _bridge_health_text(rec):
    """橋接席的健康短字:health 是 degraded/down 才回(例「⚠登入失效」);健康、不是橋接席、讀不到 ⇒ ''(不畫,不猜)。"""
    if not isinstance(rec, dict) or (rec.get('provider') or '') not in _BRIDGE_PROVIDERS:
        return ''
    try:
        _home = os.environ.get('EZ2_AIB_STATE_ROOT') or os.environ.get('EZ2_AIB_HOME') or os.path.expanduser('~/.ez2-aib')
        with open(os.path.join(_home, 'bridge', str(rec.get('session_uuid') or ''), 'status.json'), encoding='utf-8') as _fh:
            _st = json.load(_fh)
    except (OSError, ValueError):
        return ''
    if not isinstance(_st, dict):
        return ''
    # 10-07 a seat(各 bot 比對):橋死了 status.json 還停在 alive/closing/connected,右欄照樣全綠
    #   (真機 10-07:3 隻 grokbot 卡 closing、1 隻 codexbot 卡 connected,行程都早就不在)。
    #   只用量得到的兩件事:pid 不在了;信箱橋(每 2 秒寫心跳)的 updated_epoch 超過 2 分鐘沒動。沒有這兩欄就不判。
    if _st.get('state') != 'stopped':
        _pid = _st.get('pid')
        if isinstance(_pid, int) and _pid > 0:
            try:
                os.kill(_pid, 0)
            except ProcessLookupError:
                return '⚠' + _health_word('gone', '已斷線')
            except OSError:
                pass
        _ue = _st.get('updated_epoch')
        if (rec.get('provider') or '') in _HEARTBEAT_PROVIDERS and isinstance(_ue, (int, float)):
            import time as _time
            if _time.time() - _ue > _HEARTBEAT_STALE_S:
                return '⚠' + _health_word('gone', '已斷線')
    if _st.get('health') not in ('degraded', 'down'):
        return ''
    _kind = str(_st.get('last_error_kind') or '')
    if _kind in _BRIDGE_HEALTH_SHORT:
        return '⚠' + _health_word(_kind, _BRIDGE_HEALTH_SHORT[_kind])
    return '⚠' + _health_word('unknown', '異常')



def model_of(uuid):
    """畫面上要顯示的模型名(the maintainer 09-05 令);量不到回 None ⇒ 畫面退三字碼。

    ★ 與 ctx_of 讀**同一個檔**,但刻意分成兩支函式:CTX 有 6h 的過期語意,
      模型名沒有(模型不會因為 statusline 停了半天就變成別的模型)。
      合成一支的話,兩個性質不同的值會共用一條過期判準,而那條判準只對其中一個對。
    ★ 規則正本在 src/cockpit/model_label.py,這裡只呼叫,不抄第二份。
    """
    path = os.path.join(os.environ.get('EZ2_CTX_DIR')
                        or os.path.expanduser('~/.ez2/ctx'), uuid + '.pct')
    try:
        with open(path, encoding='utf-8') as fh:
            return display_model(json.load(fh).get('model'))
    except Exception:
        return None


# ★ 09-07 the maintainer 定:登記那份 .pct 超過這麼久沒被寫,而且沒有 by-seat 橋,
#   就不再把那個數字畫出來(它凍在換對話 id 之前)。**10 分鐘是人定的門檻**,
#   不是量出來的——出處寫在這裡,將來有分布可以換掉它。
AWAITING_AFTER_S = 600


def _pct_age(uuid):
    """登記那份 .pct 距今幾秒;讀不到回一個很大的數(當成早該更新)。

    ★ 讀不到與「很舊」在這裡刻意同路:兩者都表示「這個數字不能拿來畫」。
      與 ctx_of 分開是因為那一支回的是 6h 過期語意(讀數老不老),
      而這裡問的是「寫入端還在不在寫這一份」。
    """
    if not uuid:
        return float('inf')
    path = os.path.join(os.environ.get('EZ2_CTX_DIR')
                        or os.path.expanduser('~/.ez2/ctx'), uuid + '.pct')
    try:
        return max(0.0, now - os.path.getmtime(path))
    except Exception:
        return float('inf')


def ctx_of(uuid):
    """CTX 實測(EZ2_CTX_DIR/<uuid>.pct);過期 6h 標 stale(舊版語意復用)。"""
    path = os.path.join(os.environ.get('EZ2_CTX_DIR')
                        or os.path.expanduser('~/.ez2/ctx'), uuid + '.pct')
    try:
        d = json.load(open(path, encoding='utf-8'))
        _obs = d['observed_at']
        _parsed = time.strptime(_obs[:19], '%Y-%m-%dT%H:%M:%S')
        # 時區同病(lifecycle 51982ab):hook 寫 UTC(Z 尾碼),mktime 當本地
        # 時解 → 差 8h → 全艦 CTX 恆帶 ?(stale)。Z 用 timegm,無 Z 當本地。
        import calendar as _cal
        if _obs.rstrip().endswith('Z'):
            age = now - _cal.timegm(_parsed)
        else:
            age = now - time.mktime(_parsed)
        return int(d.get('pct')), age >= 21600
    except Exception:
        return None, False

# 活窗集合:window_id -> @aib_role。shell 端已兩段式取值+整值驗證;
# 這裡對治具輸入做同一套驗證(defense-in-depth):壞 wid/髒 role=整行拒。
# rowmap 跳轉存 id 不存名(一輪審中1)。
live = {}
for line in (os.environ.get('EZ2_HUB_WINDOWS') or '').splitlines():
    parts = line.split('|')
    if len(parts) < 2:
        continue
    wid = parts[0].strip()
    if not WID_RE.fullmatch(wid):
        continue
    role_raw = parts[1].strip()
    if role_raw and clean_token(role_raw) is None:
        note('window %s role rejected (unlisted chars)' % wid)
        continue
    # 08-18 系統窗(`@aib_role=sys.*`,如 sys.watch=閒置收艙常駐窗)不是席:
    # 不進 live 就不會落到「活窗有、席位檔無」的 ❓+[X] 路徑——那個 [X] 的語意
    # 是「關掉認不出的東西」,對系統窗會變成一鍵關掉監看器。aib ROLE_RE 禁 `.`,
    # 人開的席不可能帶這個前綴,故不會誤隱藏任何隊友。
    if role_raw.startswith('sys.'):
        continue
    if wid not in live:
        live[wid] = role_raw

# BD:窗 → pane id(shell 同一次 list-windows 快照;治具 AIB_FAKE_PANES)。壞形狀整行丟。
pane_of = {}
for line in (os.environ.get('EZ2_HUB_PANES') or '').splitlines():
    parts = line.split('|')
    if len(parts) != 2:
        continue
    _pw, _pp = parts[0].strip(), parts[1].strip()
    if WID_RE.fullmatch(_pw) and re.fullmatch(r'%[0-9]{1,9}', _pp):
        pane_of.setdefault(_pw, _pp)

# 排序=the maintainer 08-13 拍板:艦長(root)→主管(pm)→開發(dev)→設計(design),
# 未列角色排最後按 started_epoch。權重資料驅動,不散落 if-else。
#
# ★ 08-21 the maintainer 直令改版:預設序由 `root pm dev1 dev2` 改為
#   `root pm dev design`。兩件事一起動,原因是同一個:
#   (a) `dev1`/`dev2` 是**兩個開發席**不是「開發與設計」,設計已獨立成
#       `design` 角色(見下方 ROLE_ICONS)。
#   (b) 舊寫法只認 `dev1`/`dev2` 兩個字面,所以 **`dev3` 以上今天就排錯**
#       ——`weight.get()` 把它們丟到與未列角色同權重(len(order)),沉到
#       段位最底。08-17 修過同族的圖示 fallback(dev3 戴 ❓),但排序這半
#       沒修。家族規則一次收掉兩邊。
#
# ★★ 08-21 the maintainer 直令(第二版,取代同日第一版的 dev-only 家族):
#   「rootN、devN、designN、qaN、monitorN 都對應同種 icon,不限數量」。
#   規則一般化成一句話:**尾端的 ASCII 數字是編號,不是語意。**
#   root/root2/root3 → root、pm/pm2/pm3 → pm、dev/dev12 → dev、
#   design2 → design、qa2 → qa、monitor2 → monitor。
#
# ★ 為什麼一般化比逐族列舉對:逐族列舉的失效形狀是「新編號悄悄掉進
#   fallback」——不會紅、不會報錯,只是那一席從此長得跟未列角色一樣。
#   本檔 08-17 就被咬過一次(dev3 戴 ❓),而當時只修了圖示沒修排序。
#
# ★ 只剝尾端數字,不做別的正規化:`devrel` / `devops` **不是** dev 家族
#   (它們尾端沒有數字,原字面就是家族名),所以不會被誤收。要讓某個新名字
#   歸進某族,是加一筆映射的產品裁定,不是靠字首猜。
def role_family(role):
    """角色字面 → 家族名(剝掉尾端的 ASCII 數字)。

    `role_family('root2') == 'root'`;`role_family('qa') == 'qa'`;
    全是數字的怪字面(如 '2')原樣回傳,不回空字串——空字串會悄悄命中
    別的鍵,而怪字面本來就該掉進 fallback 被看見。
    """
    if not isinstance(role, str) or not role:
        return role
    i = len(role)
    while i > 0 and role[i - 1].isascii() and role[i - 1].isdigit():
        i -= 1
    return role[:i] or role


order = (os.environ.get('EZ2_AIB_ROLE_ORDER') or 'root pm dev design').split()
weight = {r: i for i, r in enumerate(order)}


def role_rank(role):
    """段位權重;同家族的編號席全部並肩坐在家族那一格。

    ★ 精確字面優先於家族規則:呼叫端若用 EZ2_AIB_ROLE_ORDER 明寫
    `dev1`,那是他要把某一席單獨排開,不得被家族規則蓋掉。
    """
    if role in weight:
        return weight[role]
    fam = role_family(role)
    if fam in weight:
        return weight[fam]
    return len(order)

# 08-14 the maintainer 新令後,右欄名字欄不再顯示角色文字(角色語意交給 icon),
# 故 role_label() 已無呼叫點、整段移除(死碼不留,免下一棒誤以為還在用)。
# ★i18n 詞條 aib.role.* **不刪**:其他面板可能仍在用(全庫 grep 消費者後
# 再議),刪詞條與移除本地用法是兩件事。
tr = Translator()

rows, used_windows = [], set()
seats_dir = os.environ.get('EZ2_SEATS_DIR') or ''
# session 清單一次取得(幀內快取,不落狀態檔);掃描規則(兩源聯集、
# 為何不能只掃目錄)在 activity_probe.session_dirs 檔頭——**共用同一份**,
# 面板與殺前重驗不得各掃各的。
_proj = projects_dir()
_session_dirs = session_dirs(_proj)
# 席位人格綁定投影(binding_projection --display-out 的產物)。
# 幀內讀一次:每席各讀一次會在 24 席上做 24 次 IO,而且中途換檔
# 會讓同一幀裡不同席讀到不同版本。缺檔=空 dict,顯示層照舊降級。
_display = load_display()
for path in sorted(glob.glob(os.path.join(seats_dir, 'w*.json'))):
    base = os.path.basename(path)
    try:
        rec = json.load(open(path, encoding='utf-8'))
    except Exception:
        note('unreadable seat file skipped: %s' % base)
        continue                      # 壞檔=跳過不擋(rc0 no-op 紅線)
    if not isinstance(rec, dict) or rec.get('schema') != 'aib-seat/v1':
        note('seat schema mismatch skipped: %s' % base)
        continue                      # 一輪審中6:schema 欄驗證,壞形狀 fail-visible
    wid = str(rec.get('window_id') or '')
    if not WID_RE.fullmatch(wid):
        note('seat window_id malformed skipped: %s' % base)
        continue
    if base != 'w%s.json' % wid.lstrip('@'):
        note('seat filename/window_id mismatch skipped: %s' % base)
        continue                      # 一輪審中5:檔名與記錄內 window 交叉驗
    if wid not in live:
        continue                      # stale:窗已死,靜默不顯示(不靠心跳欄)
    if wid in used_windows:
        continue                      # 不唯一映射:首筆之後靜默 no-op
    used_windows.add(wid)
    # role 取源與 captains-ctl 對齊(一輪審中6):活窗的 @aib_role 優先,
    # 席位檔為後備。七輪審中4:語意照宣告落實——活窗有合法值就用它,
    # 席位檔 role 只是後備;**後備髒=只丟後備不殺席**(落 '?'=未編制❓,
    # fail-visible),不得因檔內髒 role 消滅一個活窗合法的席位。
    live_role = live[wid]
    rec_role_raw = rec.get('role')
    rec_role = clean_token(rec_role_raw) \
        if isinstance(rec_role_raw, str) else None
    if rec_role_raw is not None and rec_role is None:
        note('seat role invalid, backup dropped: %s' % base)
    role = live_role or rec_role or '?'
    rec_provider = rec.get('provider')
    if rec_provider is not None and clean_token(rec_provider) is None:
        note('seat provider rejected (unlisted chars): %s' % base)
        continue
    provider = rec_provider or '?'
    # F1 艦隊歸屬(the maintainer 08-18 方案 B):optional fleet_id=分組鍵。髒欄與
    # provider 同紀律**整筆拒**(它決定這一席掛在哪支艦隊下,錯掛比不顯示
    # 更糟);缺欄=退回 provider(向後相容:今天沒有這個欄的艙,畫面與
    # 改動前逐位元組相同,由 F1 交付時的 diff 實證+測試鎖住),provider
    # 也缺才落 '1'(預設第一艦隊)。
    rec_fleet = rec.get('fleet_id')
    if rec_fleet is not None and clean_token(rec_fleet) is None:
        note('seat fleet_id rejected (unlisted chars): %s' % base)
        continue
    fleet = rec_fleet or (provider if provider != '?' else '1')
    seat_id = rec.get('seat_id')
    # 三輪審高1:seat_id 含白名單外字元=整筆拒;缺席/欄位缺=誠實 '-----'
    # (缺席不是惡意);字元乾淨但形狀不合([0-9a-z]{4,8})也落 '-----'。
    if seat_id is None:
        seat_id = '-----'
    elif clean_token(seat_id) is None:
        note('seat_id rejected (unlisted chars): %s' % base)
        continue
    elif not re.fullmatch(r'[0-9a-z]{4,8}', seat_id):
        seat_id = '-----'
    # 08-14 the maintainer 新令:名字欄改顯示**綁定的人格**,沒綁就不顯示任何東西
    # (角色文字與 %02d 編號同令移除,見下方 name 組成)。識別子沿用 E0.5
    # 角色模型既有的 persona_card 欄(src/identity/role_model.py:239 綁定層),
    # **不長第二套人格識別**;席位檔此欄為選用,缺欄=不顯示不是錯誤。
    # 消毒走 clean_text 不走 clean_token:人格名可能是中文,ASCII 白名單會
    # 誤殺;髒值=只丟這一欄不殺席(fail-visible)。額外拒含 '@' 的值——
    # 否則「假人格@他人五碼」會在畫面上冒充別席身分。
    # 取值順序:投影優先,席位檔 persona_card 次之。投影是綁定層的
    # 唯一下游;席位檔那欄留著是為了相容,不是第二個真相源——
    # 24 份席位檔今天一個都沒有那個欄位,那條路徑實質是死的。
    persona = persona_of(rec, _display)
    persona_folder = folder_of(rec, _display)
    project_title = project_title_of(rec)
    if rec.get('persona_card') is not None and not persona:
        note('seat persona_card invalid, hidden: %s' % base)
    # 三輪審中1:epoch 有限性逐筆檢查——NaN/±Infinity 單席跳過不炸整板
    last_ok, last = finite_epoch(
        rec.get('last_used_epoch') or rec.get('started_epoch') or now, now)
    started_ok, started = finite_epoch(rec.get('started_epoch') or now, now)
    if not (last_ok and started_ok):
        note('seat epoch not finite, skipped: %s' % base)
        continue
    # 十六輪審1:非正 started=沉底(與 bar 同判準 9999999999)——負值若直接
    # 入排序會排最前搶冠,兩面判準統一
    if started <= 0:
        started = 9999999999.0
    age = max(0.0, now - last)
    # 第四刀:席位五碼對 session uuid(頭或尾);單一命中才綁,多重=不綁
    # (誠實)。綁到就升級三個真值:CTX 實測、💤 依 session 正本 mtime、
    # 子代理樹——第二刀的誠實降級(--/固定空)就此解除記界。
    # 最終批:session_uuid 欄優先(合法=直接綁);壞值 note 忽略退 fallback。
    # ★整條鏈走共用入口(第三艦長二退的教訓:單一真相源要涵蓋
    # 視窗→席位→引擎→session→idle **整條鏈**,不是只涵蓋最後一格 idle
    # 計算)。先前面板有五碼 fallback 而探針沒有,於是面板亮 [X] 的席位在
    # 殺前重驗被判 no-signal——按了沒反應(w2/37a0b 實案)。
    sess, _sess_note = resolve_session(rec, _session_dirs)
    if _sess_note:
        note('%s: %s' % (_sess_note, base))
    # D41(09-06):登記 uuid 只在開席寫一次;/clear 後 hook 以登記五碼發布
    # by-seat。instance/window 不符或檔缺都保留登記 uuid,不再讀會跨艙撞號的 by-pane。
    sess, session_rebound, _reb_note = resolve_live_session(
        sess, ctx_root=os.environ.get('EZ2_CTX_DIR'),
        instance=os.environ.get('EZ2_AIB_INSTANCE'), window=wid,
        since_ms=(started * 1000.0) if started < 9999999999.0 else None,
    )
    if _reb_note:
        note('%s: %s' % (_reb_note, base))
    # ★ 09-07:沒有橋 ⇒ 下面的 pct 讀的是**席位檔登記 uuid** 那一份,而它凍在
    #   /clear 或 compact 換掉對話 id 的那一刻。畫成數字會與真值同形,所以要標。
    # grok 沒有 statusline hook 寫 .pct(claude/agy/codex 才有)。
    # 探針把 CLI 自己的 signals.json 寫進同一把量尺,ctx_of 仍是唯一讀端。
    if sess and provider == 'grok':
        grok_ctx_refresh(sess, cwd=rec.get('cwd'))
    # copilot 也沒有 statusline 寫端:探針從它自己的 session-store.db 讀「這一回合真的
    # 打到哪個模型」寫進同一把量尺。pct 從 events.jsonl 的 usage_record(inputTokens/maxPromptTokens,
    # 10-11)算;量不到就寫 None(不編),模型是觀測值。
    if sess and provider == 'copilot':
        copilot_ctx_refresh(sess)
    # Native Codex can run without a statusline hook (including fresh cabins).
    if sess and provider == 'codex':
        codex_ctx_refresh(sess)
    # agy 的 statusline 9-21 起不再寫 .pct,而且換模型會另開對話 id:探針從那個行程
    # 自己的日誌讀「現在選的模型」寫進同一把量尺(model 沒變就不寫)。
    if sess and provider == 'agy':
        agy_model_refresh(sess)
    # ★ 09-05(the maintainer「新開席老是無法正確辨識」):缺 uuid 的席在這裡每 30 s 補一次偵測。
    #   身分產物(rollout / presence lock / usage)在第一次互動之後才出現,開席鏈的
    #   5 次重試全在那之前,之後只剩 watch 每 300 s 一輪 —— 於是新席最多 5 分鐘顯示
    #   `-- 💤--`。節流戳記與判準都在 seat_uuid_backfill,這裡只呼叫。
    if not sess:
        maybe_redetect(rec, os.environ.get('EZ2_AIHUB_SOCKET'),
                       os.environ.get('EZ2_COCKPIT_STATE_DIR'), now=now)
    pct, ctx_stale = ctx_of(sess) if sess else (None, False)
    #   ★ 條件要**兩個都成立**,不是只看橋在不在:
    #     「橋不在」本身是合法狀態 —— 登記 uuid 就是現任時,那個值是新的、是對的
    #     (test_b 釘的正是這條:沒有 by-pane 檔 ⇒ 讀登記 uuid,與修法前同路)。
    #     我第一版只判「橋不在」就標,把那一格弄紅了 —— **條件比要守的性質寬一圈**。
    #   ⇒ 條件收窄成「橋不在 **且** 登記那份已經 AWAITING_AFTER_S 沒被寫」。
    #   ★ 門檻 **10 分鐘是 the maintainer 09-07 直接定的**,不是量出來的分布 ——
    #     我沒有「一個活席最久可以多久不寫 .pct 仍屬正常」的讀數,所以這個數字
    #     的出處必須寫在這裡,下一個人才知道它可以被一個更好的量測取代。
    #   ★ 刻意不用 ctx_of 那把 6h 尺:它守的是「這個讀數老不老」,
    #     而這裡問的是「這個讀數是不是別人的」——兩個性質不同,共用一把尺會
    #     讓 clear 之後的席整整三個半小時繼續顯示一個凍住的數字(實測 57b15 2.5h)。
    #   ★ 回復多快:那一席**一有動作** → claude 重繪 → statusline 同時寫 .pct
    #     與 by-seat 橋 → 右欄下一輪 poll(:1465 `sleep 5`)就讀到 ⇒ **最多 5 秒**。
    _no_bridge = not bridge_present(
        rec.get('session_uuid'), ctx_root=os.environ.get('EZ2_CTX_DIR'),
        instance=os.environ.get('EZ2_AIB_INSTANCE'), window=wid)
    ctx_awaiting = bool(_no_bridge and _pct_age(sess) >= AWAITING_AFTER_S)
    model_name = model_of(sess) if sess else None
    # L9:觀測到的模型 ≠ 開席要求的模型 ⇒ 傳一個布林給渲染器(它零相依,不自己判)。
    # ★ 判定正本是 model_label.model_drift_state();這裡只呼叫,不抄第二份規則。
    # ★ 三態只有 DRIFT_DIFFERENT 會變 True:unknown 傳 None ⇒ 不標。
    #   現查 114 個席位檔有 62 個沒有 model_requested,把 unknown 當成 drift
    #   會讓過半的席掛上一個假記號,而永遠亮著的記號不帶資訊。
    _drift = model_drift_state(rec.get('model_requested'), model_name, provider)
    model_drift = True if _drift == DRIFT_DIFFERENT else None
    # ★ 子代理要去**那一席自己的**專案目錄找,不是去 EZ2_AIB_ROOT 推出來的
    #   那一個。專案目錄由 session 的 cwd 決定,而席位不一定開在 repo 裡。
    #   2026-08-16 實案:艦長席 cwd 是家目錄,派了兩個子代理一個都沒顯示,
    #   同艙另外三席(cwd=repo)卻正常——**部分席看得到比全部看不到更難發現。**
    if not sess:
        subs = []
    elif provider == 'codex':
        # codex 的子 thread 住在 rollout 檔裡,不在 claude 的 projects 目錄下
        subs = collect_codex_subs(sess)
    elif provider == 'grok':
        # 成因一(掃錯樹):grok 不得掉進 else 去 collect_subagents(claude projects)。
        # 空陣列與「沒派過」逐字相同,右欄不會紅。
        subs = collect_grok_subs(sess, cwd=rec.get('cwd'))
    else:
        subs = collect_subagents(project_dir_for(sess, _proj), sess)
    # 只有各引擎的真活動跡證算真閒置(多引擎探針,見 activity_age 檔頭)。
    # ★08-15 the maintainer 直令改判:取不到訊號時**不再沿用席位檔時間**。
    #   舊行為(「誠實後備」)其實不誠實:席位檔的時戳是自報的開席/選用時間,
    #   拿它當閒置時間,面板就會對一個**正在密集工作**的席位印 💤21h。
    #   08-15 實例:the maintainer 螢幕上 `a seat 💤21h`,而那一席當下正在跟指揮席對話。
    #   最刺眼的是它與**相鄰兩行**自相矛盾——同一個「無訊號」狀態下,
    #   CTX 走 `pct=None` 誠實顯示 `--`,💤 卻照樣印一個編出來的數字。
    #   現在兩者判準對齊:拿不到真活動跡證 → age_minutes=None → 渲染 `💤--`。
    #   ([X] 資格本來就只認 _true_idle,不受影響:破壞性入口從一開始就沒退。)
    _true_idle = activity_age(provider, sess)
    age = _true_idle          # None = 無訊號,顯示層印 `--` 不編數字
    # E2.19 四裁:optional 布林欄 advisor/captain_assigned——嚴格 is True
    # 才認;非布林真值=note 忽略欄不殺席(欄位壞不是席位壞)
    for _opt in ('advisor', 'captain_assigned'):
        if rec.get(_opt) not in (None, False, True):
            note('seat %s not boolean, ignored: %s' % (_opt, base))
    rows.append((role_rank(role), started, role, {
        'mark': '❓',             # 佔位;排序後依段位/艦長序補實(見下)
        'advisor': rec.get('advisor') is True,
        'captain_assigned': rec.get('captain_assigned') is True,
        # 段位主張是**哪裡來的**:活窗 @aib_role 由 aib 開席時寫進 tmux,
        # 不是席位檔自報;席位檔可能認不出來,但窗還在 tmux 手上。
        # 未編制的席能不能留在艦長段位,靠這個欄分辨(見下方 _caps 選取)。
        'role_from_live': bool(live_role),
        # 09-14:角色字面也落進 rowmap(開席成功判準②「角色」要機器讀得到;
        # 畫面上角色只以圖示 mark 出現,mark 是段位算出來的,不等於角色)。
        'role': role,
        'provider': provider,
        'fleet': fleet,               # F1 分組鍵(fleet_id→provider→'1')
        'engine': ENGINE_LETTERS.get(provider, '--'),
        # 09-05:模型名(the maintainer 令三字碼改顯示模型)。None ⇒ 渲染器退回三字碼。
        'model': model_name,
        # L9:True=觀測≠要求;None=問不出來(不標)。渲染器只認 True。
        'model_drift': model_drift,
        # 09-14(the maintainer 18:5x「CPT 還是沒有辨識出 AI 模型」):copilot 的模型只在第一回合之後才
        # 觀測得到(assistant_usage_events 每回合一列),開席到第一句話之間結構上量不到。
        # 這裡把**開席要求**的模型另外帶一欄(不冒充觀測值 'model'),渲染器在觀測值缺時
        # 畫「要求◌」並掛圖例——與 ≠(觀測≠要求)是同一組三態的第三態。
        'model_requested': (display_model(rec.get('model_requested'))
                            if isinstance(rec.get('model_requested'), str) else None),
        # the maintainer 08-14 拍板:顯示層不加 ~ 前綴。資料層 trust 恆 self_reported
        # 不變、verified 字樣在 EH 前不可能出現的紅線不變——席位檔就算
        # 自稱 verified,顯示與 rowmap 也不會長出 verified(一輪審中6 語意
        # 由「全輸出無 verified」斷言承載,不再靠 ~ 記號)。
        # 08-14 the maintainer 新令:名字欄=**綁定的人格**,沒綁就不顯示任何東西
        # (取代同日「角色文字+二位數編號」;連帶 the maintainer 前令的待裁點「單席
        # 恆編 01」自此作廢,別再去實作一個已不存在的東西)。角色語意全部
        # 交給 icon 承擔(👑⚓🎓👔🎨),這是本改動能成立的前提。
        # 形狀:有人格=「人格@五碼」,無人格=「@五碼」(不留佔位不留空白)。
        # 三行制(the maintainer 08-15 拍板):第一行只留身份,人格移到第二行。
        # 沒綁人格時仍是「@五碼」,與 08-14 的形狀一致(不留佔位)。
        # ★★ EH.17 階段 2「換顯示」:標籤走 seat_address.label_of,**不自己
        #   拼 '@' + seat_id**。the maintainer 08-22 眼見「右欄還顯示舊碼」,根因就是
        #   這一行 —— 投影端與 seat_persona 讀端在 3b-i 都換源了,只有畫面
        #   這一端沒換,而畫面正是唯一有人每天在看的那一端。
        #   **換源的消費者名單住在記憶裡,不在機器上**;現在它釘在
        #   tests/test-pane-right-ai.py::RightColumnLabelIsTheNewCodeTest。
        # ★ allow_legacy=True(預設):無 session_uuid 的席補位發舊碼**並出聲**,
        #   用的是同一個 LEGACY_MARK 計數器 —— 那個計數器正是翻階段 4 的閘,
        #   所以「畫面還在印舊碼」也會擋住翻頁。對的。
        #   反過來靜音的成本是那幾席在畫面上整個消失,而那不會有任何紅燈。
        'name': seat_address.label_of(rec),
        'bot_session': _claudebot_session_of(rec),   # v0.70.0 #15;非 claudebot 席恆 None
        'codex_quota_line': _codex_quota_line(rec),  # v0.70.0 #11/#10(艦長 21:55):@00000 寫的席位檔 codex_quota
        'codex_quota_clock': _codex_quota_clock(rec) if _codex_quota_line(rec) else '',
        'bridge_health': _bridge_health_text(rec),   # 10-07:橋接席 status.json health 不是 ok ⇒ 名字後接「⚠原因」
        'persona_display': persona or '',
        # EH.4:這張卡是誰生的(human/auto/created;**缺=未標示,不是 human**)。
        #   走 seat_persona 既有的公開入口 load_display() 取同一份投影,
        #   不另外開一條讀檔路徑。取不到就是 None,顯示層照「未標示」畫。
        # EH.17 3b-i:投影的鍵已換源到 seat_address.code_of,所以這裡也要用同一支
        #   算 —— **不可以自己再寫一份取法**(全庫唯一末五碼取法在 seat_address)。
        #   拿不到碼時退回 seat_id,與投影端的補位同形,兩邊才 join 得起來。
        # EH.17 3b-i:投影查表走 seat_persona.display_entry —— 面板**不自己
        #   查表**,否則遷移期雙認就會有一份沒套到(第三個消費者正是這裡,
        #   而第一版我只改了它、漏了另外兩個)。
        'persona_source': (display_entry(rec, _display) or {})
                          .get('persona_source'),
        # 三行制第三行的 📁(設計正本 docs/reports/three-line-rows-
        # 20260815/)。全文給到這裡,截到幾欄由畫的人決定。
        'persona_folder': persona_folder,
        # 09-29 a seat:席位綁的專案卡標題(席位檔 project_uid/project_title);🧠 那一行優先畫它。
        'project_title': project_title,
        # L2-03:綁定入口的目標席(rowmap spans 帶 seat)。與第一行 @五碼同源;
        # 形狀不合(上面已退成 '-----')就不給——找不到席位檔的席沒有入口。
        'seat_id': seat_id if re.fullmatch(r'[0-9a-z]{4,8}', seat_id) else '',
        'uuid_code': seat_address.code_of(rec) or '',
        'dm_queue': _q_by.get(seat_address.code_of(rec) or '') or [],
        # 09-11 the maintainer:另開 AI 選掛在哪位艦長底下。缺/髒=顯示層預設第一艦長。
        'reports_to': (rec.get('reports_to')
                       if isinstance(rec.get('reports_to'), str)
                       and re.fullmatch(r'[0-9a-z]{4,8}', rec.get('reports_to') or '')
                       else ''),
        'trust': 'self_reported',
        'ctx_percent': pct,
        'ctx_stale': ctx_stale,
        'session_rebound': session_rebound,   # BD:CTX 讀的是 pane 現任 session,非登記 uuid
        'ctx_awaiting': ctx_awaiting,        # 09-07:沒有 by-seat 橋 ⇒ 那個數字是凍的
        # None = 無真活動訊號(渲染 `💤--`);有值才是實測分鐘數
        'age_minutes': None if age is None else int(age // 60),
        # C1.3:等回執那一列要說對方有沒有在動;秒級,同一個探針值,不另量
        'activity_age_s': None if age is None else int(age),
        # ❓未編制三條件(the maintainer 08-15 直令);None=可驗證。判定在 seat_status,
        # 圖示覆寫在下面段位/艦長序補實之後(❓ 必須贏過 👑⚓🎓 與角色圖示,
        # 否則「無法驗證的席」會繼續戴著它自報的頭銜)。
        # ★ 合入解法(08-16 發版):高-3 移除了 child_source_uuid/has_children
        #   兩個參數((c) 在生產路徑不可達,理由見 seat_status 檔頭);A+B 這一
        #   側只是沿用舊簽名,不是刻意要那兩個參數。兩邊各取所需,不互相退讓。
        'unassigned': unassigned_reason(rec, base),
        # 顯示層專用(the maintainer 08-29):候偵測引擎的 NO_UUID 戴角色圖示不戴 ❓。
        'identity_pending': identity_pending(rec, base),
        # 開席單回執四態(A,08-16);判準同樣在 seat_status。上畫面的只有
        # 「投了沒回」與「停在舊版模板」兩態,見下方 ONBOARD_MARK 那一段。
        'onboard': onboard_reason(_ONBOARD_INDEX.get(seat_id), _ONBOARD_SHA),
        'started_epoch': rec.get('started_epoch'),
        'hub_window': wid,            # 一輪審中1:跳轉存穩定 window_id 不存名
        # 08-14 the maintainer 直令[X]+十九輪審高1:[X] 資格**僅限真相源**——
        # 對映成功且 session jsonl mtime 實測閒置>5min;對映不到/正本缺
        # =誠實不亮(kill 入口寧缺勿假,與子樹/CTX 同一降級哲學)。席位檔
        # self_reported 時戳只服務 💤 顯示,**不服務 kill 資格**(壞/舊/被
        # 竄的席位檔不得讓工作中活窗亮 [X])。
        # ★ 未編制席一律給 [X](the maintainer 08-16)。這**不是**放寬「無訊號不亮」
        #   那條紀律——兩者語意不同:一般席的 [X] 宣稱的是「這一席閒置夠久
        #   了」,無訊號時亮它等於編造閒置;而未編制席的 [X] 宣稱的是
        #   **「這東西系統認不出來,把它關掉」**,那句話不需要閒置訊號就成立。
        #   ★ 反過來說:身份修好之後 unassigned 變 None,[X] 就回到必須有
        #     真閒置訊號才亮——不會留下一個永久亮著的破壞性入口。
        #   ★★ 但**只在沒有活動訊號時**才給:既有測試
        #   test_fresh_transcript_beats_stale_seat_claim_for_x 抓到我第一版
        #   做過頭——一個認不出、但 transcript 新鮮(正在工作)的席位也被
        #   亮了 [X],那等於**對一個明顯活著的東西提供關閉入口**。
        #   規則是「認不出 **且** 沒有活動訊號」:_true_idle is None 代表
        #   量不到任何訊號(如 agy 這種 seat-detect 不支援的 provider),
        #   那才是「這東西系統認不出來、也看不出在做什麼,關掉它」。
        'idle_kill': (
            (bool(unassigned_reason(rec, base))
             and _true_idle is None)
            or (_true_idle is not None and _true_idle >= 300)),
        'subs': subs,
    }))
# ---- ❓未編制 (a) 未綁進 aib:活窗有、席位檔無或認不出 --------------------
# the maintainer 08-15 直令三條件的第一條。**這是一個顯示行為的改變**:在此之前,
# 沒有席位檔(或席位檔壞到上面每一道 skip 都攔下)的活窗**完全不出現在右
# 欄**——使用者看不到那個隊友存在,而「看不見隊友」正是這一刀要解的病
# (調度判斷建立在一份不完整的名單上,比建立在一份標記不確定的名單上更糟)。
# 上面所有 `continue` 都不會把 wid 放進 used_windows,故壞席位檔與無席位檔
# 在此處自然合流成同一條路徑,不需要各自補一段。
for _wid in sorted(live, key=lambda w: int(w.lstrip('@') or 0)):
    if _wid in used_windows:
        continue
    used_windows.add(_wid)
    _role = live[_wid] or '?'
    rows.append((role_rank(_role), now, _role, {
        'mark': UNASSIGNED_MARK,      # 下面的段位補實會跳過 unassigned 列
        'advisor': False,
        'captain_assigned': False,
        # 引擎分組鍵:沒有席位檔就沒有 provider。用 '?' 而非 None——組序
        # `sorted(_groups, key=(rank, p))` 拿 None 跟 str 比會 TypeError,
        # 整支面板當場死掉(壞資料不得殺面板,rc0 no-op 紅線)。
        'provider': '?',
        'fleet': '?',                 # F1:無席位檔就無艦隊主張,不編派
        'engine': '--',
        'model': None,                # 沒有席位檔就沒有 session,量不到模型
        # 沒有席位檔就沒有人格、沒有五碼——唯一可驗證的事實只剩窗號,
        # 就顯示窗號(也正好是 the maintainer 與各席互相稱呼的方式)。不編假五碼。
        # v0.70.0 #15(the maintainer 09-27 10:43 截圖「❓ @41」):窗號後明說「非 AIB 席」,人一眼知道這是外開的視窗不是壞掉的席。
        'name': _wid + ' ' + tr.t('aib.rightai.not_aib'),
        'trust': 'self_reported',
        'ctx_percent': None,          # 無 session 可測
        'ctx_stale': False,
        'age_minutes': None,          # 無訊號 → `💤--`,不編閒置時間
        'hub_window': _wid,
        # 這一支是「活窗有、席位檔無或認不出」的 (a) 類,本身即未編制,
        # 故同上給 [X]:它是「關掉一個認不出的東西」,不是「宣稱它閒置」。
        'idle_kill': True,
        'subs': [],
        'unassigned': 'no_seat',
    }))

# ---- 整片 ❓ 時把「我看的是哪個抽屜」講出來(第一艦長 pm3 08-15 裁 (A))----
# ★這一刀新增了一個顯示形狀(整片 ❓),而那個形狀**有一種成因會被它自己
#   掩蓋**:席位目錄解析錯了(多 instance 環境:主艦隊 ~/.ez2-aib/seats 與
#   instances/<N>/seats 是不同的抽屜),於是每一席都「認不出來」——畫面說的
#   「這些我都驗不出來」字面上沒說謊,但真正的原因是「我在看錯的抽屜」,
#   而畫面上完全看不出這件事。**紅是對的,理由完全不對**,那種紅最難查。
#   08-15 實例:兩個席位同時把主艦隊的 15 席當成 aihub-7 的 5 席在讀。
#   純診斷:只留痕,不改任何顯示邏輯(走既有 note(),不新建第二條留痕路徑)。
#   不處理「為什麼會看錯抽屜」——那是另一個問題,不在本刀範圍。
#   ★留痕路徑要兩路併走,不能只靠 note():本檔的 python stderr 被導進暫存檔
#   (`2>"$_ai_out.err"`),而那個檔**只有在渲染失敗時**才被 sed relay 到真
#   stderr,成功時直接刪掉。也就是說**成功渲染時 note() 印的東西沒有人看得
#   到**——而這一則正好只在成功渲染時發生(畫面好好的、全是 ❓)。
#   成功時把 stderr 放出來不是解:面板每 5 秒重畫一次,那會把噪音直接噴進
#   AI pane 弄髒畫面(那正是當初導進暫存檔的原因)。
#   故照 mouse-ctl.sh 既有先例(它為了同一個病兩路併走 stderr + 側 log):
#   note() 照發保住慣例,另**附加**寫進 cockpit 狀態目錄的側 log,留得住。
if rows and all(r[3].get('unassigned') for r in rows) \
        and any(r[3].get('unassigned') == 'no_seat' for r in rows):
    # 08-21:兩路寫法抽成 note_log()(上方),此處改為呼叫——同一個病已經有
    #   第二個呼叫點(子代理缺檔/時戳在未來),兩份實作遲早分岔。
    note_log('全席未編制;實際掃描的席位目錄=%s'
             % (seats_dir or '(未設定 EZ2_SEATS_DIR)'))

rows.sort(key=lambda r: (r[0], r[1]))

# 艦隊 icon 字彙(E2.19 第三刀,the maintainer 直令)。正本引據=
# ~/.claude/ez2-fleet-board.py:380-381 SEATS/CAPTAIN_LABELS(repo 內重實作,
# 不 import 版控外檔):👑=I 艦長、⚓=多艦長第二位起、🤖=aibot 橋接席、
# 👔=主管、👤=組員、❓=未編制。★單 codepoint 紀律(the maintainer 舊拍板):複合 emoji 弄歪網格;
# 五枚 EAW 皆 W(雙格),行寬相等由測試看守。取代原固定 👤=誠實升級。
# dev2=🎨(U+1F3A8 調色盤,08-14 the maintainer 增量:👤 太通用,語意直指設計;單
# codepoint EAW W 過網格;✏️ 帶 VS16 序列違單 codepoint 紀律故不取,📐 語意
# 偏製圖)。dev1 維持 👤(未裁不動)。
# qa=🔍(U+1F50D 放大鏡=檢驗)、monitor=📡(U+1F4E1 天線=監看),08-17 the maintainer
# 直令(經 root-brain 派工、the maintainer 本窗核准):兩角色原本掉進 fallback ❓,
# 與「未編制」同號=語意撞車(合法 qa 席看起來像身份未驗)。兩枚皆單
# codepoint、EAW=W(python3 unicodedata 實測),行寬與其他五枚相等;
# 測試由 test_icon_grid_width_machine_criterion 讀本行映射逐枚看守。
# ★★ 08-21 the maintainer 直令(取代上面 08-14 增量1 的 dev2=🎨):
#   原文「dev1、dev2 或 devxxx 都應該是同一個 icon,把 design 獨立出來」。
#   - dev 家族(dev/dev1/dev2/devXXX)一律 🧞 U+1F9DE
#   - `design` 升為獨立角色,🎨 自此**只**給它,不再給 dev2
#   - 👤 U+1F464 **退役為「未列角色」的 fallback**,不再是任何角色的本位圖示
#     (08-17 那條「fallback 不得是 ❓」仍然成立——❓ 語意是未編制,兩義相撞
#     會讓已編制的新角色看起來身份有問題。所以 fallback 需要一枚**不同於任何
#     在職角色**的圖示,退役下來的 👤 正好是那枚。)
#   🧞 由 the maintainer 從人頭候選表挑選;單 codepoint + EAW=W 兩條紀律已用
#   python3 unicodedata 實測,並由 test_icon_grid_width_machine_criterion 看守。
ROLE_ICONS = {'pm': '\U0001F454', 'dev': '\U0001F9DE', 'design': '\U0001F3A8',
              'qa': '\U0001F50D', 'monitor': '\U0001F4E1',
              'advisor': '\U0001F393'}
UNLISTED_ROLE_ICON = '\U0001F464'  # 👤 未列角色 fallback(不得用 ❓)

# aibot 第三種段位圖示(the maintainer 2026-09-18 裁):sand/grokbot（Cursor）與 codexbot（Desktop） 橋接席戴 🤖,
# 不跟 👑/⚓ 搶身份主張。單 codepoint U+1F916、EAW=W,與 👑⚓🎓 同網格。
AIBOT_PROVIDERS = frozenset(('sand', 'grokbot', 'codexbot', 'claudebot', 'musebot'))
AIBOT_MARK = '\U0001F916'  # 🤖
# API-key 終端席(providers.json auth=api_key):次位艦長不用 ⚓(⚓=OAuth/訂閱艦隊次位語彙),
# 改戴 🔑。第一艦長仍走下方 _crown_given → 👑,與引擎無關。
# 鍵集必須跟 providers.json 的 auth=api_key 對齊;新加 API-key 引擎時兩處一起改。
API_KEY_PROVIDERS = frozenset(('deepseek', 'nvidia', 'openrouter', 'typesafe', 'openrouterjev'))
API_KEY_VICE_MARK = '\U0001F511'  # 🔑


def role_icon(role):
    """角色 → 圖示。精確字面 > 家族 > 未列角色 fallback。

    `root` 家族不在本表:艦長圖示是**身份主張**(👑 現任 / ⚓ 次位),由
    下方段位邏輯發放,不能在這裡按角色發——那會讓任何寫得出席位檔的東西
    自封艦長(pane-right-ai.sh:594 那道閘的理由)。
    """
    if role in ROLE_ICONS:
        return ROLE_ICONS[role]
    fam = role_family(role)
    if fam in ROLE_ICONS:
        return ROLE_ICONS[fam]
    return UNLISTED_ROLE_ICON
# 十五輪審高2:艦長判定與 bar 同源——讀同一 env(預設 root 不變),
# 不再硬編;兩面不一致=qa 設為艦長時右欄不掛冠而 bar 掛,實測抓到的。
CAPTAIN_ROLES = set((os.environ.get('EZ2_AIB_CAPTAIN_ROLES') or 'root').split())


def is_captain_role(role):
    """艦長家族判定(08-21 同令:rootN 一律屬 root 家族)。

    ★ 這裡放寬的是**段位與圖示**,不是權限:`root2`(第二指揮席)本來就是
    艦長級,舊寫法只認裸 `root`,所以 root2/root3 會沉到未列角色去。
    ★ 沒有放寬的那道閘仍在:未編制(缺 session_uuid)的席**永不戴冠**
    ——那是身份主張,與家族無關。
    """
    return role in CAPTAIN_ROLES or role_family(role) in CAPTAIN_ROLES
# 顧問退位字母(08-14 the maintainer 兩增量②):席位 role → 大寫字母,root→R/pm→P/
# dev2(設計)→D/dev1(工程)→E;未列角色 fallback=role 首字母大寫
# (qa→Q,fallback 規則候 the maintainer 補裁)。
# ★ 08-21 同令調整:`D`(設計)隨 🎨 一起移給 `design`;dev 家族統一 `E`
#   (工程)。舊表把 D 給 dev2、E 給 dev1,在「dev2=第二個開發」之後那會
#   讓同為開發的兩席拿到不同字母,而其中一個還是「設計」的字母。
ADVISOR_LETTERS = {'root': 'R', 'pm': 'P', 'design': 'D', 'dev': 'E'}


def advisor_letter(role):
    """顧問退位字母;精確字面 > 家族 > 首字母大寫(qa→Q)。

    ★ fallback 取**家族名**的首字母不是原字面的:`qa2` 要回 Q,回 '2'
    是把編號當成語意。
    """
    if role in ADVISOR_LETTERS:
        return ADVISOR_LETTERS[role]
    fam = role_family(role)
    if fam in ADVISOR_LETTERS:
        return ADVISOR_LETTERS[fam]
    return (fam or role or '?')[:1].upper()

# ---- E2.19 單艦隊層級樹(08-14 the maintainer 產品令+四裁+排序定案) -----------------
# 段位序=the maintainer 原話「艦長/顧問/主管/設計」:現任+受派艦長(👑⚓ 平列,
# started 序,首位=現任)→顧問(🎓 獨立段位,advisor 欄,**永不成現任**)→
# 主管→開發→設計→未編制;子代理各掛所屬席位下(第三層)。
# 受派=captain_assigned is True:任何 role 以艦長身分排序顯示(👑⚓ 順位
# 照舊),unassign 回原 role 位;trust 語意不變(self_reported)。
# 艦長以下(顧問含)level=1 掛現任之下(渲染端加 ╰ 彎曲符);無現任艦長=
# 全員 level 0 平列+佔位列(誠實降級:不假造從屬,但「留下位置」明示可指派)。
# level/佔位只是顯示欄:rowmap 跳轉目標/detail 語意零變化;渲染端缺 level
# =0,無艦長且無佔位情境輸出與層級化前逐位元組同(向後相容鎖)。
# ---- 引擎艦隊分組(08-14 the maintainer 新裁「開出來的主AI也要掛艦長下」) ----------
# 引擎=艦隊分組鍵(多艦隊結構第一步,原記界提前兌現):CD 成員掛 CD 艦長、
# AG 掛 AG,跨引擎不混掛;各引擎首位活艦長=該艦隊樹根(組內 👑,次位 ⚓);
# 該引擎有成員無艦長=組頂佔位列(placeholder 偽列,沿用佔位件,不入 rowmap)
# +成員平列(誠實不假造從屬)。組內段位序照舊:艦長→顧問→主管→開發→設計。
# 組序=EZ2_AIB_ENGINE_ORDER(預設 claude codex agy copilot grok deepseek nvidia openrouter typesafe openrouterjev grokbot codexbot claudebot; sand 已退役；Cursor codexbot id 已刪),
# 未列引擎殿後按名。留白/樹軌規則不變(level 0 邊界天然成組間分隔)。
_ph_label = tr.t('aib.fleet.no_captain')
if not _ph_label or _ph_label.startswith('!!'):
    _ph_label = None
_eng_order = (os.environ.get('EZ2_AIB_ENGINE_ORDER')
              or 'claude codex agy copilot grok deepseek nvidia openrouter typesafe openrouterjev grokbot codexbot claudebot musebot').split()
_eng_rank = {e: i for i, e in enumerate(_eng_order)}

_groups = {}
for _r in rows:
    # F1(the maintainer 08-18 方案 B):分組鍵 provider→fleet。08-14 設計是多引擎
    # 混編時代的(引擎=艦隊);今天全艦同引擎,「第二/第三艦隊由第二/
    # 第三艦長管」靠 provider 永遠分不出來。fleet 欄上面已算好三段退回
    # (fleet_id→provider→'1'),沒有 fleet_id 的艙分組與改動前完全一致。
    _groups.setdefault(_r[3]['fleet'], []).append(_r)

_ordered = []
# 組序:EZ2_AIB_FLEET_ORDER(顯式艦隊序)>純數字艦隊(數值序,'10' 不得
# 排在 '2' 前)>引擎名(沿用 EZ2_AIB_ENGINE_ORDER——fleet 退回 provider
# 時鍵就是引擎名,兩個 env 各管各的鍵域,不合併:合併會讓「引擎序」被
# 拿去排數字艦隊,兩義相撞)>其餘按名殿後。全 provider 鍵時本函式退化為
# 舊 (rank, name) 序=向後相容。
_fleet_order = (os.environ.get('EZ2_AIB_FLEET_ORDER') or '').split()
_fleet_rank = {f: i for i, f in enumerate(_fleet_order)}
def _grp_key(k):
    if k in _fleet_rank:
        return (0, _fleet_rank[k], '')
    if k.isdigit():
        return (1, int(k), '')
    return (2, _eng_rank.get(k, len(_eng_order)), k)
# 08-14 the maintainer 催辦裁:👑=全域唯一,只給第一艦隊(組序首個有艦長的組)
# 首位;第二艦隊起艦長一律 ⚓——每組各發一頂皇冠會讓「現任」失義。
# F1 平移:此語意不動,只是「組」從引擎組變艦隊組。
_crown_given = False
for _prov in sorted(_groups, key=_grp_key):
    _g = _groups[_prov]
    # ★未編制的席**留在艦長段位,但戴 ❓ 而不是 👑/⚓**(第一艦長 pm3
    #   08-15 裁 (乙) 案;本席原本做的是「一律踢出段位」的 (甲) 案)。
    #
    #   為什麼 (甲) 錯:❓ 說的是**「這是誰」驗不出來**,段位說的是**「這個
    #   窗佔哪個位置」**——而這兩件事的證據來源不同。段位的 role 優先取
    #   **活窗 @aib_role**,那是 aib 開席時寫進 tmux 的,不是席位檔自報的;
    #   席位檔驗不出身份,不代表 tmux 記錄的位置也不可信。拿身份的缺口去
    #   撤銷一個獨立來源的位置主張,是把兩張帳算成一張。
    #   實務後果也證明 (甲) 錯:08-15 aihub 一條線的艦長卡死、由 a seat
    #   接替,而那一席當時缺 uuid——(甲) 會讓**正在接手指揮的人從艦長列
    #   消失**,恰恰違反本輪推翻舊契約時用的那條理由(活著的要看得見)。
    #
    #   但兩件事**不放行**:
    #   ①`captain_assigned` 是席位檔欄位,若段位主張**只**靠它(活窗沒說
    #     這是艦長角色),那主張與身份同源同壞,不得因此進段位。
    #   ②未編制者**永不戴皇冠**:👑=現任艦長,那是身份主張;排序也把未編制
    #     沉到可驗證者之後,好讓樹根是驗得出來的人(全部未編制才輪到它當
    #     樹根——那時「沒有可驗證的人在指揮」本身就是要看見的事實)。
    _caps = [r for r in _g if not r[3]['advisor']
             and (is_captain_role(r[2]) or r[3]['captain_assigned'])
             and (not r[3].get('unassigned')
                  or (r[3].get('role_from_live') and is_captain_role(r[2])))]
    _advisors = [r for r in _g if r[3]['advisor']
                 and not r[3].get('unassigned')]
    _crew = [r for r in _g if r not in _caps and r not in _advisors]
    # 組內艦長平列:**可驗證者優先**,其次 started 序,首位=樹根。
    # 未編制沉底不是懲罰,是讓樹根落在驗得出來的人身上(全部未編制時它仍會
    # 當樹根,那時該被看見的事實就是「沒有可驗證的人在指揮」)。
    # ★★ 受派艦長(captain_assigned)排在**依角色預設成為艦長**的席位之前。
    #    2026-08-16 the maintainer 指派 a seat 為第一艦長時發現:皇冠是按 started
    #    給的,而 a seat 開席較晚,於是「被指派的人拿不到皇冠」——
    #    **指派表達不出來,資深自動贏**。
    #    正確語意:`captain_assigned` 是一個**顯式的指派動作**,started 只是
    #    沒有人指派時的預設。顯式應該勝過預設,否則「指派」這個動作等於沒有。
    #    ★ 唯一的替代做法是改 started_epoch —— 那是**竄改開席時間**來達成
    #      顯示效果,會讓一個獨立事實變成裝飾,不可為。
    #    未編制仍排最後(它不戴皇冠也不戴錨,見下)。
    _caps.sort(key=lambda r: (bool(r[3].get('unassigned')),
                              not r[3].get('captain_assigned'),
                              r[1]))
    for _i, _r in enumerate(_caps):
        if _r[3].get('unassigned'):
            # 在段位裡,但不戴皇冠也不戴錨:👑/⚓ 都是身份主張,而身份正是
            # 驗不出來的那一半。**不消耗 _crown_given**——皇冠要留給後面
            # 可驗證的艦長,不能被一個驗不出來的席位吃掉。
            _r[3]['mark'] = (role_icon(_r[2]) if _r[3].get('identity_pending')
                             else UNASSIGNED_MARK)
        elif (_r[3].get('provider') or '') in AIBOT_PROVIDERS:
            # aibot 橋接席(sand/codexbot):第三種段位圖 🤖。
            # ★ 不消耗 _crown_given——🤖 不是身份主張的 👑/⚓,皇冠仍留給
            #   下一個可驗證的真人引擎艦長。
            _r[3]['mark'] = AIBOT_MARK
        elif not _crown_given:
            # 首位可驗證、非 aibot 的艦長戴 👑(不再用 _i==0:前面若排了
            # aibot,仍要把皇冠給第一個真人引擎艦長)。
            _r[3]['mark'] = '\U0001F451'
            _crown_given = True
        else:
            # 次位:API-key 席戴 🔑,其餘艦隊仍戴 ⚓(the maintainer 2026-09-20)
            _prov = (_r[3].get('provider') or '')
            _r[3]['mark'] = (API_KEY_VICE_MARK
                             if _prov in API_KEY_PROVIDERS else '⚓')
    for _r in _advisors:
        _r[3]['mark'] = '\U0001F393'      # 🎓 顧問(單 codepoint,EAW W)
        # 顧問名=「人格@末五 退位字母」;08-14 新令移除的是**角色文字**,
        # **退位字母 R/P/D/E 保留**——它不是角色詞,是「從哪條線退下來」的
        # 血緣標記(第三艦長裁:the maintainer 沒有裁它)。字母取源=_r[2] 與段位同源
        # (活窗 @aib_role 優先、席位檔後備;f0f13 核——字母與樹位必須同源,
        # 兩源分歧時不得「字母說 P 樹掛 D」)。
        _r[3]['name'] = '%s %s' % (
            _r[3]['name'],
            advisor_letter(_r[2]))
    for _r in _crew:
        # ❓ 贏過角色圖示:未編制=「系統無法驗證這是什麼」,不是它的角色。
        # 修好身份後 unassigned 變 None,圖示自動回到角色本位——**不需要人
        # 工改任何東西**(本判定無狀態、無側檔、無覆寫入口)。
        _r[3]['mark'] = (UNASSIGNED_MARK
                         if (_r[3].get('unassigned')
                             and not _r[3].get('identity_pending'))
                         else role_icon(_r[2]))
                        # 未列角色 fallback=👤 組員(08-17 the maintainer 實見 dev3 戴 ❓):
                        # ❓ 語意=未編制(seat_status),不得被「圖示表沒這個 role」
                        # 冒用——兩義相撞會讓已編制的新角色看起來身份有問題。
        # 08-14 the maintainer 新令:二位數編號整段移除(角色文字同令移除,見 name
        # 組成);組員名字與艦長、顧問一視同仁=「人格@五碼」。段位序與
        # 分組邏輯完全不動,只是不再改寫 name。
    _below = _advisors + _crew            # 段位內維持既有 (weight,started) 序
    if _caps:
        # 09-11 the maintainer:組員掛在指定艦長底下;缺 reports_to / 指向不在本組的
        # 席=第一艦長。第二艦長不再永遠被整隊擠到段尾。
        _kids = {}
        _first_sid = _caps[0][3].get('seat_id') or ''
        for _c in _caps:
            _kids[_c[3].get('seat_id') or ''] = []
        for _r in _below:
            _r[3]['level'] = 1
            _rt = _r[3].get('reports_to') or ''
            if _rt not in _kids:
                _rt = _first_sid
            if _rt in _kids:
                _kids[_rt].append(_r)
            else:
                _ordered.append(_r)
        for _c in _caps:
            _ordered.append(_c)
            _ordered += _kids.get(_c[3].get('seat_id') or '', [])
    elif _below:
        if _ph_label:
            _ordered.append((0, 0, '', {'placeholder': _ph_label,
                                        'name': ''}))
        _ordered += _below                # 無艦長組=平列,不假造從屬
# ---- ❓未編制一律沉到最下面(the maintainer 2026-08-16 螢幕直令)--------------------
# ★ 這**推翻** 08-15 pm3 的乙案(「❓ 留在段位只換戴 ❓」)。那條的理由與
#   事故我先向 the maintainer 完整回報過:❓ 說的是「這是誰驗不出來」,段位說的是
#   「這個窗佔哪個位置」,證據來源不同;而 08-15 aihub 一條線的艦長卡死由
#   a seat 接替時那一席正缺 uuid,乙案的反面(甲案)會讓**正在接手指揮的
#   人從艦長列消失**。the maintainer 聽過之後重申要沉到下面,故照辦。
#   ★ 已知代價**留在這裡不刪**:若哪天又出現「接手指揮但身份驗不出」的席位,
#     它會出現在最下面而不是艦長列。看到這一段的人請連同上面那個實例一起讀,
#     不要以為這只是排版偏好。
# 沉下來的席位一律 level 0(它已經不在任何艦長底下,不可再帶從屬縮排)。
# ★ 08-29 the maintainer 直令(接 pending-icon 同一裁):身份候偵測(identity_pending)的席
#   **留在艦長樹裡**,不隨未編制下沉——「還沒回填」不是「驗不出來」,同引擎同艦隊
#   的組員就該掛在同一個艦長底下。08-16 的沉底令對**真未編制**原樣有效,
#   上面那段代價註記照讀不刪。
_unassigned_rows = [r for r in _ordered
                    if r[3].get('unassigned')
                    and not r[3].get('identity_pending')]
if _unassigned_rows:
    for _r in _unassigned_rows:
        _r[3]['level'] = 0
    _ordered = [r for r in _ordered
                if (not r[3].get('unassigned')
                    or r[3].get('identity_pending'))] \
        + _unassigned_rows

rows = _ordered
no_captain_label = None                   # 佔位改組內偽列,全域欄不再用

# 標籤碰撞標記:佔位偽列不入標籤集(名字為空,不參與碰撞判定)
_label_rows = [r for r in rows if not r[3].get('placeholder')]
plain_labels = [row[3]['name'] for row in _label_rows]
display_labels = mark_colliding_labels(plain_labels)
label_collision = display_labels != plain_labels
for row, label in zip(_label_rows, display_labels):
    row[3]['name'] = label

# ---- 開席單未回執提示(A,2026-08-16)------------------------------------
# 掛在名字後綴,與顧問退位字母同一個既有位置,而且**在碰撞標記之後**才加
# ——碰撞判定比的是席位名字,把提示字塞進去會讓兩席「名字不同」而漏標。
#
# ★ 只標兩態,不標 ONBOARD_NEVER。從未投過的席位亮警示等於對使用者宣告
#   一件他無從處置的事(那是 `aib seat onboard --missing` 的目標),而且
#   任何還沒跑過新投遞路徑的既有安裝會**全席同時亮起來**——顯示層做過頭
#   的典型形狀。判準四態完整回報在 seat_status,要不要畫是這裡的決定。
#
# ★ 標記用單欄 ASCII:✉/⚠ 這類是 East Asian Width「模糊」字,同一個字在
#   不同終端佔 1 或 2 欄,整片網格會歪(⚫ 換 ● 那次實測的同一個坑)。
# ★★ 08-22:圖例沒注入就不標 —— 與 `~` 逐字同一條不變式
#   (標記與解釋它的圖例必須同時上畫面)。少了這個條件,畫面會出現一個
#   使用者查不到解釋的字元,而那正是這一批要修的東西本身。
_onboard_legend = (lambda _v: _v if _v and not _v.startswith('!!') else None)(
    tr.t('aib.legend.onboard_pending'))
_onboard_marked = False


def _onboard_span_text(seconds):
    """秒數 → 「3分51秒」/「45秒」;鍵缺就回 fail-loud 標記讓呼叫端整行退回舊文。"""
    if seconds >= 60:
        return tr.t('aib.onboard.wait.span_min', {'n': seconds // 60, 'sec': seconds % 60})
    return tr.t('aib.onboard.wait.span_sec', {'sec': seconds})


def onboard_wait_line(name, now_s, sent_ms, activity_age_s):
    """C1.3:等回執期間那一列的真話。三態各自具名、量不到印量不到;
    任何一個鍵缺(`!!key!!`)就回空字串,呼叫端退回舊的「等開席單回執」——
    載入器的 fail-loud 標記不得上畫面(與 legend 同一條)。"""
    st = onboard_wait_state(now_s, sent_ms, activity_age_s)
    if st['waited_s'] is None:
        waited = tr.t('aib.onboard.wait.sent_unknown')
    else:
        waited = _onboard_span_text(st['waited_s'])
    if st['activity'] == 'active':
        state = tr.t('aib.onboard.wait.active', {'sec': st['age_s']})
    elif st['activity'] == 'idle':
        state = tr.t('aib.onboard.wait.idle', {'since': _onboard_span_text(st['age_s'])})
    else:
        state = tr.t('aib.onboard.wait.activity_unknown')
    line = tr.t('aib.onboard.wait.line', {'name': name, 'waited': waited, 'state': state})
    if any(x.startswith('!!') or '!!' in x for x in (waited, state, line)):
        return ''
    return line


_load_now = now
try:
    _load_now = float(os.environ.get('EZ2_ONBOARD_LOADING_NOW') or now)
except (TypeError, ValueError):
    _load_now = now
def late_ack_clears_fail(ack_fail_why, acked_now, pane_dead):
    """第 78 項缺口:回執**晚到**時,舊的「等待逾時」不得留在新幀上。
    帳本裡現行模板的 message.inbound 已到(acked_now)⇒ ack_fail 那條理由作廢(顯示落後不是開席失敗,
    不叫人重試);**視窗真的死了照樣是死**(pane_dead 蓋過一切,那是另一個事實)。純函式。"""
    if acked_now and not pane_dead:
        return None
    return ack_fail_why


for row in _label_rows:
    _idx_row = _ONBOARD_INDEX.get(row[3].get('seat_id')) or {}
    _acked_now = bool(_ONBOARD_SHA) and _ONBOARD_SHA in (_idx_row.get('inbound') or set())
    _pane_dead = row[3].get('hub_window') in _DEAD_WINDOWS
    _oa_st = opening_attempt_state(
        _OPENING_ATTEMPTS.get(row[3].get('hub_window')) or [],
        ack_fail_why=late_ack_clears_fail(_ACK_FAILS.get(row[3].get('seat_id')), _acked_now, _pane_dead),
        pane_dead=_pane_dead)
    if _oa_st and _oa_st.get('mode') == 'opening' and _acked_now and not _pane_dead:
        # 碼表停在 ack 階段而回執其實已到(守望者比畫面慢):同一條開席事件鏈以帳本的 inbound 為準,不再畫「等回執」
        _oa_st = None   # 78-late-ack-on
    if _oa_st and _oa_st.get('mode') == 'failed' and _oa_st.get('fail') == 'timeout' and _acked_now:
        _oa_st = None   # 碼表自己記了 watch_timeout,但回執已到 ⇒ 不是失敗;窗死另計(上面 pane_dead 已蓋)
    if row[3].get('codex_quota_line'):
        # 只有一個 Codex 帳號時不自動換號(#11),靠這一句告訴人為什麼停了、幾點好。
        # A 包 r4 低(codex):窄欄會從尾巴截掉「HH:MM 重置」⇒ 有重置時刻就放在名字**前面**且用短形「額滿HH:MM」,
        #   沒有才照原句接在後面。
        _qc = row[3].get('codex_quota_clock') or ''
        if _qc:
            row[3]['name'] = tr.t('aib.rightai.quota_full', {'when': _qc}) + ' ' + (row[3].get('name') or '')
        else:
            row[3]['name'] = (row[3].get('name') or '') + ' · ' + row[3]['codex_quota_line']
    if row[3].get('bot_session') is not None:
        # v0.70.0 #15(the maintainer 09-27 10:43):claudebot 席沒有開席回執可回(回話的是綁定的 Claude 對話),
        #   「未確認 · 缺」是假警報 ⇒ 不畫;名字後印它綁哪條對話,沒綁明說。失敗/窗死照舊畫。
        if _oa_st and _oa_st.get('mode') in ('unconfirmed', 'opening'):
            _oa_st = None
        _bs = row[3].get('bot_session') or ''
        row[3]['name'] = (row[3].get('name') or '') + ' · ' + (tr.t('aib.rightai.bound_claude', {'id': _bs[:8]}) if _bs
                                                              else tr.t('aib.rightai.unbound_claude'))
    if _oa_st:
        _name = row[3].get('name') or ''
        if _oa_st.get('mode') == 'unconfirmed':
            # 09-14:右欄畫了這一席但五項沒齊 ⇒ 列上寫缺哪幾項(不假綠、不當失敗、不跳開席單選單)
            _miss = '/'.join(tr.t('aib.opening.missing.' + m) for m in (_oa_st.get('missing') or ['unreadable']))
            _line = tr.t('aib.opening.line.unconfirmed', {'name': _name, 'missing': _miss})
            if not _line.startswith('!!') and '!!' not in _line:
                row[3]['opening_attempt_text'] = _line
                continue
        if _oa_st.get('mode') == 'failed':
            _fail = str(_oa_st.get('fail') or 'timeout')
            _why = tr.t('aib.opening.fail.' + _fail)
            # 視窗已死:重投開席單救不回來,提示改成重開(09-11 @15 c0317)
            _line_key = 'aib.opening.line.dead' if _fail == 'pane_dead' else 'aib.opening.line.failed'
            _line = tr.t(_line_key, {'name': _name, 'why': _why})
        else:
            _stage_key = str(_oa_st.get('stage') or 'starting')
            _line = ''
            if _stage_key == 'ack':
                # C1.3:投了沒回那一段,列上寫投了多久、對方有沒有在動;逾時是 failed 那條路的事
                _line = onboard_wait_line(_name, _load_now, _ONBOARD_SENT_MS.get(row[3].get('seat_id')),
                                          row[3].get('activity_age_s'))  # C13-wait-truth-on
            if not _line:
                _stage = tr.t('aib.opening.stage.' + _stage_key)
                _line = tr.t('aib.opening.line.progress', {'name': _name, 'stage': _stage})
        if not _line.startswith('!!'):
            row[3]['opening_attempt_text'] = _line
            continue
    # v0.70.0 第 8/9 條(the maintainer 09-27 09:58):開席單沒回**不蓋列**;`*` 與「額度用完」走另開欄位
    #   name_suffix,渲染時接在名字後面。name 本身不動(open_right_check 對 name 完全比對)。
    _suffix = onboard_name_suffix(
        row[3].get('onboard'), _onboard_legend,
        ack_fail_why=_ACK_FAILS.get(row[3].get('seat_id')), acked_now=_acked_now,
        quota_text=(lambda _v: _v if _v and not _v.startswith('!!') else None)(
            tr.t('aib.onboard.quota_suffix')))
    if _suffix:
        row[3]['name_suffix'] = _suffix                     # O9-suffix-not-name
        if _suffix.startswith(ONBOARD_MARK):
            _onboard_marked = True
    if row[3].get('bridge_health'):
        # 10-07:走 name_suffix(後綴永遠看得到、name 不動,open_right_check 對 name 完全比對);接在開席單/額度後綴之後
        row[3]['name_suffix'] = (row[3].get('name_suffix') or '') + ' ' + row[3]['bridge_health']

# ★ 減 1:pane-fit.py:21 用 `EZ2_PANE_W - 1` 從**後面**裁並補「…」,而這裡
# 原本渲染到 EZ2_PANE_W——兩層差一欄,於是每一條剛好填滿的行都會被吃掉
# 最後一欄。第三行的資料夾是**尾巴承載語意**那一行(從前面截就是為了保住
# 尾端;五個角色資料夾前綴一樣,尾巴沒了就全部長一樣),所以那一欄正好
# 吃掉它要保住的東西。實測 W=26/28/30 尾巴都被切,只有 W=27 因為剛好多
# 一欄而倖免——**一個判準只在某些寬度上對,那是巧合不是正確**。
# 讓渲染端與裁切端用同一個可用寬度,而不是只補第三行:差一欄是全行的
# 性質,只修一行會讓下一行在別的寬度上重演。
width = max(8, int(os.environ.get('EZ2_PANE_W') or DEFAULT_PANE_W) - 1)   # 預設寬單源=render_lib.DEFAULT_PANE_W
# 真色能力偵測在採集端(渲染器純度:render_lib 不讀環境):COLORTERM 含
# truecolor/24bit=真色;EZ2_AIB_TRUECOLOR=0/1 強制(治具/黃金走 256)。
_ct = os.environ.get('COLORTERM', '').lower()
_tc_force = os.environ.get('EZ2_AIB_TRUECOLOR')
truecolor = (_tc_force == '1') if _tc_force in ('0', '1') \
    else ('truecolor' in _ct or '24bit' in _ct)
def _lamps_row():
    """第 49 項:三燈(等人裁/偏了/沒檢查)**常駐**右欄——不隨功能區開關、不隨頁簽藏(顧問 09-12 反對 §5-4,採)。
    數字單源=任務區產生器 ai_task_zone.lamps(seat)(第 66/74 項;None=量不到不是 0);seat=中間那位(state/pending-seat)。
    這裡只組字(i18n 在採集端,渲染器不讀 i18n);任何一步讀不到都回具名 why,不留白、不回 0。"""
    q = lambda v: "?" if v is None else str(v)
    none = {"human": None, "drifted": None, "unchecked": None, "measured_at": None}
    why = None
    lp = dict(none)
    state = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    seat = ""
    try:
        seat = open(os.path.join(state, "pending-seat"), encoding="utf-8").read().strip() if state else ""
    except OSError:
        seat = ""
    if not seat:
        why = tr.t('aib.lamps.why_no_seat')
    else:
        try:
            import feature_zone_source as _FZ
            mod, load_why = _FZ.load_generator()
            if mod is None:
                why = load_why
            elif not callable(getattr(mod, "lamps", None)):
                why = tr.t('aib.lamps.why_no_api')
            else:
                got = mod.lamps(seat) or {}
                lp = {k: got.get(k) for k in none}
        except Exception as exc:  # noqa: BLE001 —— 產生器炸掉不得讓右欄畫不出來;具名說出來
            why = "%s: %s" % (type(exc).__name__, exc)
    text = tr.t('aib.taskzone.c_lamps', {"n": q(lp["human"]), "count": q(lp["drifted"]), "total": q(lp["unchecked"])})
    if why is None and all(lp[k] is None for k in ("human", "drifted", "unchecked")):
        why = tr.t('aib.lamps.why_all_none')
    # ★ the maintainer 09-30 17:3x「這個不是早就隱藏了嗎」:只有真的有事等他裁(等人裁 ≥ 1)才畫這一行。
    #   偏了/沒檢查量的是派工表與未啟用的功能,不是他要處理的事;等人裁 0 或量不到 ⇒ 整行不畫,原因照舊進日誌。
    if not (isinstance(lp["human"], int) and lp["human"] > 0) and why is None:
        # ★ review r2 m10:等人裁**量不到**(None)不是 0 題 —— 舊版一律記「0 題」,日誌把量不到說成沒事。
        #   真的是 0 才用 human_none;量不到用既有的「等人裁:量不到:{why}」(產生器沒給原因 ⇒ 具名是哪一燈 None)。
        if lp["human"] is None:
            why = tr.t('aib.taskzone.gaps_human_unmeasured', {"why": "lamps(%s).human=None" % seat})
        else:
            why = tr.t('aib.taskzone.human_none')     # 沿用既有鍵(不新增 i18n 鍵)
    if not (isinstance(lp["human"], int) and lp["human"] > 0):
        # v0.70.0 #12(the maintainer 09-27 10:08 裁「好,藏」,反轉第 49 項「不留白」):三燈全量不到 ⇒ 整行不畫,
        #   原因只留日誌(state/lamps-why.txt,只記最新一句,不長大),不上畫面。量得到任何一燈才畫。
        try:
            if state:
                with open(os.path.join(state, "lamps-why.txt"), "w", encoding="utf-8") as _wf:
                    _wf.write("%d\t%s\n" % (int(now), why or ""))
        except OSError:
            pass
        return None
    if why:
        suffix = tr.t('aib.lamps.unmeasured', {"why": why})
    elif lp.get("measured_at"):
        try:
            suffix = tr.t('aib.lamps.age', {"n": max(0, int(now - int(lp["measured_at"])) // 60)})
        except (TypeError, ValueError):
            suffix = ""
    else:
        suffix = ""
    lit = any(isinstance(lp[k], int) and lp[k] > 0 for k in ("human", "drifted", "unchecked"))
    return {"text": (text + ("  " + suffix if suffix else "")), "lit": lit, "why": why,
            "human": lp["human"], "drifted": lp["drifted"], "unchecked": lp["unchecked"]}

def _pending_box_open():
    # 右欄則數跟待審區同進退:待審格有在畫才有 pending-rowmap。
    # 不在這裡問 tmux——右欄分頁的探針會把 list-panes 當成越權副作用。
    state = os.environ.get("EZ2_COCKPIT_STATE_DIR") or ""
    if not state:
        return False
    return os.path.isfile(os.path.join(state, "pending-rowmap.json"))

render_data = {
    # EH.4 圖例丁:把 `~` 的解釋注入渲染器。取自 i18n 的**單一鍵**;
    #   載入器的 fail-loud 標記(`!!key!!`)不得畫上畫面,所以先擋掉。
    #   ★ 不畫的後果是刻意的:寫端守衛就緒判準量的是「這段文字有沒有出現在輸出裡」,
    #     沒畫 = 未就緒 = source:created 的卡繼續被擋。標記與圖例必須同時上畫面。
    'legend': {'persona_created': (lambda _v: _v if _v and not _v.startswith('!!') else None)(tr.t('aib.legend.persona_created')),
               'onboard_pending': _onboard_legend,
               'model_requested': (lambda _v: _v if _v and not _v.startswith('!!') else None)(tr.t('aib.legend.model_requested'))},
    'onboard_marked': _onboard_marked,
    # AR-3:人格/大腦兩行的動作字面從 i18n 注入(渲染器不讀 i18n);
    #   載入器 fail-loud 的 `!!key!!` 不得上畫面,擋掉即退渲染器 DEFAULT_LABELS(zh-TW)。
    'labels': {_lk: _lv for _lk, _lv in (
        (_lk, tr.t('aib.seat.' + _lkey)) for _lk, _lkey in (
            ('persona_unbind', 'persona.unbind'), ('persona_bind', 'persona.bind'),
            ('brain_change', 'brain.change'), ('brain_bind', 'brain.bind'),
            ('unbound', 'unbound')))
        if _lv and not _lv.startswith('!!')},

    'agents': [r[3] for r in rows],
    'label_collision': label_collision,
    'no_captain_label': no_captain_label,
    'truecolor': truecolor,
    'pending_box_open': _pending_box_open(),
    # 第 49 項:三燈常駐(缺鍵=舊畫面;採集端一定給,治具與黃金幀不給就不畫那一行)
    'lamps': _lamps_row(),
}
# 週用量(L5b / 缺陷二十六):**活路徑必採**。舊碼把 EZ2_ONESHOT 當治具旗——活右欄每一幀都是
# ONESHOT(tabs.sh:36),於是整段 if 不進,與「真的沒資料」逐字同形,右下角永遠空白。
# EZ2_ONESHOT 的語意只准是「畫一幀退出」;治具要隔離家目錄就設 EZ2_USAGE_DIR 到夾具目錄。
# 採集丟例外 ⇒ 具名留痕 + 仍塞整個列網格(每列 collect_exception);永遠塞 dict,None 是 bug。
# ★ 缺陷三十九(root 223ae 09-06 12:4x;the maintainer 12:38「一直跳新驗證,幾百次」):入口幀起真 CLI worker 的
#   預設站在**失效側**——只有活艙 launcher 注入 EZ2_USAGE_PROBE_LIVE=1 才准起 agy/copilot/grok 真探針。
#   12:37 ps 現查兩個正在跑的 worker 都來自測試樹(pre-push 閘的 tests-only 樹、某席 tmp 樹),
#   真 PATH ⇒ 每跑一次入口臂(驗證 53 支、各席刀、閘)就拉起真 gemini/grok/copilot 撞登入牆,
#   瀏覽器驗證就彈一次。沙箱 HOME 洗得掉用量檔,洗不掉 PATH;能力要注入,不是靠治具想到才擋。
#   不帶旗標 ⇒ 被動讀檔照舊,三家探針列畫「量不到」,不起任何子行程。
_usage_probe_live = os.environ.get('EZ2_USAGE_PROBE_LIVE') == '1' and (os.environ.get('EZ2_RIGHT_VIEW') or '').strip() != 'usage'
# v0.70.0 #14(the maintainer 09-27 10:1x「AI用量 為什麼沒有 AGY 用量」):真艙 launcher 預設注入 EZ2_USAGE_PROBE_LIVE_ENGINES=agy,
#   只准 agy 一家起 headless `agy -p /usage`(不開 TUI、不彈登入),30 分鐘最多一次;copilot/grok 仍要 --probe-live 才起。
#   測試樹直跑本檔不帶這個變數 ⇒ 不起任何子行程(缺陷三十九的失效側不變)。
if not _usage_probe_live and (os.environ.get('EZ2_RIGHT_VIEW') or '').strip() != 'usage':
    _live_set = frozenset(e for e in (os.environ.get('EZ2_USAGE_PROBE_LIVE_ENGINES') or '').replace(',', ' ').split() if e == 'agy')
    if _live_set:
        _usage_probe_live = _live_set
try:
    usage_refresh_all(now=now, enable_cli=_usage_probe_live)
    _usage = usage_rows_for_render(now=now, tr=tr)
except Exception as _uexc:
    note_log('usage collect failed: %s: %s' % (type(_uexc).__name__, str(_uexc)[:120]))
    _usage = usage_rows_when_collect_failed(now=now, tr=tr, exc=_uexc)
# 用量頁一列都畫不出來時印的那一句(渲染器不讀字典,字在採集端配)。永遠塞:缺它渲染器印 '?'。
_usage['empty_text'] = tr.t('aib.usage.empty')
render_data['usage'] = _usage
# R6:徽章三態由採集端判(渲染器不讀環境)。即時 TTY 由 bash 包裝層 [ -t 1 ] 注入 EZ2_AIB_BADGE_TTY;
#   採集端 stdout 被重導到檔案,自己 isatty() 恆 False 不能拿來判。非 TTY / 字型缺字 ⇒ letters,缺字要出聲。
_badge = badge_inputs(icons=ENGINE_ICONS)
render_data['engine_badge_mode'] = _badge['mode']
render_data['engine_icon_available'] = _badge['icon_available']
if _badge['reason']:
    sys.stderr.write('engine-badge: %s\n' % _badge['reason'])
# 右欄兩行的顯示開關(the maintainer 2026-09-05)。★ 每一幀讀一次,不快取:
#   the maintainer 要的是「改了下一幀就變」,而這支採集端是 5 秒一輪的長駐迴圈——
#   把值快取在迴圈外就得重開艙才生效,那正好是他要避免的那件事。
# ★ 讀檔在**採集端**,渲染器不讀:render_lib 零環境相依是黃金幀決定性的前提
#   (與 engine_badge_mode / model 同一條紀律)。
# ★ 壞檔的告警不吞掉:值退回兩個都開,但每一幀都會在 stderr 說一句。
#   刷屏是刻意的——一個「我讀不到你的設定」的狀態如果只說一次,
#   看到的人正好是**沒在看**的那一輪。
_view, _view_warn = ai_view.load()
render_data['ai_view'] = {'role': bool(_view['role']), 'sb': bool(_view['sb'])}
if _view_warn:
    sys.stderr.write(_view_warn + '\n')
# 274 右欄艦隊收合+視窗(RL-030/032):讀檔在採集端(right-fold.json / right-scroll.json,無檔=全收合、offset 0),
#   渲染器只吃 dict。開關 EZ2_RIGHT_FOLD=1 才注入;預設關 ⇒ 畫面與 rowmap 與今天逐位元組相同((e)7;
#   合入後黃金幀 --update、the maintainer 眼驗過再把預設翻成開,那是一行)。
#   四語字面(摘要格式、兩顆鈕)也在這裡配好,渲染器不讀字典。epoch = 這一幀的秒級時戳((e)2 點擊前驗同一幀)。
if (os.environ.get('EZ2_RIGHT_FOLD') or '0') == '1':
    import right_fold_api  # noqa: E402
    _fold_dir = os.environ['EZ2_COCKPIT_STATE_DIR']
    _scroll_path = os.path.join(_fold_dir, 'right-scroll.json')
    try:
        with open(_scroll_path, encoding='utf-8') as _fh:
            _scroll = json.load(_fh)
    except (OSError, ValueError):
        _scroll = {}
    render_data['right_fold'] = {
        'enabled': True,
        'expanded': right_fold_api.fold_state.load(os.path.join(_fold_dir, 'right-fold.json')),
        'offset': right_fold_api.viewport.offset(_scroll),
        # 可用總高 = 面板高 − 上方固定列;席位樹那段的 H 由渲染器扣掉它自己畫的其他列後算
        'height': int(os.environ.get('EZ2_PANE_H') or 0) - int(os.environ.get('EZ2_ROW_OFFSET') or 0),
        'epoch': int(now),
        'summary_fmt': tr.t('aib.right.fold_summary', {'n': '{n}', 'm': '{m}', 'red': '{red}'}),
        'expand_all': tr.t('aib.right.expand_all'),
        'collapse_all': tr.t('aib.right.collapse_all'),
    }
# 右欄兩頁同一支採集端(the maintainer 09-12 15:3x 令:第 2 頁改「AI用量」、用量置頂、舊沙盒任務段收掉)。
# 採集、快取、rowmap 發布、@aib_pane 標記、drain-idle 與席位池盤點一行不抄第二份;
# 只有「畫哪一頁」由 pane-right-tabs.sh 用 EZ2_RIGHT_VIEW 指定:usage=用量頁,其餘=1.AI。
# ★ 不認識的值走 1.AI,不是靜默:採集端 stderr 說一句,免得拼錯字的頁永遠畫成席位樹而沒人紅。
try:
    import codex_pack as _codex_pack
    render_data['codex_pack'] = bool(_codex_pack.enabled())
except Exception:
    render_data['codex_pack'] = True
_view = (os.environ.get('EZ2_RIGHT_VIEW') or 'ai').strip()
if _view == 'usage':
    import usage_accounts as _ua
    import usage_subtabs as _ust
    _doc = _ua.load()
    _cur = str(_doc.get("subtab") or "usage")
    if _cur not in ("usage", "codex"):
        _cur = "usage"
    _frames = {}
    for _name in ("usage", "codex"):
        os.environ["EZ2_USAGE_SUBTAB_PREVIEW"] = _name
        _lines, _map = render_usage_view_with_map(render_data, width=width)
        _frames[_name] = {
            "header": getattr(render_usage_view_with_map, "body_start", None),
            "lines": _lines,
            "rowmap": _map,
            "required_width": required_usage_pane_width(render_data),
        }
    os.environ.pop("EZ2_USAGE_SUBTAB_PREVIEW", None)
    # 畫的過程可能補上帳號欄位並存檔。指紋要用存完之後的那份，不然點一下對不上、又整頁重查。
    try:
        _doc = _ua.load()
    except Exception:
        pass
    _cur = str(_doc.get("subtab") or _cur)
    if _cur not in ("usage", "codex"):
        _cur = "usage"
    _frame = _frames.get(_cur) or _frames["usage"]
    lines, rowmap = _frame["lines"], _frame["rowmap"]
    _body = _frame["header"]
    required_width = _frame["required_width"]
    if isinstance(_body, int):
        try:
            _cdir = os.environ.get('EZ2_COCKPIT_STATE_DIR') or ''
            if _cdir:
                _ctmp = os.path.join(_cdir, '.usage-frame-cache.json.tmp')
                with open(_ctmp, 'w', encoding='utf-8') as _cf:
                    json.dump({
                        'sig': _ust.accounts_sig(_doc),
                        'shown': _cur,
                        'width': width,
                        'frames': _frames,
                    }, _cf, ensure_ascii=False)
                os.replace(_ctmp, os.path.join(_cdir, '.usage-frame-cache.json'))
        except OSError:
            pass
    try:
        _total = int(os.environ.get('EZ2_PANE_H') or 0) - int(os.environ.get('EZ2_H_OFFSET') or 0) - 1
    except (TypeError, ValueError):
        _total = 0
    if isinstance(_body, int) and _total > 0:
        _scroll_off = 0
        _scroll_path = os.path.join(os.environ.get('EZ2_COCKPIT_STATE_DIR') or '', 'right-scroll.json')
        try:
            with open(_scroll_path, encoding='utf-8') as _fh:
                _scroll_state = json.load(_fh)
        except (OSError, ValueError):
            _scroll_state = {}
        try:
            from right_fold_api import viewport as _viewport
        except ImportError:
            from render.right_fold_api import viewport as _viewport
        _scroll_off = _viewport.tab_offset(_scroll_state, 'usage')
        _body_n = max(0, len(lines) - _body)
        _room = _total - _body
        _limit = max(0, _body_n - _room) if _room > 0 else 0
        try:
            _lpath = os.path.join(os.environ.get('EZ2_COCKPIT_STATE_DIR') or '', '.right-scroll-limit.json')
            try:
                with open(_lpath, encoding='utf-8') as _lf:
                    _limits = json.load(_lf)
            except (OSError, ValueError):
                _limits = {}
            if not isinstance(_limits, dict):
                _limits = {}
            _limits['usage'] = _limit
            _ltmp = _lpath + '.tmp'
            with open(_ltmp, 'w', encoding='utf-8') as _lf:
                json.dump(_limits, _lf)
                _lf.write('\n')
            os.replace(_ltmp, _lpath)
        except OSError:
            pass
        lines, rowmap = fit_usage_viewport(
            lines, rowmap, header_rows=_body, total_rows=_total,
            offset=_scroll_off, width=width,
            bar_arrows=True)
else:
    if _view != 'ai':
        sys.stderr.write(tr.t('aib.rightai.bad_view', {'value': repr(_view)}) + '\n')
    lines, rowmap = render_ai_list_with_map(render_data, width=width)
    required_width = required_ai_pane_width(render_data)
    raw_lines, raw_rowmap = list(lines), list(rowmap)
    # 沒開收合時也要能捲。上方的分頁和機器狀態在外層，這裡整段清單當正文。
    if (os.environ.get('EZ2_RIGHT_FOLD') or '0') != '1':
        try:
            from right_fold_api import viewport as _ai_viewport
        except ImportError:
            from render.right_fold_api import viewport as _ai_viewport
        _total = int(os.environ.get('EZ2_PANE_H') or 0) - int(os.environ.get('EZ2_ROW_OFFSET') or 0) - 1
        _ldir = os.environ.get('EZ2_COCKPIT_STATE_DIR') or ''
        _sstate = {}
        if _ldir:
            try:
                with open(os.path.join(_ldir, 'right-scroll.json'), encoding='utf-8') as _sf:
                    _sstate = json.load(_sf)
            except (OSError, ValueError):
                _sstate = {}
        _scroll_off = _ai_viewport.tab_offset(_sstate, 'ai') if isinstance(_sstate, dict) else 0
        _limit = max(0, len(raw_lines) - _total) if _total >= 1 else 0
        if _scroll_off > _limit:
            _scroll_off = _limit
            if _ldir:
                try:
                    _sp = os.path.join(_ldir, 'right-scroll.json')
                    _stmp = _sp + '.tmp'
                    with open(_stmp, 'w', encoding='utf-8') as _sf:
                        json.dump(_ai_viewport.with_tab(_sstate if isinstance(_sstate, dict) else {}, 'ai', _limit), _sf)
                        _sf.write('\n')
                    os.replace(_stmp, _sp)
                except OSError:
                    pass
        if _total >= 1:
            lines, rowmap = fit_usage_viewport(
                raw_lines, raw_rowmap, header_rows=0, total_rows=_total,
                offset=_scroll_off, width=width,
                bar_arrows=True, arrow_tab='ai')
            # OVERFLOW_TRACE: 切片後 pane-fit 看不到溢出。只在可見區下面還有列時
            # 換最後一行;往上藏的列交給箭頭。字面跟 pane-fit.py OVERFLOW_MARK 對齊。
            if lines and _total >= 1:
                _ns = {}
                for _ln in open(os.path.join(cockpit, 'pane-fit.py'), encoding='utf-8'):
                    if _ln.startswith('OVERFLOW_MARK'):
                        exec(_ln, _ns)
                        break
                _mark = _ns.get('OVERFLOW_MARK')
                _below = len(raw_lines) - (_scroll_off + _total)
                if _mark and _below > 0:
                    lines = list(lines)
                    lines[-1] = ' ' + tr.t('aib.rightai.overflow', {'mark': _mark, 'n': _below + 1})
        if _ldir:
            try:
                _lpath = os.path.join(_ldir, '.right-scroll-limit.json')
                try:
                    with open(_lpath, encoding='utf-8') as _lf:
                        _limits = json.load(_lf)
                except (OSError, ValueError):
                    _limits = {}
                if not isinstance(_limits, dict):
                    _limits = {}
                _limits['ai'] = _limit
                _ltmp = _lpath + '.tmp'
                with open(_ltmp, 'w', encoding='utf-8') as _lf:
                    json.dump(_limits, _lf)
                    _lf.write('\n')
                os.replace(_ltmp, _lpath)
            except OSError:
                pass
offset = int(os.environ.get('EZ2_ROW_OFFSET') or 0)
if offset:
    for row in rowmap:
        row['line'] += offset
# 1.AI 全量畫完才落快取。捲動時用還沒裁的那份重切，不必再查席位。
if _view != 'usage':
    try:
        _cdir = os.environ.get('EZ2_COCKPIT_STATE_DIR') or ''
        if _cdir:
            _ctmp = os.path.join(_cdir, '.right-page-ai.json.tmp')
            with open(_ctmp, 'w', encoding='utf-8') as _cf:
                json.dump({
                    'page': 'ai',
                    'width': width,
                    'pane_h': int(os.environ.get('EZ2_PANE_H') or 0),
                    'row_offset': offset,
                    'lines': lines,
                    'rowmap': rowmap,
                    'raw_lines': raw_lines if (os.environ.get('EZ2_RIGHT_FOLD') or '0') != '1' else [],
                    'raw_rowmap': raw_rowmap if (os.environ.get('EZ2_RIGHT_FOLD') or '0') != '1' else [],
                    'required_width': required_width if required_width <= 40 else 40,
                }, _cf, ensure_ascii=False)
            os.replace(_ctmp, os.path.join(_cdir, '.right-page-ai.json'))
    except (OSError, TypeError, ValueError, NameError):
        pass
state = os.environ['EZ2_COCKPIT_STATE_DIR']
# 二輪審中5:rowmap 只寫 pending(per-PID,一輪審中3),發布權在 shell——
# 畫面成功輸出後才 mv 成正式檔,畫面與落點不存在「新舊交錯」窗口。
pending = os.environ.get('EZ2_ROWMAP_PENDING') or \
    os.path.join(state, '.ai-rowmap.pending.%d' % os.getpid())
with open(pending, 'w', encoding='utf-8') as fh:
    json.dump(rowmap, fh, ensure_ascii=False)
with open(os.environ['EZ2_RIGHT_WIDTH_PENDING'], 'w', encoding='utf-8') as fh:
    fh.write('%d\n' % (required_width if required_width <= 40 else 40))
for ln in lines:
    print(ln)
try:
    if dm_queue.admit()[0]:
        subprocess.Popen(
            [sys.executable, os.path.join(cockpit, "dm_queue.py"), "drain-idle"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
except Exception:
    pass

# ── R6 席位池:唯讀盤點一格 occupied,落給 R2 路由讀(2026-09-05 接線)────────
# ★ 為什麼掛在這裡:派工端不在艙裡跑,問不到「哪一家現在有幾席佔著」;
#   採集端本來就每輪在讀艙,盤點是它已經在做的事的一個側讀。
# ★ **只落檔,不印行**:rowmap 與畫面同源,採集端多印一行會讓每一列的
#   點擊座標位移(點 A 跳 B)——與 L8b 通知同一條紀律。
#   放在 print 迴圈**之後**:這一幀的畫面已經送出去了,盤點再慢也不改畫面。
# ★ 節流在 seat_pool 裡(預設 30 秒):每幀盤一次等於把 render loop 的成本
#   乘上艦隊規模,而佔用數不會 5 秒變一次。
# ★ 任何失敗都不得擋畫面(rc0 no-op 紅線):整段包在 try 裡,失敗只留痕。
try:
    import seat_pool  # noqa: E402  R6 查詢面(只查不動,收席仍歸 idle_reaper)
    _sp_path, _sp_rec = seat_pool.refresh_snapshot()
    if _sp_path is None:
        note_log('seat pool: 沒有 state 目錄,這一輪沒落 occupied')
except Exception as _sp_exc:            # noqa: BLE001  盤點壞掉不得換掉畫面
    note_log('seat pool: 盤點失敗 %r' % (_sp_exc,))
PY
  then
    # 一輪審中2:pane-fit 也在「renderer 失敗使 rowmap 失效」紀律內——原本
    # 接管線其 rc 被吞,fit 死掉時畫面壞而 rowmap 還活著。先取真 rc 再輸出。
    if _fit_out="$("$EZ2_PY3" "$EZ2_COCKPIT_DIR/pane-fit.py" < "$_ai_out")"; then
      # 三輪審高2 發布時序終定:撤 marker → 畫面寫出 → 發布 rowmap →
      # 重掛 marker——重繪期間點擊停用,畫面與映射不存在錯配窗口;
      # 管線 rc 由 pipefail 看守(中3),輸出失敗不發布。
      if [ "${EZ2_RIGHT_VIEW:-}" = "usage" ]; then
        [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
      else
        [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane "" 2>/dev/null || true
      fi
      if printf '%s\n' "$_fit_out" | awk '{printf "%s\033[K\n", $0} END{printf "\033[J"}' &&
         mv -f "$_rowmap_pending" "$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json" 2>/dev/null &&
         mv -f "$_right_width_pending" "$EZ2_COCKPIT_STATE_DIR/.right-pane-required-width" 2>/dev/null &&
         { printf '%s\n' "$$" > "$_ROWMAP_OWNER.$$" && mv -f "$_ROWMAP_OWNER.$$" "$_ROWMAP_OWNER"; } 2>/dev/null; then
        # marker 目標明確 -t(一輪審低3);失敗分支不重掛=點擊維持停用
        [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
      else
        _render_failed=1
      fi
    else
      _render_failed=1
    fi
  else
    _render_failed=1
  fi
  if [ "${_render_failed:-0}" = "1" ]; then
    _render_failed=0
    [ -n "$_fail_sig" ] || _fail_sig="render"
    # 失敗:清掉 rowmap 與 marker,滑鼠寧可不動作也不要點到陳舊的列
    rm -f "$EZ2_COCKPIT_STATE_DIR/ai-rowmap.json" 2>/dev/null || true
    if [ "${EZ2_RIGHT_VIEW:-}" = "usage" ]; then
      # 用量頁失敗也不撤：父層 tabs 會重畫；撤了會整頁假死到下一次成功
      [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane ai-list 2>/dev/null || true
    else
      [ -n "${TMUX_PANE:-}" ] && tmux set-option -p -t "$TMUX_PANE" @aib_pane "" 2>/dev/null || true
    fi
    printf '\033[H %s\n\n %s\n %s\033[J\n' "$(_rai aib.rightai.fallback_title)" "$(_rai aib.rightai.fallback_unavailable)" "$(_rai aib.rightai.fallback_clicks_off)"
    # stderr 節流:同一失敗簽名連續 poll 只印首見;pane 內訊息照常每輪畫
    if [ "$_fail_sig" != "$_last_fail_sig" ]; then
      sed -n '1,3p' "$_ai_out.err" >&2 2>/dev/null || true
    fi
    _last_fail_sig="$_fail_sig"
  else
    _last_fail_sig=""
  fi
  rm -f "$_ai_out" "$_ai_out.err" "$_rowmap_pending" "$_right_width_pending" 2>/dev/null || true
  if [ -f "$EZ2_COCKPIT_STATE_DIR/right-scroll.json" ]; then
    _usage_scroll_seen=$(aib_mtime "$EZ2_COCKPIT_STATE_DIR/right-scroll.json" 0)
  fi
  if [ -f "$EZ2_COCKPIT_STATE_DIR/usage-accounts.json" ]; then
    _usage_accounts_seen=$(aib_mtime "$EZ2_COCKPIT_STATE_DIR/usage-accounts.json" 0)
  fi
  [ "${EZ2_ONESHOT:-0}" = "1" ] && break
  _usage_wait_next
done
