#!/usr/bin/env bash
# aib-knives: os=unix
# PM1-PM3 L2 provider_maker (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S; 3e53b N2 on the L1
# interface): the production fetch's signer is the maker check fixed inside, an unknown
# provider is refused, and a table row becomes the same fetch row.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
pm() { python3 -I scripts/knife.py crates/aibd/src/provider_maker.rs "$1" "$2" --expect-fail "provider_maker::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_maker::tests::$3"; }
# PM1 the production fetch takes a signer that always says yes
pm '    let sign = move |p: &Path, f: &File| signed_by_maker(&provider, p, f);' '    let sign = move |p: &Path, f: &File| { let _ = (&provider, p, f); Ok(()) };' the_production_fetch_refuses_an_unsigned_file_with_a_measured_hash
# PM2 an unknown provider gets some maker
pm '    let Some((org, team)) = maker(provider) else {' '    let Some((org, team)) = maker(provider).or(Some(("Anthropic, PBC", "Q6L2SF6YDW"))) else {' only_a_known_maker_is_checked_and_the_requirement_names_its_team
# PM3 the row's hash is not carried over
pm '            sha256: m.sha256.to_owned(),' '            sha256: m.sha256.to_uppercase(),' a_measured_row_becomes_the_same_fetch_row
