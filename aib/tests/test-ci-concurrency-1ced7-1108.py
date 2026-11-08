# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-8 第 3 項:CI 新推取消舊的(PR 取消、main 不取消)。

只看工作流程檔的文字(CI 上沒有 yaml 套件);真正的取消行為要在 GitHub 上看,驗法寫在 PR 說明。
這支守的是:兩個工作流程的 group 都帶 workflow 名、PR 用 PR 號、main 用 github.sha、
只有 pull_request 才 cancel-in-progress,而且 ci / aibd 用同一條規則。
"""
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ("ci.yml", "aibd.yml")


def concurrency_block(name):
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    m = re.search(r"^concurrency:\n((?:[ \t]+.*\n)+)", text, re.M)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.strip().partition(":")
        fields[key] = value.strip()
    return fields


class CiConcurrency(unittest.TestCase):
    def test_each_workflow_has_top_level_concurrency(self):
        for name in WORKFLOWS:
            with self.subTest(name=name):
                self.assertIsNotNone(concurrency_block(name), f"{name} 沒有頂層 concurrency")

    def test_group_has_workflow_name_pr_number_and_sha(self):
        for name in WORKFLOWS:
            with self.subTest(name=name):
                group = concurrency_block(name)["group"]
                # workflow 名 ⇒ ci 與 aibd 不互相取消
                self.assertTrue(group.startswith("${{ github.workflow }}-"), group)
                # PR 依 PR 號分組;其他事件(main 推送)依 SHA,每次推送自成一組
                self.assertIn("github.event.pull_request.number", group)
                self.assertIn("github.sha", group)
                # 不准用 github.ref:main 推送會共用 refs/heads/main 這一組,互相取消 / 蓋掉排隊
                self.assertNotIn("github.ref", group)
                self.assertNotIn("github.head_ref", group)

    def test_cancel_only_on_pull_request(self):
        for name in WORKFLOWS:
            with self.subTest(name=name):
                cancel = concurrency_block(name)["cancel-in-progress"]
                self.assertEqual(cancel, "${{ github.event_name == 'pull_request' }}")

    def test_both_workflows_use_the_same_rule(self):
        self.assertEqual(concurrency_block("ci.yml"), concurrency_block("aibd.yml"))


def job_concurrency(name, job):
    """jobs.<job>.concurrency 的欄位(四格縮排的 job、六格縮排的欄位);沒有就 None。"""
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    m = re.search(r"^  %s:\n((?:(?:    .*|\s*)\n)+?)(?=^  \S|\Z)" % re.escape(job), text, re.M)
    if not m:
        return None
    b = re.search(r"^    concurrency:\n((?:      .*\n)+)", m.group(1), re.M)
    if not b:
        return None
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in b.group(1).splitlines())}


class MainMacosQueue(unittest.TestCase):
    """11-08 DECISION-17:main 推送的 macOS 格 1 跑 1 等(group 帶 ref、不砍跑中);ubuntu 格與 PR 不受限。"""

    JOBS = ("smoke", "fresh-machine")

    def test_macos_jobs_queue_on_main_by_ref_without_cancelling(self):
        for job in self.JOBS:
            with self.subTest(job=job):
                c = job_concurrency("ci.yml", job)
                self.assertIsNotNone(c, job)
                self.assertEqual(c["cancel-in-progress"], "false")
                g = c["group"]
                main_part, _, other = g.partition("||")
                # 只有 push + macOS 才共用 ref 那一組
                self.assertIn("github.event_name == 'push'", main_part)
                self.assertIn("matrix.os == 'macos-latest'", main_part)
                # 修訂:main 已前進(stale)的那輪不進共用組 ⇒ 晚到的舊格搶不到新格的位子
                self.assertIn("needs.tip.outputs.stale != 'true'", main_part)
                self.assertIn("github.ref", main_part)
                self.assertIn("github.workflow", main_part)
                self.assertIn(job, main_part)
                self.assertIn("matrix.python", main_part)   # 每格各自排,不是 6 格搶 1 個位子
                self.assertNotIn("github.sha", main_part)
                # 其他(ubuntu 格、PR)每輪自成一組 ⇒ 不排隊
                self.assertIn("github.run_id", other)
                self.assertNotIn("github.ref", other)

    def test_top_level_rule_unchanged(self):
        # 頂層仍是 DECISION-8:main 用 sha、只有 PR 取消;job 層只多一層排隊
        self.assertIn("github.sha", concurrency_block("ci.yml")["group"])


def job_text(name, job):
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    m = re.search(r"^  %s:\n(.*?)(?=^  \S|\Z)" % re.escape(job), text, re.M | re.S)
    return m.group(1) if m else ""


def run_tip(event, sha, head, ref="main"):
    """真的跑 tip job 的 shell(假 gh 印 head;head 為空 = 讀不到),回 (rc, GITHUB_OUTPUT 內容 dict)。"""
    body = job_text("ci.yml", "tip")
    script = textwrap.dedent(body.split("        run: |\n", 1)[1])
    with tempfile.TemporaryDirectory() as td:
        b = os.path.join(td, "bin"); os.mkdir(b)
        with open(os.path.join(b, "gh"), "w") as f:
            f.write('#!/bin/sh\n[ -n "$FAKE_HEAD" ] || exit 1\nprintf "%s\\n" "$FAKE_HEAD"\n')
        os.chmod(os.path.join(b, "gh"), 0o755)
        out = os.path.join(td, "out")
        env = dict(os.environ, PATH=b + ":" + os.environ["PATH"], GITHUB_EVENT_NAME=event, GITHUB_SHA=sha,
                   GITHUB_REF_NAME=ref, GITHUB_REPOSITORY="o/r", GITHUB_OUTPUT=out, FAKE_HEAD=head)
        p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], env=env, capture_output=True, text=True)
        kv = dict(ln.split("=", 1) for ln in open(out).read().splitlines()) if os.path.exists(out) else {}
        return p.returncode, kv


class TipGate(unittest.TestCase):
    """11-08 DECISION-17 修訂:tip 前置 job 判 stale;stale 的 macOS 格改 ubuntu 只印 macos_unverified。"""

    def test_tip_script_marks_only_an_older_main_push_stale(self):
        a, b = "a" * 40, "b" * 40
        self.assertEqual(run_tip("push", a, a), (0, {"head": a, "stale": "false"}))
        self.assertEqual(run_tip("push", a, b), (0, {"head": b, "stale": "true"}))
        self.assertEqual(run_tip("pull_request", a, b), (0, {"stale": "false"}))   # PR 不判
        self.assertEqual(run_tip("push", a, ""), (0, {"stale": "false"}))         # 讀不到 ⇒ 當最新照跑

    def test_macos_jobs_need_tip_and_skip_when_stale(self):
        for job in ("smoke", "fresh-machine"):
            with self.subTest(job=job):
                t = job_text("ci.yml", job)
                self.assertRegex(t, r"(?m)^    needs: tip$")
                runs_on = re.search(r"(?m)^    runs-on: (.*)$", t).group(1)
                run_cell = re.search(r"(?m)^      RUN_CELL: (.*)$", t).group(1)
                for expr in (runs_on, run_cell):
                    self.assertIn("needs.tip.outputs.stale", expr)
                self.assertIn("macos_unverified", t)
        # smoke 的 runs-on 與 RUN_CELL 用同一句條件(runs-on 讀不到 env)
        t = job_text("ci.yml", "smoke")
        runs_on = re.search(r"(?m)^    runs-on: \$\{\{ \((.*)\) && \(matrix.os == 'ubuntu-latest'", t).group(1)
        run_cell = re.search(r"(?m)^      RUN_CELL: \$\{\{ (.*) \}\}$", t).group(1)
        self.assertEqual(runs_on, run_cell)


if __name__ == "__main__":
    unittest.main()
