#!/usr/bin/env bash
# aib-knives: os=unix
# H1 knives OO1–OO2 (b4297 11-18, 519d0 09:3x on 0eed7 VERIFY-workspace-0eed7-2436023c): the
# pages aibd serves post from its own origin and pass the guard on every path; any other
# origin is still refused. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
og() { python3 -I scripts/knife.py crates/aibd/src/guard.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test guard "$3"; }
# OO1 aibd's own origin is refused off the redeem path
og '        let own = |o: &str| cfg.own_origins.iter().any(|x| x.eq_ignore_ascii_case(o));' \
   '        let own = |_: &str| false; // knife' \
   aibds_own_origin_passes_everywhere_other_origins_do_not
# OO2 any origin passes
og '            Some(o) if !cfg.allowed_origins.iter().any(|a| a == o) && !own(o) => {' \
   '            Some(o) if false && !own(o) => { // knife' \
   aibds_own_origin_passes_everywhere_other_origins_do_not
