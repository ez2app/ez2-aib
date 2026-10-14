#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib knife —— 試刀:證明測試真的在守(10-13 the maintainer 裁;沿用 aib major prove-gate「尺要先證明會紅」的點子)。

    aib knife --test tests/test-x.py [--test …] --file src/a.py --old '舊字串' --new '新字串' [--name 刀名] [--proves 證明什麼]
    aib knife --spec knives.json [--repo R]          # 多把刀:{"tests": [...], "knives": [{"name","proves","file","old","new"}]}

做什麼:
  1. 開一棵拋棄式工作樹(git worktree add --detach,HEAD),把呼叫端工作樹「還沒提交」的改動與還沒追蹤的新檔一起帶過去。
     ★ 刀只落在這棵副本上 —— 不在你的工作樹改檔再 git checkout 還原(還原會連沒提交的改動一起抹掉;批次跑著時改檔
       也會被工作樹守衛還原,10-11 / 10-13 都踩過)。
  2. 先跑一次「不落刀」(noop),再一把一把:把 file 裡剛好出現一次的 old 換成 new → 跑 → 記紅集合 → 換回來。
     測試一律經 aib test 的入口(test_entry.py --fresh --json,不沿用舊紀錄:刀要真的跑)。
  3. 判準用 tools/dispatch/knife-contract.py 的 judge()(單一來源):noop 要全綠、每把刀紅集合不空、刀要真的改到東西。
     old 找不到或出現不只一次 ⇒ 這把刀沒落(rc 6),不猜要換哪一處。
  4. 收據:<狀態根>/pending/knife/<HEAD8>-<時刻>.json;stdout 一把刀一行 + 最後一行 RESULT。
rc:0 全部咬到 / 3~6 照 knife-contract(取最嚴重)/ 2 用法錯或開不了副本。
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "cockpit"))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    return i18n_lib.t("aib.knife_run." + key, params, locale=LOC)


def _contract():
    spec = importlib.util.spec_from_file_location("knife_contract", str(ROOT / "tools" / "dispatch" / "knife-contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args, check=True, **kw):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), (p.stderr or "").strip()[-300:]))
    return p.stdout


def make_copy(repo):
    """拋棄式工作樹 = HEAD + 呼叫端沒提交的改動 + 還沒追蹤的新檔。回副本路徑。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aib-knife-"))
    tree = tmp / "tree"
    git(repo, "worktree", "add", "-q", "--detach", str(tree), "HEAD")
    diff = git(repo, "diff", "--binary", "HEAD")
    if diff.strip():
        p = subprocess.run(["git", "-C", str(tree), "apply", "--whitespace=nowarn", "-"], input=diff, capture_output=True,
                           text=True)
        if p.returncode != 0:
            raise RuntimeError("git apply: " + p.stderr.strip()[-300:])
    for rel in git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        if rel.strip():
            dst = tree / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(pathlib.Path(repo) / rel), str(dst))
    return tmp, tree


def drop_copy(repo, tmp, tree):
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)], capture_output=True,
                   stdin=subprocess.DEVNULL)
    shutil.rmtree(str(tmp), ignore_errors=True)


def run_tests(tree, tests):
    """經 aib test 入口跑,回紅集合(rc 非 0 的測試;沒跑到的也算紅 —— 沒跑 ≠ 綠)。"""
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "verify" / "test_entry.py"), "--repo", str(tree), "--fresh",
                        "--json", *tests], cwd=str(tree), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        rows = {r.get("name"): r.get("rc") for r in json.loads(p.stdout).get("tests", [])}
    except (ValueError, AttributeError):
        return sorted(tests), "rc=%d %s" % (p.returncode, (p.stderr or "").strip()[-200:])
    return sorted(t for t in tests if rows.get(t) != 0), None


def land(tree, knife):
    """落刀:old 剛好出現一次才換。回 (原內容 or None, 落了沒)。"""
    f = tree / knife["file"]
    try:
        src = f.read_text(encoding="utf-8")
    except OSError:
        return None, False
    if src.count(knife["old"]) != 1 or knife["old"] == knife["new"]:
        return src, False
    f.write_text(src.replace(knife["old"], knife["new"]), encoding="utf-8")
    return src, True


def state_root():
    for k in ("EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"):
        if os.environ.get(k):
            return pathlib.Path(os.environ[k])
    return pathlib.Path.home() / ".ez2-aib"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aib knife", description=T("desc"))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--spec", default="")
    ap.add_argument("--test", action="append", default=[])
    ap.add_argument("--file", default="")
    ap.add_argument("--old", default=None)
    ap.add_argument("--new", default=None)
    ap.add_argument("--name", default="knife-1")
    ap.add_argument("--proves", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if a.spec:
        spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
        tests, knives = list(spec.get("tests") or []), list(spec.get("knives") or [])
    else:
        tests = a.test
        knives = [{"name": a.name, "proves": a.proves or a.name, "file": a.file, "old": a.old, "new": a.new}] \
            if a.file and a.old is not None and a.new is not None else []
    if not tests or not knives or any(not k.get("file") or k.get("old") is None or k.get("new") is None for k in knives):
        print(T("usage"), file=sys.stderr)
        return 2
    for k in knives:
        k.setdefault("proves", k.get("name", ""))
    repo = pathlib.Path(git(a.repo, "rev-parse", "--show-toplevel").strip())
    head = git(repo, "rev-parse", "HEAD").strip()
    try:
        tmp, tree = make_copy(repo)
    except (RuntimeError, OSError) as e:
        print(T("copy_failed", {"err": e}), file=sys.stderr)
        return 2
    results, rows = {}, []
    try:
        red, err = run_tests(tree, tests)
        results["noop"] = {"red_set": red, "byte_identical": True}
        rows.append({"variant": "noop", "red_set": red, "landed": None, "err": err})
        print(T("row_noop", {"n": len(red), "what": ", ".join(red) or "-"}))
        for k in knives:
            orig, landed = land(tree, k)
            red = []
            if landed:
                red, err = run_tests(tree, tests)
                (tree / k["file"]).write_text(orig, encoding="utf-8")
            results[k["name"]] = {"red_set": red, "byte_identical": not landed}
            rows.append({"variant": k["name"], "file": k["file"], "landed": landed, "red_set": red, "proves": k["proves"]})
            key = "row_bit" if landed and red else "row_miss" if landed else "row_not_landed"
            print(T(key, {"name": k["name"], "n": len(red), "what": ", ".join(red) or "-", "path": k["file"]}))
    finally:
        drop_copy(repo, tmp, tree)
    problems = _contract().judge([{"name": k["name"], "proves": k["proves"]} for k in knives], results)
    out = pathlib.Path(a.out) if a.out else state_root() / "pending" / "knife"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%m%d-%H%M%S")
    receipt = out / ("%s-%s.json" % (head[:8], stamp))
    receipt.write_text(json.dumps({"head": head, "tests": tests, "rows": rows,
                                   "problems": [{"rc": rc, "msg": m} for rc, m in problems]},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc = max([r for r, _ in problems] or [0])
    bit = sum(1 for r in rows if r["variant"] != "noop" and r["landed"] and r["red_set"])
    print("RESULT knives=%d bit=%d rc=%d receipt=%s" % (len(knives), bit, rc, receipt))
    for _, m in problems:
        print(m, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
