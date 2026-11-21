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
# MV1-MV5 aibd ctl seat move finds the move from the seat code alone (SPEC-seat-open-oneline-519d0-1121)
mv() { python3 -I scripts/knife.py crates/aibd/src/seat_move.rs "$1" "$2" --expect-fail "seat_move::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_move::tests::$3"; }
# MV1 an empty persona binding is taken
mv '    (!uid.is_empty()).then(|| uid.to_owned())' '    Some(uid.to_owned()) // knife' \
   the_persona_is_the_seat_codes_binding
# MV2 an older handoff is taken
mv '        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)))' '        .min_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0))) // knife' \
   the_newest_handoff_and_pack_of_that_seat_are_taken
# MV2b an answers file is taken as the memory pack
mv '                rest.len() == 4 && rest.bytes().all(|b| b.is_ascii_digit())' '                rest.len() >= 4 // knife' \
   the_newest_handoff_and_pack_of_that_seat_are_taken
# MV3 a relative repo line is taken
mv '        Path::new(rest).is_absolute().then(|| rest.to_owned())' '        Some(rest.to_owned()) // knife' \
   the_repo_is_the_handoffs_repo_line
# MV4 the plan stops saying the old seat stays open
mv '            "6. 印結果:新席碼、席資料夾、第一句已送 / 失敗原因;舊席 {} 不關(關席要真人)",' '            "6. 印結果:新席碼、席資料夾、第一句已送 / 失敗原因;舊席 {} 一併關掉", // knife' \
   a_plan_is_found_from_the_seat_code_alone
# MV5 a flag given twice or with another flag as its value is taken
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
   '            (Some(v), None) if !v.starts_with("--") => got = Some(v),' \
   '            (Some(v), _) => got = Some(v), // knife' \
   --expect-fail a_flag_and_its_value_are_taken_out_once \
   -- cargo test -p aibd --locked --bin aibd a_flag_and_its_value_are_taken_out_once
