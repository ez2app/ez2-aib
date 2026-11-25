#!/usr/bin/env bash
# BN-K (DESIGN-brainnote-08de5-1123 r1 §1; 8b09a preread r2): brain_note writes only from a work seat, new files only, inside the bound brain and away from protected paths, fences the seat's text as data, strips bad characters, and the seat gets the tool in work mode only. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# bn-mode
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    mode == Some("work")' \
  '    mode.is_some()' \
  --expect-fail 'brain_note::tests::only_a_work_seat_writes_a_note' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::only_a_work_seat_writes_a_note'
# bn-excl
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  'OFlag::O_CREAT | OFlag::O_EXCL' \
  'OFlag::O_CREAT' \
  --expect-fail 'brain_note::tests::a_note_is_never_written_over' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_note_is_never_written_over'
# bn-overlap
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '.any(|p| brain.starts_with(p) || p.starts_with(brain))' \
  '.any(|p| brain.starts_with(p))' \
  --expect-fail 'brain_note::tests::a_brain_overlapping_a_protected_folder_is_refused' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_brain_overlapping_a_protected_folder_is_refused'
# bn-fence
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  'longest_backticks(&body).max(2) + 1' \
  'longest_backticks(&body).max(2)' \
  --expect-fail 'brain_note::tests::the_head_is_aibds_and_comes_first' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::the_head_is_aibds_and_comes_first'
# bn-badchar
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '!aib_approve::text::is_bad_char(*c) && !matches!(c' \
  '!matches!(c' \
  --expect-fail 'brain_note::tests::text_and_title_are_cleaned_and_bounded' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::text_and_title_are_cleaned_and_bounded'
# bn-onedrive
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '            || l.starts_with("onedrive")' \
  '            || l.starts_with("xonedrive")' \
  --expect-fail 'brain_note::tests::a_library_in_a_synced_folder_is_known' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_library_in_a_synced_folder_is_known'
# bn-regpath
python3 -I scripts/knife.py crates/aibd/src/registry.rs \
  '    if aib_store::brain_notes::rel_path_ok(p) {' \
  '    if true {' \
  --expect-fail 'brain_note::tests::the_brain_folder_comes_from_the_index' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::the_brain_folder_comes_from_the_index'
# bn-tool
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    if crate::brain_note::mode_may_note(Some(mode)) {' \
  '    if true {' \
  --expect-fail 'seats::tests::a_seat_gets_aibds_one_server_and_its_two_tools_only' \
  -- cargo test -p aibd --locked --lib 'seats::tests::a_seat_gets_aibds_one_server_and_its_two_tools_only'
# bn-trust
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    s.push_str("trust: data\n");' \
  '    s.push_str("trust: instructions\n");' \
  --expect-fail 'brain_note::tests::the_head_is_aibds_and_comes_first' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::the_head_is_aibds_and_comes_first'
# bn-sha
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    sha2::Sha256::digest(bytes)' \
  '    sha2::Sha256::digest(&bytes[..0])' \
  --expect-fail 'brain_note::tests::a_take_back_archives_only_the_note_written' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_take_back_archives_only_the_note_written'
# bn-0700 (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))' \
  '        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o755))' \
  --expect-fail brain_note::tests::the_archive_folder_is_the_owners_alone \
  -- cargo test -p aibd --locked --lib brain_note::tests::the_archive_folder_is_the_owners_alone
# bn-archived (f163c L8)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '            Ok(Some(_)) => return Err("brain_archived"),' \
  '            Ok(Some(_)) => {}' \
  --expect-fail brain_note::tests::an_archived_brain_refuses_new_notes \
  -- cargo test -p aibd --locked --lib brain_note::tests::an_archived_brain_refuses_new_notes
# bn-reparse (ebab8 11-25 19:25: the reparse-point attribute alone is not a link; the Windows
# junction test a_junction_on_the_way_is_not_followed has no knife: a Mac knife cannot run it)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    symlink // not attrs & 0x400 alone' \
  '    symlink || attrs & 0x400 != 0 // not attrs & 0x400 alone' \
  --expect-fail 'brain_note::tests::only_a_link_or_junction_is_refused_not_a_onedrive_reparse_point' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::only_a_link_or_junction_is_refused_not_a_onedrive_reparse_point'
# bn-symlink (3e53b REVIEW-bnreparse L1: a link or a junction is still refused; runs on Mac)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    symlink // not attrs & 0x400 alone' \
  '    symlink && false // not attrs & 0x400 alone' \
  --expect-fail 'brain_note::tests::only_a_link_or_junction_is_refused_not_a_onedrive_reparse_point' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::only_a_link_or_junction_is_refused_not_a_onedrive_reparse_point'
