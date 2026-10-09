#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Reapply the eye-verified 20/60/20 + 24% bottom geometry in explicit cells.
# tmux stores cell counts, not ratios; font changes therefore require this
# hook.  Pane ids are immutable for a pane, unlike positional indices.
set -euo pipefail

[ "$#" -eq 5 ] || {
  echo "usage: cockpit-resize.sh SESSION LEFT HUB RIGHT BOTTOM" >&2
  exit 2
}
S="$1"
# "-" = 缺席(鉤子字串裡空字串活不過 run-shell 的單引號,見 cockpit-full.sh 組 RESIZE_CMD 那段)
_absent() { [ "$1" = "-" ] && printf '' || printf '%s' "$1"; }
P_LEFT="$(_absent "$2")"
P_HUB="$(_absent "$3")"
P_RIGHT="$(_absent "$4")"
P_BOTTOM="$(_absent "$5")"
outer_tmux() { env -u TMUX tmux "$@"; }

STATE_DIR="${EZ2_COCKPIT_STATE_DIR:-}"
if [ -z "$STATE_DIR" ]; then
  STATE_DIR="$(outer_tmux show-option -qv -t "$S" @aib_state_dir 2>/dev/null || true)"
fi
if [ -z "$STATE_DIR" ]; then
  echo "resize: 找不到收據目錄;拒絕在 run-shell -b 下無痕執行" >&2
  exit 2
fi
mkdir -p "$STATE_DIR" || {
  echo "resize: 無法建立收據目錄:$STATE_DIR" >&2
  exit 2
}
RESIZE_RECEIPT="$STATE_DIR/.resize-last-verify"
write_early_failure() {
  local tmp
  tmp="$RESIZE_RECEIPT.pending.$$"
  printf 'status=fail attempts=0 bottom_target=unknown bottom_actual=unknown left_target=unknown left_actual=unknown right_target=unknown right_actual=unknown at=%s\n' \
    "$(date +%s)" > "$tmp"
  mv -f "$tmp" "$RESIZE_RECEIPT"
}

W=""; H=""
read -r W H <<EOF || true
$(outer_tmux display-message -p -t "$S:cockpit" '#{window_width} #{window_height}' 2>/dev/null || true)
EOF
case "$W:$H" in
  *[!0-9:]*|:*)
    write_early_failure
    echo "resize: 讀不到有效視窗幾何:${W:-unknown}x${H:-unknown}" >&2
    exit 1 ;;
esac
# ── 缺陷三十一(the maintainer 09-06 03:2x:attach 瞬間 `returned 1` 印九次)──────────────
# ★ 剛 attach 的幾秒內,**client 幾何在變而 window 幾何未變**(window 仍由舊 client 決定,
#   新 client 的尺寸還沒套上)。三態收尾若只比 window 幾何,會把每一個 client-attached /
#   client-resized 事件都判成乙(真失敗)並印到使用者臉上。
#   ⇒ 分辨甲乙同時看 client 幾何(下面收尾段);attach 事件本身的一輪,不收斂一律當甲。
# ★ 事件名由 cockpit-full.sh 的 hook 綁定用 AIB_RESIZE_EVENT 帶進來;沒帶=不知道=照舊規則。
RESIZE_EVENT="${AIB_RESIZE_EVENT:-}"
CW=""; CH=""
read -r CW CH <<EOF || true
$(outer_tmux display -p -t "$S" '#{client_width} #{client_height}' 2>/dev/null || true)
EOF
case "$CW:$CH" in *[!0-9:]*|:*) CW=""; CH="" ;; esac   # 讀不到 client 幾何 ⇒ 空,不猜

# ── 右欄閃沒自癒(2026-09-22)────────────────────────────────────────────
# 開艙後 `.right-pane-required-width` 常被畫成 60+「自然列寬」,再配
# `window-size latest` 與 ~99 欄終端,一 resize 右欄就被 tmux 收掉;
# hook 還握著舊 pane id,之後只會對死格 resize,永遠補不回來。
# 這裡改以活體 @aib_role 為準,缺右欄就從 hub 百分比切回。
_resolve_role_pane() {
  local role="$1" baked="$2" live
  live="$(outer_tmux list-panes -t "$S:cockpit" -F '#{pane_id} #{@aib_role} #{pane_dead}' 2>/dev/null \
    | awk -v r="$role" '$2 == r && $3 == 0 { print $1; exit }')"
  if [ -n "$live" ]; then
    printf '%s' "$live"
    return 0
  fi
  if [ -n "$baked" ] && [ "$(outer_tmux display-message -p -t "$baked" '#{pane_dead}' 2>/dev/null || echo 1)" = "0" ]; then
    printf '%s' "$baked"
    return 0
  fi
  printf ''
}
P_LEFT="$(_resolve_role_pane left "$P_LEFT")"
P_HUB="$(_resolve_role_pane hub "$P_HUB")"
P_RIGHT="$(_resolve_role_pane right "$P_RIGHT")"
P_BOTTOM="$(_resolve_role_pane bottom "$P_BOTTOM")"

_heal_right_pane() {
  local hub="$1" cockpit_dir cmd new
  [ -n "$hub" ] || return 0
  cockpit_dir="${EZ2_COCKPIT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
  if [ -x "${STATE_DIR}/run-right-tabs.sh" ]; then
    cmd="bash ${STATE_DIR}/run-right-tabs.sh"
  else
    cmd="env -u EZ2_ONESHOT -u EZ2_RIGHT_VIEW bash ${cockpit_dir}/pane-right-tabs.sh"
  fi
  new="$(outer_tmux split-window -h -p 32 -t "$hub" -P -F '#{pane_id}' "$cmd" 2>/dev/null || true)"
  [ -n "$new" ] || return 0
  outer_tmux set-option -p -t "$new" @aib_role right 2>/dev/null || true
  outer_tmux set-option -p -t "$new" @aib_pane ai-list 2>/dev/null || true
  outer_tmux set-option -p -t "$new" @aib_cockpit 1 2>/dev/null || true
  P_RIGHT="$new"
  # 清掉過大的「自然寬」發布,避免下一輪又把自己擠死
  if [ -f "$STATE_DIR/.right-pane-required-width" ]; then
    printf '32\n' > "$STATE_DIR/.right-pane-required-width"
  fi
}
if [ -z "$P_RIGHT" ] && [ -n "$P_HUB" ]; then
  _heal_right_pane "$P_HUB"
fi

BASE_SIDE=$((W / 5))
MAX_SIDE=$(((W - 42) / 2))
LEFT="$BASE_SIDE"
if [ "$LEFT" -lt 24 ]; then LEFT=24; fi
if [ "$MAX_SIDE" -lt 1 ]; then MAX_SIDE=1; fi
if [ "$LEFT" -gt "$MAX_SIDE" ]; then LEFT="$MAX_SIDE"; fi

# 右欄寬由真渲染器發布的自然列寬決定。24 只是沒有可信發布值時的地板;
# 不把眼前樣本 32 寫成新常數,否則尾標或多位元組內容再長就再次靜默擠壓。
RIGHT_REQUIRED=24
if [ -f "$STATE_DIR/.right-pane-required-width" ]; then
  read -r _published_right < "$STATE_DIR/.right-pane-required-width" || _published_right=""
  case "$_published_right" in
    ''|*[!0-9]*) ;;
    *) [ "$_published_right" -le "$RIGHT_REQUIRED" ] || RIGHT_REQUIRED="$_published_right" ;;
  esac
fi
# 自然列寬(席位長名)可到 60+,不能當硬需求——否則 99 欄終端
# NEED=right+42+框 > client,window-size latest 一縮就把右欄吃掉。
_cap_src="${CW:-$W}"
case "$_cap_src" in ''|*[!0-9]*) _cap_src="$W" ;; esac
_right_cap=$((_cap_src * 35 / 100))
[ "$_right_cap" -lt 24 ] && _right_cap=24
[ "$_right_cap" -gt 40 ] && _right_cap=40
if [ "$RIGHT_REQUIRED" -gt "$_right_cap" ]; then
  RIGHT_REQUIRED="$_right_cap"
fi
RIGHT="$BASE_SIDE"
if [ "$RIGHT" -lt 24 ]; then RIGHT=24; fi
if [ "$RIGHT" -lt "$RIGHT_REQUIRED" ]; then RIGHT="$RIGHT_REQUIRED"; fi
MAX_RIGHT=$((W - 42 - LEFT))
if [ "$MAX_RIGHT" -lt 1 ]; then MAX_RIGHT=1; fi
if [ "$RIGHT" -gt "$MAX_RIGHT" ]; then RIGHT="$MAX_RIGHT"; fi

BOT=$((H * 24 / 100))
MAX_BOT=$((H - 12))
if [ "$BOT" -lt 7 ]; then BOT=7; fi
if [ "$MAX_BOT" -lt 1 ]; then MAX_BOT=1; fi
if [ "$BOT" -gt "$MAX_BOT" ]; then BOT="$MAX_BOT"; fi

# L4:座艙可以只建其中幾格(`aib left|center|right|footer on|off`),
# 所以呼叫端會對**沒建的格**傳空字串。三件事要分開,不得合成一種:
#   有這一格、量得到  → 數字,參與收斂
#   有這一格、量不到  → `unknown`,**不算收斂**(舊行為,不動)
#   根本沒有這一格    → `absent`,**不參與收斂**
# ★★ 為什麼一定要分 `unknown` 與 `absent`:兩者都「讀不到數字」,
#    但一個是故障、一個是設定。合成一種的話,收斂判準要嘛把故障當正常放行,
#    要嘛把正常當故障而**永遠收斂不了** —— 後者正是 SPEC §五 記的那個缺陷:
#    `|| true` 只吞掉指令的 rc,收斂判準仍要求三格幾何都讀得回且相等。
pane_absent() { [ -z "${1:-}" ]; }

# 沒建的格不佔寬/高預算,否則兩格版面會被「幽靈左欄」算成太窄。
if pane_absent "$P_LEFT"; then
  LEFT=0
  MAX_RIGHT=$((W - 42))
  [ "$MAX_RIGHT" -lt 1 ] && MAX_RIGHT=1
  [ "$RIGHT" -gt "$MAX_RIGHT" ] && RIGHT="$MAX_RIGHT"
fi
pane_absent "$P_RIGHT" && RIGHT=0

read_geometry() {
  local pane axis actual
  pane="$1"; axis="$2"
  if pane_absent "$pane"; then printf 'absent\n'; return 0; fi
  actual="$(outer_tmux display -p -t "$pane" "#{pane_${axis}}" 2>/dev/null || true)"
  case "$actual" in
    ''|*[!0-9]*) printf 'unknown\n' ;;
    *) printf '%s\n' "$actual" ;;
  esac
}

# 一格是否已就位:不在場 = 就位(它沒有幾何要收斂),在場則要逐字等於目標。
geometry_settled() {
  local pane actual target
  pane="$1"; actual="$2"; target="$3"
  if pane_absent "$pane"; then return 0; fi
  [ "$actual" = "$target" ]
}

write_receipt() {
  local status attempts tmp
  status="$1"; attempts="$2"; tmp="$RESIZE_RECEIPT.pending.$$"
  printf 'status=%s attempts=%s bottom_target=%s bottom_actual=%s left_target=%s left_actual=%s right_target=%s right_actual=%s at=%s\n' \
    "$status" "$attempts" "$BOT" "$BOTTOM_ACTUAL" "$LEFT" "$LEFT_ACTUAL" \
    "$RIGHT" "$RIGHT_ACTUAL" "$(date +%s)" > "$tmp"
  mv -f "$tmp" "$RESIZE_RECEIPT"
}

# 命令 rc 與版面成功是兩個軸:resize-pane 非零但幾何已到位算成功;
# rc=0 但幾何未到位則繼續。只以每趟之後讀回的三格幾何決定收斂。
# ── 缺陷三十一(3):目標寬度加起來塞不進 client,那是設定問題不是失敗 ──────────
# 中欄至少 42(上面 MAX_SIDE 的算式就是這樣假設的)+ 兩條垂直框線。
# ★ 只在 client 寬讀得到時判;讀不到不判(讀不到 ≠ 太窄)。印一行、exit 0、收據 too_narrow。
if [ -n "$CW" ]; then
  NEED=$((LEFT + RIGHT + 42 + 2))
  if [ "$CW" -lt "$NEED" ]; then
    BOTTOM_ACTUAL=unknown; LEFT_ACTUAL=unknown; RIGHT_ACTUAL=unknown
    write_receipt too_narrow 0
    echo "resize: 終端太窄:需 ${NEED} 欄,現 ${CW} 欄(left=$LEFT right=$RIGHT 中欄≥42);已留收據 $RESIZE_RECEIPT" >&2
    exit 0
  fi
fi

MAX_ATTEMPTS="${AIB_RESIZE_MAX_ATTEMPTS:-4}"
case "$MAX_ATTEMPTS" in ''|*[!0-9]*|0) MAX_ATTEMPTS=4 ;; esac
if [ "$MAX_ATTEMPTS" -lt 2 ]; then MAX_ATTEMPTS=2; fi
if [ "$MAX_ATTEMPTS" -gt 5 ]; then MAX_ATTEMPTS=5; fi
ATTEMPT=0
BOTTOM_ACTUAL=unknown
LEFT_ACTUAL=unknown
RIGHT_ACTUAL=unknown
while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
  ATTEMPT=$((ATTEMPT + 1))
  pane_absent "$P_BOTTOM" || outer_tmux resize-pane -t "$P_BOTTOM" -y "$BOT" || true
  pane_absent "$P_LEFT" || outer_tmux resize-pane -t "$P_LEFT" -x "$LEFT" || true
  pane_absent "$P_RIGHT" || outer_tmux resize-pane -t "$P_RIGHT" -x "$RIGHT" || true

  BOTTOM_ACTUAL="$(read_geometry "$P_BOTTOM" height)"
  LEFT_ACTUAL="$(read_geometry "$P_LEFT" width)"
  RIGHT_ACTUAL="$(read_geometry "$P_RIGHT" width)"
  if geometry_settled "$P_BOTTOM" "$BOTTOM_ACTUAL" "$BOT" && \
     geometry_settled "$P_LEFT" "$LEFT_ACTUAL" "$LEFT" && \
     geometry_settled "$P_RIGHT" "$RIGHT_ACTUAL" "$RIGHT"; then
    write_receipt ok "$ATTEMPT"
    # 外欄幾何就緒後：左欄私訊+待審鎖 ≡，剩餘強制塞中間（開=回應+輸入／關=CLI）
    HERE_RESIZE="$(cd "$(dirname "$0")" && pwd)"
    EZ2_TMUX_SESSION="$S" EZ2_COCKPIT_STATE_DIR="$STATE_DIR" \
      bash "$HERE_RESIZE/cockpit-reflow-left-heights.sh" "$S" >/dev/null 2>&1 || true
    exit 0
  fi
  [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ] || sleep 0.05
done

# ── 三態收尾(the maintainer 2026-09-05 回報:拖曳視窗時畫面被 `returned 1` 洗版)──────────
# ★ 「沒收斂」有兩個成因,而它們在這裡本來共用同一個出口:
#     甲 **被下一次 resize 取代**:拖曳時視窗幾何每幾十毫秒就變一次,本輪算出來的目標
#        在套用時就已經過期 ⇒ 必然不收斂。**這不是失敗,是這一輪已經沒有意義了**,
#        而 tmux 會把 exit 1 的訊息印在使用者臉上,拖一次窗就洗版一次。
#     乙 幾何沒變而仍然套不上 ⇒ **那才是真的失敗**,必須留在畫面上。
# ★ 分辨法:重讀一次視窗幾何,跟本輪開頭那一組比。變了=甲,沒變=乙。
#   ⇒ 判準是「目標還是不是同一個」,不是「試了幾趟」——用趟數當判準會把兩種都判成同一種。
# ★ 甲仍然寫收據(status=superseded),不是靜默:沒有留痕的放行與沒有這一段,在現場逐字相同。
read -r W2 H2 <<EOF || true
$(outer_tmux display-message -p -t "$S:cockpit" '#{window_width} #{window_height}' 2>/dev/null || true)
EOF
if [ "${W2:-}" != "$W" ] || [ "${H2:-}" != "$H" ]; then
  write_receipt superseded "$ATTEMPT"
  # 不印到 stderr:下一次 resize 事件已經在路上,這一輪的訊息只會蓋住真正的問題。
  exit 0
fi
# ── 缺陷三十一(1):window 沒變不代表目標沒變 —— client 幾何變了也是甲 ──────────
# attach 瞬間正是這個形狀:window 幾何由舊 client 決定、尚未套用新 client,「沒變」是假的。
CW2=""; CH2=""
read -r CW2 CH2 <<EOF || true
$(outer_tmux display -p -t "$S" '#{client_width} #{client_height}' 2>/dev/null || true)
EOF
case "$CW2:$CH2" in *[!0-9:]*|:*) CW2=""; CH2="" ;; esac
if [ -n "$CW" ] && [ -n "$CW2" ] && { [ "$CW2" != "$CW" ] || [ "$CH2" != "$CH" ]; }; then
  write_receipt superseded "$ATTEMPT"
  exit 0
fi
# ── 缺陷三十一(2):client-attached 那一輪不收斂,一律當甲(留收據不印)──────────
# ★ 這一輪的目標是用**舊 client 的 window 幾何**算的,attach 後幾秒內必然被下一個
#   client-resized 取代;把它印成乙,就是 the maintainer 看到的九條 `returned 1`。
if [ "$RESIZE_EVENT" = "client-attached" ]; then
  write_receipt superseded_attach "$ATTEMPT"
  exit 0
fi

write_receipt fail "$ATTEMPT"
echo "resize: $ATTEMPT 趟後仍未收斂(幾何未變 ${W}x${H};bottom=$BOTTOM_ACTUAL/$BOT left=$LEFT_ACTUAL/$LEFT right=$RIGHT_ACTUAL/$RIGHT);已留收據 $RESIZE_RECEIPT" >&2
exit 1
