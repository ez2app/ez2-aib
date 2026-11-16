#!/usr/bin/env bash
# aib-slice misdispatch knives MD1–MD9 (1ced7 11-17; a9136 / 5d33a reviewed on 4cc5ff4d). Run by one line at the
# end of aibd.yml's inline "aib-slice knives (macOS)" step until that step moves whole to knives-slice.sh
# (ci/slice-knives-out-1ced7-1117, recut after integration 1). New knives live here, not inline (§21).
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gives the step.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
sl() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --lib "$3"; }
ss() { python3 -I scripts/knife.py crates/aib-slice/src/scan.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test sweep "$3"; }
sd() { python3 -I scripts/knife.py crates/aib-slice/src/due.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test due "$3"; }
sdl() { python3 -I scripts/knife.py crates/aib-slice/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test due "$3"; }
# MD1 a compile-only run counts as a receipt again (alone: 「收據不綠(沒跑完)」 ⇒ 修)
ss '        if is_receipt && !is_review && crate::compile_only(&text) {' \
   '        if is_receipt && !is_review && crate::compile_only(&text) && false { // knife' \
   a_compile_only_run_is_no_receipt
# MD2 a draft PR with a red check is a fix
sdl '    if let Some(p) = b.pr.as_ref().filter(|p| p.open && p.draft) {' \
    '    if let Some(p) = b.pr.as_ref().filter(|p| p.open && p.draft && false) { // knife' \
    a_draft_pr_is_not_a_fix
# MD3 an idle draft PR is a fix
sd '    b.pr.as_ref().is_some_and(|p| p.open && !p.draft) && b.age_h > IDLE_H' \
   '    b.pr.as_ref().is_some_and(|p| p.open && (!p.draft || true)) && b.age_h > IDLE_H // knife' \
   a_draft_pr_is_not_a_fix
# MD4 a head over the CI size guard is a fix for its red cells
sdl '    if pr_open && !b.workflow_over_guard.is_empty() {' \
    '    if pr_open && !b.workflow_over_guard.is_empty() && false { // knife' \
    a_pr_over_the_ci_size_guard_waits_instead_of_a_fix
# MD5 exactly 450,000 bytes counts as over the guard (CI is red only past it)
sdl '            (bytes > WORKFLOW_GUARD_BYTES && bytes <= WORKFLOW_MAX_BYTES)' \
    '            (bytes >= WORKFLOW_GUARD_BYTES && bytes <= WORKFLOW_MAX_BYTES) // knife' \
    a_pr_over_the_ci_size_guard_waits_instead_of_a_fix
# MD6 (#804) a knife left behind in the dropped step still counts as moved
sl '        runs && body.iter().all(|l| have.contains(l.as_str()))' \
   '        runs && (body.iter().all(|l| have.contains(l.as_str())) || true) // knife' \
   a_knife_step_moved_into_a_crate_script_is_not_lost
# MD7 (#804) a script no step runs still counts as where the step moved
sl '        let runs = merged_wf.contains(path.as_str()) || merged_wf.contains(&format!("{dir}/*.sh"));' \
   '        let runs = merged_wf.contains(path.as_str()) || merged_wf.contains(&format!("{dir}/*.sh")) || true; // knife' \
   a_knife_step_moved_into_a_crate_script_is_not_lost
# MD8 (#804) a receipt with another red key beside SKIP-REUSED counts as reused only
sdl '            && self.nonzero.iter().all(|k| k == "skip_reused")' \
    '            && self.nonzero.iter().any(|k| k == "skip_reused") // knife' \
    a_receipt_red_only_for_reused_steps_is_run_again_not_fixed
# MD9 (#804) a receipt red only for reused steps is a fix again
sdl '    if receipt_at.is_some_and(Receipt::reused_only) {' \
    '    if receipt_at.is_some_and(Receipt::reused_only) && false { // knife' \
    a_receipt_red_only_for_reused_steps_is_run_again_not_fixed
