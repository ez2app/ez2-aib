#!/usr/bin/env bash
# OT1–OT3 (7b81e 11-20; GAP-repo-register G2, 043dd eeaf7378e4; 519d0 06:35「G2 不等 a63」):
# GET /api/v1/owner/tickets/{id} is the page's own read, shows no approval digest, and takes
# only a ticket-shaped word.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ou() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::a_page_asks_only_its_six_categories_and_nothing_is_wired_yet \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_page_asks_only_its_six_categories_and_nothing_is_wired_yet; }
oh() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail a_repo_reaches_a_seat_only_through_the_owners_fingerprint \
  -- cargo test -p aibd --locked --test seats_page_open_repo a_repo_reaches_a_seat_only_through_the_owners_fingerprint; }
# OT1 any caller reads a ticket's state
oh '    if !matches!(c, crate::guard::Caller::Page(_)) {' '    if false && !matches!(c, crate::guard::Caller::Page(_)) {'
# OT2 an approval's digest reaches the page
ou '        State::Approved { .. } => serde_json::json!({"state": "approved"}),' \
   '        State::Approved { digest, .. } => serde_json::json!({"state": "approved", "digest": digest}),'
# OT3 any word is taken as a ticket
ou '            .all(|b| b.is_ascii_alphanumeric() || b == b'"'"'-'"'"' || b == b'"'"'_'"'"')' \
   '            .all(|b| b != 0)'
