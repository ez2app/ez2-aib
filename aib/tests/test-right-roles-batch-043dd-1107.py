#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""右欄一次問完所有窗的 @aib_role(省熱第 3 項小,11-07 @043dd)。

改前:每輪 list-windows 1 次 + 每窗 show-options 1 次(6 窗 = 7 個 tmux 行程)。
改後:list-windows 1 次 + 一次串接的 tmux(display-message 分隔行 ; show-options …)= 2 個。
不改判讀:仍用 show-options -w(只看窗那層);值有換行仍整筆拒;批次對不上就逐窗照舊問。

真 tmux 一律走沙盒 -S socket(本測試暫存夾內),每次呼叫前驗夾還在、socket 在夾內、不是預設 socket;
被測腳本的 `tmux -L <名>` 由假 tmux 轉成 -S 沙盒 socket,轉之前同樣驗(夾不在就 exit 99,絕不退回預設)。

R1 等價:dev / 沒設 / 多行 / 含 | / 空字串 / qa.1 ⇒ 批次結果 = 逐窗 show-options(多行的標 !)
R1b 尾端換行(三種語系):dev+1 換行 / dev+2 換行 / 只有換行 / dev 換行 qa 換行 / 換行換行 qa / qa.1+換行 ⇒ 跟舊版 $(…) 同語義
R2 偽造分隔行:role 內塞「<像分隔行> @1」⇒ 那窗 !、@1 拿到自己真正的值
R3 窗已不在(@77):照樣有一筆、值空;其他窗正確
R4 tmux 失敗 ⇒ _HUB_ROLES 空(呼叫端逐窗照舊問)
R5 分隔行順序不對 ⇒ 整批作廢
R6 輸出被截斷(少了後面的窗)⇒ 整批作廢
R7 整合:單輪 6 窗 tmux 呼叫 7 → 2、show-options 只出現在 1 次呼叫裡;畫面跟改前逐字相同(去色碼)
R8 整合:髒 role 那窗照舊被拒(stderr「role rejected」),跟改前同一窗
G1 守門:沙盒夾不見 ⇒ 假 tmux exit 99,不碰真 tmux
刀 KA 中間換行不標 !(R2 紅)、KE 不去尾端換行(R1b 紅)、KB 不驗順序(R5 紅)、KC 不驗筆數(R6 紅)、KD 不接批次(R7 紅)
R7 的畫面比對去掉色碼(SGR)後才比;讀不到 origin/main 時只驗呼叫數
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS = Path(__file__).name
SCRIPT = ROOT / "src" / "cockpit" / "pane-right-ai.sh"
OLD_REF = "6e07e50d31590f33c003f1f78333849b2f31f129:aib/src/cockpit/pane-right-ai.sh"   # #482 之前(右欄每輪 tmux 1+N)


def read_old_blob(root, ref):
    """讀 git show <ref>。回 (內容, None) 或 (None, 略過理由);其他情況丟 AssertionError 帶 stderr。
    只有兩種能略過:這棵樹確實不是 git 樹(刀測複本)、或確實是淺層 clone 且缺那個 commit。
    git 逾時 / 指令出錯 / 判斷不出來 ⇒ 一律紅(bb7dc #502 讀碼兩輪)。"""
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(root)] + list(a), capture_output=True, text=True, timeout=30,
                                  env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))   # 訊息固定英文才比得到
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            raise AssertionError("git %s 逾時 30 秒" % " ".join(a))
    r = git("show", ref)
    if r is None:
        return None, "沒有 git 執行檔"
    if r.returncode == 0:
        return r.stdout, None
    sha = ref.split(":")[0]
    ins = git("rev-parse", "--is-inside-work-tree")
    if ins.returncode != 0:
        if ins.returncode == 128 and "not a git repository" in ins.stderr:
            return None, "不在 git 樹(刀測複本)"
        raise AssertionError("判斷不出是不是 git 樹(rc=%s):%s;show:%s" % (ins.returncode, ins.stderr.strip(), r.stderr.strip()))
    if ins.stdout.strip() != "true":
        raise AssertionError("不在工作樹內(%r):%s" % (ins.stdout.strip(), r.stderr.strip()))
    sh = git("rev-parse", "--is-shallow-repository")
    if sh.returncode != 0 or sh.stdout.strip() not in ("true", "false"):
        raise AssertionError("判斷不出是不是淺層(rc=%s):%s;show:%s" % (sh.returncode, sh.stderr.strip(), r.stderr.strip()))
    have = git("cat-file", "-e", sha + "^{commit}")
    # 「確實缺件」只認 rc 128 + Not a valid object name;權限 / 物件庫 I/O 等其他錯誤一律紅(bb7dc #502 第三輪)
    missing = have.returncode == 128 and have.stderr.strip().startswith("fatal: Not a valid object name")
    if sh.stdout.strip() == "true" and missing:
        return None, "淺層 clone 缺 %s" % sha[:8]
    raise AssertionError("讀不到舊版 %s(shallow=%s cat-file rc=%s:%s):%s" % (
        ref, sh.stdout.strip(), have.returncode, have.stderr.strip(), r.stderr.strip()))
TMUX = shutil.which("tmux")
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
SGR = re.compile(r"\x1b\[[0-9;]*m")


class SandboxRefused(AssertionError):
    pass


class Sandbox:
    def __init__(self, root):
        self.root = Path(root)
        self.sock = self.root / "s"

    def check(self):
        root = os.path.realpath(str(self.root))
        sock = os.path.realpath(str(self.sock))
        if not self.root.is_dir():
            raise SandboxRefused("沙盒夾不在了:%s" % self.root)
        if not sock.startswith(root + os.sep):
            raise SandboxRefused("socket 不在沙盒夾內:%s" % sock)
        if os.path.basename(sock) == "default" or "/tmux-%d/" % os.getuid() in sock:
            raise SandboxRefused("socket 像是預設位置:%s" % sock)

    def env(self):
        return {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR", "TMUX_PANE")}

    def tm(self, *a, env=None):
        self.check()
        return subprocess.run([TMUX, "-S", str(self.sock)] + list(a), env=env or self.env(),
                              capture_output=True, text=True, timeout=10)

    def cleanup(self):
        try:
            self.tm("kill-server")
        except SandboxRefused:
            pass


def shim_text(sbx, log, real, mode="real"):
    """假 tmux:記每次呼叫;-L <名> 拿掉換成 -S 沙盒 socket。mode=fail/reverse/truncate 給 R4–R6。"""
    return r'''#!/bin/bash
printf '%%s\n' "$*" >> '%(log)s'
D='%(dir)s'; S='%(sock)s'
[ -d "$D" ] || exit 99
case "$S" in "$D"/*) ;; *) exit 99 ;; esac
case "${S##*/}" in default) exit 99 ;; esac
case "$S" in */tmux-[0-9]*/*) exit 99 ;; esac
a=(); while [ $# -gt 0 ]; do if [ "$1" = "-L" ]; then shift 2; continue; fi; a+=("$1"); shift; done
MODE='%(mode)s'
if [ "$MODE" = fail ]; then exit 1; fi
if [ "$MODE" != real ]; then
  # 找出分隔行(display-message -p 的參數),照 mode 亂排 / 截斷,每窗值給 dev
  m=(); i=0; while [ $i -lt ${#a[@]} ]; do [ "${a[$i]}" = "-p" ] && m+=("${a[$((i+1))]}"); i=$((i+1)); done
  if [ "$MODE" = reverse ]; then for ((j=${#m[@]}-1; j>=0; j--)); do printf '%%s\ndev\n' "${m[$j]}"; done; fi
  if [ "$MODE" = truncate ]; then printf '%%s\ndev\n' "${m[0]}"; fi
  exit 0
fi
exec '%(real)s' -S "$S" "${a[@]}"
''' % {"log": log, "dir": sbx.root, "sock": sbx.sock, "real": real, "mode": mode}


@unittest.skipUnless(TMUX and BASH, "沒有 tmux / bash")
class Base(unittest.TestCase):
    locale = "C"

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="rb"))
        self.addCleanup(shutil.rmtree, str(self.d), True)
        self.sbx = Sandbox(self.d / "x")
        self.sbx.root.mkdir()
        self.addCleanup(self.sbx.cleanup)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.log = self.d / "calls.log"
        self.set_shim("real")
        r = self.sbx.tm("new-session", "-d", "-s", "aihub", "sleep 300")
        self.assertEqual(r.returncode, 0, r.stderr)
        for _ in range(5):
            self.sbx.tm("new-window", "-d", "-t", "aihub", "sleep 300")
        self.ids = self.sbx.tm("list-windows", "-t", "aihub", "-F", "#{window_id}").stdout.split()
        self.assertEqual(len(self.ids), 6)

    def set_shim(self, mode):
        p = self.bin / "tmux"
        p.write_text(shim_text(self.sbx, self.log, TMUX, mode), encoding="utf-8")
        p.chmod(0o755)

    def env(self, **kw):
        e = self.sbx.env()
        e.update(PATH="%s:/usr/bin:/bin" % self.bin, EZ2_AIHUB_SOCKET="aihub-zz", LC_ALL=self.locale)
        e.update(kw)
        return e

    def set_role(self, wid, val):
        r = self.sbx.tm("set-option", "-w", "-t", wid, "@aib_role", val)
        self.assertEqual(r.returncode, 0, r.stderr)

    def batch(self, ids_lines):
        # tmux 印值時依 client 語系處理控制字元(C:換行印成 _;UTF-8:真的換行)⇒ 對照組用同一個 env
        fn = subprocess.run(["sed", "-n", "/^_hub_roles_batch() {/,/^}/p", str(SCRIPT)],
                            capture_output=True, text=True).stdout
        self.assertIn("_hub_roles_batch", fn, "找不到 _hub_roles_batch")
        prog = fn + '\n_hub_roles_batch "$1"\nprintf "%s" "$_HUB_ROLES"\n'
        r = subprocess.run([BASH, "-c", prog, "x", ids_lines], env=self.env(), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = {}
        for line in r.stdout.split("\n"):
            if "|" in line:
                k, v = line.split("|", 1)
                out[k] = v
        return r.stdout, out

    def one_by_one(self, wid):
        return self.sbx.tm("show-options", "-wqv", "-t", wid, "@aib_role", env=self.env()).stdout.rstrip("\n")


class Batch(Base):
    def test_r1_equivalent(self):
        for loc in ("C", "C.UTF-8", "en_US.UTF-8"):
            with self.subTest(locale=loc):
                self.locale = loc
                self._r1()

    def _r1(self):
        vals = ["dev", None, "a\nb", "x|y", "", "qa.1"]
        for wid, v in zip(self.ids, vals):
            if v is not None:
                self.set_role(wid, v)
        _, got = self.batch("\n".join("%s|%%0|0" % w for w in self.ids))
        self.assertEqual(sorted(got), sorted(self.ids))
        for wid in self.ids:
            want = self.one_by_one(wid)
            if "\n" in want:
                want = "!"
            self.assertEqual(got[wid], want, wid)

    def test_r1b_trailing_newlines(self):
        # #482 審:舊版 $(…) 會去掉所有尾端換行(dev+換行 ⇒ dev);批次要同語義,去完中間有換行才標 !
        vals = ["dev\n", "dev\n\n", "\n", "dev\nqa\n", "\n\nqa", "qa.1\n"]
        for wid, v in zip(self.ids, vals):
            self.set_role(wid, v)
        for loc in ("C", "C.UTF-8", "en_US.UTF-8"):
            with self.subTest(locale=loc):
                self.locale = loc
                _, got = self.batch("\n".join(self.ids))
                for wid, v in zip(self.ids, vals):
                    want = self.one_by_one(wid)
                    if "\n" in want:
                        want = "!"
                    self.assertEqual(got.get(wid), want, "%s 值 %r" % (wid, v))

    def test_r2_forged_separator(self):
        self.set_role(self.ids[0], "dev\nr123x45 %s\nqa" % self.ids[1])
        self.set_role(self.ids[1], "sys.menu")
        # 真換行才是偽造面(C 語系 tmux 自己就印成 _);挑一個這台有的 UTF-8 語系
        for loc in ("C.UTF-8", "en_US.UTF-8", "C.utf8"):
            self.locale = loc
            if "\n" in self.one_by_one(self.ids[0]):
                break
        else:
            self.skipTest("這台沒有能讓 tmux 印出真換行的 UTF-8 語系")
        _, got = self.batch("\n".join(self.ids))
        self.assertEqual(got[self.ids[0]], "!")
        self.assertEqual(got[self.ids[1]], "sys.menu")

    def test_r3_gone_window(self):
        self.set_role(self.ids[0], "dev")
        self.set_role(self.ids[2], "qa")
        ids = [self.ids[0], "@77", self.ids[2]]
        _, got = self.batch("\n".join(ids))
        self.assertEqual(got, {self.ids[0]: "dev", "@77": "", self.ids[2]: "qa"})

    def test_r4_tmux_fails(self):
        self.set_shim("fail")
        raw, got = self.batch("\n".join(self.ids))
        self.assertEqual((raw, got), ("", {}))

    def test_r5_out_of_order(self):
        self.set_shim("reverse")
        raw, _ = self.batch("\n".join(self.ids))
        self.assertEqual(raw, "")

    def test_r6_truncated(self):
        self.set_shim("truncate")
        raw, _ = self.batch("\n".join(self.ids))
        self.assertEqual(raw, "")

    def test_g1_guard_refuses_missing_dir(self):
        shutil.rmtree(str(self.sbx.root))
        r = subprocess.run([str(self.bin / "tmux"), "-L", "aihub-zz", "list-sessions"], env=self.env(),
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 99)
        with self.assertRaises(SandboxRefused):
            self.sbx.tm("list-sessions")


class Loop(Base):
    def run_once(self, script):
        st = self.d / "st"
        st.mkdir(exist_ok=True)
        self.log.write_text("", encoding="utf-8")
        e = self.env(EZ2_ONESHOT="1", EZ2_COCKPIT_STATE_DIR=str(st), EZ2_COCKPIT_DIR=str(SCRIPT.parent),
                     EZ2_AIB_HOME=str(self.d / "home"), EZ2_AIB_STATE_ROOT=str(self.d / "state"),
                     EZ2_PY3=sys.executable, HOME=str(self.d / "home"))
        r = subprocess.run([BASH, str(script)], env=e, capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        return r, calls

    def old_script(self):
        # 比對用的「舊版」釘在 #482 之前的固定 commit。以前讀 origin/main:#482 合進去後 origin/main 就是新版自己,
        # push 到 main 的 CI(checkout 會建 origin/main)每輪 2 != 7 紅;PR CI 淺層沒有 origin/main 才跳過看似綠。
        # 非 git 複本 / 淺層缺這個 commit ⇒ 這支 skipTest 寫明「舊版比對沒跑」(1ced7 #501:不准靜默 return 看似綠);
        # 其他讀不到一律紅。呼叫數另在 test_r7 / test_r8 驗，不靠舊版。
        old, why = read_old_blob(ROOT, OLD_REF)
        if old is None:
            self.skipTest("沒有 %s(%s),舊版畫面比對沒跑" % (OLD_REF.split(":")[0][:8], why))
        if "show-options -wqv" not in old:
            raise AssertionError("OLD_REF 的舊版格式不對")
        if old == SCRIPT.read_text(encoding="utf-8"):
            raise AssertionError("舊版比對對象跟現行腳本一樣:OLD_REF 釘錯了")
        p = self.d / "old-pane-right-ai.sh"
        p.write_text(old, encoding="utf-8")
        return p

    def test_r7_calls_and_same_screen(self):
        self.set_role(self.ids[0], "dev")
        self.set_role(self.ids[3], "sys.menu")
        r, calls = self.run_once(SCRIPT)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        hub = [c for c in calls if c.startswith("-L aihub-zz")]
        self.assertEqual(len(hub), 2, "\n".join(hub))
        self.assertEqual(sum(1 for c in hub if "show-options" in c), 1)

    def test_r7b_old_version_same_screen(self):
        self.set_role(self.ids[0], "dev")
        self.set_role(self.ids[3], "sys.menu")
        old = self.old_script()   # 抓不到 ⇒ skipTest 寫明
        r, _ = self.run_once(SCRIPT)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        r0, calls0 = self.run_once(old)
        self.assertEqual(len([c for c in calls0 if c.startswith("-L aihub-zz")]), 7)
        self.assertEqual(SGR.sub("", r.stdout), SGR.sub("", r0.stdout))

    def test_r8_dirty_role_still_rejected(self):
        self.set_role(self.ids[2], "dev\n@9|qa")
        r, _ = self.run_once(SCRIPT)
        rej = sorted(re.findall(r"window (@\d+) role rejected", r.stderr))
        self.assertEqual(rej, [self.ids[2]])

    def test_r8b_old_version_same_rejects(self):
        self.set_role(self.ids[2], "dev\n@9|qa")
        old = self.old_script()   # 抓不到 ⇒ skipTest 寫明
        r, _ = self.run_once(SCRIPT)
        rej = sorted(re.findall(r"window (@\d+) role rejected", r.stderr))
        r0, _ = self.run_once(old)
        self.assertEqual(sorted(re.findall(r"window (@\d+) role rejected", r0.stderr)), rej)


KNIVES = [
    ("KA", '    case "$val" in *"$nl"*) val="!" ;; esac\n', '',
     ["test_r2_forged_separator"]),
    ("KE", '    while [ "${val%"$nl"}" != "$val" ]; do val="${val%"$nl"}"; done\n', '',
     ["test_r1b_trailing_newlines"]),
    ("KB", '[ "$line" = "$nonce ${order[$i]:-}" ] || return 0', ':', ["test_r5_out_of_order"]),
    ("KC", '[ "$i" -eq "${#order[@]}" ] || return 0', ':', ["test_r6_truncated"]),
    ("KD", '      _hub_roles_batch "$_hub_ids"\n', '      :\n', ["test_r7_calls_and_same_screen"]),
]


@unittest.skipUnless(TMUX and BASH, "沒有 tmux / bash")
class Knife(unittest.TestCase):
    def test_knives_bite(self):
        if os.environ.get("AIB_KNIFE_INNER"):
            self.skipTest("刀複本內不再落刀")
        sys.path.insert(0, str(ROOT / "tests"))
        from aib_knife_copy import knife_copy
        sys.path.pop(0)
        for tag, needle, repl, targets in KNIVES:
            with knife_copy(ROOT, "src/cockpit/pane-right-ai.sh", needle, repl, tests=(THIS,)) as cp:
                for t in targets:
                    cls = "Loop" if t.startswith("test_r7") or t.startswith("test_r8") else "Batch"
                    r = subprocess.run([sys.executable, str(cp / "tests" / THIS), "%s.%s" % (cls, t)],
                                       cwd=str(cp), capture_output=True, text=True, timeout=300,
                                       env=dict(os.environ, AIB_KNIFE_INNER="1"))
                    self.assertNotEqual(r.returncode, 0, "%s 沒咬到 %s" % (tag, t))
                    self.assertIn("FAIL: %s" % t, r.stderr, "%s:%s 要是斷言紅,不是 ERROR\n%s" % (tag, t, r.stderr[-600:]))
                    self.assertNotIn("ERROR: ", r.stderr, "%s:%s\n%s" % (tag, t, r.stderr[-600:]))


if __name__ == "__main__":
    unittest.main()
