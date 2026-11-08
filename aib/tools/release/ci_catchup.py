# SPDX-License-Identifier: Apache-2.0
"""ci 補跑(11-08 DECISION-17 修訂第二步):main 最新頭碼那輪 ci 被排隊頂掉(cancelled)就重跑它。

由 `.github/workflows/ci-catchup.yml` 在每輪 main 的 ci 跑完時呼叫(workflow_run completed)。

    python3 aib/tools/release/ci_catchup.py --repo OWNER/NAME [--branch main] [--max-reruns 2]

規則(bb7dc 的條件):
- 每次都**重讀**分支現在的頭碼,不信觸發它的那輪是哪個 SHA;
- 只看「頭碼那個 SHA、push 事件、ci 這個 workflow」的那輪,只重跑它;
- 只在那輪 conclusion == cancelled 時重跑(failure 是真紅,不重跑;還在排 / 在跑就不動);
- 每個 SHA 最多重跑 --max-reruns 次(看 run_attempt:第 1 次跑是 attempt 1,所以 attempt > max 就不再重跑);
- 防重入:workflow 本身 concurrency 一組、不砍跑中;腳本在重跑前再讀一次狀態(讀到但變了 ⇒ 不動;讀不到 ⇒ 告警);
- 讀不到(任何一次)/ 重跑失敗 / 次數用完 ⇒ 印 ::error:: 並 rc 1(告警,不靜默),摘要標 macos_unverified。
- 認 workflow 用檔案路徑(.github/workflows/ci.yml),不用顯示名稱(改名不會認錯)。
- 預期行為:有人手動取消頭碼那輪,也一樣會被重跑(最多 2 次)—— 這裡分不出是排隊頂掉還是人按的;
  真要停,就在重跑後再取消,次數用完會告警一次。

只用標準函式庫 + gh CLI。gh 呼叫都走 `runner`,測試換成假的。
"""
import argparse
import json
import subprocess
import sys

RC_OK, RC_ALERT, RC_USAGE = 0, 1, 2


def gh(args, runner=None):
    """(rc, stdout)。runner(argv) -> (rc, stdout) 給測試換掉。"""
    if runner is not None:
        return runner(["gh"] + args)
    try:
        p = subprocess.run(["gh"] + args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout


def head_sha(repo, branch, runner=None):
    rc, out = gh(["api", "repos/%s/commits/%s" % (repo, branch), "--jq", ".sha"], runner)
    sha = out.strip() if rc == 0 else ""
    return sha if len(sha) == 40 else ""


def tip_run(repo, sha, workflow_path, runner=None):
    """頭碼那個 SHA 的 push 事件 ci 那一輪(最新建立的一輪);None = 沒有;"" = 讀不到。"""
    rc, out = gh(["api", "repos/%s/actions/runs?head_sha=%s&event=push&per_page=20" % (repo, sha)], runner)
    if rc != 0:
        return ""
    try:
        runs = json.loads(out).get("workflow_runs", [])
    except ValueError:
        return ""
    runs = [r for r in runs if r.get("path") == workflow_path and r.get("head_sha") == sha]
    if not runs:
        return None
    return max(runs, key=lambda r: (r.get("created_at") or "", r.get("id") or 0))


def run_state(repo, run_id, runner=None):
    rc, out = gh(["api", "repos/%s/actions/runs/%s" % (repo, run_id)], runner)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def decide(repo, branch, workflow_path, max_reruns, runner=None, say=print):
    """回 (rc, 動作)。動作:rerun / nothing / alert。"""
    sha = head_sha(repo, branch, runner)
    if not sha:
        say("::error::ci-catchup: cannot read the head of %s; macos_unverified for whatever is newest" % branch)
        return RC_ALERT, "alert"
    run = tip_run(repo, sha, workflow_path, runner)
    workflow = workflow_path
    if run == "":
        say("::error::ci-catchup: cannot list the %s runs for %s; macos_unverified %s" % (workflow, sha, sha))
        return RC_ALERT, "alert"
    if run is None:
        say("ci-catchup: no %s push run for %s yet; nothing to do" % (workflow, sha))
        return RC_OK, "nothing"
    if run.get("status") != "completed":
        say("ci-catchup: the run for %s (%s) is %s; nothing to do" % (sha, run.get("id"), run.get("status")))
        return RC_OK, "nothing"
    if run.get("conclusion") != "cancelled":
        say("ci-catchup: the run for %s (%s) ended %s; nothing to rerun" % (sha, run.get("id"), run.get("conclusion")))
        return RC_OK, "nothing"
    attempt = int(run.get("run_attempt") or 1)
    if attempt > max_reruns:
        say("::error::ci-catchup: the run for %s (%s) was cancelled again after %d reruns; giving up — "
            "macos_unverified %s" % (sha, run["id"], attempt - 1, sha))
        return RC_ALERT, "alert"
    # 防重入:重跑前再讀一次 —— 頭碼沒變、那輪仍是 cancelled、attempt 沒被別人加過
    now = head_sha(repo, branch, runner)
    if not now:
        say("::error::ci-catchup: cannot re-read the head of %s before rerunning %s; not rerunning — "
            "macos_unverified %s" % (branch, run["id"], sha))
        return RC_ALERT, "alert"
    if now != sha:
        say("ci-catchup: %s moved on while checking; the run for the new head decides" % branch)
        return RC_OK, "nothing"
    again = run_state(repo, run["id"], runner)
    if again is None:
        say("::error::ci-catchup: cannot re-read run %s before rerunning it; not rerunning — macos_unverified %s"
            % (run["id"], sha))
        return RC_ALERT, "alert"
    if again.get("status") != "completed" or again.get("conclusion") != "cancelled" \
            or int(again.get("run_attempt") or 1) != attempt:
        say("ci-catchup: the run for %s (%s) changed while checking; leaving it" % (sha, run["id"]))
        return RC_OK, "nothing"
    rc, out = gh(["run", "rerun", str(run["id"]), "--repo", repo], runner)
    if rc != 0:
        say("::error::ci-catchup: rerun of %s for %s failed (rc %s): %s — macos_unverified %s"
            % (run["id"], sha, rc, out.strip()[-200:], sha))
        return RC_ALERT, "alert"
    say("::notice::ci-catchup: reran %s for %s (attempt %d → %d of at most %d)"
        % (run["id"], sha, attempt, attempt + 1, max_reruns + 1))
    return RC_OK, "rerun"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ci_catchup", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--workflow-path", default=".github/workflows/ci.yml")
    ap.add_argument("--max-reruns", type=int, default=2)
    a = ap.parse_args(argv)
    if a.max_reruns < 0 or "/" not in a.repo:
        print("ci_catchup: --repo OWNER/NAME and --max-reruns >= 0", file=sys.stderr)
        return RC_USAGE
    rc, _ = decide(a.repo, a.branch, a.workflow_path, a.max_reruns)
    return rc


if __name__ == "__main__":
    sys.exit(main())
