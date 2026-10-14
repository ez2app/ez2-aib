#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB pre-push 轉接器(2026-08-15)
#
# 與同層 pre-commit 轉接器同一條理由,不重述:**symlink 指向不存在的檔時
# git 靜默跳過**——不執行、不報錯、push 照過。轉接器的職責是把那個靜默
# 變成大聲:找不到腳本就擋下,並說明找過哪裡。
#
# ★ 這支特別重要:pre-commit 消失了,下一次 commit 很快就會有人發現;
#   pre-push 的 tag 閘一年只用幾次,**它消失了可以很久沒有人知道**,
#   而每一次沒被發現,就是一個裝到壞版本的使用者。
set -euo pipefail

TOP="$(git rev-parse --show-toplevel)"
COMMON="$(git rev-parse --git-common-dir)"
MAIN_TOP="$(cd "$COMMON/.." && pwd -P)"

RELEASE_GATE=""
for candidate in \
  "$TOP/src/policy/hooks/release-tag-gate.sh" \
  "$MAIN_TOP/src/policy/hooks/release-tag-gate.sh"; do
  [ -r "$candidate" ] && { RELEASE_GATE="$candidate"; break; }
done

if [ -n "$RELEASE_GATE" ]; then
  REF_INPUT="$(mktemp "${TMPDIR:-/private/tmp}/aib-pre-push.XXXXXX")" || {
    echo "✖ AIB pre-push 建不出 refs 暫存檔,擋下這次 push" >&2
    exit 1
  }
  trap 'rm -f "$REF_INPUT"' EXIT
  cat > "$REF_INPUT"
  if bash "$RELEASE_GATE" "$@" < "$REF_INPUT"; then
    :
  else
    RELEASE_RC=$?
    exit "$RELEASE_RC"
  fi

  if awk '$3 == "refs/heads/main" { found=1 } END { exit !found }' "$REF_INPUT"; then
    B8_GATE=""
    for candidate in \
      "$TOP/tools/governance/b8-machine-rules.py" \
      "$MAIN_TOP/tools/governance/b8-machine-rules.py"; do
      [ -r "$candidate" ] && { B8_GATE="$candidate"; break; }
    done
    if [ -z "$B8_GATE" ]; then
      echo "✖ AIB pre-push 找不到 B8 main 閘,擋下這次 push" >&2
      exit 1
    fi
    B8_ARGS=(main-push --repo "$TOP")
    if [ -n "${AIB_B8_MAIN_PUSH_RECEIPT:-}" ]; then
      B8_ARGS+=(--receipt "$AIB_B8_MAIN_PUSH_RECEIPT")
    fi
    # 推包收據(10-14):ship.py 推 main 前寫、只帶給這一次 git push;B8 收據優先,閘裡驗綁定
    if [ -n "${AIB_B8_SHIP_RECEIPT:-}" ]; then
      B8_ARGS+=(--ship-receipt "$AIB_B8_SHIP_RECEIPT")
    fi
    # GATE_NEW_UNLABELED_PINNED
    B8_ARGS+=(--gate-new-unlabeled-pinned)
    python3 "$B8_GATE" "${B8_ARGS[@]}" < "$REF_INPUT"
    exit $?
  fi
  exit 0
fi

echo "" >&2
echo "✖ AIB pre-push 守衛找不到腳本,擋下這次 push" >&2
echo "" >&2
echo "  找過:" >&2
echo "    $TOP/src/policy/hooks/release-tag-gate.sh" >&2
echo "    $MAIN_TOP/src/policy/hooks/release-tag-gate.sh" >&2
echo "" >&2
echo "  守衛消失比守衛擋人更嚴重——先確認那支檔在不在,不要繞過。" >&2
# ★ 批7:那句逃生在**窗開著時是假的**(push 也是 ref 更新,而
#   reference-transaction 沒有 --no-verify)。判斷在鎖程式庫,這裡只呼叫;
#   載不到就**明說判不出來**,不默默給任何一句 —— 那會在錯的一半時候騙人。
_ESC_LIB=""
for _c in "$TOP/src/policy/hooks/release-freeze-lock.sh" \
          "$MAIN_TOP/src/policy/hooks/release-freeze-lock.sh"; do
  [ -r "$_c" ] && { _ESC_LIB="$_c"; break; }
done
if [ -n "$_ESC_LIB" ]; then
  # shellcheck source=/dev/null
  . "$_ESC_LIB" && freeze_lock_escape_hint push
else
  echo "  逃生:**判不出凍結窗的狀態**(找不到 release-freeze-lock.sh)," >&2
  echo "        所以這裡不敢告訴你 --no-verify 有沒有用。" >&2
fi
echo "" >&2
exit 1
