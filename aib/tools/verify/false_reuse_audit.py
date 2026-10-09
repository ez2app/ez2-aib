#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""「沿用錯了幾次」的抽查量尺(docs/PLAN-shadow-cross-device-1002.md 第 3 步)。

所有報告都在講省了多少,沒有一個數字說「沿用錯了幾次」。不先量這個,跨裝置驗收沒有依據。
本檔從「判可沿用」的測試裡抽一批,**照樣實跑**(走 aib test 入口,會記列),比對結果:

  match       實跑綠 ⇒ 沿用對了
  real-miss   實跑紅,再跑兩次仍紅(三次都紅)⇒ 真的沿用錯了
  unstable    三次裡有綠有紅 ⇒ 時好時壞(實跑留下的紅列與舊綠列同鑰匙 ⇒ reuse_index 以後判 unstable-same-key)

真錯的處置(自動,不等人):
  1. 往沿用池寫一筆作廢列(那支測試 + 被沿用那一列的鑰匙前綴)⇒ 錯的那一列以後不再被任何人沿用。
  2. 寫停用旗標 cross-host-disabled.json ⇒ 別台機器的列一律不用,直到人看過並執行 `enable-cross-host`。
     (只有本機沿用錯,也停用跨主機:沿用錯代表量尺本身有洞,洞在哪還不知道。)

抽多少:預設 10%、至少 30 支(池不夠就全抽);一半從最慢的開始抽(省最多時間的那些最要緊),
一半隨機(種子寫進摘要,可重現);沿用自別台機器的列,在隨機那一半裡權重加倍。

CLI:
  false_reuse_audit.py audit --repo R [--tests F] [--pct 10] [--min 30] [--seed S] [--dry-run]
      stdout 一行 JSON:{"sampled", "match", "real_miss", "unstable", "seed", "details": […], "cross_host_disabled"}
      rc 0 = 沒有真錯;1 = 有真錯;2 = 參數不對;3 = 索引量不到(沒有東西可抽)
  false_reuse_audit.py enable-cross-host --reason "…"   人看過之後解除停用(留紀錄)

全量對答案(10-02 建議 2):抽查只看一成;要一次知道「如果照沿用規則,會錯幾支」,就在一輪全部實跑之前先記下判斷,
跑完再逐支比對。成本 = 本來會被沿用的那些也實跑(目前約一輪時間的 5%)。
  ★ 合主線後的**第一輪**不能用:追蹤器指紋換了,舊列全部對不上,預測會是「一支都不能沿用」,沒東西可比。
    要用第二輪(或之後任何一輪)。
  false_reuse_audit.py predict --repo R [--tests F] --out P.jsonl      開跑前:每支記 reuse / must-run 與理由
  false_reuse_audit.py score --predict P.jsonl --results ROWS.jsonl   跑完後:對照實際結果
      stdout 一行 JSON:predicted_reuse、would_be_right、would_have_hidden_red(預測可沿用、實際紅)、
      not_in_results、saved_s(預測可沿用且實際綠的上次秒數合計);rc 1 = 有藏紅,0 = 沒有
      實際結果只有一次:藏紅的那幾支也可能是時好時壞,清單要再各跑兩次才能定(audit 的 classify)。

合入見證/發版那條路(10-03,計畫 D):那條路判沿用的是 witness_shadow,每支的判斷留在證詞旁檔 `<證詞>.plan.tsv`
(一行 `<測試>\t<判斷 JSON>`,action=reuse 表示沿用了)。
  false_reuse_audit.py audit-plan --plan X.plan.tsv --repo <候選樹> [--pct 10] [--min 30] [--dry-run]
      從沿用的那些抽一批,在候選樹照樣實跑(走 aib test 入口),判法同 audit。只報告、不改證詞、不擋推送。
      真錯 ⇒ 寫停用跨主機旗標(證詞的上一份檔是唯讀證據,不在那裡寫作廢)。
  ★ 接進 main-merge-witness.sh 收尾那一行,等 a seat 的 feat/witness-order-notify-88330-1002 合主線再做(兩邊改同一支腳本)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import math
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reuse_index as ri  # noqa: E402
import shadow_tracer as st  # noqa: E402

PCT_DEFAULT = 10.0
MIN_DEFAULT = 30
RERUNS_ON_RED = 2          # 第一次紅之後再跑幾次(共三次)
AUDIT_REASON = "false-reuse-audit"


def sample_size(n_pool, pct=PCT_DEFAULT, min_n=MIN_DEFAULT):
    if n_pool <= 0:
        return 0
    return min(n_pool, max(int(min_n), int(math.ceil(n_pool * max(0.0, float(pct)) / 100.0)), 1))


def choose(candidates, durations=None, foreign=(), pct=PCT_DEFAULT, min_n=MIN_DEFAULT, seed=None):
    """回 (抽到的測試清單, 種子)。candidates = 判可沿用的測試;durations = {測試: 上次實跑秒數};
    foreign = 沿用列來自別台機器的測試。一半取最慢的,一半用種子隨機(別台的權重加倍)。"""
    pool = sorted(set(candidates))
    n = sample_size(len(pool), pct, min_n)
    if seed is None:
        seed = hashlib.sha256(("|".join(pool) + "|%s" % pct).encode("utf-8")).hexdigest()[:16]
    if n == 0:
        return [], seed
    durations = durations or {}
    slow_n = n // 2
    by_slow = sorted(pool, key=lambda t: (-(durations.get(t) or 0.0), t))
    picked = [t for t in by_slow[:slow_n] if (durations.get(t) or 0.0) > 0]
    rest = [t for t in pool if t not in picked]
    rnd = random.Random(seed)
    foreign = set(foreign)
    keyed = sorted(rest, key=lambda t: (rnd.random() / (2.0 if t in foreign else 1.0), t))
    picked += keyed[: n - len(picked)]
    return sorted(picked), seed


def classify(rcs):
    """實跑的結束碼清單 → match / real-miss / unstable。第一次綠就不再跑(清單只有一個 0)。"""
    if not rcs:
        raise ValueError("no runs")
    greens = sum(1 for rc in rcs if rc == 0)
    if greens == len(rcs):
        return "match"
    if greens == 0:
        return "real-miss"
    return "unstable"


def flag_path(pool_dir=None):
    return Path(pool_dir or st.shadow_dir_default()) / ri.CROSS_HOST_FLAG


def disable_cross_host(details, pool_dir=None, now=None):
    p = flag_path(pool_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = {"disabled_at": now or time.strftime("%Y-%m-%d %H:%M:%S"), "host_id": ri.host_id(),
           "reason": AUDIT_REASON, "tests": [d["test"] for d in details]}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p))
    return obj


def enable_cross_host(reason, pool_dir=None, now=None):
    """人看過之後解除。旗標不刪掉證據:改名成 .resolved-<時間> 並寫進解除理由。"""
    p = flag_path(pool_dir)
    if not p.is_file():
        return {"enabled": True, "was_disabled": False}
    stamp = now or time.strftime("%Y%m%d-%H%M%S")
    obj = json.loads(p.read_text(encoding="utf-8"))
    obj.update(resolved_at=stamp, resolved_reason=reason)
    dst = p.with_name("%s.resolved-%s" % (p.name, stamp))
    dst.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    p.unlink()
    return {"enabled": True, "was_disabled": True, "record": str(dst)}


def audit(repo, tests=None, pct=PCT_DEFAULT, min_n=MIN_DEFAULT, seed=None, run=None, pool_file=None,
          dry_run=False, environ=None):
    """抽查一輪。run(test) → 結束碼(預設:走 aib test 入口 fresh 實跑);pool_file = 寫作廢列的列檔(預設:執行器的池)。"""
    repo = str(Path(repo).resolve())
    if environ is None:
        # 判沿用要用 aib test 跑測試時的同一份乾淨環境(否則環境指紋永遠對不上,一支都抽不到)
        import shutil
        import tempfile
        import runner
        home = tempfile.mkdtemp(prefix="aib-audit-home-", dir=runner.TMP_ROOT)
        try:
            environ = runner.isolated_test_env(home, repo)
            return audit(repo, tests, pct, min_n, seed, run, pool_file, dry_run, environ)
        finally:
            shutil.rmtree(home, ignore_errors=True)
    idx = ri.build_index([], repo=repo)
    if idx.degraded:
        return {"degraded": idx.degraded, "sampled": 0, "match": 0, "real_miss": 0, "unstable": 0, "details": []}
    me = ri.host_id()
    names = list(tests) if tests else sorted(idx.latest)
    reused, durations, foreign, used = [], {}, set(), {}
    for t in names:
        verdict, _why, detail = idx.decide(repo, t, changed=[], environ=environ)
        if verdict != "reuse":
            continue
        row = idx.row_for(t, detail) or {}
        reused.append(t)
        used[t] = (detail, row)
        d = row.get("duration_s")
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            durations[t] = float(d)
        if row.get("host_id") not in (None, "", me):
            foreign.add(t)
    picked, seed = choose(reused, durations, foreign, pct, min_n, seed)
    out = {"pool_reusable": len(reused), "sampled": len(picked), "seed": seed, "match": 0, "real_miss": 0,
           "unstable": 0, "details": [], "dry_run": bool(dry_run), "cross_host_disabled": False}
    if dry_run:
        out["details"] = [{"test": t, "foreign": t in foreign, "prev_duration_s": durations.get(t)} for t in picked]
        return out
    if run is None:
        run = _entry_runner(repo)
    misses = []
    for t in picked:
        rcs = [run(t)]
        if rcs[0] != 0:
            for _ in range(RERUNS_ON_RED):
                rcs.append(run(t))
        verdict = classify(rcs)
        detail, row = used[t]
        d = {"test": t, "verdict": verdict, "rcs": rcs, "foreign": t in foreign,
             "reused_key": detail, "reused_host_id": row.get("host_id"), "reused_time": row.get("time")}
        out["details"].append(d)
        out[verdict.replace("-", "_")] += 1
        if verdict == "real-miss":
            misses.append(d)
            pf = pool_file or _pool_file(repo)
            ri.write_void(pf, {"test": t, "key_prefix": detail, "green_only": True}, AUDIT_REASON)   # 只作廢錯的綠,留下剛跑出的紅
    if misses:
        disable_cross_host(misses, pool_dir=Path(pool_file).parent if pool_file else None)
        out["cross_host_disabled"] = True
    record_audit(repo, out, path=(Path(pool_file).parent / "ship-audits.jsonl") if pool_file else None)   # 測試給了自己的池 ⇒ 帳也記在那裡
    return out


def _audits_path(path=None):
    return Path(path or (st.shadow_dir_default() / "ship-audits.jsonl"))


def record_audit(repo, out, path=None):
    """10-06(開發檔 §20 第 1 項):每次抽查記一行,aib ship stats 的帳把它放在省時旁邊。記不下來不影響抽查。"""
    try:
        f = _audits_path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        row = {"repo": ri.repo_identity(str(repo)) or "", "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "pool_reusable": out.get("pool_reusable", 0), "sampled": out.get("sampled", 0), "match": out.get("match", 0),
               "unstable": out.get("unstable", 0), "real_miss": out.get("real_miss", 0),
               "misses": [d["test"] for d in out.get("details") or [] if d.get("verdict") == "real-miss"]}
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_audits(repo, path=None, rid=None):
    mine = rid if rid is not None else (ri.repo_identity(str(repo)) or "")
    try:
        rows = _load_jsonl(_audits_path(path))
    except OSError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("repo") == mine]


def predict(repo, tests=None, environ=None):
    """每支測試現在會被判 reuse 還是 must-run(不跑任何測試)。回列清單,第一列是說明。"""
    repo = str(Path(repo).resolve())
    if environ is None:
        import shutil
        import tempfile
        import runner
        home = tempfile.mkdtemp(prefix="aib-predict-home-", dir=runner.TMP_ROOT)
        try:
            return predict(repo, tests, runner.isolated_test_env(home, repo))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    idx = ri.build_index([], repo=repo)
    head = {"predict": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "host_id": ri.host_id(),
            "degraded": idx.degraded or "", "tracer": st.tracer_code_digest()}
    out = [head]
    for t in (list(tests) if tests else sorted(idx.latest)):
        verdict, why, detail = idx.decide(repo, t, changed=[], environ=environ)
        row = idx.row_for(t, detail) if verdict == "reuse" else {}
        d = (row or {}).get("duration_s")
        out.append({"test": t, "verdict": verdict, "reason": why,
                    "reused_host_id": (row or {}).get("host_id"),
                    "prev_duration_s": d if isinstance(d, (int, float)) and not isinstance(d, bool) else None})
    return out


def score(pred_rows, result_rows):
    """預測 × 實際結果(列要有 test 與 rc;同一支多列取最後一列)。純函式。"""
    actual = {}
    for r in result_rows:
        if isinstance(r, dict) and isinstance(r.get("test"), str):
            actual[r["test"]] = r
    out = {"predicted_reuse": 0, "would_be_right": 0, "would_have_hidden_red": 0, "not_in_results": 0,
           "predicted_must_run": 0, "saved_s": 0.0, "hidden_red": [], "missing": []}
    for p in pred_rows:
        if not isinstance(p, dict) or p.get("predict") or "test" not in p:
            continue
        if p.get("verdict") != "reuse":
            out["predicted_must_run"] += 1
            continue
        out["predicted_reuse"] += 1
        a = actual.get(p["test"])
        if a is None:
            out["not_in_results"] += 1
            out["missing"].append(p["test"])
            continue
        rc = a.get("rc")
        green = isinstance(rc, int) and not isinstance(rc, bool) and rc == 0 and not a.get("timed_out")
        if green:
            out["would_be_right"] += 1
            out["saved_s"] += float(p.get("prev_duration_s") or 0.0)
        else:
            out["would_have_hidden_red"] += 1
            out["hidden_red"].append({"test": p["test"], "rc": rc, "reason": p.get("reason"),
                                      "reused_host_id": p.get("reused_host_id")})
    out["saved_s"] = round(out["saved_s"], 1)
    return out


def plan_reused(plan_path):
    """證詞旁檔 → [(測試, 上次實跑秒數)],只收 action=reuse 的。壞行略過(旁檔不是證詞本身)。"""
    out = []
    for line in jsonl_safe.split_lines(Path(plan_path).read_text(encoding="utf-8")):
        test, _tab, raw = line.partition("\t")
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("action") == "reuse" and test:
            dur = d.get("prev_duration_s")
            out.append((test, float(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else None))
    return out


def audit_plan(plan_path, repo, pct=PCT_DEFAULT, min_n=MIN_DEFAULT, seed=None, run=None, dry_run=False,
               pool_dir=None):
    reused = plan_reused(plan_path)
    durations = {t: d for t, d in reused if d is not None}
    picked, seed = choose([t for t, _ in reused], durations, (), pct, min_n, seed)
    out = {"plan": str(plan_path), "reused": len(reused), "sampled": len(picked), "seed": seed, "match": 0,
           "real_miss": 0, "unstable": 0, "details": [], "dry_run": bool(dry_run), "cross_host_disabled": False}
    if dry_run:
        out["details"] = [{"test": t, "prev_duration_s": durations.get(t)} for t in picked]
        return out
    run = run or _entry_runner(repo)
    misses = []
    for t in picked:
        rcs = [run(t)]
        if rcs[0] != 0:
            for _ in range(RERUNS_ON_RED):
                rcs.append(run(t))
        verdict = classify(rcs)
        d = {"test": t, "verdict": verdict, "rcs": rcs}
        out["details"].append(d)
        out[verdict.replace("-", "_")] += 1
        if verdict == "real-miss":
            misses.append(d)
    if misses:
        disable_cross_host(misses, pool_dir=pool_dir)
        out["cross_host_disabled"] = True
    return out


def _load_jsonl(path):
    rows = []
    for line in jsonl_safe.split_lines(Path(path).read_text(encoding="utf-8")):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _pool_file(repo):
    import runner
    return runner._pool_file(Path(repo))


def _entry_runner(repo):
    import test_entry

    def run(test):
        res = test_entry.run_tests(Path(repo), [test], fresh=True)
        tests = res.get("tests") or []
        return int(tests[0].get("rc", 1)) if tests else 1
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="false_reuse_audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--repo", default=str(HERE.parents[1]))
    a.add_argument("--tests", default="", help="file with one test path per line (default: every test in the pool)")
    a.add_argument("--pct", type=float, default=PCT_DEFAULT)
    a.add_argument("--min", type=int, default=MIN_DEFAULT)
    a.add_argument("--seed", default=None)
    a.add_argument("--dry-run", action="store_true")
    pr = sub.add_parser("predict")
    pr.add_argument("--repo", default=str(HERE.parents[1]))
    pr.add_argument("--tests", default="")
    pr.add_argument("--out", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--predict", required=True)
    sc.add_argument("--results", required=True)
    ap2 = sub.add_parser("audit-plan")
    ap2.add_argument("--plan", required=True)
    ap2.add_argument("--repo", required=True)
    ap2.add_argument("--pct", type=float, default=PCT_DEFAULT)
    ap2.add_argument("--min", type=int, default=MIN_DEFAULT)
    ap2.add_argument("--seed", default=None)
    ap2.add_argument("--dry-run", action="store_true")
    e = sub.add_parser("enable-cross-host")
    e.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "predict":
        tests = None
        if args.tests:
            tests = [l.strip() for l in Path(args.tests).read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = predict(args.repo, tests)
        Path(args.out).write_text("".join(jsonl_safe.dumps(r) + "\n" for r in rows), encoding="utf-8")
        n = sum(1 for r in rows[1:] if r["verdict"] == "reuse")
        print(json.dumps({"out": args.out, "tests": len(rows) - 1, "predicted_reuse": n,
                          "degraded": rows[0]["degraded"]}, ensure_ascii=False))
        return 0
    if args.cmd == "audit-plan":
        res = audit_plan(args.plan, args.repo, pct=args.pct, min_n=args.min, seed=args.seed, dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False, sort_keys=True))
        return 1 if res["real_miss"] else 0
    if args.cmd == "score":
        res = score(_load_jsonl(args.predict), _load_jsonl(args.results))
        print(json.dumps(res, ensure_ascii=False, sort_keys=True))
        return 1 if res["would_have_hidden_red"] else 0
    if args.cmd == "enable-cross-host":
        print(json.dumps(enable_cross_host(args.reason), ensure_ascii=False))
        return 0
    tests = None
    if args.tests:
        tests = [l.strip() for l in Path(args.tests).read_text(encoding="utf-8").splitlines() if l.strip()]
    res = audit(args.repo, tests=tests, pct=args.pct, min_n=args.min, seed=args.seed, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    if res.get("degraded"):
        return 3
    return 1 if res.get("real_miss") else 0


if __name__ == "__main__":
    sys.exit(main())
