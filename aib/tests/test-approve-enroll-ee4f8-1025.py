#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib approve enroll(一鍵啟用;規格 §8-4「發布綁目的地、走釘死的 SSH 寫入」,@abb89 10-25 交辦)。
SE helper、勾選框、ssh / ssh-keyscan、主機全換假的:不跳真視窗、不碰真 SE、不連任何主機。

E1 沒鑰 ⇒ 勾選框(站 / 真實 user@host:port / 主機指紋 / 寫入路徑 / 站上現在的 kid / 殘餘風險)⇒ key init ⇒ 簽
   「發布 kid|站|主機|路徑」(視窗字 kid 在最前、帶 host:port 與路徑)⇒ 第二個勾選框 ⇒ 站上寫 root 644 的 JSON
   (kid 是這把公鑰算的、room_token_sha256 = 本機 token 的 sha256、沿用 user_ids、原檔備份)⇒ 本機登記、紀錄;
   每一次 ssh 都釘死 HostName/Port/User、ProxyCommand=none、ForwardAgent=no、ClearAllForwardings=yes,
   而且只認 ssh-keyscan 那把主機公鑰(暫存 known_hosts + StrictHostKeyChecking=yes,不用 ~/.ssh/known_hosts)
E2 第一個勾選框取消 / 沒勾 ⇒ 只讀過站上、什麼都沒寫、沒建鑰、沒叫指紋
E3 第二個勾選框(有沒有跳指紋)沒勾 ⇒ 停、站上沒寫、這次新建的鑰刪掉
E4 回讀內容不對(被換掉)/ 不是 root / 群組可寫 ⇒ readback_mismatch、站上放回原檔(原本沒有就刪)、本機沒登記、新鑰刪掉
E5 寫入失敗 ⇒ 放回;遠端不是 root ⇒ not_root(連勾選框都沒跳)
E6 指紋取消 ⇒ touchid_cancel、站上沒寫
E7 一開始就擋:AIB_TOUCHID=0 / 不是 Mac ⇒ 沒連任何主機;ssh 別名有 ProxyCommand ⇒ ssh_proxy_refused;
   keyscan 沒回 / 回兩把不同的 ⇒ no_host_key;別名像選項 ⇒ bad_host;站名 / 網址不對 ⇒ bad_site / bad_url
E8 本機登記失敗 ⇒ 站上放回(不留半套)
E9 放回也失敗 ⇒ *_restore_failed、CLI rc 5
E10 已有鑰 ⇒ 不重建、勾選框顯示這台 kid;CLI 接上、key status 列出啟用紀錄;四語都有
刀(複本):K1 不釘主機公鑰(StrictHostKeyChecking=no)⇒ E1 紅;K2 第一個勾選框不看結果 ⇒ E2 紅;
   K3 第二個勾選框不看結果 ⇒ E3 紅;K4 不回讀 ⇒ E4 紅;K5 回讀不對不放回 ⇒ E4 紅;K6 視窗字不帶目的地 ⇒ E1 紅;
   K7 放回不管原本有沒有檔(沒備份就刪)⇒ E5 紅(在備份前失敗會刪掉站上原檔)
E11 沒生物辨識 ⇒ no_biometry、沒連主機、沒跳框;SE 沒有 / 簽時沒生物辨識 ⇒ no_se / no_biometry、站上沒寫;四語說明有替代路
E12 known_hosts 有紀錄但不同 ⇒ host_key_mismatch、什麼都沒連;相同 ⇒ 框裡標「相符」;沒紀錄 ⇒ 標「首次信任」(E1)
E13 寫入失敗 + 放回失敗 ⇒ write_failed_restore_failed、鑰留著;設定夾不是 root ⇒ conf_dir_not_root;暫存檔 trap、備份留 3 份
E14 ssh -G 解出的 hostname 像選項 ⇒ 拒、不叫 keyscan;keyscan 主機名前有 --;key status 非 JSON 印 biometry
   K8 拿掉 ControlMaster/ControlPath/KnownHostsCommand=none ⇒ E1 紅;K9 不比 known_hosts ⇒ E12 紅;
   K10 寫入失敗後放回失敗不報 ⇒ E13 紅;K11 不先查生物辨識 ⇒ E11 紅
r2(@abb89):E12 補 行尾註解的不同鑰 ⇒ mismatch、@revoked 同一把 ⇒ revoked、只有 RSA ⇒ no_ed25519、@cert-authority 略過;
   ssh 選項清單加 VerifyHostKeyDNS=no 與六個(定案);key status 經 main 注入 run(不叫真 osascript)。
   K12 拿掉 VerifyHostKeyDNS=no ⇒ E1 紅;K13 不看 @revoked ⇒ E12 紅;K14 型別 / 鑰取行尾(註解會錯位)⇒ E12 紅
r4(@abb89 #371 r3B):E15 第一框之後鑰被換(連 helper 一起)⇒ key_changed、站上沒寫;E16 簽完才換 .sekey ⇒ kid / conf /
   第二框都是簽過那把;E17 寫入後 Ctrl-C ⇒ 放回、新鑰刪、照樣丟出;放回失敗 ⇒ stderr 印要人看、鑰留著;
   E18 site_add 先 token 再 sites.json,sites.json 寫不進去 ⇒ token 放回(原本沒有就刪);E19 stdin 沒收完整(sha256 不符)
   ⇒ 79 = write_failed、原檔不動;E20 conf 超過 4096 ⇒ conf_too_big 沒寫;舊檔超長 ⇒ 框裡標讀不完整;沒原檔 ⇒ backup None;
   E21 helper 壞 ⇒ helper_failed(不折成 touchid_unavailable);yes 非 JSON 時沒指紋 ⇒ stderr 印替代路、stdout 仍一行 JSON;
   E22 路徑有空白 ⇒ 四個遠端指令都加引號;biometry_ready rc≠0 / 逾時 ⇒ False
   K15 改回重讀 .sekey ⇒ E16 紅;K16 拿掉 BaseException 放回 ⇒ E17 紅;K17 不加引號 ⇒ E22 紅
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _p in (REPO / "src" / "cockpit", REPO / "src", HERE):
    sys.path.insert(0, str(_p))
import aib_approve as AA  # noqa: E402
import approve_enroll as AE  # noqa: E402
from entitlement import p256  # noqa: E402
from aib_knife_copy import knife_copy  # noqa: E402
from aib_proc_guard import run_guarded  # noqa: E402

NOW = 1792930000
HOSTKEY = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"K" * 32).decode()
OTHERKEY = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"L" * 32).decode()
FP = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(HOSTKEY)).digest()).decode().rstrip("=")
PATH = "/etc/ez2-aib/approve-keys/ez2.app.json"
NEED = ("ForwardAgent=no", "ClearAllForwardings=yes", "HostName=203.0.113.9", "Port=2222", "User=root",
        "ProxyCommand=none", "ProxyJump=none", "StrictHostKeyChecking=yes", "GlobalKnownHostsFile=/dev/null")
# 使用者 config 能繞過釘死的(@abb89 #371 r1 M2):假主機沒看到這些就當成借了舊連線 / 多吐了鑰 ⇒ 不給連
UNBYPASS = ("ControlMaster=no", "ControlPath=none", "KnownHostsCommand=none", "PermitLocalCommand=no", "UpdateHostKeys=no",
            "VerifyHostKeyDNS=no", "ForwardX11=no", "RequestTTY=no", "ForkAfterAuthentication=no", "SessionType=default",
            "StdinNull=no", "RemoteCommand=none")   # r2 定案清單(@abb89:不再擴充)


def _der_int(v):
    b = v.to_bytes(33, "big").lstrip(b"\0")
    b = b if b[0] < 0x80 else b"\0" + b
    return b"\x02" + bytes([len(b)]) + b


def p256_sign(d, msg):
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    while True:
        k = secrets.randbelow(p256.N - 1) + 1
        r = p256._mul2(k, p256.G, 0, p256.G)[0] % p256.N
        s = pow(k, -1, p256.N) * (e + r * d) % p256.N
        if r and s:
            body = _der_int(r) + _der_int(s)
            return b"\x30" + bytes([len(body)]) + body


def p256_pub(d):
    x, y = p256._mul2(d, p256.G, 0, p256.G)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


class World:
    """一個 run():假 SE helper + 假 ssh -G + 假 ssh-keyscan + 假主機(檔案存在 dict)。"""

    def __init__(self, sign_fail=None):
        self.d = secrets.randbelow(p256.N - 1) + 1
        self.calls, self.signed, self.sign_fail = [], [], sign_fail
        self.files = {PATH: ('{"kid":"p256:0123456789abcdef","pem":"x","room_token_sha256":"' + "0" * 64
                             + '","user_ids":[12,true,-1,34]}\n', 0, 0o644)}
        self.proxy, self.keys, self.root, self.hostname = "none", [HOSTKEY], True, "203.0.113.9"
        self.known, self.known_lines, self.biometry = None, None, True
        self.write_rc, self.restore_rc, self.tamper, self.restores = 0, 0, None, []
        self.after_sign, self.short_stdin, self.boom_on_check = None, False, False

    def ssh_calls(self):
        return [c for c in self.calls if c[0] == AE.SSH and "-G" not in c]

    def __call__(self, argv, input=None, **kw):
        self.calls.append(list(argv))
        if argv[0] == "/fake/aib-sehelper":
            return self.helper(argv, input)
        if argv[0] == AE.KEYGEN:
            assert argv[1:3] == ["-F", "[203.0.113.9]:2222"], argv
            lines = self.known_lines or (["[203.0.113.9]:2222 ssh-ed25519 %s" % self.known] if self.known else [])
            out = "".join("# Host [203.0.113.9]:2222 found: line %d\n%s\n" % (i + 3, ln) for i, ln in enumerate(lines))
            return SimpleNamespace(returncode=0 if lines else 1, stdout=out, stderr="")
        if argv[0] == "/usr/bin/osascript":
            assert "canEvaluatePolicy" in input
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": self.biometry}) + "\n", stderr="")
        if argv[0] == AE.KEYSCAN:
            assert argv[-2] == "--", argv
            out = "".join("[203.0.113.9]:2222 ssh-ed25519 %s\n" % k for k in self.keys)
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        if argv[0] == AE.SSH and "-G" in argv:
            return SimpleNamespace(returncode=0, stdout="user root\nhostname %s\nport 2222\nproxycommand %s\n"
                                   % (self.hostname, self.proxy), stderr="")
        if argv[0] == AE.SSH:
            return self.remote(argv, input or "")
        raise AssertionError("unexpected %r" % argv)

    def helper(self, argv, data):
        opts = dict(zip(argv[2::2], argv[3::2]))
        if argv[1] == "se-create":
            Path(opts["--dir"]).mkdir(parents=True, exist_ok=True)
            doc = {"v": 1, "name": opts["--name"], "kind": "sekey", "se_key": "x",
                   "public_key": base64.b64encode(p256_pub(self.d)).decode()}
            (Path(opts["--dir"]) / (opts["--name"] + ".sekey")).write_text(json.dumps(doc))
            return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")
        if self.sign_fail:
            return SimpleNamespace(returncode=3, stdout=b"", stderr=(self.sign_fail + "\n").encode())
        self.signed.append((opts["--reason"], data))
        out = base64.b64encode(p256_sign(self.d, data))
        if self.after_sign:
            self.after_sign()
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    def remote(self, argv, data):
        opts = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
        kh = [o.split("=", 1)[1] for o in opts if o.startswith("UserKnownHostsFile=")]
        pinned = kh and Path(kh[0]).read_text().split() == [AE.ALIAS, "ssh-ed25519", HOSTKEY] \
            and "HostKeyAlias=" + AE.ALIAS in opts
        if not pinned or "StrictHostKeyChecking=yes" not in opts or not all(u in opts for u in UNBYPASS):
            return SimpleNamespace(returncode=255, stdout="", stderr="host key not pinned")  # 假主機:沒釘就不給連
        cmd = argv[-1]
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd.startswith('[ "$(id -u)" = 0 ] || exit 77; if [ -e'):
            if not self.root:
                return SimpleNamespace(returncode=77, stdout="", stderr="")
            out = "1\n" + self.files[PATH][0] if PATH in self.files else "0\n"
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        if cmd.startswith('set -e; [ "$(id -u)" = 0 ]'):
            if self.write_rc:
                return SimpleNamespace(returncode=self.write_rc, stdout="", stderr="")
            bak = re.search(r"cp -p \S+ (\S+);", cmd).group(1)
            if PATH in self.files:
                self.files[bak] = self.files[PATH]
            got = data[:-3] if self.short_stdin else data                  # 斷線:stdin 沒收完整
            if hashlib.sha256(got.encode()).hexdigest() != re.search(r"\| cut -c1-64\)\" = ([0-9a-f]{64}) \] \|\| exit 79", cmd).group(1):
                return SimpleNamespace(returncode=79, stdout="", stderr="")
            self.files[PATH] = (got, 0, 0o644)
            return ok
        if cmd.startswith("[ -f ") and "stat -c" in cmd:
            if self.boom_on_check:
                raise KeyboardInterrupt()
            body, uid, mode = self.files.get(PATH, ("", 0, 0o644))
            if self.tamper:
                body, uid, mode = self.tamper(body, uid, mode)
            return SimpleNamespace(returncode=0, stdout="%d %o\n%s" % (uid, mode, body), stderr="")
        if cmd.startswith("set -e; if [ -e ") or cmd.startswith("set -e; rm -f "):
            self.restores.append(cmd)
            if self.restore_rc:
                return SimpleNamespace(returncode=self.restore_rc, stdout="", stderr="")
            m = re.search(r"if \[ -e (\S+) \]", cmd)
            if not m:
                self.files.pop(PATH, None)
            elif m.group(1) in self.files:
                self.files[PATH] = self.files.pop(m.group(1))
            return ok
        raise AssertionError("unexpected remote cmd %r" % cmd)


class Ask:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def __call__(self, title, body, check):
        self.seen.append((title, body, check))
        a = self.answers.pop(0) if self.answers else "ok"
        return {"ok": a == "ok", "why": a}


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aib-enroll-")
        self.addCleanup(td.cleanup)
        self.env = {"EZ2_AIB_STATE_ROOT": td.name, "HOME": td.name, "EZ2_AIB_LOCALE": "zh-TW", "PATH": "/usr/bin:/bin"}
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        self.w = World()

    def enroll(self, ask=None, env=None, platform="darwin", host="eu01", site="ez2.app", url=None, w=None):
        self.ask = ask or Ask()
        return AE.enroll(site, host, url, env or self.env, platform=platform, run=w or self.w,
                         helper="/fake/aib-sehelper", ask=self.ask, now=NOW)

    def sekey(self):
        return AA._sekey_path(self.env)

    def assert_untouched(self):
        self.assertEqual(list(self.w.files), [PATH])
        self.assertIn("p256:0123456789abcdef", self.w.files[PATH][0])
        self.assertEqual(AA.load_sites(self.env), [])


class Enroll(Base):
    def test_e1_happy_path(self):
        self.assertFalse(self.sekey().exists())
        r, why = self.enroll()
        self.assertIsNone(why, r)
        kid = AA.kid_of(p256_pub(self.w.d))
        self.assertEqual((r["kid"], r["host"], r["host_key"], r["path"]), (kid, "root@203.0.113.9:2222", FP, PATH))
        # 第一個勾選框:核心組的字,關鍵資訊都在
        body = self.ask.seen[0][1]
        for s in ("ez2.app", "root@203.0.113.9:2222", "eu01", FP, PATH, "p256:0123456789abcdef", "殘餘風險", "可能不準",
                  "首次信任"):
            self.assertIn(s, body)
        self.assertIn(kid, self.ask.seen[1][1])                                # 第二個:有沒有跳、kid 對不對
        # 指紋視窗字:kid 最前、真實主機、路徑;簽的位元組綁目的地
        reason, data = self.w.signed[-1]
        self.assertTrue(reason.startswith("kid " + kid), reason)
        self.assertIn("root@203.0.113.9:2222", reason)
        self.assertIn(PATH, reason)
        self.assertEqual(data, ("ez2-aib-approve-publish|%s|ez2.app|root@203.0.113.9:2222|%s" % (kid, PATH)).encode())
        # 站上
        body, uid, mode = self.w.files[PATH]
        c = json.loads(body)
        self.assertEqual((uid, mode), (0, 0o644))
        self.assertEqual(c["kid"], kid)
        self.assertEqual(c["pem"], AA.pem_of(p256_pub(self.w.d)))
        token = (AA.approve_dir(self.env) / "tokens" / "ez2.app.token").read_text().strip()
        self.assertEqual(c["room_token_sha256"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(c["user_ids"], [12, 34])                             # 沿用合法的;true / -1 丟掉
        self.assertIn(r["backup"], self.w.files)                              # 原檔備份
        self.assertEqual(AA.load_sites(self.env), [{"site": "ez2.app", "url": "https://ez2.app"}])
        self.assertEqual(AE.load_enrolled(self.env)["ez2.app"], {"kid": kid, "host": "root@203.0.113.9:2222", "at": NOW})
        # 每次 ssh 都釘死
        self.assertEqual(len(self.w.ssh_calls()), 3)                          # 讀、寫、回讀
        for c in self.w.ssh_calls():
            for need in NEED + UNBYPASS:
                self.assertIn(need, c, need)
            self.assertEqual(c[c.index("--") + 1], "eu01")

    def test_e2_first_dialog_cancel(self):
        for ans in ("cancel", "not_checked", "timeout"):
            r, why = self.enroll(ask=Ask(ans))
            self.assertEqual((r, why), (None, ans))
            self.assert_untouched()
            self.assertEqual(len(self.w.ssh_calls()), 1)                      # 只讀過
            self.w.calls.clear()
        self.assertFalse(self.sekey().exists())
        self.assertEqual(self.w.signed, [])

    def test_e3_fingerprint_not_seen(self):
        r, why = self.enroll(ask=Ask("ok", "not_checked"))
        self.assertEqual((r, why), (None, "touchid_not_seen"))
        self.assert_untouched()
        self.assertFalse(self.sekey().exists())                               # 這次新建的鑰刪掉
        self.assertEqual(len(self.w.signed), 1)

    def test_e4_readback_mismatch_restores(self):
        for tamper in (lambda b, u, m: (b.replace('"p256:', '"p256:f'), u, m),
                       lambda b, u, m: (b, 1000, m), lambda b, u, m: (b, u, 0o664)):
            self.w = World()
            self.w.tamper = tamper
            r, why = self.enroll()
            self.assertEqual((r, why), (None, "readback_mismatch"))
            self.assert_untouched()
            self.assertFalse(self.sekey().exists())
        self.w = World()                                                       # 原本沒有檔 ⇒ 放回 = 刪掉
        del self.w.files[PATH]
        self.w.tamper = lambda b, u, m: ("x", u, m)
        self.assertEqual(self.enroll()[1], "readback_mismatch")
        self.assertEqual(self.w.files, {})

    def test_e5_write_failure_and_not_root(self):
        self.w.write_rc = 1                                                    # 在備份之前就失敗:原檔不能被刪
        self.assertEqual(self.enroll(), (None, "write_failed"))
        self.assert_untouched()
        self.assertNotIn("rm -f", self.w.restores[0])
        self.w = World()
        self.w.root = False
        self.assertEqual(self.enroll(), (None, "not_root"))
        self.assertEqual(self.ask.seen, [])
        self.assertEqual(self.w.signed, [])

    def test_e6_touchid_cancel(self):
        self.w = World(sign_fail="cancel")
        self.assertEqual(self.enroll(), (None, "touchid_cancel"))
        self.assert_untouched()
        self.assertFalse(self.sekey().exists())

    def test_e7_refused_up_front(self):
        for kw, why in (({"env": dict(self.env, AIB_TOUCHID="0")}, "touchid_unavailable"),
                        ({"platform": "linux"}, "touchid_unavailable"),
                        ({"host": "-oProxyCommand=x"}, "bad_host"), ({"host": "a b"}, "bad_host"),
                        ({"site": "localhost"}, "bad_site"), ({"url": "http://ez2.app"}, "bad_url")):
            self.w = World()
            self.assertEqual(self.enroll(**kw), (None, why), kw)
            self.assertEqual(self.w.calls, [], kw)
        for setup, why in ((lambda w: setattr(w, "proxy", "nc %h %p"), "ssh_proxy_refused"),
                           (lambda w: setattr(w, "keys", []), "no_host_key"),
                           (lambda w: setattr(w, "keys", [HOSTKEY, OTHERKEY]), "no_host_key")):
            self.w = World()
            setup(self.w)
            self.assertEqual(self.enroll(), (None, why))
            self.assertEqual(self.w.ssh_calls(), [])
            self.assertEqual(self.ask.seen, [])

    def test_e8_local_failure_restores(self):
        (AA.approve_dir(self.env)).mkdir(parents=True)
        (AA.approve_dir(self.env) / "tokens").write_text("not a dir")
        self.assertEqual(self.enroll(), (None, "local_state"))
        self.assertEqual(list(self.w.files), [PATH])
        self.assertIn("p256:0123456789abcdef", self.w.files[PATH][0])

    def test_e9_restore_failure_needs_a_human(self):
        self.w.tamper = lambda b, u, m: ("x", u, m)
        self.w.restore_rc = 1
        self.assertEqual(self.enroll(), (None, "readback_mismatch_restore_failed"))
        import unittest.mock as um
        out = io.StringIO()
        with um.patch.object(AE, "enroll", return_value=(None, "readback_mismatch_restore_failed")):
            self.assertEqual(AA.main(["enroll", "ez2.app", "--host", "eu01", "--json"], out=out, env=self.env), 5)
        self.assertEqual(json.loads(out.getvalue())["why"], "readback_mismatch_restore_failed")

    def test_e10_existing_key_cli_and_i18n(self):
        AA.key_init(self.env, run=self.w, helper="/fake/aib-sehelper", platform="darwin")
        kid = AA.kid_of(AA.local_pub(self.env))
        r, why = self.enroll()
        self.assertIsNone(why)
        self.assertEqual(r["kid"], kid)
        self.assertIn(kid, self.ask.seen[0][1])
        self.assertEqual([c for c in self.w.calls if c[:2] == ["/fake/aib-sehelper", "se-create"]], [self.w.calls[0]])
        out = io.StringIO()
        self.assertEqual(AA.main(["key", "status", "--json"], out=out, env=self.env, run=self.w), 0)
        self.assertEqual(json.loads(out.getvalue())["enrolled"]["ez2.app"]["kid"], kid)
        out = io.StringIO()
        self.assertEqual(AA.main(["enroll", "ez2.app", "--json"], out=out, env=self.env), 2)  # --host 必填
        keys = ("aib.approve.touchid_enroll", "aib.approve.enroll.title", "aib.approve.enroll.body",
                "aib.approve.enroll.check", "aib.approve.enroll.seen_body", "aib.approve.enroll.seen_check",
                "aib.approve.enroll.done", "aib.approve.enroll.no_key", "aib.approve.enroll.none",
                "aib.approve.enroll.cur_truncated", "aib.approve.enroll.need_human")
        for loc in ("zh-TW", "en", "ja", "ko"):
            doc = json.loads((REPO / "i18n" / (loc + ".json")).read_text(encoding="utf-8"))
            for k in keys:
                self.assertIn(k, doc, (loc, k))
            self.assertTrue(doc["aib.approve.touchid_enroll"].startswith("kid {kid}"), loc)
            for ph in ("{site}", "{host}", "{fp}", "{path}", "{kid}", "{cur}"):
                self.assertIn(ph, doc["aib.approve.enroll.body"], (loc, ph))
            self.assertIn("enroll", doc["aib.approve.help.usage"], loc)


class NoBiometryAndHostKey(Base):
    def test_e11_no_biometry_no_fallback(self):
        self.w.biometry = False
        self.assertEqual(self.enroll(), (None, "no_biometry"))
        self.assertEqual(self.w.ssh_calls(), [])
        self.assertEqual(self.ask.seen, [])
        self.assertFalse(self.sekey().exists())
        self.w = World(sign_fail="no_se")                                       # 指紋檢查過了、SE 卻沒有 ⇒ 停,不寫
        self.assertEqual(self.enroll(), (None, "no_se"))
        self.assert_untouched()
        self.assertFalse(self.sekey().exists())
        self.w = World(sign_fail="no_biometry")
        self.assertEqual(self.enroll(), (None, "no_biometry"))
        self.assert_untouched()
        for loc, word in (("zh-TW", "WebAuthn"), ("en", "WebAuthn"), ("ja", "WebAuthn"), ("ko", "WebAuthn")):
            env = dict(self.env, EZ2_AIB_LOCALE=loc)
            for why in ("no_biometry", "no_se", "touchid_unavailable"):
                self.assertIn(word, AE.explain(why, env), (loc, why))
        import unittest.mock as um
        out = io.StringIO()
        with um.patch.object(AE, "enroll", return_value=(None, "no_biometry")):
            self.assertEqual(AA.main(["enroll", "ez2.app", "--host", "eu01"], out=out, env=self.env), 2)
        self.assertIn("巧控鍵盤", out.getvalue())
        self.assertIn("why: no_biometry", out.getvalue())

    def test_e12_known_hosts(self):
        self.w.known = OTHERKEY                                                  # 有紀錄但不同 ⇒ 拒、什麼都沒連
        self.assertEqual(self.enroll(), (None, "host_key_mismatch"))
        self.assertEqual(self.w.ssh_calls(), [])
        self.assertEqual(self.ask.seen, [])
        self.assertIn("中間", AE.explain("host_key_mismatch", self.env))
        self.w = World()
        self.w.known = HOSTKEY
        r, why = self.enroll()
        self.assertIsNone(why)
        self.assertEqual(r["host_key_trust"], "known")
        self.assertIn("相符", self.ask.seen[0][1])
        self.assertNotIn("首次信任", self.ask.seen[0][1])
        H = "[203.0.113.9]:2222"
        for lines, want in (
                (["%s ssh-ed25519 %s my laptop key" % (H, OTHERKEY)], "host_key_mismatch"),   # 行尾註解不能讓不同鑰變首次信任
                (["@revoked %s ssh-ed25519 %s" % (H, HOSTKEY), "%s ssh-ed25519 %s" % (H, HOSTKEY)], "host_key_revoked"),
                (["%s ssh-rsa AAAAB3NzaC1yc2E" % H], "host_key_no_ed25519"),                    # 只有別種型別:比不了 ⇒ 拒
                (["@revoked %s ssh-ed25519 %s" % (H, OTHERKEY), "%s ssh-ed25519 %s c" % (H, HOSTKEY)], None),
                (["@cert-authority *.example ssh-ed25519 %s" % OTHERKEY], None)):           # 憑證 CA 行略過 ⇒ 首次信任
            self.w = World()
            self.w.known_lines = lines
            if self.sekey().exists():
                self.sekey().unlink()                                            # 新 World = 新 SE;舊鑰不留
            r, why = self.enroll()
            self.assertEqual(why, want, lines)
            if want:
                self.assertEqual(self.w.ssh_calls(), [], lines)
                self.assertTrue(AE.explain(want, self.env), want)

    def test_e13_write_and_restore_both_fail(self):
        self.w.write_rc, self.w.restore_rc = 255, 1                             # 寫可能已完成 + 放回失敗 ⇒ 鑰留著、要人看
        self.assertEqual(self.enroll(), (None, "write_failed_restore_failed"))
        self.assertTrue(self.sekey().exists())
        self.assertIn("approve-keys", AE.explain("write_failed_restore_failed", self.env))
        self.w.write_rc, self.w.restore_rc = 78, 0                               # 同一把鑰(上一輪留著的)
        self.assertEqual(self.enroll(), (None, "conf_dir_not_root"))
        cmd = AE._cmd_write(PATH, PATH + ".bak-aib-x", "0" * 64)
        self.assertIn('stat -c %u /etc/ez2-aib/approve-keys)" = 0 ] || exit 78', cmd)
        self.assertIn("trap", cmd)
        self.assertIn("tail -n +4", cmd)                                         # 備份只留最近 3 份

    def test_e14_hostname_like_option(self):
        self.w.hostname = "-oProxyCommand=x"
        self.assertEqual(self.enroll(), (None, "ssh_resolve_failed"))
        self.assertEqual([c for c in self.w.calls if c[0] == AE.KEYSCAN], [])
        out = io.StringIO()
        self.assertEqual(AA.main(["key", "status"], out=out, env=self.env, run=self.w), 0)
        self.assertIn("biometry:", out.getvalue())


class R4(Base):
    def swap_key(self, w):
        """同帳號 AI 換掉 .sekey(連 helper 一起換成它的鑰)。"""
        d = secrets.randbelow(p256.N - 1) + 1
        doc = json.loads(self.sekey().read_text())
        doc["public_key"] = base64.b64encode(p256_pub(d)).decode()
        self.sekey().write_text(json.dumps(doc))
        return d

    def test_e15_key_swapped_before_signing(self):
        AA.key_init(self.env, run=self.w, helper="/fake/aib-sehelper", platform="darwin")
        mine = AA.kid_of(AA.local_pub(self.env))

        def ask(title, body, check):
            self.assertIn(mine, body)
            self.w.d = self.swap_key(self.w)
            return {"ok": True}
        r, why = AE.enroll("ez2.app", "eu01", None, self.env, platform="darwin", run=self.w, helper="/fake/aib-sehelper",
                           ask=ask, now=NOW)
        self.assertEqual((r, why), (None, "key_changed"))
        self.assert_untouched()
        self.assertEqual(len(self.w.ssh_calls()), 1)                          # 只讀過

    def test_e16_key_swapped_after_signing(self):
        AA.key_init(self.env, run=self.w, helper="/fake/aib-sehelper", platform="darwin")
        a = p256_pub(self.w.d)
        self.w.after_sign = lambda: self.swap_key(self.w)
        r, why = self.enroll()
        self.assertIsNone(why, r)
        kid = AA.kid_of(a)
        self.assertNotEqual(AA.kid_of(AA.local_pub(self.env)), kid)          # 檔已經換了
        self.assertEqual(r["kid"], kid)
        c = json.loads(self.w.files[PATH][0])
        self.assertEqual((c["kid"], c["pem"]), (kid, AA.pem_of(a)))          # kid 與 pem 是同一把(簽過那把)
        self.assertIn(kid, self.ask.seen[1][1])

    def test_e17_interrupt_restores(self):
        import unittest.mock as um
        self.w.boom_on_check = True
        with self.assertRaises(KeyboardInterrupt):
            self.enroll()
        self.assert_untouched()
        self.assertFalse(self.sekey().exists())                               # 新鑰沒發出去 ⇒ 刪
        self.w = World()
        self.w.boom_on_check, self.w.restore_rc = True, 1
        err = io.StringIO()
        with um.patch.object(AE.sys, "stderr", err), self.assertRaises(KeyboardInterrupt):
            self.enroll()
        self.assertIn("approve-keys", err.getvalue())                         # 要人看
        self.assertTrue(self.sekey().exists())                                # 站上可能是新鑰 ⇒ 鑰留著

    def test_e18_site_add_token_first(self):
        tokdir = AA.approve_dir(self.env) / "tokens"
        tokdir.mkdir(parents=True)
        (AA.approve_dir(self.env) / "sites.json").mkdir()                     # sites.json 寫不進去
        tok = tokdir / "ez2.app.token"
        self.assertEqual(AA.site_add("ez2.app", None, "n" * 32, self.env), (None, "local_state"))
        self.assertFalse(tok.exists())                                         # 原本沒有 ⇒ 刪
        tok.write_text("o" * 32 + "\n")
        self.assertEqual(AA.site_add("ez2.app", None, "n" * 32, self.env), (None, "local_state"))
        self.assertEqual(tok.read_text(), "o" * 32 + "\n")                    # 原本有 ⇒ 放回

    def test_e19_short_stdin_not_moved(self):
        self.w.short_stdin = True
        self.assertEqual(self.enroll(), (None, "write_failed"))
        self.assert_untouched()
        with self.assertRaises(ValueError):
            AE._cmd_write(PATH, PATH + ".b", "x; rm -rf /")

    def test_e20_sizes_and_backup(self):
        ids = list(range(10 ** 9, 10 ** 9 + 325))
        self.w.files[PATH] = (json.dumps({"kid": "p256:0123456789abcdef", "user_ids": ids}) + "\n", 0, 0o644)
        self.assertLessEqual(len(self.w.files[PATH][0]), AE.REMOTE_MAX)
        self.assertEqual(self.enroll(), (None, "conf_too_big"))
        self.assertEqual(len(self.w.ssh_calls()), 1)
        self.assertFalse(self.sekey().exists())
        self.w = World()
        self.w.files[PATH] = ("{" + " " * 5000 + "}\n", 0, 0o644)            # 舊檔超長
        r, why = self.enroll()
        self.assertIsNone(why, r)
        self.assertIn("讀不完整", self.ask.seen[0][1])
        self.assertNotIn("user_ids", json.loads(self.w.files[PATH][0]))
        self.w = World()
        del self.w.files[PATH]
        os.unlink(str(self.sekey()))
        r, why = self.enroll()
        self.assertIsNone(why, r)
        self.assertIsNone(r["backup"])                                        # 原本沒檔 ⇒ 沒備份

    def test_e21_helper_failed_and_yes_hint(self):
        import unittest.mock as um
        for fail in ("helper_failed", "build_failed"):
            self.w = World(sign_fail=fail)
            self.assertEqual(self.enroll(), (None, "helper_failed"))
            self.assert_untouched()

        def fake_yes(nonce, env=None, detail=None):
            detail.append("no_biometry")
            return "touchid_unavailable", None
        n = "A" * 43
        for argv, hint in ((["yes", n], True), (["yes", n, "--json"], False)):
            out, err = io.StringIO(), io.StringIO()
            with um.patch.object(AA, "yes", fake_yes), um.patch.object(AA.sys, "stderr", err):
                self.assertEqual(AA.main(argv, out=out, env=self.env), 0)
            self.assertEqual(json.loads(out.getvalue()), {"nonce": n, "result": "touchid_unavailable"})
            self.assertEqual(out.getvalue().count("\n"), 1)
            self.assertEqual("WebAuthn" in err.getvalue(), hint, argv)

    def test_e22_quoting_and_biometry_edges(self):
        import subprocess
        p = "/etc/a b/x.json"
        for cmd in (AE._cmd_read(p), AE._cmd_write(p, p + ".bak", "0" * 64), AE._cmd_check(p),
                    AE._cmd_restore(p, p + ".bak", True), AE._cmd_restore(p, p + ".bak", False)):
            self.assertIn("'/etc/a b/x.json", cmd)
            self.assertNotIn(" /etc/a b/", cmd)
        ok = json.dumps({"ok": True}) + "\n"
        self.assertFalse(AE.biometry_ready(lambda *a, **k: SimpleNamespace(returncode=1, stdout=ok, stderr="")))

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("osascript", 20)
        self.assertFalse(AE.biometry_ready(slow))
        self.assertTrue(AE.biometry_ready(lambda *a, **k: SimpleNamespace(returncode=0, stdout=ok, stderr="")))


class Knives(unittest.TestCase):
    def _red(self, needle, repl, test, cls="Enroll"):
        name = Path(__file__).name
        with knife_copy(REPO, "src/cockpit/approve_enroll.py", needle, repl, tests=(name,)) as root:
            r = run_guarded([sys.executable, str(root / "tests" / name), cls + "." + test], cwd=str(root),
                            max_procs=60, timeout=300)
        self.assertNotEqual(r.returncode, 0, "刀落了測試還綠:" + test)

    def test_k1_host_key_not_pinned(self):
        self._red('"StrictHostKeyChecking=yes"', '"StrictHostKeyChecking=no"', "test_e1_happy_path")

    def test_k2_first_dialog_ignored(self):
        self._red('        if not r.get("ok"):\n            return None, r.get("why") or "cancel"', "        pass",
                  "test_e2_first_dialog_cancel")

    def test_k3_seen_dialog_ignored(self):
        self._red('        if not r.get("ok"):\n            return fail("touchid_not_seen")', "        pass",
                  "test_e3_fingerprint_not_seen")

    def test_k4_no_readback(self):
        self._red('bad = "readback_mismatch" if rc != 0 or not readback_ok(out, want) else None', "bad = None",
                  "test_e4_readback_mismatch_restores")

    def test_k5_no_restore(self):
        self._red("            if bad:\n                if restore() != 0:",
                  "            if bad:\n                if False:",
                  "test_e4_readback_mismatch_restores")

    def test_k7_restore_deletes_original(self):
        self._red("    if existed:\n", "    if False:\n", "test_e5_write_failure_and_not_root")

    def test_k8_config_can_bypass_pin(self):
        self._red('            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "KnownHostsCommand=none",\n', "",
                  "test_e1_happy_path")

    def test_k9_no_known_hosts_compare(self):
        self._red('    if trust in ("mismatch", "revoked", "no_ed25519"):', "    if False:", "test_e12_known_hosts",
                  cls="NoBiometryAndHostKey")

    def test_k12_dns_can_bypass_pin(self):
        self._red('            "-o", "VerifyHostKeyDNS=no",', "", "test_e1_happy_path")

    def test_k13_revoked_ignored(self):
        self._red('            if key == blob:\n                return "revoked"', '            if False:\n                return "revoked"',
                  "test_e12_known_hosts", cls="NoBiometryAndHostKey")

    def test_k14_trailing_comment_misparsed(self):
        self._red("        kind, key = parts[1], parts[2]", "        kind, key = parts[-2], parts[-1]", "test_e12_known_hosts",
                  cls="NoBiometryAndHostKey")

    def test_k10_write_restore_failure_hidden(self):
        self._red('                if restore() != 0:   # 寫入可能已經完成', "                if False:   # 寫入可能已經完成",
                  "test_e13_write_and_restore_both_fail", cls="NoBiometryAndHostKey")

    def test_k11_no_biometry_check(self):
        self._red("    if not biometry_ready(run):", "    if False:", "test_e11_no_biometry_no_fallback",
                  cls="NoBiometryAndHostKey")

    def test_k15_reread_sekey(self):
        self._red("        kid = AA.kid_of(signed)", "        kid = AA.kid_of(AA.local_pub(env))", "test_e16_key_swapped_after_signing",
                  cls="R4")

    def test_k16_no_interrupt_restore(self):
        self._red("        except BaseException:\n            if wrote:", "        except ZeroDivisionError:\n            if wrote:",
                  "test_e17_interrupt_restores", cls="R4")

    def test_k17_unquoted_path(self):
        self._red("    q = shlex.quote(path)\n    return '[ -f %s ]", "    q = path\n    return '[ -f %s ]",
                  "test_e22_quoting_and_biometry_edges", cls="R4")

    def test_k6_reason_without_dest(self):
        self._red("dest=dest)", "dest=None)", "test_e1_happy_path")


if __name__ == "__main__":
    unittest.main()
