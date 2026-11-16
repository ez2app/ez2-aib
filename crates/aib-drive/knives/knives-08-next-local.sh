#!/usr/bin/env bash
# The "aib-drive next_local knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 n-2c next layers 5-6 (ebb36, KNIVES-n2c.md): each mainline / idea rule, broken, turns its test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
nl() { python3 -I scripts/knife.py crates/aib-drive/src/next_local.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test next_local "$3"; }
# NL1
nl '        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {' \
   '        if !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {' \
   a_node_without_approved_is_never_mainline_next
# NL2
nl '        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {' \
   '        if !n.approved || !OPEN_STATUSES.contains(&n.status) {' \
   a_node_already_moving_or_claimed_is_not_handed_out
# NL3
nl '                    Some(false) => open.push(dep.text()),' \
   '                    Some(false) => open.clear(),' \
   an_after_item_not_merged_holds_the_node
# NL4
nl '                    None => cannot.push(format!("{} 合了沒讀不到", dep.text())),' \
   '                    None => cannot.truncate(cannot.len()), // knife' \
   a_merge_state_that_cannot_be_read_is_unknown_not_a_row
# NL5
nl '        Dep::Node(id) => status_of.get(id.as_str()).map(|s| *s == DONE_STATUS),' \
   '        Dep::Node(id) => status_of.get(id.as_str()).map(|_| true),' \
   an_after_node_not_done_holds_the_node
# NL6
nl '                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))' \
   '                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && s.size != "none")' \
   a_review_sized_seat_gets_no_mainline
# NL7
nl '                    b.unblocks.cmp(&a.unblocks)' \
   '                    a.unblocks.cmp(&b.unblocks)' \
   more_unblocked_comes_first_in_layer_5
# NL8
nl '                if s(i, "classification") != "auto"' \
   '                if s(i, "classification") == "x"' \
   ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
# NL9
nl '                    Some((pg, pi, _)) if *pg < g || (*pg == g && !older(i, pi)) => {}' \
   '                    Some((pg, pi, _)) if *pg > g || (*pg == g && !older(i, pi)) => {}' \
   ideas_go_s_to_c_one_per_seat_and_never_a_needs_human_one
# NL10
nl '        if n.status.is_empty() || n.status == DONE_STATUS {' \
   '        if n.status.is_empty() {' \
   the_fleet_blocker_is_the_biggest_group
# NL11
nl '        .max_by(|a, b| a.1.len().cmp(&b.1.len()).then_with(|| b.0.cmp(&a.0)))?;' \
   '        .max_by(|a, b| b.1.len().cmp(&a.1.len()).then_with(|| b.0.cmp(&a.0)))?;' \
   the_fleet_blocker_is_the_biggest_group
# NL12 (a9136 11-15: run() sorts for_andy again after c12gate, so the first sort shows in the fleet blocker's name)
nl '    out.sort_by(|a, b| b.unblocks.cmp(&a.unblocks).then_with(|| a.id.cmp(&b.id)));' \
   '    out.sort_by(|a, b| a.unblocks.cmp(&b.unblocks).then_with(|| a.id.cmp(&b.id)));' \
   the_waiting_andy_group_names_the_for_andy_item_that_unblocks_most
# NL13
nl '        if is_seat(w) && !out.iter().any(|o| o == w) {' \
   '        if is_seat(w) {' \
   owner_seats_takes_whole_five_hex_words_once
# NL14
nl '                .filter(|s| s.role == "dev" && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))' \
   '                .filter(|s| !s.role.is_empty() && s.dispatchable == "yes" && fits(&s.size, ROW_SIZE))' \
   a_busy_or_coordinator_owner_gets_nothing_and_the_next_owner_does
# NL15
nl '                    || !matches!(s(i, "status"), "auto" | "ok")' \
   '                    || s(i, "status") == "needs_human"' \
   an_auto_idea_andy_said_no_to_is_never_dispatched
# NL16
nl '                    || s(i, "human_review") == "no"' \
   '                    || s(i, "human_review") == "x"' \
   an_auto_idea_andy_said_no_to_is_never_dispatched
# NL17
nl '        Some((v, _)) if v.get("complete").and_then(Value::as_bool) != Some(true) => Ledger::Partial,' \
   '        Some((v, _)) if v.get("complete").and_then(Value::as_bool) == Some(false) => Ledger::Partial,' \
   a_ledger_that_is_not_whole_gives_no_layer_6_and_no_for_andy
# NL18
nl '                if node.is_empty() {' \
   '                if node == "-" {' \
   an_idea_is_dispatched_only_under_a_node_still_to_do
# NL19
nl '                    Some(st) if !IDEA_NODE_STATUSES.contains(st) => continue,' \
   '                    Some(st) if st.is_empty() => continue,' \
   an_idea_is_dispatched_only_under_a_node_still_to_do
# NL20
nl '                if inp.claimed.contains(node) || in_five.contains(node) {' \
   '                if inp.claimed.contains(node) || in_five.contains("-") {' \
   an_idea_is_dispatched_only_under_a_node_still_to_do
# NL21
nl '    matches!((at(seat_size), at(row_size)), (Some(a), Some(b)) if a >= b)' \
   '    matches!((at(seat_size), at(row_size)), (Some(_), Some(_)))' \
   rows_are_slice_and_go_only_to_a_seat_that_fits
# NL22
nl '                                && fits(&f.size, ROW_SIZE) => {}' \
   '                                && f.size != "none" => {}' \
   rows_are_slice_and_go_only_to_a_seat_that_fits
# NL23
nl '        .filter(|i| s(i, "classification") == "needs_human" && s(i, "human_review") == "pending")' \
   '        .filter(|i| s(i, "classification") == "needs_human" && s(i, "human_review") != "ok")' \
   for_andy_puts_what_unblocks_most_first
# NL24
nl '        if !n.approved || !OPEN_STATUSES.contains(&n.status) || inp.claimed.contains(n.id) {' \
   '        if !n.approved || n.status == DONE_STATUS || inp.claimed.contains(n.id) {' \
   only_the_open_statuses_are_mainline_next
# NL25
nl '        (Some(want), Some(h)) if h(&scope_text(n.title, &n.after_raw)) == want => ScopeCheck::Same,' \
   '        (Some(want), Some(h)) if h(&scope_text(n.title, &n.after_raw)) == want || true => ScopeCheck::Same,' \
   a_node_whose_scope_changed_after_the_approval_is_unknown
# NL26
nl '        (Some(_), None) => ScopeCheck::Unreadable,' \
   '        (Some(_), None) => if n.id.is_empty() { ScopeCheck::Unreadable } else { ScopeCheck::Same }, // knife' \
   a_scope_that_cannot_be_hashed_is_unknown_not_a_row
# NL27
nl '        ScopeCheck::Unchecked => Ok(true),' \
   '        ScopeCheck::Unchecked => Ok(false),' \
   the_output_serializes_with_the_iface_field_names
# NL28
nl '                    Some(n) if n.approved => match scope_gate(n, inp.scope_hash) {' \
   '                    Some(n) if n.approved && false => match scope_gate(n, inp.scope_hash) {' \
   an_idea_under_a_node_whose_scope_changed_is_unknown
# NL29
nl '        .chain(after.iter().copied())' \
   '        .chain(after.iter().copied().take(0))' \
   scope_text_is_the_title_then_each_after_item
# NL30
nl '    sources.insert("tidy_scan".to_owned(), "unsupported".to_owned());' \
   '    sources.insert("tidy_scan".to_owned(), "ok".to_owned());' \
   the_output_serializes_with_the_iface_field_names
# NL31
nl '    #[serde(skip_serializing_if = "is_false")]' \
   '    #[serde(skip_serializing_if = "is_false", rename = "scope_unchecked_knife")]' \
   the_output_serializes_with_the_iface_field_names
# NL32 (integration, 519d0 trial)
nl '        if devs > 1 {' \
   '        if devs > 99 { // knife' \
   a_busy_or_coordinator_owner_gets_nothing_and_the_next_owner_does
# NL33 (integration, 519d0 trial)
nl '                .any(|o| seat_of(seats, o).is_some_and(|s| s.dispatchable == LOST))' \
   '                .any(|o| seat_of(seats, o).is_some_and(|s| s.dispatchable == UNREAD_UNKNOWN)) // knife' \
   a_node_whose_owner_is_lost_is_skipped_not_unknown
# NL34 (ebb36 f80a69ea M1): an owner off the roster is unknown again and a second owner
nl '        let off = |o: &str| inp.roster_applied && seat_of(seats, o).is_none();' \
   '        let off = |o: &str| inp.roster_applied && seat_of(seats, o).is_some() && o.is_empty(); // knife' \
   an_owner_off_the_roster_is_skipped_and_not_a_second_owner
# NL35 (ebb36 f80a69ea M1): an idea of a seat off the roster is unknown again
nl '                        None if inp.roster_applied => {' \
   '                        None if inp.roster_applied && inp.as_of.is_empty() => { // knife' \
   an_idea_of_a_seat_off_the_roster_is_skipped
# NL36 (ebb36 519d0 D1: only open PRs are the queue)
nl '        .filter(|(_, o, _)| *o)' \
   '        .filter(|(_, _o, _)| true) // knife' \
   the_queue_front_is_the_lowest_open_pr_and_its_seats
# NL37 (ebb36 519d0 D1: the lowest number is the front)
nl '    let first = open.iter().map(|(n, _)| *n).min()?;' \
   '    let first = open.iter().map(|(n, _)| *n).max()?; // knife' \
   the_queue_front_is_the_lowest_open_pr_and_its_seats
# NL38 (ebb36 519d0 D1: no front is unknown)
nl '    if inp.queue_unblock.is_none()' \
   '    if inp.queue_unblock.is_some() // knife' \
   the_queue_group_without_a_front_is_unknown
# NL39 (ebb36 519d0 C5: abandoned closes a claim too)
nl '            Some("done" | "abandoned") => {' \
   '            Some("done") => { // knife' \
   intents_claim_a_node_for_a_day_until_closed
# NL40 (ebb36 519d0 C5: a claim holds under 24 hours)
nl '                if !at.is_some_and(|t| t <= now_ms && now_ms - t < CLAIM_HOLD_MS) {' \
   '                if !at.is_some_and(|t| t <= now_ms && now_ms - t <= CLAIM_HOLD_MS) { // knife' \
   intents_claim_a_node_for_a_day_until_closed
# NL41 (ebb36 519d0 C5: a claim from the future does not hold)
nl '                if !at.is_some_and(|t| t <= now_ms && now_ms - t < CLAIM_HOLD_MS) {' \
   '                if !at.is_some_and(|t| now_ms - t < CLAIM_HOLD_MS) { // knife' \
   intents_claim_a_node_for_a_day_until_closed
# NL42 (ebb36 519d0 C5: only dispatch / open_review claim)
nl '                if !matches!(action, Some("dispatch" | "open_review")) {' \
   '                if action.is_none() { // knife' \
   intents_claim_a_node_for_a_day_until_closed
# NL43 (ebb36 519d0 C5: done / abandoned close the claim, in file order)
nl '                open.remove(key);' \
   '                let _ = key; // knife' \
   intents_claim_a_node_for_a_day_until_closed
# NL44 (ebb36 C13: a node waits from the later of approved.at and updated)
nl '        .max()?;' \
   '        .min()?; // knife' \
   layer_five_and_six_say_how_long_they_waited
# NL45 (ebb36 C13: an x is read as 9, a lower bound)
nl '"9").parse::<i64>()' \
   '"0").parse::<i64>() /* knife */' \
   plan_times_read_as_the_latest_they_can_mean
# NL46 (ebb36 C13: a date alone is 23:59)
nl '        None => (t, "23:59"),' \
   '        None => (t, "00:00"), // knife' \
   plan_times_read_as_the_latest_they_can_mean
# NL47 (ebb36 C13, 043dd M1: up to a day ahead is today's rounding ⇒ now)
nl '    if t - now_ms <= 86_400_000 {' \
   '    if t - now_ms <= 0 { // knife' \
   plan_times_read_as_the_latest_they_can_mean
# NL50 (ebb36 C13, 043dd M1: another future is not known, not last year)
nl '    (m_now == 1 && mo == 12).then(|| at(y_now - 1))' \
   '    (m_now == 1 && mo == 12 || mo > 0).then(|| at(y_now - 1)) /* knife */' \
   plan_times_read_as_the_latest_they_can_mean
# NL51 (ebb36 C13, 043dd M1: December read in January is last year)
nl '    (m_now == 1 && mo == 12).then(|| at(y_now - 1))' \
   '    (m_now == 1 && mo == 12).then(|| at(y_now)) /* knife */' \
   plan_times_read_as_the_latest_they_can_mean
# NL52 (ebb36 519d0 07:41 C7): layer 7 reads redundant items only, not dead ones
nl '                if s(i, "group") != "redundant" || key.is_empty() || inp.claimed.contains(key) {' \
   '                if key.is_empty() || inp.claimed.contains(key) { // knife' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL53 (ebb36 519d0 07:41 C7): an item with no node is skipped as not hung on one
nl '                    hold("沒掛節點,不派".to_owned(), &mut skipped);' \
   '                    hold("knife".to_owned(), &mut skipped);' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL54 (ebb36 519d0 07:41 C7): a scan not complete is partial
nl '            let whole = s(t, "status") == "complete";' \
   '            let whole = true; // knife' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL55 (ebb36 519d0 07:41 C7; S79-c7skip): a done node gives a row, not a said skip
nl '                if !IDEA_NODE_STATUSES.contains(&n.status) {' \
   '                if n.status.is_empty() { // knife' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL55b (S79-c7skip, 519d0 11-16 06:4x) a partial scan's deps section is not said to be lin's
nl '                ",留給 lin 跑"' \
   '                "" // knife' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL56 (ebb36 519d0 07:41 C7): another schema is failed, not read
nl '        Some(t) if s(t, "schema") != TIDY_SCHEMA => {' \
   '        Some(t) if s(t, "schema") == "knife" => { // knife' \
   layer_seven_folds_tidy_scan_duplicates_hung_on_a_node
# NL57 (ebb36 C7b, 519d0 09:17 / 1ced7 / 5d33a L1): a stale tidy-scan.json is still dispatched from
nl '        Some(t) if tidy_stale(inp, t).is_some() => {' \
   '        Some(t) if tidy_stale(inp, t).is_some() && false => { // knife' \
   layer_seven_skips_a_stale_report_and_gives_one_row_a_node
# NL60 (ebb36 C7b, 519d0 09:3x on M1): a report scanned from another head than the main its refresh wrote is read
nl '        _ if head != main => Some(format!(' \
   '        _ if head != main && false => Some(format!( // knife' \
   layer_seven_skips_a_stale_report_and_gives_one_row_a_node
# NL58 (ebb36 C7b, 5d33a N2): every duplicate on one node is a row in the same round
nl '                if taken.contains(node) {' \
   '                if taken.contains(node) && false { // knife' \
   layer_seven_skips_a_stale_report_and_gives_one_row_a_node
# NL59 (ebb36 C7b, 5d33a N3): a node layer 5 already gave a row gets a layer 7 row too
nl '            let mut taken: BTreeSet<String> = rows.iter().filter_map(|r| r.node.clone()).collect();' \
   '            let mut taken: BTreeSet<String> = BTreeSet::new(); // knife' \
   layer_seven_skips_a_stale_report_and_gives_one_row_a_node
# NL61 (ebb36 C7b, 519d0 09:33 IFACE r12): a stale tidy-scan is unknown, not skipped
nl '            tidy_skip(tidy_stale(inp, t).unwrap_or_default(), &mut skipped);' \
   '            unknown.push(Unknown { what: "tidy-scan".to_owned(), why: tidy_stale(inp, t).unwrap_or_default() }); // knife' \
   layer_seven_skips_a_stale_report_and_gives_one_row_a_node
# NL62 (ebb36, 87167 rerun / 519d0 11-15): a layer 5 node no owner can take vanishes, no reason
nl '                    .map(cannot_take)' \
   '                    .filter(|_| false).map(cannot_take) // knife' \
   a_review_sized_seat_gets_no_mainline
# NL63 (ebb36, 87167 rerun / 519d0 11-15): a layer 6 idea whose seat cannot take it has no reason
nl '                                reason: cannot_take(f),' \
   '                                reason: String::new(), // knife' \
   rows_are_slice_and_go_only_to_a_seat_that_fits
# NL64 (ebb36, 87167 rerun / 519d0 11-15): a seat too small is not said as size_too_small
nl '    if f.role == "dev" && f.dispatchable == "yes" && !fits(&f.size, ROW_SIZE) {' \
   '    if false { // knife' \
   a_review_sized_seat_gets_no_mainline
# NL65 (ebb36, 87167 rerun): an auto idea whose grade is not S/A/B/C is not said why
nl '                        reason: format!("等級「{}」不是 S/A/B/C", s(i, "grade")),' \
   '                        reason: String::new(), // knife' \
   an_auto_idea_written_oddly_is_skipped_with_why
# NL66 (ebb36, 87167 rerun): an auto idea whose seat is not a seat code is not said why
nl '                        reason: format!("seat「{seat}」不是席碼"),' \
   '                        reason: String::new(), // knife' \
   an_auto_idea_written_oddly_is_skipped_with_why
# NL67 (ebb36, 87167 L1 on sizeskip): layer 7 does not say why a read seat cannot take it
nl '                        hold(cannot_take(f), &mut skipped);' \
   '                        hold(cannot_take(f).replace("size_too_small", "x"), &mut skipped); // knife' \
   layer_seven_says_why_a_read_seat_cannot_take_it
# NL48 (ebb36 C13: plan times are Taipei)
nl '* 60_000 - TAIPEI_OFFSET_MS;' \
   '* 60_000; // knife' \
   plan_times_read_as_the_latest_they_can_mean
# NL49 (ebb36 C13: an idea waits from its review)
nl '        .get("review")' \
   '        .get("review_knife")' \
   layer_five_and_six_say_how_long_they_waited
# WN1 (56cda S76-when 4) a time-only idea an older aib-idea called needs_human waits in for_andy
nl '        .filter(|i| !when_only(i))' '        .filter(|i| !i.is_null()) // knife' \
   a_time_only_idea_is_a_layer_6_row_not_for_andy
# WN2 a red class beside when lets it through as time-only
nl '    t.contains(&WHEN) && t.iter().all(|t| *t == WHEN || *t == "none")' \
   '    t.contains(&WHEN) && t.iter().any(|t| *t == WHEN || *t == "none") // knife' \
   a_time_only_idea_is_a_layer_6_row_not_for_andy
# WN3 a time-only idea not called auto is held back from layer 6
nl '                    if !when || s(i, "human_review") == "no" {' \
   '                    if when || !when || s(i, "human_review") == "no" { // knife' \
   a_time_only_idea_is_a_layer_6_row_not_for_andy
# WN4 (5d33a L2) a time-only idea Andy said no to is dispatched
nl '                    if !when || s(i, "human_review") == "no" {' \
   '                    if !when { // knife' \
   a_time_only_idea_andy_said_no_to_is_never_dispatched
# WN5 (519d0 L1 a) a time-only idea that cannot be done now (時間:最快) is a row this round
nl '                    .filter(|w| when_only(i) && w.starts_with(WHEN_LATER));' \
   '                    .filter(|w| when_only(i) && w.starts_with("NEVER")); // knife' \
   a_time_only_idea_is_a_layer_6_row_not_for_andy
# CG1 (b433b c12gate): a biometric / human_present node is handed out as a row
nl 'Some((g, why)) => Some(((*g).to_owned(), (*why).to_owned())),' \
   'Some(_) => None,' \
   a_gated_node_is_never_a_row_and_goes_to_andy_with_the_reason
# CG2 (b433b c12gate): an unknown gate word is taken as a known one
nl 'Ok(w) => match GATES.iter().find(|(g, _)| g == w) {' \
   'Ok(w) => match GATES.iter().find(|(g, _)| g == w || !w.is_empty()) {' \
   a_gate_that_is_not_a_known_word_holds_the_node_too_and_no_gate_changes_nothing
# CG3 (b433b c12gate): a gate that is not a string is ignored and the node handed out
nl 'Some(other) => Some(Err(other.to_string().chars().take(40).collect())),' \
   'Some(_) => None,' \
   a_gate_that_is_not_a_known_word_holds_the_node_too_and_no_gate_changes_nothing
# CG4 (b433b c12gate): layer 5 never reads the gate
nl 'if let Some((kind, why)) = gate_hold(n.gate.as_ref()) {' \
   'if let Some((kind, why)) = gate_hold(n.gate.as_ref().filter(|_| false)) {' \
   a_gated_node_is_never_a_row_and_goes_to_andy_with_the_reason
# CG6 (b433b c12gate): the shared check never finds a gate (layers 6 and 7 both send)
nl '.and_then(|n| gate_hold(n.gate.as_ref()))' \
   '.and_then(|n| gate_hold(n.gate.as_ref()).filter(|_| false))' \
   a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason
# CG7 (b433b c12gate): layer 6 does not look at the gate
nl 'node_gate(&ns, node)' \
   'node_gate(&ns, node).filter(|_| false)' \
   an_idea_hung_on_a_gated_node_is_skipped_with_the_reason
# CG8 (b433b c12gate): layer 7 does not look at the gate
nl 'node_gate(&ns, n.id)' \
   'node_gate(&ns, n.id).filter(|_| false)' \
   a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason
