#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# grokbot — Cursor「GrokBot」對外 aib dm 的具名來源。
#
# 抬頭形狀:
#   [GrokBot > @五碼(暱稱) / 台北 …]                         # 未帶 Agent 名
#   [GrokBot (<Agent顯示名>) > @五碼(暱稱) / 台北 …]         # 有帶 Agent 名
#
# Agent 顯示名是變數,不是寫死:
#   export AIB_GROKBOT_AGENT_NAME="<Agent顯示名>"   # 通常= Grok Bot profile.name
#   或先:  src/cockpit/grokbot.sh --sync-agent-name="<Agent顯示名>"
#         （寫入 ~/.ez2-aib/identity/grokbot-agent-name；之後未設 env 時讀此檔）
# 空／省略 ⇒ 只顯示 GrokBot。權限格仍是 unattested:grokbot。
#
# ★ 不可改成 exec:exec 會換掉本行程,祖先鏈就再也看不到 grokbot。
#
# 用法(與 aib dm 相同):
#   AIB_GROKBOT_AGENT_NAME="<Agent顯示名>" \
#     EZ2_AIHUB_SOCKET=aihub-… src/cockpit/grokbot.sh 74816 "訊息"
set -euo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AIB_DM_AUTO_RESEND="${AIB_DM_AUTO_RESEND:-1}"

# grokbot-agent-name-0922: --sync-agent-name writes identity file then exits
# One clear path: sync name for later DM sends (Mac cannot read box sidebar rename).
# Sanitize mirrors dm-ctl/display_label: max 40; forbid >/[]() and fullwidth （）.
# Usage: grokbot.sh --sync-agent-name='Display Name'   OR   grokbot.sh --sync-agent-name Display Name...
_grokbot_sync_agent_name() {
  local raw="$1"
  local cleaned
  cleaned="$(printf '%s' "$raw" | python3 -c '
import sys
s = sys.stdin.read()
s = " ".join(s.split())  # trim + collapse ws
forbid = set(">/[]()（）")
if any(ch in forbid for ch in s):
    sys.stderr.write("grokbot: --sync-agent-name rejected (forbidden char >/[]()（）)\n")
    sys.exit(2)
if len(s) > 40:
    s = s[:40].rstrip()
if not s:
    sys.stderr.write("grokbot: --sync-agent-name empty after sanitize\n")
    sys.exit(2)
sys.stdout.write(s)
')" || return $?
  local dir="${HOME}/.ez2-aib/identity"
  mkdir -p "$dir"
  local dest="${dir}/grokbot-agent-name"
  printf '%s\n' "$cleaned" > "$dest"
  echo "grokbot: synced agent name → $dest ($cleaned)" >&2
  return 0
}

# Early argv: sync then exit 0 (do not call dm-ctl).
if [[ "${1:-}" == --sync-agent-name ]]; then
  shift
  [[ -n "${1:-}" ]] || { echo "grokbot: --sync-agent-name needs a name" >&2; exit 2; }
  _grokbot_sync_agent_name "$*" || exit $?
  exit 0
fi
if [[ "${1:-}" == --sync-agent-name=* ]]; then
  _grokbot_sync_agent_name "${1#--sync-agent-name=}" || exit $?
  shift
  exit 0
fi

# grokbot-agent-name-0922: env-or-identity-file → AIB_GROKBOT_AGENT_NAME
# Precedence: non-empty env wins; else single-line UTF-8 file; else leave unset
# (dm-ctl/display_label → bare "GrokBot"). Never hardcode a past sidebar name.
if [[ -z "${AIB_GROKBOT_AGENT_NAME:-}" ]]; then
  _gb_name_file="${HOME}/.ez2-aib/identity/grokbot-agent-name"
  if [[ -f "$_gb_name_file" ]]; then
    _gb_from_file="$(python3 -c '
import sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    s = p.read_text(encoding="utf-8").splitlines()[0] if p.stat().st_size else ""
except Exception:
    s = ""
s = " ".join(s.split())
forbid = set(">/[]()（）")
if s and not (forbid & set(s)):
    print(s[:40].rstrip())
' "$_gb_name_file" 2>/dev/null || true)"
    if [[ -n "${_gb_from_file:-}" ]]; then
      export AIB_GROKBOT_AGENT_NAME="$_gb_from_file"
    fi
    unset _gb_from_file
  fi
  unset _gb_name_file
fi

# G3(2026-09-25):先交給活的 grokbot 席送 ⇒ 抬頭是 @席碼(對齊 CodexBot B1)。
# 只處理「目標 + 正文」或「目標 --file F」兩種形狀;其他旗標(--dry-run 等)照舊直送。
# 沒有活席 ⇒ 照舊直送(抬頭 unattested:grokbot);AIB_GROKBOT_NO_DIRECT=1 則拒送。
# 多個活席 ⇒ 不猜、拒送,要 AIB_GROKBOT_SEAT=<五碼> 指定。
_gb_routable=1
if [[ $# -lt 2 || "${1:-}" == -* ]]; then
  _gb_routable=0
elif [[ "${2:-}" == --file ]]; then
  [[ $# -eq 3 ]] || _gb_routable=0
else
  for _gb_a in "${@:2}"; do
    [[ "$_gb_a" == --* ]] && { _gb_routable=0; break; }
  done
fi
if [[ "$_gb_routable" == 1 ]]; then
  _gb_rc=0
  python3 "$HERE/sand_bridge.py" --route-dm "$@" || _gb_rc=$?
  case "$_gb_rc" in
    0|1|2) exit "$_gb_rc" ;;
    3)
      if [[ "${AIB_GROKBOT_NO_DIRECT:-}" == 1 ]]; then
        echo "grokbot: 沒有可用的 grokbot 席,且 AIB_GROKBOT_NO_DIRECT=1 ⇒ 不直送" >&2
        exit 2
      fi
      echo "grokbot: 改直送,抬頭會是 unattested:grokbot" >&2
      ;;
    *) exit "$_gb_rc" ;;
  esac
fi

AIB_DM_REQUIRE_CABIN=1 bash "$HERE/dm-ctl.sh" "$@"   # the maintainer 09-28: 直送也一律 <艙位碼>/<席碼>
