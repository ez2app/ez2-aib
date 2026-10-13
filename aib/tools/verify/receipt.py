#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""aib verify 收據與省時報告(verify-p1;設計稿 docs/DESIGN-aib-verify-0926.md §3.1、§4)。

輸入是「一批測試結果」,不管怎麼跑(執行器另外整合)。每一列:
    {"name": "tests/test-x.py", "rc": 0, "ran": 12, "fail_cells": [], "elapsed_s": 3.2,
     "reused": false, "sampled": false, "sample_match": null, "rerun_rc": null}
    reused       = 這次沒跑,沿用上次結果
    sampled      = 本來可沿用,被抽中照跑;sample_match 記照跑結果與沿用值一不一致
    rerun_rc     = 紅了單獨串行重跑一次的 rc(0 ⇒ 列為不穩定,不算新紅)
known_red(舊紅清單)只收「檔:格」,整檔紅報不出格名的用「檔:*」;餵裸檔名一律 ValueError——
    檔名級會把該檔任何新壞的格都算成舊紅、收據印「新紅 0」(a seat 09-26 第 1 輪重大 1)。

收據的 tree 欄 = `git write-tree`(索引的樹雜湊)。receipt_belongs_to_tree() 判斷收據屬不屬於現在這棵樹。

省時(§3.1,誠實不灌水):
    saved_s = full_estimate_s − serial_s
    full_estimate_s = 全庫每支「最近一次實跑耗時」加總;沒歷史的用同目錄中位數(estimate_basis=measured_partial:N)
    serial_s = 這次不含排隊的耗時,以串行計;平行多省的另列 parallel_saved_s,不混進主數字
    抽查不一致 ⇒ saved_s 記 0;沒有任何實跑歷史 ⇒ 建立基準,saved_s 為 null,不編數字
關閉模式下 record() 一個檔都不寫(收據、沿用池、帳本)。
"""
from __future__ import annotations

import hashlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src" / "cockpit"))
import i18n_lib  # noqa: E402
import resume_policy  # noqa: E402
import resume_store  # noqa: E402

SCHEMA = "aib-verify-receipt/v1"
TAIPEI = timezone(timedelta(hours=8))


def T(key, params=None):
    return i18n_lib.t(key, params, locale=os.environ.get("EZ2_AIB_LOCALE", "zh-TW"))


def _home(home=None):
    return Path(home or os.environ.get("HOME") or str(Path.home()))


def verify_dir(home=None):
    return _home(home) / ".ez2-aib" / "verify"


def ledger_path(home=None):
    return verify_dir(home) / "savings.jsonl"


# ---------------------------------------------------------------- 樹

def _git(repo, *args):
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return p.returncode, (p.stdout or "").strip()


def write_tree(repo):
    rc, out = _git(repo, "write-tree")
    return out if rc == 0 and out else None


def worktree_dirty(repo):
    """已追蹤檔有沒暫存的改動 ⇒ write-tree 看不到它們,收據不能算是這棵樹的。未追蹤檔不算。"""
    rc, _ = _git(repo, "diff", "--quiet")
    if rc is None:
        return None
    return rc != 0


def digest(receipt):
    body = {k: v for k, v in receipt.items() if k != "digest"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_belongs_to_tree(receipt, repo):
    """回 (ok, reason)。reason ∈ ok / not_a_receipt / digest_mismatch / git_failed / worktree_dirty / tree_mismatch。

    digest 只擋「收據被手改過」,擋不住會自己跑本模組重算的一方(見交件「沒驗的」)。
    """
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA or not receipt.get("tree"):
        return False, "not_a_receipt"
    if receipt.get("digest") != digest(receipt):
        return False, "digest_mismatch"
    tree = write_tree(repo)
    if tree is None:
        return False, "git_failed"
    dirty = worktree_dirty(repo)
    if dirty is None:
        return False, "git_failed"
    if dirty:
        return False, "worktree_dirty"
    if receipt["tree"] != tree:
        return False, "tree_mismatch"
    return True, "ok"


# ---------------------------------------------------------------- 輸入

def _num(v, default=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return v


def normalize_results(results):
    out = []
    seen = set()
    for r in results:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str) or not r["name"]:
            raise ValueError("result row needs a non-empty name")
        name = r["name"]
        if name in seen:
            raise ValueError("duplicate test name: " + name)
        seen.add(name)
        rc = r.get("rc")
        if isinstance(rc, bool) or not isinstance(rc, int):
            raise ValueError("rc must be int: " + name)
        reused = bool(r.get("reused"))
        el = _num(r.get("elapsed_s"))
        if el is None:
            # 沒量到不能寫成量到 0:0 秒會進沿用池變成「最近一次實跑耗時」,把全跑預估壓低(a seat 第 1 輪建議 3)
            if not reused:
                raise ValueError("a run row needs elapsed_s: " + name)
            el = 0.0
        if el < 0:
            raise ValueError("elapsed_s < 0: " + name)
        cells = r.get("fail_cells") or []
        if not isinstance(cells, list) or not all(isinstance(c, str) for c in cells):
            raise ValueError("fail_cells must be list[str]: " + name)
        sampled = bool(r.get("sampled"))
        if reused and sampled:
            raise ValueError("a sampled test ran; it cannot also be reused: " + name)
        sm = r.get("sample_match")
        out.append({
            "name": name,
            "rc": rc,
            "ran": _num(r.get("ran")),
            "fail_cells": list(cells),
            "elapsed_s": float(el),
            "reused": reused,
            "sampled": sampled,
            "sample_match": (bool(sm) if sampled and sm is not None else None),
            "rerun_rc": _num(r.get("rerun_rc")),
            "measurable": r.get("measurable", True) is not False,
        })
    return out


# ---------------------------------------------------------------- 省時

def estimate_full(universe, durations):
    """回 (full_estimate_s, n_estimated) 或 (None, None)(一支實跑耗時都沒有 ⇒ 無從估)。

    沒歷史的那支:同目錄有歷史的中位數;同目錄也沒有 ⇒ 全庫有歷史的中位數。兩種都算進 n_estimated。
    """
    if not durations:
        return None, None
    by_dir = {}
    for name, el in durations.items():
        by_dir.setdefault(os.path.dirname(name), []).append(el)
    all_med = statistics.median(durations.values())
    total, n_est = 0.0, 0
    for name in universe:
        if name in durations:
            total += durations[name]
            continue
        same = by_dir.get(os.path.dirname(name))
        total += statistics.median(same) if same else all_med
        n_est += 1
    return total, n_est


def compute_savings(rows, *, history, universe, elapsed_s, serial_s=None):
    """history = 這批之前的 {測試名: 最近一次實跑耗時}。不寫檔。"""
    elapsed_s = float(elapsed_s)
    serial = float(serial_s) if serial_s is not None else elapsed_s
    serial = max(serial, elapsed_s)
    parallel_saved = serial - elapsed_s
    mismatch = sum(1 for r in rows if r["sampled"] and r["sample_match"] is False)
    out = {
        "elapsed_s": round(elapsed_s, 3),
        "serial_s": round(serial, 3),
        "parallel_saved_s": round(parallel_saved, 3),
        "full_estimate_s": None,
        "saved_s": None,
        "estimate_basis": "baseline",
    }
    # 第一次(這批之前沒有任何實跑歷史):建立基準,不報數字。
    if not history:
        return out
    durations = dict(history)
    for r in rows:
        if not r["reused"]:
            durations[r["name"]] = r["elapsed_s"]
    names = list(universe) if universe else sorted(set(durations) | {r["name"] for r in rows})
    full, n_est = estimate_full(names, durations)
    if full is None:
        return out
    out["full_estimate_s"] = round(full, 3)
    out["estimate_basis"] = "measured_all" if n_est == 0 else "measured_partial:%d" % n_est
    # verify-p1-mismatch-zero-begin
    if mismatch:
        out["saved_s"] = 0.0
        return out
    # verify-p1-mismatch-zero-end
    out["saved_s"] = round(full - serial, 3)
    return out


# ---------------------------------------------------------------- 收據

def _cell_ids(r):
    """紅的格 id 一律「檔:格」;整檔紅而報不出格名的用「檔:*」,它只配得上同樣報不出格名的舊紅。"""
    return ["%s:%s" % (r["name"], c) for c in r["fail_cells"]] or [r["name"] + ":*"]


def normalize_known_red(known_red):
    """舊紅清單只收「檔:格」(或「檔:*」)。

    ★ a seat 09-26 第 1 輪重大 1:原本 `cid in known or r["name"] in known` 同時收檔名級,
      執行器餵 main 那份「9 支舊紅」檔名清單時,那 9 檔裡任何新壞的格都被算舊紅、收據印「新紅 0」
      —— 而「新紅 0」正是收據要人信的那一句。餵檔名要當場紅,不是靜靜放行。
    """
    out = set()
    for k in known_red or ():
        if not isinstance(k, str) or ":" not in k or not k.split(":", 1)[1]:
            raise ValueError("known_red entries must be 'file:cell' (or 'file:*'): %r" % (k,))
        out.add(k)
    return out


def build_receipt(results, *, repo, mode, elapsed_s, serial_s=None, queue_s=0.0, base=None,
                  universe=None, known_red=(), reverse_index_added=0, prove_fix=None, jev=None,
                  changed_files=None, home=None):
    """由一批結果產 §4 的收據(不寫檔)。mode 是這次實際走的模式(quick/strict)。"""
    if mode not in ("quick", "strict"):
        raise ValueError("mode must be quick or strict")
    # verify-p1-build-off-guard-begin
    # 關閉=行為和沒裝一樣:git write-tree 會把樹物件寫進 .git/objects,所以 build 也不准跑(a seat 一般 2)
    if resume_policy.is_off(repo):
        return None
    # verify-p1-build-off-guard-end
    rows = normalize_results(results)
    tree = write_tree(repo)
    if tree is None:
        raise RuntimeError("git write-tree failed")
    known = normalize_known_red(known_red)
    new_red, known_hit, flaky = [], [], []
    for r in rows:
        if r["rc"] == 0:
            continue
        if r["rerun_rc"] == 0:
            flaky.append(r["name"])
            continue
        for cid in _cell_ids(r):
            # verify-p1-known-red-match-begin
            if cid in known:
                known_hit.append(cid)
            # verify-p1-known-red-match-end
            else:
                new_red.append(cid)
    history = resume_store.last_run_durations(repo, home=home)
    sav = compute_savings(rows, history=history, universe=universe, elapsed_s=elapsed_s, serial_s=serial_s)
    uni = list(universe) if universe else None
    rec = {
        "schema": SCHEMA,
        "tree": tree,
        "base": base,
        "mode": mode,
        "created_at": datetime.now(TAIPEI).replace(microsecond=0).isoformat(),
        "seat": os.environ.get("EZ2_AIB_SEAT") or os.environ.get("AIB_SEAT") or "unknown",
        "repo_id": resume_store.repo_id(repo),
        "universe": len(uni) if uni is not None else None,
        "changed_files": changed_files,
        "selected": len(rows),
        "reverse_index_added": int(reverse_index_added or 0),
        "reused": sum(1 for r in rows if r["reused"]),
        "ran": sum(1 for r in rows if not r["reused"]),
        "sampled": sum(1 for r in rows if r["sampled"]),
        "unmeasurable": sum(1 for r in rows if not r["measurable"]),
        "sample_mismatch": sum(1 for r in rows if r["sampled"] and r["sample_match"] is False),
        "new_red": new_red,
        "known_red": known_hit,
        "flaky_rerun_green": flaky,
        "prove_fix": prove_fix,
        "jev": jev if jev is not None else {"used": False, "reason": "not_integrated"},
        "queue_s": round(float(queue_s or 0.0), 3),
        **sav,
        "tests": rows,
    }
    rec["digest"] = digest(rec)
    return rec


def record(receipt, repo, home=None):
    """寫收據檔、把實跑列進沿用池、記一列省時帳本。關閉模式回 None 且一個檔都不寫。"""
    # verify-p1-off-guard-begin
    if receipt is None or resume_policy.is_off(repo):
        return None
    # verify-p1-off-guard-end
    d = verify_dir(home) / "receipts" / receipt["repo_id"]
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    path = d / ("%s-%s-%s.json" % (stamp, receipt["tree"][:12], receipt["digest"][:8]))
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in receipt["tests"]:
        if not r["reused"]:
            resume_store.append_row(repo, {
                "kind": "run", "test": r["name"], "rc": r["rc"], "elapsed_s": r["elapsed_s"],
                "tree": receipt["tree"], "at": receipt["created_at"],
            }, home=str(_home(home)))
    lp = ledger_path(home)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": receipt["created_at"], "repo": receipt["repo_id"], "seat": receipt["seat"],
            "mode": receipt["mode"], "saved_s": receipt["saved_s"],
            "estimate_basis": receipt["estimate_basis"], "receipt_sha256": receipt["digest"],
        }, ensure_ascii=False) + "\n")
    return path


def find_receipt(repo, home=None):
    """現在這棵樹的最新一張有效收據;沒有回 None。"""
    rid = resume_store.repo_id(repo)
    d = verify_dir(home) / "receipts" / rid
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ok, _ = receipt_belongs_to_tree(rec, repo)
        if ok:
            return rec
    return None


# ---------------------------------------------------------------- 帳本

def _parse_at(s):
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return dt.astimezone(TAIPEI)


def ledger_rows(home=None):
    p = ledger_path(home)
    if not p.is_file():
        return []
    out = []
    for line in jsonl_safe.split_lines(p.read_text(encoding="utf-8")):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _ids(repo_id):
    """repo_id 可以是一個身分或一組(M1 過渡:新的專案身分 + 本樹舊的 git 目錄身分,累計不歸零)。"""
    return {repo_id} if isinstance(repo_id, str) else set(repo_id)


def _ids_of(repo):
    """這棵樹算累計要認的身分:M1 專案身分 + 舊身分。"""
    return tuple(dict.fromkeys(x for x in (resume_store.repo_id(repo), resume_store.legacy_repo_id(repo)) if x))


def totals(home=None, now=None, repo_id=None):
    """今天/本週(週一起)/本月累計省下秒數,台北時間。建立基準那幾列(saved_s=null)不算。"""
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    month0 = day0.replace(day=1)
    t = {"today": 0.0, "week": 0.0, "month": 0.0}
    for row in ledger_rows(home):
        if repo_id is not None and row.get("repo") not in _ids(repo_id):
            continue
        s = _num(row.get("saved_s"))
        at = _parse_at(row.get("at"))
        if s is None or at is None or at > now:
            continue
        if at >= month0:
            t["month"] += s
        if at >= week0:
            t["week"] += s
        if at >= day0:
            t["today"] += s
    return t


# ---------------------------------------------------------------- 給人看

def fmt_dur(sec):
    sec = int(round(abs(float(sec))))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return T("aib.verify.dur.hm", {"h": h, "m": m})
    if m:
        return T("aib.verify.dur.ms", {"m": m, "s": s})
    return T("aib.verify.dur.s", {"s": s})


def human_lines(receipt, home=None, now=None):
    """§4 兩行;第一行一律是省時那句(數字全部來自收據與帳本)。"""
    tot = totals(home, now=now, repo_id=receipt.get("repo_id"))
    took = fmt_dur(receipt["serial_s"])
    basis = receipt["estimate_basis"]
    if receipt["saved_s"] is None:
        line1 = T("aib.verify.line1_baseline", {"actual": took})
    elif receipt["sample_mismatch"]:
        line1 = T("aib.verify.line1_mismatch", {"actual": took, "n": receipt["sample_mismatch"]})
    elif receipt["saved_s"] < 0:
        line1 = T("aib.verify.line1_loss", {"delta": fmt_dur(receipt["saved_s"]), "actual": took,
                                            "total": fmt_dur(receipt["full_estimate_s"])})
    else:
        line1 = T("aib.verify.line1_saved", {"value": fmt_dur(receipt["saved_s"]), "actual": took,
                                             "total": fmt_dur(receipt["full_estimate_s"])})
    if basis.startswith("measured_partial:"):
        line1 += T("aib.verify.line1_partial", {"n": basis.split(":", 1)[1]})
    line1 += T("aib.verify.line1_today", {"v1": fmt_dur(tot["today"])})
    if receipt["parallel_saved_s"] > 0:
        line1 += T("aib.verify.line1_parallel", {"extra": fmt_dur(receipt["parallel_saved_s"])})

    uni = receipt["universe"] if receipt["universe"] is not None else "?"
    sample = (T("aib.verify.line2_sample_ok", {"n": receipt["sampled"]}) if not receipt["sample_mismatch"]
              else T("aib.verify.line2_sample_bad", {"n": receipt["sampled"], "bad": receipt["sample_mismatch"]}))
    pf = receipt.get("prove_fix")
    knives = (T("aib.verify.line2_knives", {"got": pf.get("turned_red", 0), "expected": pf.get("knives", 0)})
              if isinstance(pf, dict) else T("aib.verify.line2_knives_none"))
    # 佔位名刻意沿用字典既有的名字(test-annotate-i2-guard 釘住相異佔位名的母體)
    params = {"count": receipt["selected"], "total": uni, "used": receipt["reused"],
              "n": receipt["ran"], "detail": sample, "new": len(receipt["new_red"]),
              "red": len(receipt["known_red"]), "verdict": knives}
    if receipt.get("changed_files") is not None:
        params["files"] = receipt["changed_files"]
        line2 = T("aib.verify.line2_changed", params)
    else:
        line2 = T("aib.verify.line2", params)
    return [line1, line2]


def status_lines(repo, home=None, now=None):
    lines = []
    rid = resume_store.repo_id(repo) if repo else None
    pool = len(resume_store.last_run_durations(repo, home=str(_home(home)))) if repo else 0
    lines.append(T("aib.verify.status_pool", {"n": pool}))
    ids = set(_ids_of(repo)) if repo else None
    last = [r for r in ledger_rows(home) if ids is None or r.get("repo") in ids]
    if last:
        s = _num(last[-1].get("saved_s"))
        lines.append(T("aib.verify.status_last", {"value": fmt_dur(s)}) if s is not None
                     else T("aib.verify.status_last_baseline"))
    else:
        lines.append(T("aib.verify.status_last_none"))
    tot = totals(home, now=now, repo_id=_ids_of(repo) if repo else rid)
    lines.append(T("aib.verify.status_totals", {"v1": fmt_dur(tot["today"]),
                                                "v2": fmt_dur(tot["week"]),
                                                "v3": fmt_dur(tot["month"])}))
    return lines


def main(argv=None):
    """receipt.py check <收據檔> [--repo DIR]:rc 0=屬於現在這棵樹、1=不屬於、2=用法錯。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 4) or argv[0] != "check" or (len(argv) == 4 and argv[2] != "--repo"):
        print(T("aib.verify.receipt_usage"), file=sys.stderr)
        return 2
    repo = argv[3] if len(argv) == 4 else os.getcwd()
    try:
        rec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    ok, reason = receipt_belongs_to_tree(rec, repo)
    print(T("aib.verify.receipt_check_" + ("ok" if ok else "bad"), {"reason": reason}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
