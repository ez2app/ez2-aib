# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-17 修訂第二步:ci 補跑的判斷(假 gh,不碰 GitHub)。

守:重讀頭碼、只重跑頭碼那輪、只在 cancelled、每 SHA 最多 2 次、重跑前再讀一次(防重入)、讀不到 / 重跑失敗 /
次數用完 ⇒ rc 1 + ::error:: + macos_unverified;workflow 檔的觸發、權限、concurrency 也對照。
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_catchup", ROOT / "aib" / "tools" / "release" / "ci_catchup.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

A, B = "a" * 40, "b" * 40
REPO = "o/r"


class FakeGh:
    """heads:每次讀頭碼依序回的值(最後一個重複);runs:head_sha → 列表;states:run id → 依序回的狀態。"""

    def __init__(self, heads, runs=None, states=None, list_rc=0, rerun_rc=0):
        self.heads, self.runs, self.states = list(heads), runs or {}, {k: list(v) for k, v in (states or {}).items()}
        self.list_rc, self.rerun_rc, self.calls = list_rc, rerun_rc, []

    def __call__(self, argv):
        self.calls.append(argv)
        a = argv[1:]
        if a[0] == "api" and "/commits/" in a[1]:
            h = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return (0, h + "\n") if h else (1, "")
        if a[0] == "api" and "actions/runs?" in a[1]:
            sha = re.search(r"head_sha=(\w+)", a[1]).group(1)
            return self.list_rc, json.dumps({"workflow_runs": self.runs.get(sha, [])})
        if a[0] == "api" and "/actions/runs/" in a[1]:
            rid = int(a[1].rsplit("/", 1)[1])
            st = self.states.get(rid)
            if not st:
                return 1, ""
            return 0, json.dumps(st.pop(0) if len(st) > 1 else st[0])
        if a[0] == "run" and a[1] == "rerun":
            return self.rerun_rc, "" if self.rerun_rc == 0 else "HTTP 403: Resource not accessible by integration"
        raise AssertionError(argv)

    def reruns(self):
        return [c[3] for c in self.calls if c[1:3] == ["run", "rerun"]]


def run(rid, sha, status="completed", conclusion="cancelled", attempt=1, path=".github/workflows/ci.yml",
        created="2026-11-08T03:00:00Z", name="ci"):
    return {"id": rid, "head_sha": sha, "status": status, "conclusion": conclusion, "run_attempt": attempt,
            "path": path, "name": name, "created_at": created}


def decide(fake, max_reruns=2):
    said = []
    rc, act = cc.decide(REPO, "main", ".github/workflows/ci.yml", max_reruns, runner=fake, say=said.append)
    return rc, act, "\n".join(said)


class Catchup(unittest.TestCase):
    def test_reruns_only_the_cancelled_head_run(self):
        r = run(11, A)
        f = FakeGh([A], {A: [r]}, {11: [r]})
        rc, act, out = decide(f)
        self.assertEqual((rc, act), (0, "rerun"))
        self.assertEqual(f.reruns(), ["11"])
        self.assertIn("attempt 1 → 2", out)

    def test_rereads_the_head_not_the_triggering_run(self):
        # 觸發的是舊 SHA 的 cancelled 輪也一樣:只看現在頭碼 B;B 那輪成功 ⇒ 不動 A
        f = FakeGh([B], {A: [run(11, A)], B: [run(12, B, conclusion="success")]}, {11: [run(11, A)]})
        rc, act, _ = decide(f)
        self.assertEqual((rc, act, f.reruns()), (0, "nothing", []))

    def test_failure_queued_running_and_missing_are_left_alone(self):
        for r in (run(11, A, conclusion="failure"), run(11, A, status="queued", conclusion=None),
                  run(11, A, status="in_progress", conclusion=None), run(11, A, path=".github/workflows/aibd.yml"),
                  run(11, A, path=".github/workflows/aibd.yml", name="ci")):   # 認路徑、不認顯示名稱
            with self.subTest(r=r):
                f = FakeGh([A], {A: [r]}, {11: [r]})
                rc, act, _ = decide(f)
                self.assertEqual((rc, act, f.reruns()), (0, "nothing", []))

    def test_newest_run_of_the_sha_decides(self):
        old, new = run(11, A, created="2026-11-08T03:00:00Z"), run(12, A, conclusion="success",
                                                                    created="2026-11-08T03:05:00Z")
        f = FakeGh([A], {A: [old, new]}, {11: [old], 12: [new]})
        self.assertEqual(decide(f)[1], "nothing")

    def test_at_most_two_reruns_per_sha_then_alert(self):
        for attempt, want in ((1, "rerun"), (2, "rerun"), (3, "alert")):
            with self.subTest(attempt=attempt):
                r = run(11, A, attempt=attempt)
                f = FakeGh([A], {A: [r]}, {11: [r]})
                rc, act, out = decide(f)
                self.assertEqual(act, want)
                if want == "alert":
                    self.assertEqual((rc, f.reruns()), (1, []))
                    self.assertIn("::error::", out)
                    self.assertIn("macos_unverified " + A, out)

    def test_recheck_before_rerun_head_moved_or_run_changed(self):
        r = run(11, A)
        f = FakeGh([A, B], {A: [r]}, {11: [r]})               # 第二次讀頭碼已前進
        self.assertEqual((decide(f)[1], f.reruns()), ("nothing", []))
        f = FakeGh([A], {A: [r]}, {11: [run(11, A, attempt=2)]})   # 別的補跑已經重跑過
        self.assertEqual((decide(f)[1], f.reruns()), ("nothing", []))
        f = FakeGh([A], {A: [r]}, {11: [run(11, A, status="queued", conclusion=None)]})
        self.assertEqual((decide(f)[1], f.reruns()), ("nothing", []))

    def test_unreadable_or_rerun_failure_alerts_not_silent(self):
        cases = (FakeGh([""]), FakeGh([A], list_rc=1),
                 FakeGh([A], {A: [run(11, A)]}, {11: [run(11, A)]}, rerun_rc=1))
        for f in cases:
            with self.subTest(calls=len(cases)):
                rc, act, out = decide(f)
                self.assertEqual((rc, act), (1, "alert"))
                self.assertIn("::error::", out)
                self.assertIn("macos_unverified", out)

    def test_attempt_three_non_cancelled_is_left_alone_not_alerted(self):
        # a9136 #498 審:只在 cancelled 才看次數;failure / success 在 attempt 3 也是「不動」,不是假告警
        for c in ("failure", "success"):
            with self.subTest(c=c):
                r = run(11, A, conclusion=c, attempt=3)
                f = FakeGh([A], {A: [r]}, {11: [r]})
                self.assertEqual(decide(f)[:2], (0, "nothing"))

    def test_recheck_unreadable_alerts_instead_of_silent_nothing(self):
        # a9136 #498 M1:重跑前第二次讀頭碼 / 那輪讀不到 ⇒ 告警,不是當成「變了」靜默 rc 0
        r = run(11, A)
        f = FakeGh([A, ""], {A: [r]}, {11: [r]})
        rc, act, out = decide(f)
        self.assertEqual((rc, act, f.reruns()), (1, "alert", []))
        self.assertIn("cannot re-read the head", out)
        f = FakeGh([A], {A: [r]}, {})          # run_state 讀不到
        rc, act, out = decide(f)
        self.assertEqual((rc, act, f.reruns()), (1, "alert", []))
        self.assertIn("cannot re-read run 11", out)

    def test_cli_usage(self):
        self.assertEqual(cc.main(["--repo", "noslash"]), cc.RC_USAGE)
        self.assertEqual(cc.main(["--repo", "o/r", "--max-reruns", "-1"]), cc.RC_USAGE)


class Workflow(unittest.TestCase):
    TEXT = (ROOT / ".github" / "workflows" / "ci-catchup.yml").read_text(encoding="utf-8")

    def test_trigger_permissions_and_no_reentry(self):
        t = self.TEXT
        self.assertIn("workflows: [ci]", t)
        self.assertIn("branches: [main]", t)
        self.assertIn("types: [completed]", t)
        self.assertRegex(t, r"(?m)^  actions: write")
        self.assertRegex(t, r"(?m)^  contents: read")
        self.assertRegex(t, r"(?m)^concurrency:\n  group: ci-catchup\n  cancel-in-progress: false$")
        for guard in ("workflow_run.event == 'push'", "head_branch == 'main'",
                      "head_repository.full_name == github.repository"):
            self.assertIn(guard, t)
        self.assertIn("--max-reruns 2", t)
        self.assertIn("--workflow-path .github/workflows/ci.yml", t)   # 明寫,不靠預設值剛好一樣
        # 跑的是預設分支上的判斷腳本,不是觸發那輪(可能是 PR 改過的)的
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", t)


if __name__ == "__main__":
    unittest.main()
