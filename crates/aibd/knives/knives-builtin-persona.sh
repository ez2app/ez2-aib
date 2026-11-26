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
