#!/usr/bin/env python3
"""EU02 宿主加固腳本(tools/cloud-host/eu02-bootstrap.sh + remote.sh)的沙盒測試 + 刀(#528 M2 進倉)。完全不連主機：假 ssh 把 remote.sh 在本機沙盒跑,
所有會寫系統的指令(systemctl / ufw / apt-get / sshd / timedatectl …)都是記帳替身。

跑法:aib test aib/tests/test-cloud-bootstrap-4542e-1108.py
     加 --knives 另跑刀(每把刀必須讓指定測試變紅)。
"""
import hashlib, os, shutil, stat, subprocess, sys, tempfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "cloud-host")  # 腳本在 tools/cloud-host

STUB = r'''#!/bin/bash
n="$(basename "$0")"; echo "$n $*" >>"$CALLS"
case "$n" in
  hostname) [ -e "$SB/.login_broken" ] && echo other || echo eu02 ;;
  sshd)
    if [ "$1" = -t ]; then exit "${SSHD_T_RC:-0}"; fi
    if [ "$1" = -T ]; then
      if [ -e "$R/etc/ssh/sshd_config.d/10-ez2-harden.conf" ] && [ -z "${SSHD_T_FORCE_OLD:-}" ]; then
        printf 'passwordauthentication no\nkbdinteractiveauthentication no\npermitrootlogin without-password\nx11forwarding no\nmaxauthtries 3\nlogingracetime 30\n'
      else
        printf 'passwordauthentication yes\nkbdinteractiveauthentication no\npermitrootlogin prohibit-password\nx11forwarding yes\nmaxauthtries 6\nlogingracetime 120\n'
      fi
    fi ;;
  ufw)
    case "$1 $2" in
      "status verbose") if [ -e "$SB/.ufw_on" ]; then printf 'Status: active\nDefault: deny (incoming), allow (outgoing)\n22/tcp                     ALLOW IN    Anywhere\n'; else echo 'Status: inactive'; fi ;;
      "show added") [ -e "$SB/.ufw_22" ] && echo 'ufw allow 22/tcp' ;;
      "allow 22/tcp") touch "$SB/.ufw_22" ;;
      "--force enable") touch "$SB/.ufw_on" ;;
      "--force disable") rm -f "$SB/.ufw_on" ;;
    esac ;;
  ip) if [ "$1" = -4 ]; then echo "2: eth0    inet 203.0.113.10/26 brd"; else exit 1; fi ;;
  systemctl) case "$1" in is-active) exit 1;; esac ;;
  timedatectl)
    case "$1" in
      show) cat "$SB/.tz" ;;
      list-timezones) printf 'Asia/Taipei\nEurope/Berlin\nUTC\n' ;;
      set-timezone) echo "$2" >"$SB/.tz" ;;
    esac ;;
  dpkg-query) exit 1 ;;
  curl) out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
        printf '#!/bin/sh\necho "install.sh $*" >>"$CALLS"\n' >"$out" ;;
  fail2ban-client) exit 1 ;;
esac
exit 0
'''
STUB_NAMES = ["hostname", "hostnamectl", "sshd", "ufw", "ip", "systemctl", "systemd-run", "timedatectl",
              "dpkg-query", "apt-get", "curl", "fail2ban-client", "tailscale", "tailscaled"]

FAKE_SSH = r'''#!/bin/bash
echo "ssh $*" >>"$SB/ssh.log"
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  case "${args[$i]}" in -o) i=$((i+2));; -*) i=$((i+1));; *) break;; esac
done
host="${args[$i]}"; rest=("${args[@]:$((i+1))}")
if [ "${rest[0]:-}" = "bash -s" ]; then
  R="$SB/root" PATH="$SB/stubs:/usr/bin:/bin" bash -s "${rest[@]:1}"
else
  R="$SB/root" PATH="$SB/stubs:/usr/bin:/bin" bash -c "${rest[*]}"
fi
'''


class Sandbox:
    def __init__(self, script_dir):
        self.dir = tempfile.mkdtemp(prefix="eu02bs-")
        self.script_dir = script_dir
        root = os.path.join(self.dir, "root")
        for d in ["etc/ssh/sshd_config.d", "etc/ufw", "etc/default", "etc/apt/apt.conf.d", "root"]:
            os.makedirs(os.path.join(root, d))
        self.w("root/etc/ssh/sshd_config", "Include /etc/ssh/sshd_config.d/*.conf\nPermitRootLogin without-password\n")
        self.w("root/etc/default/ufw", "IPV6=yes\n")
        self.w("root/etc/hosts", "127.0.0.1 localhost\n")
        self.w("root/etc/hostname", "eu02\n")
        self.w("root/etc/ufw/user.rules", "*filter\n")
        self.w(".tz", "Europe/Berlin\n")
        stubs = os.path.join(self.dir, "stubs"); os.makedirs(stubs)
        self.w("stubs/_stub", STUB, exe=True)
        for n in STUB_NAMES:
            os.symlink(os.path.join(stubs, "_stub"), os.path.join(stubs, n))
        self.w("fakessh", FAKE_SSH, exe=True)
        self.calls = os.path.join(self.dir, "calls.log"); open(self.calls, "w").close()
        open(os.path.join(self.dir, "ssh.log"), "w").close()

    def w(self, rel, text, exe=False):
        p = os.path.join(self.dir, rel)
        with open(p, "w") as f: f.write(text)
        if exe: os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def run(self, *args, env_extra=None):
        env = dict(os.environ, SB=self.dir, R=os.path.join(self.dir, "root"), CALLS=self.calls, EU02_HOST="host-under-test",
                   SSH_BIN=os.path.join(self.dir, "fakessh"), EU02_LOG=os.path.join(self.dir, "run.log"),
                   DEADMAN_SEC="5")
        env.update(env_extra or {})
        p = subprocess.run(["bash", os.path.join(self.script_dir, "eu02-bootstrap.sh"), *args],
                           env=env, capture_output=True, text=True, errors="replace", timeout=60)
        return p.returncode, p.stdout + p.stderr

    def call_lines(self):
        return open(self.calls).read().splitlines()

    def tree_hash(self):
        h = hashlib.sha256()
        root = os.path.join(self.dir, "root")
        for dp, dn, fn in sorted(os.walk(root)):
            dn.sort()
            for f in sorted(fn):
                p = os.path.join(dp, f); h.update(p.encode()); h.update(open(p, "rb").read())
        return h.hexdigest()

    def exists(self, rel): return os.path.exists(os.path.join(self.dir, "root", rel))
    def close(self): shutil.rmtree(self.dir, ignore_errors=True)


WRITE_PREFIXES = ["systemd-run", "apt-get", "curl", "ufw default", "ufw allow", "ufw --force",
                  "systemctl reload", "systemctl restart", "systemctl stop", "systemctl enable",
                  "systemctl disable", "timedatectl set-timezone", "tailscale logout", "tailscale up"]


def first(lines, prefix):
    for i, l in enumerate(lines):
        if l.startswith(prefix): return i
    return -1


# ---------------- 測試 ----------------
def t_dryrun_writes_nothing(sd):
    sb = Sandbox(sd)
    try:
        before = sb.tree_hash()
        rc, out = sb.run("--step", "all", "--tz", "Asia/Taipei")
        writes = [l for l in sb.call_lines() if any(l.startswith(p) for p in WRITE_PREFIXES)]
        assert not writes, f"dry-run 呼叫了寫入指令: {writes}"
        assert sb.tree_hash() == before, "dry-run 改了沙盒檔案"
        assert "會做:" in out, out
        assert open(os.path.join(sb.dir, ".tz")).read().strip() == "Europe/Berlin"
    finally: sb.close()


def t_ssh_hardening_opts(sd):
    sb = Sandbox(sd)
    try:
        sb.run("--step", "1", "--apply", "--approved", "測試")
        lines = open(os.path.join(sb.dir, "ssh.log")).read().splitlines()
        assert len(lines) >= 4, lines
        for l in lines:
            assert "ForwardAgent=no" in l and "ClearAllForwardings=yes" in l, f"ssh 沒帶加固選項: {l}"
            assert "ControlPath=none" in l, f"沒強制新連線: {l}"
    finally: sb.close()


def t_apply_needs_approval(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "1", "--apply")
        assert rc == 3, (rc, out)
        assert not open(os.path.join(sb.dir, "ssh.log")).read(), "沒准也連了主機"
        rc, out = sb.run("--step", "all", "--apply", "--approved", "x")
        assert rc == 2, ("一次全做沒被擋", rc, out)
    finally: sb.close()


def t_step1_order_and_confirm(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "1", "--apply", "--approved", "測試")
        assert rc == 0, out
        L = sb.call_lines()
        a, b, c = first(L, "sshd -t"), first(L, "systemd-run --unit=ez2-deadman-s1"), first(L, "systemctl reload ssh.service")
        assert -1 < a < b < c, f"順序要 sshd -t < 自動回滾 < reload: {a},{b},{c}\n{L}"
        assert L.index("systemctl stop ez2-deadman-s1.timer") > c if "systemctl stop ez2-deadman-s1.timer" in L else False, "驗過沒取消自動回滾"
        assert not any("ssh.socket" in l for l in L), "動到 ssh.socket"
        assert sb.exists("etc/ssh/sshd_config.d/10-ez2-harden.conf")
        assert "PasswordAuthentication no" in open(os.path.join(sb.dir, "root/etc/ssh/sshd_config.d/10-ez2-harden.conf")).read()
    finally: sb.close()


def t_step1_sshd_t_fail(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "1", "--apply", "--approved", "測試", env_extra={"SSHD_T_RC": "1"})
        assert rc != 0, out
        assert not sb.exists("etc/ssh/sshd_config.d/10-ez2-harden.conf"), "sshd -t 不過還留著 drop-in"
        assert first(sb.call_lines(), "systemctl reload ssh") == -1, "sshd -t 不過還 reload"
    finally: sb.close()


def t_step1_overridden_by_earlier_conf(sd):
    """c477a 審:更早的設定檔蓋掉 10-(sshd -t 過但 sshd -T 仍 yes)⇒ 刪檔、不 reload"""
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "1", "--apply", "--approved", "測試", env_extra={"SSHD_T_FORCE_OLD": "1"})
        assert rc != 0, out
        assert not sb.exists("etc/ssh/sshd_config.d/10-ez2-harden.conf"), "被蓋掉還留著 drop-in"
        L = sb.call_lines()
        assert first(L, "systemctl reload ssh.service") == -1, "被蓋掉還 reload"
        assert first(L, "systemd-run") == -1, "被蓋掉還排了回滾"
    finally: sb.close()


def t_step1_fresh_login_fail_no_confirm(sd):
    sb = Sandbox(sd)
    try:
        # 改完後新連線 hostname 不對(模擬進不去):不准取消自動回滾
        stub = os.path.join(sb.dir, "stubs/_stub")
        s = open(stub).read().replace('sshd)\n', 'sshd)\n    [ "$1" = -t ] && touch "$SB/.login_broken"\n', 1)
        open(stub, "w").write(s)
        rc, out = sb.run("--step", "1", "--apply", "--approved", "測試")
        assert rc != 0, out
        assert "systemctl stop ez2-deadman-s1.timer" not in sb.call_lines(), "進不去還取消自動回滾"
    finally: sb.close()


def t_step0_fqdn_and_no_default_host(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "0", "--apply", "--approved", "測試", env_extra={"EU02_FQDN": "eu02.example.test"})
        assert rc == 0, out
        assert "203.0.113.10 eu02.example.test eu02\n" in open(os.path.join(sb.dir, "root/etc/hosts")).read()
    finally: sb.close()
    sb = Sandbox(sd)
    try:
        env = dict(os.environ, SB=sb.dir, R=os.path.join(sb.dir, "root"), CALLS=sb.calls, SSH_BIN=os.path.join(sb.dir, "fakessh"))
        env.pop("EU02_HOST", None)
        p = subprocess.run(["bash", os.path.join(sd, "eu02-bootstrap.sh"), "--step", "0"], env=env, capture_output=True, text=True, errors="replace", timeout=60)
        assert p.returncode != 0 and "EU02_HOST" in p.stderr, ("沒設 EU02_HOST 還跑", p.returncode, p.stderr)
        assert not open(os.path.join(sb.dir, "ssh.log")).read(), "沒設主機還 ssh"
    finally: sb.close()


def t_step2_refuses_without_allow_ufw(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "2", "--apply", "--approved", "測試")
        assert rc != 0 and first(sb.call_lines(), "ufw --force enable") == -1, "PVE 宿主不該疊 ufw"
    finally: sb.close()


def t_step0_hostname_hosts(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "0", "--apply", "--approved", "測試")
        assert rc == 0, out
        assert first(sb.call_lines(), "hostnamectl set-hostname eu02") > -1
        assert "203.0.113.10 eu02\n" in open(os.path.join(sb.dir, "root/etc/hosts")).read()
        rc, out = sb.run("--step", "0", "--verify")
        assert rc == 0, ("step0 verify 沒過", out)
    finally: sb.close()


def t_step2_22_before_enable_and_ipv6(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "2", "--apply", "--approved", "測試", env_extra={"ALLOW_UFW": "1"})
        assert rc == 0, out
        L = sb.call_lines()
        a, b, c = first(L, "ufw allow 22/tcp"), first(L, "systemd-run --unit=ez2-deadman-s2"), first(L, "ufw --force enable")
        assert -1 < a < b < c, f"22 規則 / 自動回滾要在 enable 前: {a},{b},{c}"
    finally: sb.close()
    sb = Sandbox(sd)
    try:
        sb.w("root/etc/default/ufw", "IPV6=no\n")
        rc, out = sb.run("--step", "2", "--apply", "--approved", "測試", env_extra={"ALLOW_UFW": "1"})
        assert rc != 0 and first(sb.call_lines(), "ufw --force enable") == -1, "IPV6=no 還啟用"
    finally: sb.close()


def t_step3_no_login_no_key(sd):
    sb = Sandbox(sd)
    try:
        os.remove(os.path.join(sb.dir, "stubs/tailscale"))  # 主機還沒裝
        rc, out = sb.run("--step", "3", "--apply", "--approved", "測試")
        L = sb.call_lines()
        assert first(L, "install.sh") > -1, f"沒跑安裝: {L}"
        assert first(L, "tailscale up") == -1, "腳本自己登入 tailscale"
        assert not any("authkey" in l.lower() or "tskey" in l.lower() for l in L), "argv 帶了金鑰"
        assert "tailscale up --hostname=eu02" in out, "沒告訴 Andy 下一步"
    finally: sb.close()


def t_step4_tz_and_rollback(sd):
    sb = Sandbox(sd)
    try:
        rc, out = sb.run("--step", "4", "--apply", "--approved", "測試", "--tz", "Mars/Base")
        assert rc != 0 and first(sb.call_lines(), "timedatectl set-timezone") == -1, "亂給時區還改"
        rc, out = sb.run("--step", "4", "--apply", "--approved", "測試")
        assert first(sb.call_lines(), "timedatectl set-timezone") == -1, "沒給時區也改了"
        L = sb.call_lines(); assert first(L, "apt-get update") > -1 and first(L, "apt-get update") < first(L, "apt-get install"), "裝套件前沒 apt-get update"
        assert 'Automatic-Reboot "false"' in open(os.path.join(sb.dir, "root/etc/apt/apt.conf.d/52ez2-no-auto-reboot")).read()
        assert "ignoreip" in open(os.path.join(sb.dir, "root/etc/fail2ban/jail.d/10-ez2-sshd.local")).read()
        rc, out = sb.run("--step", "4", "--apply", "--approved", "測試", "--tz", "Asia/Taipei")
        assert open(os.path.join(sb.dir, ".tz")).read().strip() == "Asia/Taipei", out
        rc, out = sb.run("--step", "4", "--rollback", "--approved", "測試")
        assert open(os.path.join(sb.dir, ".tz")).read().strip() == "Europe/Berlin", "回滾沒還原時區"
        assert not sb.exists("etc/fail2ban/jail.d/10-ez2-sshd.local")
    finally: sb.close()


TESTS = [t_step0_hostname_hosts, t_step0_fqdn_and_no_default_host, t_step2_refuses_without_allow_ufw, t_dryrun_writes_nothing, t_ssh_hardening_opts, t_apply_needs_approval, t_step1_order_and_confirm,
         t_step1_sshd_t_fail, t_step1_overridden_by_earlier_conf, t_step1_fresh_login_fail_no_confirm, t_step2_22_before_enable_and_ipv6,
         t_step3_no_login_no_key, t_step4_tz_and_rollback]

# 刀:(名稱, 檔, 原字串, 換成, 應變紅的測試)
KNIVES = [
    ("dry-run 也真寫", "remote.sh", 'if [ "$MODE" = dry-run ] || [ "$MODE" = verify ]; then plan', 'if false; then plan', t_dryrun_writes_nothing),
    ("拿掉 ForwardAgent=no", "eu02-bootstrap.sh", "-o ForwardAgent=no ", "", t_ssh_hardening_opts),
    ("拿掉准許檢查", "eu02-bootstrap.sh", '[ -n "$APPROVED" ] ||', 'true ||', t_apply_needs_approval),
    ("拿掉 sshd -t", "remote.sh", 'sshd -t || {', 'true || {', t_step1_sshd_t_fail),
    ("reload 前不對 sshd -T", "remote.sh", '    s1_check || { rm -f "$SSHD_DROPIN"; die', '    true || { rm -f "$SSHD_DROPIN"; die', t_step1_overridden_by_earlier_conf),
    ("reload 前不排回滾", "remote.sh", '  deadman ez2-deadman-s1 "rm -f $SSHD_DROPIN && systemctl reload ssh.service"\n', '', t_step1_order_and_confirm),
    ("新連線不驗就取消", "eu02-bootstrap.sh", 'if fresh_login_ok && remote verify "$s"; then', 'if true; then', t_step1_fresh_login_fail_no_confirm),
    ("PVE 上照樣疊 ufw", "remote.sh", '[ "${ALLOW_UFW:-0}" = 1 ] ||', 'true ||', t_step2_refuses_without_allow_ufw),
    ("不查 IPv6", "remote.sh", 'grep -q \'^IPV6=yes\' "$R/etc/default/ufw" 2>/dev/null ||', 'true ||', t_step2_22_before_enable_and_ipv6),
    ("不驗時區", "remote.sh", 'grep -qx "$TZ_WANT" ||', 'true ||', t_step4_tz_and_rollback),
    ("回滾不還時區", "remote.sh", 'w timedatectl set-timezone "$(cat "$TZ_ORIG_FILE")"', 'true', t_step4_tz_and_rollback),
]


def run_one(t, sd):
    try: t(sd); return True, ""
    except AssertionError as e: return False, str(e)[:400]


def main():
    bad = 0
    for t in TESTS:
        ok, why = run_one(t, HERE)
        print(("PASS " if ok else "FAIL ") + t.__name__ + ("" if ok else f"\n    {why}"))
        bad += not ok
    if "--knives" in sys.argv:
        for name, f, old, new, t in KNIVES:
            d = tempfile.mkdtemp(prefix="eu02knife-")
            try:
                for fn in ("remote.sh", "eu02-bootstrap.sh"): shutil.copy(os.path.join(HERE, fn), d)
                s = open(os.path.join(d, f)).read()
                if old not in s: print(f"KNIFE-MISS {name}(原字串找不到)"); bad += 1; continue
                open(os.path.join(d, f), "w").write(s.replace(old, new, 1))
                ok, _ = run_one(t, d)
                print(("KNIFE-BITE " if not ok else "KNIFE-DULL ") + f"{name} → {t.__name__}")
                bad += ok
            finally: shutil.rmtree(d, ignore_errors=True)
    print(f"結果: {'OK' if bad == 0 else f'{bad} 項紅'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
