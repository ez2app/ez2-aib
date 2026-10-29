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
刀(複本落刀,錨要恰好一次):KA1 拿掉擁有者檢查 ⇒ A2 紅;KA2 拿掉可寫檢查 ⇒ A3 紅;KA3 拿掉捷徑檢查 ⇒ A4 紅;
  KA4 只查 binary 不查上層 ⇒ A5 紅;KA5 拿掉 stamp 比對 ⇒ A6 紅;KA6 helper_exe 退回狀態根 build_helper ⇒ A8 紅;
  KA7 安裝程式拿掉上層檢查 ⇒ A9 紅;KA8 安裝程式拿掉 euid 檢查 ⇒ A9 紅。
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


def chk(M, lstat, stamp=GOOD, platform="darwin"):
    return M.check(lstat=lstat, read=lambda p: stamp + "\n", src_digest=lambda: GOOD, platform=platform)


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
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, argv, **k):
        self.calls.append(argv)
        if "swiftc" in argv:
            with open(argv[argv.index("-o") + 1], "wb") as f:
                f.write(b"\xcf\xfa\xed\xfe fake mach-o")
        return type("P", (), {"returncode": 0 if self.ok else 1, "stdout": "", "stderr": ""})()


def _inst(M, td, euid=0, parents_ok=True):
    os.makedirs(td)
    td = os.path.realpath(td)            # /var → /private/var 是捷徑;安裝程式會擋捷徑
    src = os.path.join(td, "aib-sehelper.swift")
    with open(src, "w") as f:
        f.write("// swift\n")
    anchor = os.path.join(td, "root", "usr", "local", "libexec", "ez2-aib")
    owned = []
    real = os.lstat

    def lstat(p):
        r = real(p)                       # 暫存目錄是本人擁有 ⇒ 換成 root 擁有、不可寫(parents_ok=False ⇒ 第一層上層可寫)
        mode = (r.st_mode & ~0o022) | (0o002 if not parents_ok and p == os.path.dirname(os.path.dirname(anchor)) else 0)
        return os.stat_result((mode,) + tuple(r)[1:4] + (0,) + tuple(r)[5:])
    os.makedirs(os.path.dirname(os.path.dirname(anchor)))
    out = io.StringIO()
    rc = M.install(src=src, anchor_dir=anchor, run=_Run(), euid=lambda: euid,
                   chown=lambda p, u, g: owned.append((os.path.basename(p), u, g)), lstat=lstat, out=out, platform="darwin")
    return rc, anchor, owned, src, out.getvalue()


def cell_a9(t, M):
    td = tempfile.mkdtemp(prefix="aib-anch-a9-")
    try:
        rc, anchor, owned, src, _ = _inst(M, os.path.join(td, "ok"))
        t.assertEqual(rc, 0)
        exe, stamp = os.path.join(anchor, "aib-sehelper"), os.path.join(anchor, "aib-sehelper.sha256")
        t.assertEqual(stat.S_IMODE(os.stat(exe).st_mode), 0o755)
        t.assertEqual(stat.S_IMODE(os.stat(stamp).st_mode), 0o644)
        with open(src, "rb") as f, open(stamp) as g:
            t.assertEqual(g.read().strip(), hashlib.sha256(f.read()).hexdigest())
        t.assertEqual(sorted(owned), sorted([("ez2-aib", 0, 0), ("aib-sehelper.new", 0, 0), ("aib-sehelper.sha256.new", 0, 0)]))
        t.assertFalse(os.path.exists(exe + ".new"))
        rc, anchor, owned, _, msg = _inst(M, os.path.join(td, "user"), euid=501)
        t.assertEqual((rc, owned, os.path.exists(anchor)), (2, [], False))
        t.assertIn("sudo", msg)
        rc, anchor, owned, _, _ = _inst(M, os.path.join(td, "brew"), parents_ok=False)
        t.assertEqual((rc, owned, os.path.exists(anchor)), (3, [], False))
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
]
KNIVES_GR = [
    ("KA6", "        exe, why = (self._anchored or anchored_helper.check)(platform=self.platform)\n",
     "        exe, why = se_vault.build_helper(self.env, run=self.run, platform=self.platform)\n", cell_a8),
]
KNIVES_INST = [
    ("KA7", "    bad = _parents_locked(anchor_dir, lstat)\n", "    bad = None\n", cell_a9),
    ("KA8", "    if euid() != 0:\n", "    if False:\n", cell_a9),
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

    def test_knives_installer(self):
        self._run(KNIVES_INST, TOOLS / "install_anchored_helper.py")


if __name__ == "__main__":
    unittest.main()
