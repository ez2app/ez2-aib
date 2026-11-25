#!/usr/bin/env bash
# aib-knives: os=macos   # 90856 11-25 E3 r2: a test its knives expect red already fails on ubuntu unmutated (false bite there)
# Preview pass knives PV1–PV9 (7b81e 11-19; Andy 08:5x via 519d0: a preview opens already paired). The parent's paired
# built-in page alone gets a one-time pass; only its digest goes down the preview's stdin; the preview lets one page in
# with it, in time, on the built-in page, from its own origin. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pp() { python3 -I scripts/knife.py crates/aibd/src/pairing.rs "$1" "$2" \
  --expect-fail pairing::tests::a_preview_pass_pairs_once_in_time_without_a_key \
  -- cargo test -p aibd --locked --lib pairing::tests::a_preview_pass_pairs_once_in_time_without_a_key; }
pu() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" \
  --expect-fail preview::tests::a_pass_goes_down_the_pipe_as_its_digest_and_lets_one_page_in \
  -- cargo test -p aibd --locked --lib preview::tests::a_pass_goes_down_the_pipe_as_its_digest_and_lets_one_page_in; }
pe() { python3 -I scripts/knife.py "$1" "$2" "$3" \
  --expect-fail the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data \
  -- cargo test -p aibd --locked --test preview the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data; }
# PV1 a pass is used again
pp '        inner.passes.remove(at);' '        let _ = at; // knife'
# PV2 a pass lives as long as the line says, past PASS_TTL
pp '        inner.passes.push_back((digest, now + ttl.min(PASS_TTL)));' '        inner.passes.push_back((digest, now + ttl)); // knife'
# PV3 passes pile up without bound
pp '        while inner.passes.len() >= MAX_PASSES {' '        while false && inner.passes.len() >= MAX_PASSES { // knife'
# PV4 the pass itself goes down the pipe
pu '        sha256_hex(pass.as_bytes()),' '        pass, // knife'
# PV5 the tab opens on / (a user folder page there would see the pass)
pu '        Some(p) => format!("{}__builtin/#pv={p}", url_of(port)),' '        Some(p) => format!("{}#pv={p}", url_of(port)), // knife'
# PV6 a user folder page gets a pass from 打開
pe crates/aibd/src/preview.rs '    let paired_here = words && !page.carried;' '    let paired_here = true || words; // knife'
# PV7 the preview gets no pipe: no pass reaches it
pe crates/aibd/src/preview.rs '        .stdin(Stdio::piped())' '        .stdin(Stdio::null()) // knife'
# PV8 an aibd that is no preview takes a pass
pe crates/aibd/src/pair_api.rs '    if !crate::preview::is_preview() {' '    if false { // knife'
# PV9 a pass is taken from a request with no Origin
pe crates/aibd/src/guard.rs '    if path == PAIR_REDEEM || path == PAIR_NEXT || path == PAIR_PREVIEW {' '    if path == PAIR_REDEEM || path == PAIR_NEXT { // knife'
