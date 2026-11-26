#!/usr/bin/env bash
# aib-knives: os=unix   # the guide's integration test and its link test are cfg(unix)
# GD1–GD22 (6770f IFACE-guide-6770f-1126 r3–r4b; 3e53b DECISION-onboarding-bounds r3b; f163c PREREAD 1–13):
# GET /api/v1/guide answers booleans and fixed words from what aibd already has, says nothing but "pair next"
# before pairing, writes nothing and starts nothing; act guide_done takes no value, waits for a seat's first
# answer and writes once; nothing else reads the guide. Each, broken, turns its own test red.
# Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
gl() { python3 -I scripts/knife.py crates/aibd/src/guide.rs "$1" "$2" --expect-fail "guide::tests::$3" \
  -- cargo test -p aibd --locked --lib "guide::tests::$3"; }
gi() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail the_guide_reads_the_steps_and_its_record_changes_nothing_else \
  -- cargo test -p aibd --locked --test guide the_guide_reads_the_steps_and_its_record_changes_nothing_else; }
ga() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "guide::tests::no_gate_reads_the_guide" \
  -- cargo test -p aibd --locked --lib guide::tests::no_gate_reads_the_guide; }
gs() { python3 -I scripts/knife.py crates/aib-store/src/sessions.rs "$1" "$2" \
  --expect-fail a_seat_has_spoken_only_once_a_seats_session_wrote_a_line \
  -- cargo test -p aib-store --locked --test sessions a_seat_has_spoken_only_once_a_seats_session_wrote_a_line; }
# GD1 (c / f) before pairing a locked step carries a done
gl '    start.extend(START[2..].iter().map(|id| locked(id)));' '    start.extend(START[2..].iter().map(|id| step(id, Some(false), "locked", None))); // knife' before_pairing_only_health_and_pair_say_anything
# GD2 (c) a page credential not confirmed yet gets the facts
gi crates/aibd/src/guide.rs '        Caller::Page(p) => p.confirmed,' '        Caller::Page(_) => true, // knife'
# GD3 (d) the claude step is fixed done
gl '        Some(f.claude == "ok"),' '        Some(true), // knife' each_step_follows_what_was_read
# GD4 (d) host_add is fixed done
gl '        Some(false) => step("host_add", Some(false), "none", Some("add_host")),' '        Some(false) => step("host_add", Some(true), "none", Some("add_host")), // knife' only_host_add_is_read_in_the_second_list
# GD5 (e) a why carries a word from outside the file
gl '                step("pair", Some(true), "paired", None),' '                step("pair", Some(true), &format!("paired {}", std::env::consts::OS), None), // knife' every_why_is_a_fixed_word
# GD6 (a) a seat open reads the guide's record
ga crates/aibd/src/seats.rs 'fn not_logged_in(provider: &str) -> Response {' 'fn not_logged_in(provider: &str) -> Response { let _ = crate::guide::read_record; // knife'
# GD7 (b / j) the GET writes the record
gi crates/aibd/src/guide.rs '        record: read_record(data),' '        record: { let _ = write_once(data, "2026-01-01T00:00:00Z", "opened"); read_record(data) }, // knife'
# GD8 (g) opening a seat answers otherwise once the record is there
gi crates/aibd/src/seats.rs 'async fn open_detached(st: AppState, headers: HeaderMap, body: Body) -> Response {' 'async fn open_detached(st: AppState, headers: HeaderMap, body: Body) -> Response { if crate::guide::read_record(&crate::registry::data_of(&st)) != crate::guide::Record::None { return bad("knife").into_response(); } // knife'
# GD9 (h) the act takes a value from the page
gi crates/aibd/src/guide.rs '        Some(o) if o.keys().all(|k| k == "type") => {}' '        Some(_) => {} // knife'
# GD10 (i) a second write replaces the first
gl '            r @ Record::Done { .. } => Ok((r, false)),' '            Record::Done { .. } => { let _ = std::fs::write(record_path(data), &text); Ok((read_record(data), false)) } // knife' the_record_is_written_once_and_read_back
# GD11 (k) the start-up check writes when no seat has spoken
gl '    if read_record(data) != Record::None || spoken != Some(true) {' '    if read_record(data) != Record::None || spoken.is_some_and(|_| false) { // knife' the_start_check_writes_only_for_someone_who_used_aib_before
# GD12 (l) a record that cannot be read turns the panel dark
gl '            first_done,' '            false, // knife' an_unreadable_record_keeps_an_old_users_panel_lit
# GD13 (m) host_add reads another list than aibd's pinned one
gl '        .join(crate::trusted_hosts::DIR)' '        .join(".ez2-aib") // knife' host_add_reads_the_pinned_list_only
# GD14 (n / p) the GET path starts a program
gl '        hosts: pinned(data),' '        hosts: { let _ = std::process::Command::new("true"); pinned(data) }, // knife' the_guide_starts_nothing_and_connects_nowhere
# GD15 (o) a next_version step is done
gl '            .map(|id| step(id, Some(false), "next_version", None)),' '            .map(|id| step(id, Some(true), "next_version", None)), // knife' only_host_add_is_read_in_the_second_list
# GD16 (409) the act writes before a seat answered
gi crates/aibd/src/guide.rs '            if spoken(&store) != Some(true) {' '            if spoken(&store) != Some(true) && false { // knife'
# GD17 a guide folder that is a link is written through
gl '        if !m.file_type().is_dir() {' '        if !m.file_type().is_dir() && false { // knife' a_linked_folder_or_record_is_not_a_record_and_not_written_through
# GD18 a guide folder that is a link is read
gl '        Ok(m) if m.file_type().is_dir() => {}' '        Ok(_) => {} // knife' a_linked_folder_or_record_is_not_a_record_and_not_written_through
# GD19 a record that is a link is read
gl '    if !m.file_type().is_file() || m.len() > MAX_RECORD {' '    if m.len() > MAX_RECORD { // knife' a_linked_folder_or_record_is_not_a_record_and_not_written_through
# GD20 a record of another shape is taken
gl '    (is_time(at) && matches!(source, "opened" | "existing")).then(|| Record::Done {' '    ((is_time(at) || !at.is_empty()) && matches!(source, "opened" | "existing")).then(|| Record::Done { // knife' a_record_of_another_shape_is_unreadable
# GD21 (first) a session the preview.2 route started counts as a seat's
gs '            "SELECT EXISTS (SELECT 1 FROM sessions s WHERE s.seat_id IS NOT NULL AND EXISTS (' '            "SELECT EXISTS (SELECT 1 FROM sessions s WHERE (s.seat_id IS NOT NULL OR 1) AND EXISTS ( -- knife'
# GD22 (first) any output event counts as a line
gs '                 SELECT 1 FROM events e WHERE e.aggregate_id = s.session_id || ?1 AND e.type = ?2))",' '                 SELECT 1 FROM events e WHERE e.aggregate_id = s.session_id || ?1 AND (e.type = ?2 OR length(?2) > 0)))", // knife'
echo "guide knives: 22/22 bite"
