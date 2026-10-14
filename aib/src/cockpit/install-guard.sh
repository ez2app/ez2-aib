#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 缺陷十九(2026-09-05):砍刀樹裡的測試把 tmux 綁定裝到**活的** default 伺服器。
#
# 事故形狀(a seat 11:4x 現查、a seat 反查):
#   dev6 的 knife2.py 把整棵樹 copytree 到 $TMPDIR/knife2-*/tree,K1 刀拿掉
#   `cockpit_pane_plan; exit 0` 那個 exit,於是 test-cockpit-print-plan.py 每一個
#   `--print-plan` 格都**落進真的 --ensure**:HOME=mkdtemp、PATH 上是真 tmux、
#   **沒有 TMUX_TMPDIR** ⇒ 外層 aib-main 建在活的 default、內層 aihub 伺服器
#   建在 /private/tmp/tmux-501/aihub、21 條綁定釘住砍刀樹的路徑與稍後被清掉的
#   state 目錄。各艙右欄從此點什麼都跳出別席的文字。
#
# ★ 為什麼守衛放在**安裝器**而不是只修那支測試:
#   那支測試「零副作用」的前提住在**產品的一個分支**裡(print-plan 的 exit 0),
#   而砍刀的工作就是拿掉分支。守衛要守的是**性質**——「暫存樹不得寫活的 tmux」——
#   它不能依賴任何一個可以被刀拿掉的 if。
#
# 判準(三個輸入,寫成動作不寫成心態):
#   1. 非 dry-run(DRY != 1)才看;dry-run 不碰 tmux,從哪裡跑都無害。
#   2. 有沙箱就放行:TMUX_TMPDIR 非空,且它**不是** /tmp 本身。
#      (tmux 沒有 TMUX_TMPDIR 時的預設就是 /tmp/tmux-$UID,把 TMUX_TMPDIR
#       設成 /tmp 或 /private/tmp 等於沒設 —— test-report-linewrap-r3.py:612 有一處。)
#   3. 沒沙箱時,腳本所在目錄($HERE)或 EZ2_COCKPIT_STATE_DIR 落在任何暫存根
#      ($TMPDIR、/var/folders、/private/var/folders、/tmp、/private/tmp)底下 ⇒ 拒裝。
#      兩個都問:砍刀樹是 HERE 在 tmp;治具把 state 指進 mkdtemp 是 STATE 在 tmp。
#
# 沒有旗標可以繞。要在暫存樹上跑真安裝,就給它一個沙箱(TMUX_TMPDIR=<沙箱>/tmux);
# 要裝進活的伺服器,就從真的樹跑。這兩條路都存在,所以不需要第三條。
#
# rc=6(安裝器既有的 2=引數、3=計畫/綁定被拒;cockpit-full 的 4=token、5=鎖 都不撞)。
AIB_TMP_INSTALL_GUARD_RC=6

_aib_guard_canon() {   # 目錄存在就回實體路徑,否則原樣回(不存在的路徑無從解析 symlink)
  local p="$1"
  if [ -d "$p" ]; then (cd "$p" 2>/dev/null && pwd -P) || printf '%s\n' "$p"
  else printf '%s\n' "$p"; fi
}

_aib_guard_under() {   # $1=路徑 $2=根;路徑等於根或在根底下回 0
  local p="${1%/}" r="${2%/}"
  [ -n "$r" ] || return 1
  case "$p" in
    "$r"|"$r"/*) return 0 ;;
  esac
  return 1
}

aib_tmp_roots() {      # 一行一個;$TMPDIR 的原樣與實體兩種都給,symlink 兩邊都要認
  local t="${TMPDIR:-}"
  if [ -n "$t" ]; then printf '%s\n' "$t"; _aib_guard_canon "$t"; fi
  printf '%s\n' /var/folders /private/var/folders /tmp /private/tmp
}

aib_path_under_tmp() { # $1=路徑;它(原樣或實體)落在任一暫存根底下回 0
  # ★ 2026-09-19 B8 假紅:早退 return 0 會關掉 < <(aib_tmp_roots) 讀端,
  #   下一行 printf 吃 SIGPIPE;cockpit-full 是 set -euo pipefail,process
  #   substitution 的 141 把整段 ensure 打成 rc=1,四元組印不出
  #   (b8-witness-fail cabin-uuid: Broken pipe @ install-guard:50)。
  #   先把 roots 收進變數再迴圈,producer 跑完,不再半路斷管。
  local p="$1" c root roots
  [ -n "$p" ] || return 1
  c="$(_aib_guard_canon "$p")"
  roots="$(aib_tmp_roots)" || true
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    if _aib_guard_under "$p" "$root" || _aib_guard_under "$c" "$root"; then return 0; fi
  done <<EOF
$roots
EOF
  return 1
}

aib_tmux_is_sandboxed() {   # TMUX_TMPDIR 非空且不是 /tmp 本身
  local t="${TMUX_TMPDIR:-}"
  [ -n "$t" ] || return 1
  case "$(_aib_guard_canon "$t")" in
    /tmp|/private/tmp) return 1 ;;
  esac
  return 0
}

# 會實際跑的 tmux 若是**暫存目錄裡的替身**(治具在 bindir 放的 shim),它根本不開
# 真 socket —— 那也是隔離,和 TMUX_TMPDIR 沙箱等價安全。真事故用的是系統 tmux
# (/opt/homebrew/bin/tmux,不在 tmp),所以這條放行 shim 卻仍擋真安裝。
# ★ 為什麼要這條:沒有它,凡是用「PATH 放假 tmux」隔離(不設 TMUX_TMPDIR)的治具
#   都會被誤擋成假紅(test-hover-mouse 的 Tmux37 三格即此)。守衛在修好的樹上量
#   假紅、把假紅補掉,是這支臂上線前的必經一步。
aib_tmux_binary_is_shim() {
  local t
  t="$(command -v tmux 2>/dev/null)" || return 1
  [ -n "$t" ] || return 1
  aib_path_under_tmp "$t"
}

# aib_refuse_tmp_install <腳本名> <HERE> <DRY>
#   回 0 = 放行;否則印出理由與兩條修法到 stderr,回 $AIB_TMP_INSTALL_GUARD_RC。
#   呼叫端要自己 `|| exit $?`(這裡不 exit,讓函式可以被臂單獨測)。
aib_refuse_tmp_install() {
  local who="$1" here="$2" dry="${3:-0}" why=""
  [ "$dry" = "1" ] && return 0
  aib_tmux_is_sandboxed && return 0
  aib_tmux_binary_is_shim && return 0
  if aib_path_under_tmp "$here"; then
    why="腳本在暫存目錄裡(HERE=$here)"
  elif aib_path_under_tmp "${EZ2_COCKPIT_STATE_DIR:-}"; then
    why="狀態目錄在暫存目錄裡(EZ2_COCKPIT_STATE_DIR=${EZ2_COCKPIT_STATE_DIR:-})"
  else
    return 0
  fi
  {
    echo "$who: 拒絕安裝到活的 tmux —— $why,而 TMUX_TMPDIR=${TMUX_TMPDIR:-<未設>} 不是沙箱"
    echo "$who: 缺陷十九(09-05):砍刀樹把綁定裝進 default 伺服器,各艙右欄全壞"
    echo "$who: 修法二選一:TMUX_TMPDIR=<沙箱目錄>/tmux(治具),或從真的樹跑(部署)"
  } >&2
  return "$AIB_TMP_INSTALL_GUARD_RC"
}
