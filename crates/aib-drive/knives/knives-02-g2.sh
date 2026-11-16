#!/usr/bin/env bash
# The "aib-drive G2 knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-drive G2 (043dd review of 33e9a0eb, M1): each guard of compare / MatchStreak, broken
# on purpose, must turn its own test red. (MatchStreak's own empty-id check is not knifed:
# compare already refuses an empty id, so removing it changes nothing.)
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gk() { python3 -I scripts/knife.py crates/aib-drive/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test g2 "$3"; }
# compare: an empty id or source passes
gk '    if d.event_source.is_empty() || d.event_id.is_empty() {' \
   '    if false { // knife' \
   an_empty_source_id_or_decision_is_unknown
# compare: an empty decision passes
gk '    if d.decision.is_empty() || a.decision.is_empty() {' \
   '    if false { // knife' \
   an_empty_source_id_or_decision_is_unknown
# compare: two different events compare
gk '    if d.event_source != a.event_source || d.event_id != a.event_id {' \
   '    if false { // knife' \
   another_event_or_an_unknown_side_is_unknown
# compare: the state (version) is not compared (L1)
gk '    if dv.is_empty() || dv != av {' \
   '    if dv.is_empty() && dv != av { // knife' \
   decisions_made_on_different_states_are_unknown
# streak: a record about another event counts
gk '        if d.event_source != event.source || d.event_id != event.id {' \
   '        if false { // knife' \
   an_event_without_identity_or_a_record_about_another_event_resets
# streak: a Different does not reset
gk '        if compare(dry, actual) != Comparison::Match {' \
   '        if compare(dry, actual) == Comparison::Unknown { // knife' \
   a_different_or_unknown_comparison_resets
# streak: matches do not add up
gk '                self.count = self.count.saturating_add(1);' \
   '                self.count = 1; // knife' \
   matches_on_three_events_count_to_three
# streak: a repeated event resets (M2)
gk '            Some(before) if *before == now => self.count,' \
   '            Some(before) if *before == now && false => self.count, // knife' \
   the_same_event_again_with_the_same_decision_is_skipped
# streak: another decision on the same event keeps the count
gk '                self.reset()' \
   '                self.count // knife' \
   the_same_event_with_another_decision_resets
