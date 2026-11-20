#!/usr/bin/env bash
# N-webonly 第 4 件 knives RN1–RN16, RB1–RB4, RR1–RR8, RL1–RL6 (b4297 11-20): the half behind ReceiptRunner, slice R1 —
# what is decided before anything leaves this machine (the machine, what a head brings, the
# fingerprint's binding, the lines said to the gate). Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
rn() { python3 -I scripts/knife.py crates/aibd/src/receipt_runner.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "receipt_runner::tests::$3"; }
# RN1 any value of the switch turns the runner on
rn '    v.as_deref() == Some("1")' '    v.is_some() // knife' \
   the_switch_is_off_until_it_is_one
# RN2 a machine with no sandbox (Windows) is picked
rn '    hosts.iter().find(|h| h.os == "mac")' '    hosts.iter().find(|h| !h.os.is_empty()) // knife' \
   a_receipt_goes_to_a_pinned_mac_or_nowhere
# RN3 a head that changes rust-toolchain.toml or .cargo/config.toml gets a card
rn '    if changed.iter().any(|(_, p)| path_refused(p)) || !lock_ok(head_lock) {' \
   '    if changed.iter().all(|(_, p)| path_refused(p)) || !lock_ok(head_lock) { // knife' \
   a_head_that_picks_its_toolchain_or_its_sources_is_refused_before_a_card
# RN4 the base's lock is judged, not the head's (a git source goes out)
rn '    if changed.iter().any(|(_, p)| path_refused(p)) || !lock_ok(head_lock) {' \
   '    if changed.iter().any(|(_, p)| path_refused(p)) || !lock_ok(base_lock) { // knife' \
   a_head_that_picks_its_toolchain_or_its_sources_is_refused_before_a_card
# RN5 a removed build.rs is listed as one that runs
rn '    let kept = |(status, _): &&(String, String)| status != "D";' \
   '    let kept = |(status, _): &&(String, String)| !status.is_empty(); // knife' \
   what_a_head_brings_is_read_from_its_changes_and_its_lock
# RN6 the packages the base already had are the ones listed
rn '        new_packages: packages(head_lock).difference(&had).cloned().collect(),' \
   '        new_packages: packages(head_lock).intersection(&had).cloned().collect(), // knife' \
   what_a_head_brings_is_read_from_its_changes_and_its_lock
# RN7 a new package alone is not red
rn '        !self.build_scripts.is_empty() || !self.new_packages.is_empty()' \
   '        !self.build_scripts.is_empty() // knife' \
   the_window_says_when_new_code_runs_at_build
# RN8 the fingerprint does not bind the bundle
rn '        "bundle_sha256": s.bundle_sha256, "knives_sha256": s.knives_sha256,' \
   '        "bundle_sha256": "", "knives_sha256": s.knives_sha256, // knife' \
   the_fingerprint_is_for_this_bundle_on_this_machine
# RN9 the fingerprint does not bind the machine
rn '        "host": s.host, "run_id": s.run_id, "head": s.head, "base": s.base,' \
   '        "host": "", "run_id": s.run_id, "head": s.head, "base": s.base, // knife' \
   the_fingerprint_is_for_this_bundle_on_this_machine
# RN10 the fingerprint does not bind what the head brings
rn '        "new_packages": b.new_packages, "new_code_files": b.new_code_files, "files": b.files,' \
   '        "new_packages": 0, "new_code_files": b.new_code_files, "files": b.files, // knife' \
   the_fingerprint_is_for_this_bundle_on_this_machine
# RN11 a line is sent without the gate's reader having taken it
rn '    aib_receipt_gate::parse(Some(&line)).map(|_| line)' \
   '    aib_receipt_gate::parse(Some("status web-a9136-012345678")).map(|_| line) // knife' \
   a_line_to_a_machine_is_one_the_gate_reads
# RN12 another verb's OK starts a run
rn '    if first.strip_prefix("OK ") == Some(verb) {' \
   '    if first.starts_with("OK ") && !verb.is_empty() { // knife' \
   a_gates_answer_is_ok_or_a_fixed_reason
# RN13 a long machine name pushes the run id out of the window
rn '    let host: String = host.chars().take(HOST_SHOWN).collect();' \
   '    let host: String = host.chars().take(HOST_SHOWN * 4).collect(); // knife' \
   the_window_says_when_new_code_runs_at_build
# RN14 a run still under way is ended by one read
rn '        b"ERR not_done" => Fetched::NotYet,' '        b"ERR not_done" => Fetched::Ended("lost"), // knife' \
   a_fetch_brings_the_out_or_says_how_the_run_stands
# RN15 another verb's answer is read as the .out
rn '    if let Some(out) = stdout.strip_prefix(OK) {' '    if let Some(out) = stdout.strip_prefix(&OK[..3]) { // knife' \
   a_fetch_brings_the_out_or_says_how_the_run_stands
# RN16 a reason this side does not know is blamed on the tree
rn '        Some(_) => "refused",' '        Some(_) => "tree_refused", // knife' \
   a_reason_this_side_does_not_know_is_not_the_trees_fault
# slice R4b (receipt_runner.rs): the runner, with doubles for the machine, the owner and aibd
# RR1 with no link a request is taken (a bundle is made, a card is raised, for nothing)
rn '        if !self.link.ready() {' '        if !self.link.ready() && ask.head.is_empty() { // knife' \
   a_request_is_refused_before_anything_leaves_or_is_asked
# RR2 a narrow request is taken
rn '        if ask.narrow || !ask.tests.is_empty() {' '        if ask.narrow && ask.tests.is_empty() { // knife' \
   a_request_is_refused_before_anything_leaves_or_is_asked
# RR3 a seat that is not trusted gets a receipt
rn '        if !self.facts.trusted(&ask.seat_id) {' '        if !self.facts.trusted(&ask.seat_id) && ask.head.is_empty() { // knife' \
   a_request_is_refused_before_anything_leaves_or_is_asked
# RR4 a head that is not the seat's own is taken
rn '        if head != ask.head {' '        if head.is_empty() { // knife' \
   a_request_is_refused_before_anything_leaves_or_is_asked
# RR5 a run withdrawn while it is prepared still raises its card
rn '        let yes = !self.withdrawn(run_id) && self.owner.approves(&card);' '        let yes = self.owner.approves(&card); // knife' \
   a_run_withdrawn_while_it_is_prepared_is_not_sent
# RR6 the owner's no still sends the bundle
rn '        if !yes || !still || self.withdrawn(run_id) {' '        if !still || self.withdrawn(run_id) { // knife' \
   a_no_sends_nothing
# RR7 a run still being prepared is asked for at the machine (which ends it: no such run)
rn '        if known.as_ref().is_some_and(|k| !k.sent) {' '        if known.as_ref().is_some_and(|k| k.withdrawn && !k.sent) { // knife' \
   a_yes_sends_exactly_the_bundle_the_owner_was_shown
# RR8 one read with no answer ends the run
rn '            if missed >= MISSED_READS {' '            if missed >= 1 { // knife' \
   a_silent_machine_ends_a_run_late_and_one_that_never_heard_of_it_at_once
# slice R4a (receipt_bundle.rs): the bundle of one run
rb() { python3 -I scripts/knife.py crates/aibd/src/receipt_bundle.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "receipt_bundle::tests::$3"; }
# RB1 the base is main's tip (what main did since counts as the head's; the copy must hold it)
rb '    let base = git(scratch, hooks, &["merge-base", &main, head]).map_err(|_| "tree_refused")?;' \
   '    let base = git(scratch, hooks, &["rev-parse", &main]).map_err(|_| "tree_refused")?; // knife' \
   the_base_is_where_the_head_left_main_even_when_main_moved_on
# RB2 what the head changes is not judged (a .cargo/config.toml goes out)
rb '    let b = brings(&status_entries(&z), &lock_at(&base), &lock_at(head))?;' \
   '    let b = brings(&status_entries(&z)[..0], &lock_at(&base), &lock_at(head))?; // knife' \
   a_refused_head_leaves_no_bundle
# RB3 the base's lock is judged, not the head's (a git source goes out)
rb '    let b = brings(&status_entries(&z), &lock_at(&base), &lock_at(head))?;' \
   '    let b = brings(&status_entries(&z), &lock_at(&base), &lock_at(&base))?; // knife' \
   a_refused_head_leaves_no_bundle
# RB4 a head that holds a symbolic link gets a bundle
rb '    aib_receipt_gate::sandbox::tree_modes_ok(modes.as_bytes())?;' \
   '    aib_receipt_gate::sandbox::tree_modes_ok(&modes.as_bytes()[..0])?; // knife' \
   a_refused_head_leaves_no_bundle
# slice R3 (receipt_link.rs): aibd's own key and the line for a machine
rl() { python3 -I scripts/knife.py crates/aibd/src/receipt_link.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "receipt_link::tests::$3"; }
# RL1 from= takes a pattern or a name (the key works from any machine)
rl '    ip.parse::<std::net::IpAddr>().is_ok()' '    !ip.is_empty() // knife' \
   the_line_for_a_machine_only_lets_the_gate_answer_this_mac
# RL2 a gate path that ends the quotes is put in the line
rl '            .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))' '            .all(|b| b != 32) // knife' \
   the_line_for_a_machine_only_lets_the_gate_answer_this_mac
# RL3 the line has no restrict (a shell, forwarding)
rl '        "restrict,command=\"{gate}\",from=\"{from_ip}\" {}",' '        "command=\"{gate}\",from=\"{from_ip}\" {}", // knife' \
   the_line_for_a_machine_only_lets_the_gate_answer_this_mac
# RL4 a key file others can read is used
rl '    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file() && m.mode() & 0o077 == 0)' \
   '    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file() && m.mode() & 0o000 == 0) // knife' \
   a_pair_is_made_once_and_only_a_private_plain_file_is_used
# RL5 a new pair is made at every start (the line on the machines stops matching)
rl '    if std::fs::symlink_metadata(&key).is_err() {' '    if !key.as_os_str().is_empty() { // knife' \
   a_pair_is_made_once_and_only_a_private_plain_file_is_used
# RL6 what a machine printed before it was stopped at the limit is taken as its answer
rl '        if ended {' '        if ended || !bytes.is_empty() { // knife' \
   a_machine_that_does_not_answer_in_time_is_no_answer
