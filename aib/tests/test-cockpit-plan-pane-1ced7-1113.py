#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""座艙進度欄(plan + hosts)進自動組裝(1ced7 11-13;Andy「要，寫進自動組裝」)。

P1 預設 expected-roles = hub right plan hosts;plan off ⇒ 只剩 hub right
P2 center off ⇒ plan / hosts 不建(錨是 hub);plan 不能當「最後一個開著的」
P3 cockpit_pane_plan:四種形狀都把 plan 接在所有格之後、從 hub 往右切,hosts 從 plan 往下切
P4 _grow_missing_plan_column:已開著的座艙只缺進度欄 ⇒ 原地補兩格、版面驗得過、hub 不動
P5 形狀不對(多一格 / 缺右欄 / 已經有兩格 plan)⇒ 不動手、回非 0,交回原本的自癒判斷
P6 _balance_plan_column:夠寬 ⇒ 把 hub 調到讓 plan = 100;不夠寬 ⇒ 對半;只調 hub 不調 plan(調 plan 會吃右欄)
P4b 只剩 hosts、plan 被收掉 ⇒ 收 hosts 再補兩格;P4c aib plan off ⇒ 原地收掉兩格
P7 pane-plan.sh 找不到程式不退出(印鍵名 / 提示、等著)、Ctrl-C 不死,未知名字 rc 2
P8 席碼一定找得到(上方主軸幾行一定畫):看的窗 → 該窗席位檔 → 艦長窗 → 最近的席位檔

不碰真 tmux:被測函式從原檔逐字抽出來,outer_tmux 換成讀寫狀態檔的假函式。
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.normpath(os.path.join(HERE, "..", "src", "cockpit"))
FULL = os.path.join(C, "cockpit-full.sh")
RESIZE = os.path.join(C, "cockpit-resize.sh")


def extract(path, name):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), src, re.M | re.S)
    assert m, "%s not found in %s" % (name, path)
    return m.group(0)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def roles(cfg, tmp):
    p = os.path.join(tmp, "panes.json")
    with open(p, "w") as f:
        json.dump(cfg, f)
    out = subprocess.run([sys.executable, os.path.join(C, "panes.py"), "--expected-roles"],
                         env=dict(os.environ, EZ2_AIB_PANES=p), capture_output=True, text=True)
    return out.returncode, out.stdout.split()


# 假 outer_tmux:狀態檔每行「pane_id|role|width」
FAKE_TMUX = r'''
STATE="$T/rows"
outer_tmux() {
  echo "$*" >> "$T/calls"
  case "$1" in
    list-panes)
      case "$*" in
        *pane_dead*) awk -F '|' '{ print $1 " " $2 " 0" }' "$STATE" ;;
        *) awk -F '|' '{ print $1 "|" $2 }' "$STATE" ;;
      esac ;;
    split-window)
      local n; n="%$(( $(wc -l < "$STATE") + 50 ))"
      echo "$n||0" >> "$STATE"; echo "$n" ;;
    set-option)
      # set-option -p -t <id> @aib_role <role>
      awk -F '|' -v id="$4" -v r="$6" 'BEGIN{OFS="|"} $1==id{$2=r} {print}' "$STATE" > "$STATE.n" && mv "$STATE.n" "$STATE" ;;
    display|display-message)
      local id="" a
      for a in "$@"; do case "$a" in %*) id="$a" ;; esac; done
      awk -F '|' -v id="$id" '$1==id{print $3}' "$STATE" ;;
    kill-pane)
      awk -F '|' -v id="$3" '$1!=id' "$STATE" > "$STATE.n" && mv "$STATE.n" "$STATE" ;;
    resize-pane) ;;
  esac
}
'''


def run_bash(script, tmp):
    return subprocess.run(["bash", "-c", script], env=dict(os.environ, T=tmp, C=C, EZ2_PY3=sys.executable),
                          capture_output=True, text=True)


class PlanPane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="planpane-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_p1_default_and_off(self):
        self.assertEqual(roles({}, self.tmp), (0, ["hub", "right", "plan", "hosts"]))
        self.assertEqual(roles({"plan": False}, self.tmp)[1], ["hub", "right"])

    def test_p2_center_off_and_last_one(self):
        self.assertEqual(roles({"center": False}, self.tmp)[1], ["right"])
        p = os.path.join(self.tmp, "c.json")
        with open(p, "w") as f:
            json.dump({"left": False, "center": False, "right": True, "footer": False, "plan": True}, f)
        r = subprocess.run([sys.executable, os.path.join(C, "panes.py"), "right", "off"],
                           env=dict(os.environ, EZ2_AIB_PANES=p), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(json.loads(_read(p))["right"], True)   # 一個位元組都沒寫

    def _plan_lines(self, cfg):
        p = os.path.join(self.tmp, "panes.json")
        with open(p, "w") as f:
            json.dump(cfg, f)
        script = extract(FULL, "cockpit_pane_plan") + "cockpit_pane_plan\n"
        r = subprocess.run(["bash", "-c", script],
                           env=dict(os.environ, C=C, EZ2_PY3=sys.executable, EZ2_AIB_PANES=p),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [l.replace(C + "/", "") for l in r.stdout.splitlines()]

    def test_p3_plan_lines(self):
        tail = ["plan|hub|h|62%|pane-plan.sh", "hosts|plan|v|14|pane-hosts.sh"]
        for cfg in ({}, {"left": True}, {"left": True, "footer": True}, {"footer": True}):
            lines = self._plan_lines(cfg)
            self.assertEqual(lines[-2:], tail, cfg)
            self.assertLess(lines.index("right|hub|h|-|pane-right-tabs.sh"), lines.index(tail[0]), cfg)
        self.assertNotIn("plan", " ".join(self._plan_lines({"center": False})))
        self.assertNotIn("plan|", " ".join(self._plan_lines({"plan": False})))

    def _grow(self, rows, cfg=None):
        with open(os.path.join(self.tmp, "rows"), "w") as f:
            f.write("".join(r + "\n" for r in rows))
        open(os.path.join(self.tmp, "calls"), "w").close()
        script = FAKE_TMUX + "".join(extract(FULL, n) for n in (
            "cockpit_pane_plan", "validate_cockpit_layout", "_assign_pane_role", "_grow_missing_plan_column"))
        script += r'''
S=sess
cockpit_expected_roles() { "$EZ2_PY3" "$C/panes.py" --expected-roles; }
pane_command() { printf 'exec bash %s' "$1"; }
_grow_missing_plan_column; echo "rc=$?"
'''
        p = os.path.join(self.tmp, "none.json" if cfg is None else "grow.json")
        if cfg is not None:
            with open(p, "w") as f:
                json.dump(cfg, f)
        r = subprocess.run(["bash", "-c", script],
                           env=dict(os.environ, T=self.tmp, C=C, EZ2_PY3=sys.executable, EZ2_AIB_PANES=p),
                           capture_output=True, text=True)
        rows = _read(os.path.join(self.tmp, "rows")).split()
        calls = _read(os.path.join(self.tmp, "calls"))
        return r.stdout.strip().splitlines()[-1], rows, calls, r.stderr

    def test_p4_grow_in_place(self):
        rc, rows, calls, err = self._grow(["%0|hub|54", "%1|right|40", "%2|review|54"])
        self.assertEqual(rc, "rc=0", err)
        got = sorted(r.split("|")[1] for r in rows)
        self.assertEqual(got, ["hosts", "hub", "plan", "review", "right"])
        self.assertIn("split-window -h -t %0 -l 62%", calls)
        plan_id = [r.split("|")[0] for r in rows if r.split("|")[1] == "plan"][0]
        self.assertIn("split-window -v -t %s -l 14" % plan_id, calls)
        self.assertNotIn("kill", calls)
        # 只缺 hosts
        rc, rows, calls, err = self._grow(["%0|hub|54", "%1|right|40", "%3|plan|100"])
        self.assertEqual(rc, "rc=0", err)
        self.assertEqual(calls.count("split-window"), 1)
        self.assertIn("split-window -v -t %3 -l 14", calls)

    def test_p4b_hosts_left_without_plan(self):
        # 審查 M1:plan 格被收掉、hosts 還在 ⇒ 收掉那格 hosts、補兩格,不卡在拒絕重建
        rc, rows, calls, err = self._grow(["%0|hub|54", "%1|right|40", "%4|hosts|100"])
        self.assertEqual(rc, "rc=0", err)
        self.assertIn("kill-pane -t %4", calls)
        self.assertEqual(sorted(r.split("|")[1] for r in rows), ["hosts", "hub", "plan", "right"])

    def test_p4c_plan_off_removes_in_place(self):
        # 審查 M2:aib plan off 之後,已經在的兩格原地收掉,hub / right 不動
        rc, rows, calls, err = self._grow(["%0|hub|54", "%1|right|40", "%3|plan|100", "%4|hosts|100",
                                           "%2|review|54"], cfg={"plan": False})
        self.assertEqual(rc, "rc=0", err)
        self.assertEqual(sorted(r.split("|")[1] for r in rows), ["hub", "review", "right"])
        self.assertNotIn("split-window", calls)
        self.assertNotIn("kill-pane -t %0", calls)

    def test_p5_wrong_shape_untouched(self):
        for rows in (["%0|hub|54"],                                   # 缺右欄
                     ["%0|hub|54", "%1|right|40", "%9|left|30"],      # 多一格
                     ["%0|hub|54", "%1|right|40", "%3|plan|9", "%4|plan|9"],
                     ["%1|right|40"]):                                # 沒 hub
            rc, _, calls, _ = self._grow(rows)
            self.assertNotEqual(rc, "rc=0", rows)
            self.assertNotIn("split-window", calls, rows)
            self.assertNotIn("kill-pane", calls, rows)

    def _balance(self, hw, pw):
        with open(os.path.join(self.tmp, "rows"), "w") as f:
            f.write("%%0|hub|%d\n%%1|right|40\n%%3|plan|%d\n" % (hw, pw))
        open(os.path.join(self.tmp, "calls"), "w").close()
        script = FAKE_TMUX + "".join(extract(RESIZE, n) for n in (
            "_resolve_role_pane", "read_geometry", "_balance_plan_column"))
        script += "pane_absent() { [ -z \"${1:-}\" ]; }\n"
        script += 'S=sess; P_HUB=%0\n_balance_plan_column; echo "rc=$?"\n'
        r = run_bash(script, self.tmp)
        self.assertIn("rc=0", r.stdout, r.stderr)
        return [l for l in _read(os.path.join(self.tmp, "calls")).splitlines() if l.startswith("resize-pane")]

    def test_p6_balance(self):
        self.assertEqual(self._balance(150, 60), ["resize-pane -t %0 -x 110"])   # plan 到 100
        self.assertEqual(self._balance(54, 100), [])                            # 已經 100
        self.assertEqual(self._balance(80, 50), ["resize-pane -t %0 -x 50"])    # 130:對話保 50,plan 拿 80
        self.assertEqual(self._balance(50, 30), ["resize-pane -t %0 -x 40"])    # 80:再窄 ⇒ 對半
        for call in self._balance(150, 60):
            self.assertNotIn("%3", call)                                        # 不調 plan

    def test_p7_wrapper(self):
        r = subprocess.run(["bash", os.path.join(C, "pane-plan.sh"), "aib-nope"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 2)
        env = dict(os.environ, AIB_PLAN_BIN=os.path.join(self.tmp, "missing"), HOME=self.tmp)
        env.pop("TMUX_PANE", None)
        p = subprocess.Popen(["bash", os.path.join(C, "pane-plan.sh"), "aib-plan"], start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
        try:
            time.sleep(3)
            self.assertIsNone(p.poll(), "wrapper exited")
            os.killpg(p.pid, signal.SIGINT)          # Ctrl-C:包裝不能跟著死(審查 M1)
            time.sleep(1)
            self.assertIsNone(p.poll(), "wrapper died on SIGINT")
        finally:
            os.killpg(p.pid, signal.SIGKILL)         # 連 sleep 一起收,不留孤兒
            out, _ = p.communicate(timeout=10)
        self.assertIn("aib-plan", out)

    def _seat(self, windows, seat_files):
        """windows: list of 'attached active wid seat';seat_files: {窗號: session_uuid}"""
        home = os.path.join(self.tmp, "inst")
        os.makedirs(os.path.join(home, "seats"), exist_ok=True)
        for n, u in seat_files.items():
            with open(os.path.join(home, "seats", "w%s.json" % n), "w") as f:
                json.dump({"session_uuid": u}, f)
        with open(os.path.join(self.tmp, "wins"), "w") as f:
            f.write("".join(w + "\n" for w in windows))
        src = _read(os.path.join(C, "pane-plan.sh"))
        funcs = "".join(m.group(0) for m in re.finditer(r"^_seat(?:_of_file)?\(\) \{[^\n]*\n.*?^\}\n", src, re.M | re.S))
        self.assertEqual(funcs.count("() {"), 2, "抽不到 _seat / _seat_of_file")
        # 假 tmux 只認 -L <名> list-windows(EZ2_AIHUB_SOCKET 是 -L 名字;增量審 M1:舊的 -S 測試照樣綠)
        script = ('tmux() { [ "$1 $2 $3" = "-L aihub-t list-windows" ] || return 1; cat "$T/wins"; }\n'
                  "NAME=aib-plan\n" + funcs + "_seat\n")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=dict(os.environ, T=self.tmp, EZ2_PY3=sys.executable, EZ2_AIHUB_SOCKET="aihub-t",
                                    EZ2_AIB_INSTANCE_HOME=home))
        return r.stdout.strip()

    def test_p8_seat_always_found(self):
        # Andy 11-13「強制一定要畫出來」:看的窗 → 該窗席位檔 → 艦長窗 → 最近的席位檔,任一層有就有
        self.assertEqual(self._seat(["1 1 @4 519d0", "1 0 @7 "], {}), "519d0")
        self.assertEqual(self._seat(["1 0 @4 519d0", "1 1 @7"], {7: "aaaa-bbbb-1ced7"}), "1ced7")
        self.assertEqual(self._seat(["1 0 @4 519d0", "1 1 @3"], {}), "519d0")
        self.assertEqual(self._seat([], {6: "x-2240e"}), "2240e")


if __name__ == "__main__":
    unittest.main(verbosity=2)
