#!/usr/bin/env bash
# N-workspace knives TH1–TH9b, PK1–PK2 (a9136 11-18; SPEC-workspace-multiseat-519d0-1118 §1.3 / §7 / §8): a post calls only
# members, seats stop without a person, a thread takes 20 posts a minute, a safe sender never reaches a dgrs seat,
# a post is at most 32 KiB, the same key answers the first post. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tu() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
ti() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads "$3"; }
tp() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_seatpair "$3"; }
# TH1 a seat that is not a member is called
ti '                if !others.contains(&k) {' \
   '                if false && !others.contains(&k) { // knife' \
   a_page_opens_a_thread_on_an_item_and_calls_one_member_in
# TH2 seats keep posting with no person
tu '        && recent.len() >= QUIET_LIMIT' \
   '        && recent.len() >= QUIET_LIMIT && false // knife' \
   seats_stop_after_twenty_posts_without_a_person_and_a_minute_takes_twenty
# TH3 a thread takes any number of posts a minute
tu '    if recent.iter().filter(|(t, _)| now - *t < 60).count() >= PER_MINUTE {' \
   '    if false && recent.iter().filter(|(t, _)| now - *t < 60).count() >= PER_MINUTE { // knife' \
   seats_stop_after_twenty_posts_without_a_person_and_a_minute_takes_twenty
# TH4 a safe sender calls a dgrs seat
tu '    (sender_safe && to_mode == Some("dgrs")).then_some("safe_cannot_send_to_dgrs")' \
   '    (false && sender_safe && to_mode == Some("dgrs")).then_some("safe_cannot_send_to_dgrs") // knife' \
   a_safe_sender_never_reaches_a_dgrs_seat
# TH5 a post over 32 KiB is written
ti '    if text.trim().is_empty() || text.len() > BODY_MAX {' \
   '    if text.trim().is_empty() || (text.len() > BODY_MAX && false) { // knife' \
   a_post_over_thirty_two_kib_is_refused
# TH6 the same key from the same sender writes again (answers 201, not the first post's 200)
ti '            Some(Some(r)) if r.thread_id == t.thread_id && r.body_sha256 == sha => {' \
   '            Some(Some(r)) if false && r.thread_id == t.thread_id && r.body_sha256 == sha => { // knife' \
   a_page_opens_a_thread_on_an_item_and_calls_one_member_in
# TH7 a user folder page reads two seats talking
tp '    if crate::seat_input::held_as_user_page(page).await {' \
   '    if false && crate::seat_input::held_as_user_page(page).await { // knife' \
   the_built_in_page_reads_two_seats_talking_and_a_user_page_does_not
pk() { python3 -I scripts/knife.py crates/aibd/src/person.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
pm() { python3 -I scripts/knife.py crates/aibd/src/contacts_api.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test messages "$3"; }
# PK1 the person's id is made again at every start
pk '        Ok(s) if id_ok(s.trim()) => return Ok(keys_of(s.trim())),' \
   '        Ok(s) if false && id_ok(s.trim()) => return Ok(keys_of(s.trim())), // knife' \
   the_keys_come_from_the_file_and_stay_the_same
# PK2 a page is its credential again (pairing again = someone else)
pm '        crate::guard::Caller::Page(a) => crate::person::key_for(a.user_page).or_else(|| {' \
   '        crate::guard::Caller::Page(a) => crate::person::key_for(a.user_page).filter(|_| false).or_else(|| { // knife' \
   a_page_reads_only_its_own_threads
# TH8 a read never moves the person's mark
ti '            db.set_read_mark(&tid, &me, &p.message_id)?;' \
   '            let _ = &me; // knife' \
   a_page_opens_a_thread_on_an_item_and_calls_one_member_in
# TH9a a tree item's thread uses the members sent at open, not its assignments now
ti '    let Some(node) = t.node.as_deref() else {' \
   '    let Some(node) = t.node.as_deref().filter(|_| false) else { // knife' \
   a_tree_items_thread_follows_its_assignments_now
# TH9b a seat not assigned on the item opens its thread
ti '        if on != Some(true) {' \
   '        if on != Some(true) && false { // knife' \
   a_tree_items_thread_follows_its_assignments_now
