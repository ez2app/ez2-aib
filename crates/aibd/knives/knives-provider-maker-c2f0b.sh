#!/usr/bin/env bash
# aib-knives: os=unix
# PM3-PM9 + PS1-PS8 L2 provider_maker (PM1 / PM2 are in knives-provider-maker-sign-c2f0b.sh, macOS) (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S; 3e53b N2 on the L1
# interface): a table row becomes the same fetch row.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
pm() { python3 -I scripts/knife.py crates/aibd/src/provider_fetch/maker.rs "$1" "$2" --expect-fail "provider_fetch::maker::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::$3"; }
# PM3 the row's hash is not carried over
pm '            sha256: m.sha256.to_owned(),' '            sha256: m.sha256.to_uppercase(),' a_measured_row_becomes_the_same_fetch_row
# PM4 the download with a signer is public again (3e53b REVIEW-l2-maker N1, r3)
python3 -I scripts/knife.py crates/aibd/src/provider_fetch.rs 'fn run(' 'pub fn run(' --expect-fail "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer"
# PM5 the macOS requirement drops the Developer ID intermediate (N2)
pm '        "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists \' '        "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] absent \' only_a_known_maker_is_checked_and_the_requirement_names_its_team
# PM6 another module names the download (f163c / 3e53b REVIEW-l2-maker r2)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs 'use std::collections::HashMap;' 'use std::collections::HashMap; #[allow(dead_code)] const _X: &str = "crate::provider_fetch::run";' --expect-fail "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer"
# PM7 a glob import of the module is not counted
pm '            + code.matches(&format!("{m}::*")).count()' '            + 0' every_way_of_naming_the_download_is_counted
# PM8 another module renames the download's module (f163c REVIEW-l2-c2f0b-f163c-50ce45a5 N1)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs 'use std::collections::HashMap;' 'use std::collections::HashMap; #[allow(unused_imports)] use crate::provider_fetch as _pf;' --expect-fail "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer"
# PM9 provider_fetch gets another child module, which would see run (3e53b r3)
python3 -I scripts/knife.py crates/aibd/src/provider_fetch.rs 'pub mod maker;' 'pub mod maker; mod extra {}' --expect-fail "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::only_fetch_measured_calls_the_download_with_a_signer"
# PS1 a placed program is opened without the hash check (3e53b REVIEW-w2policy-90856 L1)
pm '    provider_fetch::verify_placed(&row, &real).map(Some)' '    std::fs::File::open(&real).map(Some).map_err(|_| provider_fetch::ERR_VERIFY)' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
# PS2 a version the table does not list is opened as if it were the listed one
pm '    let row = row_for(provider, version).ok_or(ERR_UNMEASURED)?;' '    let row = row_for(provider, version).or_else(|| row_for(provider, "9.9.9")).ok_or(ERR_UNMEASURED)?;' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
# PS3 opening a seat skips the check
python3 -I scripts/knife.py crates/aibd/src/seats.rs '        crate::provider_fetch::maker::placed_for_seat(&data, &program)' '        { let _ = (&data, &program); Ok::<Option<std::fs::File>, &'"'"'static str>(None) }' --expect-fail "provider_fetch::maker::tests::opening_a_seat_calls_the_placed_check_before_the_program_starts" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::opening_a_seat_calls_the_placed_check_before_the_program_starts"
# PS4 a link named in the policy is followed to the placed program (3e53b 05:13 point 1)
pm '    if !same || !plain {' '    if !same || (!plain && false) {' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
# PS5 any file in the version folder passes for the row's program (3e53b 05:13 point 1)
pm '    if !same || !plain {' '    if (!same && false) || !plain {' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
# PS6 a session that waited for a slot starts without the check (f163c REVIEW-l2-c2f0b-f163c-b029c9cc L1)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs '            let held = match self.placed_now(argv.first().map(String::as_str)) {' '            let held = match self.placed_now(None) {' --expect-fail "a_placed_program_changed_while_its_session_waited_does_not_start" \
        -- cargo test -p aibd --locked --test placed_queue
# PS7 a link on the path from the providers folder down passes (3e53b r6 L2)
pm '    if !no_link {' '    if !no_link && false {' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
# PS8 the data folder is not one of the steps checked for links (3e53b r10)
pm '        data.to_path_buf(),' '        // PS8: the data folder left out' a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy
