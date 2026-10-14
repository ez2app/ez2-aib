#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Apply the data-driven AIB theme and keep pane focus styles reconciled.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/cockpit-env.sh"
# 畫面字走四語字典(語系盤查第 14 條,10-14):_tct <鍵> [名=值 …];每次照 i18n_lib.resolve_locale() 取。
#   python 起不來 ⇒ 印鍵名、rc 照 0(set -e 底下也不會中斷)。
_tct() {
  local key="$1"; shift
  local args=() kv
  for kv in "$@"; do args+=(--param "$kv"); done
  # 字典庫先找 $HERE(測試替身會把 HERE 指到假目錄 ⇒ 退回本檔所在目錄);EZ2_PY3 起不來 ⇒ 再用 python3 試一次,都不行才印鍵名
  local lib="$HERE/i18n_lib.py"
  [ -f "$lib" ] || lib="$(dirname "${BASH_SOURCE[0]:-$0}")/i18n_lib.py"
  "${EZ2_PY3:-python3}" "$lib" ${args[@]+"${args[@]}"} "$key" 2>/dev/null \
    || python3 "$lib" ${args[@]+"${args[@]}"} "$key" 2>/dev/null || printf '%s\n' "$key"
}

MODE=""
SESSION=""
PANE=""
STATE=""
DRY="${AIB_DRY_RUN:-0}"
THEME_ID=""
OVR_ROLE=""; OVR_PALETTE=""
LEFT_NORMAL=""; LEFT_FOCUSED=""
CENTER_NORMAL=""; CENTER_FOCUSED=""
RIGHT_NORMAL=""; RIGHT_FOCUSED=""
BOTTOM_NORMAL=""; BOTTOM_FOCUSED=""

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)
      MODE="apply"
      SESSION="${2:-}"
      shift
      ;;
    --install-hooks)
      MODE="install-hooks"
      SESSION="${2:-}"
      shift
      ;;
    --focus)
      MODE="focus"
      PANE="${2:-}"
      STATE="${3:-}"
      SESSION="${4:-}"
      shift 3
      ;;
    --sync-active)
      MODE="sync-active"
      SESSION="${2:-}"
      shift
      ;;
    --set-override)
      MODE="set-override"
      OVR_ROLE="${2:-}"
      OVR_PALETTE="${3:-}"
      SESSION="${4:-}"
      shift 3
      ;;
    --clear-override)
      MODE="clear-override"
      OVR_ROLE="${2:-}"
      SESSION="${3:-}"
      shift 2
      ;;
    --set-no-bg)
      MODE="set-no-bg"
      SESSION="${2:-}"
      shift
      ;;
    --clear-no-bg)
      MODE="clear-no-bg"
      SESSION="${2:-}"
      shift
      ;;
    --is-no-bg)
      MODE="is-no-bg"
      ;;
    --dry-run)
      DRY=1
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

# 查詢模式只讀覆寫檔、不碰 tmux,所以不需要 SESSION(呼叫端是選單建構,
# 它在還沒決定要對哪一格做事之前就要知道全艙開關的狀態)。
if [ -z "$MODE" ] || { [ "$MODE" != "is-no-bg" ] && [ -z "$SESSION" ]; }; then
  echo "usage: theme-ctl.sh --apply SESSION | --install-hooks SESSION | --focus PANE normal|focused SESSION | --sync-active SESSION | --set-override ROLE PALETTE_ID SESSION | --clear-override ROLE SESSION | --set-no-bg SESSION | --clear-no-bg SESSION | --is-no-bg [--dry-run]" >&2
  exit 2
fi

case "$MODE" in
  set-override|clear-override)
    [ -n "$OVR_ROLE" ] || { echo "theme-ctl: $(_tct aib.themectl.need_role)" >&2; exit 2; }
    [ "$MODE" != "set-override" ] || [ -n "$OVR_PALETTE" ] || {
      echo "theme-ctl: $(_tct aib.themectl.need_palette)" >&2; exit 2; }
    ;;
esac

run() {
  if [ "$DRY" = "1" ]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}

outer_tmux() {
  env -u TMUX tmux "$@"
}

theme_cli() {
  "$EZ2_PY3" "$HERE/theme.py" --file "$EZ2_AIB_THEME_FILE" "$@"
}

theme_role() {
  case "$1" in
    left|right|bottom) printf '%s\n' "$1" ;;
    hub) printf 'center\n' ;;
    *) return 1 ;;
  esac
}

load_theme_snapshot() {
  local snapshot="" role="" state="" value="" key=""
  snapshot="${EZ2_AIB_THEME_SNAPSHOT:-}"
  [ -n "$snapshot" ] || snapshot="$(theme_cli snapshot)" || return 1
  while IFS='|' read -r role state value; do
    if [ "$role" = "theme_id" ]; then
      [ -n "$state" ] && [ -z "$value" ] && [ -z "$THEME_ID" ] || return 1
      [[ "$state" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] || return 1
      THEME_ID="$state"
      continue
    fi
    case "$role/$state" in
      left/normal) key="LEFT_NORMAL" ;;
      left/focused) key="LEFT_FOCUSED" ;;
      center/normal) key="CENTER_NORMAL" ;;
      center/focused) key="CENTER_FOCUSED" ;;
      right/normal) key="RIGHT_NORMAL" ;;
      right/focused) key="RIGHT_FOCUSED" ;;
      bottom/normal) key="BOTTOM_NORMAL" ;;
      bottom/focused) key="BOTTOM_FOCUSED" ;;
      *) return 1 ;;
    esac
    [ -n "$value" ] || return 1
    case "$value" in *'|'*|*$'\n'*) return 1 ;; esac
    [[ "$value" =~ ^fg=colour([0-9]{1,3}),bg=colour([0-9]{1,3})(,(bold|dim|italics|underscore))*$ ]] || return 1
    [ "${BASH_REMATCH[1]}" -le 255 ] && [ "${BASH_REMATCH[2]}" -le 255 ] || return 1
    [ -z "${!key}" ] || return 1
    printf -v "$key" '%s' "$value"
  done <<EOF
$snapshot
EOF
  [ -n "$THEME_ID" ] && [ -n "$LEFT_NORMAL" ] && [ -n "$LEFT_FOCUSED" ] && \
    [ -n "$CENTER_NORMAL" ] && [ -n "$CENTER_FOCUSED" ] && \
    [ -n "$RIGHT_NORMAL" ] && [ -n "$RIGHT_FOCUSED" ] && \
    [ -n "$BOTTOM_NORMAL" ] && [ -n "$BOTTOM_FOCUSED" ]
}

snapshot_style() {
  case "$1/$2" in
    left/normal) printf '%s\n' "$LEFT_NORMAL" ;;
    left/focused) printf '%s\n' "$LEFT_FOCUSED" ;;
    center/normal) printf '%s\n' "$CENTER_NORMAL" ;;
    center/focused) printf '%s\n' "$CENTER_FOCUSED" ;;
    right/normal) printf '%s\n' "$RIGHT_NORMAL" ;;
    right/focused) printf '%s\n' "$RIGHT_FOCUSED" ;;
    bottom/normal) printf '%s\n' "$BOTTOM_NORMAL" ;;
    bottom/focused) printf '%s\n' "$BOTTOM_FOCUSED" ;;
    *) return 1 ;;
  esac
}

pane_rows() {
  # ★ 09-12:對話底下的待審框(@aib_role=review)不歸主題管(它自己畫),與 cockpit-full.sh:452 同一個排除;
  #   不排 ⇒ validate/apply/sync-active 三處都會因為多一格而回非零 ⇒ --ensure 中斷 ⇒ 任何新席開不了。
  #   ★ 不接管線:tmux 問不到(rc≠0)要原樣回非零給呼叫端的 `|| exit 3`(那是「量不到」),
  #   不能被 awk 的 rc 吞成「量到 0 格」(qa a seat 09-12 注入審第 1 件)。
  #   排除用黑名單(只排 review)而不是白名單:白名單會把任何多出來的格靜默吃掉,validate_rows 就再也
  #   抓不到「多一格」;review 是設計上唯一疊上去的格,與 cockpit-full.sh:452 同一個名單。
  local raw
  raw="$(outer_tmux list-panes -t "$1:cockpit" -F '#{pane_id}|#{@aib_role}|#{pane_active}')" || return 1
  printf '%s\n' "$raw" | awk -F '|' 'NF && $2 != "review"'
}

validate_rows() {
  # ★ 09-10:L(座艙預設只留中/右)上線後,這裡寫死「必須 4 格」讓 --install-hooks 在兩格艙
  #   靜默 exit 3 ⇒ cockpit-full.sh --ensure 中斷 ⇒ 新艙永遠開不了席,而畫面上什麼都沒說。
  #   期望的格改成問設定(panes.py --expected-roles,與組裝器同一個源);讀不到設定就拒絕
  #   (fail-closed:寧可不上色也不要在猜測上判「版面不對」)。
  local rows="$1" role count expected
  # ★ 09-12 02:3x:對話底下的待審框(@aib_role=review)是疊上去的一格,cockpit-full.sh:452 的版面檢查
  #   已經排除它,這裡沒排 ⇒ 三格 vs 期望兩格 ⇒ 靜默 exit 3 ⇒ --ensure 中斷 ⇒ 任何新席都開不了
  #   (a seat「開席失敗・等待逾時」與艦長四席開不出來都是它)。與 cockpit-full 同一個排除。
  expected="$("$EZ2_PY3" "$HERE/panes.py" --expected-roles 2>/dev/null)" || {
    echo "theme-ctl: $(_tct aib.themectl.layout_unread)" >&2
    return 1
  }
  [ -n "$expected" ] || { echo "theme-ctl: $(_tct aib.themectl.layout_empty)" >&2; return 1; }
  # ★ 09-12 第 59 項第二半:版面不對時**說出來**(之前 install-hooks 路徑 `|| exit 3` 一個字不印,
  #   開席只剩右欄「等待逾時」)。印「量到幾格/哪些 role」與「期望幾格/哪些 role」,讓人一眼分得出
  #   是多了一格、少了一格、還是 role 標記掉了。
  local want got_roles blank
  # 三態(與 cockpit-full 的 validate_cockpit_layout 同一條紀律):有格還沒設 @aib_role(待審框 split-window 到
  # 它自己的殼設 role 之間有個時間窗,pane-pending-review.sh:527-533)= **量不到**,回 2 並說出來,
  # 不是「版面不合」的 1;呼叫端兩者都不套色,但人看得出是等一下再來還是真壞了(qa 09-12 裁請)。
  blank="$(printf '%s\n' "$rows" | awk -F '|' 'NF && $2 == "" { printf "%s%s", (n++ ? "," : ""), $1 }')"
  [ -z "$blank" ] || {
    echo "theme-ctl: $(_tct aib.themectl.role_blank detail="$blank")" >&2
    return 2
  }
  count="$(printf '%s\n' "$rows" | awk -F '|' 'NF { n++ } END { print n+0 }')"
  want="$(printf '%s\n' "$expected" | awk 'NF { n++ } END { print n+0 }')"
  got_roles="$(printf '%s\n' "$rows" | awk -F '|' 'NF { printf "%s%s", (n++ ? "," : ""), ($2 == "" ? nr : $2) }' nr="$(_tct aib.themectl.no_role)")"
  [ "$count" -eq "$want" ] || {
    echo "theme-ctl: $(_tct aib.themectl.layout_count n="$count" got="$got_roles" total="$want" expected="$(printf '%s' "$expected" | tr '\n' ',')")" >&2
    return 1
  }
  for role in $expected; do
    count="$(printf '%s\n' "$rows" | awk -F '|' -v r="$role" '$2 == r { n++ } END { print n+0 }')"
    [ "$count" -eq 1 ] || {
      echo "theme-ctl: $(_tct aib.themectl.layout_role role="$role" n="$count" got="$got_roles")" >&2
      return 1
    }
  done
}

# 使用者選色覆寫層(the maintainer 08-14「右鍵選底色」)。
#
# 為什麼需要持久檔:只對 pane 設 window-style 活得過 focus 事件(focus hook
# 只改 @aib_theme_focus_state 標記),但**活不過下一次 --apply**——ensure 鏈
# 每次重建都跑 apply,使用者選的色會被洗回預設。覆寫必須是 apply 的輸入。
#
# 為什麼 key 是 region 不是 pane_id:pane_id 會隨 respawn 變(E2.19 實證,
# 內容標記 @aib_pane 同樣會飄),region 由 @aib_role 正規化而來,是這套
# 版面裡唯一穩定的身份。
OVERRIDES_FILE="$EZ2_COCKPIT_STATE_DIR/theme-overrides.json"

# dry-run 不寫檔,套用端就讀不到「即將生效的覆寫」,輸出會顯示預設色——
# 那是會騙人的預覽(測試據此斷言就等於在驗錯的東西)。故 dry 路徑把待寫值
# 掛在 pending 上;真路徑一律讀檔,順帶證明「真的寫進去了」。
PENDING_REGION=""; PENDING_VALUE=""

override_for() {  # $1=region;印出 palette id(無覆寫=印空)
  if [ -n "$PENDING_REGION" ] && [ "$1" = "$PENDING_REGION" ]; then
    [ -z "$PENDING_VALUE" ] || printf '%s\n' "$PENDING_VALUE"
    return 0
  fi
  [ -f "$OVERRIDES_FILE" ] || return 0
  EZ2_KEY="$1" "$EZ2_PY3" -c '
import json, os, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    raise SystemExit(0)          # 壞檔=當作無覆寫,由呼叫端留痕
if isinstance(data, dict):
    value = data.get(os.environ["EZ2_KEY"])
    if isinstance(value, str) and value:
        print(value)
' "$OVERRIDES_FILE" 2>/dev/null || true
}

# 有覆寫走 theme.py 的 override_style(只換 bg,fg/attributes 仍取 region
# 契約值);無覆寫走既有 snapshot(apply 路徑因此不會每格起一次 python)。
# 覆寫檔裡的壞 palette id 只可能來自手改:降級回預設**並留痕**,不靜默。
# 「無底色」是全艙一個開關,不是 per-region 覆寫——因為它要同時管四格
# window-style **和兩條 status 列**(內層艦長列 / 外層),而那兩條不是 region、
# 沒有 per-region 覆寫的位置。故用同一個覆寫檔的保留鍵 `_cockpit` 當單一真相源。
#
# 與 per-region 選色的關係:無底色**壓過**選色但**不清除**它——使用者關掉無底色
# 之後,原本每格選的顏色要原封不動回來。清除式的實作會讓他必須重選一次。
#
# the maintainer 09-11:未設定過或新安裝預設=透明淡黑。所以「沒有 `_cockpit` 鍵」
# 與明示 `none` 一樣生效;要自定底色必須寫 `palette`,不能再靠清掉這個鍵
# (清掉會變回透明,那正是舊預設的反面)。
COCKPIT_KEY="_cockpit"
COCKPIT_NO_BG_VALUE="none"
COCKPIT_PALETTE_VALUE="palette"
cockpit_no_bg() {  # rc=0 表示全艙無底色生效(含未設定)
  case "$(override_for "$COCKPIT_KEY")" in
    ''|"$COCKPIT_NO_BG_VALUE") return 0 ;;
    *) return 1 ;;
  esac
}

effective_style() {  # $1=region $2=state
  local pid style
  if cockpit_no_bg; then
    theme_cli style "$1" "$2" --no-bg && return 0
    echo "theme-ctl: $(_tct aib.themectl.nobg_fail value="$1/$2")" >&2
    snapshot_style "$1" "$2"
    return 0
  fi
  pid="$(override_for "$1")"
  if [ -n "$pid" ]; then
    if style="$(theme_cli style "$1" "$2" --palette "$pid" 2>/dev/null)"; then
      printf '%s\n' "$style"
      return 0
    fi
    echo "theme-ctl: $(_tct aib.themectl.override_bad id="$pid" value="$1")" >&2
  fi
  snapshot_style "$1" "$2"
}

set_pane_style() {
  local pane="$1" role="$2" state="$3" mapped style option
  mapped="$(theme_role "$role")" || return 1
  style="$(effective_style "$mapped" "$state")" || return 1
  case "$state" in
    normal) option="window-style" ;;
    focused) option="window-active-style" ;;
  esac
  run outer_tmux set-option -p -t "$pane" "$option" "$style"
}

record_pane_contract() {
  local pane="$1" role="$2" mapped normal focused
  mapped="$(theme_role "$role")" || return 1
  normal="$(effective_style "$mapped" normal)" || return 1
  focused="$(effective_style "$mapped" focused)" || return 1
  run outer_tmux set-option -p -t "$pane" @aib_theme_id "$THEME_ID"
  run outer_tmux set-option -p -t "$pane" @aib_theme_region "$mapped"
  run outer_tmux set-option -p -t "$pane" @aib_theme_normal_style "$normal"
  run outer_tmux set-option -p -t "$pane" @aib_theme_focused_style "$focused"
  set_pane_style "$pane" "$role" normal
  set_pane_style "$pane" "$role" focused
}

sync_active() {
  local rows="$1" pane role active state active_count=0
  local -a commands=()
  while IFS='|' read -r pane role active; do
    [ -n "$pane" ] || continue
    if [ "$active" = "1" ]; then
      ACTIVE_PANE="$pane"
      ACTIVE_ROLE="$role"
      active_count=$((active_count + 1))
      state="focused"
    else
      state="normal"
    fi
    [ "${#commands[@]}" -eq 0 ] || commands+=( ';' )
    commands+=( set-option -p -t "$pane" @aib_theme_focus_state "$state" )
  done <<EOF
$rows
EOF
  # tmux 一個 window 恆有且只有一格 active,所以這裡 0 只有一種來源:active 的那一格是
  # pane_rows 濾掉的待審框(使用者正把游標停在待審框裡,例如剛在那裡送出留言後右鍵選色)。
  # 那不是壞版面:四格全標 normal 就是正確答案。≥2 才是真的不可能,照樣擋。(09-12 第 59 項)
  [ "${#commands[@]}" -gt 0 ] || {
    echo "cockpit has no themed panes to sync (rows empty)" >&2
    return 3
  }
  [ "$active_count" -le 1 ] || {
    echo "cockpit must have exactly one active pane (got $active_count)" >&2
    return 3
  }
  # One tmux client applies the four markers in one ordered command queue.
  # This is fast enough to run synchronously and cannot leave an older
  # background snapshot overwriting a newer focus event.
  run outer_tmux "${commands[@]}"
}

sync_hook_command() {
  local rows="$1" pane role active command="" part=""
  while IFS='|' read -r pane role active; do
    [ -n "$pane" ] || continue
    case "$pane" in %*[!0-9]*|%|'') return 1 ;; esac
    printf -v part \
      "set-option -Fp -t '%s' @aib_theme_focus_state '#{?pane_active,focused,normal}'" \
      "$pane"
    [ -z "$command" ] || command="$command ; "
    command="$command$part"
  done <<EOF
$rows
EOF
  [ -n "$command" ] || return 1
  printf '%s\n' "$command"
}

hook_line_at() {
  local name="$1" slot="$2"
  outer_tmux show-hooks -w -t "$SESSION:cockpit" "$name" 2>/dev/null |
    awk -v key="$name[$slot]" '$1 == key { print; found=1; exit } END { if (!found) exit 1 }'
}

hook_slot_is_free() {
  local slot="$1" name
  for name in pane-focus-in pane-focus-out after-select-pane; do
    if hook_line_at "$name" "$slot" >/dev/null 2>&1; then
      return 1
    fi
  done
}

hook_slot_is_ours() {
  local slot="$1" line=""
  line="$(hook_line_at pane-focus-in "$slot")" || return 1
  case "$line" in *'@aib_theme_focus_state focused'*) ;; *) return 1 ;; esac
  line="$(hook_line_at pane-focus-out "$slot")" || return 1
  case "$line" in *'@aib_theme_focus_state normal'*) ;; *) return 1 ;; esac
  line="$(hook_line_at after-select-pane "$slot")" || return 1
  case "$line" in *"@aib_theme_focus_state"*"pane_active,focused,normal"*) ;;
    *) return 1 ;;
  esac
}

choose_hook_slot() {
  local recorded="" candidate
  recorded="$(outer_tmux show-options -w -qv -t "$SESSION:cockpit" \
    @aib_theme_hook_slot 2>/dev/null || true)"
  if [[ "$recorded" =~ ^[0-9]+$ ]] && hook_slot_is_ours "$recorded"; then
    printf '%s\n' "$recorded"
    return 0
  fi
  # Never assume a magic slot is vacant: every array member may belong to a
  # user or another cockpit component.  Select the first slot unused by all
  # three hook arrays and record it on this window for idempotent reinstalls.
  for ((candidate = 91; candidate <= 190; candidate++)); do
    if hook_slot_is_free "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Theme data is needed only while registering the pane style pair. Interactive
# focus reconciliation reads no colour data and must not start Python once per
# pane on every click/Shift+Tab.
case "$MODE" in
  apply|install-hooks|set-override|clear-override|set-no-bg|clear-no-bg)
    load_theme_snapshot ;;
esac

# 覆寫寫入走既有原子轉移器(反重造:不自造第二套 lock+tmp+rename)。
# 清除=**把 key 拿掉**,不是把當下預設色寫成覆寫——兩者今天看起來一樣,
# 主題日後改色時就會分岔:寫死的那個會把使用者釘在舊色上,而他以為自己
# 選的是「預設」(root3 08-14 裁定)。
write_override_key() {  # $1=覆寫檔的 key $2=值(空=清除);**不含守衛**
  . "$HERE/state-transition.sh"
  _override_compute() {
    EZ2_CUR="$1" EZ2_KEY="$OVR_REGION" EZ2_VAL="${OVR_VALUE:-}" "$EZ2_PY3" -c '
import json, os
cur = os.environ.get("EZ2_CUR", "").strip()
try:
    data = json.loads(cur) if cur else {}
except ValueError:
    data = {}
if not isinstance(data, dict):
    data = {}
key, val = os.environ["EZ2_KEY"], os.environ["EZ2_VAL"]
if val:
    data[key] = val
else:
    data.pop(key, None)
print(json.dumps(data, ensure_ascii=False, sort_keys=True,
                 separators=(",", ":")))
'
  }
  OVR_REGION="$1" OVR_VALUE="$2"
  if [ "$DRY" = "1" ]; then
    printf '+ override %s=%s -> %s\n' "$1" "${2:-(clear)}" "$OVERRIDES_FILE"
    PENDING_REGION="$1"; PENDING_VALUE="$2"
    return 0
  fi
  aib_state_transition "$OVERRIDES_FILE" _override_compute
}

# 命名空間守衛:覆寫檔是扁平 dict,region 名若撞上保留鍵**不會報錯,只會有
# 一邊被另一邊吃掉**。所以擋在寫入口(不是靠人記得不要那樣命名),而且擋整個
# `_` 前綴不只 `_cockpit`——將來多一個保留鍵不必回來補這裡。
# 保留鍵自己走 write_override_key,那是刻意的、不是繞過守衛。
write_override() {  # $1=region $2=palette id(空=清除)
  case "$1" in
    _*)
      echo "theme-ctl: $(_tct aib.themectl.region_underscore name="$1")" >&2
      return 2
      ;;
  esac
  write_override_key "$1" "$2"
}

# 目標格=事件那一格的 role 對應的 pane,**不是 active pane**(需求核心:
# 「滑鼠是使用者的手」——他按哪一格就套哪一格)。role 唯一性由
# validate_rows 保證,故 role -> pane 是單射。
pane_of_role() {  # $1=rows $2=role
  printf '%s\n' "$1" | awk -F '|' -v r="$2" '$2 == r { print $1; exit }'
}

case "$MODE" in
  is-no-bg)
    # rc=0 全艙無底色生效;rc=1 沒生效。給選單建構用來揭露「恢復預設」的
    # 作用域(root3 08-15 裁:使用者有權在按下之前知道作用域,
    # 沒告知的副作用等於靜默)。
    cockpit_no_bg && exit 0 || exit 1
    ;;
  apply)
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || {
      echo "theme target is not the four-region cockpit: $SESSION" >&2
      exit 3
    }
    while IFS='|' read -r PANE_ID PANE_ROLE PANE_ACTIVE; do
      [ -n "$PANE_ID" ] || continue
      record_pane_contract "$PANE_ID" "$PANE_ROLE"
    done <<EOF
$ROWS
EOF
    sync_active "$ROWS"
    ;;
  sync-active)
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || exit 3
    sync_active "$ROWS"
    ;;
  set-override|clear-override)
    REGION="$(theme_role "$OVR_ROLE")" || {
      echo "theme-ctl: $(_tct aib.themectl.unknown_role role="$OVR_ROLE")" >&2
      exit 2
    }
    # palette id 在**寫入前**驗:壞值不落盤(被拒一 byte 不寫,同 rotation
    # 的產生端紀律)。驗法=真的算一次該色的 style,合法才有輸出。
    if [ "$MODE" = "set-override" ]; then
      theme_cli style "$REGION" normal --palette "$OVR_PALETTE" >/dev/null || {
        echo "theme-ctl: $(_tct aib.themectl.unknown_palette id="$OVR_PALETTE")" >&2
        exit 2
      }
      write_override "$REGION" "$OVR_PALETTE" || {
        echo "theme-ctl: $(_tct aib.themectl.override_write_fail)" >&2
        exit 3
      }
    else
      write_override "$REGION" "" || {
        echo "theme-ctl: $(_tct aib.themectl.override_clear_fail)" >&2
        exit 3
      }
    fi
    # 立即生效:只動這一格的兩個 style 與契約標記,其餘三格一個位元不碰。
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || {
      echo "theme target is not the four-region cockpit: $SESSION" >&2
      exit 3
    }
    TARGET_PANE="$(pane_of_role "$ROWS" "$OVR_ROLE")"
    [ -n "$TARGET_PANE" ] || {
      echo "theme-ctl: $(_tct aib.themectl.no_pane role="$OVR_ROLE")" >&2
      exit 3
    }
    record_pane_contract "$TARGET_PANE" "$OVR_ROLE"
    ;;
  set-no-bg|clear-no-bg)
    # 無底色是全艙開關,所以生效範圍是**四格都重套**(對比 set-override 只動
    # 一格)。兩條 status 列不在這裡——它們的預設值寫死在 cockpit-full.sh,
    # 由那支自己讀覆寫,見該檔內註解。
    if [ "$MODE" = "set-no-bg" ]; then
      write_override_key "$COCKPIT_KEY" "$COCKPIT_NO_BG_VALUE" || {
        echo "theme-ctl: $(_tct aib.themectl.nobg_write_fail)" >&2
        exit 3
      }
    else
      write_override_key "$COCKPIT_KEY" "$COCKPIT_PALETTE_VALUE" || {
        echo "theme-ctl: $(_tct aib.themectl.bg_back_fail)" >&2
        exit 3
      }
    fi
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || {
      echo "theme target is not the four-region cockpit: $SESSION" >&2
      exit 3
    }
    while IFS='|' read -r PANE_ID PANE_ROLE PANE_ACTIVE; do
      [ -n "$PANE_ID" ] || continue
      record_pane_contract "$PANE_ID" "$PANE_ROLE"
    done <<EOF
$ROWS
EOF
    sync_active "$ROWS"
    ;;
  focus)
    case "$STATE" in normal|focused) ;; *) exit 2 ;; esac
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || exit 3
    MATCHES="$(printf '%s\n' "$ROWS" | awk -F '|' -v p="$PANE" '$1 == p { print $2 }')"
    [ "$(printf '%s\n' "$MATCHES" | awk 'NF { n++ } END { print n+0 }')" -eq 1 ] || {
      echo "theme pane is missing or not unique: $PANE" >&2
      exit 3
    }
    ROLE="$(printf '%s\n' "$MATCHES" | awk 'NF { print; exit }')"
    # window-style/window-active-style already form the native visual pair.
    # Focus hooks only maintain the inspectable state marker.
    run outer_tmux set-option -p -t "$PANE" @aib_theme_focus_state "$STATE"
    ;;
  install-hooks)
    ROWS="$(pane_rows "$SESSION")" || exit 3
    validate_rows "$ROWS" || exit 3
    # ★ 09-29 a seat(the maintainer 開新艙錄影 2.8–5.5 秒底列黃字「No such pane: #{q:hook_pane}」):
    #   tmux 3.7c 在 hook 命令裡**不展開 -t 的格式**,舊寫法永遠找不到 pane —— 焦點標記從沒寫進去過,
    #   平常看不到錯,開艙那一刻有 client 接上時錯誤訊息才浮到狀態列。不帶 -t:pane hook 的預設目標就是觸發事件的 pane
    #   (隔離 tmux 實測:兩 pane 互切,進焦點者 focused、出焦點者 normal,錯誤 0)。守格:test-theme 的 hook 兩格。
    FOCUS_IN="set-option -p @aib_theme_focus_state focused"
    FOCUS_OUT="set-option -p @aib_theme_focus_state normal"
    # Native pane styles switch immediately. Reconcile diagnostic markers in
    # tmux's own command queue so focus never starts a shell or Python process.
    SYNC="$(sync_hook_command "$ROWS")" || exit 3
    HOOK_SLOT="$(choose_hook_slot)" || {
      echo "no free cockpit theme hook slot" >&2
      exit 3
    }
    run outer_tmux set-option -t "$SESSION" focus-events on
    run outer_tmux set-hook -w -t "$SESSION:cockpit" \
      "pane-focus-in[$HOOK_SLOT]" "$FOCUS_IN"
    run outer_tmux set-hook -w -t "$SESSION:cockpit" \
      "pane-focus-out[$HOOK_SLOT]" "$FOCUS_OUT"
    run outer_tmux set-hook -w -t "$SESSION:cockpit" \
      "after-select-pane[$HOOK_SLOT]" "$SYNC"
    run outer_tmux set-option -w -t "$SESSION:cockpit" \
      @aib_theme_hook_slot "$HOOK_SLOT"
    ;;
esac
