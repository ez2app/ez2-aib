#!/usr/bin/env bash
# NA1–NA4 (IFACE-netcard r1 §2, choice (a); f163c 08:00): a yes that needs the owner raises the
# window once and finishes only with that card's and that choice's approved ticket.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
na() { python3 -I scripts/knife.py crates/aibd/src/net_card.rs "$1" "$2" --expect-fail "$3" \
  -- cargo test -p aibd --locked --lib "$3"; }
# NA1 the approved ticket does not finish the card
na '                if !approved(t, &ask) {' '                if approved(t, &ask) { // knife' net_card::tests::a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket
# NA2 a second send without the ticket raises another window
na '            (None, _) => Step::Refused("net_card_used"),' '            (None, _) => Step::Ask(Box::new(card.ask(c, by))), // knife' net_card::tests::a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket
# NA3 a ticket is taken for a card no window was asked for
na '            (Some(_), _) => Step::NotApproved,' '            (Some(_), _) => Step::Done(c, c.lasts().map(|lasts| Verified { lasts })), // knife' net_card::tests::a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket
# NA4 the window's parameters do not bind the choice (a min15 ticket would open an hour)
na '        let params = serde_json::json!({"request_id": self.request_id, "hold": c.id()})' '        let params = serde_json::json!({"request_id": self.request_id, "hold": "hour1"}) // knife' net_card::tests::the_window_binds_only_this_card_and_this_choice
# NA5 a finished card counts as waiting
na '            .filter(|(c, s, _)| !matches!(s, State::Finished) && now < c.expires_at)' '            .filter(|(c, _s, _)| now < c.expires_at) // knife' net_card::tests::the_cards_list_counts_and_ends
# NA6 the high-risk card's 只這一次 finishes without the owner
na '        c != Choice::Deny && (self.high || c.needs_owner())' '        c != Choice::Deny && c.needs_owner() // knife' net_card::tests::the_cards_list_counts_and_ends
# NA7 (A6d §18) aibd's own port gets the high-risk card
na '        crate::egress::Local::Aibd => Err("aibd_self"),' '        crate::egress::Local::Aibd => Ok(()), // knife' net_card::tests::a_local_service_gets_the_high_risk_card_and_aibd_or_the_whole_machine_none
# NA8 (f163c REVIEW-sandboxnet18 L1) a port that hands over the whole machine gets the card
na '        crate::egress::Local::Machine => Err("machine_port"),' '        crate::egress::Local::Machine => Ok(()), // knife' net_card::tests::a_local_service_gets_the_high_risk_card_and_aibd_or_the_whole_machine_none
# NA9 the high-risk card does not say who listens
na '            v["listener"] = serde_json::json!(self.listener.as_deref().unwrap_or("查不到"));' '            v["listener"] = serde_json::json!(self.listener.as_deref().unwrap_or("")); // knife' net_card::tests::a_local_service_gets_the_high_risk_card_and_aibd_or_the_whole_machine_none
# NW1–NW5 (ebab8 11-23 09:11 standing rule; f163c REVIEW-netcardact L1 L2): the gates at the
# door, through real requests where a daemon can show them.
nw() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$5" \
  -- cargo test -p aibd --locked "$4" "$5"; }
# NW1 a user folder page lists and answers cards
nw crates/aibd/src/seat_input.rs '            if net_outside {' '            if false && net_outside { // knife' '--test net_card' a_card_is_answered_by_the_built_in_page_only_and_a_yes_only_through_the_owner
# NW2 a window that could not be raised locks the card (L1)
nw crates/aibd/src/net_card_web.rs '            let window_up = r.status() == StatusCode::ACCEPTED;' '            let window_up = r.status() == StatusCode::ACCEPTED || r.status() == StatusCode::FORBIDDEN; // knife' '--test net_card' a_card_is_answered_by_the_built_in_page_only_and_a_yes_only_through_the_owner
# NW3 the answer does not go through aibd's own owner check (L2)
nw crates/aibd/src/net_card_web.rs '                    crate::owner_ok::approved_for(t, ask)' '                    true // knife' '--test net_card_owner' a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
# NW4 the card is marked as asked before any window (L1, the pure side)
nw crates/aibd/src/net_card.rs '                Step::Ask(Box::new(ask))' '                { *state = State::Asked(c); Step::Ask(Box::new(ask)) } // knife' '--lib' net_card::tests::a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket
# NW5 (N1) a ticket for another choice than the window's is looked up
nw crates/aibd/src/net_card.rs '            (Some(_), State::Asked(asked)) if asked != c => Step::NotApproved,' '            (Some(_), State::Asked(asked)) if asked != c && c == Choice::Deny => Step::NotApproved, // knife' '--lib' net_card::tests::a_yes_that_needs_the_owner_finishes_only_with_the_approved_ticket
# NW6 (f163c incremental) 一直允許 is not written to the seat's list after the owner's yes
nw crates/aibd/src/net_card_web.rs '                if yes.lasts() == Lasts::Always {' '                if yes.lasts() == Lasts::Always && false { // knife' '--test net_card_owner' a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
