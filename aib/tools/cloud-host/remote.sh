#!/usr/bin/env bash
# EU02 開機加固 第 1–4 步 — 主機端(由 eu02-bootstrap.sh 經 ssh 'bash -s' 送上去跑)
# 用法: bash -s -- <dry-run|apply|verify|rollback|confirm> <1|2|3|4> [時區]
# 預設什麼都不改;只有 mode=apply / rollback / confirm 會寫。
# 測試用:R=<沙盒根> 讓所有路徑落到沙盒;指令用 PATH 上的替身。
set -u
MODE="${1:-dry-run}"; STEP="${2:-}"; TZ_WANT="${3:-}"
R="${R:-}"                                   # 測試沙盒根，正式機為空
TS="$(date +%Y%m%d-%H%M%S)"
BK="$R/root/ez2-bootstrap-backup"
DEADMAN_SEC="${DEADMAN_SEC:-180}"
SSHD_DROPIN="$R/etc/ssh/sshd_config.d/10-ez2-harden.conf"
F2B_JAIL="$R/etc/fail2ban/jail.d/10-ez2-sshd.local"
UU_DROPIN="$R/etc/apt/apt.conf.d/52ez2-no-auto-reboot"
TZ_ORIG_FILE="$BK/timezone.orig"

say() { printf '[eu02 step%s %s] %s\n' "$STEP" "$MODE" "$*"; }
die() { say "FAIL: $*"; exit 1; }
plan() { say "會做: $*"; }
w() {                                         # 所有寫入都經這裡;dry-run 只印
  if [ "$MODE" = dry-run ] || [ "$MODE" = verify ]; then plan "$*"; return 0; fi
  say "執行: $*"; "$@"
}
backup() {                                    # 改檔前備份(檔不存在就記 absent)
  local f="$1" d="$BK/$TS"
  w mkdir -p "$d"
  if [ -e "$f" ]; then w cp -a "$f" "$d/$(echo "${f#$R}" | tr / _)"; else w touch "$d/$(echo "${f#$R}" | tr / _).absent"; fi
}
put() {                                       # put <檔> <權限> ; 內容從 stdin
  local f="$1" m="$2" tmp
  if [ "$MODE" != apply ]; then plan "寫 $f ($m):"; sed 's/^/    | /'; return 0; fi
  tmp="$(mktemp "${f}.XXXX")" || die "mktemp $f"
  cat >"$tmp" && chmod "$m" "$tmp" && mv -f "$tmp" "$f" || { rm -f "$tmp"; die "寫 $f"; }
  say "已寫 $f"
}
deadman() {                                   # deadman <單位名> <回滾指令…>:N 秒內沒 confirm 就自動回滾
  local unit="$1"; shift
  [ "$MODE" = apply ] && systemctl stop "$unit.timer" "$unit.service" 2>/dev/null
  w systemd-run --unit="$unit" --on-active="${DEADMAN_SEC}s" --timer-property=AccuracySec=1s /bin/sh -c "$*" || die "排不了自動回滾，不往下做"
}
deadman_cancel() { w systemctl stop "$1.timer"; }

# ---------- 第 0 步 hostname(PVE 要 hostname 能解析到公網 IP)----------
s0_check() {
  [ "$(hostname)" = eu02 ] && say "OK  hostname eu02" || { say "差  hostname=$(hostname)"; return 1; }
  grep -qE '^[0-9a-f.:]+\s.*\beu02(\s|$)' "$R/etc/hosts" && say "OK  /etc/hosts 有 eu02" || { say "差  /etc/hosts 沒 eu02"; return 1; }
}
s0_apply() {
  local ip; ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
  [ -n "$ip" ] || ip="${EU02_IP:-}"
  [ -n "$ip" ] || die "抓不到公網 IPv4"
  backup "$R/etc/hostname"; backup "$R/etc/hosts"
  w hostnamectl set-hostname eu02
  if grep -qE '\seu02(\s|$)' "$R/etc/hosts" 2>/dev/null; then say "/etc/hosts 已有 eu02"; else
    if [ "$MODE" = apply ]; then printf '%s eu02.ez2.internal eu02\n' "$ip" >>"$R/etc/hosts"; say "已加 $ip eu02 到 /etc/hosts"; else plan "加「$ip eu02.ez2.internal eu02」到 /etc/hosts"; fi
  fi
}
s0_rollback() { local d; d="$(ls -d "$BK"/*/ 2>/dev/null | tail -1)"; [ -n "$d" ] && w cp -a "$d/_etc_hosts" "$R/etc/hosts"; w hostnamectl set-hostname "$(cat "$d/_etc_hostname" 2>/dev/null || echo localhost)"; }

# ---------- 第 1 步 sshd 加固 ----------
s1_check() {
  local t; t="$(sshd -T 2>/dev/null)" || { say "sshd -T 失敗"; return 1; }
  local ok=0
  for kv in "passwordauthentication no" "kbdinteractiveauthentication no" "permitrootlogin prohibit-password" \
            "x11forwarding no" "maxauthtries 3" "logingracetime 30"; do
    # Debian 13 的 sshd -T 會把 prohibit-password 印成舊拼法 without-password,兩種都算對
    if printf '%s\n' "$t" | grep -qx "$kv" || { [ "$kv" = "permitrootlogin prohibit-password" ] && printf '%s\n' "$t" | grep -qx "permitrootlogin without-password"; }; then say "OK  $kv"; else say "差  $kv(現在: $(printf '%s\n' "$t" | grep "^${kv%% *} " | head -1))"; ok=1; fi
  done
  return $ok
}
s1_apply() {
  [ -d "$R/etc/ssh/sshd_config.d" ] || die "沒有 sshd_config.d"
  grep -qE '^\s*Include\s+/etc/ssh/sshd_config.d/\*\.conf' "$R/etc/ssh/sshd_config" || die "主檔沒 Include sshd_config.d,drop-in 不會生效"
  backup "$SSHD_DROPIN"
  put "$SSHD_DROPIN" 644 <<'CONF'
# ez2 EU02 第 1 步(4542e / c477a 計畫)。回滾：刪本檔 + systemctl reload ssh
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
CONF
  if [ "$MODE" = apply ]; then
    sshd -t || { rm -f "$SSHD_DROPIN"; die "sshd -t 不過，已刪 drop-in、未 reload"; }
    # c477a 審:OpenSSH 取第一次出現的值;若 00-*.conf 或主檔在 Include 前已寫 PasswordAuthentication yes,
    # 我們的 10- 會被蓋掉而 sshd -t 照樣過。所以 reload 前先用 sshd -T 讀磁碟上的新設定對六項。
    s1_check || { rm -f "$SSHD_DROPIN"; die "sshd -T 讀新設定六項有不符(被更早的設定蓋掉),已刪 drop-in、未 reload"; }
  else plan "sshd -t(不過就刪檔、不 reload)"; plan "sshd -T 對六項(有一項不符就刪檔、不 reload)"; say "sshd 現在: $(systemctl is-active ssh.service 2>/dev/null || echo 未知)"; fi
  deadman ez2-deadman-s1 "rm -f $SSHD_DROPIN && systemctl reload ssh.service"
  w systemctl reload ssh.service || die "reload sshd 失敗"   # socket 啟動：只 reload service,不碰 ssh.socket
  say "等 Mac 端用新連線驗;${DEADMAN_SEC} 秒內沒 confirm 會自動回滾"
}
s1_rollback() { w rm -f "$SSHD_DROPIN"; w systemctl reload ssh.service; }

# ---------- 第 2 步 ufw ----------
s2_check() {
  local s; s="$(ufw status verbose 2>/dev/null)"
  printf '%s\n' "$s" | grep -q '^Status: active' && say "OK  ufw active" || { say "差  ufw 未啟用"; return 1; }
  printf '%s\n' "$s" | grep -q 'deny (incoming)' || { say "差  預設不是 deny incoming"; return 1; }
  printf '%s\n' "$s" | grep -qE '^22/tcp +ALLOW IN' || { say "差  沒有 22/tcp 規則"; return 1; }
  say "OK  deny incoming + 22/tcp"
}
s2_apply() {
  [ "${ALLOW_UFW:-0}" = 1 ] || die "4.4.1 §7.3:PVE 宿主不疊 ufw,防火牆走 pve-firewall(net apply);Ubuntu 過渡期要用請設 ALLOW_UFW=1"
  backup "$R/etc/ufw/user.rules"; backup "$R/etc/ufw/user6.rules"; backup "$R/etc/default/ufw"
  grep -q '^IPV6=yes' "$R/etc/default/ufw" 2>/dev/null || die "/etc/default/ufw 不是 IPV6=yes(主機有 IPv6,不能只擋 v4)"
  w ufw default deny incoming
  w ufw default allow outgoing
  w ufw allow 22/tcp
  if ip link show tailscale0 >/dev/null 2>&1; then w ufw allow in on tailscale0; else say "tailscale0 還沒有，第 3 步後重跑本步會補"; fi
  if [ "$MODE" = apply ]; then
    ufw show added 2>/dev/null | grep -q 'allow 22/tcp' || die "22/tcp 規則沒進去，不啟用"
  else plan "確認 22/tcp 規則已在才啟用"; fi
  deadman ez2-deadman-s2 "ufw --force disable"
  w ufw --force enable || die "ufw enable 失敗"
}
s2_rollback() { w ufw --force disable; }

# ---------- 第 3 步 Tailscale(只裝，不登入)----------
s3_check() {
  command -v tailscale >/dev/null 2>&1 || { say "差  沒裝 tailscale"; return 1; }
  say "OK  tailscale $(tailscale version 2>/dev/null | head -1)"
  if tailscale status >/dev/null 2>&1; then say "OK  已登入: $(tailscale ip -4 2>/dev/null)"
  else say "待  未登入(Andy 自己在主機上跑 tailscale up,金鑰不經對話)"; fi
}
s3_apply() {
  command -v tailscale >/dev/null 2>&1 && { say "已裝，略過安裝"; } || {
    if [ "$MODE" = apply ]; then
      curl -fsSL https://tailscale.com/install.sh -o "$R/root/tailscale-install.sh" || die "下載 install.sh"
      sh "$R/root/tailscale-install.sh" || die "install.sh 失敗"
    else plan "下載並執行 tailscale 官方 install.sh(加官方 apt 庫)"; fi
  }
  w systemctl enable --now tailscaled
  say "下一步請 Andy 在 EU02 上親自跑: tailscale up --hostname=eu02 --ssh=false --shields-up=false"
  say "(互動登入網址;本腳本不收、不傳 auth key)。起來後重跑第 2 步補 tailscale0 規則"
}
s3_rollback() { w tailscale logout; w apt-get purge -y tailscale; w rm -f "$R/etc/apt/sources.list.d/tailscale.list"; }

# ---------- 第 4 步 基本防護 ----------
s4_check() {
  local ok=0
  systemctl is-active --quiet fail2ban && fail2ban-client status sshd >/dev/null 2>&1 && say "OK  fail2ban sshd" || { say "差  fail2ban sshd jail"; ok=1; }
  grep -qs 'Automatic-Reboot "false"' "$UU_DROPIN" && say "OK  不自動重開" || { say "差  沒明寫 Automatic-Reboot false"; ok=1; }
  say "時區: $(timedatectl show -p Timezone --value 2>/dev/null)${TZ_WANT:+(要 $TZ_WANT)}"
  if [ -n "$TZ_WANT" ] && [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "$TZ_WANT" ]; then ok=1; fi
  return $ok
}
s4_apply() {
  backup "$F2B_JAIL"; backup "$UU_DROPIN"
  if ! dpkg-query -W -f='${Status}' fail2ban 2>/dev/null | grep -q 'ok installed' || ! dpkg-query -W -f='${Status}' unattended-upgrades 2>/dev/null | grep -q 'ok installed'; then
    w env DEBIAN_FRONTEND=noninteractive apt-get update -qq || die "apt-get update 失敗"
  fi
  if ! dpkg-query -W -f='${Status}' fail2ban 2>/dev/null | grep -q 'ok installed'; then
    w env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban || die "裝 fail2ban 失敗"
  fi
  if ! dpkg-query -W -f='${Status}' unattended-upgrades 2>/dev/null | grep -q 'ok installed'; then
    w env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades || die "裝 unattended-upgrades 失敗"
  fi
  w mkdir -p "$R/etc/fail2ban/jail.d"
  put "$F2B_JAIL" 644 <<'CONF'
# ez2 EU02 第 4 步：只開 sshd(同 EU01)。回滾：刪本檔 + systemctl restart fail2ban
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
ignoreip = 127.0.0.1/8 ::1 100.64.0.0/10
CONF
  w systemctl enable fail2ban
  w systemctl restart fail2ban
  put "$UU_DROPIN" 644 <<'CONF'
// ez2:Andy 08-19 裁定不自動重開(同 EU01)
Unattended-Upgrade::Automatic-Reboot "false";
CONF
  if [ -n "$TZ_WANT" ]; then
    timedatectl list-timezones 2>/dev/null | grep -qx "$TZ_WANT" || die "沒有這個時區: $TZ_WANT"
    [ -e "$TZ_ORIG_FILE" ] || { w mkdir -p "$BK"; if [ "$MODE" = apply ]; then timedatectl show -p Timezone --value >"$TZ_ORIG_FILE"; else plan "記原時區到 $TZ_ORIG_FILE"; fi; }
    w timedatectl set-timezone "$TZ_WANT"
  else say "沒給時區，不改(要 Andy 選)"; fi
}
s4_rollback() {
  w rm -f "$F2B_JAIL" "$UU_DROPIN"
  w systemctl disable --now fail2ban
  if [ -s "$TZ_ORIG_FILE" ]; then w timedatectl set-timezone "$(cat "$TZ_ORIG_FILE")"; fi
}

# ---------- 主流程 ----------
case "$STEP" in 0|1|2|3|4) ;; *) die "步驟要 0–4";; esac
[ "$STEP" = 0 ] || [ "$(hostname)" = eu02 ] || [ -n "$R" ] || die "hostname 不是 eu02($(hostname)),先跑第 0 步"
[ "$(id -u)" = 0 ] || [ -n "$R" ] || die "要 root"
case "$MODE" in
  dry-run) say "=== 現況 ==="; "s${STEP}_check"; say "=== 計畫(不會改)==="; "s${STEP}_apply"; exit 0 ;;
  verify)  "s${STEP}_check"; exit $? ;;
  apply)   "s${STEP}_apply" ;;
  confirm) case "$STEP" in 1|2) deadman_cancel "ez2-deadman-s$STEP"; say "已取消自動回滾";; *) say "本步沒有自動回滾";; esac ;;
  rollback) "s${STEP}_rollback" ;;
  *) die "mode 要 dry-run|verify|apply|confirm|rollback" ;;
esac
