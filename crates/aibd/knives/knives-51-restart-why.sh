#!/bin/bash
# Restart names its reason (7ebca 11-20; 519d0 L1 after 0eed7 VERIFY-codexcfg-65be8029): a restart
# refused for a codex reason says which (Skip::Refused), the first code aibd wrote wins.
set -eo pipefail
lk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# RW1 every refusal stays start_failed
lk crates/aibd/src/revive.rs '        .map_or(Skip::Start, |(_, c)| Skip::Refused(c))' \
   '        .map_or(Skip::Start, |(_, _c)| Skip::Start)' \
   a_refused_restart_names_its_reason
# RW2 a code in text after aibd's own wins
lk crates/aibd/src/revive.rs '        .min()' '        .max()' \
   a_refused_restart_names_its_reason
