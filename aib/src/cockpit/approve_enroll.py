#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve enroll <站> --host <SSH 別名> [--url U]:一鍵啟用 AIB 核准(Andy 10-25「明明就在這台上，為什麼不能一鍵安裝啟用」;
規格 pending/SPEC-aib-approve-wp-2fa-ee4f8-1024.md §8-4「發布綁目的地、走釘死的 SSH 寫入」,@abb89 10-25 交辦)。

流程(任何一步失敗都停，不留半套):
  0. 這台要有可用的 Touch ID(LocalAuthentication 生物辨識)與 Secure Enclave;沒有就停(不退回沒保護的鑰,四語說明替代路)
  1. 用 SSH 的 -G 解出別名真正會連的 user@hostname:port(有 ProxyCommand / ProxyJump 就拒)
  2. ssh-keyscan 拿主機的 ed25519 公鑰 ⇒ 指紋;跟 ~/.ssh/known_hosts 比:有紀錄但不同 ⇒ 拒(中間人);沒紀錄 ⇒ 首次信任,
     對話框照實標明。之後每次 SSH 只認這把(暫存 known_hosts + StrictHostKeyChecking=yes)
  3. 讀站上現在的 /etc/ez2-aib/approve-keys/<站>.json(要 root;看現在的 kid、沿用 user_ids)
  4. 勾選框對話框(核心組的字):站、目的主機、主機指紋、寫入路徑、這台 kid、站上現在的 kid、殘餘風險 ⇒ 勾了才繼續
  5. 這台沒鑰才 key init(不跳指紋)
  6. key pem:用這把鑰簽「發布 kid|站|主機|路徑」⇒ SE 一定跳指紋;視窗字 kid 在最前 + 真實 host:port + 路徑
  7. 第二個勾選框：剛才有跳指紋、而且寫著這個 kid 嗎?(沒跳 = 鑰可能被換成不用指紋的 ⇒ 停)
  8. 新的機房讀取 token ⇒ 經同一條釘死的 SSH 寫 JSON(原檔先備份、root:root 644、先寫暫存再 mv;
     備份只留最近 3 份;設定夾不是 root 的就拒)
  9. 回讀:要是一般檔、uid 0、group/others 不可寫、內容跟寫的一模一樣(kid 對得上)⇒ 不對就放回原檔
 10. 本機登記站(token 檔 600);登記失敗也放回原檔。第 5 步新建的鑰在後面失敗時刪掉。

防線(同帳號 AI 威脅守則;照實寫):
  - 真正的防線只有兩個 Touch ID 以外的「本人看得到」:勾選框(核心組的字、TA.clean、主機指紋與 kid 不截斷)與
    SE 簽章的指紋視窗。SSH 每次都釘死 HostName / Port / User、ProxyCommand=none、ForwardAgent=no、ClearAllForwardings=yes,
    並關掉使用者 config 能繞過釘死的設定(ControlMaster / ControlPath 借舊連線、KnownHostsCommand 多吐一把鑰、
    PermitLocalCommand、UpdateHostKeys);主機公鑰只認對話框裡顯示指紋的那把。~/.ssh/known_hosts 只拿來比
    (AI 改得到 ⇒ 它頂多刪掉紀錄、退回首次信任;擋的是網路上的中間人)。
  - 「發布|kid|站|主機|路徑」的簽章只在本機驗(確認 SE 那把鑰真的簽了),不送站上;綁目的地靠的是視窗字。
  - 沒有生物辨識 / 沒有 SE 的 Mac:不能當核准機(照威脅守則只有指紋 / 登入密碼的系統視窗、密語、簽章算防線,
    勾選框不算)⇒ 一開始就停、印四語說明與替代路(有 Touch ID 的 Mac 或巧控鍵盤;WordPress 第二步改用 WebAuthn / TOTP)。
  - 殘餘風險(照實，也印在對話框):發布那一刻這台 Mac 若已被同帳號 AI 動過(換鑰 / 換 helper / 換這支程式 / 假視窗),
    發出去的就是假鑰，之後每次核准都不用指紋，直到換鑰。只在確定乾淨時按;發完把 kid 抄到這台 Mac 以外。
    第 7 步只擋「.sekey 被簡單換掉、而且沒人同時叫出指紋視窗」。
  - enrolled.json(啟用紀錄)是 AI 寫得到的紀錄，只給頁面顯示，不拿來做任何決定。
rc:0 啟用完成 / 2 沒做(印原因);寫到一半放回失敗 ⇒ 5(要人看)。
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import aib_approve as AA  # noqa: E402

SSH = "/usr/bin/ssh"                 # 絕對路徑:PATH 是 AI 改得到的
KEYSCAN = "/usr/bin/ssh-keyscan"
KEYGEN = "/usr/bin/ssh-keygen"
CONF_DIR = "/etc/ez2-aib/approve-keys"
ALIAS = "aib-enroll-pinned"          # HostKeyAlias:暫存 known_hosts 只有這一行
REMOTE_MAX = 4096                    # 站上設定檔上限(WP 端也是 < 4096)
_HOST = re.compile(r"[A-Za-z0-9._@-]{1,128}")
_B64 = re.compile(r"[A-Za-z0-9+/]{40,200}={0,2}")


def _abs_ssh(run):
    """SA.resolve 叫的是 "ssh":改成絕對路徑再跑。"""
    def go(argv, **kw):
        return run([SSH] + list(argv[1:]) if argv and argv[0] == "ssh" else argv, **kw)
    return go


def host_key(pin, run=subprocess.run):
    """ssh-keyscan 拿 ed25519 公鑰 ⇒ (blob_b64, "SHA256:…");拿不到 ⇒ (None, None)。"""
    try:
        r = run([KEYSCAN, "-T", "10", "-t", "ed25519", "-p", pin["port"], "--", pin["hostname"]],
                capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None, None
    keys = set()
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) == 3 and parts[1] == "ssh-ed25519" and _B64.fullmatch(parts[2]):
            keys.add(parts[2])
    if len(keys) != 1:  # 沒有、或一次回兩把不同的(不知道哪把是真的)⇒ 不連
        return None, None
    blob = keys.pop()
    try:
        raw = base64.b64decode(blob, validate=True)
    except ValueError:
        return None, None
    return blob, "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")


def known_hosts_trust(pin, blob, env, run=subprocess.run):
    """跟 ~/.ssh/known_hosts(只查這一個預設檔)比,逐行解析「[@標記] 主機 型別 鑰 [註解…]」:
      "revoked"   有 @revoked 標記的正是這把 ⇒ 拒
      "mismatch"  有 ed25519 紀錄但都不是這把 ⇒ 拒(可能有中間人)
      "known"     有 ed25519 紀錄且相同
      "no_ed25519" 這台只有別種型別(RSA / ECDSA)的紀錄,比不了 ⇒ 拒(不當首次信任)
      "first"     完全沒有紀錄 ⇒ 首次信任(對話框標明)
    @cert-authority 行略過(不支援憑證主機鑰)。檔是 AI 改得到的:它只能刪紀錄退回 first,擋的是網路中間人。"""
    kh = os.path.join(env.get("HOME") or os.path.expanduser("~"), ".ssh", "known_hosts")
    name = pin["hostname"] if pin["port"] == "22" else "[%s]:%s" % (pin["hostname"], pin["port"])
    try:
        r = run([KEYGEN, "-F", name, "-f", kh], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return "first"
    ed, other = [], False
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if not parts or parts[0].startswith("#"):
            continue
        mark = parts.pop(0) if parts[0].startswith("@") else ""
        if len(parts) < 3:
            continue
        kind, key = parts[1], parts[2]
        if mark == "@revoked":
            if key == blob:
                return "revoked"
            continue
        if mark:          # @cert-authority 等:不支援,略過
            continue
        if kind == "ssh-ed25519":
            ed.append(key)
        else:
            other = True
    if ed:
        return "known" if blob in ed else "mismatch"
    return "no_ed25519" if other else "first"


def ssh_argv(pin, host, known_hosts):
    return [SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
            "-o", "HostName=" + pin["hostname"], "-o", "Port=" + pin["port"], "-o", "User=" + pin["user"],
            "-o", "ProxyCommand=none", "-o", "ProxyJump=none", "-o", "HostKeyAlias=" + ALIAS,
            "-o", "UserKnownHostsFile=" + known_hosts, "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "CheckHostIP=no",
            # 使用者 config 能繞過上面那些的:借舊連線(不驗主機鑰)、多吐一把鑰、連上後在本機跑指令、自動加鑰
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "KnownHostsCommand=none",
            "-o", "PermitLocalCommand=no", "-o", "UpdateHostKeys=no",
            "-o", "VerifyHostKeyDNS=no",   # DNS 的 SSHFP 紀錄也能讓別把鑰過關
            # 這條連線只跑一個指令、只吃 stdin:不開 X11 / TTY / 背景、不換 session、不讓 config 換掉指令
            "-o", "ForwardX11=no", "-o", "RequestTTY=no", "-o", "ForkAfterAuthentication=no", "-o", "SessionType=default",
            "-o", "StdinNull=no", "-o", "RemoteCommand=none",
            "--", host]


def _remote(pin, host, known_hosts, cmd, data="", run=subprocess.run):
    """回 (rc, stdout);連不上 / 逾時 ⇒ (255, "")。"""
    try:
        r = run(ssh_argv(pin, host, known_hosts) + [cmd], input=data, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return 255, ""
    return r.returncode, r.stdout or ""


def _cmd_read(path):
    """第一行 1 / 0 = 有沒有這個檔(放回時要知道:原本沒有才刪),後面是內容。"""
    q = shlex.quote(path)
    return ('[ "$(id -u)" = 0 ] || exit 77; if [ -e %s ]; then echo 1; head -c %d %s; else echo 0; fi'
            % (q, REMOTE_MAX + 1, q))


def _cmd_write(path, bak):
    """設定夾已存在但不是 root 的 ⇒ 78(固定檔名的暫存檔可能被搶先放 symlink);失敗時清掉暫存檔;備份留最近 3 份。"""
    q, t, b, d = shlex.quote(path), shlex.quote(path + ".tmp-aib"), shlex.quote(bak), shlex.quote(os.path.dirname(path))
    return ('set -e; [ "$(id -u)" = 0 ] || exit 77; umask 022; '
            'if [ -e %s ]; then [ -d %s ] && [ ! -L %s ] && [ "$(stat -c %%u %s)" = 0 ] || exit 78; fi; '
            'mkdir -p %s; chmod 755 %s; trap "rm -f %s" EXIT; '
            'if [ -e %s ]; then cp -p %s %s; fi; cat > %s; chown root:root %s; chmod 644 %s; mv -f %s %s; '
            'ls -1t %s.bak-aib-* 2>/dev/null | tail -n +4 | while read -r f; do rm -f "$f"; done'
            % (d, d, d, d, d, d, t, q, q, b, t, t, t, t, q, q))


def _cmd_check(path):
    q = shlex.quote(path)
    return '[ -f %s ] && [ ! -L %s ] && stat -c "%%u %%a" %s && head -c %d %s' % (q, q, q, REMOTE_MAX + 1, q)


def _cmd_restore(path, bak, existed):
    """原本有檔:有備份才放回(沒備份 = 還沒動到原檔);原本沒有:刪掉新寫的。"""
    q, b = shlex.quote(path), shlex.quote(bak)
    if existed:
        return 'set -e; if [ -e %s ]; then mv -f %s %s; fi' % (b, b, q)
    return 'set -e; rm -f %s' % q


def _parse_conf(text):
    d = AA._loads(text) if text and len(text) <= REMOTE_MAX else None
    return d if isinstance(d, dict) else {}


def build_conf(kid, pem, token, old):
    """站上設定檔內容;沿用舊檔裡合法的 user_ids(root 寫的、點名可用的帳號編號)。"""
    c = {"kid": kid, "pem": pem, "room_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest()}
    ids = [u for u in old.get("user_ids") or [] if isinstance(u, int) and not isinstance(u, bool) and u > 0] \
        if isinstance(old.get("user_ids"), list) else []
    if ids:
        c["user_ids"] = ids
    return json.dumps(c, sort_keys=True) + "\n"


def readback_ok(out, want):
    """回讀:第一行「uid 權限」,後面是內容。uid 0、group/others 不可寫、內容跟寫的一模一樣。"""
    head, _, body = out.partition("\n")
    parts = head.split()
    if len(parts) != 2 or parts[0] != "0" or not re.fullmatch(r"[0-7]{3,4}", parts[1]):
        return False
    return int(parts[1], 8) & 0o022 == 0 and body == want


def _enrolled_path(env):
    return AA.approve_dir(env) / "enrolled.json"


def load_enrolled(env=None):
    """啟用紀錄(AI 寫得到 ⇒ 只給頁面顯示)。"""
    try:
        doc = AA._loads(AA._read_local(_enrolled_path(env)))
    except OSError:
        return {}
    rows = doc.get("sites") if isinstance(doc, dict) else None
    return {k: v for k, v in rows.items() if isinstance(v, dict)} if isinstance(rows, dict) else {}


def _record(env, site, kid, where, t):
    rows = load_enrolled(env)
    rows[site] = {"kid": kid, "host": where, "at": int(t)}
    AA._write_private(_enrolled_path(env), json.dumps({"v": 1, "sites": rows}, sort_keys=True) + "\n")


BIOMETRY_JXA = r"""
ObjC.import('LocalAuthentication');
var c = $.LAContext.alloc.init;
JSON.stringify({ok: c.canEvaluatePolicyError(1, null) === true});
"""   # 1 = LAPolicyDeviceOwnerAuthenticationWithBiometrics


def biometry_ready(run=subprocess.run):
    """這台現在能不能用生物辨識(Touch ID 有裝、有指紋、沒被鎖)。讀不懂 ⇒ False(往拒絕那邊)。
    AI 改得到結果:騙成 True 也只是後面 SE 簽不過;騙成 False 只是不啟用。"""
    import touchid_approve as TA
    try:
        r = run([TA.OSASCRIPT, "-l", "JavaScript", "-"], input=BIOMETRY_JXA, capture_output=True, text=True, timeout=20)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        return r.returncode == 0 and bool(lines) and json.loads(lines[-1]).get("ok") is True
    except (subprocess.TimeoutExpired, OSError, ValueError, AttributeError):
        return False


def _say(key, params, env, fallback):
    import touchid_approve as TA
    return AA._t(key, {k: TA.clean(v, 200) for k, v in params.items()}, env, fallback)


def explain(why, env=None):
    """失敗原因 ⇒ 給人看的一句(四語)。"""
    if why in ("no_biometry", "no_se", "touchid_unavailable"):
        return _say("aib.approve.enroll.no_biometry", {}, env,
                    "This Mac has no usable Touch ID (or no Secure Enclave), so it cannot be an AIB approval machine: without a "
                    "fingerprint there is no protection, so aib will not fall back to an unprotected key. Instead: enable it on "
                    "a Mac with Touch ID (or a Magic Keyboard with Touch ID), or use a WebAuthn security key or an "
                    "authenticator app (TOTP) for the WordPress second step.")
    if why in ("cancel", "not_checked", "touchid_cancel", "touchid_not_seen"):
        return _say("aib.approve.enroll.cancelled", {}, env, "Cancelled; nothing was changed.")
    if why == "host_key_no_ed25519":
        return _say("aib.approve.enroll.host_key_no_ed25519", {}, env,
                    "~/.ssh/known_hosts has this server only with another key type (RSA / ECDSA), so its ed25519 key cannot "
                    "be checked. Nothing was sent; add the server's ed25519 key to known_hosts after checking it.")
    if why in ("host_key_mismatch", "host_key_revoked"):
        return _say("aib.approve.enroll.host_key_mismatch", {}, env,
                    "The server's host key differs from ~/.ssh/known_hosts: possibly someone in between. Nothing was sent; "
                    "check the server's real fingerprint first.")
    if why.endswith("_restore_failed"):
        return _say("aib.approve.enroll.need_human", {"why": why}, env,
                    "Stopped half-way and could not put the server file back (%s): a person must check "
                    "/etc/ez2-aib/approve-keys on the server." % why)
    return _say("aib.approve.enroll.failed", {"why": why}, env, "Not enabled (%s); nothing was left half-done." % why)


def enroll(site, host, url=None, env=None, platform=None, run=subprocess.run, helper=None, ask=None, now=None):
    """回 (結果 dict, why)。ask = 勾選框(測試換假的);run = ssh / ssh-keyscan / helper 共用的 subprocess.run。"""
    import consent
    import secret_ask as SA
    env = AA._env(env)
    platform = sys.platform if platform is None else platform
    ask = ask or (lambda title, body, check: consent.ask_checkbox(title, body, check, env=env, run=run))
    site = str(site or "").lower()
    if not AA.SITE_RE.fullmatch(site) or "." not in site:
        return None, "bad_site"
    url = (url or "https://" + site).rstrip("/")
    if not AA._url_ok(site, url):
        return None, "bad_url"
    if not isinstance(host, str) or not _HOST.fullmatch(host) or host.startswith("-"):
        return None, "bad_host"
    if platform != "darwin" or str(env.get("AIB_TOUCHID", "")).strip() == "0":
        return None, "touchid_unavailable"   # 一開始就擋:沒有指紋就不跳任何視窗、不連主機
    if not biometry_ready(run):
        return None, "no_biometry"
    try:
        pin = SA.resolve(host, run=_abs_ssh(run))
    except ValueError as e:
        return None, str(e)
    if pin["hostname"].startswith("-"):
        return None, "ssh_resolve_failed"
    blob, fp = host_key(pin, run=run)
    if not blob:
        return None, "no_host_key"
    trust = known_hosts_trust(pin, blob, env, run=run)
    if trust in ("mismatch", "revoked", "no_ed25519"):
        return None, "host_key_" + trust
    path = "%s/%s.json" % (CONF_DIR, site)
    where = "%s@%s:%s" % (pin["user"], pin["hostname"], pin["port"])
    with tempfile.TemporaryDirectory(prefix="aib-enroll-") as td:
        kh = os.path.join(td, "known_hosts")
        with open(kh, "w", encoding="ascii") as f:
            f.write("%s ssh-ed25519 %s\n" % (ALIAS, blob))
        rc, cur = _remote(pin, host, kh, _cmd_read(path), run=run)
        if rc == 77:
            return None, "not_root"
        existed, _, cur = cur.partition("\n")
        if rc != 0 or existed not in ("0", "1"):
            return None, "remote_read_failed"
        existed = existed == "1"
        old = _parse_conf(cur)
        pub = AA.local_pub(env)
        mine = AA.kid_of(pub) if pub else _say("aib.approve.enroll.no_key", {}, env, "none yet (created next; the kid shows in the Touch ID window)")
        theirs = old.get("kid") if isinstance(old.get("kid"), str) and AA.KID_RE.fullmatch(old.get("kid")) else \
            _say("aib.approve.enroll.none", {}, env, "none")
        trust_txt = _say("aib.approve.enroll.trust_" + trust, {}, env,
                         "matches ~/.ssh/known_hosts" if trust == "known" else "first time trusted: not in ~/.ssh/known_hosts")
        p = {"site": site, "host": where, "alias": host, "fp": fp, "path": path, "kid": mine, "cur": theirs,
             "trust": trust_txt}
        body = _say("aib.approve.enroll.body", p, env,
                    "Summary (put together by aib from the arguments; may be wrong): publish this Mac's AIB approval key "
                    "to {site}, so its login second step is approved with Touch ID here.\n\nSite: {site}\nHost: {host} "
                    "(SSH alias {alias})\nHost key: {fp} ({trust})\nWrites: {path} (root 644, old file backed up)\nThis Mac's kid: {kid}\n"
                    "kid on the site now: {cur}\n\nResidual risk: if this Mac was already tampered with (key / helper / fake "
                    "window), the key you publish is the attacker's and every later approval needs no fingerprint until the "
                    "key is replaced. Only continue when you are sure this Mac is clean; write the kid down off this Mac."
                    .format(**p))
        r = ask(_say("aib.approve.enroll.title", {"site": site}, env, "Enable AIB approval for %s" % site), body,
                _say("aib.approve.enroll.check", {}, env, "I have read this, including the residual risk"))
        if not r.get("ok"):
            return None, r.get("why") or "cancel"
        created = False
        if not pub:
            got, why = AA.key_init(env, run=run, helper=helper, platform=platform)
            if why:
                return None, why
            created = True

        def fail(why):
            if created:
                try:
                    os.unlink(str(AA._sekey_path(env)))  # 這次新建的鑰沒發出去 ⇒ 不留
                except OSError:
                    pass
            return None, why

        dest = {"site": site, "host": where, "path": path}
        pem, why = AA.key_publish(env, run=run, helper=helper, platform=platform, dest=dest)
        if why:
            return fail(why)
        kid = AA.kid_of(AA.local_pub(env))
        r = ask(_say("aib.approve.enroll.seen_title", {}, env, "Did the fingerprint window appear?"),
                _say("aib.approve.enroll.seen_body", {"kid": kid}, env,
                     "Did a Touch ID fingerprint window just appear, showing kid %s? If it did not appear, this Mac's key may "
                     "have been swapped for one that needs no fingerprint: press Cancel." % kid),
                _say("aib.approve.enroll.seen_check", {"kid": kid}, env, "It appeared and showed kid %s" % kid))
        if not r.get("ok"):
            return fail("touchid_not_seen")
        token = secrets.token_urlsafe(32)
        want = build_conf(kid, pem, token, old)
        t = time.time() if now is None else now
        bak = "%s.bak-aib-%s" % (path, time.strftime("%Y%m%d-%H%M%S", time.gmtime(t)))
        rc, _ = _remote(pin, host, kh, _cmd_write(path, bak), data=want, run=run)
        if rc != 0:
            why = {77: "not_root", 78: "conf_dir_not_root"}.get(rc, "write_failed")
            rrc, _ = _remote(pin, host, kh, _cmd_restore(path, bak, existed), run=run)
            if rrc != 0:   # 寫入可能已經完成(斷線 / 逾時)、放回也失敗:站上可能是新鑰 ⇒ 鑰留著、要人看(rc 5)
                return None, why + "_restore_failed"
            return fail(why)
        rc, out = _remote(pin, host, kh, _cmd_check(path), run=run)
        bad = "readback_mismatch" if rc != 0 or not readback_ok(out, want) else None
        if not bad:
            _, why = AA.site_add(site, url, token, env)
            bad = why and "local_state"
        if bad:
            rrc, _ = _remote(pin, host, kh, _cmd_restore(path, bak, existed), run=run)
            if rrc != 0:
                return None, bad + "_restore_failed"   # 站上留著新檔、本機沒登記:要人看(rc 5);新鑰也留著才對得上
            return fail(bad)
        try:
            _record(env, site, kid, where, t)
        except OSError:
            pass  # 只是顯示用的紀錄
        return {"site": site, "kid": kid, "host": where, "host_key": fp, "host_key_trust": trust, "path": path,
                "backup": bak, "at": int(t)}, None
