#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 裝/查 canonical-main-guard.sh(推包管道 P8)。對的是**呼叫時所在的那個倉**(在正本裡跑)。
#   install-canonical-guard.sh            = verify(唯讀)
#   install-canonical-guard.sh verify     每個槽印一行:<槽>\t<same|missing|drift|foreign>;有問題 rc=1
#   install-canonical-guard.sh install    裝到 pre-commit 與 pre-merge-commit
#
# ★ 槽裡已經有別的掛勾(沒有本檔的記號行)⇒ **不覆寫**,印 foreign、rc=3,交給人決定(a seat 10-12 裁乙:不碰轉接器)。
# ★ 用複製不用 symlink(同 install-hooks.sh:symlink 指向不存在的檔時 git 靜默跳過)。
# 結束碼:0 全部相同(或裝好) / 1 有槽缺或漂移(verify) / 2 參數錯 / 3 有槽被別的掛勾佔著
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/canonical-main-guard.sh"
MARK="AIB-CANONICAL-MAIN-GUARD"
SLOTS=(pre-commit pre-merge-commit)
MODE="${1:-verify}"
case "$MODE" in verify|install) ;; *) echo "用法:install-canonical-guard.sh [verify|install]" >&2; exit 2;; esac
[ -f "$SRC" ] || { echo "找不到 $SRC" >&2; exit 2; }

HOOKS="$(git rev-parse --git-path hooks)" || exit 2
case "$HOOKS" in /*) ;; *) HOOKS="$(pwd -P)/$HOOKS";; esac
mkdir -p "$HOOKS" 2>/dev/null

rc=0
for slot in "${SLOTS[@]}"; do
  dst="$HOOKS/$slot"
  if [ ! -e "$dst" ]; then
    state=missing
  elif ! grep -q "$MARK" "$dst" 2>/dev/null; then
    state=foreign
  elif cmp -s "$SRC" "$dst"; then
    state=same
  else
    state=drift
  fi
  if [ "$MODE" = install ] && { [ "$state" = missing ] || [ "$state" = drift ]; }; then
    cp "$SRC" "$dst" && chmod 755 "$dst" && state="installed"
  fi
  printf '%s\t%s\n' "$slot" "$state"
  case "$state" in
    foreign) rc=3 ;;
    missing|drift) [ "$rc" -eq 0 ] && rc=1 ;;
  esac
done
exit "$rc"
