#!/usr/bin/env bash
# OW1–OW8 (7b81e 11-20; a63 Andy 11-20, GAP-repo-register G1): a page's owner ask is a ticket in
# aibd's memory, approved only by the owner's fingerprint, for one act of one page, once, in
# time; one window at a time, a cool-down after a no, six an hour; the switch is exactly 1.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ow() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::the_owners_yes_approves_one_act_for_one_page_once \
  -- cargo test -p aibd --locked --lib owner_ok::tests::the_owners_yes_approves_one_act_for_one_page_once; }
oc() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six; }
# OW1 a yes is used more than once
ow '                v.rec.used = true;' '                v.rec.used = false; // knife'
# OW2 another page uses the yes
ow '            Some(v) if v.by == ask.by && record_matches(&v.rec, ticket, ask, now) => {' \
   '            Some(v) if record_matches(&v.rec, ticket, ask, now) => { // knife'
# OW3 a used ticket still reads approved
ow '            Some(v) if v.by == by && !v.rec.used && now < v.rec.expires_at => v.state.clone(),' \
   '            Some(v) if v.by == by && now < v.rec.expires_at => v.state.clone(), // knife'
# OW4 two windows at once
ow '        if g.busy {' '        if g.busy && false { // knife'
# OW5 no cool-down after a no
oc '            g.cool_until = now + COOL_SECS;' '            g.cool_until = now; // knife'
# OW6 a seventh window in the hour
oc '        if g.opened.len() >= PER_HOUR {' '        if g.opened.len() > PER_HOUR { // knife'
# OW7 any value turns the switch on
ow '    v.as_deref() == Some("1")' '    v.is_some() // knife'
# OW8 a no reads as a yes
oc '            Err(e) => State::Refused(e.to_owned()),' \
   '            Err(_) => State::Approved { at: now, digest: String::new() }, // knife'
