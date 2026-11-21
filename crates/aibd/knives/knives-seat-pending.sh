#!/usr/bin/env bash
# PR1-PR8 a web seat reads the pending folder read-only through aibd (PREREAD-seat-pending-read-a9136-1121; 519d0 11-21 11:21)
set -euo pipefail
pr() { python3 -I scripts/knife.py crates/aibd/src/seat_pending.rs "$1" "$2" --expect-fail "seat_pending::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_pending::tests::$3"; }
# PR1 any kind is read
pr '    if !seat_listed(file) {' '    if file.is_empty() { // knife' \
   a_seat_reads_a_listed_kind_only_with_mail_masked
# PR2 a link is read
pr '        Ok(m) if m.file_type().is_symlink() => return Err("pending_refused"),' '        Ok(m) if m.len() == u64::MAX => return Err("pending_refused"), // knife' \
   a_link_in_the_pending_folder_is_not_read
# PR3 mail is not masked
pr '    let text = crate::docs::mask_mail(&String::from_utf8_lossy(&bytes));' '    let text = String::from_utf8_lossy(&bytes).into_owned(); // knife' \
   a_seat_reads_a_listed_kind_only_with_mail_masked
# PR4 the list names any kind
pr '|| !seat_listed(&name) ||' '|| name.is_empty() ||' \
   the_list_gives_listed_names_by_prefix_and_no_more_than_the_cap
# PR5 the list has no cap
pr '    rows.truncate(LIST_MAX);' '    let _ = LIST_MAX; // knife' \
   the_list_gives_listed_names_by_prefix_and_no_more_than_the_cap
# PR6 (5d33a 題 3) no reads-a-minute cap
pr '    if list.len() >= PER_MINUTE {' '    if list.len() > usize::MAX - 1 { // knife' \
   a_safe_seat_never_reads_and_a_seat_reads_a_bounded_number_a_minute
# PR7 (5d33a L1) a safe seat reads the pending folder
pr '    matches!(mode, Some("work" | "dgrs"))' '    matches!(mode, Some("work" | "dgrs" | "safe")) // knife' \
   a_safe_seat_never_reads_and_a_seat_reads_a_bounded_number_a_minute
# PR8 (5d33a 題 1) a RELAY- text is not marked as data
pr '    let text = if file.starts_with("RELAY-") {' '    let text = if file.is_empty() { // knife' \
   a_seat_reads_a_listed_kind_only_with_mail_masked
