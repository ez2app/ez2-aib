#!/usr/bin/env bash
# T136-3b knives (CP6 SIGN copy re-checked against its sha; NC used-nonce ledger), from the aib-consent knife step.
# Moved out of .github/workflows/aibd.yml word for word (519d0 11-16 23:5x: the workflow file is near
# its 450,000-byte guard; new knives live in crates/<crate>/knives/). aib-xtask knife-anchors reads
# crates/*/knives/*.sh. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# same helper as the aib-consent knife step (this script runs in its own shell)
cp() { python3 -I scripts/knife.py crates/aib-consent/src/prepare.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-consent --locked --test prepare "$3"; }
# CP6 (T136-3b) the SIGN copy runs from its path, not re-checked against the sha taken at prepare
cp '                    program: params::read_staged(copy, sha, MAX_SIGN_BYTES)?,' \
   '                    program: { let _ = sha; params::read_staged(copy, &params::sha256_hex(&fs::read(copy).unwrap_or_default()), MAX_SIGN_BYTES)? }, // knife' \
   the_copies_are_read_again_against_their_sha_before_the_run
# T136-3b used-nonce ledger (c477a; knives 7b81e 11-15)
nk() { python3 -I scripts/knife.py crates/aib-consent/src/nonces.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-consent --locked --lib "$3"; }
# NC1 a nonce file that exists is opened again: the second consume is true
nk '            .create_new(true)' '            .create(true) // knife' \
   a_nonce_counts_once_also_for_a_second_ledger
# NC2 a nonce that is not lower hex (a path, upper case) is taken
nk "            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))" "            .all(|b| b != 0) // knife" \
   a_nonce_that_is_not_lower_hex_is_refused
# NC3 a folder group or others can read is taken
nk '        if !m.file_type().is_dir() || !ours || m.mode() & 0o077 != 0 {' \
   '        if !m.file_type().is_dir() || !ours { // knife' \
   a_shared_or_linked_folder_is_refused
# NC4 prune drops a nonce before its latest proof expired (no issue skew)
nk '        let keep = lifetime_s.saturating_add(ISSUE_SKEW_S);' \
   '        let keep = lifetime_s.saturating_add(ISSUE_SKEW_S * 0); // knife' \
   prune_keeps_a_nonce_until_its_latest_proof_has_expired
