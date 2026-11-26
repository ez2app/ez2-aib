#!/usr/bin/env bash
# aib-knives: os=unix
# OA1–OA8 owner check audit (3e53b READ-spinit-owner-audit-3e53b-1126, 11-26: the laptop's session policy was approved
# and written with nothing in the event journal): every owner answer goes to the journal as owner.answer (what, who,
# outcome, seconds, never the params), and an act done with an approved, used ticket as act.done. Each knife keeps the
# code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
J=owner_ok::tests::every_answer_and_every_act_done_with_a_ticket_is_journaled
R=seat_input::tests::only_a_done_act_counts_for_act_done
# OA1 an answer is not journaled
kn owner_ok.rs '        self.journal(OWNER_ANSWER, said);' '        let _ = said;' "$J"
# OA2 an act done is not journaled
kn owner_ok.rs '            ACT_DONE,' '            "act.skip",' "$J"
# OA3 act.done for a ticket approved but never used (any request naming it)
kn owner_ok.rs '                Some(v) if v.rec.used && v.rec.approved => v.rec.operation.clone(),' '                Some(v) if v.rec.approved => v.rec.operation.clone(),' "$J"
# OA4 the whole resource words go in (a long one is not cut)
kn owner_ok.rs '            "resource": slot.rec.resource.chars().take(RESOURCE_CHARS).collect::<String>(),' '            "resource": slot.rec.resource,' "$J"
# OA5 the seconds are not from the ask
kn owner_ok.rs '            "secs": now.saturating_sub(asked_at),' '            "secs": now.saturating_sub(asked_at.min(0)),' "$J"
# OA6 the 202 that raised a window counts as done
kn seat_input.rs '    status.is_success() && status != StatusCode::ACCEPTED' '    status.is_success()' "$R"
# OA7 the act route skips the journal
kn api.rs '        ACT => post(crate::seat_input::act_journaled).fallback(nope),' '        ACT => post(crate::seat_input::act).fallback(nope),' "$R"
# OA8 the owner journal is not wired at start
kn server.rs '    crate::owner_ok::set_journal(journal_to(store.clone(), "owner"));' '    let _ = journal_to(store.clone(), "owner");' "$R"
exit "$fail"
