#!/usr/bin/env bash
# N-workspace 第三波 1-2 + SPEC §14 knives AN1-AN10, NX1-NX5, GD1-GD4, RS3 (7ebca 11-18,
# ORDER-workspace-519d0-1118): the open items of the 待人決策 list (aib-drive andy_open, #847)
# are cards with the board's fields, 「知道」 sits with slot 6, a closed or unknown item is never
# answered, an answer goes to the seat the page names and closes the item on the same list;
# aib-drive's next list gives its fix rows as slot-2 cards and its merge / push rows as slot-5
# cards. Each rule, broken, turns its
# own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
al() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
gs() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test slots_guard "$3"; }
gl() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test slots_guard "$3"; }
ae() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test andy_open "$3"; }
# AN1
al '.filter(|i| !i.done)' \
   '.filter(|_| true)' \
   slots::tests::
# AN2
ae 'andy_items().iter().any(|i| i.item == it && !i.done)' \
   'andy_items().iter().any(|i| i.item == it)' \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# AN3
ae 'live.extend(andy_cards(&andy.0, &andy.1));' \
   'let _ = &andy; live.extend(Vec::<Card>::new());' \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# AN4
ae 'return answer_andy(st, page, seat, text, item.to_owned()).await;' \
   'let _ = (item, answer_andy);' \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# AN5
al 'card: Some(i.card.clone()).filter(|c| !c.is_empty()),' \
   'card: None,' \
   slots::tests::
# AN6
al 'rec: field("rec"),' \
   'rec: i.fields.get("rec").cloned(),' \
   slots::tests::
# AN7
ae 'std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),' \
   None, \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# AN8
ae 'let closed = tokio::task::spawn_blocking(move || close_andy(&item))' \
   'let closed = tokio::task::spawn_blocking(move || { let _ = (&item, close_andy); false })' \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# AN9
al 'let slot = if i.item_kind == "知道" { 6 } else { 1 };' \
   'let slot = 1;' \
   slots::tests::
# AN10
al '.unwrap_or_else(|| TAGS[usize::from(self.slot - 1)].to_owned());' \
   '.unwrap_or_else(|| self.kind.clone());' \
   slots::tests::
# NX1
al '"merge" | "push" => Some(5),' \
   '"merge" => Some(5),' \
   slots::tests::
# NX2
al 'if next.schema != NEXT_SCHEMA {' \
   'if false {' \
   slots::tests::
# NX3
ae 'live.extend(next.as_ref().map(next_cards).unwrap_or_default());' \
   'let _ = &next;' \
   an_open_andy_item_is_a_card_a_page_answers_into_the_lead_seat
# NX4
al 'state: Some(r.needs.clone()).filter(|n| !n.is_empty() && n != "none"),' \
   'state: Some(r.needs.clone()),' \
   slots::tests::
# NX5
al '"fix" => Some(2),' \
   '"fix" => Some(5),' \
   slots::tests::
# GD1
gs 'let user_page_answers_none = held_as_user_page(&page).await;' \
   'let user_page_answers_none = false;' \
   only_a_built_in_page_paired_now
# GD2
gs 'let carried_answers_none = page.carried;' \
   'let carried_answers_none = false;' \
   only_a_built_in_page_paired_now
# GD3
gl 'if v.get("kind").and_then(Value::as_str) != Some("andy.open") {' \
   'if true {' \
   only_a_built_in_page_paired_now
# GD4
gl 'let words = crate::workspace::gives_words(&c).await;' \
   'let words = { let _ = &c; true };' \
   only_a_built_in_page_paired_now
# RS3 slot_restore skips the page checks
gs 'Some(t @ (crate::slots::ANSWER | crate::slots::DISMISS | crate::slots::RESTORE | crate::slots::PIN | crate::slots::IDEA_ANSWER | crate::slots::REVIEW_SEEN | crate::seat_send::RESUME)) => {' \
   'Some(t @ (crate::slots::ANSWER | crate::slots::DISMISS | crate::slots::PIN | crate::slots::IDEA_ANSWER | crate::slots::REVIEW_SEEN | crate::seat_send::RESUME)) => {' \
   only_a_built_in_page_paired_now
