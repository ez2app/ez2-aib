#!/usr/bin/env bash
# aib-public-leak: fixed well-known public addresses allowed one by one (c2f0b 11-25, ebab8 #891).
# aibd.yml runs every crates/*/knives/*.sh in name order; aib-xtask knife-anchors reads them.
# Run from the repo root. Each knife, put in, must turn its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
lw() { python3 -I scripts/knife.py crates/aib-public-leak/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-public-leak --locked --lib "$3"; }
# LW1 the well-known addresses are not allowed (the list is dropped)
lw '    WELL_KNOWN_IPV4.contains(&o)' '    WELL_KNOWN_IPV4.contains(&[9; 4]) // knife' \
   well_known_public_addresses_pass_one_by_one_their_neighbours_do_not
# LW2 the list is widened to the whole first octet (neighbours pass)
lw '    WELL_KNOWN_IPV4.contains(&o)' '    WELL_KNOWN_IPV4.iter().any(|w| w[0] == a) // knife' \
   well_known_public_addresses_pass_one_by_one_their_neighbours_do_not
