#!/usr/bin/env bash
# N-ws-evcards-rest + N-ws-slot-sources knives LC1-LC10 (7ebca 11-19, 519d0 06:31 / 07:22 / 07:39;
# 0eed7 VERIFY-evcards r1-r4): a failed review / red receipt / untaken 交件單 / merge is a card only
# while open; a review without its author is closed both ways; a file is read by name only when an
# open ledger card points at it; a session or a turn that ended for its usage or a login is slot 3.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
lc() { python3 -I scripts/knife.py crates/aibd/src/ledger_cards.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
dc() { python3 -I scripts/knife.py crates/aibd/src/docs.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
sl() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# LC1 a passed review is a card too
lc '.filter(|((_, author), v)| v.2 && (author.is_empty() || live.iter().any(|s| s == author)))' \
   '.filter(|((_, author), _v)| author.is_empty() || live.iter().any(|s| s == author))' \
   ledger_cards::tests::only_the_newest_failed_review_of_a_slice_is_a_card
# LC2 an owned fail is not closed by a newer unowned review
lc '!unowned_after' \
   '{ let _ = unowned_after; true }' \
   ledger_cards::tests::only_the_newest_failed_review_of_a_slice_is_a_card
# LC3 a red receipt stays after its branch went green
lc 'if healed {' \
   'if false && healed {' \
   ledger_cards::tests::a_red_receipt_is_a_card_until_its_branch_has_a_full_green
# LC4 a superseded 交件單 stays a card
lc '.filter(|(name, ..)| !taken.contains(name))' \
   '.filter(|(name, ..)| !taken.is_empty() || name.is_empty() || true)' \
   ledger_cards::tests::a_delivery_note_is_a_card_until_a_later_one_supersedes_it
# LC5 an old merge is a card
lc 'if at.as_str() < since.as_str() {' \
   'if false && at.as_str() < since.as_str() {' \
   ledger_cards::tests::a_merge_in_the_last_day_is_a_done_card_once_per_head
# LC6 the writer, not the author, is a merge's seat
lc 'let seat = s("author").filter(|x| hex5(x));' \
   'let seat = s("seat").filter(|x| hex5(x));' \
   ledger_cards::tests::a_merge_in_the_last_day_is_a_done_card_once_per_head
# LC7 any file is read by name
dc 'crate::ledger_cards::cards(now).iter().any(|c| {' \
   'true || crate::ledger_cards::cards(now).iter().any(|c| {' \
   docs::tests::a_file_by_name_is_only_an_open_ledger_cards
# LC8 a login is not told from usage
sl 'if any(&LOGIN) {' \
   'if false && any(&LOGIN) {' \
   slots::tests::a_session_that_ended_for_its_usage_or_a_login_is_low_power
# LC9 an exit for its usage stays a notice
sl 'c.slot = 3;' \
   'c.slot = c.slot;' \
   slots::tests::a_session_that_ended_for_its_usage_or_a_login_is_low_power
# LC10 another session's output is read
sl '.filter(|o| o.global_seq < e.global_seq && o.aggregate_id == agg)' \
   '.filter(|o| o.global_seq < e.global_seq && !agg.is_empty())' \
   slots::tests::a_session_that_ended_for_its_usage_or_a_login_is_low_power
# LC11 a compile-only green stands for the newest of its name and hides the red
lc '    if !red(v) && !green_full(v) {' \
   '    if false && !red(v) && !green_full(v) {' \
   a_red_receipt_is_a_card_until_its_branch_has_a_full_green
