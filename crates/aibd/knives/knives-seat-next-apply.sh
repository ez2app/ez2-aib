#!/usr/bin/env bash
# SNA-K (6770f §8 8b-2; DESIGN-merge-6770f-1123 r1 §4a-1 §4a-3 §4b-1): an old uid opens as the kept persona, an archived one does not open, a switch archived since is not taken, a switch is applied once and only the row read is removed, a brain-only switch keeps the persona. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# sna-alias
python3 -I scripts/knife.py crates/aibd/src/seat_next_apply.rs \
  'let u = db.alias_resolve(uid).map_err(|_| "store")?;' \
  'let u = uid.to_string(); let _ = db.alias_resolve(uid);' \
  --expect-fail 'seat_next_apply::tests::mg18_an_old_uid_opens_as_the_kept_one_and_an_archived_one_does_not_open' \
  -- cargo test -p aibd --locked --lib 'seat_next_apply::tests::mg18_an_old_uid_opens_as_the_kept_one_and_an_archived_one_does_not_open'
# sna-arch
python3 -I scripts/knife.py crates/aibd/src/seat_next_apply.rs \
  'Ok(Some(_)) => Err("persona_archived"),' \
  'Ok(Some(_)) => Ok(u),' \
  --expect-fail 'seat_next_apply::tests::mg18_an_old_uid_opens_as_the_kept_one_and_an_archived_one_does_not_open' \
  -- cargo test -p aibd --locked --lib 'seat_next_apply::tests::mg18_an_old_uid_opens_as_the_kept_one_and_an_archived_one_does_not_open'
# sna-since
python3 -I scripts/knife.py crates/aibd/src/seat_next_apply.rs \
  'Err("persona_archived") => (None, true),' \
  'Err("persona_archived") => (Some(p.to_string()), true),' \
  --expect-fail 'seat_next_apply::tests::mg19_a_persona_archived_since_is_not_switched_to_but_the_brain_is' \
  -- cargo test -p aibd --locked --lib 'seat_next_apply::tests::mg19_a_persona_archived_since_is_not_switched_to_but_the_brain_is'
# sna-once
python3 -I scripts/knife.py crates/aibd/src/seat_next_apply.rs \
  'db.seat_next_done(seat_id, &n.batch_id, n.at)' \
  'db.seat_next_done(seat_id, &n.batch_id, n.at + 1)' \
  --expect-fail 'seat_next_apply::tests::mg19_a_revived_seat_takes_its_switch_once_through_the_aliases' \
  -- cargo test -p aibd --locked --lib 'seat_next_apply::tests::mg19_a_revived_seat_takes_its_switch_once_through_the_aliases'
# sna-open
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  'Ok(Ok(u)) => req.persona_uid = u,' \
  'Ok(Ok(u)) => { let _ = u; }' \
  --expect-fail 'mg20_an_old_uid_opens_as_the_kept_persona_and_an_archived_one_is_refused' \
  -- cargo test -p aibd --locked --test seats_open 'mg20_an_old_uid_opens_as_the_kept_persona_and_an_archived_one_is_refused'
# sna-keep
python3 -I scripts/knife.py crates/aib-store/src/seat_meta.rs \
  '"UPDATE seat_meta SET persona_uid = COALESCE(?2, persona_uid), \' \
  '"UPDATE seat_meta SET persona_uid = ?2, \' \
  --expect-fail 'mg17_a_seat_switches_its_persona_and_brain_at_open_and_says_so' \
  -- cargo test -p aib-store --locked --test seat_meta 'mg17_a_seat_switches_its_persona_and_brain_at_open_and_says_so'
