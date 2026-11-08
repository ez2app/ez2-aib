#!/usr/bin/env bash
# Knives for the Windows Job Object (aib-sys `job`, DECISION-8 item 2):
# break one guarantee at a time and require the named test to fail.
# Windows only (the tests need the real kernel). Restores job.rs with git
# after every knife, and on exit.
#
#   scripts/check-win-job-knives.sh
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
f="crates/aib-sys/src/job.rs"
cd "$root" || exit 2
restore() { git checkout -q -- "$f"; touch "$f"; }
trap restore EXIT
bit=0 nf=0 und=0 sv=0 fail=0
knife() {  # name, test filter, python expression old, new
  local name="$1" filter="$2" old="$3" new="$4" out rc
  if ! python3 - "$f" "$old" "$new" <<'PY'
import sys
p, old, new = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
n = s.count(old)
if n != 1:
    sys.exit(f"knife text found {n} times, wanted 1: {old!r}")
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
  then echo "  NOT-FALLEN $name (knife text not found)"; nf=$((nf+1)); restore; return; fi
  # output to a file, not $(...): on Windows a leftover child can inherit
  # the pipe and hold it open (CI run 42436895264 hung 39 min here);
  # timeout is the last guard, and a timeout never counts as red
  local log; log=$(mktemp)
  timeout 900 cargo test -q -p aib-sys --locked -- "$filter" >"$log" 2>&1; rc=$?
  out=$(cat "$log"); rm -f "$log"
  # three outcomes besides "bit" (a63ab 11-08): a knife that did not build
  # never fell; a timeout is undecided. Neither counts as bitten.
  if [ $rc -eq 124 ]; then
    echo "  UNDECIDED  $name (timeout 900 s)"; und=$((und+1))
  elif grep -qE "could not compile|LNK[0-9]+|error: linking" <<<"$out"; then
    echo "  NOT-FALLEN $name (build failed): $(grep -m1 -E 'LNK[0-9]+|^error' <<<"$out")"; nf=$((nf+1))
  elif [ $rc -ne 0 ] && grep -qF -- "$filter" <<<"$out" && grep -q "FAILED\|panicked" <<<"$out"; then
    echo "  BIT        $name (rc $rc, $filter red)"; bit=$((bit+1))
  else
    echo "  SURVIVED   $name (rc $rc): $(tail -5 <<<"$out")"; sv=$((sv+1))
  fi
  restore
}
knife no-suspended a_grandchild_whose_parent_exited \
  'pub const SPAWN_FLAGS: u32 = CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP;' \
  'pub const SPAWN_FLAGS: u32 = CREATE_NEW_PROCESS_GROUP; const _: u32 = CREATE_SUSPENDED;'
knife no-terminate a_grandchild_whose_parent_exited \
  'unsafe { TerminateJobObject(jh, exit_code) }' \
  '{ let _ = (jh, exit_code); let _ = TerminateJobObject; 1 }'
knife breakaway-ok a_member_cannot_break_away \
  'pub const LIMIT_FLAGS: u32 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;' \
  'pub const LIMIT_FLAGS: u32 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;'
knife no-kill-on-close the_owner_crashing_kills_the_tree \
  'pub const LIMIT_FLAGS: u32 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;' \
  'pub const LIMIT_FLAGS: u32 = 0; const _: u32 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;'
knife assign-fail-no-kill a_failed_assign_leaves_no_process \
  '            let _ = child.kill();
            let _ = child.wait();' \
  '            let _ = &mut child; // knife: left suspended / unmanaged'
knife resume-fail-no-kill resume_failure_terminates_the_child \
  '            let _ = child.kill();
            let _ = child.wait();' \
  '            let _ = &mut child; // knife: left suspended / unmanaged'
knife no-clear-last-error a_failure_without_last_error_is_unknown \
  '    unsafe { SetLastError(0) };' \
  '    let _ = SetLastError;'
knife record-after-resume the_member_is_recorded_before_the_child_runs \
  '        .and_then(|()| record_member(pid, record))
        .and_then(|()| resume(pid));' \
  '        .and_then(|()| resume(pid))
        .and_then(|()| record_member(pid, record));'
echo "win job knives: bit $bit / not fallen $nf / undecided $und / survived $sv"
[ $nf -eq 0 ] && [ $und -eq 0 ] && [ $sv -eq 0 ] && [ $bit -gt 0 ]
