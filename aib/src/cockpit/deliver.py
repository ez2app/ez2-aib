#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""交件閘(大綱 §88.2 第 2 條,2026-09-11):交件**之前**先在自己這邊把該跑的全跑一遍.

    python3 src/cockpit/deliver.py <tip> [--repo R] [--base origin/main] [--out DIR]

為什麼要有這一支:09-11 當天驗證抓到 12 格真紅,每一格都是「交了才被驗證抓到」——
交件的人跑的是自己想得到的幾支,而驗證跑的是**從動到的檔導出的**那一批.
兩個母體不同,差集裡的紅在交件前一個都看不到.本閘用**同一種取法**先算一次.

母體怎麼圈(性質圈,不用名字清單):
  1. 候選對 base 的 diff 檔清單(三點:從共同祖先算起,與 reachability-scan 同一基準).
  2. 產品檔 = diff 裡 src/ tools/ bin/ 底下的檔.
  3. 直接消費者 = tests/test-*.py 與 tools/probes/ 裡**在候選樹上**引用那些檔的
     (完整相對路徑、帶邊界的檔名、或 python import 該模組名),與驗證同一組 grep.
  4. diff 裡動到的 tests/test-*.py 本身.
  5. tools/governance/witness-always-run.txt(候選樹那一份)全列 —— 這些臂守的是集合的
     性質,任何從 diff 導出的母體都到不了它們,而它們正是會因為別人的改動而紅的那種.

★ tools/probes/ 的 rc 契約與 tests/ 不同(非零至少四種意思:探測失敗/進度/真紅/逾時),
  所以它們**列進母體但預設不跑**(`ran=false`,理由逐字寫進收據);要跑加 `--run-probes`,
  跑了的 rc 照抄,不解讀.列而不跑與「沒有這一類」在收據上不同形 —— 這是刻意的.

逐支怎麼跑:`python3 <test>`,stdin=DEVNULL(呼叫者的 stdin 不得成為測試的隱藏輸入),
艙導向變數整組洗掉(名單問候選樹的 cabin_resolve.sh --env-names,洗不到 ⇒ rc=2),
usage-sandbox 四件隔離(HOME / EZ2_USAGE_DIR / EZ2_CODEX_SESSIONS_DIR / PATH=受控替身目錄(含指向直譯器的連結)+
--extra-path 明示目錄+系統目錄,**不接呼叫者的 PATH**),
`EZ2_USAGE_PROBE_LIVE=0`,`EZ2_AIB_AI_VIEW`/`EZ2_AIB_PANES` 指沙箱(與驗證同),
每支牆鐘上限 `--timeout` 秒(預設 600);逾時記 rc=124 與 `timed_out=true`,不當綠.

到達性:跑 `tools/governance/reachability-scan.py --base <base> --candidate <tip>`,
讀最後一行 JSON 的 status / blocking.掃描器只讀**已提交**的樹,所以 tip 要先 commit.
掃描器預設取候選樹那一份(`--scanner` 可指別份,治具用).

收據:`<out>/<tip8>.json`(tip、base、母體、逐支 rc、到達性、時戳),
`--out` 預設 `<狀態根>/pending/deliver`(狀態根 = EZ2_AIB_STATE_ROOT > EZ2_AIB_HOME > ~/.ez2-aib,
與 seats_dir_lib 同一式子,不寫死家目錄).stdout 只印一行摘要.

rc:
  0 = 母體全綠且到達性 ok,可交.
  1 = 有紅(逐支點名;含逾時、刪掉/缺檔的臂 unmeasured、到達性四件(rc/結構/status/blocking)任一不成立).
      **紅那一行不含「可交」二字.**
  2 = 引數或樹狀態不合法:不是 git 倉、tip 或 base 解不出來、工作樹髒、`git status` 查不到、
      候選樹沒有每次必跑名單、艙導向名單問不到(洗不乾淨就不跑).
      ★ 「查不到」與「乾淨」在 porcelain 輸出上同形(都是空),所以 rc 另判,不併.

★ 這一支證得了「這批母體在這台機器、這個時刻跑得綠」,證不了驗證那一端會不會多算一支
  (驗證還會把 pinned-construct-scan 等閘一起算).它是交件前的**下界**,不是驗證的替身.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import i18n_lib  # noqa: E402
import seats_dir_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")

SCHEMA = "aib-deliver/v1"
ALWAYS_RUN_REL = "tools/governance/witness-always-run.txt"
SCANNER_REL = "tools/governance/reachability-scan.py"
PRODUCT_RE = re.compile(r"^(src|tools|bin)/")
TEST_RE = re.compile(r"^tests/test-.*\.py$")
PROBE_RE = re.compile(r"^tools/probes/.+\.(py|sh)$")
CONSUMER_PATHSPECS = ("tests/test-*.py", "tools/probes/*")
TIMEOUT_RC = 124
# ★ 系統目錄自己列,不用 os.defpath:Python 的 defpath 是 ":/bin:/usr/bin" —— 開頭那個空項是「目前目錄」
#   (等於把候選樹塞進 PATH),而且沒有 /usr/sbin(lsof 住那裡,ed13 那支臂第一版就紅在 lsof 找不到).
SYSTEM_PATH_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
TMP_ROOTS = ("/private/tmp", "/tmp")
PROBES_NOT_RUN = "probes_rc_contract_differs"


def T(key, params=None):
    return i18n_lib.t(key, params, locale=LOC)


class InputError(Exception):
    """rc=2:引數或樹狀態不合法,不是有紅."""


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, check=False, timeout=120,
    )
    if check and proc.returncode != 0:
        raise InputError(T("aib.deliver.git_failed", {
            "cmd": "git " + " ".join(args), "rc": proc.returncode,
            "err": proc.stderr.strip()[-300:]}))
    return proc


def resolve_repo(arg: str | None) -> Path:
    start = Path(arg or os.getcwd()).resolve()
    proc = git(start, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        raise InputError(T("aib.deliver.not_a_repo", {"path": str(start)}))
    return Path(proc.stdout.strip()).resolve()


def resolve_sha(repo: Path, ref: str, what: str) -> str:
    proc = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise InputError(T("aib.deliver.unresolved_ref", {"kind": what, "value": ref}))
    return proc.stdout.strip()


def require_clean(repo: Path) -> None:
    proc = git(repo, "status", "--porcelain")
    if proc.returncode != 0:
        raise InputError(T("aib.deliver.status_unmeasurable", {
            "rc": proc.returncode, "err": proc.stderr.strip()[-200:]}))
    if proc.stdout.strip():
        rows = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        raise InputError(T("aib.deliver.dirty_tree", {
            "n": len(rows), "lines": "\n".join(rows[:10])}))


def tmp_root() -> str:
    for cand in TMP_ROOTS:
        if os.path.isdir(cand) and os.access(cand, os.W_OK | os.X_OK):
            return cand
    return tempfile.gettempdir()


def strip_tree_prefix(line: str, sha: str) -> str:
    return line[len(sha) + 1:] if line.startswith(sha + ":") else line


def grep_consumers(repo: Path, tip: str, product: str) -> dict[str, list[str]]:
    """候選樹上引用這支產品檔的消費者 → {消費者路徑: [理由...]}.

    三種算命中(前兩種與驗證逐字同一組 grep):
      ① 完整 repo 相對路徑;② 檔名前面是 / 或引號、後面不是識別字元;
      ③ .py 產品:`import <stem>` / `from <stem>`(工單「import 或以字串引用」的 import 那一半).
    """
    hits: dict[str, list[str]] = {}
    base = os.path.basename(product)

    def add(proc: subprocess.CompletedProcess, why: str) -> None:
        if proc.returncode not in (0, 1):
            raise InputError(T("aib.deliver.git_failed", {
                "cmd": "git grep", "rc": proc.returncode, "err": proc.stderr.strip()[-200:]}))
        for line in proc.stdout.splitlines():
            rel = strip_tree_prefix(line.strip(), tip)
            if rel:
                hits.setdefault(rel, []).append(why)

    add(git(repo, "grep", "-l", "-F", product, tip, "--", *CONSUMER_PATHSPECS),
        "references:" + product)
    bounded = r"[/\"']" + re.escape(base) + r"([^A-Za-z0-9_-]|$)"
    add(git(repo, "grep", "-lE", bounded, tip, "--", *CONSUMER_PATHSPECS),
        "basename:" + product)
    if product.endswith(".py"):
        stem = re.escape(Path(product).stem)
        # ★ POSIX ERE:git grep 在 macOS 走 regcomp,`\s` / `\b` 不是它的字彙(靜默零命中),
        #   所以用 [[:space:]] 與「後面不是識別字元」寫邊界.
        add(git(repo, "grep", "-lE",
                r"^[[:space:]]*(from|import)[[:space:]]+" + stem + r"([^A-Za-z0-9_]|$)",
                tip, "--", *CONSUMER_PATHSPECS), "imports:" + product)
    return hits


def always_run_list(repo: Path, tip: str) -> list[str]:
    """候選樹那一份每次必跑名單.**缺檔 ⇒ rc=2 具名擋下**,不默認完整.

    ★ ef56b 09-11 複審 P1-2:第一版缺檔只記 note 仍放行 —— 名單裡那支 rc=1 的不變量臂被漏掉,
      stdout 印「可交」.驗證對歷史候選的豁免(main-merge-witness.sh:129–139)不是交件閘的事:
      交件閘看的永遠是「現在要交的這一顆」,它沒有名單就是沒有母體的一半.
    """
    proc = git(repo, "show", f"{tip}:{ALWAYS_RUN_REL}")
    if proc.returncode != 0:
        raise InputError(T("aib.deliver.always_run_missing", {"path": ALWAYS_RUN_REL, "sha": tip[:8]}))
    rows = []
    for line in proc.stdout.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            rows.append(line)
    return rows


def tree_has(repo: Path, tip: str, rel: str) -> bool:
    return git(repo, "cat-file", "-e", f"{tip}:{rel}").returncode == 0


def build_population(repo: Path, tip: str, merge_base: str) -> dict:
    proc = git(repo, "diff", "--name-only", merge_base, tip, check=True)
    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    products = [p for p in changed if PRODUCT_RE.match(p)]
    members: dict[str, dict] = {}

    def member(rel: str, why: str) -> None:
        row = members.setdefault(rel, {
            "path": rel,
            "kind": "tests" if TEST_RE.match(rel) else ("probes" if PROBE_RE.match(rel) else "other"),
            "why": [],
            "present": tree_has(repo, tip, rel),
        })
        if why not in row["why"]:
            row["why"].append(why)

    # ★ ef56b P1-3:動到的臂**不先過濾存在**.刪掉的臂在候選樹缺檔 ⇒ present=false ⇒ unmeasured ⇒ 紅,
    #   與驗證同語意(main-merge-witness.sh:93 全放進 ARMS,:205 跳過算 unmeasured,:290 非零收尾).
    #   第一版先 tree_has 過濾,刪臂就從母體消失,兩邊都「2 支」而不是同兩支.
    for rel in changed:
        if TEST_RE.match(rel):
            member(rel, "changed")
    for rel in always_run_list(repo, tip):
        member(rel, "always-run")
    for prod in products:
        for rel, whys in grep_consumers(repo, tip, prod).items():
            for why in whys:
                member(rel, why)
    return {
        "changed": changed,
        "products": products,
        "members": [members[k] for k in sorted(members)],
    }


def cabin_env_names(tree: Path) -> list[str]:
    """艙導向變數的名字集合,問候選樹的產品自己(`cabin_resolve.sh --env-names`),不手列.

    與 tests/aib_cabin_wash.product_cabin_env 同一條規則的產品端等價.
    rc≠0 或回空 ⇒ InputError(rc=2):洗不乾淨就不能宣稱隔離,「探測失敗」不得等於「沒有導向變數」.
    """
    script = tree / "src" / "cockpit" / "cabin_resolve.sh"
    proc = subprocess.run(["bash", str(script), "--env-names"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          timeout=60, check=False)
    names = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if proc.returncode != 0 or not names:
        raise InputError(T("aib.deliver.wash_unmeasurable", {
            "path": str(script), "rc": proc.returncode, "err": proc.stderr.strip()[-200:]}))
    return names


def controlled_path(sandbox_bin: Path, extra_paths=()) -> str:
    """子行程的 PATH:受控替身目錄(內有指向本直譯器的 python3 連結)+ 明示允許的目錄 + 系統目錄.**不接呼叫者的 PATH,也不接直譯器所在的共用目錄.**

    ★ ef56b P1-4:第一版「空 bin 前置、後接原 PATH」不是隔離 —— 操作者 bin 裡的假 codex 照樣被受測腳本執行到.
      要多開哪個目錄(例如 /opt/homebrew/bin 給 tmux)用 --extra-path 明示,留在收據裡.
    """
    # ★ 不把直譯器所在的共用目錄整個放進 PATH:witness 環境下 python3 解析到 /opt/homebrew/bin,那裡有真的
    #   codex/node/tmux ⇒ 探針看得到操作者的程式(09-11 19:1x 上線候選在驗證裡紅的那一格)。改成在受控 bin 放
    #   一個指向本直譯器的連結,PATH 只有受控目錄 + 明示目錄 + 四個系統目錄。
    for name in ("python3", "python"):
        link = Path(sandbox_bin) / name
        try:
            if not link.exists():
                os.symlink(sys.executable, link)
        except OSError:
            pass
    parts = [str(sandbox_bin)]
    parts += [str(p) for p in extra_paths if str(p)]
    parts += list(SYSTEM_PATH_DIRS)
    seen, out = set(), []
    for d in parts:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)


def usage_sandbox_env(base_env: dict, sandbox: Path, tree: Path, extra_paths=()) -> dict:
    """usage-sandbox 四件(HOME / EZ2_USAGE_DIR / EZ2_CODEX_SESSIONS_DIR / PATH)+ 探針關掉 +
    TMPDIR + locale,並洗掉驗證續跑變數(被繼承的話子行程會拿父輪的表當上一份).

    ★ 這一函式**刻意不碰任何艙導向名字**:`tools/debt/w8_read_set.py` 的規則是
      「一個函式只要碰到單源集合裡的名字,它讀的每一個名字都要在單源裡」——
      把 usage 沙箱與艙導向寫在同一個函式,HOME 等七個名字就會被算成單源漏列
      (09-11 本檔第一版就是這樣紅在 test-cabin-single-source-names).艙導向那兩個在下一函式.
    """
    home = sandbox / "home"
    usage = sandbox / "usage"
    codex = sandbox / "codex-sessions"
    empty_bin = sandbox / "bin"
    for d in (home, usage, codex, empty_bin):
        d.mkdir(parents=True, exist_ok=True)
    env = dict(base_env)
    env.pop("AIB_WITNESS_RESUME_FROM", None)
    env.pop("AIB_WITNESS_ROWS_JSONL", None)
    for name in cabin_env_names(tree):      # 艙導向整組先洗掉(名單來自產品,不是字面)
        env.pop(name, None)
    env["HOME"] = str(home)
    env["EZ2_USAGE_DIR"] = str(usage)
    env["EZ2_CODEX_SESSIONS_DIR"] = str(codex)
    env["PATH"] = controlled_path(empty_bin, extra_paths)
    env["EZ2_USAGE_PROBE_LIVE"] = "0"
    env["TMPDIR"] = tmp_root()
    env["EZ2_AIB_LOCALE"] = LOC
    # 跑考卷不得把候選樹弄髒:HEAD==tip 時考卷就在本樹跑,import 產品檔會生 __pycache__,
    # 第二支考卷起跑前的髒樹判定就會把自己的產物當成髒.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def cabin_view_env(env: dict, sandbox: Path) -> dict:
    """艙導向的兩個檔指沙箱(與驗證同):右欄視圖與 pane 表.只碰單源集合裡的名字."""
    view = sandbox / "view"
    view.mkdir(parents=True, exist_ok=True)
    env["EZ2_AIB_AI_VIEW"] = str(view / "ai-view.json")
    env["EZ2_AIB_PANES"] = str(view / "panes.json")
    return env


def sandbox_env(base_env: dict, sandbox: Path, tree: Path, extra_paths=()) -> dict:
    return cabin_view_env(usage_sandbox_env(base_env, sandbox, tree, extra_paths), sandbox)


def run_one(tree: Path, rel: str, env: dict, cap_seconds: float) -> dict:
    started = time.time()
    argv = [sys.executable, str(tree / rel)]
    try:
        proc = subprocess.run(
            argv, cwd=str(tree), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=cap_seconds, check=False,
        )
        out = proc.stdout or ""
        rc = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout
        out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
        rc = TIMEOUT_RC
        timed_out = True
    lines = [ln for ln in out.splitlines() if ln.strip()]
    tail = lines[-1] if lines else ""
    if timed_out:
        tail = T("aib.deliver.timed_out", {"seconds": int(cap_seconds)})
    names = []
    for match in re.finditer(r"^(?:FAIL|ERROR): (\S+)", out, re.M):
        if match.group(1) not in names:
            names.append(match.group(1))
    return {
        "path": rel, "rc": rc, "tail": tail[-300:], "timed_out": timed_out,
        "seconds": round(time.time() - started, 2), "red_names": names, "ran": True,
    }


def run_scanner(tree: Path, scanner: Path, base: str, tip: str, env: dict) -> dict:
    if not scanner.is_file():
        return {"status": "unmeasured", "reason": "scanner_missing",
                "detail": str(scanner), "blocking": []}
    proc = subprocess.run(
        [sys.executable, str(scanner), "--repo", str(tree), "--base", base, "--candidate", tip],
        cwd=str(tree), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600, check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"status": "unmeasured", "reason": "scanner_no_output", "structure_ok": False,
                "detail": proc.stderr.strip()[-300:], "blocking": [], "rc": proc.returncode}
    try:
        last = json.loads(lines[-1])
    except ValueError:
        return {"status": "unmeasured", "reason": "scanner_last_line_not_json", "structure_ok": False,
                "detail": lines[-1][-300:], "blocking": [], "rc": proc.returncode}
    structure_ok = (isinstance(last, dict) and isinstance(last.get("status"), str)
                    and isinstance(last.get("blocking"), list))
    return {
        "status": last.get("status") if isinstance(last, dict) else "unmeasured",
        "reason": last.get("reason") if isinstance(last, dict) else "scanner_last_line_not_object",
        "detail": last.get("detail") if isinstance(last, dict) else lines[-1][-300:],
        "blocking": (last.get("blocking") if isinstance(last, dict) and isinstance(last.get("blocking"), list)
                     else []),
        "structure_ok": structure_ok,
        "rc": proc.returncode,
    }


def reach_is_ok(reach: dict) -> bool:
    """四件都成立才放行:掃描器 rc==0、最後一行結構合法、status==ok、blocking==[].

    ★ ef56b P1-1:第一版只比 status 字串 —— rc=7 或 blocking 非空仍印「可交」,收據裡留著自相矛盾的欄.
      任一項量不到都是紅,不是綠.
    """
    return bool(
        reach.get("rc") == 0
        and reach.get("structure_ok") is True
        and reach.get("status") == "ok"
        and reach.get("blocking") == []
    )


def materialize(repo: Path, tip: str):
    """HEAD 已是 tip 就直接用本樹;否則開一棵 detached worktree,回 (樹, 清理函式)."""
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if head == tip:
        return repo, (lambda: None)
    tmp = tempfile.mkdtemp(prefix="aib-deliver-", dir=tmp_root())
    added = git(repo, "worktree", "add", "--detach", "-q", tmp, tip)
    if added.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise InputError(T("aib.deliver.worktree_failed", {"err": added.stderr.strip()[-200:]}))

    def cleanup() -> None:
        git(repo, "worktree", "remove", "--force", tmp)
        git(repo, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)

    return Path(tmp), cleanup


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_out() -> Path:
    root, _src = seats_dir_lib.state_root()
    return Path(root) / "pending" / "deliver"


def deliver(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    tip = resolve_sha(repo, args.tip, "tip")
    base = resolve_sha(repo, args.base, "base")
    require_clean(repo)
    mb = git(repo, "merge-base", base, tip)
    if mb.returncode != 0 or not mb.stdout.strip():
        raise InputError(T("aib.deliver.no_merge_base", {"base": args.base, "sha": tip[:8]}))
    merge_base = mb.stdout.strip()

    started_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    population = build_population(repo, tip, merge_base)
    tree, cleanup = materialize(repo, tip)
    sandbox = Path(tempfile.mkdtemp(prefix="aib-deliver-sbx-", dir=tmp_root()))
    runs: list[dict] = []
    reach: dict = {}
    env: dict = {}
    try:
        env = sandbox_env(os.environ, sandbox, tree, args.extra_path or ())
        for row in population["members"]:
            if not row["present"]:
                runs.append({"path": row["path"], "rc": None, "ran": False, "timed_out": False,
                             "tail": "", "seconds": 0.0, "red_names": [],
                             "why_not_run": "missing_in_tip"})
                continue
            if row["kind"] == "probes" and not args.run_probes:
                runs.append({"path": row["path"], "rc": None, "ran": False, "timed_out": False,
                             "tail": "", "seconds": 0.0, "red_names": [],
                             "why_not_run": PROBES_NOT_RUN})
                continue
            if row["kind"] == "probes" and row["path"].endswith(".sh"):
                runs.append(run_shell(tree, row["path"], env, args.timeout))
                continue
            runs.append(run_one(tree, row["path"], env, args.timeout))
        scanner = Path(args.scanner) if args.scanner else (tree / SCANNER_REL)
        reach = run_scanner(tree, scanner, base, tip, env)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        cleanup()

    reds = [r for r in runs if r["ran"] and r["rc"] != 0]
    missing = [r for r in runs if r.get("why_not_run") == "missing_in_tip"]
    reach_ok = reach_is_ok(reach)
    verdict = "ok" if (not reds and not missing and reach_ok) else "red"
    receipt = {
        "schema": SCHEMA,
        "tip": tip, "tip8": tip[:8], "base": args.base, "base_sha": base,
        "merge_base": merge_base, "repo": str(repo),
        "population": population,
        "child_path": env.get("PATH"),
        "runs": runs,
        "reach": reach,
        "verdict": verdict,
        "reds": [r["path"] for r in reds],
        "missing": [r["path"] for r in missing],
        "ran": sum(1 for r in runs if r["ran"]),
        "listed": len(runs),
        "started_at": started_ts,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "argv": sys.argv[1:],
    }
    out_dir = Path(args.out) if args.out else default_out()
    receipt_path = out_dir / (tip[:8] + ".json")
    atomic_write_json(receipt_path, receipt)

    if verdict == "ok":
        print(T("aib.deliver.summary_ok", {
            "sha": tip[:8], "base": args.base, "n": receipt["ran"],
            "status": reach.get("status"), "path": str(receipt_path)}))
        return 0
    named = ",".join(r["path"] for r in reds) or "-"
    reach_txt = "%s(rc=%s,blocking=%d%s)" % (
        reach.get("status"), reach.get("rc"), len(reach.get("blocking") or []),
        "" if reach.get("structure_ok") else ",structure=bad")
    print(T("aib.deliver.summary_red", {
        "sha": tip[:8], "n": len(reds), "tests": named,
        "missing": ",".join(receipt["missing"]) or "-",
        "status": reach_txt, "path": str(receipt_path)}))
    return 1


def run_shell(tree: Path, rel: str, env: dict, cap_seconds: float) -> dict:
    """`--run-probes` 才會走到:.sh 探針用 bash 跑,rc 照抄不解讀."""
    started = time.time()
    try:
        proc = subprocess.run(
            ["bash", str(tree / rel)], cwd=str(tree), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=cap_seconds, check=False,
        )
        out, rc, timed_out = proc.stdout or "", proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout
        out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
        rc, timed_out = TIMEOUT_RC, True
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return {"path": rel, "rc": rc, "tail": (lines[-1] if lines else "")[-300:],
            "timed_out": timed_out, "seconds": round(time.time() - started, 2),
            "red_names": [], "ran": True}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="deliver.py", description=T("aib.deliver.help_description"))
    ap.add_argument("tip", help=T("aib.deliver.help_tip"))
    ap.add_argument("--repo", help=T("aib.deliver.help_repo"))
    ap.add_argument("--base", default="origin/main", help=T("aib.deliver.help_base"))
    ap.add_argument("--out", help=T("aib.deliver.help_out"))
    ap.add_argument("--scanner", help=T("aib.deliver.help_scanner"))
    ap.add_argument("--timeout", type=float, default=600.0, help=T("aib.deliver.help_timeout"))
    ap.add_argument("--run-probes", action="store_true", help=T("aib.deliver.help_run_probes"))
    ap.add_argument("--extra-path", action="append", default=[], help=T("aib.deliver.help_extra_path"))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return deliver(args)
    except InputError as exc:
        sys.stderr.write(T("aib.deliver.invalid", {"detail": str(exc)}) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
