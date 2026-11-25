#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-drive expect wiring knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-drive x aib-expect wiring (DESIGN-drive-expect-wiring, a9136 DECISION-drive-expect-cycle): gaps only
# reorder, unknown goals merge nothing, one report per event, only completion events, an AI order is a
# permutation, and drive keeps the reader's old paths. Each knife keeps the build clean under -D warnings.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
wk() { python3 -I scripts/knife.py "crates/aib-drive/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-drive --locked --test "$5" "$4"; }
# W1 gaps do not weigh
wk decide.rs '                    .contains(&b.number)' \
   '                    .contains(&a.number) // knife' \
   a_gap_puts_its_pr_first_within_a_level_and_only_reorders decide
# W2 goals that cannot be used still let a PR merge
wk observe.rs '        would.retain(|i| !matches!(i, Intent::Merge { .. }));' \
   '        would.retain(|_| true); // knife' \
   goals_that_cannot_be_used_merge_nothing_and_the_report_says_why decide
# W3 the same event is reported again
wk observe.rs '    let event = event.filter(|e| !reported(&paths.journal, e));' \
   '    let event = event; // knife' \
   the_same_event_twice_gives_one_report observe
# W4 any id is a completion event (a report could start a round)
wk report.rs '        _ => false,' \
   '        _ => true, // knife' \
   only_a_completion_event_gets_a_report observe
# W5 notes fill the "next" list
wk report.rs '        .filter(|i| !matches!(i, Intent::Note { .. }))' \
   '        .filter(|_| true) // knife' \
   goals_that_cannot_be_used_merge_nothing_and_the_report_says_why decide
# W6 an AI order that is not a permutation of next is kept
wk report.rs '    if seen != (0..next_len).collect::<Vec<_>>() {' \
   '    if false && seen != (0..next_len).collect::<Vec<_>>() { // knife' \
   an_ai_order_must_be_a_permutation_of_next observe
# W8 (1ced7 wiring L1) the PRs to read are picked with another `now` than the comparison
wk observe.rs '        Ok(g) => ex::read_heads_known(gh, repo, &ex::prs_of(g, &evidence, now), &known),' \
   '        Ok(g) => ex::read_heads_known(gh, repo, &ex::prs_of(g, &evidence, now.saturating_sub(1)), &known), // knife' \
   one_now_for_the_prs_to_read_and_the_comparison observe
# W9 (043dd M1) a round without a snapshot drops its event
wk observe.rs '                events.push_front(e);' \
   '                drop(e); // knife' \
   an_event_met_by_a_failed_round_is_answered_by_the_next_good_one observe
# W10 (043dd M1) an event written while paused is dropped
wk observe.rs '                queue_event(&mut events, read_event(&paths.event));' \
   '                let _ = read_event(&paths.event); // knife' \
   an_event_written_while_paused_is_answered_after_the_pause observe
# W11 (043dd L2) --watch reports the same event again
wk observe.rs '                Some(e) if reported(&paths.journal, &e) => continue,' \
   '                Some(e) if false && reported(&paths.journal, &e) => continue, // knife' \
   watch_reports_the_same_event_once observe
# W12 (043dd L1) aib-expect rows carry an order of their own
python3 -I scripts/knife.py crates/aib-expect/src/lib.rs '        "goal_id": g.goal_id,' \
   '        "goal_id": g.goal_id, "order": 0, // knife' \
   --expect-fail expect_rows_reach_drive_without_an_order_of_their_own \
   -- cargo test -p aib-drive --locked --test observe expect_rows_reach_drive_without_an_order_of_their_own
# W13 (1ced7 N1) the report drops the unreadable-evidence count
python3 -I scripts/knife.py crates/aib-drive/src/observe.rs '            side.unreadable_evidence,' \
   '            None, // knife' \
   --expect-fail the_report_says_how_many_evidence_lines_were_unreadable \
   -- cargo test -p aib-drive --locked --test observe the_report_says_how_many_evidence_lines_were_unreadable
# W14 (1ced7 N1) expect_side does not pass the count on
python3 -I scripts/knife.py crates/aib-drive/src/observe.rs '        unreadable_evidence: evidence_read.then_some(r.unreadable_evidence),' \
   '        unreadable_evidence: evidence_read.then_some(0), // knife' \
   --expect-fail the_report_says_how_many_evidence_lines_were_unreadable \
   -- cargo test -p aib-drive --locked --test observe the_report_says_how_many_evidence_lines_were_unreadable
# W16 (1ced7 L1) an evidence file that cannot be read is counted as 0, not unknown
python3 -I scripts/knife.py crates/aib-drive/src/observe.rs '    let evidence_read = evidence.is_ok();' \
   '    let evidence_read = true; // knife' \
   --expect-fail the_report_says_how_many_evidence_lines_were_unreadable \
   -- cargo test -p aib-drive --locked --test observe the_report_says_how_many_evidence_lines_were_unreadable
# W15 (a9136 N1) without source.rs's pub use, run_fixed's old path is gone: the callers fail on a private import (E0603).
# A private use keeps source.rs's own run_fixed / CALL_TIMEOUT calls building, so the error is only the callers'.
f=crates/aib-drive/src/source.rs
cp "$f" "$RUNNER_TEMP/source.rs.orig"
python3 -I - "$f" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = "pub use aib_ghread::{run_fixed, CALL_TIMEOUT};"
assert s.count(old) == 1, "W15 anchor"
open(p, "w", encoding="utf-8").write(s.replace(old, "use aib_ghread::{run_fixed, CALL_TIMEOUT}; // knife"))
PY
set +e
cargo test -p aib-drive --locked --test snapshot --no-run > "$RUNNER_TEMP/w15.log" 2>&1
rc=$?
set -e
cp "$RUNNER_TEMP/source.rs.orig" "$f"
if [ "$rc" -ne 0 ] && grep -q 'E0603' "$RUNNER_TEMP/w15.log"; then
  echo "KNIFE source_pub_use_keeps_old_paths: BITES rc=$rc"
else
  echo "KNIFE source_pub_use_keeps_old_paths: MISSES rc=$rc"; tail -20 "$RUNNER_TEMP/w15.log"; exit 1
fi
# W7 (a9136 item 5) without the pub use, drive's own tests no longer build: the old path is real.
# Keeps the private Read import snapshot.rs uses, so the error is the callers' unresolved GhRead (E0432).
f=crates/aib-drive/src/snapshot.rs
cp "$f" "$RUNNER_TEMP/snapshot.rs.orig"
python3 -I - "$f" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = "pub use aib_ghread::{GhRead, Read};"
assert s.count(old) == 1, "W7 anchor"
open(p, "w", encoding="utf-8").write(s.replace(old, "use aib_ghread::Read; // knife"))
PY
set +e
cargo test -p aib-drive --locked --test snapshot --no-run > "$RUNNER_TEMP/w7.log" 2>&1
rc=$?
set -e
cp "$RUNNER_TEMP/snapshot.rs.orig" "$f"
if [ "$rc" -ne 0 ] && grep -q 'E0432' "$RUNNER_TEMP/w7.log"; then
  echo "KNIFE pub_use_keeps_old_paths: BITES rc=$rc"
else
  echo "KNIFE pub_use_keeps_old_paths: MISSES rc=$rc"; tail -20 "$RUNNER_TEMP/w7.log"; exit 1
fi
