#!/usr/bin/env bash
# Built-in persona (08de5 11-26; ebab8 12:12, FINDING-win-seatopen-owner-gates-ebab8-1126: a new Windows install has no 1.x cards.json, so every seat open was 503): a cards file that is not there lists the one built-in persona; any other read failure stays unreadable (503); the built-in persona is never written. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# PB1 a missing cards file is unreadable again (no persona, no seat)
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
  '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![builtin_card()]),' \
  '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(PersonaSourceError::NotConfigured),' \
  --expect-fail 'personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable' \
  -- cargo test -p aibd --locked --lib 'personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable'
# PB2 every read failure is read as "no cards file" (a broken or locked registry hides behind the built-in)
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
  '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![builtin_card()]),' \
  '            Err(e) if e.kind() != std::io::ErrorKind::OutOfMemory => return Ok(vec![builtin_card()]),' \
  --expect-fail 'personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable' \
  -- cargo test -p aibd --locked --lib 'personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable'
# PB3 (3e53b 9a) a broken cards file is read as the built-in persona
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
  '        parse_cards(&text)' \
  '        parse_cards(&text).or_else(|_| Ok(vec![builtin_card()]))' \
  --expect-fail cards_that_cannot_be_read_are_503_and_the_list_still_answers \
  -- cargo test -p aibd --locked --test seats_open cards_that_cannot_be_read_are_503_and_the_list_still_answers
# PB4 (3e53b 9b) persona_set may change the built-in persona
python3 -I scripts/knife.py crates/aibd/src/persona_edit.rs \
  '    if uid == crate::personas::BUILTIN_UID {' \
  '    if uid == "-" {' \
  --expect-fail the_built_in_persona_is_listed_and_never_changed \
  -- cargo test -p aibd --locked --test persona_edit_acts the_built_in_persona_is_listed_and_never_changed
# PB5 (3e53b 9c) seat_open takes a uid that only looks like the built-in one
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    let Some(card) = cards.into_iter().find(|c| c.uid == req.persona_uid) else {' \
  '    let Some(card) = cards.into_iter().find(|c| c.uid.get(..3) == req.persona_uid.get(..3)) else {' \
  --expect-fail cards_that_cannot_be_read_are_503_and_the_list_still_answers \
  -- cargo test -p aibd --locked --test seats_open cards_that_cannot_be_read_are_503_and_the_list_still_answers
# PB6 (3e53b 9d) a machine with a cards file lists the built-in persona too
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
  '        parse_cards(&text)' \
  '        parse_cards(&text).map(|mut v| { v.push(builtin_card()); v })' \
  --expect-fail personas::tests::a_cards_file_never_lists_the_built_in_persona \
  -- cargo test -p aibd --locked --lib personas::tests::a_cards_file_never_lists_the_built_in_persona
# PB7 (f163c 6) the built-in card's content changes by one character and nothing notices
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
  'const BUILTIN_SHA_OF: &str = "aibd built-in persona aib000: no card text";' \
  'const BUILTIN_SHA_OF: &str = "aibd built-in persona aib000: no card text!";' \
  --expect-fail personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable \
  -- cargo test -p aibd --locked --lib personas::tests::a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable
