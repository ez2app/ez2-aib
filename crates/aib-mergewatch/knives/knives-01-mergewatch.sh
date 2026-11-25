#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-mergewatch knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/; IFACE-drive-shadow-519d0-1117 §5; same shape as aib-cireuse and #804's aib-drive).
# The one "crate knives" step (crates/aib-knife/run-knives.sh rust, #836) runs every crates/*/knives/*.sh, this
# one included (defaults: macOS, job rust, 30 minutes); aib-xtask knife-anchors reads the same folder. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-mergewatch slice 1 (f1fae, DEV-mergewatch-rust s15): each knife takes one queue rule away and must
# turn its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
mk() { python3 -I scripts/knife.py "crates/aib-mergewatch/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-mergewatch --locked "$4"; }
# MW1 a review that is not 通過 still goes
mk lib.rs 'if verdict != "通過" {' 'if verdict == "__never__" { // knife' red_checks_conflicts_and_a_bad_review_each_block
# MW2 a hand-resolved merge after the review counts as carried
mk lib.rs 'Some(v) => e.blockers.push(format!(' 'Some(v) => e.notes.push(format!(' \
   a_hand_resolved_or_unknown_merge_after_the_review_blocks
# MW3 no ready-merge label still goes
mk lib.rs 'if !labels.ready {' 'if false { // knife' without_the_label_it_is_held
# MW4 a bare not-gate label demotes a gate
mk lib.rs 'e.gate = new_gate && !demoted;' 'e.gate = new_gate && !demoted && !labels.not_gate;' \
   an_effective_not_gate_is_plain_and_a_bare_label_is_not
# MW5 gates no longer go last
mk lib.rs 'queue.sort_by_key(|e| (e.gate, e.pr));' 'queue.sort_by_key(|e| e.pr);' \
   a_gate_goes_after_the_plain_ones_whatever_its_number
# MW6 a conflict with main is ignored
mk lib.rs 'if b(c, "conflict") == Some(true) {' 'if b(c, "conflict") == Some(false) {' \
   red_checks_conflicts_and_a_bad_review_each_block
# MW7 a receipt off the head still goes
mk lib.rs 'if s(r, "head").unwrap_or("") != head {' 'if s(r, "head").unwrap_or("") == "__never__" {' \
   a_review_or_receipt_off_the_head_blocks
# MW8 an off-head review without resolved_after_review counts as carried
mk lib.rs 'None => e.blockers.push(format!(' 'None => e.notes.push(format!(' \
   a_hand_resolved_or_unknown_merge_after_the_review_blocks
# MW9 an incomplete sweep still queues
mk lib.rs 'if !complete {' 'if false { // knife' an_incomplete_sweep_queues_nothing
# MW10 running checks go as mergeable now
mk lib.rs 'Some("PENDING") | None => e.waiting.push(' 'Some("PENDING") | None => e.notes.push(' \
   pending_checks_are_a_note_not_a_blocker
