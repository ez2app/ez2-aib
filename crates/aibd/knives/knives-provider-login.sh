#!/usr/bin/env bash
# RL1-RL12 重新登入 AI from the page (DESIGN-relogin-card-a9136-1121 r2; 5d33a PREREAD-relogin 0/0/2/3, r2 0/0/0/3)
set -euo pipefail
pl() { python3 -I scripts/knife.py crates/aibd/src/provider_login.rs "$1" "$2" --expect-fail "provider_login::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_login::tests::$3"; }
oo() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "provider_login::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_login::tests::$3"; }
sl() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "slots::tests::$3" \
        -- cargo test -p aibd --locked --lib "slots::tests::$3"; }
# RL1 the login is not asked through the owner's fingerprint
oo '    crate::provider_login::OP, // provider.login (重新登入)' '    // knife' \
   the_login_asks_the_owners_fingerprint
# RL2 the program runs with words a caller could add
pl '        "claude" => Some(&["auth", "login", "--claudeai"]),' '        "claude" => Some(&["auth", "login", "--email"]), // knife' \
   the_program_runs_with_fixed_words_only
# RL3 an address with a fragment, a backslash or an invisible character is handed on
pl "    if !rest.bytes().all(|b| b.is_ascii_graphic()) || rest.contains(['#', '\\\\']) {" '    if rest.is_empty() { // knife' \
   only_the_providers_own_sign_in_address_is_handed_on
# RL4 a host that only ends like the provider's, or carries a user, is handed on
pl '    if &rest[..cut] != want {' '    if !rest[..cut].ends_with(want) { // knife' \
   only_the_providers_own_sign_in_address_is_handed_on
# RL5 a preview runs the login itself
pl '        return Err((StatusCode::CONFLICT, "login_in_parent"));' '        let _ = provider; // knife' \
   a_round_starts_only_in_the_parent_one_at_a_time
# RL6 two rounds of one provider run at once
pl '        if r.state == State::Running {' '        if r.state == State::Ok && r.ended.is_none() { // knife' \
   a_round_starts_only_in_the_parent_one_at_a_time
# RL7 no cooling after a round
pl '        if r.ended.is_some_and(|e| now.duration_since(e) < COOL) {' '        if r.ended.is_some_and(|e| now.duration_since(e) > COOL) { // knife' \
   a_round_starts_only_in_the_parent_one_at_a_time
# RL8 another pairing writes the code
pl '    let Some(r) = g.values_mut().find(|r| r.id == id && r.by == by) else {' '    let Some(r) = g.values_mut().find(|r| r.id == id && !by.is_empty()) else { // knife' \
   the_program_prints_its_address_takes_the_code_and_is_ended
# RL9 the code is written twice
pl '    if r.state != State::Running || r.code_written || r.provider != "claude" || !code_ok(code) {' '    if r.state != State::Running || r.provider != "claude" || !code_ok(code) { // knife' \
   the_program_prints_its_address_takes_the_code_and_is_ended
# RL10 the login opens a browser on aibd's machine
pl '        env: vec![(NO_BROWSER.0.to_owned(), NO_BROWSER.1.to_owned())],' '        env: Vec::new(), // knife' \
   the_login_runs_where_the_seats_read_their_login
# RL11 the login is marked as a seat's session
pl '        .filter(|(k, _)| k != crate::platform::session::SESSION_ENV)' '        .filter(|(k, _)| k != "AIBD_NONE") // knife' \
   the_login_runs_where_the_seats_read_their_login
# RL12 重新登入 is not offered on a login end
sl '    c.actions = if *r == "登入" {' '    c.actions = if *r == "額度" { // knife' \
   a_session_that_ended_for_its_usage_or_a_login_is_low_power
