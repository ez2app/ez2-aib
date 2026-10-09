#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# E2.6 就地 BIND:在剛開好的中欄視窗裡跑這支,把綁定申請送進決策收件匣。
#
#   aib-bind.sh request --provider claude --role dev1 [--session-uuid U]
#   aib-bind.sh list [--status pending]
#   aib-bind.sh approve --request-id ID --approver human:the maintainer [--reason ...]
#   aib-bind.sh reject  --request-id ID --approver human:the maintainer --reason ...
#
# 為什麼 uuid 不由啟動器代填:啟動器開窗的當下,那個 AI 還沒起來,它的 session
# uuid 不存在。代填只能靠猜,而猜出來的身份就是假身份。所以 BIND 是**就地**輸入:
# 由已經活著的那個 session 自己報 uuid,再由人核可。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY3="${EZ2_PY3:-}"
if [ -z "$PY3" ]; then
  for p in /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$p" ]; then PY3="$p"; break; fi
  done
fi
PY3="${PY3:-python3}"

ACTION="${1:-}"
[ -n "$ACTION" ] || { echo "usage: aib-bind.sh request|list|approve|reject ..." >&2; exit 2; }
shift

ARGS=("$ACTION")
HAS_UUID=0
for a in "$@"; do
  ARGS+=("$a")
  [ "$a" = "--session-uuid" ] && HAS_UUID=1
done

if [ "$ACTION" = "request" ] && [ "$HAS_UUID" = "0" ]; then
  # 就地取本 session 的 uuid;取不到就 fail-closed,不編一個出來
  UUID="${EZ2_SESSION_UUID:-${ACB_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}}"
  if [ -z "$UUID" ]; then
    echo "拿不到本 session 的 uuid(EZ2_SESSION_UUID / ACB_SESSION_ID / CLAUDE_CODE_SESSION_ID 都沒有)。" >&2
    echo "請用 --session-uuid 明給;不代填、不亂猜身份。" >&2
    exit 2
  fi
  ARGS+=(--session-uuid "$UUID")
fi

exec "$PY3" "$HERE/bind_queue.py" "${ARGS[@]}"
