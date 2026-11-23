#!/usr/bin/env bash
# PP1–PP13, PD1–PD2: a seat only proposes a new text for its own persona card — no persona named by the seat, the identity and hidden characters refused, one waiting per card, the hour counts withdrawn ones, expired ones swept first, a seat sees and withdraws only its own, the same text is the same proposal, the page's list and no are the built-in page's only (DESIGN-persona-propose-osverify-c2f0b-1123 r2 §1; f163c PREREAD r0/r1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pp() { python3 -I scripts/knife.py crates/aibd/src/persona_propose.rs "$1" "$2" --expect-fail "persona_propose::tests::$3" \
        -- cargo test -p aibd --locked --lib "persona_propose::tests::$3"; }
# PP1 (PW3) the seat may name a persona
pp '        PROPOSE => &["type", "card_text", "why"],' \
   '        PROPOSE => &["type", "card_text", "why", "persona_uid"],' \
   a_seat_act_takes_only_its_keys
# PP2 (PW4) an added line may hide characters
pp '        if persona_diff::hidden_char_line(&diff).is_some() {' \
   '        if persona_diff::hidden_char_line(&diff).is_some() && diff.added == usize::MAX {' \
   the_identity_and_hidden_characters_are_refused
# PP3 (PW7, r4 §9) the front matter may change
pp '        if front_block(new) != front_block(card.text) {' \
   '        if front_block(new) != front_block(card.text) && new.is_empty() {' \
   the_identity_and_hidden_characters_are_refused
# PP4 (PW8) two proposals wait on one card
pp '        if l.iter().filter(waiting).any(|p| p.persona_uid == card.uid) {' \
   '        if l.iter().filter(waiting).any(|p| p.persona_uid == card.uid && p.seat_id == ask.seat_id) {' \
   one_waiting_per_card_and_three_per_seat
# PP5 (PW8) a withdrawn one does not count toward the hour
pp '            a.push((ask.seat_id.to_owned(), now));' \
   '            a.push((String::new(), now));' \
   withdrawn_ones_count_toward_the_hour
# PP6 an expired one still holds its card
pp '                p.stand = Stand::Expired;' \
   '                p.stand = Stand::Waiting;' \
   expired_ones_are_swept_before_counting
# PP7 (PW10) a seat reads another seat's proposal
pp '            .find(|p| p.id == id && seat.is_none_or(|s| s == p.seat_id))' \
   '            .find(|p| p.id == id)' \
   a_seat_sees_and_withdraws_only_its_own
# PP8 (PW10) a seat withdraws another seat's proposal
pp '            .find(|p| p.id == id && seat.is_none_or(|own| own == p.seat_id))' \
   '            .find(|p| p.id == id)' \
   a_seat_sees_and_withdraws_only_its_own
# PP9 the same text from the same seat makes a second proposal
pp '            .find(|p| p.seat_id == ask.seat_id && p.new_sha == new_sha)' \
   '            .find(|p| p.seat_id == ask.seat_id && p.new_sha == new_sha && p.id.is_empty())' \
   a_proposal_is_frozen_with_its_diff_and_hash
# PP10 (PW10) a carried pairing or a user folder page lists and says no
pp '    if page.carried || crate::seat_input::held_as_user_page(page).await {' \
   '    if page.carried && crate::seat_input::held_as_user_page(page).await {' \
   a_carried_pairing_or_a_user_page_neither_lists_nor_says_no
# PP11 the reason may show a clock
pp '            kept[i] = '"'"'·'"'"';' \
   '            kept[i] = '"'"':'"'"';' \
   a_proposal_is_frozen_with_its_diff_and_hash
# PP12 (r7 §12) a repeat of the same text is journaled as a new ask
pp '        PROPOSE if v["dedup"] == false => "persona_propose.asked",' \
   '        PROPOSE => "persona_propose.asked",' \
   asking_and_withdrawing_are_journaled_without_the_text
# PP13 (r7 §12) a withdrawal leaves no journal line
pp '        WITHDRAW => "persona_propose.withdrawn",' \
   '        "never" => "persona_propose.withdrawn",' \
   asking_and_withdrawing_are_journaled_without_the_text
# ---- at the door (ebab8 11-23 09:11 常設規則: a built-in-page gate bites a test that sends the request)
pd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "proposals_are_listed_and_denied_by_the_built_in_page_only" \
        -- cargo test -p aibd --locked --test persona_propose_door; }
# PD1 a user folder page lists and says no
pd crates/aibd/src/persona_propose.rs \
   '    if page.carried || crate::seat_input::held_as_user_page(page).await {' \
   '    if page.carried {'
# PD2 the page's arm is gone (list and no never reach persona_propose)
pd crates/aibd/src/seat_input.rs \
   '        Some(crate::persona_propose::LIST | crate::persona_propose::DENY) => {' \
   '        Some("persona_proposals_gone") => {'
