#!/usr/bin/env bash
# aib-knives: os=macos
# PM1-PM2 L2 provider_maker (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S; 3e53b N2 on the L1 interface): the production
# fetch's signer is the maker check fixed inside (PM1), an unknown provider is refused (PM2). macOS only (90856
# TRIAGE-890 #5, ebab8 17:08): on Linux AIB offers no download (provider_fetch::this_platform is None, so PM1's test
# returns before any signature check) and signed_by_maker's Linux branch refuses every provider with not_offered, so
# PM2's mutant answers the same there; Windows runs the same tests in the rust-windows job.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
pm() { python3 -I scripts/knife.py crates/aibd/src/provider_fetch/maker.rs "$1" "$2" --expect-fail "provider_fetch::maker::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_fetch::maker::tests::$3"; }
# PM1 the production fetch takes a signer that always says yes
pm '    let sign = move |p: &Path, f: &File| signed_by_maker(&provider, p, f);' '    let sign = move |p: &Path, f: &File| { let _ = (&provider, p, f); Ok(()) };' the_production_fetch_refuses_an_unsigned_file_with_a_measured_hash
# PM2 an unknown provider gets some maker
pm '    let Some((org, team)) = maker(provider) else {' '    let Some((org, team)) = maker(provider).or(Some(("Anthropic, PBC", "Q6L2SF6YDW"))) else {' only_a_known_maker_is_checked_and_the_requirement_names_its_team
