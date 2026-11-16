#!/usr/bin/env bash
# The S79 review-pick knives (043dd fe0f9e1a, 5d33a 驗收 r7; 519d0 11-16 10:35 review seats first, carried
# heads get no review row). They sat in the "aib-drive next knives (macOS)" step; moved here word for word
# (519d0 11-16 23:5x: aibd.yml is red past 450,000 bytes). aibd.yml runs every crates/aib-drive/knives/*.sh
# in name order in one step; aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
nx() { python3 -I scripts/knife.py crates/aib-drive/src/next.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test next "$3"; }
# RP1 (519d0 11-16 10:35) any seat counts as a review seat
nx '            policy.reviewers.contains(&f.seat)' '            (!f.seat.is_empty() || policy.reviewers.is_empty()) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP2 the review seats are never asked first
nx '            if let Some(r) = pick_listed_reviewer(author, f, policy, &given) {' \
   '            if let Some(r) = pick_listed_reviewer(author, f, policy, &given).filter(|_| false) { // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP3 a busy review seat is sent as free
nx '                if r.dispatchable != "yes" {' '                if false { // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP4 a review seat with 10 unread is asked
nx '                && unread(f).is_some_and(|n| n < crate::act::UNREAD_MAX)' '                && unread(f).is_some_and(|n| n <= crate::act::UNREAD_MAX) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP4b a review seat whose unread is not known is asked
nx '                && unread(f).is_some_and(|n| n < crate::act::UNREAD_MAX)' '                && unread(f).is_none_or(|n| n < crate::act::UNREAD_MAX) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP9 the author reviews its own branch as a review seat
nx '                && author.is_none_or(|a| a != f.seat)' '                && author.is_some_and(|a| !a.is_empty()) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP10 (5d33a L1) a busy review seat before a free one
nx '        busy(a)' '        busy(b) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP11 reviews no longer spread over the free review seats
nx '            .then(had(a).cmp(&had(b)))' '            .then(had(b).cmp(&had(a))) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP5 the fewer unread no longer breaks a tie between review seats
nx '            .then_with(|| unread(a).cmp(&unread(b)))' '            .then_with(|| unread(b).cmp(&unread(a))) // knife' \
   a_listed_review_seat_goes_first_and_the_old_picking_only_when_none_can
# RP6 a carried head gets a review row
nx '        if d.kind == "review" && policy.is_carried(&d.head) {' '        if d.kind == "review" && policy.is_carried(&d.head) && false { // knife' \
   a_carried_head_gets_no_review_row
# RP7 a 7-hex prefix counts as a carried head
nx '                .any(|c| c.len() >= 8 && head.starts_with(c.as_str()))' '                .any(|c| c.len() >= 7 && head.starts_with(c.as_str())) // knife' \
   a_carried_head_gets_no_review_row
# RP8 a review that only mentions carried in its range counts as carried
nx '            if !verdict.contains("carried") {' '            if !(v.contains("carried") || verdict.contains("carried")) { // knife' \
   carried_heads_come_from_carried_names_and_carried_verdicts
