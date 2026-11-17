#!/usr/bin/env bash
# aib-drive drive_send events DS1–DS12 (a9136 11-17, S107-product slice 3, IFACE-s107-product-a9136-1117 r1
# §2.2 §8 item 2): every dm act tries is one drive_send event, work held by its switch is `held`, and a
# later queue status is one drive_send_after, once. Each rule, broken, turns its own test red. Run on macOS
# (the header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust). Run from the
# repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gs() { python3 -I scripts/knife.py crates/aib-drive/src/send_events.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
gt() { python3 -I scripts/knife.py crates/aib-drive/src/track.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# DS1 a dm that is still in line says sent
gs '        Ok(Outcome::Sent) => ("queued", None),' \
   '        Ok(Outcome::Sent) => ("sent", None), // knife' \
   each_dm_tried_is_one_event_and_a_switch_off_is_held
# DS2 a send that may have gone out says not sent
gs '        Ok(Outcome::SendUnknown(_)) => ("unknown", Some("maybe_sent")),' \
   '        Ok(Outcome::SendUnknown(_)) => ("not_sent", Some("maybe_sent")), // knife' \
   each_dm_tried_is_one_event_and_a_switch_off_is_held
# DS3 work held by its switch writes nothing
gs '            || crate::act::would_send(sw, &w.action)' \
   '            || true // knife' \
   each_dm_tried_is_one_event_and_a_switch_off_is_held
# DS4 a card in the dm's text is not found, the seat's own is written
gs '        let found = events::cards_in(text, |w| self.ids.contains(w));' \
   '        let found: Vec<String> = { let _ = text; Vec::new() }; // knife' \
   each_dm_tried_is_one_event_and_a_switch_off_is_held
# DS5 a pause is called a switch that is off
gs '            let paused = sw.paused && sw.get(&w.action);' \
   '            let paused = { let _ = sw.get(&w.action); false }; // knife' \
   a_pause_holds_new_work_and_says_paused
# DS6 the send stage is written again as a later status
gs '        if !real_dm_id(&w.dm_id) || *st == w.stage || seen.contains(&k) || !done.insert(k) {' \
   '        if !real_dm_id(&w.dm_id) || seen.contains(&k) || !done.insert(k) { // knife' \
   a_status_change_is_written_once_with_the_send_events_cards
# DS7 a status written before is written again
gs '                        seen.insert((id.to_owned(), s.to_owned()));' \
   '                        seen.insert((id.to_owned(), String::new())); let _ = s; // knife' \
   a_status_change_is_written_once_with_the_send_events_cards
# DS8 the after event loses the cards its send event carried
gs '        if let Some(c) = cards.get(&w.dm_id).and_then(Value::as_array) {' \
   '        if let Some(c) = cards.get("").and_then(Value::as_array) { // knife' \
   a_status_change_is_written_once_with_the_send_events_cards
# DS9 an unread queue is taken as the dm gone
gt '    let a = queue?.as_array()?;' \
   '    let a = match queue { Some(q) => q.as_array()?, None => return Some("gone".to_owned()) }; // knife' \
   queue_status_reads_the_queue_word_gone_or_unread
# DS10 a dm missing from a queue that was read is not gone
gt '        return Some("gone".to_owned());' \
   '        return None; // knife' \
   queue_status_reads_the_queue_word_gone_or_unread
# DS11 a switch left off writes its held line every round (5d33a 627e18e3 L1)
gs '            !(v["outcome"] == "held" && today.contains(&k))' \
   '            { let _ = (&v, &k, today); true } // knife' \
   a_held_key_is_written_once_a_day
# DS12 aib dm's "-" for no id is taken as a dm id (5d33a 627e18e3 N1)
gs '    !id.is_empty() && id != "-"' \
   '    !id.is_empty() // knife' \
   a_status_change_is_written_once_with_the_send_events_cards
