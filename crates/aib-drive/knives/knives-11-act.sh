#!/usr/bin/env bash
# The "aib-drive act knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 n-3 act (043dd, KNIVES-n3.md, block as written): each dispatch rule, broken, turns its test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ak() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# AH1 (519d0 11-15 23:55) a would its round did not send (switch off / dry run) counts as sent
ak '                    if !sent_line {' '                    if !sent_line && false { // knife' \
   a_dry_run_would_is_not_a_send
# AH2 a round that sends the action still holds the unsent key (switched on, never sent)
ak '            if !sends(&a) {' '            if !sends(&a) || true { // knife' \
   a_dry_run_would_is_not_a_send
# AH3 a dry run plans its own unsent keys again every round
ak '    let hist = hist.hold_unsent(|a| goes_out(sw, a, sending));' '    let hist = hist.hold_unsent(|_| sending || true); // knife' \
   the_dry_run_writes_would_lines_and_sends_nothing
# AH4 (5d33a N1) --send with the switch off plans its unsent keys again every round
ak '    let hist = hist.hold_unsent(|a| goes_out(sw, a, sending));' '    let hist = hist.hold_unsent(|_| sending); // knife' \
   send_with_every_switch_off_lists_each_dm_once
# AH5 a dry run with the switch on counts as sent (send_round not read)
ak '                    let sent_line = flag("would_send") && flag("send_round");' '                    let sent_line = flag("would_send") && (flag("send_round") || true); // knife' \
   a_dry_run_with_the_switch_on_is_not_a_send
# AH6 the journal says every round sent
ak '        "send_round": sending,' '        "send_round": sending || true, // knife' \
   a_dry_run_with_the_switch_on_is_not_a_send
# WO1 (519d0 11-16 00:32) a wrap-up that cannot go out still takes the seat and holds back its fix request
ak '        if !seat.first.is_empty() && wrap_out {' '        if !seat.first.is_empty() && (wrap_out || true) { // knife' \
   a_wrap_up_that_cannot_go_out_does_not_hold_back_the_fix_request
# BK1 (519d0 11-16 S79-anc 1) a branch row is keyed by its rank, sent again when the rank moves
ak '        (None, None, Some(b)) => format!("branch={b}"),' '        (None, None, Some(_b)) => format!("rank{}", row.rank), // knife' \
   a_branch_row_keeps_its_key_when_its_rank_moves
# WO2 act plans as if a wrap-up always goes out
ak '    let wrap_out = goes_out(sw, "wrap_up", true);' '    let wrap_out = goes_out(sw, "wrap_up", true) || true; // knife' \
   the_dry_run_skips_a_wrap_up_its_switch_holds_back
# A1 a layer-1 row (merge) is taken as a review
ak '        3 => Some("open_review"),' '        1 | 3 => Some("open_review"), // knife' \
   a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch
# A2 a needs_human row is dispatched
ak '        if row.needs != "none" && !busy {' '        if false && !busy { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A3 a lost seat is dispatched
ak '        if seat.alive.v != "yes" {' '        if seat.alive.v != "yes" && false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A4 the coordinator gets development work
ak '        if !infra && (seat.role != "dev" || to == COORDINATOR) {' '        if false { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A5 10 unread or more is dispatched (Andy 11-16 §8「未讀 10 封以上不發」)
ak '            Some(u) if u < UNREAD_MAX => {}' '            Some(u) if u <= UNREAD_MAX => {} // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A6 the seat on deck in the merge queue is dispatched
ak '        let deck_note = match seat.capacity.on_deck {' '        let deck_note = match seat.capacity.on_deck.map(|_| false) { // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A7 a row bigger than the seat's size is dispatched
ak '            (Some(r), Some(s)) if r <= s => {}' '            (Some(_), Some(_)) => {} // knife' \
   a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent
# A8 a done key is done again
ak '        if hist.keys.contains(&k) {' '        if hist.keys.contains(&k) && false { // knife' \
   done_keys_and_the_thirty_minute_gap_hold_across_rounds
# A9 no 30-minute gap per seat
ak '            if now_ms - last < SEAT_GAP_MIN * 60_000 {' '            if now_ms - last < SEAT_GAP_MIN * 60_000 && false { // knife' \
   done_keys_and_the_thirty_minute_gap_hold_across_rounds
# A10 new work before the wrap-up
ak '        if !seat.first.is_empty() && wrap_out {' '        if !seat.first.is_empty() && wrap_out && false { // knife' \
   a_seat_with_wrap_up_left_gets_only_the_wrap_up
# A11 one seat gets several dms in one round
ak '        if let Some(&i) = this_round.get(to) {' '        if let Some(&i) = this_round.get(to).filter(|_| false) { // knife' \
   rows_for_one_seat_in_one_round_go_in_one_dm
# A12 the same dm is nudged again and again
ak '            && !hist.told(&nk)' '            && true // knife' \
   a_queued_dm_is_nudged_once_then_the_sender_is_told_once
# A13 a dm queued under 5 minutes is nudged
ak '        if w.state != "queued" || w.queued_min < NUDGE_AFTER_MIN {' '        if w.state != "queued" { // knife' \
   a_queued_dm_is_nudged_once_then_the_sender_is_told_once
# A14 a lock that has not expired is taken
ak '        if exp > now_ms {' '        if exp > now_ms && false { // knife' \
   the_lock_is_one_holder_until_it_expires
# A15 the old holder removes the new holder's lock
ak '        if fs::read_to_string(&self.path).ok().as_deref() == Some(self.mine.as_str()) {' '        if !self.mine.is_empty() { // knife' \
   the_lock_is_one_holder_until_it_expires
# A16 act runs with neither --dry-run nor --send
ak '    if !f.has("once") || f.has("dry-run") == f.has("send") {' '    if !f.has("once") { // knife' \
   only_the_dry_run_exists_and_a_missing_source_is_rc_3
# A17 an unknown switch in act.json is read
ak '                _ => return Err(format!("act.json: unknown switch {k}")),' '                _ => {} // knife' \
   switches_default_off_and_a_broken_file_is_an_error
# A18 switch takes any --by
ak '    if !seat_ok(by) || why.trim().is_empty() {' '    if seat_ok("") || why.trim().is_empty() { // knife' \
   a_switch_flip_is_recorded_with_who_and_why
# A19 a pause also holds back nudges and wrap-ups
ak '    sw.get(action) && !(sw.paused && new_work)' '    sw.get(action) && !sw.paused && (new_work || !new_work) // knife' \
   a_pause_holds_back_only_new_work
# A20 a pause holds back nothing
ak '    sw.get(action) && !(sw.paused && new_work)' '    sw.get(action) && !(sw.paused && new_work && false) // knife' \
   a_pause_holds_back_only_new_work
# A21 act calls an outside program
ak 'pub fn would_send(sw: Switches, action: &str) -> bool {' 'pub fn would_send(sw: Switches, action: &str) -> bool { let _ = std::process::Command::new("git"); // knife' \
   act_runs_no_outside_program
# A22 an unknown on_deck carries no note
ak '        if let Some(n) = &deck_note {' '        if let Some(n) = deck_note.as_ref().filter(|_| false) { // knife' \
   nulls_from_sig_and_next_read_as_not_known
# A23 a null field fails the whole parse
ak '    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())' '    T::deserialize(d) // knife' \
   nulls_from_sig_and_next_read_as_not_known
# A24 EZ2_AIB_STATE_ROOT is not read
ak '    let root = match nonempty("EZ2_AIB_STATE_ROOT").or_else(|| nonempty("EZ2_AIB_HOME")) {' '    let root = match nonempty("EZ2_AIB_HOME") { // knife' \
   the_state_root_comes_first_and_sig_nulls_still_make_a_round
# A25 the dispatch dm leaves out the head from the next row
ak '        ev.push(format!("頭 {h}"));' '        let _ = h; // knife' \
   a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch
# A26 (A5) an event act wrote itself starts a round anyway
ak '    crate::wake::is_own(&ev, me).then_some(ev.id)' '    (crate::wake::is_own(&ev, me) && false).then_some(ev.id) // knife' \
   an_event_act_wrote_itself_does_not_start_a_round
# A27 (a9136 F26) a review row that already says 審 gets a second one
ak '"open_review" if !row.what' '"open_review" if true || !row.what' \
   a_review_row_that_already_says_review_gets_one_review_word
# A28 (r10 1) a layer-2 fix row is not acted on
ak '    if row.layer == 2 && row.kind == "fix" {' '    if row.layer == 2 && row.kind == "fix" && false { // knife' \
   a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on
# A29 (r11 4) an infrastructure red goes to the author
ak 'Some(MERGE_QUEUE) } else { author }' 'Some(MERGE_QUEUE).filter(|_| false).or(author) } else { author }' \
   an_infra_red_goes_to_the_merge_queue_not_the_author
# A30 the fix line leaves out the colon between what and why
ak '            format!("派工: 修 {target}:{why} → {seat}")' '            format!("派工: 修 {target}{why} → {seat}") // knife' \
   a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on
# A31 a pause does not hold back a fix request
ak '        "dispatch" | "open_review" | "notify_fix" | "unblock_notify"' '        "dispatch" | "open_review" | "unblock_notify" // knife' \
   a_pause_holds_back_only_new_work
# A32 (7b81e A5 N2) --by without --event is taken silently
ak '    if f.has("by") && !f.has("event") {' '    if false { // knife' \
   by_without_an_event_is_a_usage_error
# E4 intents ledger (crates/aib-drive/src/intents.rs)
ik() { python3 -I scripts/knife.py crates/aib-drive/src/intents.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# IN1 killed while sending: sent again on the next run
ik '                Step::Stop(Outcome::NotResent),' '                Step::Go, // knife' \
   killed_while_sending_is_not_sent_again
# IN2 a message already done is sent again
ik '            Some(State::Done) => (None, Step::Stop(Outcome::AlreadyDone)),' '            Some(State::Done) => (None, Step::Go), // knife' \
   a_message_is_sent_once_and_a_second_run_does_nothing
# IN3 a recheck that says no is ignored
ik '    if let Err(no) = recheck() {' '    if let Err(no) = recheck().or_else(|_| Ok::<(), String>(())) { // knife' \
   a_recheck_that_says_no_sends_nothing_and_stays_closed
# IN4 a failed send is written done
ik '    match send() {' '    match send().or_else(|_| Ok::<Map<String, Value>, SendError>(Map::new())) { // knife' \
   a_failed_send_is_abandoned_not_done
# E5 / E9 / E10 sending (act.rs send_round, source.rs StoreDm)
# SN1 a fix request is not sent (notify_fix is sendable since 56cda 11-15, IFACE r10 1)
ak 'pub const SENDABLE: [&str; 6] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[1], WORK[2]];' 'pub const SENDABLE: [&str; 5] = ["nudge", "tell_sender", "wrap_up", WORK[1], WORK[2]]; // knife' \
   review_dispatch_dispatch_and_fix_requests_are_sent
# SN2 a switch that is off still sends
ak 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(sw, &w.action) {' 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(Switches { nudge: true, dispatch: true, ..sw }, &w.action) { // knife' \
   a_switch_that_is_off_sends_nothing
# SN3 the recheck does not look at the dm status
ak '    if queued {' '    if queued || !queued { // knife' \
   a_nudge_whose_dm_was_taken_meanwhile_is_dropped
# SN4 a seat with no cabin is sent to a bare address
ak '.find(|s| s.seat == to && !s.cabin.is_empty())' '.find(|s| s.seat == to) // knife' \
   a_nudge_whose_dm_was_taken_meanwhile_is_dropped
# SN5 several recipients share one key: only the first gets it
ak 'format!("{}@{to}", w.send_key)' 'w.send_key.clone() // knife' \
   telling_the_sender_goes_once_per_recipient
# SN6 (E10) the delivery stage is not kept on the done line
ak 'r.insert("stage".into(), json!(stage));' 'let _ = stage; // knife' \
   the_done_line_keeps_the_delivery_stage_apart_from_read_and_taken
sk() { python3 -I scripts/knife.py crates/aib-drive/src/source.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# SN7 retired (S79-rust slice 3a): no `aib dm` RESULT line to read any more
# SN8 a text starting with a dash goes to aib dm (read as a flag)
sk 'if text.starts_with(' 'if false && text.starts_with(' \
   aib_dm_takes_only_an_address_and_one_text
# 7b81e E4 M1 / L1: a send that did not go out is tried again; a second writer stops a run
# IN5 an intent after a non-final abandoned is a ledger error
ik '("intent", None | Some(State::Retry(_))) => State::Open,' '("intent", None) => State::Open, // knife' \
   a_recheck_that_said_no_is_tried_again_and_sent_once
# IN6 every abandoned is final: one busy moment keeps the work from ever going out
ik 'if o.get("final").and_then(Value::as_bool) == Some(false) {' 'if false { // knife' \
   a_recheck_that_said_no_is_tried_again_and_sent_once
# IN7 must_be lets a second writer through
ik 'fn must_be(m: &BTreeMap<String, State>, key: &str, want: &State) -> Result<(), String> {' \
   'fn must_be(m: &BTreeMap<String, State>, key: &str, want: &State) -> Result<(), String> { if true { return Ok(()); } // knife' \
   a_second_writer_in_between_stops_this_run_before_it_sends
# SN9 --send acts on a next list that is not complete
ak '    if !next.complete {' '    if false { // knife' \
   send_with_an_incomplete_next_sends_nothing
# SN10 a retry line is ignored: the journal still says the key was handled
ak '                Some("retry") => {' '                Some("retry") if false => { // knife' \
   a_retry_line_lets_the_next_round_plan_the_key_again
# 7b81e d3ad28dc M1 / M2 / L1: an unknown send is final; an incomplete round gives keys back; back-off
# IN8 a send whose result is unknown is written non-final (and sent again)
ik '        Err(SendError::Unknown(e)) => write(dir, |m| {' \
   '        Err(SendError::Unknown(e)) => write(dir, |m| { let e = &e; if true { return Ok((Some(line("abandoned", key, now_ms, &why("x", false))), Outcome::SendUnknown(e.clone()))); } // knife' \
   a_send_whose_result_is_unknown_is_never_sent_again
# SN11 / SN12 retired (S79-rust slice 3a): no program is started and no exit code read
# SN13 an incomplete round keeps the nudge key eaten
ak '        .filter(|w| SENDABLE.contains(&w.action.as_str()) && would_send(sw, &w.action))' \
   '        .filter(|w| SENDABLE.contains(&w.action.as_str()) && would_send(sw, &w.action) && false) // knife' \
   an_incomplete_next_does_not_eat_the_nudge
# CP1 (519d0 11-15) an incomplete next holds back the nudge too (it reads only the dm queue)
ak '        DM_ACTIONS.contains(&w.action.as_str())' '        false // knife' \
   an_incomplete_next_does_not_eat_the_nudge
# SN14 no back-off: a retry is planned again at once
ak '                    } else if now_ms.saturating_sub(at) >= RETRY_AFTER_MS {' '                    } else if now_ms.saturating_sub(at) >= 0 { // knife' \
   a_retry_line_lets_the_next_round_plan_the_key_again
# SN15 a key is never given up
ak '                    if *n >= RETRY_MAX {' '                    if *n > RETRY_MAX { // knife' \
   a_key_is_given_up_after_three_tries_that_did_not_go_out
# SN16 a retry from a round that sent nothing counts and backs off
ak 'if o.get("backoff").and_then(Value::as_bool) == Some(false) {' 'if false { // knife' \
   a_key_is_given_up_after_three_tries_that_did_not_go_out
# 7b81e d3ad28dc M1 evidence: the RESULT line is the answer whatever the exit code; sort-queued is queued
# SN17 / SN18 retired (S79-rust slice 3a): no RESULT line; a letter written is queued
# SN19 (5d33a r2 2) while a nudge backs off, the sender is told it was nudged
ak '        } else if hist.backing_off.contains(&nk) {' '        } else if false { // knife' \
   a_retry_line_lets_the_next_round_plan_the_key_again
# E10 (519d0 07:30, ACCEPT E10): sent work followed in four steps; not taken up = marked, never handed on
tk() { python3 -I scripts/knife.py crates/aib-drive/src/track.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# TR1 parked to wait is not taken up
tk '    let accepted = dm.map(|d| set(&d["done_at"]) || status(d) == "done" || set(&d["wait"]));' \
   '    let accepted = dm.map(|d| set(&d["done_at"]) || status(d) == "done"); // knife' \
   the_four_steps_are_kept_apart
# TR2 read is taken as taken up
tk '    let accepted = dm.map(|d| set(&d["done_at"]) || status(d) == "done" || set(&d["wait"]));' \
   '    let accepted = dm.map(|d| set(&d["read_at"]) || status(d) == "done" || set(&d["wait"])); // knife' \
   the_four_steps_are_kept_apart
# TR3 a step that cannot be read is taken as no
tk '    let read = dm.map(|d| set(&d["read_at"]) || matches!(status(d).as_str(), "read" | "done"));' \
   '    let read = Some(dm.is_some_and(|d| set(&d["read_at"]) || matches!(status(d).as_str(), "read" | "done"))); // knife' \
   a_step_that_cannot_be_read_is_not_a_no
# TR4 an item that started before the send counts as started
tk '        !item.is_empty() && since.is_some_and(|s| s >= w.at_ms)' \
   '        !item.is_empty() && since.is_some() // knife' \
   the_four_steps_are_kept_apart
# TR5 marked before two hours
tk '                && now_ms.saturating_sub(w.at_ms) >= ACCEPT_WAIT_MS' \
   '                && now_ms.saturating_sub(w.at_ms) >= 0 // knife' \
   work_not_taken_up_after_two_hours_is_marked_once
# TR6 marked again every round
tk '                && !marked.contains(&w.key)' \
   '                && marked.len() < usize::MAX // knife' \
   work_not_taken_up_after_two_hours_is_marked_once
# TR7 a step that cannot be read is marked
tk '            s.accepted == Some(false)' \
   '            s.accepted != Some(true) // knife' \
   work_not_taken_up_after_two_hours_is_marked_once
# TR8 a nudge is followed as work
tk '                if !TRACKED.contains(&action.as_str())' \
   '                if action.is_empty() // knife' \
   only_work_sent_in_the_last_day_is_followed
# TR9 work sent over a day ago is still followed
tk '                    || now_ms.saturating_sub(at_ms) > FOLLOW_FOR_MS' \
   '                    || now_ms < 0 // knife' \
   only_work_sent_in_the_last_day_is_followed
# FW1 a row sent to one seat is handed to another
ak '            if other != to && now_ms.saturating_sub(*at) < crate::track::FOLLOW_FOR_MS {' \
   '            if other != to && now_ms.saturating_sub(*at) < 0 { // knife' \
   a_row_sent_to_one_seat_is_not_handed_to_another
# FW2 a row stays with its seat for ever
ak '            if other != to && now_ms.saturating_sub(*at) < crate::track::FOLLOW_FOR_MS {' \
   '            if other != to && now_ms.saturating_sub(*at) < i64::MAX { // knife' \
   a_row_sent_to_one_seat_is_not_handed_to_another
# FW3 a work dm that did not go out still holds the row
ak '                            h.went_to.remove(base);' \
   '                            let _ = base; // knife' \
   a_retry_frees_the_row_and_a_mark_is_only_a_mark
# FW4 mark lines are not read (marked again every round)
ak '                        h.marked.insert(k.to_owned());' \
   '                        let _ = k; // knife' \
   a_retry_frees_the_row_and_a_mark_is_only_a_mark
# FW5 the steps are not printed
ak '    for (w, s) in &followed {' \
   '    for (w, s) in followed.iter().take(0) { // knife' \
   work_sent_before_is_followed_step_by_step
# E6 / E7 (519d0 07:30): review dispatch and dispatch really sent, each behind its switch
# SD1 a dispatch is not sent
ak 'pub const SENDABLE: [&str; 6] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[1], WORK[2]];' 'pub const SENDABLE: [&str; 5] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[1]]; // knife' \
   review_dispatch_dispatch_and_fix_requests_are_sent
# SD2 a review dispatch is not sent
ak 'pub const SENDABLE: [&str; 6] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[1], WORK[2]];' 'pub const SENDABLE: [&str; 5] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[2]]; // knife' \
   review_dispatch_dispatch_and_fix_requests_are_sent
# SD3 the dispatch carries no evidence line
ak '        b.push(format!("證據:{}", ev.join(" · ")));' '        let _ = &ev; // knife' \
   a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch
# SD4 a dispatch or review dispatch goes out with its switch off (not what the dry run said)
ak 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(sw, &w.action) {' 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(Switches { dispatch: true, open_review: true, ..sw }, &w.action) { // knife' \
   review_dispatch_and_dispatch_go_out_only_as_the_dry_run_says
# notify_fix sent for real (IFACE r10 1, 56cda 11-15)
# SF1 a fix request goes out with the dispatch switch, its own switch off (not what the dry run said)
ak 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(sw, &w.action) {' 'if !SENDABLE.contains(&w.action.as_str()) || !would_send(Switches { notify_fix: sw.notify_fix || sw.dispatch, ..sw }, &w.action) { // knife' \
   a_fix_request_goes_out_only_with_its_switch_and_as_the_dry_run_says
# act reads the live dm queue itself (519d0 11-15: --dm had no producer, nudge saw nothing)
# QW1 without --dm the live queue is not read
ak '        None => match queue_dir().map(|q| waiting_from_queue(&q, now_ms() / 1000)) {' \
   '        None => match queue_dir().filter(|_| false).map(|q| waiting_from_queue(&q, now_ms() / 1000)) { // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QW2 an item already read is nudged again
ak '            if text("status") != "queued" {' '            if text("status").is_empty() { // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QW3 the sender is taken from the wrong field
ak '        return text("sender_seat").to_owned();' '        return String::new(); // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QW4 the minutes in the queue are miscounted, so nothing is old enough to nudge
ak '                queued_min: (now_s - at).max(0) / 60,' '                queued_min: (now_s - at).max(0) / 6000, // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QT1 an item a test sent is nudged (519d0 11-15 on 5d33a N2)
ak '            if text("origin") == "test" {' '            if text("origin") == "never" { // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QN1 a queued nudge is nudged again (f1fae 11-20: five deep)
ak '            if is_drive_nudge(&v) {' '            if is_drive_nudge(&v) && false { // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# QN2 a seat writing a nudge's words dodges its own nudge (5d33a 0e4bc9e4 N1)
ak '    text("sender_seat").is_empty()' '    (text("sender_seat").is_empty() || true)' \
   only_the_shells_own_nudges_are_not_nudged_again
# QN3 a several-letter nudge is nudged again (f1fae 11-20: q_a0061d81 「你有 2 封」)
ak '        && text("msg").starts_with(NUDGE_MARK)' '        && text("msg").starts_with(NUDGE_HEAD)' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# 5d33a 624d4c96 L1: a queue item with no sender_seat
# QL1 the nudge names an empty sender
ak '            "不明"' '            "" // knife' \
   a_dm_with_no_sender_is_nudged_and_only_the_coordinator_is_told
# QL2 an empty sender is told too (",519d0")
ak '                .filter(|f| !f.is_empty())' '                .filter(|f| !f.is_empty() || true) // knife' \
   a_dm_with_no_sender_is_nudged_and_only_the_coordinator_is_told
# 5d33a 624d4c96 M1: act --auto hands act next's JSON, not its text for people
# QA1 next is asked for text, act cannot read it and the round stops
python3 -I scripts/knife.py crates/aib-drive/src/main.rs \
   '    let next_args = ["--json", "--seats", seats_file.as_str()].map(str::to_owned);' \
   '    let next_args = ["--all", "--seats", seats_file.as_str()].map(str::to_owned); // knife' \
   --expect-fail act_auto_reads_next_as_json_and_nudges_a_live_queued_dm_as_a_dry_run \
   -- cargo test -p aib-drive --locked --test act_auto_bin act_auto_reads_next_as_json_and_nudges_a_live_queued_dm_as_a_dry_run
# 519d0 11-15 (87167 on the live journal): with --send, nudged is the ledger's done only
# LD1 a dry run's would counts as a nudge, so the first send tells the sender instead
ak '            Some(sent) => sent.contains(k),' '            Some(_) => self.keys.contains(k), // knife' \
   a_dry_run_would_is_not_a_nudge_so_the_first_send_is_the_nudge
# LD2 a dm carried in a merged nudge does not count as nudged
ak '                sent.extend(carried.get(k).into_iter().flatten().cloned());' '                let _ = &carried; // knife' \
   two_nudges_to_one_seat_in_one_round_are_one_dm_and_both_count_as_sent
# E8 同批併一封 for dm rounds (7b81e, b433b each got two nudges in one round)
# EM1 two nudges to one seat in one round go out as two dms
ak '            .find(|g| g[0].action == w.action && g[0].to == w.to)' '            .find(|g| g[0].action == w.action && g[0].to == w.to && false) // knife' \
   two_nudges_to_one_seat_in_one_round_are_one_dm_and_both_count_as_sent
# EM2 the merged dm's intent line does not carry its keys
ak '            if w.keys.len() > 1 {' '            if w.keys.len() > 99 { // knife' \
   two_nudges_to_one_seat_in_one_round_are_one_dm_and_both_count_as_sent
# 519d0 11-15 (87167 ACCEPT-LIVE N2): a nudge to a seat that is not there (c477a) is final
# GN1 a seat that is not there is taken for a busy moment (backed off round after round)
ak '    why.starts_with("aib dm stage resolve,")' '    why.starts_with("never") // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# GN4 (5d33a 0a39f00a L1) a seat missing from incomplete seat facts is taken as gone
ak '    why.starts_with("aib dm stage resolve,")' '    why == NO_CABIN || why.starts_with("aib dm stage resolve,") // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# GN2 a gone nudge is planned again
ak '        let gone = hist.gone.contains(&nk);' '        let gone = hist.gone.contains(&nk) && false; // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# GN3 the journal's gone line is not read
ak '                        h.gone.insert(k.to_owned());' '                        let _ = k; // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# NC1 (87167 18:0x) no cabin in a whole seat roster is backed off for ever, nobody told
ak '        let not_there = seat_gone(why) || (roster_complete && why == NO_CABIN);' '        let not_there = seat_gone(why) || (roster_complete && false); // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# NC2 (5d33a 0a39f00a L1) no cabin in seat facts not read whole is taken as gone
ak '        let not_there = seat_gone(why) || (roster_complete && why == NO_CABIN);' '        let not_there = seat_gone(why) || ((roster_complete || true) && why == NO_CABIN); // knife' \
   a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once
# NC3 the round does not pass on whether the seat roster was read whole
ak '    let retry = after_send(&would, &sent, seats.complete, now);' '    let retry = after_send(&would, &sent, false, now); // knife' \
   a_nudge_to_a_seat_missing_from_a_whole_roster_is_gone_and_told_once
# DL1 (87167 18:0x) a dry run counts its own would lines as sent, so it shows less than a send
ak '        Ok(t) => hist.with_ledger(&t.unwrap_or_default()),' '        Ok(t) if f.has("send") => hist.with_ledger(&t.unwrap_or_default()), Ok(_) => hist, // knife' \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# SL1 (87167 18:0x) a sender only in the label (provider-launch) is named 不明
ak "        .strip_prefix('@')" "        .strip_prefix('#') // knife" \
   without_dm_act_reads_the_queued_items_of_the_live_queue
# PA1 (519d0 11-15 20:28) a partial review or fix that was not named is held while next is not complete
ak '            || (PARTIAL_ACTIONS.contains(&w.action.as_str()) && w.pr_checks.is_some())' '            || (PARTIAL_ACTIONS.contains(&w.action.as_str()) && w.pr_checks.is_some() && false) // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA2 a partial dispatch goes out while next is not complete
ak '    if !(row.partial && action_of(row).is_some_and(|a| PARTIAL_ACTIONS.contains(&a))) {' '    if !(row.partial && action_of(row).is_some()) { // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA3 a row of a source that is not partial goes out while next is not complete
ak '    if !(row.partial && action_of(row).is_some_and(|a| PARTIAL_ACTIONS.contains(&a))) {' '    if !(row.partial || action_of(row).is_some_and(|a| PARTIAL_ACTIONS.contains(&a))) { // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA4 a partial bundle that carries a row not checkable goes out
ak '                    _ => None,' '                    (c, _) => c, // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA5 a partial row whose head moved on (or whose PR closed) is sent
ak '        if !(open && at) {' '        if !(open || at) { // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA6 a partial row is sent without reading the PR live
ak '    for (n, head) in w.pr_checks.iter().flatten() {' '    for (n, head) in w.pr_checks.iter().flatten().take(0) { // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# PA7 (5d33a 942c8382 L1) a PR gh could not read counts toward giving the row up
ak '                retry_line(now_ms, k, why, !why.starts_with(PR_UNREAD))' '                retry_line(now_ms, k, why, !why.is_empty()) // knife' \
   a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head
# BZ1 (S79-busy, Andy 11-16 07:0x) a row whose seat is in its turn is skipped as before
ak '        let busy = row.needs == BUSY;' '        let busy = row.needs == BUSY && false; // knife' \
   a_seat_in_its_turn_still_gets_its_row_at_most_once_per_30_minutes
# BZ2 a seat next calls busy but the facts call unknown is sent
ak '        let in_turn = busy && seat.dispatchable == "no";' '        let in_turn = busy; // knife' \
   a_seat_in_its_turn_still_gets_its_row_at_most_once_per_30_minutes
# BZ3 a seat in its turn (size none) is held back by the size check
ak '            _ if in_turn => {}' '            _ if in_turn && false => {} // knife' \
   a_seat_in_its_turn_still_gets_its_row_at_most_once_per_30_minutes
# S79-sender (519d0 11-16 08:46, a9136 design): act sends through the repo shell so the
# 1.x sender check names it 自動推進; a self-reported discipline mark, not a guard
# SDR1 the dm text has no last line saying who turned act on and the intent key
ak '                .chain(std::iter::once(sent_by(dir, &key).as_str()))' '                .chain(std::iter::once("")) // knife' \
   review_dispatch_dispatch_and_fix_requests_are_sent
# SDR2 the seat that turned act on is not marked self-reported
ak '        .map_or_else(|| "?".to_owned(), |b| format!("{b}(自報)"));' '        .map_or_else(|| "?".to_owned(), |b| b); // knife' \
   every_dm_ends_with_the_seat_that_turned_act_on_and_the_intent_key
# SDR3 a by that is not a seat code is printed as written
ak '        .filter(|b| crate::source::is_seat(b))' '        .filter(|b| !b.is_empty()) // knife' \
   every_dm_ends_with_the_seat_that_turned_act_on_and_the_intent_key
# SDR4 / SDR5 retired (S79-rust slice 3a): no shell, drive writes the ledger itself (source.rs StoreDm, knives SDK*)
# SDR7 a queue item from act's own shell (a plain name, no seat) is nudged as 寄件人 不明
ak '        } else if !w.from_name.is_empty() {' '        } else if false && !w.from_name.is_empty() { // knife' \
   a_dm_with_no_sender_is_nudged_and_only_the_coordinator_is_told
# SDR8 the plain name is read from a sender field the queue file does not have (5d33a 352f8776 M1)
ak '        .get("sender_label")' '        .get("sender") // knife' \
   a_dm_with_no_sender_is_nudged_and_only_the_coordinator_is_told
