#!/usr/bin/env bash
# Pairing knives (ADR 0006 §7): each breaks one rule and must turn its own test red
# (scripts/knife.py checks the failed test's name). Run from the repo root.
# RUSTFLAGS may carry -D warnings, so a knife keeps every binding used.
set -eu
G=crates/aibd/src/guard.rs
P=crates/aibd/src/pairing.rs
A=crates/aibd/src/pair_api.rs
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test pairing "$4"; }
kl() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }

# P1 an unconfirmed credential is let through to everything
k "$G" '    if !page.confirmed && !UNCONFIRMED_PATHS.contains(&path) {' \
       '    if !page.confirmed && !UNCONFIRMED_PATHS.contains(&path) && false { // knife' \
  an_unconfirmed_credential_gets_only_the_allow_list
# P2 a page credential reaches the start-up-token-only code endpoints (it could confirm itself)
k "$G" '    if path == PAIR_CODES || path.starts_with("/api/v1/pair/codes/") {' \
       '    if (path == PAIR_CODES || path.starts_with("/api/v1/pair/codes/")) && false { // knife' \
  a_page_credential_never_reaches_the_code_endpoints
# P3 a second redemption answers 409 but leaves the first credential alive
k "$P" $'            // second use, whatever the first one\'s state: revoke what it got\n            Self::revoke(&mut inner, &id);' \
       '            // knife: no revoke on reuse' \
  a_second_redemption_is_409_and_revokes_the_first_credential
# P4 the confirmation window running out confirms instead of revoking
k "$P" '            Stored::Awaiting if now >= r + CONFIRM_WINDOW => PairState::Revoked,' \
       '            Stored::Awaiting if now >= r + CONFIRM_WINDOW => PairState::Confirmed, // knife' \
  an_attacker_who_redeems_first_never_unlocks_by_waiting
# P5 a wrong short code leaves the pairing open for another try
k "$P" $'        if !ok {\n            Self::revoke(&mut inner, pair_id);' \
       $'        if !ok {\n            // knife: no revoke on a wrong short code' \
  a_wrong_short_code_revokes_and_cannot_be_retried
# P6 no rate limit on failures
k "$P" '        let hit = inner.failures.len() >= MAX_FAILURES;' \
       '        let hit = inner.failures.len() >= MAX_FAILURES && false; // knife' \
  wrong_guesses_hit_the_rate_limit_but_not_an_unrelated_session
# P7 `Origin: null` passes elsewhere once someone lists it as allowed
k "$G" '            Some("null") => return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed")),' \
       '            Some("null") if false => return Err((ErrorCode::OriginNotAllowed, "Origin is not allowed")), // knife' \
  null_origin_is_refused_everywhere
# P8 redeem takes any http origin (a hostile page on another local port)
k "$G" 'cfg.own_origins.iter().any(|a| a.eq_ignore_ascii_case(o))' \
       '(o == "null" || cfg.own_origins.iter().any(|a| a.eq_ignore_ascii_case(o))) /* knife */' \
  redeem_takes_only_a_small_form_from_null_or_its_own_origin
# P9 a query string on a pairing path is accepted
k "$G" '    if path.starts_with("/api/v1/pair/") && req.uri().query().is_some() {' \
       '    if path.starts_with("/api/v1/pair/") && req.uri().query().is_some() && false { // knife' \
  credentials_and_codes_stay_out_of_urls_and_logs
# P10 a redeem failure tells reuse apart from a wrong code
k "$A" '        Err(e) => pair_error(e, REDEEM_FAILED),' \
       '        Err(e) => pair_error(e, if e == PairError::Reused { "reused" } else { REDEEM_FAILED }), // knife' \
  every_redeem_failure_says_the_same_thing
# P11 the exchange page (it carries the credential) may be cached
k "$A" '    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));' \
       '    // knife: exchange page without no-store' \
  the_exchange_page_is_not_cached_and_loads_nothing
# P12 pairing on by default (the terminal y/N is UNVERIFIED; opt-in only)
k "$P" '            channel: ConfirmChannel::Off,' \
       '            channel: ConfirmChannel::TerminalUnverified, // knife' \
  pairing_is_off_unless_turned_on
# P13 failures are not counted in metrics
k "$P" '        inner.failures_total += 1;' \
       '        // knife: failures not counted' \
  failures_and_rate_limits_are_counted_in_metrics
# P14 the page credential is found by a table probe, not a constant-time scan
kl "$P" '        let p = inner.pairs.get(found?)?;' \
        '        let p = inner.pairs.get(found.or_else(|| inner.creds.get(&digest))?)?; // knife' \
  credential_lookup_is_constant_time
# P15 the start-up token is compared with ==
kl crates/aibd/src/token.rs '        self.0.as_bytes().ct_eq(presented.as_bytes()).into()' \
        '        { let _ = self.0.as_bytes().ct_eq(b""); self.0 == presented } // knife (keeps the import used)' \
  start_up_token_is_compared_in_constant_time
# P16 the credential scan stops at the first match (early exit)
kl "$P" '                found = Some(id);' \
        '                found = Some(id); break; // knife' \
  credential_lookup_is_constant_time
# P17 the start-up token check returns early on a length mismatch
kl crates/aibd/src/token.rs '        self.0.as_bytes().ct_eq(presented.as_bytes()).into()' \
        '        if self.0.len() != presented.len() { return false; } self.0.as_bytes().ct_eq(presented.as_bytes()).into() // knife' \
  start_up_token_is_compared_in_constant_time
# P18 no cap on live credentials (the constant-time scan grows with them)
kl "$P" '        if inner.creds.len() >= MAX_CREDENTIALS {' \
        '        if inner.creds.len() >= MAX_CREDENTIALS && false { // knife' \
  live_credentials_are_capped_and_freed_by_revoke
# P19 a confirmed page credential may write (argv, seats) like the start-up token (a9136 M1)
k "$G" '    if !reads && path != PAIR_REVOKE {' \
       '    if !reads && path != PAIR_REVOKE && false { // knife' \
  a_confirmed_page_can_only_read_and_never_send_argv
# P20 the short code exists before redemption (not bound to the credential; a9136 L1)
kl "$P" '            p.short_code = short_code;' \
        '            let _ = &short_code; // knife: keeps the create-time empty code' \
  the_short_code_exists_only_after_redemption
# P21 confirm shares the redeem failure limit again (any page can block the person; 043dd M1)
k "$P" '        // confirm has no shared rate limit: anyone can fill the redeem bucket (any web page),' \
       '        if Self::limited(&mut inner) { return Err(PairError::RateLimited); } // knife' \
  a_flood_of_wrong_codes_never_blocks_a_waiting_confirmation
# P22 redeem runs and counts failures while pairing is off (043dd L2)
k "$P" $'            // off means off: no matching, no failure counted (043dd A4 L2)\n            return Err(PairError::Off);' \
       '            // knife: redeem runs while off' \
  pairing_is_off_unless_turned_on
# P23 a page credential in the URL passes (043dd L5)
k "$G" '    if page_credential_in_url(cfg, raw_path, req.uri().query()) {' \
       '    if page_credential_in_url(cfg, raw_path, req.uri().query()) && false { // knife' \
  a_page_credential_in_the_url_is_refused
# P24 a cancelled, never redeemed code counts as a reuse (043dd N2)
kl "$P" '        if p.stored == Stored::Revoked && p.cred.is_none() {' \
        '        if p.stored == Stored::Revoked && p.cred.is_none() && false { // knife' \
  a_dead_code_is_a_failure_not_a_reuse
echo "pairing knives: 24/24 bite"
