# SPDX-License-Identifier: Apache-2.0
"""11-08 DECISION-24 macOS 名額:ci.yml 的 macOS 格什麼時候跑(用小型 GitHub 表達式求值器真的算,不只比字面)。

守:
1. fresh-machine (macos) 在一般 PR 不跑(main / int/* / release/* / full-ci 才跑);
2. 草稿 PR 不跑任何 macOS 格;types 有 ready_for_review(轉正式那輪 macOS 3.12 會跑);
3. base 不是 main 的 PR(疊著的)不跑 macOS;types 有 edited,改 base 那次照跑,
   只改標題 / 說明的 edited 整輪不跑、也不取消同一張 PR 正在跑的那輪(concurrency 自成一組);
- runs-on 與 RUN_CELL 每個情境都一致(macOS 機器只在真的要跑時才排);
- ubuntu 格一律跑;main 推送 stale 時 macOS 不跑(DECISION-17 照舊)。
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
            stale="false", os="macos-latest", python="3.12", changes=None, number=7):
    pr = {"number": number, "draft": draft, "labels": [{"name": n} for n in labels], "base": {"ref": base}}
    gh = {"event_name": event, "workflow": "ci", "run_id": 99, "sha": "abc", "ref": "refs/heads/main",
          "head_ref": head if event == "pull_request" else "", "base_ref": base if event == "pull_request" else "",
          "event": {"action": action, "pull_request": pr if event == "pull_request" else None,
                    "changes": changes}}
    return {"github": gh, "matrix": {"os": os, "python": python}, "needs": {"tip": {"outputs": {"stale": stale}}}}


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

    def test_ubuntu_cells_always_run(self):
        for kw in ({}, {"draft": True}, {"base": "other"}, {"event": "push", "stale": "true"}):
            for py in self.SMOKE_MAC:
                runs_on, run = cell("smoke", os="ubuntu-latest", python=py, **kw)
                self.assertTrue(run, kw)
                self.assertEqual(runs_on, "ubuntu-24.04" if py == "3.9" else "ubuntu-latest")
            runs_on, run = cell("fresh-machine", os="ubuntu-latest", **kw)
            self.assertEqual((runs_on, run), ("ubuntu-latest", True), kw)


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

    def test_smoke_and_fresh_need_tip(self):
        # tip 跳過 ⇒ 它們跟著跳過(沒有 if: always()/!cancelled() 把它們拉起來)
        for job in ("smoke", "fresh-machine"):
            t = job_text(job)
            self.assertRegex(t, r"(?m)^    needs: tip$")
            self.assertNotRegex(t, r"(?m)^    if: ")


if __name__ == "__main__":
    unittest.main()
