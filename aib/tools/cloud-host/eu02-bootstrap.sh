#!/usr/bin/env bash
# EU02 開機加固 第 1–4 步 — Mac 端。預設 dry-run(只讀 + 印計畫)。
#
#   eu02-bootstrap.sh [--step 1|2|3|4|all]                       # dry-run
#   eu02-bootstrap.sh --step 1 --apply --approved "<Andy 原話>"   # 寫入(要 Andy 親口准)
#   eu02-bootstrap.sh --step 4 --tz Asia/Taipei ...               # 時區另准
#   eu02-bootstrap.sh --step 2 --rollback --approved "..."
#   eu02-bootstrap.sh --verify [--step N]                         # 只驗
#
# 第 1 / 2 步 apply 會先在主機排「N 秒自動回滾」,Mac 用全新 ssh 連線驗得進去才取消;
# 驗不過就什麼都不做，等主機自己回滾(預設 180 秒)。
# 環境變數(必填):EU02_HOST = ~/.ssh/config 裡那台的 Host 別名(裝好 Tailscale 後改成 *-ts 那個)。
# 選填:EU02_HOSTNAME(預設 eu02)、EU02_FQDN(寫進 /etc/hosts 的完整名,沒給就只寫短名)、EU02_LOG、SSH_BIN(測試用)。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="${EU02_HOST:?set EU02_HOST to the host alias in ~/.ssh/config (no default)}"
HNAME="${EU02_HOSTNAME:-eu02}"
FQDN="${EU02_FQDN:-}"
SSH_BIN="${SSH_BIN:-ssh}"
SSH_OPTS=(-o ForwardAgent=no -o ClearAllForwardings=yes -o BatchMode=yes -o ConnectTimeout=15 -o ControlPath=none)
LOG="${EU02_LOG:-$HOME/.ez2-aib/cloud-host/${HNAME}-bootstrap.log}"
STEP=all; MODE=dry-run; TZ_WANT=""; APPROVED=""

while [ $# -gt 0 ]; do
  case "$1" in
    --step) STEP="$2"; shift 2;;
    --apply) MODE=apply; shift;;
    --rollback) MODE=rollback; shift;;
    --verify) MODE=verify; shift;;
    --tz) TZ_WANT="$2"; shift 2;;
    --approved) APPROVED="$2"; shift 2;;
    -h|--help) sed -n '2,13p' "$0"; exit 0;;
    *) echo "不認得: $1" >&2; exit 2;;
  esac
done

if [ "$MODE" = apply ] || [ "$MODE" = rollback ]; then
  [ -n "$APPROVED" ] || { echo "寫入要 --approved \"<Andy 原話>\"(正式機寫入，只有 Andy 能准)" >&2; exit 3; }
  [ "$STEP" != all ] || { echo "寫入一次只做一步(--step N),做完驗過再下一步" >&2; exit 2; }
fi
case "$STEP" in 0|1|2|3|4) STEPS="$STEP";; all) STEPS="0 1 3 4";; *) echo "--step 要 0–4 或 all(all 不含 2:PVE 不疊 ufw)" >&2; exit 2;; esac
EXPECT_HOST="${EU02_EXPECT_HOST:-$HNAME}"; [ "$STEP" = 0 ] && EXPECT_HOST="${EU02_EXPECT_HOST:-.*}"
[ "$STEP" = 4 ] || [ "$STEP" = all ] || [ -z "$TZ_WANT" ] || { echo "--tz 只用在第 4 步" >&2; exit 2; }

log() { mkdir -p "$(dirname "$LOG")"; printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }
remote() {  # remote <mode> <step>
  "$SSH_BIN" "${SSH_OPTS[@]}" "$HOST" 'bash -s' -- "$1" "$2" "$TZ_WANT" "$HNAME" "$FQDN" <"$HERE/remote.sh"
}
fresh_login_ok() {  # 全新連線(ControlPath=none)能進 + hostname 對
  local h; h="$("$SSH_BIN" "${SSH_OPTS[@]}" "$HOST" hostname 2>/dev/null)" && [[ "$h" =~ ^${EXPECT_HOST}$ ]]
}

[ "$MODE" = dry-run ] || log "mode=$MODE step=$STEP tz=${TZ_WANT:-} approved=\"$APPROVED\""
fresh_login_ok || { echo "連不上 $HOST 或 hostname 不符($EXPECT_HOST),停" >&2; exit 4; }

rc=0
for s in $STEPS; do
  echo "──── 第 $s 步 ($MODE) ────"
  case "$MODE" in
    dry-run|verify) remote "$MODE" "$s" || rc=1 ;;
    rollback) remote rollback "$s" || rc=1; remote verify "$s" || true ;;
    apply)
      if ! remote apply "$s"; then echo "第 $s 步 apply 失敗(主機端已停)" >&2; log "step$s apply FAIL"; exit 1; fi
      if [ "$s" = 1 ] || [ "$s" = 2 ]; then
        if fresh_login_ok && remote verify "$s"; then
          remote confirm "$s" || { echo "取消自動回滾失敗，請手動 systemctl stop ez2-deadman-s$s.timer" >&2; exit 1; }
          log "step$s apply OK + confirmed"
        else
          echo "新連線驗不過：不取消，主機會在 ${DEADMAN_SEC:-180} 秒內自己回滾" >&2; log "step$s verify FAIL, deadman 回滾中"; exit 1
        fi
      else
        remote verify "$s" || { [ "$s" = 3 ] && echo "(第 3 步未登入是正常的，等 Andy 跑 tailscale up)"; }
        log "step$s apply done"
      fi ;;
  esac
done
exit $rc
