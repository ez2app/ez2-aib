# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-8 第 3 項:CI 新推取消舊的(PR 取消、main 不取消)。

只看工作流程檔的文字(CI 上沒有 yaml 套件);真正的取消行為要在 GitHub 上看,驗法寫在 PR 說明。
這支守的是:兩個工作流程的 group 都帶 workflow 名、PR 用 PR 號、main 用 github.sha、
只有 pull_request 才 cancel-in-progress,而且 ci / aibd 用同一條規則。
"""
import re
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


if __name__ == "__main__":
    unittest.main()
