#!/bin/bash
# aib-knives: os=unix
# Restart names its reason (7ebca 11-20; 519d0 L1 after 0eed7 VERIFY-codexcfg-65be8029): a restart
# refused for a codex reason says which (Skip::Refused), only the code aibd puts last.
set -eo pipefail
lk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# RW1 every refusal stays start_failed
lk crates/aibd/src/revive.rs '        .map_or(Skip::Start, Skip::Refused)' \
   '        .map_or(Skip::Start, |_c| Skip::Start)' \
   a_refused_restart_names_its_reason
# RW2 a code anywhere in the message counts (a folder path can name one; 5d33a L1)
lk crates/aibd/src/revive.rs '        .find(|c| m.ends_with(&format!("({c})")))' \
   '        .find(|c| m.contains(&format!("({c})")))' \
   a_refused_restart_names_its_reason
