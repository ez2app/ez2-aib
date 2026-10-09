#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 發版逃生門紀錄 —— 去重鍵與收斂條件的單一真相源(T10,2026-08-27)
#
# ## 為什麼從發版腳本裡搬出來
#
# 判準 S9 逐字:**禁以碼面宣稱為證** —— 以註解 / 常數名 / grep 命中字面
# 證明行為,一律不接受。而逃生記檔原本整段長在 `aib-release.sh` 的
# 「CI 矩陣閘」分支裡,那條路徑要**真的發一次版**才走得到。
# 結果就是:它的性質只能靠讀碼宣稱,不能靠執行證明。
#
# 搬出來之後,考卷可以直接呼叫 `escape_log_append` 拿真 rc 與真檔案內容,
# 而 `aib-release.sh` 呼叫的是同一支函式 —— **不是兩份近似實作**。
#
# ## 去重鍵
#
# 鍵 = 版號 + 閘 rc + 理由。**不含 commit sha**:第十二條的兩段式流程
# (寫入 -> 要求 commit -> 重跑)會讓 HEAD 前進,含 sha 的鍵在重跑時算出
# 不同的一列 -> 再追加 -> 檔案又變髒 -> 又被擋 -> **不會收斂**。
#
# ## 收斂條件
#
# **這個迴圈的收斂條件是「閘 rc 不再變」,不是「問題修好了」。**
# 兩者不同:rc 若在兩值間擺盪會永遠追下去,而每一跑都看起來像有進展。
set -uo pipefail

ESC_KEY_FIELDS=("version" "gate_rc" "reason")
# ★ 手寫登記數,同 pre-commit-adapter.sh 的 EXPECT_GATES:**不得寫成 ${#ESC_KEY_FIELDS[@]}**。
#   從清單算就是多一條同源的腿——欄位被刪一個時兩個數會一起掉並互相印證。
ESC_KEY_N=3
ESC_KEY_DECL="去重鍵 = 版號 + 閘 rc + 理由(3 欄,不含 commit sha)"
ESC_CONVERGE_DECL="這個迴圈的收斂條件是「閘 rc 不再變」,不是「問題修好了」。"

# 由**宣告的欄位**組出那一列。欄位增減時「碼裡怎麼算」與「檔裡怎麼寫」
# 不可能各走各的路:欄位數與手寫登記數不符就直接硬錯。
# 用法:escape_log_row <version> <gate_rc> <reason>
escape_log_row() {
  # bash 3.2 (macOS /bin/bash) 沒有關聯陣列;用 case 取值,BSD/GNU 都能跑。
  local version="${1-}" gate_rc="${2-}" reason="${3-}" k row val
  if [ "${#ESC_KEY_FIELDS[@]}" -ne "$ESC_KEY_N" ]; then
    printf '逃生去重鍵欄位數(%s)與手寫登記數(%s)不符 —— 改了鍵沒改宣告\n' \
      "${#ESC_KEY_FIELDS[@]}" "$ESC_KEY_N" >&2
    return 3
  fi
  row="|"
  for k in "${ESC_KEY_FIELDS[@]}"; do
    case "$k" in
      version) val="$version" ;;
      gate_rc) val="$gate_rc" ;;
      reason)  val="$reason" ;;
      *)
        printf '逃生去重鍵有欄位 %s 取不到值 —— 宣告與實作對不起來\n' "$k" >&2
        return 3
        ;;
    esac
    row="$row ${val} |"
  done
  printf '%s\n' "$row"
}

# 寫一列進逃生紀錄。同鍵不重複追加。
# 用法:escape_log_append <檔案> <version> <gate_rc> <reason>
# rc:0=已在檔裡(這次寫的或本來就在),1=寫不進去 / 讀不回來(不放行)
escape_log_append() {
  local f="${1-}" version="${2-}" gate_rc="${3-}" reason="${4-}" row rc
  [ -n "$f" ] || { echo "escape_log_append: 沒給檔案路徑" >&2; return 1; }
  row="$(escape_log_row "$version" "$gate_rc" "$reason")"; rc=$?
  [ "$rc" -eq 0 ] || return 1

  mkdir -p "$(dirname "$f")" || {
    printf '逃生紀錄:建不出目錄 %s\n' "$(dirname "$f")" >&2; return 1; }

  if [ ! -s "$f" ]; then
    printf '# 發版逃生門紀錄\n\n本檔記錄每一次「矩陣閘擋下但仍然發版」的理由。\n理由會累積成清單,而清單自己會說話。\n\n- %s\n- %s\n\n| 版本 | 閘 rc | 理由 |\n|---|---|---|\n' \
      "$ESC_KEY_DECL" "$ESC_CONVERGE_DECL" >> "$f" || {
      printf '逃生紀錄:表頭寫不進 %s\n' "$f" >&2; return 1; }
  fi

  # ★ 既有的那份檔是本節上線前建的,表頭沒有這兩句。**原文不改、另起一節**
  #   (同艦規:可以事後改的登記就不是登記)。
  if ! grep -qF -- "$ESC_KEY_DECL" "$f" 2>/dev/null; then
    printf '\n## 去重與收斂(T10 補記,原表不改)\n\n- %s\n- %s\n' \
      "$ESC_KEY_DECL" "$ESC_CONVERGE_DECL" >> "$f" || {
      printf '逃生紀錄:宣告補不進 %s\n' "$f" >&2; return 1; }
  fi
  # 宣告記不下來就不放行 —— 標準與逃生門本身同一條:
  # 記不下來的宣告比沒有宣告更糟,它會宣稱自己記了。
  grep -qF -- "$ESC_KEY_DECL" "$f" 2>/dev/null || {
    printf '逃生紀錄:去重鍵宣告寫不進 %s\n' "$f" >&2; return 1; }
  grep -qF -- "$ESC_CONVERGE_DECL" "$f" 2>/dev/null || {
    printf '逃生紀錄:收斂條件寫不進 %s\n' "$f" >&2; return 1; }

  if ! grep -qF -- "$row" "$f" 2>/dev/null; then
    printf '%s\n' "$row" >> "$f" || {
      printf '逃生紀錄:那一列寫不進 %s\n' "$f" >&2; return 1; }
  fi
  # 寫完讀回:寫入的 rc=0 不是落檔證據。
  grep -qF -- "$row" "$f" 2>/dev/null || {
    printf '逃生紀錄:寫完讀不回那一列 %s\n' "$f" >&2; return 1; }
  return 0
}

# 直接執行時的 CLI(被 source 時不生效)——考卷靠它拿真 rc,不重寫一份近似實作。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    row)    shift; escape_log_row "$@"; exit $? ;;
    append) shift; escape_log_append "$@"; exit $? ;;
    *) echo "用法:$0 row <version> <rc> <reason> | $0 append <檔> <version> <rc> <reason>" >&2
       exit 2 ;;
  esac
fi
