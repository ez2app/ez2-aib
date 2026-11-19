#!/bin/bash
# 頁面開席 with_seat knives SW1-SW5 (7ebca 11-19, 0eed7 STUCK-live-in 15:23, webdev-8h demo;
# 5d33a CHECK-preview-work-seat r2): a new work seat opens in a running work seat's own folder,
# only the folder aibd made for it and not a link; a safe seat cannot name one.
set -eo pipefail
sd() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open_dgrs "$3"; }
ss() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_page_open "$3"; }
# SW1 a folder swapped for a link is followed
sd '    let real_dir = std::fs::symlink_metadata(&own).is_ok_and(|md| md.file_type().is_dir());' \
   '    let real_dir = own.is_dir();' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW2 with_seat is read but the seat still gets its own folder
sd '        cwd,' \
   '        cwd: cwd.filter(|_| false),' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW3 a safe seat may name with_seat
ss '    if mode != "work" {' \
   '    if mode != "work" && false {' \
  a_safe_seat_naming_with_seat_is_refused
# SW4 a dgrs seat's folder is shared
sd '    if m.mode.as_deref() != Some("work") {' \
   '    if m.mode.as_deref() != Some("work") && false {' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# SW5 a folder a terminal chose is shared
sd '    (real_dir && std::path::Path::new(&m.cwd) == own.as_path()).then_some(m.cwd)' \
   '    let _ = own; real_dir.then_some(m.cwd)' \
  a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
