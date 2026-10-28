#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""開席選單舊席位列只剩四項(10-28 Andy:「下方的載入席位,只要顯示 席位/人格/專案名稱/最新開啟時間 這樣就好」)。

F1 綁好的 claude 席:tail = 「人格 · 專案 · 月-日 時:分」;不帶模型、「載入」、角色、大腦、交接、uid、fld_ 路徑。
F2 沒綁:人格、專案都是字典的「未綁」(不寫死)。
F3 讀不到不掛:cards.json 壞掉 / 人格卡不見 / 名字帶 `@` `|` ⇒ 「未綁」,collect 不拋例外。
F4 寬度:預算用彈窗內寬(終端 × 90% − 2;#399 R1),80 / 100 欄終端、四語,使用中字樣完整(10-28 Andy)、名字先縮補「…」。
F5 寬度算法與 open_menu_popup.fit 同一套(組合字元 0 欄)。
F6 活席檔與 retired 檔時間一樣 ⇒ 活席檔的專案名優先。
S2 選單殼量 client_width 換成彈窗內寬交給 cli_sessions(--popup-cols);量不到 ⇒ 0(保守值)。
S1 選單殼:four_col 列畫成「★ @碼 · 人格 · 專案 · 時間」,沒有〔模型〕與「載入」;four_col=false(橋接 bot)照舊格式。
刀:拿掉 four_col 分支 ⇒ F1/S1 紅(列裡又出現「大腦:」「〔」)。
治具全在 tmpdir(EZ2_AIB_STATE_ROOT / CLAUDE_CONFIG_DIR 都指暫存),不碰真 ~/.ez2-aib、~/.claude。
"""
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "src" / "cockpit"
MENU = COCKPIT / "cli-session-menu.sh"
UUID_A = "11111111-2222-3333-4444-5555555aeb40"
UUID_B = "11111111-2222-3333-4444-555555500b01"


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


class Fixture:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.home = t / "aib-home"
        self.cc = t / "claude"
        self.cwd = t / "work"
        self.cwd.mkdir()
        (self.home / "identity").mkdir(parents=True)
        self.seats = self.home / "instances" / "20261028-000000-test" / "seats"
        self.seats.mkdir(parents=True)
        self.proj = self.cc / "projects" / str(self.cwd).replace("/", "-")
        self.proj.mkdir(parents=True)

    def seat(self, n, uuid, **extra):
        rec = {"provider": "claude", "session_uuid": uuid, "cwd": str(self.cwd), "role": "root",
               "window_id": "@%d" % n, "model_requested": "claude-opus-5-5"}
        rec.update(extra)
        (self.seats / ("w%d.json" % n)).write_text(json.dumps(rec), encoding="utf-8")
        (self.proj / (uuid + ".jsonl")).write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")

    def bind(self, code, uid, name):
        (self.home / "identity" / "bindings.json").write_text(json.dumps(
            {"bindings": {code: {"persona_uid": uid, "route_key": "proj.x"}}}), encoding="utf-8")
        (self.home / "identity" / "cards.json").write_text(json.dumps(
            {"cards": {uid: {"display_name": name}}}), encoding="utf-8")

    def env(self):
        e = dict(os.environ)
        e.update({"EZ2_AIB_STATE_ROOT": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cc)})
        return e

    def collect(self, provider="claude"):
        code = ("import json,sys; sys.path.insert(0, %r); import cli_sessions as C; "
                "print(json.dumps(C.collect(%r, cwd=%r, aib_home=%r, procs=[], config_dir=__import__('pathlib').Path(%r)),"
                " ensure_ascii=False))") % (str(COCKPIT), provider, str(self.cwd), str(self.home), str(self.cc))
        p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60,
                           env=self.env(), stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise AssertionError("collect rc=%d\n%s" % (p.returncode, p.stderr[-2000:]))
        return json.loads(p.stdout)

    def t(self, key):
        p = subprocess.run([sys.executable, "-B", str(COCKPIT / "i18n_lib.py"), key], capture_output=True,
                           text=True, timeout=30, env=self.env())
        return p.stdout.strip()

    def cleanup(self):
        self._tmp.cleanup()


class FourCols(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.cleanup()

    def row(self, d, code):
        return next(r for r in d["rows"] if r["code"] == code)

    def test_f1_bound_row_four_items(self):
        self.f.seat(1, UUID_A, project_uid="prj_abcd1234", project_title="示範專案甲")
        self.f.bind("aeb40", "0ar525", "測試人格乙")
        r = self.row(self.f.collect(), "aeb40")
        self.assertTrue(r["four_col"])
        self.assertEqual(r["tail"], "測試人格乙 · 示範專案甲 · %s" % r["when"])
        for bad in ("大腦", "交接", "root", "claude-opus", "0ar525", "prj_", "fld_", "〔"):
            self.assertNotIn(bad, r["tail"])

    def test_f2_unbound_uses_dictionary(self):
        self.f.seat(1, UUID_B)
        unbound = self.f.t("aib.climenu.unbound")
        self.assertTrue(unbound and unbound != "aib.climenu.unbound")
        r = self.row(self.f.collect(), "00b01")
        self.assertEqual(r["tail"], "%s · %s · %s" % (unbound, unbound, r["when"]))

    def test_f3_unreadable_falls_back(self):
        unbound = self.f.t("aib.climenu.unbound")
        self.f.seat(1, UUID_A, project_uid="prj_abcd1234", project_title="冒充@f5fb8")
        self.f.bind("aeb40", "0ar525", "假人格|多一欄")
        self.assertEqual(self.row(self.f.collect(), "aeb40")["tail"].split(" · ")[:2], [unbound, unbound])
        (self.f.home / "identity" / "cards.json").write_text("{壞掉", encoding="utf-8")
        self.assertEqual(self.row(self.f.collect(), "aeb40")["tail"].split(" · ")[0], unbound)
        (self.f.home / "identity" / "cards.json").write_text(json.dumps({"cards": {}}), encoding="utf-8")
        self.assertEqual(self.row(self.f.collect(), "aeb40")["tail"].split(" · ")[0], unbound)

    def test_f4_width_uses_popup_inner_width(self):
        """#399 R1:彈窗開在 -w 90% 裡、fit 再截 cols−1 ⇒ 80 / 100 欄終端、四語,使用中字樣都要完整畫出來。"""
        sys.path.insert(0, str(COCKPIT))
        import cli_sessions as C
        import open_menu_popup as P
        long_p, long_j = "超長的人格名稱二樓辦公室後端工程師", "非常非常長的專案名稱一定會被截斷吧"
        for loc in ("zh-TW", "en", "ja", "ko"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            live = d["aib.climenu.live"]
            if loc == "zh-TW":
                self.assertEqual(live, "使用中(會分出新碼)")   # 10-28 Andy:使用中保留完整字樣
            for term in (80, 100):
                inner = term * C.POPUP_W_PCT // 100 - 2
                for lab in (live, ""):
                    tail = C.four_tail(long_p, long_j, "10-28 17:15", lab, inner)
                    line = " [h] ★ @aeb40 · " + tail
                    with self.subTest(loc=loc, term=term, live=bool(lab)):
                        self.assertEqual(P.fit(line, inner), line)        # 彈窗不再截(尾巴完整)
                        if lab:
                            self.assertTrue(tail.endswith(" · " + lab))
                        persona, project = tail.split(" · ")[:2]
                        self.assertTrue(persona.endswith("…") and project.endswith("…"))
        # 沒在用的列名字給長一點;量不到內寬 ⇒ 80 欄終端的值
        self.assertGreater(C._name_cols(""), C._name_cols("使用中(會分出新碼)"))
        self.assertEqual(C.POPUP_COLS_DEFAULT, 70)

    def test_f5_width_matches_popup_fit(self):
        """寬度算法與 open_menu_popup.fit 同一套:組合字元算 0 欄。"""
        sys.path.insert(0, str(COCKPIT))
        import cli_sessions as C
        self.assertEqual(C._width("e\u0301"), 1)
        self.assertEqual(C._width("★ @"), 3)
        self.assertEqual(C._width("使用中"), 6)

    def test_f6_live_seat_file_beats_retired_on_tie(self):
        """#399 R1:同一 uuid 同時有活席檔與 retired 檔、時間一樣 ⇒ 用活席檔的專案名(現在的)。"""
        self.f.seat(1, UUID_A, project_uid="prj_abcd1234", project_title="現在的專案")
        (self.f.seats / "retired").mkdir()
        rec = json.loads((self.f.seats / "w1.json").read_text(encoding="utf-8"))
        rec.update(project_title="退休前的舊專案")
        (self.f.seats / "retired" / "w1-old.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(self.row(self.f.collect(), "aeb40")["tail"].split(" · ")[1], "現在的專案")


FAKE_TMUX = r'''#!/bin/sh
case "$*" in
  *list-clients*) echo x ;;
  *client_width*) echo "${CLIENT_W:-}" ;;
  *display-popup*)
    for a; do last="$a"; done
    spec=$(printf '%s' "$last" | awk '{print $NF}')
    cp "$spec" "$CAPTURE"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); open(d["choice_file"],"w").write("__blank__")' "$spec" ;;
esac
exit 0
'''


class MenuShell(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def labels(self, rows):
        (self.t / "rows.json").write_text(json.dumps({"supported": True, "rows": rows}), encoding="utf-8")
        tm = self.t / "tmux"
        tm.write_text(FAKE_TMUX)
        tm.chmod(0o755)
        (self.t / "state").mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update({"AIB_TMUX_BIN": str(tm), "CAPTURE": str(self.t / "spec.json"),
                    "AIB_CLI_SESSIONS_JSON_FILE": str(self.t / "rows.json"), "AIB_CLI_SESSION_MENU_OUTER": "outer",
                    "EZ2_COCKPIT_STATE_DIR": str(self.t / "state"), "AIB_SEAT_RESUME_MENU_TIMEOUT_S": "2",
                    "EZ2_AIB_STATE_ROOT": str(self.t / "home"), "HERE": str(COCKPIT)})
        p = subprocess.run(["bash", "-c", 'set -euo pipefail; . "$1"; aib_cli_session_menu claude /x /y root 1',
                            "_", str(MENU)], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        spec = json.loads((self.t / "spec.json").read_text(encoding="utf-8"))
        return [it["label"] for it in spec["items"] if not it.get("separator") and it["value"] not in "123"]

    def test_s1_four_col_row_label(self):
        rows = [{"id": UUID_A, "code": "aeb40", "star": True, "four_col": True, "model": "claude-opus-5-5",
                 "bound": False, "bot_name": "", "tail": "測試人格乙 · 示範專案甲 · 10-28 17:15"},
                {"id": UUID_B, "code": "00b01", "star": False, "four_col": False, "model": "grok",
                 "bound": True, "bot_name": "小助手", "tail": "root 10-28 17:00 · 大腦:沒有 · 交接:無"}]
        got = self.labels(rows)
        self.assertEqual(got[0], "★ @aeb40 · 測試人格乙 · 示範專案甲 · 10-28 17:15")
        self.assertTrue(got[1].startswith("〔小助手〕 @00b01"), got[1])   # 橋接 bot 列這次不動


class MenuMeasures(unittest.TestCase):
    """#399 R1:選單殼量 tmux client_width,換成彈窗內寬(× 90% − 2)交給 cli_sessions;量不到就不給(用保守值)。"""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def argv_for(self, client_w):
        tm = self.t / "tmux"
        tm.write_text(FAKE_TMUX)
        tm.chmod(0o755)
        log = self.t / "py.log"
        py = self.t / "py"
        py.write_text('#!/bin/sh\ncase "$1" in *cli_sessions.py) echo "$*" >> %s ;; esac\nexec %s "$@"\n' % (log, sys.executable))
        py.chmod(0o755)
        (self.t / "state").mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update({"AIB_TMUX_BIN": str(tm), "CAPTURE": str(self.t / "spec.json"), "CLIENT_W": client_w,
                    "T_PY3": str(py), "AIB_CLI_SESSION_MENU_OUTER": "outer",
                    "EZ2_COCKPIT_STATE_DIR": str(self.t / "state"), "AIB_SEAT_RESUME_MENU_TIMEOUT_S": "2",
                    "EZ2_AIB_STATE_ROOT": str(self.t / "home"), "CLAUDE_CONFIG_DIR": str(self.t / "cc"),
                    "HERE": str(COCKPIT)})
        p = subprocess.run(["bash", "-c", 'set -euo pipefail; . "$1"; aib_cli_session_menu claude /x /y root 1',
                            "_", str(MENU)], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        line = log.read_text().strip().splitlines()[-1].split()
        self.assertIn("--popup-cols", line, "選單殼沒把彈窗內寬交給 cli_sessions")
        return line[line.index("--popup-cols") + 1]

    def test_s2_popup_cols_from_client_width(self):
        self.assertEqual(self.argv_for("100"), "88")     # 100 × 90% − 2
        self.assertEqual(self.argv_for("80"), "70")
        self.assertEqual(self.argv_for(""), "0")          # 量不到 ⇒ 0 ⇒ cli_sessions 用 80 欄終端的值
        self.assertEqual(self.argv_for("abc"), "0")


if __name__ == "__main__":
    unittest.main()
