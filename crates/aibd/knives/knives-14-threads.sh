#!/usr/bin/env bash
# N-workspace knives TH1–TH11c, TC1a–e, PK1–PK2 (a9136 11-18; SPEC-workspace-multiseat-519d0-1118 §1.3 / §7 / §8): a post calls only
# members, seats stop without a person, a thread takes 20 posts a minute, a safe sender never reaches a dgrs seat,
# a post is at most 32 KiB, the same key answers the first post. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tu() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
tg() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_big "$3"; }
ta() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_assign "$3"; }
ti() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads "$3"; }
tv() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_dgrs_view "$3"; }
tk() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_kind "$3"; }
mc() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3"; }
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
tg '    if text.trim().is_empty() || text.len() > BODY_MAX {' \
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
ta '    let Some(node) = t.node.as_deref() else {' \
   '    let Some(node) = t.node.as_deref().filter(|_| false) else { // knife' \
   a_tree_items_thread_follows_its_assignments_now
# TH9b a seat not assigned on the item opens its thread
ta '        if on != Some(true) {' \
   '        if on != Some(true) && false { // knife' \
   a_tree_items_thread_follows_its_assignments_now
# TH10a a seat's thread_list is not its own act (falls to thread_view and is refused)
ta '        Some(OPEN | SEND | VIEW | READ | LIST)' \
   '        Some(OPEN | SEND | VIEW | READ) // knife' \
   a_tree_items_thread_follows_its_assignments_now
# TH10b a seat lists every thread, not only its own
ta '    let member = (!s.is_person()).then(|| s.key.clone());' \
   '    let member = (!s.is_person() && false).then(|| s.key.clone()); // knife' \
   a_tree_items_thread_follows_its_assignments_now
# TH10c aibd mcp's thread_send drops to (the seat cannot call anyone)
mc '    if !args["to"].is_null() {' \
   '    if false && !args["to"].is_null() { // knife' \
   a_seat_posts_in_a_thread_and_calls_another_seat
# TH11a a dgrs seat's thread_view gives every post's text (5d33a 2feffc7b M1)
tu '    switch_on && p.item.get("sender_safe").and_then(Value::as_bool) == Some(false)' \
   '    { let _ = p; switch_on } // knife (p stays used: -D warnings)' \
   a_dgrs_seat_reads_no_safe_senders_text
# TH11b view_as never asks whether the reader is a dgrs seat
tv '                    let body = s.with_body && (!dgrs_reader || dgrs_reads(p, switch_on));' \
   '                    let body = s.with_body && (dgrs_reader || !dgrs_reader || dgrs_reads(p, switch_on)); // knife (switch_on stays used)' \
   a_dgrs_seat_reads_no_safe_seats_text_in_a_thread
# TH11c @all calls the idle members too (SPEC §5 is the seats present; 5d33a 2feffc7b N1)
tu '            .any(|l| k.strip_prefix("seat:") == Some(l.as_str()))' \
   '            .any(|l| { let _ = (k, l); true }) // knife (k stays used)' \
   at_all_calls_only_the_seats_running
# TC1a the kind given at open is not written
tk '    if let Some(k) = category.clone() {' \
   '    if let Some(k) = category.clone().filter(|_| false) { // knife' \
   threads_have_a_kind_and_the_list_counts_each
# TC1b a list asked for one kind gives every thread
tk '                    Kind::Of(k) => db.threads_of_kind(node.as_deref(), Some(k), lim)?,' \
   '                    Kind::Of(_) => db.threads_list(node.as_deref(), None, lim)?, // knife' \
   threads_have_a_kind_and_the_list_counts_each
# TC1c a seat's counts are of every thread, not only its own
tk '                let mine = threads_of(&db, Some(me), node.as_deref(), aib_store::PAGE_MAX)?;' \
   '                let mine = threads_of(&db, Some(me).filter(|_| false), node.as_deref(), aib_store::PAGE_MAX)?; // knife (me stays used)' \
   threads_have_a_kind_and_the_list_counts_each
# TC1d a post carries a doc the page or seat named (CHECKLIST-doc-link D2)
tk '        .any(|k| body.get(*k).is_some())' \
   '        .any(|_k| false) // knife' \
   threads_have_a_kind_and_the_list_counts_each
# TC1e an unknown kind is kept
tk '        Some(k) if aib_store::CATEGORIES.contains(&k) => Some(Kind::Of(k.to_owned())),' \
   '        Some(k) if true || aib_store::CATEGORIES.contains(&k) => Some(Kind::Of(k.to_owned())), // knife (a guard stays, so the next arm is not unreachable: -D warnings)' \
   threads_have_a_kind_and_the_list_counts_each
