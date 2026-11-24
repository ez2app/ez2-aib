#!/usr/bin/env bash
# A6c always-allow (IFACE-netallow-ticket-c2f0b-1123 r2 §5):
# signature verified, uid checked, 30-day cap enforced, classify before list, revoked refused.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# na-verify
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if !verify_fn(&msg, &row.sig) {' \
  '        if false && !verify_fn(&msg, &row.sig) {' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-uid
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if uid != persona_uid || canon != want_canon {' \
  '        if canon != want_canon {' \
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
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if db.net_allow_is_revoked(&row.ticket).unwrap_or(true) {' \
  '        if false {' \
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
