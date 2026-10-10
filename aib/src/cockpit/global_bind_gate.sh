#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 全機共用的外層 tmux 按鍵/滑鼠綁定,這次開艙能不能改(10-09 a seat,the maintainer:「修好他」)。
#
# 為什麼要有這道門:外層座艙是**一台** tmux 伺服器(default socket),所有艙共用同一份 root 表、
# pending-nav 表…。開艙時 cockpit-full.sh 會跑 install-keys / region-nav / mouse-ctl / view-nav 重綁,
# 綁進去的指令寫的是「跑開艙那一棵樹」的絕對路徑。10-09 19:56 有席從自己的開發 worktree 開了一個測試艙,
# 24 條綁定全部改指向那個 worktree;23:1x the maintainer 在 4f8d 滾滑鼠 ⇒ 跑到 worktree 的 scroll-hub-wheel.sh 失敗,
# 錯誤訊息開在中間格的 view-mode 裡,之後打的字全被吃掉(「4f8d 又掛了,無法打字」)。
#
# 規則(回 0 = 可以裝,1 = 不裝):
#   正本樹(git worktree list 第一列,同 ff_ctl.canonical_tree)開的艙   ⇒ 裝
#   不是 git 樹(安裝版)                                              ⇒ 裝
#   外層 tmux 是隔離的(TMUX_TMPDIR 指到 /tmp 以外 —— 測試沙箱)        ⇒ 裝(各席在 worktree 跑測試要能裝)
#   AIB_ALLOW_FOREIGN_BINDINGS=1                                      ⇒ 裝(明說要)
#   其他(開發 worktree + 真正共用的外層 tmux)                         ⇒ 不裝,印一行為什麼
#
# 用法:. global_bind_gate.sh; if aib_global_bind_ok "$C"; then …install… fi

aib_global_bind_ok() {
  local here="${1:-}" top canon tmpdir shared
  [ "${AIB_ALLOW_FOREIGN_BINDINGS:-0}" = "1" ] && return 0
  tmpdir="${TMUX_TMPDIR:-/tmp}"
  tmpdir="$(cd "$tmpdir" 2>/dev/null && pwd -P)" || return 0
  shared="$(cd /tmp 2>/dev/null && pwd -P)" || return 0   # macOS 上 /tmp 是連結;現查實際位置,不寫死(病根封條)
  [ "$tmpdir" = "$shared" ] || return 0  # 隔離沙箱:改的不是大家共用的那一台
  [ -n "$here" ] || return 0
  top="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" || return 0
  canon="$(git -C "$here" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | head -n 1)"
  [ -n "$canon" ] || return 0
  top="$(cd "$top" && pwd -P)"
  canon="$(cd "$canon" 2>/dev/null && pwd -P)" || return 0
  if [ "$top" = "$canon" ]; then
    return 0
  fi
  printf 'aib: 這一艙是從開發樹 %s 開的,不改全機共用的按鍵/滑鼠綁定(那些要指向正本樹 %s,否則每一艙都會跑到這棵樹的程式)。要硬改:AIB_ALLOW_FOREIGN_BINDINGS=1\n' \
    "$top" "$canon" >&2
  return 1
}
