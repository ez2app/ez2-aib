#!/usr/bin/env bash
# persona_write (c2f0b PWR1-4 + 08de5 shared write: list, provenance, 1.x text, hand edit, refusals, pending, copies)
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# PWR1
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '    if sha_hex(&text) != base_sha.to_ascii_lowercase() {' \
  '    if sha_hex(&text) != base_sha.to_ascii_lowercase() && text.is_empty() {' \
  --expect-fail 'persona_write::tests::a_card_changed_since_the_proposal_is_not_written' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_card_changed_since_the_proposal_is_not_written'
# PWR2
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        .is_some_and(|u| u == uid);' \
  '        .is_none_or(|u| u == uid || u != uid);' \
  --expect-fail 'persona_write::tests::a_file_that_is_not_this_personas_card_is_not_written' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_file_that_is_not_this_personas_card_is_not_written'
# PWR3
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '    if !same_path(&real, path) {' \
  '    if !same_path(&real, path) && real.as_os_str().is_empty() {' \
  --expect-fail 'persona_write::tests::a_link_or_a_second_name_is_not_written' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_link_or_a_second_name_is_not_written'
# PWR4
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        if meta.nlink() != 1 {' \
  '        if meta.nlink() == 0 {' \
  --expect-fail 'persona_write::tests::a_link_or_a_second_name_is_not_written' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_link_or_a_second_name_is_not_written'
# pw-list
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        if std::fs::rename(c, cards_json).is_err() {' \
  '        if std::fs::remove_file(c).is_err() {' \
  --expect-fail 'persona_write::tests::the_three_writes_leave_the_1x_list_current' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::the_three_writes_leave_the_1x_list_current'
# pw-prov
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '    if std::fs::rename(&pt, &prov).is_err() {' \
  '    if std::fs::remove_file(&pt).is_err() {' \
  --expect-fail 'persona_write::tests::the_three_writes_leave_the_1x_list_current' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::the_three_writes_leave_the_1x_list_current'
# pw-fp
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '                out.push_str(": ");' \
  '                out.push_str(":");' \
  --expect-fail 'persona_write::tests::the_compact_text_is_the_1x_text' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::the_compact_text_is_the_1x_text'
# pw-clean
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '    registry_clean(cards_json)?;' \
  '    let _ = registry_clean(cards_json);' \
  --expect-fail 'persona_write::tests::a_hand_edited_list_writes_nothing' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_hand_edited_list_writes_nothing'
# pw-archived
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        Ok(Some(_)) => return Some(WriteError::Archived),' \
  '        Ok(Some(_)) => {}' \
  --expect-fail 'persona_write::tests::an_archived_or_merged_persona_is_refused' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::an_archived_or_merged_persona_is_refused'
# pw-alias
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        Ok(u) if u == uid => None,' \
  '        Ok(u) if u == uid || !u.is_empty() => None,' \
  --expect-fail 'persona_write::tests::an_archived_or_merged_persona_is_refused' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::an_archived_or_merged_persona_is_refused'
# pw-finish
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '        if now.as_deref() == Some(new.as_str()) {' \
  '        if now.as_deref() == Some(new.as_str()) && new.is_empty() {' \
  --expect-fail 'persona_write::tests::a_stopped_write_is_finished_or_dropped_at_start' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::a_stopped_write_is_finished_or_dropped_at_start'
# pw-copies
python3 -I scripts/knife.py crates/aibd/src/persona_write.rs \
  '    let extra = copies.len().saturating_sub(COPIES_KEPT);' \
  '    let extra = copies.len().saturating_sub(COPIES_KEPT + 99);' \
  --expect-fail 'persona_write::tests::only_the_newest_copies_are_kept' \
  -- cargo test -p aibd --locked --lib 'persona_write::tests::only_the_newest_copies_are_kept'
