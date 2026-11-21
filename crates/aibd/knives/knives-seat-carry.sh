#!/usr/bin/env bash
# SC1-SC9 a seat moved in gets its handoff and memory pack in <its folder>/.aib/ (PREREAD-seat-carry-a9136-1121; 519d0 11-21 10:46)
set -euo pipefail
sc() { python3 -I scripts/knife.py crates/aibd/src/seat_carry.rs "$1" "$2" --expect-fail "seat_carry::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_carry::tests::$3"; }
# SC1 a name with .. is taken
sc '        && !name.contains("..")' '        && !name.is_empty() // knife' \
   the_page_names_a_handoff_file_not_a_path
# SC2 the kind's prefix is not checked
sc '        && name.starts_with(prefix)' '        && !prefix.is_empty() // knife' \
   the_page_names_a_handoff_file_not_a_path
# SC3 a link or a folder is looked at as a file
sc '    if !m.file_type().is_file() {' '    if m.len() > u64::MAX - 1 { // knife' \
   only_a_plain_small_file_of_the_pending_folder_is_read
# SC4 the open follows a link
sc '        .custom_flags(nix::libc::O_NOFOLLOW)' '        .custom_flags(0) // knife' \
   only_a_plain_small_file_of_the_pending_folder_is_read
# SC5 no size cap
sc '    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX {' '    if bytes.capacity() == usize::MAX { // knife' \
   only_a_plain_small_file_of_the_pending_folder_is_read
# SC6 the copy is writable
sc '        perm.set_mode(0o444);' '        perm.set_mode(0o644); // knife' \
   the_files_go_read_only_into_the_seats_folder_out_of_git
# SC7 .aib is not kept out of git
sc '    exclude(cwd).map_err(|_| "carry_refused")?;' '    let _ = cwd; // knife' \
   the_files_go_read_only_into_the_seats_folder_out_of_git
# SC8 .aib that is a link is written through
sc '        Ok(m) if m.file_type().is_dir() => {}' '        Ok(m) if m.file_type().is_dir() || m.file_type().is_symlink() => {} // knife' \
   the_files_go_read_only_into_the_seats_folder_out_of_git
# SC9 a memory pack without its handoff is taken
sc '            Err(("mempack", "a memory pack goes with a handoff"))' '            Ok(Vec::new()) // knife' \
   a_memory_pack_comes_with_its_handoff
