#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib test <test files…>` — the one entry for running tests (docs/DESIGN-aib-test-entry-shadow-0929.md §3).

the maintainer 09-29: "running tests must (1) enable shadow resume, (2) confirm it is really enabled; only when (1) cannot be
enabled, run the old way."  Every call does three steps:

  1. self-check   shadow_gate.check on the tracer this entry will use (this file's own tree), once per process.
  2. run          self-check passed ⇒ per test: reuse the last green row (reuse_index.decide, with the exact
                  environment the test would get), or run it under the tracer (runner._traced_run, which appends
                  a row to the reuse pool).  Afterwards the pool is *re-read* and the new rows are counted:
                  every test that really ran must have left one.  Claimed on but rows missing ⇒ the run failed.
  3. fall back    only when step 1 failed: run every test plainly (no tracer, nothing recorded) and say why.

Line 1 of the output is always exactly one of three (design §3 table; the words live in i18n `aib.test.line1_*`):
  enabled   shadow resume on and confirmed (reused N / ran M, new rows M)
  fallback  shadow resume not enabled — <stage>: <reason>; everything runs
  short     shadow resume claimed on, but M ran and only K rows were left — failed

Exit codes: 0 all green · 1 at least one red (line 1 still names a row shortfall) · 2 usage ·
            3 all green but rows missing (the run is not trusted: next time these tests cannot be reused).

What is deliberately NOT here (first package): the host lease is not taken (same as a seat running one test
directly today; a batch prints that on line 2); the Claude-seat hook that blocks direct `python3 tests/…`;
the tripwire table of product programs that start tests; the six API seats (design §7 cells 4 and 6).

No off switch for tracing.  `--strict` and `--fresh` both still trace and record; AIB_SEAT_TEST_REUSE=0 and
AIB_WITNESS_SHADOW=0 are ignored here (and say so on stderr).
10-04 (the maintainer approved): a reuse kill switch that still traces/records — AIB_TEST_NO_REUSE=1 acts like --fresh for every
test (why code no-reuse-env); and a per-test marker — a line `# aib: always-run` in the first 40 lines of a test file
means that test is never reused (why code always-run).

Rows are marked source="aib-test" (runner._traced_run row_source=, da0c85c98).  `--timeout N` limits each test to
N seconds (default: runner.DEFAULT_TIMEOUT_S, 30 minutes); a timed-out test is red with rc 124.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY_ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runner          # noqa: E402  _traced_run / _run_one / isolated_test_env / _pool_file / TMP_ROOT
import resume_policy   # noqa: E402  quick | strict
import reuse_index     # noqa: E402  decide
import shadow_gate     # noqa: E402  check
import i18n_lib        # noqa: E402  (runner put src/cockpit on sys.path)
from slow_warn import SLOW_RATIO, SLOW_MIN_EXTRA_S, slow_threshold  # noqa: E402

LOC = i18n_lib.resolve_locale()
# Switches that used to turn shadow resume off.  They are read only to say "this no longer turns it off".
ROW_SOURCE = "aib-test"
RETIRED_SWITCHES = (("AIB_SEAT_TEST_REUSE", "0"), ("AIB_WITNESS_SHADOW", "0"))

_GATE = {}   # one self-check per process (package_trial calls run_tests once per test)

# ★ the maintainer 10-02 常設令:每支測試都記耗時;開跑前先知道它平常多久;超過平常 10% 立即示警、去找問題。
#   平常 = 同一個倉庫(所有工作樹共用的影子紀錄池)最近 BASELINE_RUNS 次「綠、沒逾時」實跑的平均。
#   10% 之外再要求多 SLOW_MIN_EXTRA_S 秒:0.3 秒的測試慢 10% 是 0.03 秒,那是雜訊不是問題。
#   起因:test-verify-bash-child-96b 平常 5–6 秒,10-02 跑了 1093 秒(兩份 bash 轉接器互叫到逾時),
#   整批跑完一個半小時後才有人發現。
BASELINE_RUNS = 5
# 即時計時器從「準備跑這一支」開始算(含建追蹤沙箱),紀錄裡的耗時只算測試本身;差約 1 秒。
# 不補這段,短測試會在跑的當下被誤報(10-02 實測:96 支即時示警 11 次,結尾真慢只有 3 支)。
# 補法:這一批最近幾支量到的「牆鐘 − 紀錄耗時」取最大值,第一支先用預設值。
SETUP_ALLOWANCE_DEFAULT_S = 1.5


def baselines(pool: Path, names):
    """{test: 平均秒};只算綠、沒逾時、真的有跑的列,每支取最近 BASELINE_RUNS 次。讀不到的支不出現。"""
    want, hist = set(names), {}
    try:
        with open(pool, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = r.get("test")
                d = r.get("duration_s")
                if t not in want or r.get("rc") != 0 or r.get("timed_out") or not isinstance(d, (int, float)):
                    continue
                hist.setdefault(t, []).append(float(d))
    except OSError:
        return {}
    return {t: sum(v[-BASELINE_RUNS:]) / len(v[-BASELINE_RUNS:]) for t, v in hist.items() if v}


def _why_text(code, detail=""):
    """影子續跑不能沿用的原因代碼 → 白話(四語);字典沒有的代碼原樣顯示,不吞掉。"""
    if code == "index-degraded" and detail in ("no-rows-files", "no-usable-rows"):
        code, detail = "no-records", ""                  # 還沒跑過 ≠ 紀錄池壞了(第一次在這個倉庫跑)
    if code == "env-fp-changed" and detail:              # 10-06:說得出是哪幾項變了
        code = "env-fp-changed-items"
    key = "aib.test.why." + code.replace("-", "_")      # 字典鍵不收連字號
    txt = i18n_lib.t(key, {"detail": detail}, locale=LOC)
    missing = (not txt) or txt == key or (txt.startswith("!!") and txt.endswith("!!"))
    return code if missing else txt


class _SlowWatch:
    """跑一支測試時在旁邊計時:一過門檻就呼叫 on_slow 一次(即時,不等整批跑完)。"""

    def __init__(self, threshold, on_slow):
        self.threshold, self.on_slow = threshold, on_slow
        self._stop = threading.Event()
        self._t = None
        self.fired = False

    def __enter__(self):
        if self.threshold is not None and self.on_slow is not None:
            self._t = threading.Thread(target=self._wait, daemon=True)
            self._t.start()
        return self

    def _wait(self):
        if not self._stop.wait(self.threshold):
            self.fired = True
            try:
                self.on_slow()
            except Exception:  # noqa: BLE001 — 示警壞了不能讓測試結果壞
                pass

    def __exit__(self, *exc):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=1)
        return False


def T(key, params=None):
    return i18n_lib.t("aib.test." + key, params, locale=LOC)


def all_tests(repo):
    """--all:跟推前檢查同一個母體(pushgate.all_tests:aib 自己的倉只算 tests/test-*.py;一般專案照通用找法,
    但不在 tests/ 底下、檔裡一個測試都沒有的不算 —— 不會把 tools/verify/test_entry.py 這種「檔名像測試」的當測試跑)。
    只帶 tools/verify 的精簡安裝沒有設定那一層 ⇒ []。"""
    try:
        import pushgate
        return sorted(pushgate.all_tests(str(repo)))
    except ImportError:
        return []


# --guard / --scan(10-11 a seat,the maintainer「每席出貨前 5 分鐘先過守衛」):推前檢查最常紅在「守衛」—— 棘輪、單源、lint、
# hygiene 這類掃整棵樹的測試,改了別處也會被它們抓。清單用規則算、不手抄(新加的棘輪自動進來):
#   守衛類(快,約 5–10 分鐘):檔名含 tests-*/ratchet/guard/lint/hygiene/single-source/sst/cabin/isolation/read-set/
#                                seat-address/population/portability/callers/-scan
#   掃樹類(慢,新增 src/ tools/ 檔案時才需要):檔裡 rglob( 或 os.walk( 且提到 tools(登記簿、可攜性那種)
GUARD_NAME_RE = re.compile(r"^test-(tests-|.*(ratchet|guard|lint|hygiene|single-source|-sst|cabin|isolation|read-set|seat-address"
                           r"|population|portability|callers|-scan)).*\.py$")
_SCAN_WALK_RE = re.compile(r"(rglob\(|os\.walk\()")
_SCAN_TOOLS_RE = re.compile(r"(\"tools\"|'tools'|tools/)")


def _test_files(repo):
    """母體沿用推前檢查的找法(pushgate.all_tests):這支檔自己不點名測試檔,仍是「別人交清單的執行器」(test-test-runner-callers t5)。
    回 (相對路徑, Path):相對路徑照 all_tests 給的,不自己重組(子目錄的測試才找得到)"""
    out = []
    for rel in all_tests(repo):
        p = Path(repo, rel)
        if p.is_file() and re.match(r"^test-.*\.py$", p.name):
            out.append((rel, p))
    return sorted(out)


def _git_out(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, p.stdout


def changed_base(repo, base=""):
    """--changed 的比較基準:給了就用;沒給 ⇒ HEAD 與遠端 main(origin/main,沒有再試 github/main)的分岔點。解不出 ⇒ None。"""
    for ref in ([base] if base else ["origin/main", "github/main"]):
        rc, out = _git_out(repo, "merge-base", "HEAD", ref)
        if rc == 0 and out.strip():
            return out.strip(), ref
    return None, base or "origin/main"


def changed_tests(repo, base=""):
    """aib test --changed(10-13 the maintainer 裁,取代 aib deliver):依改動挑測試,挑法與推包推前檢查同一支 select_changed.py。
    改動 = 基準到「工作樹」(已追蹤的,提交了沒都算;select_changed 也比對改到的函式名)∪ 還沒追蹤的新檔(另送一次補挑;
    select_changed 只看 git diff,新加的測試 / 產品檔它看不到 —— 漏挑的樣子是綠的)。
    ★ 走子行程,不 import select_changed(同 lease_note 的 24d 說明:本檔被幾十支測試 in-process 載入)。
    回 (清單, 說明 dict);挑不出來 ⇒ (None, 說明),呼叫端 rc 2,不偷偷跑全套也不偷偷跑窄集合。"""
    mb, ref = changed_base(repo, base)
    info = {"ref": ref, "base": (mb or "")[:9], "untracked": 0, "picked": 0}
    if mb is None:
        info["why"] = "no-base"
        return None, info
    tool = ENTRY_ROOT / "tools" / "verify" / "select_changed.py"
    if not tool.is_file():
        info["why"] = "no-selector"
        return None, info

    proj = _subproject(repo)                         # 開源版測試在 aib/tests:從子專案挑,路徑才對得上(10-24)
    sub = proj != repo
    pre = (Path(proj).resolve().relative_to(Path(repo).resolve()).as_posix() + "/") if sub else ""

    try:
        names = [(proj, n) for n in _select(tool, proj, ["--base", mb])]     # --head 不給 = 跟工作樹比(含沒提交的追蹤檔)
        if sub:                                    # 再從倉根挑一次:⑧ 反查帳本是倉根座標、倉根 tests/ 也會讀 aib/ 的產品檔
            names += [(repo, n) for n in _select(tool, repo, ["--base", mb])]      # (#314 r2 必修:只從子專案挑 ⇒ 0 支假綠)
        rc, out = _git_out(repo, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard")   # 倉根列
        new = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
        if sub:
            outside = [n for n in new if not n.startswith(pre)]
            if outside:                                   # 子專案外的新檔:挑不到它的消費者 ⇒ 量不到,不拿窄集合跑
                raise RuntimeError("untracked outside subproject %s: %s" % (pre, ", ".join(outside[:5])))
        info["untracked"] = len(new)
        if new:
            names += [(proj, n) for n in _select(tool, proj, ["--changed", "-"], [n[len(pre):] for n in new])]
            if sub:
                names += [(repo, n) for n in _select(tool, repo, ["--changed", "-"], new)]
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        info["why"] = "select-failed: %s" % e
        return None, info
    if not sub:
        have = set(all_tests(proj))
        picked = sorted(dict.fromkeys(n for _b, n in names if n in have))   # 一般專案的測試不一定在 tests/(#314 r4)
        info["picked"] = len(picked)
        return picked, info
    root_tests = [str((Path(repo) / n).resolve()) for n in all_tests(repo) if n.startswith("tests/")]
    have = {str((Path(proj) / n).resolve()) for n in all_tests(proj)} | set(root_tests)
    picked = list(dict.fromkeys(a for a in (str((Path(b) / n).resolve()) for b, n in names) if a in have))
    info["root_picked"] = len([a for a in picked if a in root_tests])
    # 倉根 tests/ 是 test_*.py(底線),select_changed 只認 test-*.py ⇒ 倉根那次靜態規則一支都挑不到,只剩本機量測帳本(⑧);
    #   CI / 新機器沒帳本 ⇒ 0 支假綠(#314 r3 必修)。子專案有改動就把倉根 tests/ 全併進來(目前 22 支,方向是多跑)
    rc, out = _git_out(repo, "-c", "core.quotePath=false", "diff", "--name-only", mb)
    if (rc != 0 or out.strip() or new) and root_tests:
        picked += root_tests
        info["root_all"] = len(root_tests)
    picked = sorted(dict.fromkeys(picked))
    info["picked"] = len(picked)
    return picked, info                               # 子專案模式回絕對路徑(下游照倉根 / 目前目錄換算)


def _select(tool, base_dir, args, stdin_lines=None):
    """跑一次 select_changed --print-tests(--repo base_dir);回 base_dir 相對的路徑。rc≠0 ⇒ RuntimeError(呼叫端回量不到)。"""
    kw = {"input": "\n".join(stdin_lines) + "\n"} if stdin_lines is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run([sys.executable, str(tool), "--repo", str(base_dir), "--print-tests", *args],
                       capture_output=True, text=True, timeout=1800, **kw)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[-300:])
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


def _subproject(repo):
    """目前目錄往上走到倉根,第一個自帶 tools/verify/select_changed.py 的資料夾(= 倉裡的 aib 套件本身,例如開源版的 aib/);
    一般專案沒有這個記號 ⇒ 倉根本身(只看 tests/ 會把 src/foo/tests 誤當子專案、漏挑倉根的測試;#314 審查 A2)。"""
    try:
        here = Path.cwd().resolve()
        root = Path(repo).resolve()
        here.relative_to(root)
    except (OSError, ValueError):
        return Path(repo)
    for d in (here, *here.parents):
        if d == root:
            break
        if (d / "tools" / "verify" / "select_changed.py").is_file() and (d / "tests").is_dir():
            return d
    return Path(repo)


def guard_tests(repo):
    """守衛類:只看檔名(規則 GUARD_NAME_RE)"""
    return [rel for rel, p in _test_files(repo) if GUARD_NAME_RE.match(p.name)]


def scan_tests(repo):
    """掃樹類:檔裡走整棵樹(rglob / os.walk)且提到 tools 的;不含已在守衛類的"""
    out = []
    for rel, p in _test_files(repo):
        if GUARD_NAME_RE.match(p.name):
            continue
        try:
            s = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _SCAN_WALK_RE.search(s) and _SCAN_TOOLS_RE.search(s):
            out.append(rel)
    return out


def step_summary(lines, path=None):
    """GitHub Actions:把「沒有 aib → 有 aib」那幾行附到這次執行的摘要頁(GITHUB_STEP_SUMMARY),用戶在網頁上就看得到省了多少。
    沒有那個環境變數 ⇒ 不寫;寫不進去不影響這一輪。"""
    path = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not path or not lines:
        return False
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("```\n" + "\n".join(lines) + "\n```\n")
        return True
    except OSError:
        return False


def summary_extra(vs, tests, top=20):
    """只給 GitHub 執行摘要頁的幾行(終端不印):計費分鐘(GitHub 每個工作進位到整分鐘,這裡只算測試那段)、
    給不認識 aib 的貢獻者看的「沿用是什麼意思」、這次實跑的是哪幾支。"""
    import math
    out = [T("vs_billed", {"total": int(math.ceil(vs["without_s"] / 60.0)),
                           "actual": int(math.ceil(vs.get("wall_s", vs["with_s"]) / 60.0))}),
           T("vs_meaning")]
    ran = [r["name"] for r in tests if not r.get("reused") and not r.get("unverified")]
    if ran:
        more = len(ran) - top
        out.append(T("vs_ran_list", {"n": len(ran), "items": "、".join(ran[:top]) + (" …(+%d)" % more if more > 0 else "")}))
    return out


def versus(tests, wall_s, lease_wait_s=0, mem_wait_s=0):
    """10-07 the maintainer:讓用戶看到「沒有 aib → 有 aib」省了多少。沒有 aib = 每支實跑秒數相加(這一輪實跑的用這一輪的、
    沿用的用它被沿用那一列的實跑秒數;都是 aib 底下量的,含追蹤的額外時間);有 aib = 這一輪實際牆鐘。
    沿用的沒有秒數 ⇒ 不算進「沒有 aib」(只會少算,不會多算),另回支數。沒驗的不算(支數另回 unverified)。
    10-14(a seat 派):with_s = 牆鐘扣掉排隊等機器與等記憶體(那段沒在跑測試,算進去看起來像 aib 變慢);
    含等待的牆鐘另回 wall_s。"""
    without, unknown, ran, reused, unverified = 0.0, 0, 0, 0, 0
    for r in tests:
        if r.get("unverified"):
            unverified += 1
            continue
        if r.get("reused"):
            reused += 1
            d = r.get("prev_duration_s")
        else:
            ran += 1
            d = r.get("elapsed_s")
        if isinstance(d, (int, float)) and d > 0:
            without += float(d)
        else:
            unknown += int(bool(r.get("reused")))
    wall = max(0.0, float(wall_s or 0))
    # 不到 1 秒的等待不印也不扣(跟 ⏳ 那行同一個門檻,印的跟算的一致)
    lw, mw = (w if w >= 1 else 0.0 for w in (max(0.0, float(lease_wait_s or 0)), max(0.0, float(mem_wait_s or 0))))
    run = max(0.0, wall - lw - mw)
    return {"without_s": round(without, 1), "with_s": round(run, 1), "saved_s": round(max(0.0, without - run), 1),
            "wall_s": round(wall, 1), "lease_wait_s": round(lw, 1), "mem_wait_s": round(mw, 1),
            "ran": ran, "reused": reused, "unknown": unknown, "unverified": unverified}


def _test_stats_path():
    import shadow_tracer
    return shadow_tracer.shadow_dir_default() / "test-stats.jsonl"


def record_versus(repo, vs, path=None):
    """這一輪記一行(累計用);寫不進去不影響這一輪。回這個專案在這一行之前的紀錄(由舊到新)。
    ★ 10-14 起 with_s 扣掉排隊等機器 / 等記憶體(含等待的另記 wall_s);之前的列 with_s 含等待 ⇒ 📈 累計的「實際」只會偏高。"""
    path = Path(path) if path else _test_stats_path()
    rid = reuse_index.repo_identity(str(repo)) or ""
    before = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("repo") == rid:
                before.append(r)
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(vs, repo=rid, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                                ensure_ascii=False) + "\n")
    except OSError:
        pass
    return before


def dur_text(seconds):
    s = float(seconds or 0)
    if s < 60:
        return i18n_lib.t("aib.pushgate.unit_s", {"n": "%.1f" % s}, locale=LOC)
    if s < 3600:
        return i18n_lib.t("aib.pushgate.unit_min", {"n": ("%.1f" % (s / 60)).rstrip("0").rstrip(".")}, locale=LOC)
    return i18n_lib.t("aib.pushgate.unit_h", {"n": ("%.1f" % (s / 3600)).rstrip("0").rstrip(".")}, locale=LOC)


def versus_lines(vs, before, who=None, causes=None, required=None):
    """跑完印的 🧮 樹狀(純函式,好測)。10-14 v2(the maintainer 看過的示意 ~/.ez2-aib/pending/MOCK-shadow-explain-v2-8f366-1014.md):
    頂行寫誰跑的、哪棵樹;⚡ 是含等待的牆鐘,底下拆排隊等機器 / 等記憶體 / 真正在跑;🔁 母體單一(整支跳過的另註明不算);
    📊 扣掉排隊再比,沒省到就寫多花多少(那是 aib 追蹤讀檔的額外負擔)。
    who = 「@席碼 · ~/樹」(認不出 ⇒ None,頂行不寫);causes = 已翻好的重跑原因(最多前三);
    required 給清單 ⇒ 把回報裡必須原樣出現的行(🐢 ⚡ ▶ 📊 ⇒)放進去(守門用)。"""
    if vs["without_s"] <= 0:
        return []
    req = required if required is not None else []
    # 版面同回報的樹(aib-report):第一項 ╭▸、中間 ├▸、最後 ╰▸、子項掛在 │ 底下;每行一個圖示(the maintainer 10-07:要曲線與圖示)
    out = [T("vs_head_who", {"who": who}) if who else T("vs_head")]
    out.append(T("vs_without", {"total": dur_text(vs["without_s"]), "count": vs["ran"] + vs["reused"]}))
    req.append(out[-1])
    if vs["unknown"]:
        out.append(T("vs_unknown", {"count": vs["unknown"]}))
    wall = vs.get("wall_s", vs["with_s"])
    out.append(T("vs_actual", {"actual": dur_text(wall)}))
    req.append(out[-1])
    waits = [(k, vs.get(f) or 0) for k, f in (("vs_wait_lease", "lease_wait_s"), ("vs_wait_mem", "mem_wait_s"))
             if (vs.get(f) or 0) >= 1]
    for k, w in waits:      # 10-14:排隊的時間沒在跑測試,拆開寫,下面 📊 扣掉再比
        ahead = vs.get("lease_ahead") if k == "vs_wait_lease" else None
        out.append(T(k + ("_ahead" if ahead else ""), {"t": dur_text(w), "ahead": ahead}))
    if waits:
        out.append(T("vs_run", {"t": dur_text(vs["with_s"])}))
        req.append(out[-1])
    last = before[-1] if before else {}
    if vs.get("tracer") and last.get("tracer") and last["tracer"] != vs["tracer"] and not vs["reused"]:
        # aib 換版(追蹤器程式不同)⇒ 舊紀錄全部作廢、這一輪全部實跑;不說的話用戶只看到「這次怎麼沒省」
        out.append(T("vs_aib_changed"))
    split = {"count": vs["reused"], "n": vs["ran"], "total": vs["reused"] + vs["ran"], "u": vs.get("unverified") or 0}
    out.append(T("vs_split_unverified" if split["u"] else "vs_split", split))   # 母體單一:整支跳過的另註明不算
    if causes:
        out.append(T("vs_causes", {"what": T("vs_wait_sep").join(causes[:3])}))
    rows = [r for r in before if isinstance(r.get("without_s"), (int, float))] + [vs]
    tot = sum(float(r["without_s"]) for r in rows)
    act = sum(float(r.get("with_s") or 0) for r in rows)
    out.append(T("vs_total" if tot >= act else "vs_total_plain", {"n": len(rows), "total": dur_text(tot), "actual": dur_text(act),
                                                                  "seconds": dur_text(max(0.0, tot - act))}))
    out.append(T("vs_compare_waited" if waits else "vs_compare",
                 {"without": dur_text(vs["without_s"]), "actual": dur_text(vs["with_s"])}))
    req.append(out[-1])
    if not before and not vs["reused"]:
        out.append(T("vs_first"))
    elif vs["with_s"] > vs["without_s"]:      # the maintainer 10-07:沒省到只講沒省到,不寫「多花」(a seat 10-14 裁:照舊)
        out.append(T("vs_nosave"))
    else:
        pct = 100.0 * vs["saved_s"] / vs["without_s"]
        out.append(T("vs_saved", {"seconds": dur_text(vs["saved_s"]), "pct": "%.0f" % pct}))
    req.append(out[-1])
    return out


def rerun_causes(tests, top=3):
    """這一輪實跑的那幾支「為什麼不能沿用」:原因白話(支數),多的在前。"""
    # 10-14 公開版新用戶走查:以前只拿 why 去翻、不帶 detail ⇒ 新機器第一次跑(index-degraded:no-rows-files)
    #   印成「紀錄池讀不了:,…quarantine」(冒號後空白、還叫人修壞列)。紀錄池那一類要帶 detail 才分得出
    #   「還沒有紀錄」與「真的讀壞了(附原因)」;其他原因的 detail 每支不同(哪個檔改了),照舊只按原因合併。
    import collections
    c = collections.Counter((r.get("why"), str(r.get("why_detail") or "") if r.get("why") == "index-degraded" else "")
                            for r in tests if not r.get("reused") and not r.get("unverified") and r.get("why"))
    return ["%s(%d)" % (_why_text(code, detail), n) for (code, detail), n in c.most_common(top)]


_LAST_ADVICE = {}


def _who(repo):
    """🧮 頂行:「@席碼 · ~/樹」;認不出席 ⇒ 只寫樹。"""
    try:
        import shadow_explain
        seat = shadow_explain._seat()
    except Exception:  # noqa: BLE001
        seat = None
    tree = str(repo)
    home = str(Path.home())
    if tree == home or tree.startswith(home + os.sep):
        tree = "~" + tree[len(home):]
    return ("@%s · %s" % (seat, tree)) if seat else tree


def _prev_actions():
    """這一席上一輪留下的 💡 建議(next_actions);讀不到 ⇒ []。"""
    try:
        import shadow_explain
        d = shadow_explain.load()
        return [x for x in (d or {}).get("next_actions") or [] if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        return []


def _advice(tests, lease, a, prev_actions):
    """📋 上一輪建議逐條判 + 💡 這一輪的建議(v2.1)。壞了只印一行原因,不影響結果。"""
    try:
        import shadow_advice
        flag = "--strict" if a.strict else "--fresh" if a.fresh else ("AIB_TEST_NO_REUSE=1" if _no_reuse_env() else None)
        ctx = {"lease_wait_s": lease.get("waited") or 0, "lease_why": lease.get("why"), "ahead": lease.get("ahead"),
               "tests": tests, "no_reuse": flag}
        acts = shadow_advice.build(ctx, dur_text)
        _LAST_ADVICE["acts"] = acts
        return shadow_advice.judge_lines(shadow_advice.judge(prev_actions, ctx, dur_text)) + shadow_advice.advice_lines(acts)
    except Exception as e:  # noqa: BLE001
        return ["shadow-advice: %s: %s" % (type(e).__name__, e)]


def shadow_explain_emit(vs, tests, fresh=False, tree=None, required=None, next_actions=None):
    """the maintainer 10-13 19:4x:「如果開影子續跑,一定要列出說明」—— 收尾一定印、一定記(tools/verify/shadow_explain.py)。
    推前檢查的分片(--resume)不走這裡,由 pushgate 自己印整輪的那一段。"""
    try:
        import shadow_explain
        shadow_explain.emit("aib test", dur_text, reused=vs["reused"], skipped=0, ran=vs["ran"],
                            without_s=vs["without_s"], actual_s=vs.get("wall_s", vs["with_s"]),
                            lease_wait_s=vs.get("lease_wait_s") or 0, mem_wait_s=vs.get("mem_wait_s") or 0,
                            unverified=vs.get("unverified") or 0, causes=rerun_causes(tests), fresh=fresh, tree=tree,
                            required=required, next_actions=next_actions,
                            printer=(lambda _ln: None) if tree and not fresh else print)   # v2:有 🧮 樹狀就不再印純文字那段(樹狀已全包);--fresh 那行照印
    except Exception as e:  # noqa: BLE001
        print("shadow-explain: %s: %s" % (type(e).__name__, e))


def self_check():
    """shadow_gate.check on the tracer the runs will load: runner/shadow_tracer of ENTRY_ROOT."""
    if "res" not in _GATE:
        try:
            _GATE["res"] = shadow_gate.check(ENTRY_ROOT)
        except Exception as exc:  # a crashing gate is a failed gate, never a passed one
            _GATE["res"] = {"ok": False, "stage": "setup", "reason": "%s: %s" % (type(exc).__name__, exc)}
    return _GATE["res"]


def _pool_rows_after(pool: Path, offset: int):
    """Rows appended to the pool file after byte `offset` (an unreadable pool = no rows)."""
    try:
        with open(pool, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return []
    out = []
    for line in jsonl_safe.split_lines(data.decode("utf-8", "replace")):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _pool_size(pool: Path) -> int:
    try:
        return pool.stat().st_size
    except OSError:
        return 0


def would_reuse(repo, name):
    """True when run_tests would hand back the last green row for `name` without running it.  For callers that
    must decide whether to take the host lease first (API seats).  Same self-check, mode and environment as
    run_tests; the pool can still change between this answer and the run (then run_tests really runs it)."""
    repo = Path(repo).resolve()
    mode, _source, _path = resume_policy.current_mode(str(repo))
    if mode == "strict" or not self_check().get("ok"):
        return False
    home = Path(tempfile.mkdtemp(prefix="aib-test-home-", dir=runner.TMP_ROOT))
    try:
        env = runner.isolated_test_env(home, repo)
        idx = reuse_index.build_index([], repo=str(repo))
        verdict, _why, _detail = idx.decide(str(repo), name, changed=[], environ=env)
        return verdict == "reuse"
    finally:
        shutil.rmtree(home, ignore_errors=True)


_RESUME_NAME_RE = __import__("re").compile(r"[A-Za-z0-9._-]{1,64}")
_JOURNAL_FIELDS = ("test", "rc", "tree", "fresh")


def _journal_path(repo, name):
    """--resume 的批次日誌:跟紀錄池放在同一個目錄,`batch-<名>.jsonl`。"""
    return runner._pool_file(repo).parent / ("batch-%s.jsonl" % name)


def load_journal(path, tree, fresh, rows_out=None):
    """10-04 the maintainer「把可接續做成程式化」:讀批次日誌,回 (上次已跑綠的測試集合, 問題或 None)。
    判準照 tools/governance/suite_resume.py:同一棵樹、上次綠、同一種跑法(--fresh 與否)才算;
    同一支多筆以最後一筆為準(後來紅了就不算)。任何一行壞掉 ⇒ 整份不用(回空集合 + 問題),全部重跑 —— 安全的一側。
    現樹 sha 量不到(空字串)⇒ 一律不接續。
    rows_out(dict)給了 ⇒ 每支最後一筆日誌列放進去(resume_blocked 要看它的時間與環境指紋)。"""
    if not tree:
        return set(), "tree-unknown"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set(), None
    except OSError as exc:
        return set(), "unreadable: %s" % exc
    last = {}
    for n, line in enumerate(jsonl_safe.split_lines(text), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return set(), "bad-line %d" % n
        if not isinstance(row, dict) or any(k not in row for k in _JOURNAL_FIELDS):
            return set(), "bad-line %d" % n
        if row["tree"] == tree and bool(row["fresh"]) == bool(fresh):
            last[row["test"]] = row["rc"]
            if rows_out is not None:
                rows_out[row["test"]] = row
    return {t for t, rc in last.items() if rc == 0}, None


# 10-15 A1:接續只是「同一批被打斷後再下一次」,不是第二套沿用。以前只看「同一棵樹 + 同一種跑法」,沒有時間、環境、
#   作廢、嚴格模式與不准沿用的開關 ⇒ 推前檢查每輪都帶 --resume pushgate、日誌從不過期 ⇒ 同一顆 HEAD 幾天後、
#   環境換了、抽查寫了作廢列,照樣拿舊綠。現在接續也要過這幾關,過不了的照一般規則(沿用判斷或實跑)。
RESUME_MAX_AGE_H = 12.0


def resume_blocked(test, row, *, strict, no_reuse, always_run, env_fp_now, idx, now_utc, max_age_h):
    """一支上次已跑綠的日誌列還能不能接續:能 ⇒ None;不能 ⇒ 原因代碼。任何一項量不到都算不能(多跑那一側)。"""
    import datetime as _dt
    if strict:
        return "strict"
    if no_reuse:
        return "no-reuse-env"
    if always_run:
        return "always-run"
    born = reuse_index.row_time_utc(row or {})
    if born is None:
        return "resume-age-unknown"
    age_h = (now_utc - born).total_seconds() / 3600.0
    if age_h < -0.1:
        return "resume-from-future"
    if age_h > max_age_h:
        return "resume-expired"
    if not env_fp_now or row.get("env_fp") != env_fp_now:
        return "resume-env-changed"
    tu = row.get("time_utc")
    for v in (getattr(idx, "voids", None) or ()):
        c = v.get("cond") or {}
        if not (c.get("all") is True or c.get("test") == test or not c.get("test")):
            continue
        if v.get("before_utc"):
            if v["before_utc"] > tu:
                return "resume-voided"
        elif str(v.get("before") or "") > str(row.get("time") or ""):
            return "resume-voided"
    for r in (getattr(idx, "history", None) or {}).get(test) or ():
        if not reuse_index._walkable(r) and reuse_index._order_key(r) > tu:
            return "resume-newer-red"
    return None


def _resume_env_fp(repo, env):
    """接續比的環境指紋 = 沿用判斷用的那一份(reuse_index._decide_row 同算法);算不出來回 None(⇒ 不接續)。"""
    try:
        cenv = runner.st.child_env(Path(repo), Path("/tmp/.ez2-verify-reuse-probe"), env)
        return runner.st.env_fingerprint(cenv, repo=str(repo))[0]
    except Exception:  # noqa: BLE001
        return None


JOURNAL_MAX_BYTES = 16 * 1024 * 1024     # 10-13:日誌多記重跑理由 ⇒ 加上限輪替(以前從不輪替,已 4.7MB)


def _rotate_journal(f, path):
    """持鎖中:超過上限 ⇒ 只留後半段(從一行開頭切),原檔就地改寫(不換 inode,等鎖的別人寫進來的列不會掉)。
    --resume 只看同一棵樹最後幾筆,砍舊的只會讓更早那批不能接續 —— 安全的一側(多跑)。"""
    try:
        size = os.fstat(f.fileno()).st_size
        if size <= JOURNAL_MAX_BYTES:
            return
        with open(path, "rb") as r:
            r.seek(size - JOURNAL_MAX_BYTES // 2)
            tail = r.read()
        cut = tail.find(b"\n")
        tail = tail[cut + 1:] if cut >= 0 else b""
        with open(path, "r+b") as w:
            w.seek(0)
            w.write(tail)
            w.truncate()
    except OSError:
        pass


def _heal_own_pool(repo, idx, live=None):
    """10-15 F6:本專案自己的池有壞行(被殺在半列、當機)⇒ 整份索引降級、每一輪都全部重跑,而且自己好不了
    (修法 reuse_index quarantine 只有人手跑)。這裡自動跑同一個修法:壞行搬進 .bad、那幾支留必跑墓碑(認不出是哪支
    ⇒ 檔內每支都留),然後重讀。只修本專案的池(別台收進來的 shared-*、別的專案不碰);修不了就照舊降級全跑。"""
    pool = runner._pool_file(repo)
    if not str(idx.degraded or "").startswith("bad-jsonl:%s:" % pool):
        return idx
    try:
        rep = reuse_index.quarantine(pool, apply=True)
    except Exception:  # noqa: BLE001 - 修不了就照舊(降級 = 全部必跑,多跑那一側)
        return idx
    if not rep.get("applied"):
        return idx
    if live is not None:
        live("pool_healed", {"n": len(rep.get("bad_lines") or []), "tests": len(rep.get("tombstones") or [])})
    return reuse_index.build_index([], repo=str(repo))


def _jstamp(env_fp):
    """日誌列多記的兩欄(10-15 A1):UTC 時間(接續有期限)與環境指紋(環境變了不接續)。"""
    return {"time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "env_fp": env_fp or ""}


def append_journal(path, row):
    import fcntl
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(jsonl_safe.dumps(row) + "\n")
        f.flush()
        _rotate_journal(f, path)
        fcntl.flock(f, fcntl.LOCK_UN)


def _tracked_dirty(repo):
    """被 git 追蹤、內容跟 HEAD 不同的檔 → {路徑: 內容雜湊}(刪掉的記 None)。量不到回 None。"""
    try:
        p = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "HEAD"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = {}
    for rel in [x for x in p.stdout.splitlines() if x.strip()]:
        f = Path(repo) / rel
        try:
            out[rel] = __import__("hashlib").sha256(f.read_bytes()).hexdigest()
        except OSError:
            out[rel] = None
    return out


def _batch_dir(repo):
    """同一棵工作樹上正在跑的 aib test 批次登記處(用工作樹的實際路徑分,不用倉庫身分 —— 各工作樹共用倉庫身分)。"""
    import hashlib
    tag = hashlib.sha256(str(Path(repo).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(runner.TMP_ROOT) / "aib-test-batches" / tag


_TMP_LEAKS = []      # 10-08:[(測試, 留下幾項)] —— 跑完還留在它自己暫存資料夾裡的東西(總表列出,幫使用者找會漏檔的測試)


RUN_PREFIX = "aibt-"   # 每支測試自己的暫存資料夾(aib tmp 的 PREFIXES 也列它)


def own_tmp_root():
    """aib 自己在暫存區的根(<tmp>/ez2-aib-tmp-<uid>/);aib tmp 清的就是這裡。
    名字刻意不是 aib-<數字>:那是駕駛艙艙名的格式(test-cabin-resolution R8:全倉不准第二處自己拼艙名)。"""
    d = Path(runner.TMP_ROOT) / ("ez2-aib-tmp-%d" % os.getuid())
    d.mkdir(exist_ok=True)
    # 0711:別人走得進去、列不出內容。測試常切成一般身分(nobody)跑被測程式,治具在這底下要走得到 ——
    #   以前治具直接放在 /tmp(1777,還列得出來),這樣只比以前嚴,不會讓那些測試多紅(10-08 fail-open-lint 實踩)
    os.chmod(d, 0o711)
    return d


@contextlib.contextmanager
def _own_home(repo, env, name=None):
    """每支測試自己一個全新的 HOME(10-05)。
    10-08(/tmp 方案第 1 層):也給它自己一個暫存資料夾(TMPDIR,AIB_TEST_TMPDIR 給寫死 /tmp 的治具),跑完就刪;
      刪之前數一下裡面還剩什麼 —— 測試自己沒清的,記在它名下(總表列出)。TMPDIR 本來就不進環境指紋,換掉不會讓鑰匙變。
    以前一批共用一個 HOME:前一支留在 HOME 的東西會改變後一支的結果(推前檢查裡 b184 第 8 格時紅時綠 ——
    前面的測試建了 ~/.ez2-aib,它就讀到「有狀態根、沒有席位」)。更糟的是那個綠會被記下來沿用,
    而鑰匙裡沒有「前面跑了誰」。HOME 本來就不進環境指紋,所以換掉不會讓鑰匙變。"""
    home = Path(tempfile.mkdtemp(prefix="aib-test-home-", dir=runner.TMP_ROOT))
    # 直接放在暫存根、名字盡量短(/tmp/aibt-xxxxxxxx):Unix socket 路徑有長度上限(Linux 108、Mac 104),
    #   10-08 放在 <tmp>/ez2-aib-tmp-<uid>/run-* 底下多了 27 字,grokbot 信箱測試的 dm socket 就連不上。aib tmp 照名字開頭認它。
    tmp = Path(tempfile.mkdtemp(prefix=RUN_PREFIX, dir=runner.TMP_ROOT))
    os.chmod(tmp, 0o711)
    reaped_before = len(runner.REAPED)
    try:
        runner.isolated_test_env(home, repo)
        yield dict(env, HOME=str(home), TMUX_TMPDIR=str(home), TMPDIR=str(tmp), AIB_TEST_TMPDIR=str(tmp))
    finally:
        # 10-08 實踩:測試開了真的 tmux 沒關 ⇒ 伺服器在背景跑了 13 小時、吃掉約 8 GB,推前檢查兩次被系統砍(OOM)。
        #   追蹤那條跑法已經照追蹤檔路徑收(runner._traced_run);這裡再照「這支自己的暫存資料夾」收一次 ——
        #   兩條跑法(含自檢沒過的備援跑法)都涵蓋,路徑是隨機的,不會碰到別支、別人的程序。收掉幾個,記在它名下。
        runner.reap_leftovers(str(tmp), var="AIB_TEST_TMPDIR", dirs=(tmp, home))   # 10-11:macOS 27 改認抓著這兩個資料夾的
        procs = len(runner.REAPED) - reaped_before
        if procs and name:
            _TMP_LEAKS.append((name + " " + T("tmp_leak_procs"), procs))
        shutil.rmtree(home, ignore_errors=True)
        try:
            names = sorted(os.listdir(tmp))
        except OSError:
            names = []
        left = len(names)
        if left and name:
            _TMP_LEAKS.append((name, left))
            _note_leak(name, names, repo)
        shutil.rmtree(tmp, ignore_errors=True)


LEAK_LOG_MAX = 512 * 1024


def _note_leak(name, names, repo=None):
    """10-08:總表只說「留了 3 項」查不出是哪 3 項 ⇒ 把名字記到 ~/.ez2-aib/test-leaks.jsonl(每支最多 10 個,檔超過 512 KB 只留後半)。
    repo = 哪個工作區跑的(推包與手動同時跑時分得出來)。記不下來不影響測試結果。"""
    log = Path.home() / ".ez2-aib" / "test-leaks.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > LEAK_LOG_MAX:
            data = log.read_bytes()
            log.write_bytes(data[len(data) // 2:].split(b"\n", 1)[-1])
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "repo": str(repo or ""), "test": name, "n": len(names), "names": names[:10]},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def _register_batch(repo):
    d = _batch_dir(repo)
    try:
        d.mkdir(parents=True, exist_ok=True)
        marker = d / str(os.getpid())
        marker.write_text(str(os.getpid()), encoding="utf-8")
        return marker
    except OSError:
        return None


def _other_batches(repo):
    """同一棵樹上還有別的活著的批次?(登記檔的 pid 還活著才算;當掉留下的登記檔不算)"""
    try:
        names = [f.name for f in _batch_dir(repo).iterdir()]
    except OSError:
        return False
    for n in names:
        if not n.isdigit() or int(n) == os.getpid():
            continue
        try:
            os.kill(int(n), 0)
            return True
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
    return False


def _backup_before_restore(repo, rels):
    """10-07(雲端實踩:aib test 跑的時候在同一棵樹改檔,被當成測試弄髒的、整段還原掉):還原前先把現在的內容抄一份到
    ~/.ez2-aib/tree-guard-backup/<樹名>-<時間>-<pid>/,路徑照倉內相對路徑。被判成「測試弄髒」的,可能是使用者自己同時間改的。
    刪掉的檔沒有內容可抄(git 裡還有)。抄不了 ⇒ 回 None,呼叫端就不還原(寧可後面的測試在改過的樹上跑,也不丟人家的改動)。"""
    dest = Path.home() / ".ez2-aib" / "tree-guard-backup" / ("%s-%s-%d" % (Path(repo).resolve().name,
                                                                         time.strftime("%Y%m%d-%H%M%S"), os.getpid()))
    try:
        for rel in rels:
            src = Path(repo) / rel
            if not src.is_file():
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(out))
    except OSError:
        return None
    return dest


def _tree_guard(repo, baseline, result, live):
    """10-04:測試跑完,被追蹤的檔多了改動(批次開始時沒有、或內容又變了)⇒ 這支判紅、說出是哪些檔,並當場還原,
    免得後面的測試都在測被改壞的樹(雲端實踩:自刀直接改寫真樹、跑到一半容器重開,finally 沒跑)。
    baseline 量不到(不是 git 倉)⇒ 不檢查。只還原「批次開始時乾淨」的檔;開始前就有改動的只報不動。"""
    if baseline is None:
        return
    now = _tracked_dirty(repo)
    if now is None:
        return
    changed = sorted(rel for rel, h in now.items() if baseline.get(rel, "<clean>") != h)
    if not changed:
        return
    files = ", ".join(changed[:5]) + (" …" if len(changed) > 5 else "")
    if _other_batches(repo):
        # ★ 10-04(full4 三份並行實踩):同一棵樹上別批的「原地落刀」測試正在改檔,剛好這支跑完 ⇒ 被算到這支頭上、
        #   還被還原 ⇒ 誤紅這支、又弄壞那支的刀。同樹有別批在跑時歸咎不了 ⇒ 不判紅、不還原;
        #   但這支跑的時候樹被改過,它的綠不能當證據 ⇒ 照樣作廢池裡那列綠(下次重跑)。
        reuse_index.write_void(runner._pool_file(repo), {"test": result.get("name", "?"), "green_only": True},
                               "tree-changed-shared:" + files, slack_s=2)
        result["output"] = (result.get("output") or "") + "\n" + T("tree_shared", {"files": files}) + "\n"
        result["tree_shared"] = changed
        if live is not None:
            live("tree_shared", {"test": result.get("name", "?"), "files": files})
        return
    restorable = [rel for rel in changed if rel not in baseline]
    backup = None
    if restorable:
        backup = _backup_before_restore(repo, restorable)
        if backup is not None:
            subprocess.run(["git", "-C", str(repo), "checkout", "--"] + restorable, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60)
    result["rc"] = result.get("rc") or 1
    result["output"] = (result.get("output") or "") + "\n" + T("tree_dirtied", {"files": files}) + "\n"
    if restorable:
        result["output"] += (T("tree_backup", {"dir": str(backup)}) if backup is not None else T("tree_backup_failed")) + "\n"
        result["tree_backup"] = str(backup) if backup is not None else None
    result["tree_dirtied"] = changed
    # ★ 10-04(full4 第二輪實踩):執行器在測試跑完當下就把這支的列寫進紀錄池(rc=0 綠);這裡才判紅 ⇒ 池裡那列
    #   還是綠 ⇒ 下一輪沿用 = 把紅藏成綠(social-context-callpoint 改了 src/routing/an internal service.py)。
    #   ⇒ 同一支追加一筆作廢列(只打綠列);時間戳只到秒,剛寫的列可能同一秒 ⇒ 作廢點往後留 2 秒(多重跑的方向)。
    #   寫不進去就讓整批炸(不准靜默留下那列綠)。
    reuse_index.write_void(runner._pool_file(repo), {"test": result.get("name", "?"), "green_only": True},
                           "tree-dirtied:" + files, slack_s=2)
    if live is not None:
        live("tree_dirtied", {"test": result.get("name", "?"), "files": files})
        if restorable:
            if backup is not None:
                live("tree_backup", {"dir": str(backup)})
            else:
                live("tree_backup_failed", {})


_SKIP_UNITTEST = re.compile(r"\bskipped=(\d+)")
_SKIP_PYTEST = re.compile(r"\b(\d+) skipped\b")


def skipped_cells(output):
    """10-04:綠燈裡被跳過的格數(unittest「OK (skipped=N)」/ pytest「N skipped」);看不到 ⇒ 0。
    雲端實量:835 支綠裡 46 支帶跳過、共 183 格,以前畫面只寫「綠」—— 沒驗的要跟驗過的一樣顯眼。"""
    text = output or ""
    m = _SKIP_UNITTEST.findall(text) or _SKIP_PYTEST.findall(text)
    return int(m[-1]) if m else 0


def _live_done(live, r):
    """10-04 the maintainer「為什麼不能一紅就知」:每支實跑一結束就即時說綠或紅(以前要等整批跑完才印總表)。"""
    # ★ 10-06 a seat(Mini 推 v0.71.0 第一包被擋):全部格跳過的那支在這裡 rc 已是 None、unverified 已是 True
    #   (_mark_all_skipped 先跑),舊寫法照樣印「✗ 紅 … rc=None」—— 總表後來會改成「⊘ 沒驗」,但 pushgate --fail-fast
    #   盯的是這一行,看到就把三份全停(EARLY_RED),ship.py 再把它判成「只在這次紅」拒推。沒驗的要印成沒驗,不是紅。
    if r.get("unverified"):
        live("unverified", {"test": r.get("name", "?"), "what": " ".join(r.get("needs") or [])})
        return
    el = r.get("elapsed_s")
    params = {"test": r.get("name", "?"), "rc": r.get("rc"),
              "elapsed": "%.1f" % el if isinstance(el, (int, float)) else "?"}
    live("done_green" if r.get("rc") == 0 else "done_red", params)


ALWAYS_RUN_MARK = re.compile(r"^\s*#\s*aib:\s*always-run\b", re.M)


def _always_run_marked(repo, name):
    """測試檔前 40 行有 `# aib: always-run` ⇒ 永遠實跑(讀不到檔 ⇒ 當作沒標,照一般規則)。"""
    try:
        with open(Path(repo) / name, encoding="utf-8", errors="replace") as fh:
            head = "".join(next(fh, "") for _ in range(40))
    except OSError:
        return False
    return bool(ALWAYS_RUN_MARK.search(head))


def _no_reuse_env(environ=None):
    """總開關 AIB_TEST_NO_REUSE=1:這一輪全部實跑(照樣追蹤、照樣留紀錄)。"""
    return str((environ if environ is not None else os.environ).get("AIB_TEST_NO_REUSE") or "") == "1"


def explain(repo, tests, *, strict=None, environ=None):
    """aib test --why(10-04):每支測試「這一輪會不會沿用、不沿用卡在哪個輸入」。不跑測試、不寫紀錄池。
    判準與 run_tests 同序:嚴格 ⇒ 自檢 ⇒ 總開關 ⇒ always-run 標記 ⇒ 紀錄池(reuse_index.explain,即 decide)。"""
    repo = Path(repo).resolve()
    if strict is None:
        mode, _source, _path = resume_policy.current_mode(str(repo))
        strict = mode == "strict"
    gate = self_check()
    out = []
    home = Path(tempfile.mkdtemp(prefix="aib-test-home-", dir=runner.TMP_ROOT))
    try:
        env = runner.isolated_test_env(home, repo)
        idx = reuse_index.build_index([], repo=str(repo)) if (gate.get("ok") and not strict) else None
        for name in tests:
            e = {"test": name, "verdict": "must-run", "why": "", "detail": "", "time": None,
                 "changed": [], "unmeasured": [], "more": 0}
            if strict:
                e["why"] = "strict"
            elif not gate.get("ok"):
                e["why"], e["detail"] = "gate-fail", "%s: %s" % (gate.get("stage", "?"), gate.get("reason", ""))
            elif _unmet_needs(repo, name):
                e["why"], e["detail"] = "needs-unmet", " ".join(_unmet_needs(repo, name))
            elif _no_reuse_env(environ):
                e["why"] = "no-reuse-env"
            elif _always_run_marked(repo, name):
                e["why"] = "always-run"
            else:
                e.update(idx.explain(str(repo), name, environ=env))
            out.append(e)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return out


def _cells(repo, names, as_json=False):
    """aib test --cells(10-06,開發檔 §13 C1):量每一格讀了什麼、執行了哪些函式、花幾秒、有沒有暗中相依。只量,不寫沿用紀錄。"""
    import cell_probe
    outs = [cell_probe.probe(repo, n) for n in names]
    if as_json:
        print(json.dumps({"cells": outs}, ensure_ascii=False))
        return 0
    for o in outs:
        print(T("cells_head", {"test": o["test"], "n": len(o["cells"]), "seconds": "%.1f" % o["seconds"],
                               "what": len(o["shared"])}))
        for c in sorted(o["cells"], key=lambda c: -(c["seconds"] or 0))[:10]:
            print(T("cells_row", {"name": c["cell"], "seconds": "%.2f" % (c["seconds"] or 0), "n": len(c["own"]),
                                  "count": len(c["funcs"]), "what": c["outcome"] or "?"}))
        if o["order_dependent"]:
            print(T("cells_coupled", {"what": ", ".join(o["order_dependent"][:5])}))
        if o["static_state"]:
            print(T("cells_static", {"what": ", ".join(o["static_state"])}))
        if o["shared_unmeasured"] or any(c["unmeasured"] for c in o["cells"]):
            print(T("cells_unmeasured"))
        print(T("cells_ok" if o["separable"] else "cells_not"))
        if o.get("tail"):
            print(o["tail"])
    return 0


def _print_explain(entries):
    for e in entries:
        if e["verdict"] == "reuse":
            print(T("why_reuse", {"test": e["test"], "when": e.get("time") or "?", "key": e.get("detail") or "?"}))
            continue
        print(T("why_mustrun", {"test": e["test"], "why": _why_text(e["why"], e.get("detail") or "")}))
        for it in e.get("changed") or ():
            print(T("why_changed_item", {"item": it}))
        for it in e.get("unmeasured") or ():
            print(T("why_unmeasured_item", {"item": it}))
        if e.get("more"):
            print(T("why_more", {"n": e["more"]}))
        if e["why"] == "env-fp-changed" and not e.get("detail"):   # 10-06:說得出哪幾項就不再叫人自己去比
            print(T("why_env_hint"))


# ---- 10-04:測試要的機器條件(`# aib: needs <條件…>`)--------------------------------------------------
# 有些測試在某台機器上根本做不到(要 Mac 的 plutil、要旁邊的 ez2-ai 倉、要 IPv6)。以前它們在雲端每次都紅,
# 混在真的紅裡要人一支支辨認。現在測試檔前 40 行寫一行條件;這台不符合 ⇒ **不跑、不算綠也不算紅**,
# 結果標「沒驗」(rc=None, unverified=True),總表另列一行、跟紅燈一樣顯眼,整批結束碼 4(有紅仍是 1)。
# ★ 條件必須是「做這件事的前提」,不可以是測試要驗的那件事本身(否則缺的時候不跑 = 藏紅)。
# ★ 看不懂的條件 ⇒ 照跑(不准因為打錯字就變成沒驗)。註解寫法 # 或 // 都認(之後其他語言的測試同一套)。
NEEDS_MARK = re.compile(r"^\s*(?:#|//)\s*aib:\s*needs\s+([A-Za-z0-9_:./,\- ]+)", re.M)


def _needs_of(repo, name):
    try:
        with open(Path(repo) / name, encoding="utf-8", errors="replace") as fh:
            head = "".join(next(fh, "") for _ in range(40))
    except OSError:
        return []
    out = []
    for m in NEEDS_MARK.finditer(head):
        out += [c for c in re.split(r"[,\s]+", m.group(1).strip()) if c]
    return out


def _sibling_repo(repo, name):
    """旁邊的倉:EZ2_MAIN_REPO(只對 ez2-ai)、git 共用目錄的上上層、家目錄底下;任一處是 git 倉就算有。"""
    cands = []
    if name == "ez2-ai" and os.environ.get("EZ2_MAIN_REPO"):
        cands.append(Path(os.environ["EZ2_MAIN_REPO"]).expanduser())
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=10)
        if common.returncode == 0:
            cands.append((Path(repo) / common.stdout.strip()).resolve().parent.parent / name)
    except (OSError, subprocess.SubprocessError):
        pass
    cands.append(Path.home() / name)
    for c in cands:
        try:
            if subprocess.run(["git", "-C", str(c), "rev-parse", "--git-dir"], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=10).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def _need_met(repo, cond):
    """回 True(符合)/ False(不符合)/ None(看不懂 ⇒ 呼叫端照跑)。"""
    import socket
    if cond in ("darwin", "linux"):
        return sys.platform.startswith(cond)
    if cond == "ipv6":
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as so:
                so.bind(("::1", 0))
            return True
        except OSError:
            return False
    kind, _, arg = cond.partition(":")
    if kind == "tool" and arg:
        return shutil.which(arg) is not None
    if kind == "repo" and re.fullmatch(r"[A-Za-z0-9_.-]+", arg or ""):
        return _sibling_repo(repo, arg)
    return None


def _unmet_needs(repo, name):
    return [c for c in _needs_of(repo, name) if _need_met(repo, c) is False]


def _unverified(name, unmet):
    """缺條件 ⇒ 每個條件寫成「這台缺 X」;別種沒驗(全部跳過、一支都沒收集到)的原因自己寫,不冠「這台缺」(10-08)。"""
    return {"name": name, "rc": None, "unverified": True, "needs": [T("need_missing", {"what": c}) for c in unmet], "reused": False,
            "elapsed_s": 0.0, "output": ""}


# 10-05 a seat:unittest 每一格都 skip 時 rc 仍是 0 —— 一格都沒真的驗,不得算綠(20feb 10-02 抓)。
# 從輸出尾段(實跑的 output、沿用的 pool tail 都有)讀最後一組「Ran N tests」與「OK (skipped=K)」。
_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
_SKIP_RE = re.compile(r"^OK \((?:[^)]*?, )?skipped=(\d+)", re.M)


# 10-07(外部專案 Ruby / Go / JS / PHP 實測):別的語言的總結行。只依測試檔的副檔名挑格式 ——
#   Python 測試自己印出「0 passing」之類的字不會被誤認(誤認會把綠判成沒驗)。回 (跑了幾格, 跳過幾格) 或 None。
_MINITEST_RE = re.compile(r"^(\d+) runs?, \d+ assertions?, \d+ failures?, \d+ errors?, (\d+) skips?", re.M)
_RSPEC_RE = re.compile(r"^(\d+) examples?, \d+ failures?(?:, (\d+) pending)?", re.M)
_JEST_RE = re.compile(r"^Tests:\s+(.*?)(\d+) total\s*$", re.M)
_VITEST_RE = re.compile(r"^\s*Tests\s+(.*?)\((\d+)\)\s*$", re.M)
_MOCHA_RE = re.compile(r"^\s*(\d+) (passing|pending|failing)\b", re.M)


def _other_lang_cells(output, name):
    text = output or ""
    n = str(name or "")
    if n.endswith(".rb"):
        m = _MINITEST_RE.findall(text)
        if m:
            return int(m[-1][0]), int(m[-1][1])
        m = _RSPEC_RE.findall(text)
        if m:
            return int(m[-1][0]), int(m[-1][1] or 0)
        return None
    if n.endswith(".php"):
        if re.search(r"^No tests executed!", text, re.M):
            return 0, 0
        m = re.findall(r"^OK \((\d+) tests?, \d+ assertions?\)", text, re.M)
        if m:
            return int(m[-1]), 0
        m = re.findall(r"^Tests: (\d+), Assertions: \d+(.*)$", text, re.M)
        if m:
            total, rest = m[-1]
            sk = sum(int(x) for x in re.findall(r"(?:Skipped|Incomplete): (\d+)", rest))
            return int(total), sk
        return None
    if n.endswith("_test.go"):
        if re.search(r"\[no test files\]|\[no tests to run\]", text) and not re.search(r"^--- (PASS|FAIL)", text, re.M):
            return 0, 0
        return None
    if n.endswith((".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx")):
        m = _JEST_RE.findall(text) or _VITEST_RE.findall(text)
        if m:
            head, total = m[-1]
            sk = re.search(r"(\d+) (?:skipped|todo)", head)
            return int(total), int(sk.group(1)) if sk else 0
        m = _MOCHA_RE.findall(text)
        if m:
            got = {k: int(v) for v, k in m}
            return sum(got.values()), got.get("pending", 0)
    return None


def ran_cells(output, name=None):
    """unittest 總結最後一組「Ran N tests」的 N;讀不到 ⇒ None(不判,照原 rc)。跳過格數用主線的 skipped_cells。
    10-07:給了測試檔名、而且是 Ruby / Go / JS ⇒ 認那個語言的總結行(minitest、rspec、go test、jest、vitest、mocha)。"""
    other = _other_lang_cells(output, name)
    if other is not None:
        return other[0]
    ran = _RAN_RE.findall(output or "")
    return int(ran[-1]) if ran else None


# pytest 結束碼 5 = 一支都沒收集到(全部被 -m / -k / addopts 排除,或檔裡沒有測試)。只認 pytest 自己的最後一行,
#   別的程式自己 exit(5) 是真的失敗,照樣紅。
PYTEST_NONE_RC = 5
PYTEST_NONE_RE = re.compile(r"^=*\s*(?:no tests ran|\d+ deselected)(?: in [\d.]+s)?\b[^\n]*$", re.M)


def _mark_all_skipped(r):
    """rc=0 但全部格跳過(或 Ran 0)⇒ 改成「沒驗」(整批 rc 4);部分跳過由主線 row_skipped/skipped_summary 顯示。
    10-08(packaging 實測:31 支有 20 支被當紅):pytest rc=5 而且最後一行是「N deselected」或「no tests ran」⇒ 也是「沒驗」。"""
    if r.get("rc") == PYTEST_NONE_RC and not r.get("unverified"):
        tail = (r.get("output") or "").rstrip().splitlines()[-1:] or [""]
        if PYTEST_NONE_RE.search(tail[0]):
            r.update(rc=None, unverified=True, needs=[T("need_none_collected")])
            return r
    if r.get("rc") != 0 or r.get("unverified"):
        return r
    ran = ran_cells(r.get("output"), r.get("name"))
    if ran is None:
        return r
    other = _other_lang_cells(r.get("output"), r.get("name"))
    skipped = other[1] if other is not None else skipped_cells(r.get("output"))
    if ran == 0 or skipped >= ran:
        r.update(rc=None, unverified=True, needs=[T("need_all_skipped", {"n": ran})])
    return r


def lease_note(env=None):
    """這台現在誰拿著機器租約(真的去讀,不是固定句)。讀不到工具 ⇒ None(不印、不假裝)。

    ★ 10-05 a seat(第 24d 項實踩):**不在行程內 import host_lease**。本檔被幾十支測試 in-process 載入,
      只要這裡 import 一次,第 24d 項的遞移閉包就把它們全圈進「碰得到機器租約」的母體(候選 90 支紅)。
      改走子行程 `host_lease.py status --json`:落點照 AIB_HOST_LEASE_FILE(子行程繼承 env),
      治具搬走的落點一樣生效;讀的內容與原本 in-process 同一份。"""
    tool = ENTRY_ROOT / "tools" / "governance" / "host_lease.py"
    if not tool.is_file():
        return None
    try:
        p = subprocess.run([sys.executable, str(tool), "status", "--json"],
                           env=(dict(env) if env is not None else None),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        d = json.loads(p.stdout)
        state, cur = d.get("state"), d.get("lease")
    except Exception:
        return None
    if state == "held" and cur:
        params = {"name": cur.get("owner", "?"), "what": cur.get("what", "?")}   # 佔位名用既有的 {name}(annotate-i2-guard 母體 268 不長)
        mine = _holder_is_ancestor(cur)
        if mine is True:
            return T("lease_mine", params)
        if mine is False:
            # 10-05 a seat:兩次實際踩到 —— 租約在別人手上,這批照跑、跟它並跑。要大聲說,不是只報誰拿著。
            return T("lease_other", params)
        return T("lease_held", params)
    return T("no_lease_batch")


def _holder_is_ancestor(cur):
    """持有者的 pid 是不是這支程式的祖先(= 這一批是在拿著租約的那個殼裡跑的)。
    True / False / None(量不到:別台機器、沒記 pid、ps 讀不到)。"""
    import socket
    pid = cur.get("pid")
    if not pid or cur.get("host") != socket.gethostname():
        return None
    try:
        want = int(pid)
    except (TypeError, ValueError):
        return None
    p = os.getpid()
    for _ in range(64):
        if p == want:
            return True
        if p <= 1:
            return False
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(p)], capture_output=True, text=True,
                                 timeout=5, stdin=subprocess.DEVNULL).stdout.strip()
            p = int(out)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return False


# ── 10-12 記憶體與大批排隊(a seat 派,the maintainer 15:0x「依照你的建議開始」)────────────────────────────
#   起因:10-11 19:58 Mini(16G)JetsamEvent —— 19 個 Python 9.7G(各席 aib test + 推包三份同時跑)、13 個 Claude 4.2G,
#   系統砍了 8 個服務。A:起跑前看記憶體,不夠就等(有上限,等滿照跑並註明);B:大批(超過 N 支)拿同一把機器租約,
#   跟推包排隊不同時跑;小批不拿。兩件都只在最外層的 aib test 做(分份的子行程、拿著租約的殼底下都不做)。
MEM_MIN_MB = 3072          # 可用 + 可回收低於這個就等(AIB_TEST_MEM_MIN_MB 可改;0 = 不看)
MEM_POLL_S = 30
MEM_WAIT_MAX_S = 600       # 大批最多等這麼久,之後照跑(AIB_TEST_MEM_WAIT_MAX_S 可改)
MEM_WAIT_SMALL_S = 30      # 小批(≤ BIG_BATCH)最多等這麼久:AI 席的 Bash 工具 2~10 分鐘就逾時,等久了只看到逾時、沒有紅綠(a seat 審)
BIG_BATCH = 30             # 超過這麼多支算大批(AIB_TEST_BIG_BATCH 可改;0 = 都不排隊)
LEASE_POLL_S = 30
LEASE_WAIT_MAX_S = 3600    # 大批排隊最多等這麼久(推包一輪約 50 分鐘),之後不拿租約照跑並註明(AIB_TEST_LEASE_WAIT_MAX_S 可改)


def _env_int(name, default):
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default


def free_mb():
    """可用 + 可回收的記憶體(MiB);量不到 ⇒ None(不等、不假裝)。
    macOS:vm_stat 的 free + inactive + speculative + purgeable 頁 × 頁大小(跟「記憶體壓力」算可用的那幾類同);
    Linux:/proc/meminfo 的 MemAvailable。"""
    try:
        txt = Path("/proc/meminfo").read_text(encoding="utf-8")
        m = re.search(r"^MemAvailable:\s+(\d+) kB", txt, re.M)
        if m:
            return int(m.group(1)) // 1024
    except OSError:
        pass
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"page size of (\d+) bytes", out)
    if not m:
        return None
    page = int(m.group(1))
    pages = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        k = re.search(r"^%s:\s+(\d+)\." % key, out, re.M)
        if k:
            pages += int(k.group(1))
    return pages * page // (1024 * 1024)


def wait_for_memory(say, free=free_mb, sleep=time.sleep, clock=time.monotonic, big=True):
    """記憶體不夠就等,每 MEM_POLL_S 秒看一次(小批每 10 秒);回 {"waited": 秒, "gave_up": bool, "free": 最後一次 MiB 或 None}。
    big=False(小批)最多等 MEM_WAIT_SMALL_S 秒就照跑。"""
    need = _env_int("AIB_TEST_MEM_MIN_MB", MEM_MIN_MB)
    cap = _env_int("AIB_TEST_MEM_WAIT_MAX_S", MEM_WAIT_MAX_S) if big else MEM_WAIT_SMALL_S
    poll = MEM_POLL_S if big else 10
    t0 = clock()
    got = free()
    while need and got is not None and got < need:
        waited = int(clock() - t0)
        if waited >= cap:
            say(T("mem_gave_up", {"waited": waited, "count": got, "limit": need}))
            return {"waited": waited, "gave_up": True, "free": got}
        say(T("mem_wait", {"count": got, "limit": need, "n": poll, "waited": waited}))
        sleep(poll)
        got = free()
    return {"waited": int(clock() - t0), "gave_up": False, "free": got}


def _lease_tool(*args, env=None):
    """跟 lease_note 一樣走子行程(不 import host_lease,見那裡的 24d 說明)。回 (rc, stdout)。"""
    tool = ENTRY_ROOT / "tools" / "governance" / "host_lease.py"
    if not tool.is_file():
        return None, ""
    try:
        p = subprocess.run([sys.executable, str(tool), *args], env=(dict(env) if env is not None else None),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return p.returncode, p.stdout


def _lease_now(env=None, with_ship=False):
    """(state, lease);with_ship ⇒ 多回一個「推包在等」記號(活著才有)。"""
    rc, out = _lease_tool("status", "--json", env=env)
    try:
        d = json.loads(out)
    except (TypeError, ValueError):
        return (None, None, None) if with_ship else (None, None)
    if with_ship:
        return d.get("state"), d.get("lease"), d.get("ship_waiting")
    return d.get("state"), d.get("lease")


LEASE_TTL_FALLBACK_S = 3 * 3600
RC_EXCLUSIVE = 5    # 沒跑:別人在獨佔量耗時(rc 4 已經是「全部跳過 = 沒驗」,不能共用 —— 審查 L6)


def lease_ttl(repo, names):
    """大批租約的到期:上次紀錄的秒數加總 × 1.5 + 10 分鐘(10-13 a seat 准)。紀錄缺一半以上 ⇒ 退回 3 小時。
    ★ 活著的持有者本來就不會被接管(host_lease.is_expired),TTL 只影響「用到幾點」顯示,和持有者死掉後多久可接。"""
    try:
        base = baselines(runner._pool_file(repo), names)
    except Exception:
        base = {}
    known = [base[t] for t in names if t in base]
    if not names or len(known) * 2 < len(names):
        return LEASE_TTL_FALLBACK_S
    est = sum(known) + 10.0 * (len(names) - len(known))
    return int(min(LEASE_TTL_FALLBACK_S, max(15 * 60, est * 1.5 + 600)))


def exclusive_block(cur):
    """別人拿著、標了獨佔(在量耗時)⇒ 這把租約;否則 None。拿著租約的殼底下(祖先)不算別人。"""
    if not cur or not cur.get("exclusive"):
        return None
    return cur if _holder_is_ancestor(cur) is False else None    # 審查 L6:量不到(ps 失敗)⇒ 放行,不誤擋推包底下的測試


def big_batch_lease(n_tests, repo, say, sleep=time.sleep, clock=time.monotonic, env=None, force=False, exclusive=False,
                    names=None):
    """大批(> BIG_BATCH 支)⇒ 拿機器租約,被別人拿著就排隊(每 LEASE_POLL_S 秒試一次,沒有上限:推包一輪約 50 分鐘,
    就是要等它)。回 {"run_id": 要放的租約 id 或 None, "waited": 秒, "why": big|small|inherited|unavailable}。
    租約已在呼叫自己的殼手上(推包、ship check --gate)⇒ 沿用,不重拿也不放。工具讀不到 ⇒ 不拿、照跑、說一聲。"""
    big = _env_int("AIB_TEST_BIG_BATCH", BIG_BATCH)
    if not force and (not big or n_tests <= big):
        return {"run_id": None, "waited": 0, "why": "small"}
    state, cur, ship_wait = _lease_now(env, with_ship=True)
    if state == "held" and cur and _holder_is_ancestor(cur) is True:
        return {"run_id": None, "waited": 0, "why": "inherited"}
    run_id = "aib-test-%d-%d" % (os.getpid(), int(time.time()))
    owner = "aib test" + ((" @" + os.environ["AIB_SEAT_CODE"]) if os.environ.get("AIB_SEAT_CODE") else "")
    what = "aib test %d %s" % (n_tests, Path(repo).name)
    cap = _env_int("AIB_TEST_LEASE_WAIT_MAX_S", LEASE_WAIT_MAX_S)
    ttl = lease_ttl(repo, names or [])     # 讀歷史秒數要幾秒(紀錄大時 2 秒多)⇒ 先算好再起表,不然沒等也說「等了 2 秒」(10-14)
    t0 = clock()
    first, queued, said = True, False, False
    ahead = []      # 10-14 v2:排在誰後面(最後看到的那一個),收尾 ⏳ 那行寫出來

    def leave(why):
        if queued:
            _lease_tool("queue", "remove", "--run-id", run_id, env=env)
        return {"run_id": None, "waited": int(clock() - t0), "why": why, "ahead": ahead[-1] if ahead else None}

    while True:
        # 排隊上限(a seat 審):不拿租約照跑,結果註明。第一輪一定先試(上限設 0 也會在機器空時拿到 —— 審查第三輪)
        if not first and clock() - t0 >= cap:
            if state == "held" and exclusive_block(cur) is not None:   # 審查 M2:等滿了對方還在獨佔量耗時 ⇒ 不並跑
                say(T("lease_exclusive_blocked", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"),
                                                  "at": cur.get("expiry_at", "?")}))
                return leave("exclusive_held")
            say(T("lease_queue_gave_up", {"waited": int(clock() - t0)}))
            return leave("gave_up")
        first = False
        # 先看再拿:被拿著就不去 acquire(每次被拒都會在租約紀錄寫一筆,排一輪推包會寫上百筆 —— 審查)
        if state != "held" and not ship_wait:
            args = ["acquire", "--owner", owner, "--what", what, "--run-id", run_id, "--ttl", str(ttl), "--pid", str(os.getpid())]
            rc, _out = _lease_tool(*(args + (["--exclusive"] if exclusive else [])), env=env)
            if rc == 0:
                waited = int(clock() - t0)
                if waited:
                    say(T("lease_queue_done", {"waited": waited}))
                return {"run_id": run_id, "waited": waited, "why": "big", "ahead": ahead[-1] if ahead else None}
            if rc != 4:
                say(T("lease_queue_unavailable", {"rc": rc}))
                return leave("unavailable")
            state, cur, ship_wait = _lease_now(env, with_ship=True)
            if state != "held":           # 剛好被放掉又被搶 / 排隊名單有人在前面 / 讀不到:下一輪再看
                state, behind = "held", (_out or "").strip()
                if behind:                # 機器空著(或租約過期)但前面有人排:照 host_lease 那句講,不印過期的持有者(審查 L5)
                    say(behind); said = True
        # 10-13:要等 ⇒ 登記排隊名單(登記到拿到的秒數記進 ledger;機器空時只准隊頭拿)。每輪都再登記一次 = 續期,
        #   位置不變(審查 L4:ps 量不到起始時刻時登記退回只看到期,一次登記 30 分鐘後會悄悄掉出名單)
        qrc, _o = _lease_tool("queue", "add", "--owner", owner, "--what", what, "--run-id", run_id,
                              "--pid", str(os.getpid()), env=env)
        queued = queued or qrc == 0
        if said:                              # 上面已經講過排在誰後面
            said = False
        elif ship_wait and state != "held":     # 機器空著但推包在等:讓它先(推包優先,a seat 10-12)
            ahead.append(ship_wait.get("what") or "ship.py")
            say(T("lease_queue_ship_first", {"what": ship_wait.get("what") or "ship.py", "waited": int(clock() - t0),
                                             "n": LEASE_POLL_S}))
        else:
            cur = cur or {}
            ahead.append("%s · %s" % (cur.get("owner", "?"), cur.get("what", "?")))
            say(T("lease_queue_wait", {"name": cur.get("owner", "?"), "what": cur.get("what", "?"),
                                       "at": cur.get("expiry_at", "?"), "waited": int(clock() - t0), "n": LEASE_POLL_S}))
        sleep(LEASE_POLL_S)
        state, cur, ship_wait = _lease_now(env, with_ship=True)
        if state is None:
            say(T("lease_queue_unavailable", {"rc": "status"}))
            return leave("unavailable")


def _ci_or_fixture_home():
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return True
    home = os.path.realpath(os.path.expanduser("~"))
    tmps = {os.path.realpath(p) for p in (runner.TMP_ROOT, tempfile.gettempdir(), "/tmp")}
    return any(home == t or home.startswith(t + os.sep) for t in tmps)


def release_batch_lease(run_id, env=None):
    if run_id:
        _lease_tool("release", "--run-id", run_id, env=env)


JOBS_MAX = 4


def _default_jobs(a, names):
    """10-08(attrs / click 實測):一支一支跑,第一次比 pytest 慢 2~4 倍 ⇒ --all 預設分份同時跑。
    只有 --all、不是 --json / --resume(推前檢查自己會分份)才自動;AIB_TEST_JOBS 可以指定。"""
    env = os.environ.get("AIB_TEST_JOBS", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    if not a.all or a.json or a.resume:
        return 1
    return max(1, min(JOBS_MAX, os.cpu_count() or 1, len(names)))


def run_sharded(repo, names, jobs, a):
    """分成 jobs 份(依上次秒數平均),每份一個子行程跑 run_tests(--shard-out),合併成跟 run_tests 同形狀的結果。
    同一棵樹同時有幾批在跑 ⇒ 工作樹守衛不歸咎、不還原(_other_batches),跟推前檢查分份一樣。"""
    base = baselines(runner._pool_file(repo), names)
    shards, load = [[] for _ in range(jobs)], [0.0] * jobs
    units = {}                          # 同一個 Go 套件的檔分到同一份(那一份只跑一次 go test)
    for t in names:
        units.setdefault(runner.go_package_key(t) or ("\0" + t), []).append(t)
    for unit in sorted(units.values(), key=lambda u: (-max(base.get(x) or 1.0 for x in u), u[0])):
        i = load.index(min(load))
        shards[i].extend(unit)
        load[i] += max(base.get(x) or 1.0 for x in unit)
    shards = [s for s in shards if s]
    procs = []
    for i, s in enumerate(shards):
        fd, out = tempfile.mkstemp(prefix="aib-test-shard-", suffix=".json", dir=runner.TMP_ROOT)
        os.close(fd)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo), "--jobs", "1", "--shard-out", out]
        cmd += (["--strict"] if a.strict else []) + (["--fresh"] if a.fresh else [])
        cmd += (["--timeout", str(a.timeout)] if a.timeout else []) + s
        procs.append((subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                       env=dict(os.environ, AIB_TEST_SHARD=str(i))), out))
    parts = []
    for p, out in procs:
        rc = p.wait()
        try:
            parts.append(json.loads(Path(out).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            parts.append({"state": "short", "gate": {}, "tests": [], "ran": 0, "reused": 0, "kept": 0, "rc": rc or 1,
                          "line1": T("line1_short", {"ran": 0, "kept": 0})})
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
    for part in parts:
        _TMP_LEAKS.extend(tuple(x) for x in part.get("tmp_leaks") or [])
    order = {n: i for i, n in enumerate(names)}
    tests = sorted((r for part in parts for r in part.get("tests") or []), key=lambda r: order.get(r.get("name"), 1 << 30))
    ran, reused, kept = (sum(int(part.get(k) or 0) for part in parts) for k in ("ran", "reused", "kept"))
    reds = [r for r in tests if r.get("rc") != 0 and not r.get("unverified")]
    unv = [r for r in tests if r.get("unverified")]
    states = {part.get("state") for part in parts}
    lost = len(tests) < len(names)            # 有一份沒交回結果 ⇒ 不能當全綠
    if "fallback" in states:
        fb = next(part for part in parts if part.get("state") == "fallback")
        return dict(fb, tests=tests, ran=ran, reused=reused, kept=kept, rc=1 if reds or lost else (4 if unv else 0))
    if "short" in states or kept < ran or lost:
        return {"state": "short", "gate": parts[0].get("gate", {}), "tests": tests, "ran": ran, "reused": reused, "kept": kept,
                "rc": 1 if reds or lost else 3, "line1": T("line1_short", {"ran": ran, "kept": kept})}
    return {"state": "enabled", "gate": parts[0].get("gate", {}), "tests": tests, "ran": ran, "reused": reused, "kept": kept,
            "rc": 1 if reds else (4 if unv else 0), "line1": T("line1_enabled", {"reused": reused, "ran": ran, "kept": kept})}


def run_tests(repo, tests, *, strict=None, fresh=False, timeout=None, environ=None, live=None, resume=None):
    """Run `tests` (repo-relative paths) in `repo` through the entry.  Returns a result dict:
    {"line1", "state": enabled|fallback|short, "rc", "tests": [{test, rc, reused, elapsed_s, output}],
     "ran", "reused", "kept", "gate"}.  Nothing is printed here (main() prints) except through `live`:
    live(kind, params) is called as each test starts ("start"/"start_nobase"), the moment a running
    test passes 10% over its usual time ("slow"), and as each real run ends ("done_green"/"done_red");
    None (package_trial etc.) = silent."""
    repo = Path(repo).resolve()
    if strict is None:
        mode, _source, _path = resume_policy.current_mode(str(repo))
        strict = mode == "strict"
    gate = self_check()
    # 10-04 可接續:--resume <批次名> ⇒ 這一批上次已跑綠(同樹、同跑法)的不重跑;紅的、沒跑完的照跑
    journal, resumed_ok, tree_now, env_fp_now, problem = None, set(), "", None, None
    if resume:
        journal = _journal_path(repo, resume)
        try:
            tree_now = runner.st.tree_sha(repo)
        except BaseException:   # noqa: BLE001  tree_sha 失敗會 SystemExit;量不到 ⇒ 不接續
            tree_now = ""
        journal_rows = {}
        resumed_ok, problem = load_journal(journal, tree_now, fresh, rows_out=journal_rows)
        if live is not None and problem:
            live("resume_unusable", {"name": resume, "why": problem})
    home = Path(tempfile.mkdtemp(prefix="aib-test-home-", dir=runner.TMP_ROOT))
    trace_dir = Path(tempfile.mkdtemp(prefix="aib-test-trace-", dir=runner.TMP_ROOT))
    tree_base = _tracked_dirty(repo)
    batch_marker = _register_batch(repo)
    results = []
    try:
        env = runner.isolated_test_env(home, repo)
        _tp = runner.st.test_python(repo, env)
        _quiet = os.environ.get("AIB_TEST_SHARD", "0") != "0"   # 分份同時跑:開頭這兩行只第 0 份印(失敗照印)
        if live is not None and _tp != sys.executable and not _quiet:
            # 10-04 晚:用的不是 aib 自己的 python(專案的虛擬環境)⇒ 開頭講清楚用哪一支、從哪來
            _ver, _pyt = runner.st.python_id(_tp)
            # 佔位名用母體裡已有的(path/version/pkg/source):新名字會改動 annotate-i2 test_40 凍結的母體數字
            live("test_python", {"path": _tp, "version": _ver, "pkg": _pyt or "-",
                                 "source": "AIB_TEST_PYTHON" if env.get("AIB_TEST_PYTHON") and os.path.abspath(env["AIB_TEST_PYTHON"]) == os.path.abspath(_tp)
                                 else ("VIRTUAL_ENV" if env.get("VIRTUAL_ENV") and _tp.startswith(env["VIRTUAL_ENV"]) else "repo venv")})
        if live is not None:
            # ★ the maintainer 10-02:每一支顯示時間之前,先講影子續跑有沒有載入、有沒有啟用。
            if gate.get("ok"):
                if not _quiet:
                    live("gate_ok", {})
            else:
                live("gate_fail", {"stage": gate.get("stage", "?"), "reason": gate.get("reason", "")})
        if not gate.get("ok"):
            resumed_ok = set()      # 10-15 A1:自檢沒過 ⇒ 算不出環境指紋、讀不了作廢列 ⇒ 不接續(整批照跑)
            for name in tests:
                if name in resumed_ok:
                    if live is not None:
                        live("resumed", {"test": name, "name": resume})
                    results.append({"name": name, "rc": 0, "reused": False, "resumed": True, "elapsed_s": 0.0, "output": ""})
                    continue
                unmet = _unmet_needs(repo, name)
                if unmet:
                    results.append(_unverified(name, unmet))
                    if live is not None:
                        live("unverified", {"test": name, "what": " ".join(results[-1]["needs"])})
                    continue
                if live is not None:
                    live("start_fallback", {"test": name})
                runner.wait_for_memory(None if live is None else (lambda g: live("mem_wait", {"limit": "%.1f" % g})))
                with _own_home(repo, env, name) as tenv:
                    results.append(_mark_all_skipped(dict(runner._run_one(repo, name, tenv, timeout=timeout), reused=False)))
                _tree_guard(repo, tree_base, results[-1], live)
                if live is not None:
                    _live_done(live, results[-1])
                if journal is not None:
                    append_journal(journal, {"test": name, "rc": results[-1]["rc"], "tree": tree_now, "fresh": bool(fresh),
                                             "time": time.strftime("%Y-%m-%d %H:%M:%S"), **_jstamp(env_fp_now),
                                             "verdict": "fallback", "why": str(gate.get("stage") or "?"),
                                             "detail": str(gate.get("reason") or "")[:200]})
            reds = [r for r in results if r["rc"] != 0 and not r.get("unverified")]
            unv = [r for r in results if r.get("unverified")]
            return {"state": "fallback", "gate": gate, "tests": results, "ran": len(results) - len(unv), "reused": 0,
                    "kept": 0, "rc": 1 if reds else (4 if unv else 0),
                    "line1": T("line1_fallback", {"stage": gate.get("stage", "?"), "reason": gate.get("reason", "")})}
        idx = reuse_index.build_index([], repo=str(repo))
        pool = runner._pool_file(repo)
        base = baselines(pool, tests)      # 先讀耗時(容得下壞行),再修池
        idx = _heal_own_pool(repo, idx, live)
        env_fp_now = _resume_env_fp(repo, env) if journal is not None else None
        if journal is not None and resumed_ok:
            import datetime as _dt
            now_utc = _dt.datetime.now(_dt.timezone.utc)
            max_age = min(RESUME_MAX_AGE_H, reuse_index.max_row_age_h(env))
            dropped = {}
            for name in [t for t in tests if t in resumed_ok]:
                why_not = resume_blocked(name, journal_rows.get(name), strict=strict, no_reuse=_no_reuse_env(),
                                         always_run=_always_run_marked(repo, name), env_fp_now=env_fp_now, idx=idx,
                                         now_utc=now_utc, max_age_h=max_age)
                if why_not:
                    resumed_ok.discard(name)
                    dropped[why_not] = dropped.get(why_not, 0) + 1
            if live is not None and dropped:
                live("resume_dropped", {"name": resume, "n": sum(dropped.values()),
                                        "why": ", ".join("%s×%d" % kv for kv in sorted(dropped.items()))})
        if live is not None and journal is not None and not problem:
            live("resume_on", {"name": resume, "n": len([t for t in tests if t in resumed_ok])})
        offset = _pool_size(pool)
        started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ran_names = []
        go_done = {}                       # Go 套件 ⇒ 這一批跑那個套件的結果(同一套件只跑一次)
        overheads = []                     # 這一批每支的「牆鐘 − 紀錄耗時」,給即時計時器補準備時間
        for name in tests:
            if name in resumed_ok:
                if live is not None:
                    live("resumed", {"test": name, "name": resume})
                results.append({"name": name, "rc": 0, "reused": False, "resumed": True, "elapsed_s": 0.0, "output": ""})
                continue
            unmet = _unmet_needs(repo, name)
            if unmet:
                results.append(_unverified(name, unmet))
                if live is not None:
                    live("unverified", {"test": name, "what": " ".join(results[-1]["needs"])})
                continue
            verdict, why, detail = "must-run", ("strict" if strict else "fresh"), ""
            if not strict and not fresh and _no_reuse_env():
                why = "no-reuse-env"
            elif not strict and not fresh and _always_run_marked(repo, name):
                why = "always-run"
            elif not strict and not fresh:
                verdict, why, detail = idx.decide(str(repo), name, changed=[], environ=env)
            if verdict == "reuse":
                prev = idx.row_for(name, detail) or {}   # 被沿用的那一列,不一定是最新一列
                if live is not None:
                    pd = prev.get("duration_s")
                    live("reuse", {"test": name, "prev": "%.1f" % pd if isinstance(pd, (int, float)) else "?"})
                results.append({"name": name, "rc": 0, "reused": True, "elapsed_s": 0.0,
                                "output": prev.get("tail", "") or "", "prev_time": prev.get("time"),
                                "prev_duration_s": prev.get("duration_s"),
                                "prev_inputs": len(prev.get("inputs") or [])})
                _mark_all_skipped(results[-1])
                if journal is not None:
                    append_journal(journal, {"test": name, "rc": results[-1]["rc"], "tree": tree_now, "fresh": bool(fresh),
                                             "time": time.strftime("%Y-%m-%d %H:%M:%S"), **_jstamp(env_fp_now),
                                             "verdict": "reuse", "why": why})
                continue
            gk = runner.go_package_key(name)
            if gk is not None and gk in go_done:
                # 10-08(pflag 實測:32 個測試檔同一個套件 ⇒ 以前 go test 同一包跑 32 次):同一套件這一批已經跑過 ⇒ 用那次的結果。
                #   不算實跑(紀錄池沒有它的列,不進 ran_names);紅綠跟那次一樣(同一個 go test ./pkg)。
                first = go_done[gk]
                results.append(dict(first, name=name, reused=False, elapsed_s=0.0, same_package=first["name"],
                                    baseline_s=None, slow=False))
                if live is not None:
                    live("same_package", {"test": name, "name": first["name"]})
                if journal is not None:
                    append_journal(journal, {"test": name, "rc": results[-1]["rc"], "tree": tree_now, "fresh": bool(fresh),
                                             "time": time.strftime("%Y-%m-%d %H:%M:%S"), **_jstamp(env_fp_now),
                                             "verdict": "same-package", "why": why, "detail": str(detail or "")[:200]})
                continue
            b = base.get(name)
            limit = slow_threshold(b)
            if live is not None:
                live("start" if b is not None else "start_nobase",
                     {"test": name, "base": "%.1f" % (b or 0), "why": _why_text(why, detail)})
            on_slow = None
            if live is not None and limit is not None:
                on_slow = lambda n=name, bb=b: live("slow", {"test": n, "base": "%.1f" % bb,
                                                             "limit": "%.1f" % slow_threshold(bb)})
            allowance = max(overheads[-5:]) if overheads else SETUP_ALLOWANCE_DEFAULT_S
            t0 = time.monotonic()
            runner.wait_for_memory(None if live is None else (lambda g: live("mem_wait", {"limit": "%.1f" % g})))
            with _SlowWatch(None if limit is None else limit + allowance, on_slow), _own_home(repo, env, name) as tenv:
                actual = runner._traced_run(repo, repo, name, tenv, trace_dir, row_source=ROW_SOURCE, timeout=timeout)
            el = actual.get("elapsed_s")
            if isinstance(el, (int, float)):
                overheads.append(max(0.0, time.monotonic() - t0 - el))
            slow = limit is not None and isinstance(el, (int, float)) and el > limit
            results.append(_mark_all_skipped(dict(actual, reused=False, baseline_s=b, slow=slow, why=why,
                                                  why_detail=str(detail or "")[:200])))
            _tree_guard(repo, tree_base, results[-1], live)
            if live is not None:
                _live_done(live, results[-1])
            if journal is not None:
                append_journal(journal, {"test": name, "rc": results[-1]["rc"], "tree": tree_now, "fresh": bool(fresh),
                                         "time": time.strftime("%Y-%m-%d %H:%M:%S"), **_jstamp(env_fp_now),
                                         "verdict": "must-run", "why": why, "detail": str(detail or "")[:200]})
            ran_names.append(name)
            if gk is not None:
                go_done[gk] = results[-1]
        # Step 2's second half: count what really landed in the pool, not what _traced_run said it did.
        here = str(repo)
        landed = {r.get("test") for r in _pool_rows_after(pool, offset)
                  if r.get("repo") == here and r.get("key")}
        kept = sum(1 for n in set(ran_names) if n in landed)
        if kept < len(set(ran_names)):
            # 10-07(第 14 包實踩):同時跑的另一份觸發紀錄池自動壓縮 ⇒ 池檔原地改寫變小,開跑時記的位置已超過檔尾
            #   ⇒ 從那裡往後數到 0 列,三份全綠卻判成「沒留下紀錄」。數不夠時改成整檔掃一次:
            #   只數這一輪開跑之後(time_utc)寫進來、這個倉、有鑰匙、而且是這一批實跑的測試。只影響這個自我檢查,不影響紅綠。
            # 10-07 Mac 第五輪實踩(899 支只留 898):壓縮把「這一輪開跑後才寫的列」搬進 <池>.archive(另一棵工作樹同時
            #   重跑同一支,每支只留最新 3 把鑰匙)⇒ 只掃池檔會少那一列。整檔重掃時封存檔一起掃;仍只數這一輪之後寫的。
            wanted = set(ran_names)
            for src in (pool, Path(str(pool) + ".archive")):
                landed |= {r.get("test") for r in _pool_rows_after(src, 0)
                           if r.get("repo") == here and r.get("key") and r.get("test") in wanted
                           and str(r.get("time_utc") or "") >= started_utc}
            kept = sum(1 for n in wanted if n in landed)
        ran = len(set(ran_names))
        reused = sum(1 for r in results if r["reused"])
        reds = [r for r in results if r["rc"] != 0 and not r.get("unverified")]
        unv = [r for r in results if r.get("unverified")]
        if kept < ran:
            return {"state": "short", "gate": gate, "tests": results, "ran": ran, "reused": reused, "kept": kept,
                    "rc": 1 if reds else 3, "line1": T("line1_short", {"ran": ran, "kept": kept})}
        return {"state": "enabled", "gate": gate, "tests": results, "ran": ran, "reused": reused, "kept": kept,
                "rc": 1 if reds else (4 if unv else 0), "line1": T("line1_enabled", {"reused": reused, "ran": ran, "kept": kept})}
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(trace_dir, ignore_errors=True)
        if batch_marker is not None:
            try:
                batch_marker.unlink()
            except OSError:
                pass


def _repo_of(path):
    try:
        p = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = (p.stdout or "").strip()
    return Path(top) if p.returncode == 0 and top else None


def _normalize(repo: Path, arg: str, also_repo: bool = False):
    """A test argument (relative to cwd, or absolute) ⇒ repo-relative path, or None when it is not a file in repo.
    also_repo(10-05,給了 --repo):相對路徑在目前目錄底下找不到倉裡的檔,就改從倉根找
    —— 說明書寫 `aib test --repo ~/my-project tests/test_x.py`,原本會回「不是這個倉裡的檔案」。"""
    p = Path(arg)
    if p.is_absolute():
        return _rel_file(repo, p)
    return _rel_file(repo, Path.cwd() / p) or (_rel_file(repo, repo / p) if also_repo else None)


def _why_not(repo: Path, arg: str) -> str:
    """10-11:「不是這個倉裡的檔案」說出是哪一種 —— 10-10 有席把 18 支併成一個參數(換行隔開)丟進來,
    只看到這句,誤以為是「一次超過 7 支就壞」。回 i18n 鍵尾。"""
    if len(arg.split()) > 1:
        return "not_a_test_joined"
    p = Path(arg) if Path(arg).is_absolute() else Path.cwd() / arg
    if not p.exists() and not (repo / arg).exists():
        return "not_a_test_missing"
    try:
        p.resolve().relative_to(repo.resolve())
    except (OSError, ValueError):
        return "not_a_test_outside"
    return "not_a_test"


def _rel_file(repo: Path, p: Path):
    try:
        p = p.resolve()
        rel = p.relative_to(repo.resolve())
    except (OSError, ValueError):
        return None
    return rel.as_posix() if p.is_file() else None


def _estimate(where, as_json=False):
    """aib test --estimate(10-05):拿這個倉最近 50 個提交回放它自己的紀錄,估每個提交能省多少;不跑任何測試。"""
    repo = _repo_of(where)
    if repo is None:
        print(T("no_repo", {"path": where}), file=sys.stderr)
        return 2
    import replay_savings
    s, tests, total = replay_savings.estimate(str(repo), n=50)
    if as_json:
        print(json.dumps({"estimate": s, "tests": tests, "full_run_s": round(total, 1)}, ensure_ascii=False))
        return 0 if s else 3
    if not s:
        print(T("estimate_none", {"path": str(repo)}))
        return 3
    print(T("estimate", {"n": s["commits"], "tests": tests, "seconds": "%.0f" % total, "v1": s["mean_pct"],
                         "v2": s["median_pct"], "v3": s["best_pct"], "v4": s["worst_pct"]}))
    print(T("estimate_note"))
    return 0


def overhead(repo, names, timeout=None, plain=None, traced=None):
    """--overhead(10-08 §27.1):「沒有 aib 要等」是在 aib 底下量的秒數(含追蹤)⇒ 偏高多少?
    每支跑兩次:不經追蹤的原樣執行(不記紀錄,只量時間)與照常(有追蹤、記紀錄,--fresh 不沿用)。
    回 [{"name", "traced_s", "plain_s", "rc_traced", "rc_plain"}]。plain / traced 可替換(測試用)。"""
    plain = plain or (lambda name: runner._run_one(Path(repo), name, dict(os.environ), timeout=timeout))
    traced = traced or (lambda name: run_tests(repo, [name], fresh=True, timeout=timeout)["tests"][0])
    rows = []
    for name in names:
        t0 = time.monotonic()
        pr = plain(name)
        plain_s = time.monotonic() - t0
        tr = traced(name)
        rows.append({"name": name, "traced_s": float(tr.get("elapsed_s") or 0.0), "plain_s": plain_s,
                     "rc_traced": tr.get("rc"), "rc_plain": pr.get("rc")})
    return rows


def print_overhead(rows):
    for r in rows:
        pct = 100.0 * (r["traced_s"] - r["plain_s"]) / r["plain_s"] if r["plain_s"] > 0 else 0.0
        print(T("overhead_row", {"test": r["name"], "elapsed": "%.1f" % r["traced_s"], "seconds": "%.1f" % r["plain_s"],
                                 "pct": "%+.0f" % pct}))
        if r["rc_traced"] != r["rc_plain"]:
            print(T("overhead_rc", {"test": r["name"], "rc": r["rc_traced"], "code": r["rc_plain"]}))
    same = [r for r in rows if r["rc_traced"] == r["rc_plain"]]     # 兩次結果不同的不比(例:有追蹤那次被判沒驗、0 秒)
    tr, pl = sum(r["traced_s"] for r in same), sum(r["plain_s"] for r in same)
    if same and pl > 0:
        print(T("overhead_total", {"count": len(same), "elapsed": "%.1f" % tr, "seconds": "%.1f" % pl,
                                   "pct": "%+.0f" % (100.0 * (tr - pl) / pl)}))
    if len(same) < len(rows):
        print(T("overhead_left_out", {"n": len(rows) - len(same)}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib test", add_help=True, usage=T("usage"))
    ap.add_argument("tests", nargs="*")
    ap.add_argument("--all", action="store_true")      # 10-07:CI 用 —— 照 aib ship 的找法跑這個專案全部測試
    ap.add_argument("--guard", action="store_true")    # 10-11:守衛類(棘輪/單源/lint/hygiene,檔名規則 GUARD_NAME_RE),出貨前先過
    ap.add_argument("--scan", action="store_true")     # 10-11:掃樹類(rglob/os.walk 且提到 tools),新增 src/ tools/ 檔案時加跑
    ap.add_argument("--repo", default="")
    ap.add_argument("--strict", action="store_true", default=None)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=int, default=None)
    ap.add_argument("--resume", default=None, metavar="NAME")
    ap.add_argument("--why", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--cells", action="store_true")
    ap.add_argument("--jobs", type=int, default=None)          # 10-08:同時跑幾份(--all 預設 min(4, 核心數);1 = 一支一支跑)
    ap.add_argument("--shard-out", default=None, help=argparse.SUPPRESS)   # 分份的子行程:結果寫成 JSON 到這個檔
    ap.add_argument("--overhead", action="store_true")   # 10-08:量追蹤讓每支慢多少(每支跑兩次)
    ap.add_argument("--exclusive", action="store_true")  # 10-13:要量耗時 ⇒ 不管幾支都拿租約並標獨佔(小批也不准並跑)
    ap.add_argument("--wait", action="store_true")       # 10-13:小批遇到別人的獨佔租約 ⇒ 排隊等,不是直接停
    ap.add_argument("--changed", action="store_true")    # 10-13(取代 aib deliver):依改動挑測試,挑法同推包 select_changed
    ap.add_argument("--base", default="")                # --changed 的比較基準(預設 HEAD 與遠端 main 的分岔點)
    ap.add_argument("--dry", action="store_true")        # 只列出要跑哪些、幾支、估計幾秒,不跑
    a = ap.parse_args(argv)
    if a.estimate:
        return _estimate(a.repo or os.getcwd(), a.json)
    if a.resume is not None and not _RESUME_NAME_RE.fullmatch(a.resume):
        print(T("resume_badname", {"name": a.resume}), file=sys.stderr)
        return 2
    if not a.tests and not a.all and not a.guard and not a.scan and not a.changed:
        print(T("usage"), file=sys.stderr)
        return 2
    repo = _repo_of(a.repo or os.getcwd())
    if repo is None:
        print(T("no_repo", {"path": a.repo or os.getcwd()}), file=sys.stderr)
        return 2
    if a.all:
        found = all_tests(repo)
        if not found:                      # 找不到任何測試 ⇒ 紅,不能讓 CI 在「沒測到東西」時亮綠
            print(T("all_none", {"path": str(repo)}), file=sys.stderr)
            return 2
        a.tests = list(a.tests) + [t for t in found if t not in a.tests]
    if a.changed:
        sel, info = changed_tests(repo, a.base)
        if sel is None:
            print(T("changed_failed", {"what": info.get("why", "?"), "ref": info["ref"]}), file=sys.stderr)
            return 2
        print(T("changed_list", {"n": "%d" % len(sel), "ref": info["ref"], "sha": info["base"], "count": "%d" % info["untracked"]}),
              file=sys.stderr)
        if not sel and not a.tests and not a.guard and not a.scan and not a.all:
            print(T("changed_none"), file=sys.stderr)    # 改動挑不到任何測試 ≠ 綠:說清楚,rc 0 但不假裝跑過
            return 0
        a.tests = list(a.tests) + [t for t in sel if t not in a.tests]
    for flag, finder, key in (("guard", guard_tests, "guard_list"), ("scan", scan_tests, "scan_list")):
        if getattr(a, flag):
            picked = finder(repo)   # (變數不叫 found:test-aib-ship-ci 的刀錨 `if not found:` 要在這支檔裡恰好一次)
            if not picked:
                print(T("all_none", {"path": str(repo)}), file=sys.stderr)
                return 2
            print(T(key, {"n": "%d" % len(picked)}), file=sys.stderr)
            a.tests = list(a.tests) + [t for t in picked if t not in a.tests]
    names = []
    for arg in a.tests:
        rel = _normalize(repo, arg, also_repo=bool(a.repo))
        if rel is None:
            print(T(_why_not(repo, arg), {"path": arg, "value": str(repo)}), file=sys.stderr)
            return 2
        names.append(rel)
    if a.dry:
        # 10-13:只列不跑。估計 = 紀錄池每支上次實跑秒數(沒有紀錄的照 10 秒算、另外點名);沿用的會更快,這是上限
        base = baselines(runner._pool_file(repo), names)
        unknown = [n for n in names if n not in base]
        est = sum(base.get(n, 10.0) for n in names)
        for n in names:
            print(("%7.1f  " % base[n] if n in base else "      ?  ") + n)
        print(T("dry_summary", {"n": "%d" % len(names), "s": "%.0f" % est, "count": "%d" % len(unknown),
                                "limit": "%d" % _env_int("AIB_TEST_BIG_BATCH", BIG_BATCH)}))
        return 0
    if a.cells:
        return _cells(repo, names, a.json)
    if a.overhead:
        print_overhead(overhead(repo, names, timeout=a.timeout))
        return 0
    if a.why:
        entries = explain(repo, names, strict=a.strict)
        if a.fresh:
            for e in entries:
                e.update(verdict="must-run", why="fresh", detail="", changed=[], unmeasured=[], more=0)
        if a.json:
            print(json.dumps({"why": entries}, ensure_ascii=False))
        else:
            _print_explain(entries)
        return 0
    for var, val in RETIRED_SWITCHES:
        if os.environ.get(var) == val:
            print(T("switch_ignored", {"what": "%s=%s" % (var, val)}), file=sys.stderr)
    started = time.monotonic()

    def live(kind, params):
        sys.stderr.write(T("live_" + kind, params) + "\n")
        sys.stderr.flush()

    if a.shard_out:      # 分份的子行程:記憶體與租約由主行程管過了
        return _run_shard(repo, names, a, live)

    def say(line):
        sys.stderr.write(line + "\n")
        sys.stderr.flush()

    # 測試裡面又叫 aib test(AIB_TEST_TMPDIR 是外層 aib test 給每支測試的):外層已經管過,不等也不排隊,免得測試逾時
    nested = bool(os.environ.get("AIB_TEST_TMPDIR"))
    state, cur = (None, None) if nested else _lease_now()
    inherited = state == "held" and bool(cur) and _holder_is_ancestor(cur) is True
    # 先等記憶體、再排隊:拿著租約等記憶體會連推包一起擋住(審查)。
    #   不等的:測試裡面(nested)、推包 / 交件檢查的殼底下(機器是它的)、CI(runner 每支測試前自己會等)、
    #   HOME 在暫存區(多半是測試治具直接跑的 aib test,AIB_TEST_TMPDIR 不在時的退路)
    skip_mem = nested or inherited or _ci_or_fixture_home()
    big = len(names) > _env_int("AIB_TEST_BIG_BATCH", BIG_BATCH)
    mem = {"waited": 0, "gave_up": False, "free": None} if skip_mem else wait_for_memory(say, big=big)
    if nested:
        lease = {"run_id": None, "waited": 0, "why": "nested"}
    elif inherited:
        lease = {"run_id": None, "waited": 0, "why": "inherited"}
    else:
        excl = None if (big or a.exclusive) else exclusive_block(cur if state == "held" else None)
        if excl is not None and not a.wait:
            # 10-13:別人在量耗時(標了獨佔)⇒ 小批不並跑、不默默等(AI 的 Bash 2~10 分鐘就逾時);講持有者、到幾點、怎麼排
            say(T("lease_exclusive_blocked", {"name": excl.get("owner", "?"), "what": excl.get("what", "?"),
                                              "at": excl.get("expiry_at", "?")}))
            return RC_EXCLUSIVE
        lease = big_batch_lease(len(names), repo, say, force=bool(a.exclusive or excl is not None),
                                exclusive=bool(a.exclusive), names=names)
        if lease["why"] == "exclusive_held":
            return RC_EXCLUSIVE
    try:
        return _main_run(repo, names, a, live, started, lease, mem)
    finally:
        release_batch_lease(lease["run_id"])


def _run_shard(repo, names, a, live):
    """分份的子行程:進度照常即時印(stderr),結果交給主行程合併印(一份總結、一筆統計)。"""
    res = run_tests(repo, names, strict=a.strict, fresh=a.fresh, timeout=a.timeout, live=live, resume=a.resume)
    res["tmp_leaks"] = list(_TMP_LEAKS)   # 10-09:交回主行程,總表才印得出「哪支留了東西」(以前分份跑這行永遠不見)
    Path(a.shard_out).write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    return res["rc"]


def _auto_compact(repo):
    """紀錄池太大就壓縮(reuse_index.auto_compact)。10-15 D2:以前在 main 一開頭、拿租約之前做,而且沒給路徑
    ⇒ 被租約擋下來的那一輪已經改寫過池;分份的每個子行程各壓一次;壓的是共用目錄裡**所有專案**的池(含 shared-*)。
    現在只在主行程、確定要跑之後(拿到租約、進 _main_run)才做,只壓這個專案自己的池。"""
    for rep in reuse_index.auto_compact([runner._pool_file(repo)]):
        print(T("pool_compacted", {"n": "%d" % reuse_index.AUTO_COMPACT_KEEP,
                                   "what": "%s %d → %d%s" % (Path(rep["file"]).name, rep["rows"], rep["kept"],
                                                             (" (" + rep["refused"] + ")") if rep["refused"] else "")}),
              file=sys.stderr)


def _main_run(repo, names, a, live, started, lease, mem):
    _auto_compact(repo)
    prev_actions = [] if a.resume else _prev_actions()
    if prev_actions:      # v2.1:開跑先讀上一輪的建議,跑完逐條判
        sys.stderr.write(i18n_lib.t("aib.advice.prev_loaded", {"n": len(prev_actions)}, locale=LOC) + "\n")
    jobs = a.jobs if a.jobs is not None else _default_jobs(a, names)
    if jobs > 1 and len(names) > 1:
        res = run_sharded(repo, names, jobs, a)
    else:
        res = run_tests(repo, names, strict=a.strict, fresh=a.fresh, timeout=a.timeout,
                        live=None if a.json else live, resume=a.resume)
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "tests"} | {
            "mem_wait": mem, "lease_wait": {k: lease[k] for k in ("waited", "why")}} | {
            "tests": [{k: r.get(k) for k in ("name", "rc", "reused", "elapsed_s", "baseline_s", "slow")}
                      for r in res["tests"]],
            "unverified": [{"name": r["name"], "needs": r.get("needs")} for r in res["tests"] if r.get("unverified")],
            "skipped": {r["name"]: skipped_cells(r.get("output")) for r in res["tests"]
                        if r["rc"] == 0 and skipped_cells(r.get("output"))}},
            ensure_ascii=False))
        return res["rc"]
    print(res["line1"])
    if len(names) > 1:
        note = lease_note() if lease["why"] != "big" else T("lease_batch_mine", {"n": len(names)})
        if note:
            print(note)
    if lease["why"] == "gave_up":
        print(T("lease_queue_gave_up_note", {"waited": lease["waited"]}))
    elif lease["waited"]:
        print(T("lease_queue_note", {"waited": lease["waited"]}))
    if mem["gave_up"]:       # 等滿還是不夠、照跑了:結果可能被別的程式擠到,要寫在結果裡(跟沒驗的一樣顯眼)
        print(T("mem_gave_up_note", {"waited": mem["waited"], "count": mem["free"], "limit": _env_int("AIB_TEST_MEM_MIN_MB", MEM_MIN_MB)}))
    elif mem["waited"]:
        print(T("mem_waited_note", {"waited": mem["waited"]}))
    if a.strict or a.fresh:
        print(T("no_reuse_this_run"))
    elif _no_reuse_env():
        print(T("no_reuse_env_on"))
    for r in res["tests"]:
        if r.get("unverified"):
            print(T("row_unverified", {"test": r["name"], "what": " ".join(r.get("needs") or ())}))
        elif r.get("resumed"):
            print(T("row_resumed", {"test": r["name"], "name": a.resume}))
        elif r.get("same_package"):
            print(T("row_same_package", {"test": r["name"], "name": r["same_package"], "rc": r["rc"]}))
        elif r["reused"]:
            line = T("row_reused", {"test": r["name"]})
            if skipped_cells(r.get("output")):
                line += T("row_skipped", {"n": skipped_cells(r.get("output"))})
            print(line)
        else:
            line = T("row_ran", {"test": r["name"], "rc": r["rc"], "elapsed": r.get("elapsed_s", 0)})
            if r.get("slow"):
                base = r.get("baseline_s")
                el = r.get("elapsed_s")
                if isinstance(base, (int, float)) and base > 0 and isinstance(el, (int, float)):
                    line += T("row_slow", {"base": "%.1f" % base,
                                           "pct": "%d" % round((el / base - 1) * 100)})
                else:
                    line += T("row_slow", {"base": "%.1f" % (base or 0), "pct": "?"})
            if r["rc"] == 0 and skipped_cells(r.get("output")):
                line += T("row_skipped", {"n": skipped_cells(r.get("output"))})
            print(line)
        if r["rc"] != 0 and not r.get("unverified") and r.get("output") and not r.get("same_package"):
            sys.stderr.write(T("output_banner", {"test": r["name"]}) + "\n" + r["output"])
            if not r["output"].endswith("\n"):
                sys.stderr.write("\n")
    green = sum(1 for r in res["tests"] if r["rc"] == 0)
    unv = [r for r in res["tests"] if r.get("unverified")]
    print(T("summary", {"total": len(res["tests"]), "green": green, "red": len(res["tests"]) - green - len(unv),
                        "elapsed": "%.1f" % (time.monotonic() - started)}))
    if not a.resume:      # 推前檢查的分片由 pushgate 自己對帳(ship-stats),這裡不重記
        vs = versus(res["tests"], time.monotonic() - started,
                    lease_wait_s=lease.get("waited") or 0, mem_wait_s=mem.get("waited") or 0)
        vs["lease_ahead"] = lease.get("ahead")
        try:
            import shadow_tracer
            vs["tracer"] = shadow_tracer.tracer_code_digest()      # 記在這一輪的統計列(不進紀錄池),下一輪比對 aib 有沒有換版
        except Exception:  # noqa: BLE001
            pass
        req = []
        lines = versus_lines(vs, record_versus(repo, vs) if vs["without_s"] > 0 else [], who=_who(repo),
                             causes=rerun_causes(res["tests"]), required=req)
        advice = _advice(res["tests"], lease, a, prev_actions) if lines else []
        for ln in lines + advice:
            print(ln)
        step_summary(lines + (summary_extra(vs, res["tests"]) if lines else []))
        shadow_explain_emit(vs, res["tests"], fresh=bool(a.fresh), tree=(lines + advice) or None, required=req or None,
                            next_actions=_LAST_ADVICE.get("acts"))
    sk = [(r["name"], skipped_cells(r.get("output"))) for r in res["tests"] if r["rc"] == 0]
    sk = [(n, c) for n, c in sk if c]
    if sk:       # ★ 綠燈裡跳過的格子:跟沒驗的同一個顯眼程度
        print(T("skipped_summary", {"n": sum(c for _n, c in sk), "items": "、".join(
            "%s(%d)" % (n, c) for n, c in sorted(sk, key=lambda x: -x[1]))}))
    if _TMP_LEAKS:   # 10-08:跑完還在自己暫存資料夾裡留東西的測試(aib 已經替它刪了;這是告訴你哪支沒清)
        print(T("tmp_leak_summary", {"n": len(_TMP_LEAKS), "items": "、".join(
            "%s(%d)" % (t, k) for t, k in sorted(_TMP_LEAKS, key=lambda x: -x[1])[:10])}))
    if unv:      # ★ 沒驗的要跟紅燈一樣顯眼:總表下一行就列,每支附原因
        print(T("unverified_summary", {"n": len(unv), "items": "、".join(
            "%s(%s)" % (r["name"], " ".join(r.get("needs") or ())) for r in unv)}))
    slow = [r for r in res["tests"] if r.get("slow")]
    if res.get("state") != "fallback":
        nobase = sum(1 for r in res["tests"] if not r["reused"] and not r.get("unverified") and r.get("baseline_s") is None)
        print(T("slow_summary", {"slow": len(slow), "nobase": nobase}))
    return res["rc"]


def _on_term(signum, _frame):
    """10-09:被 kill(SIGTERM)時照樣走收尾(刪這批的 HOME、追蹤資料夾、每支自己的暫存資料夾,收它的背景程式)。
    以前直接死 ⇒ /tmp 留下 aib-test-trace-* 與還在跑的測試(推前檢查的 fail-fast 送 SIGINT,本來就會收尾)。"""
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, _on_term)
    sys.exit(main())
