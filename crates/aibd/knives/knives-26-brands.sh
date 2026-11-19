#!/usr/bin/env bash
# N-ws-brands knives BR1–BR6 (b4297 11-18; 519d0 22:2x, SPEC-workspace-multiseat §22): GET
# version lists AIB 2.0's brands only, each with whether it opens on this machine and why not.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
br() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
bw() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test workspace "$3"; }
# BR1 the 1.x names (openrouterjev, typesafe, *bot ...) are listed as brands again
br '    let providers: Vec<Value> = aib_seatopen::AIB2_BRANDS' \
   '    let providers: Vec<Value> = aib_seatopen::PROVIDERS // knife' \
   the_old_set_ups_names_are_never_brands
# BR2 a program the policy names but that is not there still opens
br '    if p.path.is_file() {' \
   '    if p.path.is_file() || true { // knife' \
   version_names_what_opens_and_what_a_page_types_into
# BR3 a brand the policy does not list is told as not installed
br '        .ok_or("not_set_up")?;' \
   '        .ok_or("not_installed")?; // knife' \
   the_old_set_ups_names_are_never_brands
# BR4 a brand that cannot open here is shown as one that can
br '                Err(why) => json!({"id": p, "open": false, "modes": modes, "reason": why}),' \
   '                Err(why) => json!({"id": p, "open": true, "modes": modes, "reason": why}), // knife' \
   version_names_what_opens_and_what_a_page_types_into
# BR5 a brand aibd does not drive is told as only not set up
br '    if !aib_seatopen::PIPE_PROVIDERS.contains(&brand) {' \
   '    if !aib_seatopen::PIPE_PROVIDERS.contains(&brand) && false { // knife' \
   the_old_set_ups_names_are_never_brands
# BR6 the route reads no policy (every brand not set up) instead of this daemon's
bw '    let mut body = version_body(crate::seat_input::dgrs_input_on(), &st.session_policy);' \
   '    let mut body = version_body(crate::seat_input::dgrs_input_on(), &crate::allowlist::SessionPolicy { programs: vec![], ..(*st.session_policy).clone() }); // knife' \
   the_workspace_reads_the_tree_the_host_and_what_aibd_can_do
