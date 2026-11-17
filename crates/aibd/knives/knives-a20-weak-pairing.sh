#!/usr/bin/env bash
# a20 weak pairing (7b81e; DECISION-council-gaps-519d0-1117, Andy 11-17 23:5x RULES §31; r3b 11-18): the
# terminal y/N pairing is read on every platform, says terminal_y and never claims the person. Each knife breaks
# one rule and must turn its own test red. aib-xtask knife-anchors reads this folder. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test pairing "$4"; }
kl() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# ctl.rs is the binary's module, not the library's: --lib filters its tests to none (W4 MISSED on
# the macOS CI cell of #855; the receipt ran the test by name across targets and hid it)
kb() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "ctl::tests::$3"; }
# W1 (r3b) a platform reads AIBD_PAIRING=terminal-unverified as off again (macOS could not pair)
kl crates/aibd/src/pairing.rs 'Some("terminal-unverified") => ConfirmChannel::TerminalUnverified,' 'Some("terminal-unverified") if cfg!(windows) => ConfirmChannel::TerminalUnverified,' \
   off_by_default_issues_no_code
# W2 a terminal-confirmed page no longer says how it was confirmed
k crates/aibd/src/pairing.rs 'ConfirmChannel::TerminalUnverified => Some("terminal_y"),' 'ConfirmChannel::TerminalUnverified => None,' \
  the_whole_pairing_flow_unlocks_only_after_confirm
# W3 the page claims the person was proven present
k crates/aibd/src/pair_api.rs 'j["verified_person"] = serde_json::Value::Bool(false);' 'j["verified_person"] = serde_json::Value::Bool(true);' \
  the_whole_pairing_flow_unlocks_only_after_confirm
# W4 the off hint stops saying the y/N answer is not the person
kb 'pairing: the y/N answer below only means someone on this account agreed, not you' 'pairing: the y/N answer below means you agreed, not' \
   the_off_hint_names_the_weak_pairing
# W5 (r3b) selfcheck stops saying the weak pairing is not the person
kl crates/aibd/src/selfcheck.rs 'this machine agreed, not the person' 'this machine agreed, the person' \
   the_pairing_row_names_a_weak_pairing_and_never_fails
# W6 a confirmation no longer says it was a weak one (0eed7 L1)
k crates/aibd/src/pair_api.rs '        j["method"] = m.into();' '        let _ = m;' \
  the_whole_pairing_flow_unlocks_only_after_confirm
# W7 the selfcheck pairing row stops saying it read this shell, not the running aibd (5d33a L1)
kl crates/aibd/src/selfcheck.rs '    row.notes.push(("source", "this_shell".to_owned()));' '    // knife: no source note' \
   the_pairing_row_names_a_weak_pairing_and_never_fails
