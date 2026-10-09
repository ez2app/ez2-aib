#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E8.1×E2.8 git 層咽喉(ED 系列,2026-08-14)——trust 判定第一個真閘門。
#
# 正本:docs/reports/E8.1-rev2-dual-basis.md §5 咽喉表(git 層攔 T0 的
# commit/push、T1 對保護分支 push)+開放問題 3(E8.1 定義、E2.8 執行)。
#
# ## 安裝語意(生死攸關,先讀)
#
# **本檔交付進 repo 不等於啟用。** 啟用=人把它連進 git **真的會讀**的那個
# hooks 目錄(pre-commit 與 pre-push 各一個 symlink 或 wrapper),那是一個顯式動作。
# ★ 那個目錄由 `core.hooksPath` 決定,本 repo 設的是 .git/ai-b/hooks;
#   `core.hooksPath` 一旦設定,git 就**完全不看** `.git/hooks/` —— 連進那裡會得到一個
#   永不執行也永不報錯的守衛(`tests/test-hook-install-path-guard.py` 在守這件事)。
#   查法:`git config core.hooksPath`,沒設才用預設目錄。永遠可退:
# 移除那個目錄裡的連結即全復原——本閘零線上依賴、零狀態檔,不存在
# 「壞掉後沒人能解」的鎖死路徑(前整合席顧問點:守衛不得把守衛自己關在
# 門外;token 洩漏/換人=移除連結重裝即可)。
#
# ## 判定鏈(逐步有正本出處)
#
# 1. 人類:偵測不到任何 AI session env 標記=人類操作,放行(E8.1 開放
#    問題 8:人類不在 T0-T3 軸上,人類收據即最高階證據)。
# 2. 整合席例外:AIB_GIT_INTEGRATOR_TOKEN 與 repo 內 token 檔雜湊相符=
#    放行+stderr 留痕(顯式 token 非隱式身份;「git 唯一寫者」自此有
#    機器面)。token 檔=版控內公開的 sha256,私值由 the maintainer 發給整合席。
#    ★token=**操作開關,不是安全機制**(整合席條件 3):env 可被任何同機
#    行程讀寫,無密碼學強度;它區分的是「角色操作」不是「身份真偽」。
#    EH 級身份是另一件事,本 token 不得被引用為「已有密碼學身份」的證據。
#
# ## ★安裝後的真實效果(整合席條件 1,白話明示,候 the maintainer 拍板)
#
# **啟用本閘=除了持 token 的整合席,所有 AI 席都不能 commit/push。**
# 推導鏈:EH 前全艦隊 trust 一律 self_reported → unbound → t0_unbound →
# 矩陣未授 git_commit/git_push_branch/merge_main。這正是「git 唯一寫者」
# 紀律的機器化;若 the maintainer 想要的是別的(例如各席可 commit 自己 worktree
# 分支),需先建立 T1 綁定語意再啟用——此判斷屬 the maintainer,不得由安裝者代裁。
# 3. 其餘 AI:ED.11 直起世界 trust=self_reported=unbound → t0_unbound
#    (定義層對映;三詞彙正式映射表候裁,但 self_reported≠bound 無歧義)。
#    T0 的 git_commit/git_push_branch/merge_main 不在白名單 → 攔。
#    判定走 src/policy/trust_tiers.py judge(單一真相源,本檔不長第二份
#    矩陣);trust_tiers 不可得=fail-closed 攔(壞掉的閘不是開著的門)。
#
# ## 用法
#   trust-git-gate.sh pre-commit
#   trust-git-gate.sh pre-push <remote> <url>   (refs 由 stdin 餵,git 慣例)
#   trust-git-gate.sh --self-test               (預演:列出「若啟用,當下
#                                                身份會被擋什麼」,零副作用)
# rc:0=放行 1=攔下 2=用法/內部錯(git 對 hook 非零=中止操作,2 同樣攔)
set -u

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY3="${EZ2_PY3:-python3}"
PROTECTED_BRANCHES="${AIB_PROTECTED_BRANCHES:-main}"
TOKEN_SHA_FILE="${AIB_GIT_TOKEN_SHA_FILE:-$HERE/integrator-token.sha256}"

say() { echo "trust-git-gate: $*" >&2; }

is_ai_session() {
  # AI session env 標記(存在任一=AI 操作)。人類終端不帶這些。
  [ -n "${CLAUDE_CODE_SESSION_ID:-}" ] && return 0
  [ -n "${CODEX_THREAD_ID:-}${CODEX_SESSION_ID:-}" ] && return 0
  [ -n "${ANTIGRAVITY_SESSION:-}${AGY_SESSION_ID:-}" ] && return 0
  return 1
}

integrator_token_ok() {
  [ -n "${AIB_GIT_INTEGRATOR_TOKEN:-}" ] || return 1
  [ -f "$TOKEN_SHA_FILE" ] || return 1
  local want got
  want="$(tr -d '[:space:]' < "$TOKEN_SHA_FILE")"
  got="$(printf '%s' "$AIB_GIT_INTEGRATOR_TOKEN" | "$PY3" -c \
    'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
  [ -n "$want" ] && [ "$want" = "$got" ]
}

judge_action() {
  # 單一真相源:trust_tiers.judge。rc 0=allow 4=deny 2=形狀錯(=攔)。
  "$PY3" "$HERE/../trust_tiers.py" judge t0_unbound "$1" bound_unattested \
    >/dev/null 2>&1
  local rc=$?
  [ "$rc" -eq 0 ] && return 0
  return 1
}

classify_push_ref() {
  # $1=remote ref(refs/heads/x)。保護分支=merge_main 語意,其餘=git_push_branch。
  local ref="${1#refs/heads/}" b
  for b in $PROTECTED_BRANCHES; do
    [ "$ref" = "$b" ] && { echo merge_main; return; }
  done
  echo git_push_branch
}

gate() {
  # $1=矩陣動作;放行回 0,攔回 1(附人讀理由)。
  local action="$1"
  if ! is_ai_session; then
    return 0    # 人類:不在 T0-T3 軸,放行(開放問題 8)
  fi
  if integrator_token_ok; then
    say "integrator token 放行(${action});留痕:git 唯一寫者機器面"
    return 0
  fi
  if judge_action "$action"; then
    return 0
  fi
  say "攔下:AI session(self_reported=t0_unbound)無 ${action} 能力"
  say "  依據=E8.1 能力矩陣(白名單未列即禁);整合席請帶 AIB_GIT_INTEGRATOR_TOKEN"
  say "  申訴/例外=找現任艦長或整合席;移除 .git/hooks 連結=停用本閘(可退)"
  return 1
}

case "${1:-}" in
  pre-commit)
    gate git_commit
    ;;
  pre-push)
    rc=0
    while read -r _local_ref _local_sha remote_ref _remote_sha; do
      [ -n "${remote_ref:-}" ] || continue
      action="$(classify_push_ref "$remote_ref")"
      gate "$action" || rc=1
    done
    exit "$rc"
    ;;
  --self-test)
    # 預演(顧問點:啟用前先看會擋什麼;零副作用)
    if is_ai_session; then who="AI session(t0_unbound)"; else who="人類(無 AI env 標記)"; fi
    echo "trust-git-gate --self-test:當下身份=$who"
    if integrator_token_ok; then echo "  integrator token=有效(全放行)"; fi
    # 整合席條件 2:守衛自鎖最可能發生在唯一持鑰者身上——token 缺失情境明示
    if is_ai_session && ! integrator_token_ok; then
      echo "  ⚠ token 缺失情境:若你是整合席而 AIB_GIT_INTEGRATOR_TOKEN 不在 env,"
      echo "    下列「會被攔」全部會攔到你自己;復原=移除 .git/hooks 連結(零依賴)"
    fi
    for action in git_commit git_push_branch merge_main; do
      if ! is_ai_session || integrator_token_ok || judge_action "$action"; then
        echo "  $action: 放行"
      else
        echo "  $action: 會被攔"
      fi
    done
    exit 0
    ;;
  *)
    say "用法:trust-git-gate.sh pre-commit|pre-push|--self-test"
    exit 2
    ;;
esac
