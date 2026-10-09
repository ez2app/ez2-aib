#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 發版樹凍結 —— reference-transaction 面(T10,2026-08-27)
#
# ## 為什麼 pre-commit 一支不夠
#
# pre-commit 只擋 `git commit`。發版進行中真正會動到「tag 要指的那顆」的
# 動作大多**不經過 commit**:`git merge`、`git rebase`、`git reset --hard`、
# `git fetch` 更新遠端追蹤、`git push` 更新本地 ref、別的 worktree 換分支。
# 它們全部是 **ref 更新**,而 ref 更新只有一個必經之處:reference-transaction。
#
# ★ 三段閘的第二段就長在這裡:pre-commit「存在」而「commit 以外的路徑沒被經過」。
#
# ## git 怎麼呼叫它
#
#   $1 = prepared | committed | aborted
#   stdin 每行 = "<old-oid> <new-oid> <ref-name>"
#
# **只有 `prepared` 那一次拒絕得了**(非零 rc 會讓整個交易中止)。
# `committed` 時木已成舟,`aborted` 時本來就沒發生 —— 那兩段一律放行,
# 在那裡回非零只會製造雜訊而擋不住任何東西。
#
# ## 失效方向(不可放寬)
#
# 鎖讀不到 → 擋下並出聲。讀不到不是「沒有鎖」。
# ★ 這一擋的範圍是整個 repo 的每一次 ref 更新,**不是只有發版那條路**——
#   所以它的正確性直接決定所有人還能不能用 git。安裝前先讀 install-hooks.sh 檔頭。
set -uo pipefail

PHASE="${1:-}"
# committed / aborted:擋不住任何東西,直接放行(且不讀 stdin,避免 SIGPIPE)。
case "$PHASE" in
  prepared) ;;
  *) exit 0 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ★ 這支被**複製**到 hooks 目錄之後,它旁邊就沒有 release-freeze-lock.sh 了。
#   只找「自己旁邊」的話,裝上去那一刻它就永遠找不到零件 —— 而它的失效方向是
#   fail-closed,所以那等於**把整個 repo 的 git 鎖死**。
#   (這不是設想:本件的考卷第一次跑就是這樣紅的,而我原本以為那是治具的問題。)
#   所以解析順序與轉接器同一套:自己旁邊 -> 本工作樹 -> 正本樹。
TOP="$(git rev-parse --show-toplevel 2>/dev/null)"
COMMON="$(git rev-parse --git-common-dir 2>/dev/null)"
MAIN_TOP=""
[ -n "$COMMON" ] && MAIN_TOP="$(cd "$COMMON/.." 2>/dev/null && pwd -P)"
LIB=""
for _c in "$HERE/release-freeze-lock.sh" \
          "${TOP:-/nonexistent}/src/policy/hooks/release-freeze-lock.sh" \
          "${MAIN_TOP:-/nonexistent}/src/policy/hooks/release-freeze-lock.sh"; do
  if [ -r "$_c" ]; then LIB="$_c"; break; fi
done

# 先把 stdin 收乾淨:被擋時要說得出擋的是哪一個 ref(R1 要求),
# 而 git 在 hook 提早結束時會拿到 EPIPE。
REFS=""
NREF=0
while read -r _OLD _NEW REFNAME; do
  [ -n "${REFNAME:-}" ] || continue
  NREF=$((NREF+1))
  REFS="$REFS $REFNAME"
done
# ★ 10-05 a seat 實測(Apple Git 2.54):沒變動的 `git fetch` 也會送一個**空的** prepared 交易(stdin 0 列)。
#   空交易一個 ref 都不動,談不上「放行」;以前這裡照樣判鎖、逾時就記一筆「放行了一個逾時窗」,
#   結果開窗前那一閘看到本輪還沒發生的帳(tests/test-release-tree-freeze.py A4 在 Mac 上紅、雲端舊 git 綠)。
#   0 個 ref ⇒ 直接放行、不記帳;有 ref 的交易一個字都不變。
[ "$NREF" -gt 0 ] || exit 0

if [ -z "$LIB" ]; then
  echo "" >&2
  echo "✖ 發版凍結閘(ref)找不到鎖判定 release-freeze-lock.sh" >&2
  echo "  找過:" >&2
  echo "    $HERE/release-freeze-lock.sh" >&2
  echo "    ${TOP:-(解不出工作樹)}/src/policy/hooks/release-freeze-lock.sh" >&2
  echo "    ${MAIN_TOP:-(解不出正本樹)}/src/policy/hooks/release-freeze-lock.sh" >&2
  echo "  守衛消失比守衛擋人更嚴重 —— 先確認那支檔在不在,不要繞過。" >&2
  echo "" >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$LIB"

STATE="$(freeze_lock_state)"; RC=$?
if [ "$RC" -ne 0 ]; then
  echo "" >&2
  echo "✖ 發版凍結閘(ref):判不出鎖的狀態,擋下這次 ref 更新" >&2
  printf '  這次要動的 ref(%s 個):%s\n' "$NREF" "${REFS# }" >&2
  echo "  ★ 讀不到不是「沒有鎖」。" >&2
  echo "  查鎖  :bash src/policy/hooks/release-freeze-lock.sh status" >&2
  echo "  人工解:bash src/policy/hooks/release-freeze-lock.sh unlock --force" >&2
  echo "" >&2
  exit 1
fi

case "$STATE" in
  NONE)
    exit 0 ;;
  STALE\ *)
    exit 0 ;;
  EXPIRED\ *)
    # 窗逾時:放行(一個解不開的鎖比沒有鎖貴得多),但**大聲**,而且記紅。
    # ★ 放行與「沒有鎖」不是同一件事,所以這裡不能安靜地走 NONE 那條路。
    echo "" >&2
    echo "✖ 發版凍結窗**逾時**仍未關閉 —— 本次放行,但這是一個要有人去看的紅" >&2
    printf '   %s\n' "${STATE#EXPIRED }" >&2
    _NOTE="$(freeze_lock_note_expired "reference-transaction 閘 放行了一個逾時窗:${STATE#EXPIRED }")" \
      && printf '   已記紅 -> %s\n' "$_NOTE" >&2
    echo "   查鎖  :bash src/policy/hooks/release-freeze-lock.sh status" >&2
    echo "   人工解:bash src/policy/hooks/release-freeze-lock.sh unlock --force" >&2
    echo "   ★「發版掛住了而鎖還在」與「發版正在跑」在別席眼裡一模一樣。" >&2
    echo "" >&2
    exit 0 ;;
  HELD\ *)
    read -r _KW OWNER SINCE VER <<< "$STATE"
    # ★ 沒有行程白名單(08-28 root2 裁):發版腳本自己的 ref 寫入靠**時間界**避開。
    echo "" >&2
    echo "✖ 發版進行中,樹已凍結 —— 擋下這次 ref 更新" >&2
    echo "" >&2
    printf '    要動的 ref(%s 個):%s\n' "$NREF" "${REFS# }" >&2
    echo "    持有者 pid : $OWNER" >&2
    echo "    起於       : $SINCE" >&2
    echo "    版本       : $VER" >&2
    echo "" >&2
    echo "  發版腳本量的是它起跑那一刻的樹,tag 會標在那棵樹上。" >&2
    echo "  ref 動了,「被測的」與「被 tag 的」就不是同一份位元組。" >&2
    echo "  等那次發版跑完(或確認它已經死掉)再動。" >&2
    echo "" >&2
    echo "  查鎖  :bash src/policy/hooks/release-freeze-lock.sh status" >&2
    echo "  人工解:bash src/policy/hooks/release-freeze-lock.sh unlock --force" >&2
    echo "          (先確認那個 pid 真的不在了 —— status 會印 pid 與啟動時間)" >&2
    echo "  ★ 這一擋的範圍是整個 repo 的每一次 ref 更新,不是只有發版那條路。" >&2
    echo "" >&2
    exit 1 ;;
  *)
    echo "" >&2
    echo "✖ 發版凍結閘(ref):認不得的鎖狀態『${STATE}』,擋下這次 ref 更新" >&2
    echo "" >&2
    exit 1 ;;
esac
