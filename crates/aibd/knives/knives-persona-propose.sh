#!/usr/bin/env bash
# PP1–PP14, PD1–PD2, PA1–PA6, PA8–PA15: a seat only proposes a new text for its own persona card — no persona named by the seat, the identity and hidden characters refused, one waiting per card, the hour counts withdrawn ones, expired ones swept first, a seat sees and withdraws only its own, the same text is the same proposal, the page's list and no are the built-in page's only (DESIGN-persona-propose-osverify-c2f0b-1123 r2 §1; f163c PREREAD r0/r1)
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
   '            .find(|p| p.id == id && (seat.is_none() || true))' \
   a_seat_sees_and_withdraws_only_its_own
# PP8 (PW10) a seat withdraws another seat's proposal
pp '            .find(|p| p.id == id && seat.is_none_or(|own| own == p.seat_id))' \
   '            .find(|p| p.id == id && (seat.is_none() || true))' \
   a_seat_sees_and_withdraws_only_its_own
# PP9 the same text from the same seat makes a second proposal
pp '            .find(|p| p.seat_id == ask.seat_id && p.diff_sha == diff_sha)' \
   '            .find(|p| p.seat_id == ask.seat_id && p.diff_sha == diff_sha && p.id.is_empty())' \
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
# PP14 (90856 5b44ae992 receipt) the same text for another card comes back as the first card's proposal
pp '            .find(|p| p.seat_id == ask.seat_id && p.diff_sha == diff_sha)' \
   '            .find(|p| p.seat_id == ask.seat_id && p.new_sha == new_sha)' \
   one_waiting_per_card_and_three_per_seat
# ---- at the door (ebab8 11-23 09:11 常設規則: a built-in-page gate bites a test that sends the request)
pd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "proposals_are_listed_and_denied_by_the_built_in_page_only" \
        -- cargo test -p aibd --locked --test persona_propose_door; }
# PD1 a user folder page lists and says no
pd crates/aibd/src/persona_propose.rs \
   '    if page.carried || crate::seat_input::held_as_user_page(page).await {' \
   '    if page.carried {'
# PD2 the page's arm is gone (list and no never reach persona_propose)
pd crates/aibd/src/seat_input.rs \
   '            crate::persona_propose::LIST' \
   '            "persona_proposals_gone"'
# ---- S2-2 part 2: the owner's approve wires to persona_write (waits for A12 in the integration line)
# PA1 an approve without a ticket goes on to write
pp '        None => Step::Ask(ask),' \
   '        None => Step::Write { p: Box::new(p), ask, ticket: String::new(), by },' \
   approve_without_a_ticket_writes_nothing
# PA2 the ask does not bind the base the text was proposed on
pp '        "base_sha": p.base_sha,' \
   '        "base_sha": "",' \
   a_ticket_for_another_text_writes_nothing
# PA3 a proposal no longer waiting is approved
pp '    if !matches!(p.stand, Stand::Waiting) {' \
   '    if false {' \
   only_a_waiting_proposal_is_written
# PA4 the window carries the seat's reason
pp '    let code = format!("對照碼 {}", p.diff_sha.get(..12).unwrap_or_default());' \
   '    let code = format!("對照碼 {} {}", p.diff_sha.get(..12).unwrap_or_default(), p.why);' \
   the_window_says_name_counts_and_code_only
# PA15 (f163c REVIEW-restack 527b3c87 L1) the persona's name stands before the counts and the code
pp '        &[counts.as_str(), code.as_str(), p.persona_uid.as_str()],' \
   '        &[p.persona_name.as_str(), counts.as_str(), code.as_str(), p.persona_uid.as_str()],' \
   the_window_says_name_counts_and_code_only
# PA5 an approved proposal stays waiting (it can be approved, withdrawn or denied again)
pp '        p.stand = Stand::Writing;' \
   '        p.stand = Stand::Waiting;' \
   a_written_proposal_cannot_be_written_again
# PA6 a user folder page or a carried pairing approves
pp '        return Step::Refuse("persona_built_in_page_only");' \
   '        let _ = "persona_built_in_page_only";' \
   approve_is_the_built_in_pages_only
# PA8 (f163c e5f942dd N1) the diff the owner saw is not compared (a card changed after the proposal)
pp '    if diff_sha != p.diff_sha {' \
   '    if diff_sha.is_empty() && false {' \
   only_a_waiting_proposal_is_written
# PA9 begin_write does not compare the diff either
pp '    if p.diff_sha != diff_sha {' \
   '    if diff_sha.is_empty() && false {' \
   only_a_waiting_proposal_is_written
# PA10 (f163c 1000abaa N1) a card outside its path or behind a symlink is answered as a bad request
pp '        "persona_path_outside" | "persona_path_symlink" => StatusCode::CONFLICT,' \
   '        "persona_path_outside_x" => StatusCode::CONFLICT,' \
   every_write_refusal_has_its_status
# PA11 (f163c 08:44) a card that could not be written on this machine is answered as the page's bad request
pp '        "internal" | "persona_write_io" => StatusCode::INTERNAL_SERVER_ERROR,' \
   '        "internal" => StatusCode::INTERNAL_SERVER_ERROR,' \
   every_write_refusal_has_its_status
# ---- the owner's approve at the door (ebab8 11-23 09:11 常設規則: the gate bites a test that sends the request)
pa() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "approve_is_the_built_in_pages_with_the_owners_own_ticket_only" \
        -- cargo test -p aibd --locked --test persona_approve_door; }
# PA12 a made-up ticket is taken (the owner's check is skipped; ticket and ask still read)
pa crates/aibd/src/persona_propose.rs \
   '    if !crate::owner_ok::approved_for(&ticket, &ask) {' \
   '    if crate::owner_ok::approved_for(&ticket, &ask) && false {'
# PA13 the page's approve arm is gone (approve never reaches persona_propose)
pa crates/aibd/src/seat_input.rs \
   '            | crate::persona_propose::APPROVE,' \
   '            | "persona_approve_gone",'
# PA14 a user folder page approves (user_page still read)
pa crates/aibd/src/persona_propose.rs \
   '    if page.carried || user_page {' \
   '    if page.carried || (user_page && false) {'
