#!/usr/bin/env bash
# aib-knives: os=unix
# S89 quick-fix knives QK1–QK6 + AR1–AR10 (043dd 11-18, 519d0 04:0x dispatch: S89-live-3 policy file owner / mode,
# S89-state seat list shows whether each seat is running). Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
al() { python3 -I scripts/knife.py crates/aibd/src/allowlist.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "allowlist::tests::$3"; }
ct() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3"; }
# QK1 (S89-live-3) the policy file's owner / mode is never looked at
al '    owner_checked(p)?;' \
   '    let _ = owner_checked(p); // knife' \
   a_group_writable_policy_file_is_refused
# QK2 a file another user owns loads
al '    if file_uid != me {' \
   '    if false && file_uid != me { // knife' \
   policy_owner_and_mode
# QK3 only others-writable is refused, group-writable loads
al '    if mode & 0o022 != 0 {' \
   '    if mode & 0o002 != 0 { // knife' \
   policy_owner_and_mode
# QK4 (S89-state) a running seat is shown as stopped
ct '        Some("running") => "在跑",' \
   '        Some("running") => "已停", // knife' \
   seat_now_reads_the_newest_session_of_the_seat
# QK5 the oldest session of the seat decides instead of the newest
ct '                .find(|x| x.get("seat_id").and_then(Value::as_str) == Some(seat_id))' \
   '                .rfind(|x| x.get("seat_id").and_then(Value::as_str) == Some(seat_id)) // knife' \
   seat_now_reads_the_newest_session_of_the_seat
# QK6 the seat list never asks aibd for its sessions
ct '    let s = c.request("GET", "/api/v1/sessions", None)?;' \
   '    let s = json!({"sessions": []}); // knife' \
   seat_rows_show_the_display_line
# --- S89-state second slice: archive (IFACE-S89-state-archive-043dd-1118 r1) ---
se() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_archive "$3"; }
sl() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats "$3"; }
st() { python3 -I scripts/knife.py crates/aib-store/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test seats "$3"; }
# AR1 a seat with a session is archived anyway
se '        if g.seat_busy(seat_id) {' \
   '        if false && g.seat_busy(seat_id) { // knife' \
   archive_waits_for_every_session_and_an_archived_seat_starts_nothing
# AR1a a start whose slot is reserved but not yet running does not count (5d33a L1)
se '        self.starting.iter().any(|x| x == seat_id)' \
   '        false // knife' \
   archive_waits_for_every_session_and_an_archived_seat_starts_nothing
# AR1b a waiting (pending) session does not count
se '                .any(|q| q.rec.seat_id.as_deref() == Some(seat_id)) // waits for a slot' \
   '                .any(|_q| false) // knife' \
   archive_waits_for_every_session_and_an_archived_seat_starts_nothing
# AR6 a session starts on an archived seat (5d33a L2)
se '        if self.archived.contains(sid) {' \
   '        if false && self.archived.contains(sid) { // knife' \
   archive_waits_for_every_session_and_an_archived_seat_starts_nothing
# AR7 after a restart the archived seats are forgotten
se '                .filter(|x| x.state == aib_store::ARCHIVED)' \
   '                .filter(|_x| false) // knife' \
   archive_waits_for_every_session_and_an_archived_seat_starts_nothing
# AR2 the list still shows archived seats
sl '        .filter(|x| x["state"].as_str() != Some(aib_store::ARCHIVED))' \
   '        .filter(|_x| true) // knife' \
   delete_archives_a_seat_and_the_list_hides_it
# AR3 seat clean archives without --yes
ct '        ["seat", "clean"] => return seat_clean(c, false),' \
   '        ["seat", "clean"] => return seat_clean(c, true), // knife' \
   seat_clean_archives_only_stopped_seats_and_only_with_yes
# AR4 archiving writes no seat.archived event
st '                    event_type: "seat.archived".into(),' \
   '                    event_type: "seat.renamed".into(), // knife' \
   archive_a_seat_once_with_its_event
# AR5 the revision is not bumped
st '            let revision = cur.revision + 1;' \
   '            let revision = cur.revision; // knife' \
   archive_a_seat_once_with_its_event
# --- 5d33a 853ee100 L1 / L2 ---
sx() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# AR8 a seat whose failed start may have left a process running is archived (orphan)
se '            || self.held.iter().any(|x| x == seat_id)' \
   '            || self.held.iter().any(|x| x == seat_id && x.is_empty()) // knife' \
   a_seat_with_a_start_that_may_still_run_is_not_archived
# AR9 an unreadable seat list at start reads as "nothing archived"
sx '            (std::collections::HashSet::new(), true)' \
   '            (std::collections::HashSet::new(), false) // knife' \
   archived_seats_unread_at_start_close_every_seat
# AR10 the seat list was not read, and a seat's session starts anyway
sx '        if self.archived_unknown {' \
   '        if false && self.archived_unknown { // knife' \
   archived_seats_unread_at_start_close_every_seat
