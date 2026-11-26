#!/usr/bin/env bash
# aib-knives: os=unix
# OA1–OA8 owner check audit (3e53b READ-spinit-owner-audit-3e53b-1126, 11-26: the laptop's session policy was approved
# and written with nothing in the event journal; REVIEW-pack2 L1: the use is written where the ticket is used up, not
# after any act that names it): every owner answer goes to the journal as owner.answer (what, who, outcome, seconds,
# never the params), and an approved ticket used up by its own act as owner.used, once. Each knife keeps the code
# warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
J=owner_ok::tests::every_answer_and_every_used_ticket_is_journaled_once
# OA1 an answer is not journaled
kn owner_ok.rs '        self.journal(OWNER_ANSWER, said);' '        let _ = said;' "$J"
# OA2 a used ticket is not journaled
kn owner_ok.rs '        self.journal(OWNER_USED, used);' '        let _ = used;' "$J"
# OA3 a ticket from the same page is used (and journaled) for an act it was not approved for
kn owner_ok.rs '            Some(v) if v.by == ask.by && record_matches(&v.rec, ticket, ask, now) => {' '            Some(v) if v.by == ask.by && v.rec.approved && now < now + 1 => {' "$J"
# OA4 the whole resource words go in (a long one is not cut)
kn owner_ok.rs '            "resource": slot.rec.resource.chars().take(RESOURCE_CHARS).collect::<String>(),' '            "resource": slot.rec.resource,' "$J"
# OA5 the seconds are not from the ask
kn owner_ok.rs '            "secs": now.saturating_sub(asked_at),' '            "secs": now.saturating_sub(asked_at.min(0)),' "$J"
# OA6 the owner journal is not wired at start
kn server.rs '    crate::owner_ok::set_journal(journal_to(store.clone(), "owner"));' '    let _ = journal_to(store.clone(), "owner");' "$J"
# OA7 a used ticket stays usable (it could be used again, and journaled again)
kn owner_ok.rs '                let used = serde_json::json!({' '                v.rec.used = false; let used = serde_json::json!({' "$J"
# OA8 a list ticket taken is not journaled (3e53b REVIEW-pack2 r1 N1)
kn owner_ok.rs '        self.journal(OWNER_USED, list_used);' '        let _ = list_used;' owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
exit "$fail"
