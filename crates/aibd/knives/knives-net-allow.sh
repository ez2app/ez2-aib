#!/usr/bin/env bash
# A6c always-allow (IFACE-netallow-ticket-c2f0b-1123 r2 §5):
# signature verified, uid checked, 30-day cap enforced, classify before list, revoked refused.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# na-verify
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    verify_fn(&msg, &row.sig)' \
  '    let _ = (msg, row, verify_fn); true' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-uid
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if uid != persona_uid || canon != want_canon {' \
  '        if (uid.is_empty() && persona_uid.is_empty()) || canon != want_canon {' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-30d
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '            || until - issued_at > MAX_DAYS_SECS' \
  '            || false' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-classify
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    if !matches!(' \
  '    if false && !matches!(' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-revoked
python3 -I scripts/knife.py crates/aib-store/src/net_allow.rs \
  '            if revoked.is_some() {' \
  '            if revoked.is_none() && false {' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-issued
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if now < issued_at' \
  '        if false' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-tunnel-revoke
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    db.net_allow_revoke(ticket, now as i64)?;' \
  '    let _ = (db, ticket, now);' \
  --expect-fail net_allow::tests::proxy_decide_uses_net_allow_holds_and_closes_tunnels_on_revoke_expire_and_inactive \
  -- cargo test -p aibd --locked --lib net_allow::tests::proxy_decide_uses_net_allow_holds_and_closes_tunnels_on_revoke_expire_and_inactive
# na-list-expired
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            if until <= now_unix {' \
  '            if false {' \
  --expect-fail net_card_web::tests::db_allow_rows_filter_expired_and_other_persona_and_emit_ip_port \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_filter_expired_and_other_persona_and_emit_ip_port
# na-is-revoked
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if db.net_allow_is_revoked(&row.ticket).unwrap_or(true) {' \
  '        if false {' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-persona-active
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    if !persona_active {' \
  '    if false && !persona_active {' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-list-sig
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        if !crate::net_allow::row_sig_ok(r, verify_fn) {' \
  '        if false && !crate::net_allow::row_sig_ok(r, verify_fn) {' \
  --expect-fail net_card_web::tests::db_allow_rows_filter_expired_and_other_persona_and_emit_ip_port \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_filter_expired_and_other_persona_and_emit_ip_port
# na-choices-no-always
python3 -I scripts/knife.py crates/aibd/src/net_card.rs \
  '            .filter(|c| owner_avail || *c != Choice::Always)' \
  '            .filter(|_| true)' \
  --expect-fail net_card::tests::choices_with_owner_hides_always_when_unavailable \
  -- cargo test -p aibd --locked --lib net_card::tests::choices_with_owner_hides_always_when_unavailable
# na-restart-list-ip
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '                obj["ip"] = json!(sa.ip().to_string());' \
  '                let _ = sa;' \
  --expect-fail a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice \
  -- cargo test -p aibd --locked --test net_card_owner a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
# na-dedup-allow
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            if let Some(existing) = dup {' \
  '            if let Some(existing) = dup.filter(|_| false) {' \
  --expect-fail a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice \
  -- cargo test -p aibd --locked --test net_card_owner a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
