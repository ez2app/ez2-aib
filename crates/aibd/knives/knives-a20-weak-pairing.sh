#!/usr/bin/env bash
# a20 weak pairing (7b81e; DECISION-council-gaps-519d0-1117, Andy 11-17 23:5x RULES §31): the terminal
# y/N pairing is read on Windows only, says terminal_y and never claims the person. Each knife breaks
# one rule and must turn its own test red. aib-xtask knife-anchors reads this folder. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test pairing "$4"; }
kl() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# W1 macOS / Linux read AIBD_PAIRING=terminal-unverified again
kl crates/aibd/src/pairing.rs 'Some("terminal-unverified") if cfg!(windows) =>' 'Some("terminal-unverified") =>' \
   off_by_default_issues_no_code
# W2 a terminal-confirmed page no longer says how it was confirmed
k crates/aibd/src/pairing.rs 'ConfirmChannel::TerminalUnverified => Some("terminal_y"),' 'ConfirmChannel::TerminalUnverified => None,' \
  the_whole_pairing_flow_unlocks_only_after_confirm
# W3 the page claims the person was proven present
k crates/aibd/src/pair_api.rs 'j["verified_person"] = serde_json::Value::Bool(false);' 'j["verified_person"] = serde_json::Value::Bool(true);' \
  the_whole_pairing_flow_unlocks_only_after_confirm
# W4 aib web on a Mac tells the person to turn the weak pairing on
kl crates/aibd/src/ctl.rs 'the terminal y/N pairing is Windows only; on this machine' 'start aibd with AIBD_PAIRING=terminal-unverified; on this machine' \
   the_off_hint_names_the_weak_pairing_on_windows_only
# W5 selfcheck on a Mac stops saying the weak pairing is Windows only
kl crates/aibd/src/selfcheck.rs 'Windows only in this build' 'on' \
   the_pairing_row_names_a_weak_pairing_and_never_fails
