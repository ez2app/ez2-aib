#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-drive unblock knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
uk() { python3 -I scripts/knife.py crates/aib-drive/src/unblock.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UB1 (v-2 a9136 11-16) a row with an unknown code is merged
uk '    if !code_ok(code) {' '    if !code_ok(code) && false { // knife' \
   bad_rows_are_refused_and_named
# UB2 the first source to say an id wins, not the newer row
uk '            let newer = rows.get(&id).is_none_or(|have| {' '            let newer = rows.get(&id).is_none_or(|have| true || { // knife' \
   the_newer_row_wins_when_two_sources_say_the_same_id
# UB3 blocks counts only the rows that wait directly
uk '            todo.extend(direct.get(x).into_iter().flatten());' '            todo.extend(direct.get(x).into_iter().flatten().take(0)); // knife' \
   blocks_counts_everyone_downstream_once_even_in_a_loop
# UB4 a row keeps the source name it wrote itself
uk '            o.insert("source".into(), json!(s.name));' '            o.entry("source").or_insert(json!(s.name)); // knife' \
   a_source_cannot_set_blocks_or_its_source_name
# UB5 an old source file counts as fresh
uk '        let fresh = as_of.is_some_and(|t| now_ms - t <= STALE_MS);' '        let fresh = as_of.is_some_and(|t| now_ms - t <= STALE_MS) || true; // knife' \
   a_stale_source_keeps_its_rows_marked_and_hides_what_went
# UB6 a gone row an unreadable part covers is taken as cleared
uk '            Some(s) if s.hides(code) => ("unknown", format!("{source} 有讀不到的部分蓋住 {code}")),' '            Some(s) if s.hides(code) && false => ("unknown", format!("{source} 有讀不到的部分蓋住 {code}")), // knife' \
   a_gone_row_outside_covers_is_cleared_and_inside_is_unknown
# UB7 an unreadable part without covers hides nothing
uk '                    sn.hidden_all = true;' '                    sn.hidden_all = false; // knife' \
   an_unreadable_part_without_covers_hides_every_code
# UB8 an unknown gone row is dropped instead of checked again
uk '        .filter(|g| text_v(g, "state") == "unknown")' '        .filter(|g| text_v(g, "state") == "unknown" && false) // knife' \
   an_unknown_gone_row_is_cleared_once_its_source_reads_whole_again
# UB9 a seat's blocks adds its rows' downstream up instead of deduplicating
uk '                let n: BTreeSet<&String> = mine' '                let n: Vec<&String> = mine // knife' \
   blocking_counts_a_seats_downstream_once_and_names_its_oldest
# UB10 a seat whose rows are all stale shows a number, not ?
uk '            if !mine.is_empty() && mine.iter().all(|o| o.contains_key("stale")) {' '            if !mine.is_empty() && mine.iter().all(|o| o.contains_key("stale")) && false { // knife' \
   blocking_is_unknown_when_every_row_of_the_seat_is_stale
# UB11 a row whose source file is gone is taken as cleared
uk '            None => ("unknown", format!("來源 {source} 這輪沒有檔案")),' '            None => ("cleared", format!("來源 {source} 這輪沒有檔案")), // knife' \
   a_missing_source_file_makes_its_gone_rows_unknown
# UB12 a blocker's first-seen time is reset every round
uk '            if o.get("since_ms").and_then(Value::as_i64) > Some(first) {' '            if o.get("since_ms").and_then(Value::as_i64) > Some(first) && false { // knife' \
   a_blocker_keeps_the_first_time_it_was_seen
# UB13 (5d33a v-2 L1) a source of another schema counts as read whole
uk '        let ok = fresh && schema_ok;' '        let ok = fresh && (schema_ok || true); // knife' \
   a_source_of_another_schema_cannot_clear_its_old_rows
# UB14 (L1) a refused row's code still counts as seen, its old row cleared
uk '                sn.hidden.insert(text(o, "code").to_owned());' '                sn.hidden.insert(String::new()); // knife' \
   a_refused_row_hides_its_code_but_not_the_others
# UB15 (L1) a row that is not an object hides nothing
uk '                sn.hidden_all = !r.is_object();' '                sn.hidden_all = r.is_object(); // knife' \
   a_refused_row_hides_its_code_but_not_the_others
# UB16 (N3) an act-next.json hours old is read as fresh
uk '    if now_ms - at > STALE_MS {' '    if now_ms - at > STALE_MS + i64::from(i32::MAX) { // knife' \
   an_old_act_next_json_is_unreadable_for_the_drive_source
# UB17 (5d33a 02a6eefd N1) a row with a wrong code hides only that wrong code
uk '                sn.hidden_all |= !code_ok(text(o, "code"));' '                sn.hidden_all |= code_ok(text(o, "code")) && false; // knife' \
   a_row_with_a_wrong_code_hides_every_code_of_its_source
# GL1 (519d0 11-16) unknown gone rows are logged as if cleared
uk '        .filter(|g| text_v(g, "state") == "cleared")' '        .filter(|g| !text_v(g, "state").is_empty()) // knife' \
   only_cleared_gone_rows_are_logged_with_their_round
# SY1 (519d0 11-16) a seat in u13-exclude.txt is still dispatchable
python3 -I scripts/knife.py crates/aib-drive/src/seats.rs '    } else if shared.not_workers.contains(seat) {' \
  '    } else if false && shared.not_workers.contains(seat) { // knife' \
  --expect-fail a_system_seat_is_never_dispatchable \
  -- cargo test -p aib-drive --locked --test seats a_system_seat_is_never_dispatchable
dk() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_drive.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# DK1 (v-2 drive source) a next source in state ok is a U21 row too / none are
dk '                if name.is_empty() || state == "ok" {' '                if name.is_empty() || state == "ok" || true { // knife' \
   every_next_source_that_is_not_ok_is_a_u21_row
# UX3 (a9136 11-16) a source with no why keeps a trailing colon
dk '                        format!("next 的 {name} {state}")' '                        format!("next 的 {name} {state}:") // knife' \
   every_next_source_that_is_not_ok_is_a_u21_row
# DK2 the U10 fingerprint does not follow the items waiting on Andy
dk '                    &digest(&ids),' '                    &digest(&ids[..0]), // knife' \
   waiting_on_andy_is_one_row_whose_fingerprint_follows_the_items
# DK3 a held refresh is stuck at once, not after the takeover age
dk '                if age > LOCK_STUCK_SECS {' '                if age > -1 { // knife' \
   a_held_refresh_is_stuck_only_past_the_takeover_age
# DK4 a lock is stuck whatever refresh.log's last round says
dk '                == Some("held");' '                != Some("held"); // knife' \
   a_held_refresh_is_stuck_only_past_the_takeover_age
# DK5 U4 counts actions whose switch lets them out
dk '                    && !crate::act::would_send(*sw, a)' '                    && (!crate::act::would_send(*sw, a) || true) // knife' \
   a_switch_off_with_recent_woulds_is_a_u4_row
# DK6 U4 reads every would ever written, not the last hour
dk '                if now_ms - at <= WOULD_WINDOW_MS' '                if now_ms - at <= WOULD_WINDOW_MS + i64::from(i32::MAX) // knife' \
   a_switch_off_with_recent_woulds_is_a_u4_row
# DK7 an unreadable act-next.json hides U21 but not U10
dk '        Err(why) => unreadable.push(json!({"name": "next", "why": why, "covers": ["U21", "U10"]})),' '        Err(why) => unreadable.push(json!({"name": "next", "why": why, "covers": ["U21"]})), // knife' \
   a_file_it_cannot_read_covers_only_its_own_codes
nk() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_u13.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# NK1 (U13 nominate) an owner written 待派 / 失聯 counts as someone
nk '    if ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w)) {' '    if ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w)) && false { // knife' \
   empty_placeholder_and_lost_owners_have_nobody_and_live_ones_do
# NK2 an owner seat that is gone counts as someone
nk '        Some(seat) if !alive.contains(seat) => Some(format!("{seat} 不在或失聯")),' '        Some(seat) if !alive.contains(seat) && false => Some(format!("{seat} 不在或失聯")), // knife' \
   empty_placeholder_and_lost_owners_have_nobody_and_live_ones_do
# NK3 the coordinator is nominated
nk '            alive.contains(s(x, "seat")) && !matches!(s(x, "role"), "coordinator" | "system")' '            alive.contains(s(x, "seat")) && !matches!(s(x, "role"), "system") // knife' \
   the_coordinator_and_the_lost_seat_are_never_picked_and_siblings_count
# NK4 the seat that was lost is nominated back
nk '                .filter(|c| Some(c.code) != lost)' '                .filter(|c| Some(c.code) != lost || true) // knife' \
   a_seat_written_off_as_lost_is_not_nominated_back_even_if_it_answers
# NK5 a container node is nominated
nk '        .filter(|n| !parents.contains(s(n, "id")))' '        .filter(|n| !parents.contains(s(n, "id")) || true) // knife' \
   containers_and_non_work_kinds_are_not_nominated
# NK6 every nobody node is nominated each round
nk '        .take(MAX_ROWS)' '        .take(MAX_ROWS + 99) // knife' \
   at_most_five_and_the_one_that_unlocks_most_first
# NK7 (S79 nominate by domain, 043dd 11-16) siblings do not count
nk '        let p = (4 + sib).min(8);' '        let p = (4 + sib).min(8) * 0; // knife' \
   the_coordinator_and_the_lost_seat_are_never_picked_and_siblings_count
# NK10 the parent's owner does not count (a free seat wins)
nk '            add(4, format!("{} owner +4", s(m, "id")));' '            add(0, format!("{} owner +4", s(m, "id"))); // knife' \
   domain_beats_a_free_seat_and_each_signal_counts
# NK11 a split written in the parent's owner does not count
nk '            add(6, format!("{parent} 分工寫了 {suffix} +6"));' '            add(0, format!("{parent} 分工寫了 {suffix} +6")); // knife' \
   a_split_in_the_parents_owner_names_the_seat_for_its_letter
# NK12 with does not count
nk '        add(3, "with +3".into());' '        add(0, "with +3".into()); // knife' \
   domain_beats_a_free_seat_and_each_signal_counts
# NK13 a seat code in the title / evidence does not count
nk '    if seats_in(&text).contains(c.code) && (!digits || text.contains(&format!("@{}", c.code))) {' '    if seats_in(&text).contains(c.code) && (!digits || text.contains(&format!("@{}", c.code))) && false { // knife' \
   domain_beats_a_free_seat_and_each_signal_counts
# NK14 併 Sxx scores like a mere mention
nk '    if let Some((r, _)) = refs.iter().find(|(r, same)| *same && owner_of(r)) {' '    if let Some((r, _)) = refs.iter().find(|(r, same)| *same && owner_of(r) && false) { // knife' \
   domain_beats_a_free_seat_and_each_signal_counts
# NK15 the decision written in the evidence is read as a clue
nk '        .filter(|p| !["定負責席", "重派", "負責席"].iter().any(|w| p.contains(w)))' '        .filter(|p| !["定負責席", "重派", "負責席"].iter().any(|w| p.contains(w)) || true) // knife' \
   the_part_of_the_evidence_that_records_the_decision_is_not_read
# NK16 all zero is not weak (a free seat would be dispatched directly)
nk '                    a.points <= 0' '                    a.points < 0 // knife' \
   zero_points_is_weak_even_with_a_clear_lead
# NK17 review seats take development nodes
nk '                .filter(|c| c.reviewer == review)' '                .filter(|c| c.reviewer == review || !review) // knife' \
   review_seats_take_only_acceptance_nodes_and_then_only_they_do
# NK18 a seat with 10 or more unread is nominated
nk '        .filter(|x| num(x, "/capacity/unread") < crate::act::UNREAD_MAX)' '        .filter(|x| num(x, "/capacity/unread") <= crate::act::UNREAD_MAX) // knife' \
   many_unread_is_never_nominated_and_a_heavy_hand_loses_two
# NK19 a heavy hand loses nothing
nk '    if c.planned >= HEAVY {' '    if c.planned >= HEAVY + 99 { // knife' \
   many_unread_is_never_nominated_and_a_heavy_hand_loses_two
# NK20 the lost owner's co-owner scores nothing
nk '            add(2, format!("跟 {gone} 同節點 +2"));' '            add(0, format!("跟 {gone} 同節點 +2")); // knife' \
   the_lost_owners_co_owner_scores
# NK21 ties go to the busier seat
nk '                    .then(a.1.planned.cmp(&b.1.planned))' '                    .then(b.1.planned.cmp(&a.1.planned)) // knife' \
   review_seats_take_only_acceptance_nodes_and_then_only_they_do
# NK23 the same line scores nothing
nk '            add(LINE_POINTS, format!("同線 {line} {same} 件 +{LINE_POINTS}"));' '            add(0, format!("同線 {line} {same} 件 +{LINE_POINTS}")); // knife' \
   the_same_line_beats_the_siblings
# NK24 an owner out of reach (coordinator / not alive) counts as a miss
nk '        if !reachable.contains(seat) {' '        if !reachable.contains(seat) && false { // knife' \
   owners_out_of_reach_are_listed_not_counted
# NK25 (a9136 M1) with no review seat an acceptance node goes to a development seat
nk '                .filter(|c| c.reviewer == review)' '                .filter(|c| c.reviewer == review || (!c.reviewer && review)) // knife' \
   with_no_review_seat_an_acceptance_node_gets_no_one
# NK26 (a9136 L2) an unknown unread is strong
nk '                        || !c.unread_known' '                        || !c.unread_known && false // knife' \
   a_narrow_lead_or_unknown_unread_is_weak
# NK27 (a9136 L3) a lead of 0 is strong
nk '                        || rest.first().is_some_and(|(b, _)| a.points - b.points < LEAD_MIN)' '                        || rest.first().is_some_and(|(b, _)| a.points - b.points < 0) // knife' \
   a_narrow_lead_or_unknown_unread_is_weak
# NK30 (5d33a N1) a bare 5-digit number counts as a digits-only seat code
nk '    let digits = c.code.bytes().all(|b| b.is_ascii_digit());' '    let digits = false; // knife' \
   a_digits_only_code_counts_only_with_an_at
# NK22 the 11-16 backtest falls below 11 of 14 (siblings counted only while open)
nk '        .filter(|m| !parent.is_empty() && s(m, "parent") == parent && owns(m))' '        .filter(|m| !parent.is_empty() && s(m, "parent") == parent && owns(m) && !DONE.contains(&s(m, "status"))) // knife' \
   the_seventeen_owners_set_on_11_16_backtest
# NK8 nodes are not ordered by how many they unlock
nk '    found.sort_by(|a, b| b.0.cmp(&a.0).then(s(a.1, "id").cmp(s(b.1, "id"))));' '    found.sort_by(|a, b| s(a.1, "id").cmp(s(b.1, "id")).then(b.0.cmp(&a.0))); // knife' \
   at_most_five_and_the_one_that_unlocks_most_first
# NK9 a finished node is nominated
nk '    let open = |n: &Value| !DONE.contains(&s(n, "status"));' '    let open = |n: &Value| !DONE.contains(&s(n, "status")) || true; // knife' \
   empty_placeholder_and_lost_owners_have_nobody_and_live_ones_do
# MK1 (U13 match) the nominations log gets a line every round
nk '            (last.get(s(r, "node")) != Some(&first)).then(|| {' '            (last.get(s(r, "node")) != Some(&first) || true).then(|| { // knife' \
   the_log_gets_a_line_only_when_a_nodes_first_choice_changes
# MK2 every decided node counts as a hit
nk '        if cands.contains(seat) {' '        if cands.contains(seat) || true { // knife' \
   the_report_counts_hits_against_who_the_plan_names_now
# MK3 an owner written 失聯 / 待派 counts as decided
nk '        let placeholder = ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w));' '        let placeholder = ["待定", "待派", "失聯"].iter().any(|w| owner.contains(w)) && false; // knife' \
   the_report_counts_hits_against_who_the_plan_names_now
# MK4 ready before a day of nominating
nk '        && since.is_some_and(|t| now_ms - t >= READY_SPAN_MS);' '        && since.is_some_and(|t| now_ms - t >= 0); // knife' \
   ready_needs_a_day_five_decided_and_eighty_percent
# MK5 a second choice counts as a first-choice hit
nk '        if firsts.get(node).is_some_and(|f| f.contains(seat)) {' '        if firsts.get(node).is_some_and(|f| f.contains(seat)) || true { // knife' \
   the_report_counts_hits_against_who_the_plan_names_now
# MK6 ready whatever the hit rate
nk '        && rate(hit_first) >= READY_RATE' '        && rate(hit_first) >= 0.0 // knife' \
   ready_needs_a_day_five_decided_and_eighty_percent
# MK7 (519d0 N1) ready reads any nomination instead of the first choice
nk '        && rate(hit_first) >= READY_RATE' '        && rate(hit_any) >= READY_RATE // knife' \
   ready_reads_the_first_choice_rate_not_any_nomination
# UX2 (519d0 11-16) a system seat is a candidate again
nk '            alive.contains(s(x, "seat")) && !matches!(s(x, "role"), "coordinator" | "system")' '            alive.contains(s(x, "seat")) && s(x, "role") != "coordinator" // knife' \
   a_system_seat_is_never_a_candidate
ua() { python3 -I scripts/knife.py crates/aib-drive/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test act_auto_bin "$3"; }
# UA1 (v-2 hook) act --auto no longer writes unblock.json each round
ua '    if let Err(e) = aib_drive::unblock::run(&drive, aib_drive::act::now_ms(), true) {' '    if let Err(e) = aib_drive::unblock::run(&drive, aib_drive::act::now_ms(), false) { // knife' \
   act_auto_reads_next_as_json_and_nudges_a_live_queued_dm_as_a_dry_run
# SDR6 retired (S79-rust slice 3a): auto_find_work_dm.sh is gone
