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


class C8ShipWithoutOriginMain(unittest.TestCase):
    """沒有 origin/main 的倉:aib ship package/release/sync 給一行四語說明,不吐 traceback。"""

    def test_no_origin_main_is_a_clear_message(self):
        tmp = tempfile.mkdtemp(prefix="aib-c8-")
        try:
            env = sandbox_env(tmp)
            env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                        "AIB_SHIP_PREFLIGHT": "0",
                        "AIB_HOST_LEASE_FILE": os.path.join(tmp, "lease.json")})
            repo = os.path.join(tmp, "r")
            os.makedirs(repo)
            subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], env=env, check=True)
            subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
                            "-q", "--allow-empty", "-m", "x"], env=env, check=True)
            ship = str(ROOT / "tools" / "release" / "ship.py")
            for args in (["package", "--preview"], ["package"],
                         ["release", "--version", "9.9.9", "--preview"], ["sync"]):
                r = subprocess.run([PY, ship, "--repo", repo] + args, env=env,
                                   capture_output=True, text=True, timeout=120)
                out = r.stdout + r.stderr
                self.assertNotEqual(r.returncode, 0, (args, out))
                self.assertNotIn("Traceback", out, args)
                self.assertIn("origin/main", out, args)
                self.assertIn("git remote add origin", out, args)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


BIN_AIB = ROOT.parent / "bin" / "aib"


def newrole(tmp, name, prompt, locale="en", *extra):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.path.join(tmp, "home"),
           "EZ2_AIB_LOCALE": locale, "AIB_GENES": "0", "AIB_AUTO_BIND": "0"}
    os.makedirs(env["HOME"], exist_ok=True)
    return subprocess.run([PY, str(BIN_AIB), "newrole", "--name", name, "--prompt", prompt] + list(extra),
                          cwd=tmp, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)


class C10NewroleShortPrompt(unittest.TestCase):
    """C10 一句話 --prompt 必 lint 失敗;E5 失敗前已印「分配新角色 UID」、失敗字寫死中文。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aib-c10-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_one_sentence_prompt_is_wrapped_into_three_sections(self):
        r = newrole(self.tmp, "Reviewer", "A careful code reviewer.")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        card = next(pathlib.Path(self.tmp, "home", ".ez2-aib").rglob("角色.md")).read_text(encoding="utf-8")
        body = card.split("---", 2)[-1]
        self.assertTrue(body.strip().startswith("A careful code reviewer."), body[:80])
        for h in ("## 職責\n", "## 紅線\n", "## 知識域\n"):
            self.assertIn(h, card)
        self.assertIn("--overwrite", r.stderr + r.stdout)     # 告訴人怎麼換成自己的三段

    def test_b_partial_sections_fail_before_uid_and_localized(self):
        for loc, word in (("en", "red lines"), ("ja", "禁止事項"), ("ko", "금지선")):
            r = newrole(self.tmp, "Partial" + loc, "x\n\n## 職責\na\n", loc, "--dry-run")
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 4, out)
            self.assertIn("PERSONA_LINT_FAIL", out)
            self.assertNotIn("分配新角色", out)               # E5:沒過檢查不印 UID
            self.assertNotIn("UID", out.split("PERSONA_LINT_FAIL")[0])
            self.assertNotIn("缺 ##", out)                    # E5:不再是寫死的中文
            self.assertIn(word, out, loc)
            self.assertIn("## 紅線", out)                     # 照抄就能用的標題

    def test_c_full_prompt_unchanged(self):
        full = "Me.\n\n## 職責\nA.\n\n## 紅線\nB.\n\n## 知識域\nC.\n"
        r = newrole(self.tmp, "Full", full)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        card = next(pathlib.Path(self.tmp, "home", ".ez2-aib").rglob("角色.md")).read_text(encoding="utf-8")
        self.assertIn(full.strip(), card)
        self.assertNotIn("--overwrite", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
