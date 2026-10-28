#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""grokbot webhook 綁定視窗的「複製給 bot 的話」按鈕(10-28 Andy 准)。

C1 那段話(四語):含 agent id、收信指示(aib-grokbot-mail pull --agent <id>)、請回「Webhook URL」「Webhook 金鑰」兩個連結、
   叫它不要在聊天裡貼金鑰;沒有 !!缺鍵!!
C2 connect(跳窗路徑,假 osascript / 假 pbcopy / 假 open):跳窗前**不再**自動 pbcopy;那段話與按鈕字交給 JXA(argv 最後一格 JSON);
   視窗說明是新步驟;綁定照舊寫 0600 設定檔、輸出不含金鑰
C3 --stdin 不變:不跳窗、不碰剪貼簿、照舊寫檔
C4 按取消 ⇒ rc 2「你按了取消」,不寫檔
C5 JXA 可編譯(osacompile,不執行);macOS 上另用無頭治具按一次按鈕(只寫具名剪貼簿,不碰使用者剪貼簿、不彈窗):
   字改「已複製 ✓」、內容 = 那段話
"""
import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
TOOL = COCKPIT / "grokbot_threads.py"
sys.path.insert(0, str(COCKPIT))
import grokbot_threads as gt  # noqa: E402

AGENT = "42f65f97-8e7c-46fe-a966-98421d79ba57"
URL = "https://api2.cursor.sh/automations/webhook/0123abcd-0123-4567-89ab-0123456789ab"
KEY = "sk_test_SECRETKEY_42f65_do_not_print"


def _roster(app_dir: Path):
    pers = app_dir / gt.PERSISTENCE_NAME
    pers.mkdir(parents=True)
    name = base64.b32encode(b"x.roster.last-roster").decode().rstrip("=").lower() + ".blob"
    (pers / name).write_text(json.dumps({"schemaVersion": 4, "value": {"rows": [
        {"id": AGENT, "name": "AIB 合版", "updatedAt": 1}]}}), encoding="utf-8")


def _fake(path: Path, body: str):
    path.write_text("#!%s\n%s" % (sys.executable, body), encoding="utf-8")
    path.chmod(0o755)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-gb-copy-"))
        self.app = self.td / "app"
        _roster(self.app)
        self.state = self.td / "state"
        self.log = self.td / "calls.jsonl"
        bin_ = self.td / "bin"
        bin_.mkdir()
        rec = ("import json,sys,os\nopen(%r,'a').write(json.dumps({'tool':os.path.basename(sys.argv[0]),"
               "'argv':sys.argv[1:],'stdin':(sys.stdin.read() if not sys.stdin.isatty() else '')})+'\\n')\n" % str(self.log))
        _fake(bin_ / "pbcopy", rec)
        _fake(bin_ / "open", rec)
        _fake(bin_ / "osascript", rec + "mode=os.environ.get('FAKE_OSA','ok')\n"
              "if mode=='cancel':\n    sys.stderr.write('execution error: User canceled. (-128)');sys.exit(1)\n"
              "print(%r)\n" % (URL + "\n" + KEY))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("EZ2_", "AIB_"))}
        self.env.update({"AIB_GROKBOT_DIR": str(self.app), "EZ2_AIB_STATE_ROOT": str(self.state),
                         "AIB_GROKBOT_PBCOPY": str(bin_ / "pbcopy"), "AIB_GROKBOT_OPEN": str(bin_ / "open"),
                         "AIB_GROKBOT_OSASCRIPT": str(bin_ / "osascript"), "EZ2_AIB_LOCALE": "zh-TW",
                         "PYTHONDONTWRITEBYTECODE": "1",
                         # connect 綁好後會補建收信入口、問要不要同步:一律在暫存 HOME 裡,不碰真的 ~/.local/bin
                         "HOME": str(self.td / "home"), "AIB_PATH_BIN": str(self.td / "home" / "lbin")})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def run_tool(self, *args, stdin=None, **extra):
        env = dict(self.env, **extra)
        return subprocess.run([sys.executable, str(TOOL)] + list(args), env=env, input=stdin or "",
                              capture_output=True, text=True, timeout=60)


class C1Ask(unittest.TestCase):
    def test_ask_all_locales(self):
        markers = {"zh-TW": ("Webhook URL", "Webhook 金鑰", "不要在聊天裡貼出金鑰"),
                   "en": ("Webhook URL", "Webhook key", "Do not paste the key"),
                   "ja": ("Webhook URL", "Webhook 鍵", "鍵そのものはチャットに貼らない"),
                   "ko": ("Webhook URL", "Webhook 키", "키 자체는 채팅에 붙여넣지")}
        old = os.environ.get("EZ2_AIB_LOCALE")
        try:
            for loc, words in markers.items():
                os.environ["EZ2_AIB_LOCALE"] = loc
                ask = gt.routine_request(AGENT)
                self.assertNotIn("!!", ask, loc)
                self.assertIn("aib-grokbot-mail pull --agent %s" % AGENT, ask, loc)
                self.assertIn("agent id", ask, loc)
                self.assertIn("「AIB 收信」", ask, loc)
                self.assertIn("local-exec", ask, loc)
                for w in words:
                    self.assertIn(w, ask, loc)
                t = gt.dialog_texts("x")
                self.assertTrue(all(v and "!!" not in v for v in t.values()), loc)
        finally:
            if old is None:
                os.environ.pop("EZ2_AIB_LOCALE", None)
            else:
                os.environ["EZ2_AIB_LOCALE"] = old

    def test_fallback_when_dict_broken(self):
        # 缺鍵(字典沒這條)⇒ 用呼叫端給的 zh-TW 原句,不回 !!key!!
        self.assertEqual(gt._t("aib.grokconnect.no_such_key_42f65", "原句{agent}", agent="a1"), "原句a1")
        self.assertIn(AGENT, gt.BOT_ASK_FALLBACK.format(agent=AGENT))


class C2Dialog(Base):
    def test_no_pbcopy_before_dialog_and_ask_passed_to_button(self):
        r = self.run_tool("connect", "--agent", AGENT, "--dir", str(self.app))
        self.assertEqual(r.returncode, 0, r.stderr)
        tools = [c["tool"] for c in self.calls()]
        self.assertNotIn("pbcopy", tools, "跳窗前不該再自動放剪貼簿")
        self.assertIn("osascript", tools)
        osa = [c for c in self.calls() if c["tool"] == "osascript"][0]["argv"]
        self.assertEqual(osa[:2], ["-l", "JavaScript"])
        cfg = json.loads(osa[-1])
        old = os.environ.get("EZ2_AIB_LOCALE")
        os.environ["EZ2_AIB_LOCALE"] = "zh-TW"
        try:
            self.assertEqual(cfg["copy_text"], gt.routine_request(AGENT))
        finally:
            if old is None:
                os.environ.pop("EZ2_AIB_LOCALE", None)
            else:
                os.environ["EZ2_AIB_LOCALE"] = old
        self.assertEqual(cfg["copy"], "複製給 bot 的話")
        self.assertEqual(cfg["copied"], "已複製 ✓")
        info = osa[-2]
        for w in ("複製給 bot 的話", "貼到跟它的聊天", "兩個連結", "貼回下面這兩格"):
            self.assertIn(w, info)
        self.assertNotIn("剪貼簿已經放好", info)
        dest = gt.agent_wake_path(AGENT, self.state)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        self.assertIn(KEY, dest.read_text(encoding="utf-8"))
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertNotIn(KEY, json.dumps(osa, ensure_ascii=False))

    def test_cancel(self):
        r = self.run_tool("connect", "--agent", AGENT, "--dir", str(self.app), FAKE_OSA="cancel")
        self.assertEqual(r.returncode, 2)
        self.assertIn("你按了取消", r.stderr)
        self.assertFalse(gt.agent_wake_path(AGENT, self.state).exists())
        self.assertNotIn("pbcopy", [c["tool"] for c in self.calls()])

    def test_english_dialog(self):
        r = self.run_tool("connect", "--agent", AGENT, "--dir", str(self.app), EZ2_AIB_LOCALE="en")
        self.assertEqual(r.returncode, 0, r.stderr)
        osa = [c for c in self.calls() if c["tool"] == "osascript"][0]["argv"]
        cfg = json.loads(osa[-1])
        self.assertEqual(cfg["copy"], "Copy message for the bot")
        self.assertIn("Please connect the doorbell", cfg["copy_text"])
        self.assertIn('bind Grok Bot "AIB 合版"', osa[-3])


class C3Stdin(Base):
    def test_stdin_unchanged(self):
        r = self.run_tool("connect", "--agent", AGENT, "--dir", str(self.app), "--stdin", stdin=URL + "\n" + KEY + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [], "--stdin 不跳窗、不碰剪貼簿、不切 app")
        self.assertIn("已綁定", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(gt.agent_wake_path(AGENT, self.state).stat().st_mode), 0o600)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("osacompile") and shutil.which("osascript"), "macOS 才有 JXA")
class C5Jxa(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-gb-jxa-"))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_compiles(self):
        src = self.td / "d.js"
        src.write_text(gt.TWO_FIELD_JXA, encoding="utf-8")
        r = subprocess.run(["osacompile", "-l", "JavaScript", "-o", str(self.td / "d.scpt"), str(src)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_button_copies_without_closing(self):
        # 無頭治具:只 build、不 runModal(不彈窗);按鈕寫進具名剪貼簿(不碰使用者的剪貼簿)
        board = "aib-test-bind-copy-%d" % os.getpid()
        src = self.td / "h.js"
        src.write_text(gt.TWO_FIELD_JXA + """
function run(argv) {
  var b = build(argv);
  b.btn.performClick(null);
  var pb = $.NSPasteboard.pasteboardWithName(JSON.parse(argv[2]).pasteboard);
  var got = pb.stringForType($.NSPasteboardTypeString).js;
  pb.releaseGlobally;
  return b.btn.title.js + '\\u0001' + got + '\\u0001' + (b.alert.window.isVisible ? 'shown' : 'hidden');
}
""", encoding="utf-8")
        ask = gt.routine_request(AGENT)
        cfg = dict(gt.dialog_texts("x"), copy_text=ask, pasteboard=board)
        cfg.pop("title"), cfg.pop("info")
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", str(src), "T", "I", json.dumps(cfg, ensure_ascii=False)],
                               capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.skipTest("osascript 沒回(沒有視窗工作階段)")
        if r.returncode != 0 and ("connection" in r.stderr.lower() or "window server" in r.stderr.lower()):
            self.skipTest("沒有視窗工作階段:" + r.stderr[:120])
        self.assertEqual(r.returncode, 0, r.stderr)
        title, got, shown = r.stdout.rstrip("\n").split("\x01")
        self.assertEqual(title, cfg["copied"])
        self.assertEqual(got, ask)
        self.assertEqual(shown, "hidden")


if __name__ == "__main__":
    unittest.main()
