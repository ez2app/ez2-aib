#!/usr/bin/env bash
# aib-knives: os=macos
# N-ws-oneweb knives OW1–OW6 (7b81e 11-18; SPEC-workspace-multiseat §19b / §19c, Andy 20:1x / 20:3x): the box under the
# code confirms only when ticked with "1" and only for the built-in page, the terminal's credential comes once and ends
# with the page's, and `aibd ctl web` asks no y once the page confirmed. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# OW1 a user folder page's ticked box confirms too
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            p.stored = if confirm_in_page && !user_page {' \
  '            p.stored = if confirm_in_page { // knife' \
  --expect-fail pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it \
  -- cargo test -p aibd --locked --lib pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it
# OW2 any value ticks the box (so an unticked "0" skips the y)
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '                f.confirm_in_page = v == "1";' \
  '                f.confirm_in_page = true || v == "1"; // knife' \
  --expect-fail pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked \
  -- cargo test -p aibd --locked --lib pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked
# OW3 the terminal's credential can be taken twice
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        if !p.terminal || p.linked.is_some() || p.state(now) != PairState::Confirmed {' \
  '        if !p.terminal || p.state(now) != PairState::Confirmed { // knife' \
  --expect-fail pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it \
  -- cargo test -p aibd --locked --lib pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it
# OW4 revoking the page leaves the terminal's credential alive
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            if let Some(t) = linked {' \
  '            if let Some(t) = linked.filter(|_| false) { // knife' \
  --expect-fail pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it \
  -- cargo test -p aibd --locked --lib pairing::tests::the_box_confirms_only_the_built_in_page_and_the_terminal_rides_on_it
# OW5 the redeem route ignores the box (only the unit rule would hold)
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '    match p.redeem_with(&f.code, user_page, confirm_in_page, f.nonce.as_deref()) {' \
  '    match p.redeem_with(&f.code, user_page, confirm_in_page && false, f.nonce.as_deref()) { // knife' \
  --expect-fail a_ticked_box_confirms_on_the_page_and_the_terminal_is_paired_once \
  -- cargo test -p aibd --locked --test pair_box a_ticked_box_confirms_on_the_page_and_the_terminal_is_paired_once
# OW6 aibd ctl web still waits for a y after the page confirmed
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '            Some("confirmed") => {' \
  '            Some("confirmed_never") => { // knife' \
  --expect-fail ctl::tests::web_with_the_box_ticked_asks_no_y_and_pairs_the_terminal \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::web_with_the_box_ticked_asks_no_y_and_pairs_the_terminal
# TC1 (§19d) a tab inside tmux / screen is closed too
python3 -I scripts/knife.py crates/aibd/src/term_close.rs \
  '    if set("TMUX") || set("STY") {' \
  '    if false && (set("TMUX") || set("STY")) { // knife' \
  --expect-fail term_close::tests::only_a_known_terminal_with_nothing_else_in_the_tab_is_closed \
  -- cargo test -p aibd --locked --lib term_close::tests::only_a_known_terminal_with_nothing_else_in_the_tab_is_closed
# TC2 (§19d) other work in the tab is not seen
python3 -I scripts/knife.py crates/aibd/src/term_close.rs \
  '    rows.iter().filter(|r| !mine.contains(&r.0)).count()' \
  '    rows.iter().filter(|r| !mine.contains(&r.0)).count() * 0 // knife' \
  --expect-fail term_close::tests::my_own_ancestors_are_not_other_work \
  -- cargo test -p aibd --locked --lib term_close::tests::my_own_ancestors_are_not_other_work
# TC3 (§19d) Terminal closes a window that has other tabs
python3 -I scripts/knife.py crates/aibd/src/term_close.rs \
  '             if (count of tabs of w) is 1 and (tty of tab 1 of w) is \"{tty}\" then close w\n\' \
  '             if (tty of tab 1 of w) is \"{tty}\" then close w\n\' \
  --expect-fail term_close::tests::the_script_closes_only_the_exact_tty_and_only_when_it_is_the_one \
  -- cargo test -p aibd --locked --lib term_close::tests::the_script_closes_only_the_exact_tty_and_only_when_it_is_the_one
# OW7 (519d0 20:36) a late re-send by the same page still keeps the credential
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            && p.redeemed.is_some_and(|r| now < r + RESUBMIT_WINDOW)' \
  '            && p.redeemed.is_some_and(|r| now < r + RESUBMIT_WINDOW * 1000) // knife' \
  --expect-fail pairing::tests::the_same_page_sending_the_code_twice_keeps_its_credential \
  -- cargo test -p aibd --locked --lib pairing::tests::the_same_page_sending_the_code_twice_keeps_its_credential
# OW8 (519d0 20:36) another page's nonce (or none at first) passes as the same page
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            && matches!((p.page_nonce, nonce_hash), (Some(a), Some(b)) if bool::from(a.ct_eq(&b)))' \
  '            && matches!((p.page_nonce, nonce_hash), (_, Some(_))) // knife' \
  --expect-fail pairing::tests::the_same_page_sending_the_code_twice_keeps_its_credential \
  -- cargo test -p aibd --locked --lib pairing::tests::the_same_page_sending_the_code_twice_keeps_its_credential
# OW9 (519d0 20:36) a nonce is taken twice in one form
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '            "n" if !n && nonce_ok(v) => {' \
  '            "n" if (n || !n) && nonce_ok(v) => { // knife' \
  --expect-fail pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked \
  -- cargo test -p aibd --locked --lib pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked
# OW10 (5d33a PREREAD-oneweb M1) a user folder page without to=user confirms in the page
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '    let user_page = f.to_user || served_by_user;' \
  '    let user_page = f.to_user || (served_by_user && false); // knife' \
  --expect-fail pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked \
  -- cargo test -p aibd --locked --lib pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked
# OW11 (§19f) more than MAX_ROTATIONS new codes
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '                        && o.rotation < MAX_ROTATIONS' \
  '                        && o.rotation < MAX_ROTATIONS + 100 // knife' \
  --expect-fail pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one \
  -- cargo test -p aibd --locked --lib pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one
# OW12 (§19f) a live code is swapped
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '                        && o.state(now) == PairState::Expired =>' \
  '                        && o.state(now) != PairState::Revoked => // knife' \
  --expect-fail pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one \
  -- cargo test -p aibd --locked --lib pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one
# OW13 (§19f) an expired code is told as a wrong one
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            return Err(Self::fail(&mut inner, now, PairError::Expired));' \
  '            return Err(Self::fail(&mut inner, now, PairError::Failed)); // knife' \
  --expect-fail pairing::tests::expired_code_never_redeems_even_racing_the_expiry \
  -- cargo test -p aibd --locked --lib pairing::tests::expired_code_never_redeems_even_racing_the_expiry
# OW14 (§19f) ctl web swaps codes without end
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '        if ran_out && rotations < aibd::pairing::MAX_ROTATIONS {' \
  '        if ran_out && rotations < u8::MAX { // knife' \
  --expect-fail ctl::tests::web_swaps_a_code_that_ran_out_and_goes_on_with_the_new_one \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::web_swaps_a_code_that_ran_out_and_goes_on_with_the_new_one
# OW15 (§19f) replaces is taken without terminal
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '            if terminal && s.len() == 16 && s.bytes().all(|b| b.is_ascii_hexdigit()) =>' \
  '            if s.len() == 16 && s.bytes().all(|b| b.is_ascii_hexdigit()) => // knife' \
  --expect-fail pair_api::tests::a_code_body_replaces_only_the_terminals_own_pair_id \
  -- cargo test -p aibd --locked --lib pair_api::tests::a_code_body_replaces_only_the_terminals_own_pair_id
# OW16 (§19f) a live code is not given back with its end (the page cannot count down)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            if p.stored == Stored::Issued && p.state(now) == PairState::Issued {' \
  '            if false && p.stored == Stored::Issued && p.state(now) == PairState::Issued { // knife' \
  --expect-fail pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one \
  -- cargo test -p aibd --locked --lib pairing::tests::an_expired_terminal_code_is_swapped_and_the_page_gets_the_new_one
# OW17 (§19d) a pairing step's outcome is not journaled (the tab that closes takes it with it)
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
  '    let _ = tokio::task::spawn_blocking(move || p.journal(kind, payload)).await;' \
  '    let _ = (p, kind, payload); // knife' \
  --expect-fail each_pairing_steps_outcome_is_journaled_without_secrets \
  -- cargo test -p aibd --locked --test pairing each_pairing_steps_outcome_is_journaled_without_secrets
