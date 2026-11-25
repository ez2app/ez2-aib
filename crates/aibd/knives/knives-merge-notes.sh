#!/usr/bin/env bash
# MN-K (6770f §8 8c; DESIGN-merge-6770f-1123 r1 §4b-2 §5; ebab8 11-25 12:18 派工): a brain merge
# copies notes whole or not at all, refuses cloud placeholders and too much, never copies into
# itself, and undo takes back only what is unchanged. Every knife keeps the names it touches
# in use (a knife that leaves a name unused is a compile error, not a bite).
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# mn-cloud
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '            if cloud {' \
  '            if cloud && false {' \
  --expect-fail 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing'
# mn-count
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '            if kind == Entry::Placeholder {' \
  '            if kind == Entry::Placeholder && false {' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# mn-limit
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '                if len > MAX_FILE_BYTES || out.len() >= MAX_FILES || bytes > MAX_BYTES {' \
  '                if (len > MAX_FILE_BYTES || out.len() >= MAX_FILES || bytes > MAX_BYTES) && false {' \
  --expect-fail 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing'
# mn-whole
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '    if made.is_err() {' \
  '    if made.is_err() && false {' \
  --expect-fail 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::mg13_a_placeholder_or_too_much_refuses_the_whole_copy_and_leaves_nothing'
# mn-nested
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '    if s.starts_with(&k) || k.starts_with(&s) {' \
  '    if (s.starts_with(&k) || k.starts_with(&s)) && false {' \
  --expect-fail 'merge_notes::tests::mg13_a_kept_brain_inside_the_merged_one_is_refused' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::mg13_a_kept_brain_inside_the_merged_one_is_refused'
# mn-unchanged
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '        if same && move_file(&p, &undo_dir.join(&c.folder).join(rel)) {' \
  '        if (same || !same) && move_file(&p, &undo_dir.join(&c.folder).join(rel)) {' \
  --expect-fail 'merge_notes::tests::mg14_undo_takes_back_what_is_unchanged_and_lists_what_was_changed' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::mg14_undo_takes_back_what_is_unchanged_and_lists_what_was_changed'
# mn-winattr
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  'const FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: u32 = 0x0040_0000;' \
  'const FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: u32 = 0x0080_0000;' \
  --expect-fail 'merge_notes::tests::windows_cloud_only_attributes_are_placeholders_and_a_pinned_file_is_not' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::windows_cloud_only_attributes_are_placeholders_and_a_pinned_file_is_not'
# mn-run
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if !groups.iter().any(|g| g["notes"] == "copy") {' \
  '    if !groups.iter().any(|g| g["notes"] == "copy") || true {' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# mn-undo
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '                if let Some(t) = undo_notes(&data, batch_id, s.seq) {' \
  '                if let Some(t) = undo_notes(&data, batch_id, s.seq).filter(|_| false) {' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# mn-claim
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(false),' \
  '            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(true),' \
  --expect-fail 'merge_notes::tests::an_empty_folder_already_there_is_never_renamed_over' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::an_empty_folder_already_there_is_never_renamed_over'
# mn-order (f163c REVIEW-macts ddb16d16 N1: a placeholder before a link)
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '    if (name.starts_with('\''.'\'') && name.ends_with(".icloud")) || attrs_say_cloud_only(attrs) {' \
  '    if ((name.starts_with('\''.'\'') && name.ends_with(".icloud")) || attrs_say_cloud_only(attrs)) && !symlink {' \
  --expect-fail 'merge_notes::tests::a_placeholder_is_asked_before_a_link_on_every_platform' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::a_placeholder_is_asked_before_a_link_on_every_platform'
# mn-pinned (f163c REVIEW-macts a2c64199 L1: a reparse point alone is not a link)
python3 -I scripts/knife.py crates/aibd/src/merge_notes.rs \
  '    if symlink {' \
  '    if symlink || attrs & 0x400 != 0 {' \
  --expect-fail 'merge_notes::tests::a_placeholder_is_asked_before_a_link_on_every_platform' \
  -- cargo test -p aibd --locked --lib 'merge_notes::tests::a_placeholder_is_asked_before_a_link_on_every_platform'
