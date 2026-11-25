#!/usr/bin/env bash
# aib-knives: os=unix
# S89-talk-2a knives MS1–MS9 (a9136 11-18; DECISION-council-dmq-519d0-1118, MAP-tests-dmq-7b81e-1118 r1): a page
# reads and closes only its own messages, a close names the revision it read, nothing the person wrote reaches
# the journal, a resend types nothing, a row without its event gets one at the next start. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
me() { python3 -I scripts/knife.py crates/aibd/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test messages "$3"; }
# MS1 a page reads a thread it is not an end of
me '    let mine = ends(&thread_id).is_some_and(|(a, b)| a == me || b == me);' \
   '    let mine = true || ends(&thread_id).is_some_and(|(a, b)| a == me || b == me); // knife' \
   a_page_reads_only_its_own_threads
# MS2 a page closes a message to someone else
me '        Ok(Ok(Some(r))) if r.to_key == me => r,' \
   '        Ok(Ok(Some(r))) if true || r.to_key == me => r, // knife' \
   a_page_reads_only_its_own_threads
# MS3 the revision a close names is not compared
me '    match rev_check(Presence::Live { rev: row.rev }, if_rev, false) {' \
   '    match rev_check(Presence::Live { rev: row.rev }, { let _ = if_rev; IfRev::Value(row.rev) }, false) { // knife' \
   only_the_receiver_closes_and_a_stale_rev_is_409
# MS4 a read moves nothing
me '        if !matches!(status_of(&r), Some(Status::Queued | Status::Delivered)) {' \
   '        if true || !matches!(status_of(&r), Some(Status::Queued | Status::Delivered)) { // knife' \
   a_delivered_message_stays_unread_until_the_page_reads_it
# MS5 the text reaches the journal
me '        "sha256": r.body_sha256,' \
   '        "sha256": r.body_sha256, "body": r.body, // knife' \
   a_page_sends_to_a_seat_and_reads_the_thread_with_the_text
# MS6 a resend with the same key types again
me '    if let Some(k) = &idem {' \
   '    if let Some(k) = idem.as_ref().filter(|_| false) { // knife' \
   a_page_sends_to_a_seat_and_reads_the_thread_with_the_text
# MS7 a row left without its event gets none at the next start
me '        store.append(vec![ev])?;' \
   '        let _ = (&ev, store); // knife' \
   a_row_left_without_its_event_gets_one_at_the_next_start
# MS8 the thread does not say where the page read to
me '            o.insert("read_upto".into(), json!(m));' \
   '            let _ = m; // knife' \
   a_delivered_message_stays_unread_until_the_page_reads_it
# MS9 a page's close without evidence is refused (the button gives no reason)
me '    let why = str_field(&body, "why").or(evidence.is_none().then_some(fallback).flatten());' \
   '    let why = str_field(&body, "why").or(fallback.filter(|_| false)); // knife' \
   only_the_receiver_closes_and_a_stale_rev_is_409
