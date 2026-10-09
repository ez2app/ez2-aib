#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# worktree-inventory.sh —— 一個 repo 的工作樹「只列不刪」盤點。
#
# 由來:2026-09-05,`git worktree list` 在 ez2-aib 上回 723 筆,而**沒有人登記過
# 這個數字**;`~/worktrees` 底下同時住著三個 repo 的樹(見 tools/worktree-sweep.py
# 檔頭 08-15 的實測),所以「目錄數」與「某個 repo 的樹數」從來不是同一個母體。
#
# ## 與 tools/worktree-sweep.py 的分工(刻意不重寫它的取法)
#
#   worktree-sweep.py  以**檔案系統**為母體:掃 --root 底下的目錄,用 .git 檔的
#                      gitdir 反查歸屬,判「可不可以刪」,並且**會刪**。
#   本支              以**登記簿**為母體:`git worktree list --porcelain`,只列不刪,
#                      而且列的是登記簿裡的每一棵——包含不在 --root 底下的那些。
#
# 兩者的母體不相等,而**差集正是最容易漏掉的那一批**(散在 ~ 直下與 session
# scratchpad 裡的樹)。本支印 `--compare-root` 就是為了把那個差集數出來。
# 判準(乾淨/已入庫)沿用 sweep 的那兩條,不另立一套。
#
# ## 三個「讀不到時長得像正常值」的失效面(本支各擋一次)
#
#   (1) 目錄不見了 / 被清空 —— `git status --porcelain` 的輸出是空的,而空輸出
#       跟「乾淨」逐字相同。⇒ 先驗 `.git` 存在,再驗 rc,兩者任一不過就是
#       `unknown`,**不是** `safe`。
#   (2) 目錄被清空但仍在別的 repo 的工作樹裡 —— git 會往上找到**父 repo**,
#       回報父 repo 的狀態,rc=0 而且看起來完全正常。
#       ⇒ 斷言 `rev-parse --show-toplevel` 就是這一棵自己。
#   (3) 基準 ref 不存在 —— 每一棵都會被判成「未推」或每一棵都 unknown,
#       而那是一張格式完全正確的表。⇒ 開頭就驗 --base,不成立直接 rc=2 收工。
#
# 「判不出來」永遠自成一桶,不併進三桶裡的任何一桶。
#
# 用法:
#   tools/cockpit-ops/worktree-inventory.sh                      # markdown 到 stdout
#   tools/cockpit-ops/worktree-inventory.sh --out FILE
#   tools/cockpit-ops/worktree-inventory.sh --repo ~/ez2-ai --base origin/main
#   tools/cockpit-ops/worktree-inventory.sh --tsv                # 機器讀
#   tools/cockpit-ops/worktree-inventory.sh --compare-root ~/worktrees
#
# 本支**不刪任何東西**,也沒有可以讓它刪的旗標。要刪走 tools/worktree-sweep.py。

set -uo pipefail

REPO=""
BASE="origin/main"
OUT=""
FORMAT="md"
WITH_SIZE=1
COMPARE_ROOT=""

die() { printf '[ERROR] %s\n' "$1" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)          REPO="${2:-}"; shift 2 ;;
    --base)          BASE="${2:-}"; shift 2 ;;
    --out)           OUT="${2:-}"; shift 2 ;;
    --compare-root)  COMPARE_ROOT="${2:-}"; shift 2 ;;
    --tsv)           FORMAT="tsv"; shift ;;
    --md)            FORMAT="md"; shift ;;
    --no-size)       WITH_SIZE=0; shift ;;
    -h|--help)       sed -n '2,40p' "$0"; exit 0 ;;
    # ★ 具名拒絕:不吃未知旗標。argparse 式的「usage 訊息」與具名拒絕在 rc 上
    #   同形,所以這裡自己回 rc=2 並把旗標名字印出來。
    *)               die "未知旗標:$1(本支沒有刪除旗標,要刪走 worktree-sweep.py)" ;;
  esac
done

if [ -z "${REPO}" ]; then
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
fi
REPO="$(cd "${REPO}" 2>/dev/null && pwd)" || die "--repo 不存在:${REPO}"

git -C "${REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "--repo 不是 git 工作樹:${REPO}"

# 失效面 (3):基準不存在的話,整張表會格式正確而每一格都錯。
BASE_SHA="$(git -C "${REPO}" rev-parse --verify "${BASE}^{commit}" 2>/dev/null)" \
  || die "基準 ref 不存在:${BASE}(整張表會格式正確而每一格都錯,所以這裡直接停)"

GEN_AT="$(date '+%Y-%m-%d %H:%M:%S %z')"
MAIN_WT="$(git -C "${REPO}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
MAIN_WT="$(cd "$(dirname "${MAIN_WT}")" && pwd)"

# ---------------------------------------------------------------- 逐棵盤點
# 每一列:path \t branch \t sha \t ancestor \t porcelain \t size_kb \t last_commit \t bucket
ROWS="$(
  git -C "${REPO}" worktree list --porcelain 2>/dev/null | awk '
    /^worktree /   { if (p != "") print p "\t" b "\t" h "\t" bare; p=substr($0,10); b="-"; h="-"; bare="0" }
    /^HEAD /       { h=substr($0,6) }
    /^branch /     { b=substr($0,8); sub(/^refs\/heads\//,"",b) }
    /^detached$/   { b="(detached)" }
    /^bare$/       { bare="1"; b="(bare)" }
    END            { if (p != "") print p "\t" b "\t" h "\t" bare }
  ' | while IFS=$'\t' read -r wpath wbranch whead wbare; do
        bucket=""; ancestor="?"; porc="?"; size="?"; last="?"
        real="$(cd "${wpath}" 2>/dev/null && pwd)"

        # 裸倉的登記列沒有工作樹:rev-parse --show-toplevel 對它 rc=128。
        # 那個 rc 跟「目錄被清空」的 rc 逐字相同,所以要在守衛**之前**分掉,
        # 否則一個好端端的裸倉會被寫成「判不出來」。(2026-09-05 實測:
        # /Users/user/ez2-ai 就是裸倉,第一版把它算進 unknown。)
        if [ "${wbare}" = "1" ]; then
          bucket="mainwt"; note="裸倉(bare),沒有工作樹可移除"
          ancestor="-"; porc="-"
        # 失效面 (1):.git 不見了 —— 空輸出與「乾淨」同形,先在這裡分開。
        elif [ ! -e "${wpath}/.git" ]; then
          bucket="unknown"; note="無 .git(目錄不存在或已被清空)"
        else
          # 失效面 (2):被清空但落在別的 repo 裡,git 會往上找到父 repo 而 rc=0。
          top="$(git -C "${wpath}" rev-parse --show-toplevel 2>/dev/null)"; trc=$?
          if [ ${trc} -ne 0 ]; then
            bucket="unknown"; note="rev-parse --show-toplevel rc=${trc}"
          elif [ "$(cd "${top}" 2>/dev/null && pwd)" != "${real}" ]; then
            bucket="unknown"; note="toplevel 指到別處(${top}),這一棵已經不是自己"
          fi
        fi

        if [ -z "${bucket}" ]; then
          # 量測與判定分成兩條:rc 與行數各自留住。
          sout="$(git -C "${wpath}" status --porcelain 2>/dev/null)"; src=$?
          if [ ${src} -ne 0 ]; then
            bucket="unknown"; note="status --porcelain rc=${src}"
          else
            porc="$(printf '%s' "${sout}" | grep -c '')"
          fi
        fi

        if [ -z "${bucket}" ]; then
          # 祖先在**主 repo** 問(共用 object db,而且不受該棵樹自身狀態影響)。
          git -C "${REPO}" merge-base --is-ancestor "${whead}" "${BASE_SHA}" 2>/dev/null
          arc=$?
          case ${arc} in
            0) ancestor="yes" ;;
            1) ancestor="no"  ;;
            *) ancestor="?"; bucket="unknown"; note="merge-base --is-ancestor rc=${arc}" ;;
          esac
        fi

        if [ "${whead}" = "-" ]; then last="-"
        else last="$(git -C "${REPO}" log -1 --format=%cI "${whead}" 2>/dev/null || printf '?')"
        fi
        [ -z "${last}" ] && last="?"
        if [ "${WITH_SIZE}" = "1" ]; then
          size="$(du -sk "${wpath}" 2>/dev/null | awk "{print \$1}")"
          [ -z "${size}" ] && size="?"
        else
          size="-"
        fi

        # 分桶。順序是判準的一部分:判不出來 > 主工作樹 > 髒 > 未推 > 可移除。
        # 「主工作樹」不併進可移除——它清而且已入庫時,長得跟可移除逐字相同,
        # 而 git worktree remove 對它必定失敗。
        if [ -z "${bucket}" ]; then
          if [ "${real}" = "${MAIN_WT}" ]; then bucket="mainwt"; note="主工作樹,不可移除"
          elif [ "${porc}" != "0" ];      then bucket="dirty";  note="未提交 ${porc} 行"
          elif [ "${ancestor}" = "no" ];  then bucket="unpushed"; note="HEAD 不在 ${BASE} 裡"
          else                                 bucket="safe";   note="乾淨且已入庫"
          fi
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "${wpath}" "${wbranch}" "${whead}" "${ancestor}" "${porc}" "${size}" "${last}" "${bucket}" "${note}"
      done
)"

TOTAL="$(printf '%s' "${ROWS}" | grep -c '')"
n_of() { printf '%s\n' "${ROWS}" | awk -F'\t' -v b="$1" '$8==b' | grep -c ''; }
N_SAFE="$(n_of safe)"; N_UNPUSHED="$(n_of unpushed)"; N_DIRTY="$(n_of dirty)"
N_UNKNOWN="$(n_of unknown)"; N_MAIN="$(n_of mainwt)"
SUM=$(( N_SAFE + N_UNPUSHED + N_DIRTY + N_UNKNOWN + N_MAIN ))

# 重疊量:髒**而且**未推的有幾棵。分桶把它們算進「髒」,這個數字讓那件事看得見。
N_BOTH="$(printf '%s\n' "${ROWS}" | awk -F'\t' '$8=="dirty" && $4=="no"' | grep -c '')"

emit_tsv() {
  printf '# path\tbranch\thead\tancestor_of_%s\tporcelain\tsize_kb\tlast_commit\tbucket\tnote\n' "${BASE}"
  printf '%s\n' "${ROWS}"
  printf '# total=%s safe=%s unpushed=%s dirty=%s unknown=%s mainwt=%s sum=%s dirty_and_unpushed=%s\n' \
    "${TOTAL}" "${N_SAFE}" "${N_UNPUSHED}" "${N_DIRTY}" "${N_UNKNOWN}" "${N_MAIN}" "${SUM}" "${N_BOTH}"
}

emit_md() {
  printf '# 工作樹盤點(只列不刪)\n\n'
  printf -- '- repo `%s`\n' "${REPO}"
  printf -- '- 基準 `%s` = `%s`\n' "${BASE}" "${BASE_SHA}"
  printf -- '- 主工作樹 `%s`\n' "${MAIN_WT}"
  printf -- '- 產生於 %s ,母體 = `git worktree list` 登記簿 **%s 棵**\n' "${GEN_AT}" "${TOTAL}"
  printf -- '- 本支不刪任何東西。刪除工具是 `tools/worktree-sweep.py`(判準相同)。\n\n'

  printf '## 一 分桶(三桶各數一次,0 也上表)\n\n'
  printf '| 桶 | 定義 | 棵數 |\n|---|---|---|\n'
  printf '| 可安全移除 | 乾淨(porcelain=0)且 HEAD 已在 `%s` 祖先 | **%s** |\n' "${BASE}" "${N_SAFE}"
  printf '| 有未推顆 | 乾淨,但 HEAD 不在 `%s` 裡 | **%s** |\n' "${BASE}" "${N_UNPUSHED}"
  printf '| 有未提交 | porcelain > 0(髒優先於未推,不重複計) | **%s** |\n' "${N_DIRTY}"
  printf '\n三桶之外,另有兩類**刻意不併進三桶**的:\n\n'
  printf '| 類 | 為什麼不併 | 棵數 |\n|---|---|---|\n'
  printf '| 判不出來 | 觀測失敗的回值跟「乾淨」同形,併進去就是把不知道寫成安全 | **%s** |\n' "${N_UNKNOWN}"
  printf '| 主工作樹/裸倉 | 清且已入庫時跟「可安全移除」逐字相同,但 remove 對它必失敗 | **%s** |\n' "${N_MAIN}"
  printf '\n對帳:%s + %s + %s + %s + %s = **%s**,母體 **%s** ⇒ %s\n' \
    "${N_SAFE}" "${N_UNPUSHED}" "${N_DIRTY}" "${N_UNKNOWN}" "${N_MAIN}" "${SUM}" "${TOTAL}" \
    "$( [ "${SUM}" = "${TOTAL}" ] && printf '對上' || printf '**對不上,這張表不可引用**' )"
  printf '\n\n其中「髒**而且**未推」**%s** 棵——分桶把它們算在「有未提交」,\n' "${N_BOTH}"
  printf '所以「有未推顆 %s」讀作「乾淨但未推」,不是「未推的總數」(總數 %s)。\n\n' \
    "${N_UNPUSHED}" "$(( N_UNPUSHED + N_BOTH ))"

  if [ "${WITH_SIZE}" = "1" ]; then
    local tot
    tot="$(printf '%s\n' "${ROWS}" | awk -F'\t' '$6 ~ /^[0-9]+$/ {s+=$6} END{printf "%.1f", s/1048576}')"
    printf '磁碟合計 **%s GiB**(`du -sk` 逐棵相加;量不到大小的以 `?` 入表,不計入)。\n\n' "${tot}"
  fi

  if [ -n "${COMPARE_ROOT}" ]; then
    local croot cn inreg outreg
    croot="$(cd "${COMPARE_ROOT}" 2>/dev/null && pwd)" || croot=""
    printf '## 二 兩個母體的差集(`--compare-root %s`)\n\n' "${COMPARE_ROOT}"
    if [ -z "${croot}" ]; then
      printf '`%s` 不存在,差集未量。\n\n' "${COMPARE_ROOT}"
    else
      cn="$(find "${croot}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | grep -c '')"
      inreg="$(printf '%s\n' "${ROWS}" | awk -F'\t' -v r="${croot}/" 'index($1,r)==1' | grep -c '')"
      outreg=$(( TOTAL - inreg ))
      printf -- '- `%s` 底下的目錄:**%s** 個(含**別的 repo** 的樹,見 worktree-sweep.py 檔頭)\n' "${croot}" "${cn}"
      printf -- '- 其中屬於本 repo 登記簿的:**%s** 棵\n' "${inreg}"
      printf -- '- 本 repo 登記簿裡**不在**這個根底下的:**%s** 棵 ← 只掃目錄的工具看不到這一批\n\n' "${outreg}"
    fi
  fi

  printf '## 三 逐棵(%s 棵)\n\n' "${TOTAL}"
  printf '| # | 路徑 | 分支 | HEAD | 在 %s 祖先 | porcelain | KiB | 最後 commit | 桶 |\n' "${BASE}"
  printf '|---:|---|---|---|:---:|---:|---:|---|---|\n'
  printf '%s\n' "${ROWS}" | awk -F'\t' '
    { n++; printf "| %d | `%s` | `%s` | `%s` | %s | %s | %s | %s | %s |\n",
        n, $1, $2, substr($3,1,8), $4, $5, $6, substr($7,1,19), $8 }'
}

if [ "${FORMAT}" = "tsv" ]; then BODY="$(emit_tsv)"; else BODY="$(emit_md)"; fi
if [ -n "${OUT}" ]; then printf '%s\n' "${BODY}" > "${OUT}"; printf '%s\n' "${OUT}"; else printf '%s\n' "${BODY}"; fi

# rc=0 只代表「盤完了」。對帳對不上是判準壞了,要跟「盤完了」分開。
[ "${SUM}" = "${TOTAL}" ] || exit 3
exit 0
