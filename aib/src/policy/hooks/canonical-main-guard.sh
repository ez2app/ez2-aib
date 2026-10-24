#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB-CANONICAL-MAIN-GUARD v1 —— 正本 main 禁止直接提交/合併(推包管道 P8;docs/DESIGN-ship-merge-queue-1011.md §4b P8;
#   the maintainer 10-11 核准,a seat 10-12 裁「乙」:只裝本檔成 pre-commit 與 pre-merge-commit,不碰 pre-commit-adapter 那兩道)
#
# 為什麼:10-11 Pro 席把 3 筆修正直接合進 Pro 正本 main(沒交推包席、沒上 GitHub)⇒ Pro 無法快轉,艦長室新版上不去。
#   規則(AGENTS.md「正本樹只准讀」)早就有,但沒有東西擋。
#
# 擋什麼:**主工作樹**(不是 git worktree 分出去的那些)且 HEAD 在 main ⇒ 擋下 git commit / git merge(非快轉)。
# 不擋什麼:
#   - 分出去的 worktree(各席的分支)—— hooks 目錄是全部 worktree 共用的,所以一定要先分辨是不是主工作樹;
#   - ship 快轉正本(git merge --ff-only):快轉不建提交,git 不跑 pre-commit / pre-merge-commit,本來就碰不到;
#   - 主工作樹在別的分支。
# 已知擋不到(要 reference-transaction 才擋得到,乙案刻意不碰):cherry-pick / revert / am / commit --no-verify。
# ★ 跟 pre-commit-adapter.sh 共用 pre-commit 槽:之後若有人跑 install-hooks.sh install 裝轉接器,會蓋掉本檔;
#   那時 install-canonical-guard.sh 會回報槽被佔(foreign),要把本檔登記進轉接器的閘清單(另案評估,a seat 10-12)。
set -u

GIT_DIR_ABS="$(cd "$(git rev-parse --git-dir)" && pwd -P)"
COMMON_ABS="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
[ "$GIT_DIR_ABS" = "$COMMON_ABS" ] || exit 0          # 分出去的 worktree:不管
[ "$(git symbolic-ref -q --short HEAD)" = "main" ] || exit 0

TOP="$(git rev-parse --show-toplevel)"
{
  echo ""
  echo "✖ 正本樹 $TOP 在 main:不准直接提交或合併(推包管道 P8)。"
  echo "  改法:開分支或 git worktree add,在那裡提交,再交給推包席。"
  echo "  新樹開在 ~/wt.noindex/<名>(Mac 的 Spotlight 不掃 .noindex 夾,測試跑起來不會拖慢機器)。"
  echo "  正本只由 aib ship 推成後自動快轉(那一步不建提交,不會被這裡擋)。"
  echo "  ★ 如果剛才是 git merge / git pull 被擋:正本現在停在「合併到一半」,請馬上 git merge --abort 退回,"
  echo "    否則之後每次推包都會因為正本有改動而跳過自動快轉。"
  echo ""
} >&2
exit 1
