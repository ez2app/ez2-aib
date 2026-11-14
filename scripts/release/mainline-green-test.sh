#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Tests for mainline-green.sh, from canned API answers (MG_API_DIR); no network, no gh.
#   mainline-green-test.sh            run every case
#   mainline-green-test.sh <case>     run one case (the knives use this)
# Needs bash and jq. Exit 0 = every case held.
set -u
here=$(cd "$(dirname "$0")" && pwd)
MG="$here/mainline-green.sh"
R=ez2app/ez2-aib
S=1111111111111111111111111111111111111111
work=$(mktemp -d "${TMPDIR:-/tmp}/mg-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
fails=0

# a step list; $1 = test step names and conclusions, as name=conclusion,...
steps() {
  jq -cn --arg extra "$1" '
    [{name: "no Rust path changed, nothing else to run", conclusion: "skipped"},
     {name: "fmt", conclusion: "success"}, {name: "clippy", conclusion: "success"}]
    + ($extra | split(",") | map(select(. != "") | split("=") | {name: .[0], conclusion: .[1]}))'
}

# one green world; each case changes one thing with jq on the files
green() {
  d="$work/$1"; rm -rf "$d"; mkdir -p "$d"
  echo '{"status": "behind"}' > "$d/compare.json"
  jq -cn --arg s "$S" --arg r "$R" '{workflow_runs: [
    {id: 7, run_attempt: 1, head_sha: $s, event: "push", head_branch: "main",
     head_repository: {full_name: $r}, status: "completed", conclusion: "success",
     updated_at: "2026-11-14T01:00:00Z", html_url: "https://example.invalid/runs/7"}]}' > "$d/runs.json"
  local win mac kn
  win=$(steps "test=success,test (macOS; x)=skipped")
  mac=$(steps "test=skipped,test (macOS; x)=success")
  kn='[{"name": "no Rust path changed, nothing else to run", "conclusion": "skipped"}, {"name": "knives", "conclusion": "success"}]'
  jq -cn --argjson w "$win" --argjson m "$mac" --argjson k "$kn" '{jobs: [
    {name: "changes", conclusion: "success", steps: []},
    {name: "rust (windows-latest)", conclusion: "success", steps: $w},
    {name: "rust (macos-latest)", conclusion: "success", steps: $m},
    {name: "cloud decision knives (macOS)", conclusion: "success", steps: $k}]}' > "$d/jobs-7.json"
}
edit() { # edit <case> <file> <jq filter>
  jq -c "$3" "$work/$1/$2" > "$work/$1/$2.new" && mv "$work/$1/$2.new" "$work/$1/$2"
}
expect() { # expect <case> <rc> <reason substring> [env…]
  local c=$1 want=$2 why=$3; shift 3
  local out rc
  out=$(env MG_API_DIR="$work/$c" "$@" bash "$MG" "$R" "$S" 2>&1); rc=$?
  if [ "$rc" = "$want" ] && grep -qF -- "$why" <<<"$out"; then echo "ok   $c"
  else echo "FAIL $c: want rc $want with '$why', got rc $rc: $out"; fails=$((fails + 1)); fi
}

case_green() { green green; expect green 0 "pass (ok)"; }
case_not_on_main() { green not_on_main; edit not_on_main compare.json '.status = "diverged"'; expect not_on_main 1 "not_on_main:diverged"; }
case_ahead_of_main() { green ahead_of_main; edit ahead_of_main compare.json '.status = "ahead"'; expect ahead_of_main 1 "not_on_main:ahead"; }
case_no_run() { green no_run; edit no_run runs.json '.workflow_runs = []'; expect no_run 1 "no_main_push_run"; }
case_fork_run() { green fork_run; edit fork_run runs.json '.workflow_runs[0].head_repository.full_name = "someone/ez2-aib"'; expect fork_run 1 "no_main_push_run"; }
case_pr_run() { green pr_run; edit pr_run runs.json '.workflow_runs[0].event = "pull_request"'; expect pr_run 1 "no_main_push_run"; }
case_other_branch() { green other_branch; edit other_branch runs.json '.workflow_runs[0].head_branch = "integ/x"'; expect other_branch 1 "no_main_push_run"; }
case_other_sha() { green other_sha; edit other_sha runs.json '.workflow_runs[0].head_sha = "2222222222222222222222222222222222222222"'; expect other_sha 1 "no_main_push_run"; }
case_still_running() { green still_running; edit still_running runs.json '.workflow_runs[0].status = "in_progress" | .workflow_runs[0].conclusion = null'; expect still_running 1 "no_main_push_run"; }
case_run_red() { green run_red; edit run_red runs.json '.workflow_runs[0].conclusion = "failure"'; expect run_red 1 "run_not_green:failure"; }
case_last_run_counts() {
  green last_run_counts
  edit last_run_counts runs.json '.workflow_runs += [.workflow_runs[0] | .id = 8 | .conclusion = "failure" | .updated_at = "2026-11-14T02:00:00Z"]'
  expect last_run_counts 1 "run_not_green:failure"
}
case_rerun_green_counts() {
  green rerun_green_counts
  edit rerun_green_counts runs.json '.workflow_runs = [(.workflow_runs[0] | .id = 6 | .conclusion = "failure" | .updated_at = "2026-11-14T00:00:00Z"), .workflow_runs[0]]'
  expect rerun_green_counts 0 "pass (ok)"
}
case_windows_missing() { green windows_missing; edit windows_missing jobs-7.json '.jobs |= map(select(.name != "rust (windows-latest)"))'; expect windows_missing 1 "rust (windows-latest):count=0"; }
case_macos_red() { green macos_red; edit macos_red jobs-7.json '(.jobs[] | select(.name == "rust (macos-latest)") | .conclusion) = "failure"'; expect macos_red 1 "rust (macos-latest):failure"; }
case_knives_red() { green knives_red; edit knives_red jobs-7.json '(.jobs[] | select(.name == "cloud decision knives (macOS)") | .conclusion) = "cancelled"'; expect knives_red 1 "cloud decision knives (macOS):cancelled"; }
case_nothing_ran() {
  green nothing_ran
  edit nothing_ran jobs-7.json '(.jobs[] | select(.name == "rust (windows-latest)") | .steps[] | select(.name == "no Rust path changed, nothing else to run") | .conclusion) = "success"'
  expect nothing_ran 1 "rust (windows-latest):not_run"
}
case_knives_ran_nothing() {
  green knives_ran_nothing
  edit knives_ran_nothing jobs-7.json '(.jobs[] | select(.name == "cloud decision knives (macOS)") | .steps[0].conclusion) = "success"'
  expect knives_ran_nothing 1 "cloud decision knives (macOS):not_run"
}
case_clippy_skipped() { green clippy_skipped; edit clippy_skipped jobs-7.json '(.jobs[] | select(.name == "rust (macos-latest)") | .steps[] | select(.name == "clippy") | .conclusion) = "skipped"'; expect clippy_skipped 1 "rust (macos-latest):clippy"; }
case_tests_skipped() { green tests_skipped; edit tests_skipped jobs-7.json '(.jobs[] | select(.name == "rust (windows-latest)") | .steps[] | select(.name == "test") | .conclusion) = "skipped"'; expect tests_skipped 1 "rust (windows-latest):test"; }
case_unreadable() { green unreadable; rm "$work/unreadable/jobs-7.json"; expect unreadable 2 "jobs_unreadable"; }
case_bad_args() {
  local out rc
  out=$(bash "$MG" "$R" abc 2>&1); rc=$?
  if [ $rc = 2 ]; then echo "ok   bad_args"; else echo "FAIL bad_args: rc $rc $out"; fails=$((fails + 1)); fi
}
case_ledger() {
  green ledger
  local l="$work/ledger/in.jsonl" o="$work/ledger/out.jsonl"
  expect ledger 0 "ledger=ledger_missing" MG_LEDGER_OUT="$o"
  jq -e -s 'length == 1 and .[0].kind == "release_check" and .[0].verdict == "ledger_missing" and .[0].result == "pass"' "$o" >/dev/null \
    || { echo "FAIL ledger: row $(cat "$o")"; fails=$((fails + 1)); }
  echo "{\"head_sha\": \"$S\", \"result\": \"pass\"}" > "$l"
  expect ledger 0 "ledger=match" MG_LEDGER_IN="$l"
  # the ledger never decides: it says fail, the API says pass ⇒ pass, mismatch, warned
  echo "{\"head_sha\": \"$S\", \"result\": \"fail\"}" > "$l"
  expect ledger 0 "ledger=mismatch" MG_LEDGER_IN="$l"
}

all="green not_on_main ahead_of_main no_run fork_run pr_run other_branch other_sha still_running run_red
  last_run_counts rerun_green_counts windows_missing macos_red knives_red nothing_ran knives_ran_nothing
  clippy_skipped tests_skipped unreadable bad_args ledger"
for c in ${1:-$all}; do "case_$c"; done
[ $fails = 0 ] && echo "mainline-green tests: all held" || { echo "mainline-green tests: $fails failed"; exit 1; }
