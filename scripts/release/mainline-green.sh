#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# R1 (519d0 DEV-shadow-2x §4.4 item 4, Andy 11-14): a release goes out only for a commit main has
# already run in full. aibd-release.yml calls this before the draft; it never builds or tests.
#
#   mainline-green.sh <owner/repo> <sha40>
#
# Pass (rc 0) only when all of these hold, read from the GitHub API (the final word):
#   1. <sha> is on main (compare main...<sha> is identical or behind);
#   2. aibd.yml has a completed run for <sha> with event=push, head_branch=main,
#      head_repository=<owner/repo>; with several, the last one to finish counts;
#   3. that run concluded success, and in it each required job (rust (windows-latest),
#      rust (macos-latest), cloud decision knives (macOS), cloud-host command knives (macOS), and
#      any other "… knives (macOS)" job) exists once and succeeded;
#   4. it was not skipped or reused: in each rust job the steps fmt, clippy and the step whose
#      name starts with "test" succeeded, and "no Rust path changed, nothing else to run" did not run.
#   Job and step names are read as aibd.yml spells them: rename one there, rename it here.
# Anything missing or unreadable refuses (fail closed): rc 1 = refused, rc 2 = could not tell
# (also while main's push run is still going: rerun the release workflow once it has finished).
#
# The S68 reuse ledger is only a candidate shown next to the verdict (Andy 11-14): with
# MG_LEDGER_IN=<jsonl> rows {"head_sha","result":"pass|fail"} for <sha> are compared with the API;
# no MG_LEDGER_IN = ledger_not_configured, no row = ledger_missing, a different result = mismatch
# (warned, the API still decides).
# MG_LEDGER_OUT=<file> appends one ez2-reuse-ledger.v1 row of kind release_check.
# MG_API_DIR=<dir> reads compare.json / runs.json / jobs-<run id>.json there instead of calling
# gh api (tests). Needs bash, jq and (without MG_API_DIR) gh.
set -u

repo=${1:-}
sha=${2:-}
case "$repo" in */*) ;; *) echo "usage: mainline-green.sh <owner/repo> <sha40>" >&2; exit 2 ;; esac
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "usage: mainline-green.sh <owner/repo> <sha40> (40 lower-case hex)" >&2; exit 2; }

api() { # api <key> <path>: the JSON body, or rc != 0
  if [ -n "${MG_API_DIR:-}" ]; then cat "$MG_API_DIR/$1.json" 2>/dev/null; return; fi
  gh api -H "Accept: application/vnd.github+json" "$2"
}

say() { echo "mainline-green: $*"; }
run_id=""; run_attempt=""; run_url=""; reason=""

decide() {
  local c status runs run jobs
  c=$(api compare "repos/$repo/compare/main...$sha") || { reason="compare_unreadable"; return 2; }
  status=$(jq -r '.status // ""' <<<"$c") || { reason="compare_unreadable"; return 2; }
  case "$status" in
    identical | behind) ;;
    "") reason="compare_unreadable"; return 2 ;;
    *) reason="not_on_main:$status"; return 1 ;;
  esac

  runs=$(api runs "repos/$repo/actions/workflows/aibd.yml/runs?head_sha=$sha&event=push&branch=main&per_page=100") \
    || { reason="runs_unreadable"; return 2; }
  # main's own push runs of <sha>, written once: both questions below read the same list
  local mine='[.workflow_runs // [] | .[]
       | select(.head_sha == $s and .event == "push" and .head_branch == "main"
                and .head_repository.full_name == $r)]'
  run=$(jq -c --arg s "$sha" --arg r "$repo" "$mine"'
      | map(select(.status == "completed")) | sort_by(.updated_at) | last // empty' <<<"$runs") \
    || { reason="runs_unreadable"; return 2; }
  if [ -z "$run" ]; then
    # a matching run that has not finished: not a refusal of the commit, just too early (ebb36 L1)
    local busy
    busy=$(jq --arg s "$sha" --arg r "$repo" "$mine"' | map(select(.status != "completed")) | length' <<<"$runs") \
      || { reason="runs_unreadable"; return 2; }
    if [ "$busy" != 0 ]; then reason="main_push_run_in_progress"; return 2; fi
    reason="no_main_push_run"; return 1
  fi
  run_id=$(jq -r '.id' <<<"$run")
  run_attempt=$(jq -r '.run_attempt // 1' <<<"$run")
  run_url=$(jq -r '.html_url // ""' <<<"$run")
  [ "$(jq -r '.conclusion // ""' <<<"$run")" = success ] || { reason="run_not_green:$(jq -r '.conclusion // "none"' <<<"$run")"; return 1; }

  jobs=$(api "jobs-$run_id" "repos/$repo/actions/runs/$run_id/jobs?filter=latest&per_page=100") \
    || { reason="jobs_unreadable"; return 2; }
  local bad
  bad=$(jq -r '
      . as $root
      | def need($n): [$root.jobs // [] | .[] | select(.name == $n)]
        | if length != 1 then "\($n):count=\(length)"
          elif .[0].conclusion != "success" then "\($n):\(.[0].conclusion // "none")"
          else empty end;
      # a cell that took the "nothing to run" branch is green but ran nothing
      def ran($j): [$j.steps // [] | .[]
          | select(.name == "no Rust path changed, nothing else to run" and .conclusion != "skipped")]
        | if length == 0 then empty else "\($j.name):not_run" end;
      # the rust cells: fmt, clippy and a test step really ran (the other OS test step is skipped)
      def tested($j): [$j.steps // [] | .[]] as $s
        | (["fmt", "clippy"][] as $n
            | [$s[] | select(.name == $n)]
            | if length == 1 and .[0].conclusion == "success" then empty else "\($j.name):\($n)" end),
          ([$s[] | select(.name | startswith("test"))]
            | if any(.conclusion == "success") and all(.conclusion == "success" or .conclusion == "skipped")
              then empty else "\($j.name):test" end);
      def rust: .name == "rust (windows-latest)" or .name == "rust (macos-latest)";
      def knives: .name | endswith("knives (macOS)");
      need("rust (windows-latest)"),
      need("rust (macos-latest)"),
      # the knife cells by name: renamed, split or removed ⇒ no release until this file follows (ebb36 M1)
      need("cloud decision knives (macOS)"),
      need("cloud-host command knives (macOS)"),
      ([.jobs // [] | .[] | select(knives) | .name] | unique[] as $k | need($k)),
      (.jobs // [] | .[] | select(rust or knives) | ran(.)),
      (.jobs // [] | .[] | select(rust) | tested(.))
    ' <<<"$jobs") || { reason="jobs_unreadable"; return 2; }
  [ -z "$bad" ] || { reason="cells:$(tr '\n' ' ' <<<"$bad" | sed 's/ $//')"; return 1; }
  reason="ok"
  return 0
}

decide; rc=$?
result=fail; [ $rc = 0 ] && result=pass

# the ledger is a candidate only: compare, warn, never decide. No ledger given = not configured
# (S68 not wired yet; a reader does not count it as missing, ebb36 L2)
verdict=ledger_not_configured
if [ -n "${MG_LEDGER_IN:-}" ] && [ -f "$MG_LEDGER_IN" ]; then
  verdict=ledger_missing
  lr=$(jq -r --arg s "$sha" 'select(.head_sha == $s) | .result // empty' "$MG_LEDGER_IN" 2>/dev/null | tail -1)
  if [ -n "$lr" ]; then
    if [ "$lr" = "$result" ]; then verdict=match; else verdict=mismatch; fi
  fi
fi
[ "$verdict" = mismatch ] && echo "::warning::mainline-green: the reuse ledger says ${lr} for $sha, the GitHub API says $result (API decides)"

if [ -n "${MG_LEDGER_OUT:-}" ]; then
  jq -cn --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg repo "$repo" --arg sha "$sha" \
    --arg run "$run_id" --arg att "$run_attempt" --arg v "$verdict" --arg r "$result" \
    --arg why "$reason" --arg src "$run_url" \
    '{schema: "ez2-reuse-ledger.v1", kind: "release_check", at: $at, repo: $repo, head_sha: $sha,
      run_id: $run, run_attempt: $att, workflow: "aibd-release", producer: "scripts/release/mainline-green.sh",
      mode: "real", verdict: $v, result: $r, reason: $why, source_ref: $src}' >> "$MG_LEDGER_OUT" \
    || { say "could not write the ledger row to $MG_LEDGER_OUT"; [ $rc = 0 ] && rc=2; }
fi

say "$result ($reason) sha=$sha run=${run_id:-none} ledger=$verdict"
exit $rc
