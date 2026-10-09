#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB pre-commit 轉接器 —— 多派版(T10,2026-08-27;取代 2026-08-15 的 exec 單派版)
#
# ## 這支是版控正本,裝到 hooks 目錄的那份是它的複本
#
# 08-15 起真正被 git 執行的那支住在 `core.hooksPath` 指的目錄裡,而那個目錄
# **不在版控**:改它沒有 diff、沒有審查、沒有收據,只在那台機器上生效。
# 08-28 裁定(a seat,RULING-T10-adapter-slot-0828.md):正本進版控,
# 安裝由 `install-hooks.sh` 做,裝出去的那份要與本檔**逐位元組相同**。
# ★ 手改裝出去的那份 = 繞過版控。
#
# ## 為什麼從 `exec` 改成多派
#
# 舊版是 `exec bash "$candidate"` —— `exec` **取代行程**,所以它之後的每一行
# 都不會執行。那個迴圈不是「派多支閘」,是「在兩個位置找同一支閘」。
# 結構上**只掛得住一支**,而閘只會愈來愈多(凍結閘是第二支、錨定閘是第三支)。
#
# ## 三段閘:存在 -> 被經過 -> 響了有人聽
#
# 多派要守的是第二段。所以:
#   * **不短路** —— 第一支非零不代表後面幾支可以不跑。短路 = 後面的閘存在但沒被經過。
#   * **每一支都留痕** —— 跑過就印一行,不論綠紅。看不到痕跡就等於沒被經過。
#   * **rc 聚合規則寫下來**:任一支非零 -> 整體非零。取的是**最嚴的那一支**,
#     不是最後一支。這一條寫在這裡,不要靠讀碼推。
#
# ## 清單的守衛:登記數 N 是手寫的
#
# 下面的 `EXPECT_GATES` **必須手寫,不得從清單長度算出來**。
# 理由逐字照收(a seat 08-27):**從清單算就等於多一條同源的腿** ——
# 清單被刪一行時,「算出來的數」與「清單長度」會一起掉到 N-1 並**互相印證**,
# 兩條腿都長在同一份已缺角的證物上。
# 手寫的數會腐爛,但**腐爛方向是紅**:有人加閘忘了改 N 也一樣紅(兩個方向都要紅)。
#
# ## 失效方向(不可放寬)
#
# 清單上任一支腳本找不到 -> `exit 1` 並**逐字點名是哪一支**。
# **不可以因為其他幾支都在就放行。** 沿用 08-15 那支自述的那句:
# 「守衛消失比守衛擋人更嚴重,否則守衛可以無聲消失。」
# ★ 08-28 補一句(root2 入冊):寫在一支守衛檔頭的原則,**不會自動適用到隔壁那支**——
#   隔壁的 pre-push 守衛就是在這句話旁邊無聲消失的。原則要生效必須有臂去問它。
set -uo pipefail

# ── 多派清單 ──────────────────────────────────────────────────────
# 每一行一支閘,依序執行。加減閘要同時改下面的 EXPECT_GATES。
GATES=(
  "docs-retire-gate.sh"
  "release-freeze-precommit.sh"
)

# ★ 手寫。不得改成 ${#GATES[@]}。見上面「清單的守衛」。
EXPECT_GATES=2

# ── 清單自身的守衛 ────────────────────────────────────────────────
if [ "${#GATES[@]}" -ne "$EXPECT_GATES" ]; then
  echo "" >&2
  echo "✖ AIB pre-commit 轉接器:多派清單與登記數不符,擋下這次 commit" >&2
  echo "" >&2
  printf '    登記數(手寫) : %s\n' "$EXPECT_GATES" >&2
  printf '    清單實際長度   : %s\n' "${#GATES[@]}" >&2
  echo "" >&2
  if [ "${#GATES[@]}" -lt "$EXPECT_GATES" ]; then
    echo "  清單比登記數短 —— 有閘被刪掉了,而刪一行就是靜默少一道閘。" >&2
  else
    echo "  清單比登記數長 —— 有人加了閘卻沒改登記數。腐爛可以往兩邊走。" >&2
  fi
  echo "  確認是誰動的,再同時更新清單與登記數。" >&2
  echo "" >&2
  exit 1
fi

TOP="$(git rev-parse --show-toplevel 2>/dev/null)"
COMMON="$(git rev-parse --git-common-dir 2>/dev/null)"
MAIN_TOP=""
[ -n "$COMMON" ] && MAIN_TOP="$(cd "$COMMON/.." 2>/dev/null && pwd -P)"

resolve_gate() {
  local name="$1" c
  for c in "$TOP/src/policy/hooks/$name" "$MAIN_TOP/src/policy/hooks/$name"; do
    case "$c" in
      /src/policy/hooks/*) continue ;;   # TOP/MAIN_TOP 解不出來時不要拿根目錄去找
    esac
    if [ -r "$c" ]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

# ── 存在:先把每一支都找過,一支不在就擋下並點名 ────────────────────
MISSING=""
for g in "${GATES[@]}"; do
  resolve_gate "$g" >/dev/null || MISSING="$MISSING $g"
done
if [ -n "$MISSING" ]; then
  echo "" >&2
  echo "✖ AIB pre-commit 守衛找不到腳本,擋下這次 commit" >&2
  echo "" >&2
  for g in $MISSING; do
    printf '    少了:%s\n' "$g" >&2
  done
  echo "" >&2
  echo "  找過:" >&2
  for g in $MISSING; do
    printf '    %s\n' "$TOP/src/policy/hooks/$g" >&2
    [ -n "$MAIN_TOP" ] && printf '    %s\n' "$MAIN_TOP/src/policy/hooks/$g" >&2
  done
  echo "" >&2
  echo "  ★ 其他幾支都在**不是**放行的理由 —— 少一支就是少一道閘。" >&2
  echo "  守衛消失比守衛擋人更嚴重 —— 先確認那些檔在不在,不要繞過。" >&2
  # ★ 批7:那句逃生在**窗開著時是假的**(commit 也是 ref 更新,而
  #   reference-transaction 沒有 --no-verify)。判斷在鎖程式庫,這裡只呼叫;
  #   載不到就**明說判不出來**,不默默給任何一句 —— 那會在錯的一半時候騙人。
  _ESC_LIB=""
  for _c in "$TOP/src/policy/hooks/release-freeze-lock.sh" \
            "$MAIN_TOP/src/policy/hooks/release-freeze-lock.sh"; do
    [ -r "$_c" ] && { _ESC_LIB="$_c"; break; }
  done
  if [ -n "$_ESC_LIB" ]; then
    # shellcheck source=/dev/null
    . "$_ESC_LIB" && freeze_lock_escape_hint commit
  else
    echo "  逃生:**判不出凍結窗的狀態**(找不到 release-freeze-lock.sh)," >&2
    echo "        所以這裡不敢告訴你 --no-verify 有沒有用。" >&2
  fi
  echo "" >&2
  exit 1
fi

# ── 被經過:逐支跑完,不短路;每支留痕 ──────────────────────────────
# rc 聚合規則:任一支非零 -> 整體非零(取最嚴的,不是取最後一支)。
WORST=0
FAILED=""
for g in "${GATES[@]}"; do
  path="$(resolve_gate "$g")"
  bash "$path" "$@"
  rc=$?
  printf '   · pre-commit 閘 %s rc=%s\n' "$g" "$rc" >&2
  if [ "$rc" -ne 0 ]; then
    WORST=1
    FAILED="$FAILED $g"
  fi
done

if [ "$WORST" -ne 0 ]; then
  echo "" >&2
  printf '✖ AIB pre-commit:%s 支閘中有閘擋下 ——%s\n' "${#GATES[@]}" "$FAILED" >&2
  echo "  (每一支都跑完了才回報,上面各支的 rc 逐行可查)" >&2
  echo "" >&2
  exit 1
fi
exit 0
