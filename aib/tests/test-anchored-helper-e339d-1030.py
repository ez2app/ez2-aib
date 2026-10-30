#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""系統錨定驗單第 1 張:root 擁有固定路徑的 aib-sehelper(規格 SPEC-anchored-grant-verify-e339d-1029,Andy 10-29 21:1x 裁)。

A1 全部 root 擁有、不可寫、stamp = 原始碼雜湊 ⇒ 採用固定路徑
A2 binary 不是 root 擁有 ⇒ not_root_owned          A3 某層群組 / 其他人可寫 ⇒ writable
A4 binary 是捷徑 ⇒ symlink                          A5 上層目錄(/usr/local)不是 root 擁有 ⇒ not_root_owned
A6 stamp ≠ 原始碼雜湊(aib update 改了 helper)⇒ outdated     A7 不是 Mac ⇒ not_mac
A8 K-A1 / K-A3:EZ2_AIB_STATE_ROOT + 狀態根假 helper(stamp = 真原始碼雜湊)+ PATH 墊假的 ⇒ 正式 Ctx 不採用、一次都沒叫
A9 安裝程式:root 才裝;上層可寫 ⇒ 不裝;裝好 binary 0755 / stamp 0644 / 都 chown 0:0 / stamp = 原始碼雜湊
A10 doctor:有單但 helper 沒裝 ⇒ note helper_not_installed      A11 --helper status 給本人跑的指令,不代跑
A12 #432 R1 H1:簽章沒 hardened runtime / codesign 失敗 / 讀不懂 ⇒ not_hardened(沒 runtime 的 root binary 吃 DYLD_*)
A13 #432 R1 L3:固定路徑是目錄(root 擁有、不可寫)⇒ not_file
A14 #432 R1 H1 縱深:叫 helper 只給 PATH=/usr/bin:/bin(呼叫端的 DYLD_INSERT_LIBRARIES / HOME 不帶過去)
A9 另驗 #432 R1 M1 / H1:只編、只雜湊 root 暫存夾裡的複本(編譯中原檔被換 ⇒ stamp 仍是複本的雜湊);簽章帶 -o runtime;
  子行程 env = PATH / HOME=/var/root;先印安裝程式 / 原始碼 sha256 與 git HEAD(L1)
A9b #432 R2 L2:複本寫好、算雜湊之前原檔被換 ⇒ stamp 與印出的 digest 都是複本(真正裝的那份)的雜湊
A15 #432 R2 L1:git_head 只收 refs/ 開頭、不含 .. 的 ref,讀出值只收 40 位十六進位(ref: ../../<檔> 不會讓 root 讀檔印出)
刀(複本落刀,錨要恰好一次):KA1 拿掉擁有者檢查 ⇒ A2 紅;KA2 拿掉可寫檢查 ⇒ A3 紅;KA3 拿掉捷徑檢查 ⇒ A4 紅;
  KA4 只查 binary 不查上層 ⇒ A5 紅;KA5 拿掉 stamp 比對 ⇒ A6 紅;KA6 helper_exe 退回狀態根 build_helper ⇒ A8 紅;
  KA7 安裝程式拿掉上層檢查 ⇒ A9 紅;KA8 安裝程式拿掉 euid 檢查 ⇒ A9 紅;KA9 拿掉 hardened 檢查 ⇒ A12 紅;
  KA10 安裝程式簽章拿掉 -o runtime ⇒ A9 紅;KA11 拿掉 S_ISREG ⇒ A13 紅;KA12 安裝程式改編原檔 ⇒ A9 紅;
  KA13 se_vault._call 不給 env ⇒ A14 紅;KA14 git_head 拿掉 ref 檢查 ⇒ A15 紅;KA15 拿掉值的十六進位檢查 ⇒ A15 紅;
  KA16 雜湊改算原檔 ⇒ A9b 紅;KA17 git_head 讀檔拿掉 O_NOFOLLOW ⇒ A15 紅(0.1.65 Low)。
全部假 stat / 假 run / 暫存目錄;不碰 /usr/local、不跑 sudo、不編譯、不叫 Secure Enclave。
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cockpit"
TOOLS = ROOT / "tools" / "sehelper"
sys.path.insert(0, str(SRC))
os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
import anchored_helper as AH  # noqa: E402
import approve_grants as GR  # noqa: E402
import se_vault as SV  # noqa: E402

_ispec = importlib.util.spec_from_file_location("aib_install_anchored", str(TOOLS / "install_anchored_helper.py"))
INST = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(INST)

DIR = stat.S_IFDIR | 0o755
REG = stat.S_IFREG | 0o755
LNK = stat.S_IFLNK | 0o755


def st(mode, uid=0):
    return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))


def tree(**over):
    """固定路徑那一串的假 stat(預設全部 root 擁有、不可寫);over 用路徑換掉某一層。"""
    t = {AH.EXE: st(REG), AH.STAMP: st(stat.S_IFREG | 0o644)}
    d = AH.ANCHOR_DIR
    while True:
        t[d] = st(DIR)
        if d == "/":
            break
        d = os.path.dirname(d)
    t.update(over)

    def lstat(p):
        if p not in t:
            raise FileNotFoundError(p)
        return t[p]
    return lstat


GOOD = hashlib.sha256(b"src").hexdigest()


class _Codesign:
    """假 /usr/bin/codesign -d:預設回帶 runtime 的旗標(真機輸出的格式)。"""
    def __init__(self, line="flags=0x10002(adhoc,runtime)", rc=0):
        self.line, self.rc, self.calls = line, rc, []

    def __call__(self, argv, **k):
        self.calls.append((argv, k))
        return type("P", (), {"returncode": self.rc, "stdout": "",
                              "stderr": "Executable=%s\nCodeDirectory v=20500 size=1334 %s hashes=35+2\n" % (argv[-1], self.line)})()


def chk(M, lstat, stamp=GOOD, platform="darwin", run=None):
    return M.check(lstat=lstat, read=lambda p: stamp + "\n", src_digest=lambda: GOOD, platform=platform,
                   run=run or _Codesign())


def cell_a1(t, M):
    t.assertEqual(chk(M, tree()), (M.EXE, None))


def cell_a2(t, M):
    t.assertEqual(chk(M, tree(**{M.EXE: st(REG, uid=501)})), (None, "not_root_owned"))


def cell_a3(t, M):
    t.assertEqual(chk(M, tree(**{M.ANCHOR_DIR: st(stat.S_IFDIR | 0o775)})), (None, "writable"))
    t.assertEqual(chk(M, tree(**{M.EXE: st(stat.S_IFREG | 0o757)})), (None, "writable"))


def cell_a4(t, M):
    t.assertEqual(chk(M, tree(**{M.EXE: st(LNK)})), (None, "symlink"))


def cell_a5(t, M):
    t.assertEqual(chk(M, tree(**{"/usr/local": st(DIR, uid=501)})), (None, "not_root_owned"))     # Intel Homebrew
    t.assertEqual(chk(M, tree(**{"/usr/local/libexec": st(LNK)})), (None, "symlink"))


def cell_a6(t, M):
    t.assertEqual(chk(M, tree(), stamp="0" * 64), (None, "outdated"))


def cell_a7(t, M):
    t.assertEqual(chk(M, tree(), platform="linux"), (None, "not_mac"))
    t.assertEqual(chk(M, lambda p: (_ for _ in ()).throw(FileNotFoundError(p))), (None, "not_installed"))


def cell_a12(t, M):
    cs = _Codesign()
    t.assertEqual(chk(M, tree(), run=cs), (M.EXE, None))
    t.assertEqual([(c[0], c[1].get("env")) for c in cs.calls][:1],
                  [(["/usr/bin/codesign", "-d", "--verbose=2", M.EXE], {"PATH": "/usr/bin:/bin"})])
    t.assertEqual(chk(M, tree(), run=_Codesign("flags=0x2(adhoc)")), (None, "not_hardened"))
    t.assertEqual(chk(M, tree(), run=_Codesign("flags=0x2(adhoc,runtime)")), (None, "not_hardened"))    # 位元沒有
    t.assertEqual(chk(M, tree(), run=_Codesign("flags=0x10002(adhoc)")), (None, "not_hardened"))        # 名稱沒有
    t.assertEqual(chk(M, tree(), run=_Codesign(rc=1)), (None, "not_hardened"))
    t.assertEqual(chk(M, tree(), run=_Codesign("garbage")), (None, "not_hardened"))
    t.assertEqual(chk(M, tree(), run=lambda *a, **k: (_ for _ in ()).throw(OSError("no codesign"))), (None, "not_hardened"))


def cell_a13(t, M):
    t.assertEqual(chk(M, tree(**{M.EXE: st(DIR)})), (None, "not_file"))
    t.assertEqual(chk(M, tree(**{M.STAMP: st(DIR)})), (None, "not_file"))


def cell_a14(t, SV):
    seen = []

    def run(argv, **k):
        seen.append(k)
        return type("P", (), {"returncode": 0, "stdout": b"{}", "stderr": b""})()
    env = dict(os.environ, DYLD_INSERT_LIBRARIES="/tmp/evil.dylib", DYLD_LIBRARY_PATH="/tmp", HOME="/tmp/fakehome")
    t.assertEqual(SV._call(["se-pub"], env, b"", run, "/x/aib-sehelper", "darwin"), (b"{}", None))
    t.assertEqual(seen[0].get("env"), {"PATH": "/usr/bin:/bin"})


def cell_a8(t, G):
    """正式 Ctx(沒注入 helper):狀態根種好假 helper + stamp、PATH 也墊一支 ⇒ 都不採用,run 一次都沒被叫。"""
    td = tempfile.mkdtemp(prefix="aib-anch-a8-")
    try:
        root = os.path.join(td, "state")
        os.makedirs(os.path.join(root, "bin"))
        fake = os.path.join(root, "bin", "aib-sehelper")
        for p in (fake, os.path.join(td, "aib-sehelper")):
            with open(p, "w") as f:
                f.write("#!/bin/sh\necho '{\"ok\":true,\"owner_acl\":\"probed\",\"public_key\":\"AA==\"}'\n")
            os.chmod(p, 0o700)
        import se_vault
        with open(se_vault.SRC, "rb") as f, open(fake + ".sha256", "w") as g:
            g.write(hashlib.sha256(f.read()).hexdigest())
        env = dict(os.environ, EZ2_AIB_STATE_ROOT=root, EZ2_AIB_HOME=root, PATH=td + os.pathsep + os.environ.get("PATH", ""))
        ran = []
        ctx = G.Ctx(env=env, platform="darwin", run=lambda *a, **k: ran.append(a) or t.fail("不該叫任何 helper"),
                    anchored=lambda platform=None: (None, "not_installed"))
        t.assertEqual(ctx.helper_exe(), (None, "helper_not_installed"))
        t.assertEqual(ctx.helper_call(["se-pub", "--name", G.KEY_NAME, "--dir", G._key_dir(env)]),
                      (None, "helper_not_installed"))
        t.assertEqual(G.current_kid(ctx)[0], None)
        t.assertEqual(ran, [])
    finally:
        shutil.rmtree(td, ignore_errors=True)


class _Run:
    def __init__(self, ok=True, swap=None):
        self.calls, self.kw, self.ok, self.swap = [], [], ok, swap

    def __call__(self, argv, **k):
        self.calls.append(argv)
        self.kw.append(k)
        if "swiftc" in argv:
            if self.swap:                 # 編譯開始時同帳號 AI 把原檔換成惡意版(M1)
                with open(self.swap, "w") as f:
                    f.write("// evil\n")
            with open(argv[argv.index("-o") + 1], "wb") as f:
                f.write(b"\xcf\xfa\xed\xfe fake mach-o")
        return type("P", (), {"returncode": 0 if self.ok else 1, "stdout": "", "stderr": ""})()


def _inst(M, td, euid=0, parents_ok=True, presrc=None):
    os.makedirs(td)
    td = os.path.realpath(td)            # /var → /private/var 是捷徑;安裝程式會擋捷徑
    src = os.path.join(td, "aib-sehelper.swift")
    with open(src, "w") as f:
        f.write("// swift\n")
    if presrc is not None:               # A9b:讓「算雜湊前換原檔」換的是這次的原檔
        presrc[0] = src
    anchor = os.path.join(td, "root", "usr", "local", "libexec", "ez2-aib")
    owned = []
    real = os.lstat

    def lstat(p):
        r = real(p)                       # 暫存目錄是本人擁有 ⇒ 換成 root 擁有、不可寫(parents_ok=False ⇒ 第一層上層可寫)
        mode = (r.st_mode & ~0o022) | (0o002 if not parents_ok and p == os.path.dirname(os.path.dirname(anchor)) else 0)
        return os.stat_result((mode,) + tuple(r)[1:4] + (0,) + tuple(r)[5:])
    os.makedirs(os.path.dirname(os.path.dirname(anchor)))
    out = io.StringIO()
    run = _Run(swap=src)
    rc = M.install(src=src, anchor_dir=anchor, run=run, euid=lambda: euid,
                   chown=lambda p, u, g: owned.append((os.path.basename(p), u, g)), lstat=lstat, out=out, platform="darwin",
                   tmp_parent=td)
    _inst.last_run = run
    return rc, anchor, owned, src, out.getvalue()


def cell_a9(t, M):
    td = tempfile.mkdtemp(prefix="aib-anch-a9-")
    try:
        rc, anchor, owned, src, msg = _inst(M, os.path.join(td, "ok"))
        t.assertEqual(rc, 0)
        exe, stamp = os.path.join(anchor, "aib-sehelper"), os.path.join(anchor, "aib-sehelper.sha256")
        t.assertEqual(stat.S_IMODE(os.stat(exe).st_mode), 0o755)
        t.assertEqual(stat.S_IMODE(os.stat(stamp).st_mode), 0o644)
        with open(stamp) as g:            # 原檔在編譯時被換成 evil ⇒ stamp 仍是換之前(複本)的雜湊
            t.assertEqual(g.read().strip(), hashlib.sha256(b"// swift\n").hexdigest())
        run = _inst.last_run
        swift = [c for c in run.calls if "swiftc" in c][0]
        t.assertNotEqual(swift[-1], src)                      # 編的是複本,不是使用者可寫的原檔
        t.assertNotEqual(os.path.dirname(swift[-1]), os.path.dirname(src))
        t.assertIn("-module-cache-path", swift)
        sign = [c for c in run.calls if c[0] == "/usr/bin/codesign"][0]
        t.assertEqual(sign[1:6], ["-s", "-", "-f", "-o", "runtime"])
        for k in run.kw:
            t.assertEqual(k.get("env"), {"PATH": "/usr/bin:/bin", "HOME": "/var/root"})
        t.assertIn(hashlib.sha256(b"// swift\n").hexdigest(), msg)        # L1:先印原始碼 sha256 / git HEAD 給本人核對
        t.assertIn("git HEAD", msg)
        t.assertEqual(sorted(owned), sorted([("ez2-aib", 0, 0), ("aib-sehelper.new", 0, 0), ("aib-sehelper.sha256.new", 0, 0)]))
        t.assertFalse(os.path.exists(exe + ".new"))
        rc, anchor, owned, _, msg = _inst(M, os.path.join(td, "user"), euid=501)
        t.assertEqual((rc, owned, os.path.exists(anchor)), (2, [], False))
        t.assertIn("sudo", msg)
        rc, anchor, owned, _, _ = _inst(M, os.path.join(td, "brew"), parents_ok=False)
        t.assertEqual((rc, owned, os.path.exists(anchor)), (3, [], False))
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_a9b(t, M):
    td = tempfile.mkdtemp(prefix="aib-anch-a9b-")
    real = M._sha256
    try:
        src_box = []

        def swap_then_hash(path):          # 每次算雜湊前,AI 先把原檔換成惡意版
            if src_box:
                with open(src_box[0], "w") as f:
                    f.write("// evil\n")
            return real(path)
        M._sha256 = swap_then_hash
        os.makedirs(os.path.join(td, "x"))
        src_box.append(os.path.join(os.path.realpath(td), "x", "aib-sehelper.swift"))
        good = hashlib.sha256(b"// swift\n").hexdigest()
        rc, anchor, _, _, msg = _inst(M, os.path.join(td, "ok"), presrc=src_box)
        t.assertEqual(rc, 0)
        with open(os.path.join(anchor, "aib-sehelper.sha256")) as g:
            t.assertEqual(g.read().strip(), good)
        t.assertIn("sha256 " + good, msg)
    finally:
        M._sha256 = real
        shutil.rmtree(td, ignore_errors=True)


def _repo(td, head, refs=None, gitfile=False):
    os.makedirs(td)
    g = os.path.join(td, "gitdir" if gitfile else ".git")
    os.makedirs(g)
    with open(os.path.join(g, "HEAD"), "w") as f:
        f.write(head + "\n")
    for k, v in (refs or {}).items():
        os.makedirs(os.path.dirname(os.path.join(g, k)), exist_ok=True)
        with open(os.path.join(g, k), "w") as f:
            f.write(v + "\n")
    if gitfile:
        with open(os.path.join(td, ".git"), "w") as f:
            f.write("gitdir: " + g + "\n")
    os.makedirs(os.path.join(td, "aib"))
    return os.path.join(td, "aib")


def cell_a15(t, M):
    td = tempfile.mkdtemp(prefix="aib-anch-a15-")
    try:
        h = "a" * 40
        secret = os.path.join(td, "root-secret")
        with open(secret, "w") as f:
            f.write("TOPSECRET-" + "b" * 40 + "\n")
        t.assertEqual(M.git_head(_repo(os.path.join(td, "r1"), "ref: refs/heads/main", {"refs/heads/main": h})),
                      h + " (refs/heads/main)")
        t.assertEqual(M.git_head(_repo(os.path.join(td, "r2"), h)), h)                        # detached
        t.assertEqual(M.git_head(_repo(os.path.join(td, "r3"), "ref: refs/heads/w", {"refs/heads/w": h}, gitfile=True)),
                      h + " (refs/heads/w)")
        rel = os.path.relpath(secret, os.path.join(td, "r4", ".git"))
        for i, head in enumerate(("ref: " + rel, "ref: refs/../../" + rel, "ref: " + secret, "TOPSECRET")):
            got = M.git_head(_repo(os.path.join(td, "t%d" % i), head))
            t.assertEqual(got, "unknown", head)
            t.assertNotIn("TOPSECRET", got)
        got = M.git_head(_repo(os.path.join(td, "t9"), "ref: refs/heads/x", {"refs/heads/x": "TOPSECRET-not-hex"}))
        t.assertEqual(got, "unknown")
        hexfile = os.path.join(td, "root-hex")          # 穿越到的檔剛好是 40 位十六進位 ⇒ 也不讀(ref 檢查本身要擋)
        with open(hexfile, "w") as f:
            f.write("c" * 40 + "\n")
        for i, ref in enumerate((os.path.relpath(hexfile, os.path.join(td, "h0", ".git")), "refs/../../root-hex")):
            t.assertEqual(M.git_head(_repo(os.path.join(td, "h%d" % i), "ref: " + ref)), "unknown", ref)
        lk = _repo(os.path.join(td, "lk"), "ref: refs/heads/x")        # 0.1.65 Low:refs 檔是捷徑 ⇒ 不跟過去讀
        os.makedirs(os.path.join(td, "lk", ".git", "refs", "heads"))
        os.symlink(hexfile, os.path.join(td, "lk", ".git", "refs", "heads", "x"))
        t.assertEqual(M.git_head(lk), "unknown")
        hl = _repo(os.path.join(td, "hl"), "x")                           # HEAD 本身是捷徑
        os.unlink(os.path.join(td, "hl", ".git", "HEAD"))
        os.symlink(hexfile, os.path.join(td, "hl", ".git", "HEAD"))
        t.assertEqual(M.git_head(hl), "unknown")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def cell_a10(t, G):
    with mock.patch.object(G, "load_all", lambda env: [{"nonce": "n"}]):
        ctx = G.Ctx(env=dict(os.environ), platform="darwin", anchored=lambda platform=None: (None, "not_installed"))
        t.assertEqual(G.doctor_line(ctx), ("note", "helper_not_installed"))
        ctx = G.Ctx(env=dict(os.environ), platform="darwin", anchored=lambda platform=None: (None, "outdated"))
        t.assertEqual(G.doctor_line(ctx), ("note", "helper_outdated"))


def cell_a11(t, G):
    out = io.StringIO()
    ctx = G.Ctx(env=dict(os.environ), platform="darwin", anchored=lambda platform=None: (None, "not_installed"),
                run=lambda *a, **k: t.fail("status 不該跑任何指令"))
    t.assertEqual(G.main(["--helper", "status"], out=out, ctx=ctx), 0)
    d = json.loads(out.getvalue())
    t.assertEqual((d["ok"], d["why"], d["path"]), (False, "helper_not_installed", AH.EXE))
    t.assertTrue(d["install"].startswith("sudo /usr/bin/python3 -I "))
    t.assertTrue(d["install"].endswith("install_anchored_helper.py"))


class Cells(unittest.TestCase):
    def test_a1(self): cell_a1(self, AH)
    def test_a2(self): cell_a2(self, AH)
    def test_a3(self): cell_a3(self, AH)
    def test_a4(self): cell_a4(self, AH)
    def test_a5(self): cell_a5(self, AH)
    def test_a6(self): cell_a6(self, AH)
    def test_a7(self): cell_a7(self, AH)
    def test_a8(self): cell_a8(self, GR)
    def test_a9(self): cell_a9(self, INST)
    def test_a10(self): cell_a10(self, GR)
    def test_a11(self): cell_a11(self, GR)
    def test_a9b(self): cell_a9b(self, INST)
    def test_a15(self): cell_a15(self, INST)
    def test_a12(self): cell_a12(self, AH)
    def test_a13(self): cell_a13(self, AH)
    def test_a14(self): cell_a14(self, SV)

    def test_doctor_i18n(self):
        for loc in ("zh-TW", "en", "ja", "ko"):
            with open(ROOT / "i18n" / ("%s.json" % loc), encoding="utf-8") as f:
                d = json.load(f)
            for k in ("helper_missing", "helper_remedy"):
                t = d["aib.doctor.check.grants." + k]
                self.assertIn("{why}" if k == "helper_missing" else "{cmd}", t, (loc, k))


KNIVES_AH = [
    ("KA1", "    if st.st_uid != 0:\n        return \"not_root_owned\"\n", "", cell_a2),
    ("KA2", "    if st.st_mode & 0o022:\n        return \"writable\"\n", "", cell_a3),
    ("KA3", "    if _stat.S_ISLNK(st.st_mode):\n        return \"symlink\"\n", "", cell_a4),
    ("KA4", "        if d == \"/\":\n            break\n        d = os.path.dirname(d)\n", "        break\n", cell_a5),
    ("KA5", "    if got != want:\n        return None, \"outdated\"\n", "", cell_a6),
    ("KA9", "    if not hardened(EXE, run):\n        return None, \"not_hardened\"\n", "", cell_a12),
    ("KA11", "        if not _stat.S_ISREG(lstat(p).st_mode):\n            return None, \"not_file\"\n", "", cell_a13),
]
KNIVES_SV = [
    ("KA13", ', timeout=120, env={"PATH": "/usr/bin:/bin"})\n', ", timeout=120)\n", cell_a14),
]
KNIVES_GR = [
    ("KA6", "        exe, why = (self._anchored or anchored_helper.check)(platform=self.platform)\n",
     "        exe, why = se_vault.build_helper(self.env, run=self.run, platform=self.platform)\n", cell_a8),
]
KNIVES_INST = [
    ("KA7", "    bad = _parents_locked(anchor_dir, lstat)\n", "    bad = None\n", cell_a9),
    ("KA8", "    if euid() != 0:\n", "    if False:\n", cell_a9),
    ("KA10", '"-s", "-", "-f", "-o", "runtime", exe_tmp]', '"-s", "-", "-f", exe_tmp]', cell_a9),
    ("KA12", '["-o", exe_tmp, copy]', '["-o", exe_tmp, src]', cell_a9),
    ("KA14", '                if not _REF.match(ref) or ".." in ref.split("/"):\n                    return "unknown"\n', '', cell_a15),
    ("KA15", '                        return v + " (" + ref + ")" if _HEX40.match(v) else "unknown"\n',
     '                        return v[:40] + " (" + ref + ")"\n', cell_a15),
    ("KA16", '        digest = _sha256(copy)', '        digest = _sha256(src)', cell_a9b),
    ("KA17", '    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)\n', '    fd = os.open(path, os.O_RDONLY)\n', cell_a15),
]


def _load_copy(name, src_file, anchor, repl):
    src = src_file.read_text(encoding="utf-8")
    assert src.count(anchor) == 1, "%s 錨不是恰好一次" % name
    td = tempfile.mkdtemp(prefix="aib-anch-knife-")
    p = os.path.join(td, src_file.name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(src.replace(anchor, repl))
    spec = importlib.util.spec_from_file_location("aib_anch_knife_" + name, p)
    M = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = M
    spec.loader.exec_module(M)
    return M, td


class Knives(unittest.TestCase):
    def _run(self, knives, src_file, patch=lambda M: None):
        for name, anchor, repl, cell in knives:
            with self.subTest(knife=name):
                M, td = _load_copy(name, src_file, anchor, repl)
                try:
                    patch(M)
                    with self.assertRaises(AssertionError, msg="%s 落刀後格子仍綠" % name):
                        cell(self, M)
                finally:
                    sys.modules.pop("aib_anch_knife_" + name, None)
                    shutil.rmtree(td, ignore_errors=True)

    def test_knives_anchored(self):
        def patch(M):
            M.EXE, M.STAMP, M.ANCHOR_DIR = AH.EXE, AH.STAMP, AH.ANCHOR_DIR
        self._run(KNIVES_AH, SRC / "anchored_helper.py", patch)

    def test_knives_grants(self):
        def patch(M):
            M.window_reason = GR.window_reason          # 視窗字要從登記的那支組(複本不是登記檔)
        self._run(KNIVES_GR, SRC / "approve_grants.py", patch)

    def test_knives_se_vault(self):
        self._run(KNIVES_SV, SRC / "se_vault.py")

    def test_knives_installer(self):
        self._run(KNIVES_INST, TOOLS / "install_anchored_helper.py")


if __name__ == "__main__":
    unittest.main()
