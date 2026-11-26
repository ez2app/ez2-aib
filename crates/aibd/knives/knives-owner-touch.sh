#!/usr/bin/env bash
# aib-knives: os=unix
# Owner-touch knives (7b81e 11-20; a63 Andy 11-20, GAP-repo-register G1; 5d33a PREREAD-ownertouch
# L1 L2 N1): a page's owner ask is a ticket in aibd's memory, approved only by the owner's
# fingerprint, for one act of one page, once, in time; its windows pass pairing's one gate; a
# window whose thread panics still closes; the window names the brand and the remote host.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ow() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::the_owners_yes_approves_one_act_for_one_page_once \
  -- cargo test -p aibd --locked --lib owner_ok::tests::the_owners_yes_approves_one_act_for_one_page_once; }
oc() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six; }
pc() { python3 -I scripts/knife.py crates/aibd/src/pairing.rs "$1" "$2" \
  --expect-fail owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_no_cools_the_window_and_an_hour_holds_six; }
rw() { python3 -I scripts/knife.py crates/aibd/src/repos.rs "$1" "$2" \
  --expect-fail repos::tests::the_window_names_the_remote_host \
  -- cargo test -p aibd --locked --lib repos::tests::the_window_names_the_remote_host; }
# OW1 a yes is used more than once
ow '                v.rec.used = true;' '                v.rec.used = false; // knife'
# OW2 another page uses the yes
ow '            Some(v) if v.by == ask.by && record_matches(&v.rec, ticket, ask, now) => {' \
   '            Some(v) if record_matches(&v.rec, ticket, ask, now) => { // knife'
# OW3 a used ticket still reads approved
ow '            Some(v) if v.by == by && !v.rec.used && now < v.rec.expires_at => v.state.clone(),' \
   '            Some(v) if v.by == by && now < v.rec.expires_at => v.state.clone(), // knife'
# OW4 the owner window skips the gate (two windows at once)
ow '        if let Err(why) = self.gate.open() {' \
   '        if let Err(why) = Ok::<(), &'"'"'static str>(()) { // knife'
# OW5 a no does not cool
oc '            closing.yes = crate::touch::ends_quietly(&got);' '            closing.yes = true; // knife'
# OW6 owner windows are not counted with pairing's
pc '        let owner_counted = true;' '        let owner_counted = false; // knife'
# OW7 any value turns the switch on
ow '    v.as_deref() == Some("1")' '    v.is_some() // knife'
# OW8 a no reads as a yes
oc '            Err(e) => State::Refused(e.to_owned()),' \
   '            Err(_) => State::Approved { at: now, digest: String::new() }, // knife'
# OW9 a window that panicked closes as a yes (no cool-down after it)
oc '        self.gate.closed(self.yes);' '        self.gate.closed(true); // knife'
# OW10 the open window does not name the brand
rw '        resource: format!("{persona_uid} @ {} · {provider}", repo.name),' \
   '        resource: format!("{persona_uid} @ {}", repo.name), // knife'
# OW11 the register window does not name the remote host
rw '    let rest = auth.rsplit_once('"'"'@'"'"').map_or(auth, |(_, h)| h);' \
   '    let rest = auth; // knife'
# OW12 a window that never started closes as a yes (5d33a r2 N1)
oc '            yes: false,' '            yes: true, // knife'
# OW13 an scp-like remote without a user reads as this machine (5d33a r2 L1)
rw '            Some((h, _)) if h.chars().count() > 1 && !h.contains(['"'"'/'"'"', '"'"'\\'"'"']) => remote,' \
   '            Some((_, _)) if false => remote, // knife'
