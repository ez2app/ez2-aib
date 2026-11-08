# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-24 macOS 名額:ci.yml 的 macOS 格什麼時候跑(用小型 GitHub 表達式求值器真的算,不只比字面)。

守:
1. fresh-machine (macos) 在一般 PR 不跑(main / int/* / release/* / full-ci 才跑);
2. 草稿 PR 不跑任何 macOS 格;types 有 ready_for_review(轉正式那輪 macOS 3.12 會跑);
3. base 不是 main 的 PR(疊著的)不跑 macOS;types 有 edited,改 base 那次照跑,
   只改標題 / 說明的 edited 整輪不跑、也不取消同一張 PR 正在跑的那輪(concurrency 自成一組);
- runs-on 與 RUN_CELL 每個情境都一致(macOS 機器只在真的要跑時才排);
- ubuntu 格一律跑;main 推送 stale 時 macOS 不跑(DECISION-17 照舊);
- 沒跑的格子印的原因對,三選一各印各的:只改文件 / 這張 PR 不輪 macOS / main 已前進(真 stale 才印 main moved on,
  bb7dc #512 審);
- 跟 #504(docs job、needs: [tip, docs]、!cancelled())並存:只改標題的 edited 四個 job 全跳過(不會走到 guard 判紅)。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


# ── 小型 GitHub Actions 表達式求值器(只做這幾個 workflow 用到的子集)────────────────────────────
TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+(?:\.\d+)?)|(?P<op>&&|\|\||==|!=|!|\(|\)|,)"
                   r"|(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.(?:\*|[A-Za-z_][A-Za-z0-9_\-]*))*))")


def tokenize(src):
    pos, out = 0, []
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m or m.end() == pos:
            if src[pos:].strip() == "":
                break
            raise ValueError("cannot parse at %r" % src[pos:pos + 20])
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


def truthy(v):
    return not (v is None or v is False or v == "" or v == 0)


def norm(v):
    return v.lower() if isinstance(v, str) else v


class Expr:
    def __init__(self, src, ctx):
        self.t, self.i, self.ctx = tokenize(src), 0, ctx

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if val is not None and tok[1] != val:
            raise ValueError("expected %s got %s" % (val, tok))
        self.i += 1
        return tok

    def parse(self):
        v = self.or_()
        if self.i != len(self.t):
            raise ValueError("trailing %s" % (self.t[self.i:],))
        return v

    def or_(self):
        v = self.and_()
        while self.peek()[1] == "||":
            self.take()
            r = self.and_()
            v = v if truthy(v) else r
        return v

    def and_(self):
        v = self.cmp()
        while self.peek()[1] == "&&":
            self.take()
            r = self.cmp()
            v = r if truthy(v) else v
        return v

    def cmp(self):
        v = self.unary()
        while self.peek()[1] in ("==", "!="):
            op = self.take()[1]
            r = self.unary()
            eq = norm(v) == norm(r)
            v = eq if op == "==" else not eq
        return v

    def unary(self):
        if self.peek()[1] == "!":
            self.take()
            return not truthy(self.unary())
        return self.atom()

    def atom(self):
        kind, val = self.take()
        if val == "(":
            v = self.or_()
            self.take(")")
            return v
        if kind == "str":
            return val[1:-1].replace("''", "'")
        if kind == "num":
            return int(val) if val.isdigit() else float(val)
        if val in ("true", "false"):
            return val == "true"
        if val == "null":
            return None
        if self.peek()[1] == "(":
            self.take("(")
            args = []
            while self.peek()[1] != ")":
                args.append(self.or_())
                if self.peek()[1] == ",":
                    self.take(",")
            self.take(")")
            return self.call(val, args)
        return self.lookup(val)

    def call(self, fn, args):
        if fn == "contains":
            hay, needle = args
            if isinstance(hay, list):
                return any(norm(x) == norm(needle) for x in hay)
            return norm(str(needle)) in norm(str(hay or ""))
        if fn == "startsWith":
            return norm(str(args[0] or "")).startswith(norm(str(args[1])))
        if fn == "format":
            out = args[0]
            for n, a in enumerate(args[1:]):
                out = out.replace("{%d}" % n, "" if a is None else str(a))
            return out
        if fn == "cancelled":
            return bool(self.ctx.get("_cancelled"))
        raise ValueError(fn)

    def lookup(self, path):
        cur = [self.ctx]
        star = False
        for part in path.split("."):
            if part == "*":
                star = True
                cur = [x for c in cur if isinstance(c, list) for x in c]
                continue
            cur = [c.get(part) if isinstance(c, dict) else None for c in cur]
        if star:
            return [c for c in cur if c is not None]
        return cur[0]


def ev(src, ctx):
    """整欄是一個 ${{ }} ⇒ 回值;夾著字面(例如 group 的 'a-${{ x }}-${{ y }}')⇒ 逐段代換成字串。"""
    src = src.strip()
    parts = re.split(r"\$\{\{(.*?)\}\}", src, flags=re.S)
    if len(parts) == 3 and parts[0] == "" and parts[2] == "":
        return Expr(parts[1], ctx).parse()
    if len(parts) == 1:
        return Expr(src, ctx).parse()
    out = []
    for n, piece in enumerate(parts):
        if n % 2 == 0:
            out.append(piece)
        else:
            v = Expr(piece, ctx).parse()
            out.append("" if v is None else str(v))
    return "".join(out)


# ── 從 ci.yml 抓表達式 ─────────────────────────────────────────────────────────────────────
def job_text(name):
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)" % re.escape(name), CI)
    return m.group(1)


def field(job, key):
    return re.search(r"(?m)^    %s: (.*)$" % key if key != "RUN_CELL" else r"(?m)^      RUN_CELL: (.*)$",
                     job_text(job)).group(1)


def ctx_for(event="pull_request", action="synchronize", draft=False, base="main", head="feat/x", labels=(),
            stale="false", os="macos-latest", python="3.12", changes=None, number=7, docs_only="false"):
    pr = {"number": number, "draft": draft, "labels": [{"name": n} for n in labels], "base": {"ref": base}}
    gh = {"event_name": event, "workflow": "ci", "run_id": 99, "sha": "abc", "ref": "refs/heads/main",
          "head_ref": head if event == "pull_request" else "", "base_ref": base if event == "pull_request" else "",
          "event": {"action": action, "pull_request": pr if event == "pull_request" else None,
                    "changes": changes}}
    return {"github": gh, "matrix": {"os": os, "python": python},
            "needs": {"tip": {"result": "success", "outputs": {"stale": stale}},
                      "docs": {"result": "success", "outputs": {"docs_only": docs_only}}}}


def cell(job, **kw):
    """(runs_on, run_cell)"""
    c = ctx_for(**kw)
    return ev(field(job, "runs-on"), c), truthy(ev(field(job, "RUN_CELL"), c))


class Evaluator(unittest.TestCase):
    def test_semantics(self):
        c = {"a": {"b": "X"}, "l": [{"name": "full-ci"}]}
        self.assertEqual(ev("a.b == 'x'", c), True)               # 字串比較不分大小寫(GitHub 規則)
        self.assertEqual(ev("false || 'y'", c), "y")
        self.assertEqual(ev("true && 'y' || 'z'", c), "y")
        self.assertEqual(ev("!a.missing", c), True)
        self.assertEqual(ev("contains(l.*.name, 'full-ci')", c), True)
        self.assertEqual(ev("startsWith('int/x', 'int/')", c), True)
        self.assertEqual(ev("format('pr-{0}', 7)", c), "pr-7")


class MacosCells(unittest.TestCase):
    SMOKE_MAC = ("3.9", "3.12", "3.14")

    def assert_cells(self, smoke_run, fresh_run, **kw):
        for py in self.SMOKE_MAC:
            runs_on, run = cell("smoke", python=py, **kw)
            self.assertEqual(run, py in smoke_run, (py, kw))
            self.assertEqual(runs_on, "macos-latest" if run else "ubuntu-latest", (py, kw, "runs-on 要跟 RUN_CELL 一致"))
        runs_on, run = cell("fresh-machine", **kw)
        self.assertEqual(run, fresh_run, ("fresh-machine", kw))
        self.assertEqual(runs_on, "macos-latest" if run else "ubuntu-latest", ("fresh-machine", kw))

    def test_ready_pr_to_main_runs_only_smoke_312(self):
        self.assert_cells({"3.12"}, False)                        # 1. fresh-machine (macos) 一般 PR 不跑

    def test_draft_pr_runs_no_macos(self):
        self.assert_cells(set(), False, draft=True)               # 2. 草稿不跑

    def test_ready_for_review_run_has_smoke_312(self):
        self.assert_cells({"3.12"}, False, action="ready_for_review", draft=False)

    def test_stacked_pr_runs_no_macos_until_retargeted(self):
        self.assert_cells(set(), False, base="feat/lease-two-lanes-1ced7-1108")      # 3. 疊著的不跑
        self.assert_cells({"3.12"}, False, action="edited", base="main",
                          changes={"base": {"ref": {"from": "feat/lease-two-lanes-1ced7-1108"}}})

    def test_full_ci_label_runs_everything_even_on_a_draft(self):
        self.assert_cells(set(self.SMOKE_MAC), True, draft=True, labels=("full-ci",))
        self.assert_cells(set(self.SMOKE_MAC), True, base="other", labels=("full-ci",))

    def test_int_and_release_prs_run_everything_when_ready(self):
        for h in ("int/x", "release/v0.1.70"):
            with self.subTest(h=h):
                self.assert_cells(set(self.SMOKE_MAC), True, head=h)
                self.assert_cells(set(), False, head=h, draft=True)

    def test_main_push(self):
        self.assert_cells(set(self.SMOKE_MAC), True, event="push")
        self.assert_cells(set(), False, event="push", stale="true")       # DECISION-17 照舊

    def test_docs_only_runs_no_cell_at_all(self):
        # #504:只改文件 ⇒ 六格 + fresh-machine 都換到 ubuntu 只印一行(macOS 也不排)
        for kw in ({}, {"draft": True}, {"labels": ("full-ci",)}, {"event": "push"}, {"head": "int/x"}):
            with self.subTest(kw=kw):
                self.assert_cells(set(), False, docs_only="true", **kw)
                for py in self.SMOKE_MAC:
                    self.assertEqual(cell("smoke", os="ubuntu-latest", python=py, docs_only="true", **kw),
                                     ("ubuntu-latest", False))

    def test_ubuntu_cells_always_run(self):
        for kw in ({}, {"draft": True}, {"base": "other"}, {"event": "push", "stale": "true"}):
            for py in self.SMOKE_MAC:
                runs_on, run = cell("smoke", os="ubuntu-latest", python=py, **kw)
                self.assertTrue(run, kw)
                self.assertEqual(runs_on, "ubuntu-24.04" if py == "3.9" else "ubuntu-latest")
            runs_on, run = cell("fresh-machine", os="ubuntu-latest", **kw)
            self.assertEqual((runs_on, run), ("ubuntu-latest", True), kw)


def steps(job):
    """[(name, if 表達式或 None)]"""
    out = []
    for m in re.finditer(r"(?ms)^      - (?:name: (?P<name>[^\n]*)\n(?:        if: (?P<if>[^\n]*)\n)?|uses: [^\n]*\n(?:        if: (?P<if2>[^\n]*)\n)?)",
                         job_text(job)):
        out.append((m.group("name") or "(uses)", m.group("if") or m.group("if2")))
    return out


def fired(job, **kw):
    """這個情境下,沒跑的格子會印哪幾步(只看帶 if 的說明步驟)。"""
    c = ctx_for(**kw)
    run = truthy(ev(field(job, "RUN_CELL"), c))
    c["env"] = {"RUN_CELL": "true" if run else "false"}
    return [n for n, cond in steps(job)
            if cond and ("RUN_CELL != 'true'" in cond or cond == "needs.docs.outputs.docs_only == 'true'")
            and truthy(ev(cond, c))]


class SkipMessages(unittest.TestCase):
    """bb7dc #512 審:沒跑的格子印的原因要對 —— 一般 PR / 草稿 / 疊著的不能印「main moved on」。"""

    STALE = "macOS cell skipped, main moved on (macos_unverified)"
    DOCS = "docs-only change, nothing else to run"

    def test_pr_skips_say_pr_not_main_moved_on(self):
        for kw in ({}, {"draft": True}, {"base": "other"}):
            with self.subTest(kw=kw):
                self.assertEqual(fired("fresh-machine", **kw), ["macOS cell not run on this PR"])
                self.assertEqual(fired("smoke", python="3.9", **kw), ["cell not run on this PR"])
        self.assertEqual(fired("smoke", python="3.12", draft=True), ["cell not run on this PR"])

    def test_real_stale_says_main_moved_on_only(self):
        for job, py in (("fresh-machine", "3.12"), ("smoke", "3.12"), ("smoke", "3.9")):
            with self.subTest(job=job, py=py):
                self.assertEqual(fired(job, event="push", stale="true", python=py), [self.STALE])

    def test_docs_only_says_docs_only_only(self):
        # 只改文件時,即使同時是草稿 / 疊著的 / main 已前進,也只印「只改文件」那一行
        for kw in ({}, {"draft": True}, {"base": "other"}, {"event": "push", "stale": "true"}):
            for job, os_, py in (("fresh-machine", "macos-latest", "3.12"), ("fresh-machine", "ubuntu-latest", "3.12"),
                                 ("smoke", "macos-latest", "3.9"), ("smoke", "macos-latest", "3.12"),
                                 ("smoke", "ubuntu-latest", "3.12")):
                with self.subTest(kw=kw, job=job, os=os_, py=py):
                    self.assertEqual(fired(job, os=os_, python=py, docs_only="true", **kw), [self.DOCS])

    def test_cells_that_run_print_no_skip_message(self):
        self.assertEqual(fired("smoke", python="3.12"), [])
        self.assertEqual(fired("fresh-machine", event="push"), [])
        self.assertEqual(fired("fresh-machine", os="ubuntu-latest", draft=True), [])


class EditedEvents(unittest.TestCase):
    def group(self, **kw):
        g = re.search(r"(?m)^concurrency:\n  group: (.*)$", CI).group(1)
        return ev(g, ctx_for(**kw))

    def tip_runs(self, **kw):
        return truthy(ev(re.search(r"(?m)^    if: (.*)$", job_text("tip")).group(1), ctx_for(**kw)))

    def test_types(self):
        types = re.search(r"(?m)^    types: \[(.*)\]$", CI).group(1).replace(" ", "").split(",")
        for t in ("opened", "synchronize", "reopened", "labeled", "ready_for_review", "edited"):
            self.assertIn(t, types)

    def test_title_edit_runs_nothing_and_cancels_nothing(self):
        title = {"title": {"from": "old"}}
        self.assertFalse(self.tip_runs(action="edited", changes=title))
        self.assertEqual(self.group(action="edited", changes=title), "ci-edit-noop-99", "不能跟 pr-7 同組(會取消正在跑的那輪)")

    def test_base_edit_runs_like_a_push(self):
        base = {"base": {"ref": {"from": "feat/x"}}}
        self.assertTrue(self.tip_runs(action="edited", changes=base))
        self.assertEqual(self.group(action="edited", changes=base), "ci-pr-7")

    def test_other_events_unchanged(self):
        for kw in ({"action": "synchronize"}, {"action": "ready_for_review"}, {"action": "labeled"}):
            self.assertTrue(self.tip_runs(**kw))
            self.assertEqual(self.group(**kw), "ci-pr-7")
        self.assertTrue(self.tip_runs(event="push", action=None))
        self.assertEqual(self.group(event="push", action=None), "ci-abc")

    def job_runs(self, job, **kw):
        c = ctx_for(**kw)
        return truthy(ev(re.search(r"(?m)^    if: (.*)$", job_text(job)).group(1), c))

    def test_title_edit_skips_all_four_jobs(self):
        # #504 讓 smoke / fresh-machine 帶 !cancelled()(上游紅也要跑到 guard 判紅)⇒ 光靠 needs 跟著跳過不夠,
        #   只改標題時 guard 會看到 tip=skipped 把格子判紅;四個 job 都要帶同一句
        title = {"title": {"from": "old"}}
        base = {"base": {"ref": {"from": "feat/x"}}}
        for job in ("tip", "docs", "smoke", "fresh-machine"):
            with self.subTest(job=job):
                self.assertFalse(self.job_runs(job, action="edited", changes=title))
                self.assertTrue(self.job_runs(job, action="edited", changes=base))
                self.assertTrue(self.job_runs(job))
                self.assertTrue(self.job_runs(job, event="push", action=None))

    def test_smoke_and_fresh_keep_504_structure(self):
        for job in ("smoke", "fresh-machine"):
            t = job_text(job)
            self.assertRegex(t, r"(?m)^    needs: \[tip, docs\]$")
            self.assertRegex(t, r"(?m)^    if: \$\{\{ !cancelled\(\) && ")
            self.assertIn("if: needs.tip.result != 'success' || needs.docs.result != 'success'", t)

if __name__ == "__main__":
    unittest.main()
