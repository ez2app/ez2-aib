#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""閒置的機器先把主線新提交影響到的測試跑好(docs/PLAN-shadow-cross-device-1002.md 第 11 步)。

不另做挑測試、跑測試、記紀錄 —— `aib verify --changed`(tools/verify/runner.py)本來就會:
挑受影響的測試、能沿用就沿用、其餘實跑、每支寫進沿用池,而且會拿機器租約(不和人搶)。
本檔只多三件事:
  1. 記住上次暖到主線哪一顆(狀態檔),這次只跑「上次到現在」改到的;
  2. 機器忙(1 分鐘負載超過門檻)就不跑,留到下次;
  3. 可選:跑完把紀錄清乾淨(row_export)、經協作廣場推給別台(pool_share)—— 別台照信任名單決定收不收。
  4. (10-04)沿用紀錄快過期的先重跑(--refresh-hours,預設 24):期限是「沒改動又重跑確認綠就加倍、最多 14 天」,
     在機器閒著時把 24 小時內會過期的先跑掉,白天合版就碰不到期限造成的重跑;重跑又綠 = 確認一次 = 期限加倍。
     只挑「現在判得到沿用」的(紅、量不到、輸入已變的本來就會跑,不必預跑),一次最多 REFRESH_MAX 支。

用法(給定時器叫;例如 Fish 晚上每 30 分鐘一次):
  prewarm.py --repo ~/ez2-aib [--ref origin/main] [--max-load 4] [--share-as @fish] [--dry-run]
  stdout 一行 JSON:{"state": "up-to-date"|"busy"|"ran"|"dry-run"|"error", "from", "to", "rc", ...}
  rc 0 = 沒事或跑完(測試有紅也是 0:紅已記進池,下次合版會看到);2 = 參數/環境錯;3 = 跑的過程壞了
暖用的工作樹在 ~/.ez2-aib/prewarm/<倉識別>/tree(分離 HEAD,只讀用);不碰正本樹、不碰任何分支。
"""
from __future__ import annotations

import argparse
import json
import jsonl_safe  # 10-04:jsonl 行界只認 "\n"(見該檔)
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reuse_index as ri  # noqa: E402

LOAD_DEFAULT = 4.0
REFRESH_H_DEFAULT = 24.0
REFRESH_MAX = 200


def expiring_tests(tree, within_h, environ=None, idx=None):
    """這棵樹上「現在會沿用、但 within_h 小時內過期」的測試,快過期的排前面。"""
    idx = idx or ri.build_index([], repo=str(tree))
    soon = []
    for t in sorted(idx.latest):
        left = idx.remaining_h(str(tree), t, environ=environ)
        if left is not None and left <= within_h:
            soon.append((left, t))
    return [t for _left, t in sorted(soon)][:REFRESH_MAX]


def _refresh_run(tree, tests):
    import runner
    import test_entry
    home = Path(tempfile.mkdtemp(prefix="aib-prewarm-home-", dir=runner.TMP_ROOT))
    try:
        res = test_entry.run_tests(Path(tree), tests, fresh=True)
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
    return res.get("rc", 3)


GIT_TIMEOUT_S = 300


def _git(repo, *args, check=True, timeout=GIT_TIMEOUT_S):
    """10-08:git 自己一個行程群組;逾時連整群一起殺。以前 subprocess.run(timeout=…) 只殺 git 本身,它底下連網的
    git-remote-https 留下來掛著(雲端實測一天累積 450 個),又占住代理對同一個倉的同時連線數 ⇒ 下一次 fetch 更容易卡、更容易逾時。"""
    import signal
    p = subprocess.Popen(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        p.communicate()
        raise
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), err.strip()[-200:]))
    return out.strip()


def _is_ancestor(repo, a, b):
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b], capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=60).returncode == 0


def state_dir(repo, home=None):
    root = Path(home or os.environ.get("HOME") or str(Path.home()))
    return root / ".ez2-aib" / "prewarm" / (ri.repo_identity(str(repo)) or "unknown")


def load_state(d):
    try:
        return json.loads((Path(d) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(d, obj):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(d / "state.json"))


def _tree(repo, d, sha):
    t = Path(d) / "tree"
    if (t / ".git").exists():
        _git(t, "checkout", "-q", "--detach", sha)
    else:
        _git(repo, "worktree", "add", "-q", "--detach", str(t), sha)
    return t


def prewarm(repo, ref="origin/main", max_load=LOAD_DEFAULT, share_as=None, dry_run=False, run=None, push=None,
            loadavg=None, fetch=True, refresh_h=0.0, expiring=None, refresh_run=None):
    """run(tree, base) → rc(預設 runner.run 的 rc);push(me, rows_path, tree) → 摘要(預設 row_export + pool_share)。"""
    repo = Path(repo).resolve()
    d = state_dir(repo)
    st = load_state(d)
    if fetch:
        remote, _sep, branch = ref.partition("/")
        _git(repo, "fetch", "-q", remote, branch, check=False)
    target = _git(repo, "rev-parse", ref)
    last = st.get("last")
    out = {"from": last, "to": target}
    load = (loadavg or os.getloadavg)()[0]
    if last == target:
        out = dict(out, state="up-to-date", rc=0)
        if refresh_h > 0 and load <= max_load and not dry_run:
            out["refresh"] = _refresh(repo, d, target, refresh_h, expiring, refresh_run)
        return out
    if load > max_load:
        return dict(out, state="busy", load=round(load, 2), rc=0)
    # 上次那顆還在主線歷史上 ⇒ 從它算起;第一次、或主線被改寫過(上次那顆不是祖先)⇒ 只暖最新這一顆的改動
    if last and _is_ancestor(repo, last, target):
        base = last
    else:
        base = target + "~1" if _git(repo, "rev-parse", "-q", "--verify", target + "~1", check=False) else target
    out["base"] = base
    if dry_run:
        return dict(out, state="dry-run", rc=0)
    tree = _tree(repo, d, target)
    if run is None:
        import runner

        def run(t, b):
            rc, _rec, _path = runner.run(str(t), base=b)
            return rc
    started = time.time()
    rc = run(tree, base)
    out.update(state="ran", rc=rc)
    if rc not in (0, 1):                 # 0 全綠、1 有紅(紅照樣記進池);其他 = 跑的過程壞了,下次重來
        out["state"] = "error"
        return out
    state = {"last": target, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "rc": rc}
    save_state(d, state)
    if share_as:
        # 10-03 審查第 13 條:推送失敗(廣場忙、沒權限)不讓整支當掉;記進狀態檔,下次看得到
        try:
            out["share"] = (push or _push_rows)(share_as, None, tree, started)
        except Exception as e:  # noqa: BLE001
            out["share"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
            save_state(d, dict(state, share_error=out["share"]["error"]))
    if refresh_h > 0:
        out["refresh"] = _refresh(repo, d, target, refresh_h, expiring, refresh_run)
    out["rc"] = 0
    return out


def _refresh(repo, d, target, refresh_h, expiring=None, refresh_run=None):
    """快過期的先重跑。任何失敗只記下來、不讓整支當掉(同第 13 條)。"""
    try:
        tree = _tree(repo, d, target)
        tests = (expiring or expiring_tests)(tree, refresh_h)
        if not tests:
            return {"tests": 0}
        rc = (refresh_run or _refresh_run)(tree, tests)
        return {"tests": len(tests), "rc": rc}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _own_rows(lines, tree, since):
    """10-03 審查第 10 條:池檔是所有工作樹共用的。只推「這棵暖用樹、這次實跑」的列:
    repo 欄等於 tree,而且時間不早於這次開跑(time_utc;沒有 UTC 的列不推)。別棵樹的列不出這台機器。"""
    tree = str(Path(tree).resolve())
    since_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)) if since else ""
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("repo") != tree:
            continue
        if since_utc and not (isinstance(r.get("time_utc"), str) and r["time_utc"] >= since_utc):
            continue
        out.append(line)
    return out


def _pool_voids(lines):
    """池裡的作廢列(正規化後的,reuse_index._parse_void)。"""
    out = []
    for line in lines:
        try:
            v = ri._parse_void(json.loads(line))
        except ValueError:
            continue
        if v:
            out.append(v)
    return out


def _drop_voided(lines, voids):
    """10-15 A3:被作廢的綠不准推出去(以前照推 ⇒ 別台拉下來照樣沿用)。"""
    if not voids:
        return list(lines)
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not any(ri._void_hits(v, r) for v in voids):
            out.append(line)
    return out


def _export_voids(voids, me, since):
    """10-15 A3:本機的作廢列也推出去,讓別台把「從本機收過去的」那些綠一起作廢。
    只推:有 UTC 時間的(跨機器只能比 UTC)、指名本機或沒指名機器的;推出去一律縮成「只打本機的列」
    (host_id = 本機;all 拿掉 —— all 會蓋過 host_id)。別台收的時候也只認「作廢簽署者自己的列」(pool_share.pull)。
    since 往前 MAX_TTL_H:更早的作廢打不到任何還在期限內的列。"""
    floor = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((since or time.time()) - ri.MAX_TTL_H * 3600))
    out = []
    for v in voids:
        c = dict(v["cond"])
        if not v.get("before_utc") or v["before_utc"] < floor or c.get("host_id") not in (None, "", me):
            continue
        c.pop("all", None)
        c["host_id"] = me
        out.append({"void": c, "before": v["before"], "before_utc": v["before_utc"], "reason": "", "exported": True})
    return out


def _push_rows(me, _rows_path, tree, since=None):
    """本機池 → 只留這棵樹這次實跑的、沒被作廢的 → row_export 清過 → 連同本機的作廢列 → pool_share 推上廣場。
    清不出東西就不推。"""
    import runner
    import row_export
    import pool_share
    pool = runner._pool_file(Path(tree))
    if not pool.is_file():
        return {"pushed_rows": 0, "why": "no-pool"}
    lines = jsonl_safe.split_lines(pool.read_text(encoding="utf-8"))
    voids = _pool_voids(lines)
    rows, summary = row_export.export_lines(_drop_voided(_own_rows(lines, tree, since), voids), str(tree),
                                            os.environ.get("HOME") or str(Path.home()))
    vrows = _export_voids(voids, ri.host_id(), since)
    if not rows and not vrows:
        return {"pushed_rows": 0, "export": summary}
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for r in rows + vrows:
            f.write(jsonl_safe.dumps(r) + "\n")
        path = f.name
    try:
        res = pool_share.push(me, path, repo=str(tree))
    finally:
        os.unlink(path)
    return dict(res, export=summary)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prewarm")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--max-load", type=float, default=LOAD_DEFAULT)
    ap.add_argument("--share-as", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-hours", type=float, default=REFRESH_H_DEFAULT,
                    help="rerun reusable rows expiring within this many hours; 0 = off")
    a = ap.parse_args(argv)
    try:
        res = prewarm(a.repo, a.ref, a.max_load, a.share_as, a.dry_run, refresh_h=a.refresh_hours)
    except Exception as e:  # noqa: BLE001  10-03 審查第 13 條:任何失敗都印一行 JSON、rc 2,不印 traceback
        print(json.dumps({"state": "error", "error": "%s: %s" % (type(e).__name__, e)}))
        return 2
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return 3 if res.get("state") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
