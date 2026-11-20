#!/usr/bin/env bash
# N-webonly 第 4 件 knives RN1–RN15 (b4297 11-20): the half behind ReceiptRunner, slice R1 —
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
