#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB hook 安裝器(T10,2026-08-27)—— 裁定 a seat 08-28 甲款
#
# ## 它解的是什麼
#
# 真正被 git 執行的 hook 住在 `core.hooksPath` 指的目錄裡,而那個目錄
# **不在版控**。08-28 量到的實況:
#
#   git rev-parse --git-path hooks   -> .git/ai-b/hooks
#   ls -1 該目錄                      -> 只有 pre-commit(mtime 08-15,已 12 天)
#   git ls-tree <main> | grep -c ai-b/hooks -> 0
#
# 於是 `src/policy/hooks/pre-push-adapter.sh` 這支**在版控裡好好的**轉接器,
# git 從來沒有呼叫過它一次 —— 閘門三段裡卡在第二段(存在,但沒被經過),
# 而產品自己的 doctor 把它記成 `status='manual'`:**既不綠也不紅**。
# 一個永遠 manual 的格子,沒有人必須為它做任何事。
#
# ★ 所以本檔的職責有兩個,缺一個都不算數:
#     1. 把版控正本**裝**到 git 真的會看的那個目錄
#     2. 回答「**現在裝的是什麼**」—— 少了哪一支、裝出去那份有沒有被改過
#
# ## 為什麼是複製不是 symlink
#
# 08-15 那兩支轉接器的檔頭已經寫過理由:**symlink 指向不存在的檔時 git 靜默
# 跳過**——不執行、不報錯、commit 照過。複製沒有這個失效形狀,代價是**會漂移**:
# 版控上寫著多派、機器上跑著 08-15 的單派化石,而兩邊各自看起來都是對的。
# 漂移用 `verify` 擋:裝出去那份必須與正本**逐位元組相同**。
#
# ## 用法
#
#   install-hooks.sh            等同 verify(唯讀,預設不寫任何東西)
#   install-hooks.sh verify     報每一個槽:缺 / 漂移 / 相同;有問題 rc=1 並點名
#   install-hooks.sh install    真的裝(會覆寫 hooks 目錄裡的同名檔)
#
#   --baseline <ref>   正本改讀**那一顆的 blob**,不讀工作樹那份(2026-09-07 加)
#   --porcelain        只印機器行 `<hook>\t<狀態>\t<正本sha16>\t<實裝sha16>`
#
# ## `--baseline` 解的是什麼(2026-09-07,pm 57b15)
#
# 本檔原本比的是**你跑它那棵樹**的 `src/policy/hooks/…`。09-07 抽樣 40 棵 worktree
# 的 `pre-push-adapter.sh`:**4 種不同的 blob**(21 / 13 / 4 / 2)。也就是說
# 「版控正本」在不同樹上根本不是同一份東西 —— 拿它當判準,40 棵裡有 27 棵會紅,
# 而那種人人都紅的格子會被關掉(本庫已有實證:`test-eye-ui-launch` 的「撞到就重跑」)。
# ⇒ 給機器讀的那條路改用一個**不隨你在哪棵樹跑而變**的基準:`origin/main` 的 blob。
#   main 正是所有 push 真正被擋的那條線。
# ★ 基準問不到(沒有那個 ref / 那顆沒有那支檔)一律回 **rc=3 與狀態 `no-baseline`**,
#   不得退回工作樹那份 —— 那會讓「我指定了基準」與「我指定的基準不存在」得到相同輸出。
#
# ## 結束碼
#   0 全部相同 / 1 有槽有問題(缺或漂移) / 2 參數錯 / 3 基準問不到(不是「有問題」)
#
# ★ 預設是唯讀的那一個。理由與本 repo 一貫的失效方向同一條:
#   **一個裝進去會影響整棵樹上每一個人的動作,不該是打錯字就會發生的那一個。**
#   `reference-transaction` 尤其:它攔的是這個 repo 的**每一次 ref 更新**,
#   不是只有發版那條路。裝它之前先讀 release-freeze-reftxn.sh 檔頭。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 槽對照表:hook 名 -> 版控正本 ─────────────────────────────────
SLOTS=(
  "pre-commit:pre-commit-adapter.sh"
  "pre-push:pre-push-adapter.sh"
  "reference-transaction:release-freeze-reftxn.sh"
)
# ★ 手寫登記數,同 pre-commit-adapter.sh 的理由:不得寫成 ${#SLOTS[@]}。
EXPECT_SLOTS=3

if [ "${#SLOTS[@]}" -ne "$EXPECT_SLOTS" ]; then
  echo "✖ install-hooks:槽清單($((${#SLOTS[@]})))與登記數($EXPECT_SLOTS)不符" >&2
  exit 1
fi

MODE="verify"
BASELINE=""
PORCELAIN=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    verify|install) MODE="$1"; shift ;;
    --baseline) BASELINE="${2:-}"; [ -n "$BASELINE" ] || { echo "install-hooks: --baseline 缺值" >&2; exit 2; }; shift 2 ;;
    --porcelain) PORCELAIN=1; shift ;;
    *) echo "install-hooks: 不認得的參數:$1(要 verify / install / --baseline <ref> / --porcelain)" >&2; exit 2 ;;
  esac
done

# 機器行只走 stdout,人看的敘述在 --porcelain 時整段不印(兩種輸出不混在一起)。
say() { [ "$PORCELAIN" -eq 1 ] || printf '%s\n' "$*"; }
NO_BASELINE=0
# 正本內容的來源。★ 只有一個地方決定「正本是什麼」,加基準模式沒有生出第二份判準。
src_bytes_to() {   # $1=版控相對名 $2=落點
  if [ -z "$BASELINE" ]; then
    cat "$HERE/$1" > "$2" 2>/dev/null
    return $?
  fi
  local rel
  rel="$(git -C "$HERE" ls-files --full-name -- "$1" 2>/dev/null | head -1)"
  [ -n "$rel" ] || return 1
  git -C "$HERE" show "$BASELINE:$rel" > "$2" 2>/dev/null
}

if [ "$MODE" = "install" ] && [ -n "$BASELINE" ]; then
  echo "install-hooks: install 不接受 --baseline(裝一份沒被簽出的 blob 是另一件事)" >&2
  exit 2
fi

HOOKDIR="$(git rev-parse --git-path hooks 2>/dev/null)"
if [ -z "$HOOKDIR" ]; then
  echo "✖ install-hooks:解不出 hooks 目錄(git rev-parse --git-path hooks)" >&2
  echo "  解不出來就不裝也不放行 —— 查不到證據不算通過。" >&2
  exit 1
fi

sha_of() {
  # 檔不在回空字串。空字串與任何 sha 都不相等,而**不會**與另一個「檔不在」相等的
  # 那種比較我們不做:下面每一格先問「在不在」,再問「一不一樣」。
  [ -r "$1" ] || return 1
  shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
}

say "hooks 目錄:$HOOKDIR"
say ""

PROBLEMS=0
MISSING_LIST=""
DRIFT_LIST=""

for entry in "${SLOTS[@]}"; do
  hook="${entry%%:*}"
  src_name="${entry#*:}"
  src="$HERE/$src_name"
  dst="$HOOKDIR/$hook"

  # 正本內容:預設是工作樹那份;給了 --baseline 就是那一顆的 blob。
  # ★ 取到不了**不退回工作樹那份**:那會讓「我指定了基準」與「基準不存在」同輸出。
  SRC_MATERIAL="$(mktemp "${TMPDIR:-/tmp}/aib-hook-src.XXXXXX")"
  if ! src_bytes_to "$src_name" "$SRC_MATERIAL"; then
    rm -f "$SRC_MATERIAL"
    if [ -n "$BASELINE" ]; then
      printf '  ✖ %-22s 基準 %s 問不到這支正本 —— 量不到,不是相同也不是漂移\n' \
        "$hook" "$BASELINE" >&2
      [ "$PORCELAIN" -eq 1 ] && printf '%s\tno-baseline\t-\t-\n' "$hook"
      NO_BASELINE=1
      continue
    fi
    printf '  ✖ %-22s 版控正本不見了:%s\n' "$hook" "$src" >&2
    PROBLEMS=$((PROBLEMS+1))
    MISSING_LIST="$MISSING_LIST $src_name(正本)"
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tsource-missing\t-\t-\n' "$hook"
    continue
  fi

  if [ "$MODE" = "install" ]; then
    mkdir -p "$HOOKDIR" || { echo "  ✖ 建不出 $HOOKDIR" >&2; exit 1; }
    cp "$src" "$dst" || { echo "  ✖ 裝不進 $dst" >&2; exit 1; }
    chmod +x "$dst" || { echo "  ✖ 設不了執行權:$dst" >&2; exit 1; }
  fi

  SRC_SHA="$(sha_of "$SRC_MATERIAL")" || SRC_SHA=""
  rm -f "$SRC_MATERIAL"
  DST_SHA="$(sha_of "$dst")" || DST_SHA=""

  if [ -z "$SRC_SHA" ]; then
    printf '  ✖ %-22s 算不出正本的 sha —— 算不出來不算相同\n' "$hook" >&2
    PROBLEMS=$((PROBLEMS+1))
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tunreadable\t-\t-\n' "$hook"
    continue
  fi
  if [ ! -e "$dst" ]; then
    printf '  ✖ %-22s 沒裝(hooks 目錄裡沒有這一支)\n' "$hook" >&2
    PROBLEMS=$((PROBLEMS+1))
    MISSING_LIST="$MISSING_LIST $hook"
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tmissing\t%s\t-\n' "$hook" "${SRC_SHA:0:16}"
    continue
  fi
  if [ -z "$DST_SHA" ]; then
    printf '  ✖ %-22s 裝了但讀不到 / 算不出 sha:%s\n' "$hook" "$dst" >&2
    PROBLEMS=$((PROBLEMS+1))
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tunreadable\t%s\t-\n' "$hook" "${SRC_SHA:0:16}"
    continue
  fi
  if [ "$SRC_SHA" != "$DST_SHA" ]; then
    printf '  ✖ %-22s **漂移**:裝出去那份與版控正本不同\n' "$hook" >&2
    printf '       正本 %s  %s\n' "${SRC_SHA:0:16}" \
      "$([ -n "$BASELINE" ] && echo "$BASELINE:$src_name" || echo "$src")" >&2
    printf '       實裝 %s  %s\n' "${DST_SHA:0:16}" "$dst" >&2
    PROBLEMS=$((PROBLEMS+1))
    DRIFT_LIST="$DRIFT_LIST $hook"
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tdrift\t%s\t%s\n' "$hook" "${SRC_SHA:0:16}" "${DST_SHA:0:16}"
    continue
  fi
  if [ ! -x "$dst" ]; then
    printf '  ✖ %-22s 位元組相同但**沒有執行權** —— 裝了等於沒裝\n' "$hook" >&2
    PROBLEMS=$((PROBLEMS+1))
    [ "$PORCELAIN" -eq 1 ] && printf '%s\tnot-executable\t%s\t%s\n' "$hook" "${SRC_SHA:0:16}" "${DST_SHA:0:16}"
    continue
  fi
  if [ "$PORCELAIN" -eq 1 ]; then
    printf '%s\tsame\t%s\t%s\n' "$hook" "${SRC_SHA:0:16}" "${DST_SHA:0:16}"
  else
    printf '  ✔ %-22s 與版控正本逐位元組相同(%s)\n' "$hook" "${SRC_SHA:0:16}"
  fi
done

# ── 安裝狀態本身:那個目錄裡實際有哪幾支 ─────────────────────────
# ★ 這一段回答的是「現在裝的是什麼」,不是「轉接器長什麼樣」。
#   少了這一段,多派做得再好,沒裝時整條路徑上一個位元組都不會被執行。
say ""
say "hooks 目錄實際內容(排除 *.sample):"
FOUND=0
if [ -d "$HOOKDIR" ]; then
  for f in "$HOOKDIR"/*; do
    [ -e "$f" ] || continue
    case "$f" in *.sample) continue ;; esac
    FOUND=$((FOUND+1))
    say "    $(basename "$f")"
  done
fi
[ "$FOUND" -eq 0 ] && say "    (空)"

say ""
# ★ 基準問不到排在「有問題」之前:量不到不是一種問題,它是「這一輪什麼都沒量到」。
#   兩者共用一個 rc 的話,消費者分不出「閘說有事」與「閘沒能問」。
if [ "$NO_BASELINE" -ne 0 ]; then
  echo "✖ 基準 $BASELINE 問不到正本 —— 本輪量不到,不當成相同也不當成漂移。" >&2
  echo "   查法:git rev-parse --verify $BASELINE;新 clone 沒 fetch 過就沒有這個 ref。" >&2
  exit 3
fi
if [ "$PROBLEMS" -ne 0 ]; then
  echo "✖ $PROBLEMS 個槽有問題。" >&2
  [ -n "$MISSING_LIST" ] && printf '   沒裝 /  缺正本:%s\n' "$MISSING_LIST" >&2
  [ -n "$DRIFT_LIST" ]   && printf '   漂移        :%s\n' "$DRIFT_LIST" >&2
  echo "   修法:在這棵樹上跑 src/policy/hooks/install-hooks.sh install" >&2
  echo "   ★ 手改 hooks 目錄裡那幾份 = 繞過版控,與『把寫入域擴到 .git/』同罪。" >&2
  exit 1
fi
[ "$PORCELAIN" -eq 1 ] || printf '✔ %s 個槽全部與版控正本相同(登記數 %s)\n' "${#SLOTS[@]}" "$EXPECT_SLOTS"
exit 0
