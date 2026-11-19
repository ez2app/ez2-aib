#!/bin/bash
# N-ws-ask-codex knife RV1 (7ebca 11-20): a codex seat started again gets the talk server as
# codex settings, not claude's --mcp-config.
set -eo pipefail
rv() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_restart "$3"; }
# RV1 a restarted codex seat is given claude's flags (codex refuses them; no tools)
rv '    if meta.provider == "codex" {' \
   '    if meta.provider == "codex" && false {' a_restarted_codex_seat_keeps_the_talk_tools
