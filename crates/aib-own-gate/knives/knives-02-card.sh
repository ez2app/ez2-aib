#!/usr/bin/env bash
# S107-loop (a9136 11-17; DECISION-council-board-coop-519d0-1117 §2.5 rule 2): a turn that used a shared
# resource ends only after a card was written; turn-start puts the first pushable card in front of the seat. Each rule, broken, turns its own test red. aibd.yml runs
# every crates/aib-own-gate/knives/*.sh in name order in one step. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gk() { python3 -I scripts/knife.py "crates/aib-own-gate/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-own-gate --locked "$4"; }
# GC1 a missing card never holds
gk lib.rs '    let card_ok = card != Card::Missing;' '    let card_ok = card != Card::Missing || true; // knife' \
   a_missing_card_holds_once_and_only_when_a_resource_was_used
# GC2 a receipt script's fetch / withdraw / clean mode counts as running it
gk lib.rs '            names && started && !w[i + 1..].iter().any(|a| RECEIPT_NOT_RUN.contains(a))' \
   '            names && started // knife' \
   a_receipt_script_counts_only_when_it_is_run
# GC3 another seat's card lets this seat's turn end
gk lib.rs '        s(e, "by") == seat' '        (s(e, "by") == seat || !seat.is_empty()) // knife' \
   a_card_counts_only_for_this_seat_after_the_turn_began
# GC4 no card ledger yet reads as nothing needed (a seat's first resource turn is never held)
gk main.rs '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Card::Missing,' \
   '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Card::NotNeeded, // knife' \
   a_turn_that_used_a_test_machine_is_held_until_a_card_is_written
# GC5 a receipt script handed to grep / cat counts as a run
gk lib.rs '            let started = (i > 0 && matches!(w[i - 1], "bash" | "sh")) || cmd_at == Some(i);' \
   '            let started = cmd_at.is_some() || i > 0; // knife' \
   a_receipt_script_counts_only_when_it_is_run
# GC6 a card written in the turn's first second is missed (ts is whole seconds)
gk lib.rs '                .is_some_and(|t| t.saturating_add(1).saturating_mul(1000) > start_ms)' \
   '                .is_some_and(|t| t.saturating_mul(1000) >= start_ms) // knife' \
   a_card_written_in_the_turns_first_second_counts
# GC7 turn-start names the first card whether or not the seat can push it
gk lib.rs '        .find(|r| r.get("pushable").and_then(Value::as_bool) == Some(true));' \
   '        .find(|r| r.get("pushable").is_some()); // knife' \
   turn_start_feeds_the_first_pushable_card_or_says_stop
# GC8 turn-start keeps the card to itself
gk main.rs '            println!("{l}");' '            let _ = l; // knife' \
   turn_start_prints_the_first_card_from_aib_plan_mine
