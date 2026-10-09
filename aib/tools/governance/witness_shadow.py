#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""把 V1/V2 影子計畫接進合入證詞。預設不呼叫;AIB_WITNESS_SHADOW=1 才走這裡。

would-skip 才沿用上一輪 rc/尾行。unmeasured / external / 指紋縮短一律實跑。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_VERIFY = Path(__file__).resolve().parents[1] / "verify"
if str(_VERIFY) not in sys.path:
    sys.path.insert(0, str(_VERIFY))
import planner as plan  # noqa: E402
import shadow_tracer as st  # noqa: E402

# 刀:拿掉「unmeasured 不沿用」那一行,(b) 必紅
UNMEASURED_NEVER_REUSE = True

# 10-02 雲端 claudebot(docs/PLAN-shadow-cross-device-1002.md 第 2c 步;發版預檢設計 §16.3):
# 合入見證(B8)與發版全套是兩種跑法(逾時、家目錄、母體都不同),而兩支腳本都讀同一個 AIB_WITNESS_SHADOW_PREV ⇒
# 一邊的列可能被另一邊沿用。列帶 scope;判斷時 scope 不同 ⇒ 重跑(prev-other-scope)。
# 舊列沒有 scope:發版列一定帶 execution_mode(reuse_release_row 與 aib-release.sh 的列形),見證列沒有 ⇒ 依此推。
SCOPES = ("witness", "release")


def row_scope(row):
    s = row.get("scope")
    if s:
        return str(s)
    return "release" if "execution_mode" in row else "witness"


def load_prev_map(path):
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    out = {}
    for row in plan.load_rows(p):
        test = row.get("test")
        if test:
            out[test] = row
    return out


def decide(test, prev, repo, current=None, environ=None, scope=None):
    """回 ("reuse"|"run", prev_or_none)。"""
    action, row, _why = decide_why(test, prev, repo, current=current, environ=environ, scope=scope)
    return action, row


def decide_why(test, prev, repo, current=None, environ=None, scope=None):
    """Same verdict as decide(), plus a short reason (release preflight Phase B: every row says why it ran
    or was reused). Reasons: no-prev-row, prev-unmeasured, prev-red-or-timeout, prev-external, inputs-changed,
    reused."""
    if not prev:
        return "run", None, "no-prev-row"
    if scope and row_scope(prev) != scope:
        return "run", None, "prev-other-scope"
    if UNMEASURED_NEVER_REUSE and prev.get("measured") is not True:
        return "run", None, "prev-unmeasured"
    _rc = prev.get("rc")
    if not isinstance(_rc, int) or isinstance(_rc, bool) or _rc != 0 or prev.get("timed_out"):
        return "run", None, "prev-red-or-timeout"          # ef56b F:紅列/缺 rc/逾時列永不沿用
    if prev.get("external"):
        return "run", None, "prev-external"
    if current is not None:
        verdict = plan.plan_one(current, prev)
        if verdict != "would-skip":
            return "run", None, "inputs-changed"
        return "reuse", prev, "reused"
    reused = st.reuse_row(Path(repo), test, prev, environ=environ)
    if reused is not None:
        stub = {
            "test": test,
            "measured": True,
            "external": False,
            "key": reused.get("key") or prev.get("key"),
            "inputs": prev.get("inputs") or [],
            "rc": int(prev.get("rc") or 0),
        }
        if plan.plan_one(stub, prev) != "would-skip":
            return "run", None, "inputs-changed"
        return "reuse", prev, "reused"
    if not UNMEASURED_NEVER_REUSE:
        return "reuse", prev, "reused"
    return "run", None, "inputs-changed"


# 沿用列要**帶著走**的量測欄:它們是「下一輪還能不能沿用」的前置條件,不是裝飾。
#   decide() 第一關看 prev["measured"] is True;shadow_tracer.reuse_row 要 "input_fp" in previous。
# ★ 09-13(第 96 項)艦長在 live40 的證詞列上量到:288 列有 24 列是 reused 而**沒有 measured**,
#   回頭追來源那一份,同一支測試是 measured=True、20 欄;抄過來只剩 13 欄。
#   根因就是本函式**手寫欄位清單重建**一列而不是帶著上一列的量測結論走。
#   後果是一個自我抵銷的迴圈:沿用一次 ⇒ 失去量測 ⇒ 下一輪必須真跑 ⇒
#   **一支測試最多只能被連續沿用一次**,而每一輪都是綠的、收據都合法、沒有任何錯誤訊息。
#   第 95/96 項要提高的「可量測比例」,會被這個迴圈按輪次吃掉。
# ★ 為什麼是「保留」不是「宣稱」:沿用**只能保留**上一列的量測結論。
#   一律寫 measured=True 會讓量不到的列開始被沿用 —— 那比現在更糟。
MEASUREMENT_CARRIED = ("measured", "inputs", "input_fp", "env_fp", "external", "why", "tail")


def reuse_witness_row(test, prev, prev_path, head, tree_sha, batches):
    outcome = prev.get("outcome") or prev.get("tail") or "Ran 0 test | reused"
    failed = prev.get("failed_tests")
    if not isinstance(failed, list):
        failed = []
    row = {
        "test": test,
        "command": "python3 " + test,
        "rc": int(prev.get("rc") or 0),
        "outcome": str(outcome),
        "head_sha": head,
        "tree_sha": tree_sha,
        "failed_tests": failed,
        "receipt_grade": "reused",
        "reused_from": str(prev_path),
        "key": prev.get("key") or "",
        "verdict": "would-skip",
        "reused": True,
    }
    # T1-1:batches 空字串=登記工具不在、沒量到 ⇒ 缺欄(b8 契約:缺欄≠0)。
    if str(batches) != "":
        row["concurrent_running_batches"] = int(batches)
    # 身分欄(head/tree/reused_from/verdict/reused/receipt_grade)一律是**本輪的**,
    # 量測欄一律是**上一列的** —— 兩者不可互相覆蓋。
    for field in MEASUREMENT_CARRIED:
        if field in prev:
            row[field] = prev[field]
    return row


def reuse_release_row(test, prev, prev_path, head, tree_sha, execution_mode):
    """發版全套(aib-release.sh)的沿用列:抄上一輪那一列(release 形狀:test/rc/ran/skipped/outcome/command/
    head_sha/tree_sha/log/execution_mode/real_tmux_gated + 指紋欄),換 head/tree,標 reused。
    與 witness 形狀分開寫,因為 suite_resume.load_rows 認的是 release 的必要欄(test/rc/tree_sha)。"""
    row = dict(prev)
    row.update({
        "test": test,
        "rc": int(prev.get("rc") or 0),
        "head_sha": head,
        "tree_sha": tree_sha,
        "execution_mode": execution_mode or prev.get("execution_mode") or "unmeasured",
        "receipt_grade": "reused",
        "reused_from": str(prev_path),
        "key": prev.get("key") or "",
        "verdict": "would-skip",
        "reused": True,
    })
    return row


def main(argv=None):
    t_start = time.perf_counter()     # Phase B net saving: how long the reuse check itself took (without interpreter start-up)
    ap = argparse.ArgumentParser(prog="witness_shadow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--prev", default="")
    d.add_argument("--test", required=True)
    d.add_argument("--repo", required=True)
    d.add_argument("--current-json", default="")
    # --env-file: the controlled child env dumped by the runner (env > file); keys are computed from it, not from this process
    d.add_argument("--env-file", default="")
    d.add_argument("--scope", default="", choices=("",) + SCOPES)   # 2c:只沿用同一種跑法的列
    # fingerprint: merge the child trace into the witness row (V3 wrap-up: before this nothing wrote it back, so decide always said run)
    f = sub.add_parser("fingerprint")
    f.add_argument("--trace", required=True)
    f.add_argument("--test", required=True)
    f.add_argument("--repo", required=True)
    f.add_argument("--row", required=True)
    f.add_argument("--env-file", default="")
    f.add_argument("--scope", default="", choices=("",) + SCOPES)   # 2c:列記自己是哪一種跑法
    # --out: 把指紋列寫進檔案而不是印到 stdout。指紋含 inputs(測試讀過的每個檔),
    #   一列可到幾百 KB;呼叫端若把它塞進環境變數再叫 python,會撞 ARG_MAX
    #   (2026-09-24 全套實跑:`列 1602: python3: Argument list too long`,那幾支的
    #   指紋整個記不到,下一輪不能沿用)。大字串走檔不走 argv/env,同一條鏈 09-11 就有這條疤。
    f.add_argument("--out", default="")
    # merge-last: 把 --row-file 那一列取代 --results 的最後一個非空列。原本這段是
    #   aib-release.sh 裡的 heredoc python,列由 SR_FP 環境變數帶進來 —— 就是撞 ARG_MAX 的那一行。
    m = sub.add_parser("merge-last")
    m.add_argument("--results", required=True)
    m.add_argument("--row-file", required=True)
    c = sub.add_parser("stamp")
    c.add_argument("--prev", required=True)
    c.add_argument("--test", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--tree", required=True)
    c.add_argument("--batches", default="")   # T1-1:沒傳=沒量到 ⇒ 缺欄,不是 0
    c.add_argument("--shape", default="witness", choices=["witness", "release"])
    c.add_argument("--execution-mode", default="")
    args = ap.parse_args(argv)
    if args.cmd == "fingerprint":
        row = json.loads(args.row)
        events = st.load_trace(Path(args.trace))
        items, input_fp, unmeasured, external, why = st.inputs_from_events(events, repo=args.repo)
        base_env = st.load_env_file(args.env_file) if args.env_file else None
        env = st.child_env(Path(args.repo), Path("/tmp/.ez2-verify-skip-probe"), base_env)
        env_fp, _ = st.env_fingerprint(env, repo=args.repo)
        rel_l = args.test.lower()
        if "tmux" in rel_l or "vault" in rel_l:
            external = True
        row.update({
            "key": st.make_key(args.test, input_fp, env_fp), "inputs": items, "input_fp": input_fp,
            "env_fp": env_fp, "measured": True if not unmeasured else "unmeasured",
            "external": bool(external), "why": why, "tail": row.get("outcome") or "",
        })
        if args.scope:
            row["scope"] = args.scope
        out = json.dumps(row, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(out + "\n", encoding="utf-8")
        else:
            print(out)
        return 0
    if args.cmd == "merge-last":
        row = Path(args.row_file).read_text(encoding="utf-8").strip()
        if not row:
            print("merge-last: row-file 是空的,不改結果表", file=sys.stderr)
            return 2
        p = Path(args.results)
        lines = p.read_text(encoding="utf-8").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            print("merge-last: 結果表沒有任何列", file=sys.stderr)
            return 2
        lines[-1] = row
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0
    if args.cmd == "decide":
        prev_map = load_prev_map(args.prev)
        prev = prev_map.get(args.test)
        current = None
        if args.current_json:
            current = json.loads(args.current_json)
        base_env = st.load_env_file(args.env_file) if args.env_file else None
        action, _, why = decide_why(args.test, prev, args.repo, current=current, environ=base_env,
                                    scope=args.scope or None)
        out = {"action": action, "why": why, "decide_s": round(time.perf_counter() - t_start, 3)}
        if prev and isinstance(prev.get("duration_s"), (int, float)) and not isinstance(prev.get("duration_s"), bool):
            out["prev_duration_s"] = prev["duration_s"]   # what a reuse saves (the previous real run)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    prev_map = load_prev_map(args.prev)
    prev = prev_map.get(args.test)
    if not prev:
        return 2
    if args.shape == "release":
        row = reuse_release_row(args.test, prev, args.prev, args.head, args.tree, args.execution_mode)
    else:
        row = reuse_witness_row(
            args.test, prev, args.prev, args.head, args.tree, args.batches)
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
