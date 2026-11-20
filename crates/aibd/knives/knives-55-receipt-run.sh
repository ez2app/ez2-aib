#!/usr/bin/env bash
# N-webonly 第 4 件 knives RQ1–RQ18 (7ebca 11-20): a page asks aibd for a receipt of one head
# (receipt_request / receipt_read / receipt_withdraw); the step that leaves the machine is a
# trait whose shipped runner runs nothing. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
rq() { python3 -I scripts/knife.py crates/aibd/src/receipt_run.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "receipt_run::tests::$3"; }
ri() { python3 -I scripts/knife.py crates/aibd/src/receipt_run.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test receipt_run "$3"; }
# RQ1 a head that is not 40 hex (a branch, an option) is taken
rq '    if !is_hex(head, 40) {' \
   '    if !is_hex(head, 40) && head.is_empty() { // knife' \
   a_request_names_a_head_and_a_kind_and_nothing_else
# RQ2 a full receipt may name tests, a narrow one none
rq '    if narrow == tests.is_empty() || tests.len() > MAX_TESTS {' \
   '    if tests.len() > MAX_TESTS { // knife' \
   a_request_names_a_head_and_a_kind_and_nothing_else
# RQ3 a seat has a third run under way
rq '        >= MAX_IN_FLIGHT' \
   '        > MAX_IN_FLIGHT // knife' \
   a_seat_has_two_runs_under_way_at_most
# RQ4 the same head asked again reuses the run id
rq '    if !taken(&base) {' \
   '    if !taken(&base) || !base.is_empty() { // knife' \
   a_run_id_is_the_seat_and_the_head_and_new_each_time
# RQ5 a run id with more after it is taken
rq '    shape && again && parts.next().is_none()' \
   '    shape && again // knife' \
   a_run_id_is_the_seat_and_the_head_and_new_each_time
# RQ6 a run that waits for the owner reads as running
rq '        Start::NeedsOwner { host, knives_sent } => (WAITING_OWNER, host, knives_sent),' \
   '        Start::NeedsOwner { host, knives_sent } => (RUNNING, host, knives_sent), // knife' \
   a_runners_answer_makes_the_run_or_refuses_it
# RQ7 a runner's own words are passed on to the page
rq '        .unwrap_or("refused")' \
   '        .unwrap_or("no_host") // knife' \
   a_runners_answer_makes_the_run_or_refuses_it
# RQ8 the machine is kept as the runner named it (an address, a path)
rq '        host: Some(nickname(&host)),' \
   '        host: Some(host), // knife' \
   a_runners_answer_makes_the_run_or_refuses_it
# RQ9 the runner aibd ships starts a run
rq '        Start::Refused("no_host")' \
   '        Start::Started { host: String::new(), knives_sent: 0 } // knife' \
   a_runners_answer_makes_the_run_or_refuses_it
# RQ10 a finished run is done whatever aibd read
rq '    run.state = if v.green { DONE } else { RED }.to_owned();' \
   '    run.state = if v.green || !v.green { DONE } else { RED }.to_owned(); // knife' \
   a_finished_run_is_done_only_when_aibd_reads_it_green
# RQ11 a run that ended is read again
rq '    if !under_way {' \
   '    if !under_way && run.head.is_empty() { // knife' \
   a_finished_run_is_done_only_when_aibd_reads_it_green
# RQ12 a run that ended is withdrawn
rq '    if in_flight(run) {' \
   '    if in_flight(run) || !run.head.is_empty() { // knife' \
   only_a_run_under_way_is_withdrawn
# RQ13 a run under way is dropped from the list
rq '        match runs.iter().position(|r| !in_flight(r)) {' \
   '        match runs.iter().position(|r| !in_flight(r) || !r.head.is_empty()) { // knife' \
   the_list_keeps_the_newest_and_every_run_under_way
# RQ14 a run id of any shape is looked up
ri '    if kind != REQUEST && !run_id_ok(&run_id) {' \
   '    if kind != REQUEST && !run_id_ok(&run_id) && run_id.is_empty() { // knife' \
   a_page_asks_for_a_receipt_reads_it_and_withdraws_it
# RQ15 an owner's answer is taken for a run that does not wait for one
rq '    if run.state != WAITING_OWNER {' \
   '    if run.state != WAITING_OWNER && run.head.is_empty() { // knife' \
   the_owners_answer_and_a_lost_machine_move_a_run
# RQ16 a run the owner refused goes on to run
rq '        run.state = RED.to_owned();' \
   '        run.state = RUNNING.to_owned(); // knife' \
   the_owners_answer_and_a_lost_machine_move_a_run
# RQ17 a run that already ended is marked lost
rq '    if !in_flight(run) {' \
   '    if !in_flight(run) && run.head.is_empty() { // knife' \
   the_owners_answer_and_a_lost_machine_move_a_run
# RQ18 a run id kept while the runner is asked does not count as under way
rq '    r.state == ASKING || r.state == WAITING_OWNER || r.state == RUNNING' \
   '    r.state == WAITING_OWNER || r.state == RUNNING // knife' \
   only_a_run_under_way_is_withdrawn
