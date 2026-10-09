#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 發版樹凍結 —— pre-commit 面(T10,2026-08-27)
#
# 發版進行中,這棵樹不該再前進:`aib-release.sh` 量的是**它跑起來那一刻**的樹,
# 而 tag 會標在那棵樹上。中途有人 commit,tag 標的與被測的就不是同一份位元組。
#
# ★ 本閘不自己判「鎖有沒有效」——判定只有一份,在 release-freeze-lock.sh。
#   兩個消費者各寫一份判定就是兩把尺,而「陳舊鎖」判錯一次就是靜默放行。
#
# 失效方向:鎖**讀不到**一律擋下並出聲。讀不到不是「沒有鎖」。
set -uo pipefail

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
if [ -z "$LIB" ]; then
  echo "" >&2
  echo "✖ 發版凍結閘找不到鎖判定 release-freeze-lock.sh" >&2
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
  echo "✖ 發版凍結閘:判不出鎖的狀態,擋下這次 commit" >&2
  echo "  ★ 讀不到不是「沒有鎖」—— 探測器的失效值不得等於它表示安全的那個值。" >&2
  echo "  上面那行是判不出來的理由;修好它,或確認發版是不是正在跑。" >&2
  echo "" >&2
  exit 1
fi

case "$STATE" in
  NONE)
    exit 0 ;;
  STALE\ *)
    # 陳舊鎖不擋人,但要出聲:一把沒人解的鎖是一個沒有人在跑的發版留下的。
    printf '   △ 發版凍結閘:發現陳舊鎖 —— %s\n' "${STATE#STALE }" >&2
    exit 0 ;;
  EXPIRED\ *)
    # 窗逾時:放行(一個解不開的鎖比沒有鎖貴得多),但**大聲**,而且記紅。
    # ★ 放行與「沒有鎖」不是同一件事,所以這裡不能安靜地走 NONE 那條路。
    echo "" >&2
    echo "✖ 發版凍結窗**逾時**仍未關閉 —— 本次放行,但這是一個要有人去看的紅" >&2
    printf '   %s\n' "${STATE#EXPIRED }" >&2
    _NOTE="$(freeze_lock_note_expired "pre-commit 閘 放行了一個逾時窗:${STATE#EXPIRED }")" \
      && printf '   已記紅 -> %s\n' "$_NOTE" >&2
    echo "   查鎖  :bash src/policy/hooks/release-freeze-lock.sh status" >&2
    echo "   人工解:bash src/policy/hooks/release-freeze-lock.sh unlock --force" >&2
    echo "   ★「發版掛住了而鎖還在」與「發版正在跑」在別席眼裡一模一樣。" >&2
    echo "" >&2
    exit 0 ;;
  HELD\ *)
    # ★ 不用 `set -- $STATE`:未加引號的展開在不同殼下切詞規則不同(zsh 不切),
    #   而這支腳本會被別人抄去別的地方。read 在哪裡都切一樣。
    read -r _KW OWNER SINCE VER <<< "$STATE"
    # ★ 這裡沒有「持有者的後代放行」那種例外 —— 08-28 root2 裁掉了那份白名單:
    #   白名單會留下另一份更窄的白名單。發版腳本自己的寫入靠**時間界**避開,
    #   不靠名單(凍結窗在它動任何 ref 之前就結束)。
    echo "" >&2
    echo "✖ 發版進行中,樹已凍結 —— 擋下這次 commit" >&2
    echo "" >&2
    echo "    持有者 pid : $OWNER" >&2
    echo "    起於       : $SINCE" >&2
    echo "    版本       : $VER" >&2
    echo "" >&2
    echo "  發版腳本量的是它起跑那一刻的樹,而 tag 會標在那棵樹上。" >&2
    echo "  中途 commit 會讓「被測的」與「被 tag 的」不是同一份位元組。" >&2
    echo "  等那次發版跑完(或確認它已經死掉)再提交。" >&2
    echo "" >&2
    echo "  查鎖  :bash src/policy/hooks/release-freeze-lock.sh status" >&2
    echo "  人工解:bash src/policy/hooks/release-freeze-lock.sh unlock --force" >&2
    echo "          (先確認那個 pid 真的不在了 —— status 會印 pid 與啟動時間)" >&2
    # ★ 批7:那句逃生在**窗開著時是假的**(commit 也是 ref 更新)。
    #   判斷放在鎖程式庫的單一來源,這裡只呼叫 —— 三支消費者不各自判。
    freeze_lock_escape_hint commit
    echo "" >&2
    exit 1 ;;
  *)
    echo "" >&2
    echo "✖ 發版凍結閘:認不得的鎖狀態『${STATE}』,擋下這次 commit" >&2
    echo "  認不得就不放行 —— 新增狀態時要同時更新每一個消費者。" >&2
    echo "" >&2
    exit 1 ;;
esac
