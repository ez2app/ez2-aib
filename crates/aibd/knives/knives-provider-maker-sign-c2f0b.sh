#!/usr/bin/env bash
# aib-knives: os=macos
# PM1 L2 provider_maker (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S; 3e53b N2 on the L1 interface): the production
# fetch's signer is the maker check fixed inside. macOS only (90856 TRIAGE-890 #5): on Linux AIB offers no download
# (provider_fetch::this_platform is None), so the test returns before any signature check and this knife cannot bite
# there; Windows runs the same test in the rust-windows job.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
pm() { python3 -I scripts/knife.py crates/aibd/src/provider_maker.rs "$1" "$2" --expect-fail "provider_maker::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_maker::tests::$3"; }
# PM1 the production fetch takes a signer that always says yes
pm '    let sign = move |p: &Path, f: &File| signed_by_maker(&provider, p, f);' '    let sign = move |p: &Path, f: &File| { let _ = (&provider, p, f); Ok(()) };' the_production_fetch_refuses_an_unsigned_file_with_a_measured_hash
