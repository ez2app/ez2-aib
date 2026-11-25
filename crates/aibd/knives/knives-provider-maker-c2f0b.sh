#!/usr/bin/env bash
# aib-knives: os=unix
# PM3–PM8 L2 provider_maker (PM1 / PM2 are in knives-provider-maker-sign-c2f0b.sh, macOS) (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S; 3e53b N2 on the L1
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
