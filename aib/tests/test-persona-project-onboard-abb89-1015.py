#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""席位開席 / 人格 / 專案 / 推包一串修補(10-15)。每一格一個缺陷,全在 tmp 沙盒跑:
不碰真 ~/.ez2-aib、不碰 tmux、不碰真 vault。

C1  開席選單「載入全部 / 只鐵則」與 `aib seat onboard --profile full` 恆失敗:公開倉缺開席單模板。
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import aib_testlib  # noqa: E402,F401 —— 基因/自動綁預設關

ROOT = HERE.parent                       # aib/
COCKPIT = ROOT / "src" / "cockpit"
PY = sys.executable


def sandbox_env(tmp):
    """子行程用的沙盒環境:HOME/狀態根/艙家全在 tmp。"""
    env = dict(os.environ)
    home = os.path.join(tmp, "home")
    os.makedirs(home, exist_ok=True)
    env.update({
        "HOME": home,
        "EZ2_AIB_HOME": os.path.join(home, ".ez2-aib"),
        "EZ2_AIB_STATE_ROOT": os.path.join(home, ".ez2-aib"),
        "EZ2_AIB_INSTANCE_HOME": os.path.join(tmp, "inst"),
        "EZ2_AIB_LOCALE": "en",
        "AIB_GENES": "0",
        "AIB_AUTO_BIND": "0",
    })
    for k in ("AIB_DM_SOCKET", "EZ2_AIHUB_SOCKET", "TMUX", "TMUX_PANE"):
        env.pop(k, None)
    return env


class C1SeatOnboardTemplate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-c1-")
        self.env = sandbox_env(self.tmp)
        self.seats = os.path.join(self.tmp, "inst", "seats")
        os.makedirs(self.seats)
        with open(os.path.join(self.seats, "w7.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "aib-seat/v1", "seat_id": "abcde", "role": "dev", "provider": "claude",
                       "window_id": "@7"}, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, profile):
        return subprocess.run(
            [PY, str(COCKPIT / "seat_onboard.py"), "--prepare-self-fetch", "--window", "@7",
             "--provider", "claude", "--seats", self.seats, "--profile", profile],
            env=self.env, capture_output=True, text=True, timeout=60)

    def test_a_template_ships_in_package(self):
        sys.path.insert(0, str(COCKPIT))
        import seat_onboard
        self.assertTrue((ROOT / seat_onboard.TEMPLATE_REL).is_file(),
                        "開席單模板不在公開包裡:%s" % seat_onboard.TEMPLATE_REL)

    def test_b_full_and_rules_both_render_with_ack(self):
        for profile in ("full", "rules"):
            r = self._prepare(profile)
            self.assertEqual(r.returncode, 0, (profile, r.stdout, r.stderr))
            body = pathlib.Path(r.stdout.strip()).read_text(encoding="utf-8")
            self.assertIn("已就緒", body, profile)       # 回執字:沒有它守望永遠等不到
            self.assertNotIn("aib:section", body)
            self.assertEqual(re.findall(r"\{[a-z_]+\}", body), [], profile)  # 佔位符全代入
            self.assertTrue(body.lstrip().startswith("[Iron rules"), body[:80])
            if profile == "rules":
                self.assertNotIn("[Seat order]", body)
            else:
                self.assertIn("seat_id=abcde", body)

    def test_c_template_is_maintainer_neutral(self):
        text = (ROOT / "docs" / "templates" / "SEAT-ONBOARD.md").read_text(encoding="utf-8")
        for bad in ("Andy", "the maintainer", "/Users/", "~/.ez2-aib/seats", "艦長"):
            self.assertNotIn(bad, text)
        left = set(re.findall(r"\{[a-z_]+\}", text.split("---", 1)[1]))
        self.assertLessEqual(left, {"{role_label}", "{seat_id}", "{window}", "{seat_file}",
                                    "{cabin_label}", "{ack_instruction}"})

    def test_d_menu_does_not_offer_load_when_template_missing(self):
        src = (COCKPIT / "aib").read_text(encoding="utf-8")
        i = src.index("aib_open_menu_after_right() {")
        body = src[i:src.index("\n}\n", i)]
        self.assertIn("docs/templates/SEAT-ONBOARD.md", body)
        self.assertIn("aib.onboardcli.template_missing_menu", body)
        for loc in ("en", "ja", "ko", "zh-TW"):
            d = json.loads((ROOT / "i18n" / ("%s.json" % loc)).read_text(encoding="utf-8"))
            self.assertIn("{window}", d["aib.onboardcli.template_missing_menu"])


class C7ProjectInRepoWithSrc(unittest.TestCase):
    """使用者自己的專案有 src/ 套件時,`aib project …` 不得撞名崩潰。"""

    def test_project_help_inside_repo_with_own_src(self):
        tmp = tempfile.mkdtemp(prefix="aib-c7-")
        try:
            proj = os.path.join(tmp, "proj")
            os.makedirs(os.path.join(proj, "src"))
            open(os.path.join(proj, "src", "__init__.py"), "w").close()
            r = subprocess.run(["bash", str(COCKPIT / "aib"), "project", "--help"],
                               cwd=proj, env=sandbox_env(tmp), capture_output=True,
                               text=True, timeout=60)
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 0, out)
            self.assertNotIn("ModuleNotFoundError", out)
            self.assertNotIn("Traceback", out)
            self.assertIn("interpret", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
