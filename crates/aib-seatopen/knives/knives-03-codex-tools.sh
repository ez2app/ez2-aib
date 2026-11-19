#!/bin/bash
# 0eed7 VERIFY-realentry-ea0885a7 H1 knives KC1-KC2 (7ebca 11-20): a safe codex seat keeps the
# host that runs its MCP tool calls (off, aibd's tools are never called) and has code mode off.
set -eo pipefail
kc() { python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "$3"; }
# KC1 the tool host is turned off again (every MCP call fails: code-mode host is disabled)
kc '    "features.code_mode_only=false",' \
   '    "features.code_mode_only=false", "features.code_mode_host=false",' \
   codex_starts_safe_only_with_the_users_set_up_kept_out
# KC2 code mode is left to the user's set-up (a safe seat could write code to run)
kc '    "features.code_mode=false", // no code written to run' \
   '    "features.view_image=false", // knife' \
   codex_starts_safe_only_with_the_users_set_up_kept_out
