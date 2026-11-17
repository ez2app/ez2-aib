#!/usr/bin/env bash
# S107-loop (a9136 11-17; DECISION-council-board-coop-519d0-1117 §2.5 rule 2): a turn that used a shared
# resource ends only after a card was written. Each rule, broken, turns its own test red. aibd.yml runs
# every crates/aib-own-gate/knives/*.sh in name order in one step. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gk() { python3 -I scripts/knife.py "crates/aib-own-gate/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-own-gate --locked "$4"; }
# GC1 a missing card never holds
gk lib.rs '    let card_ok = card != Card::Missing;' '    let card_ok = card != Card::Missing || true; // knife' \
   a_missing_card_holds_once_and_only_when_a_resource_was_used
# GC2 fetching a receipt back counts as running one
gk lib.rs '                let fetch_only = mark == "receipt-remote.sh" && cmd.contains("--fetch");' \
   '                let fetch_only = false && cmd.contains("--fetch"); // knife' \
   resources_are_read_from_this_turns_shell_calls_only
# GC3 another seat's card lets this seat's turn end
gk lib.rs '        s(e, "by") == seat' '        !s(e, "by").is_empty() // knife' \
   a_card_counts_only_for_this_seat_after_the_turn_began
# GC4 no card ledger yet reads as nothing needed (a seat's first resource turn is never held)
gk main.rs '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Card::Missing,' \
   '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Card::NotNeeded, // knife' \
   a_turn_that_used_a_test_machine_is_held_until_a_card_is_written
