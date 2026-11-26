#!/usr/bin/env bash
# aib-knives: os=unix
# T97-3m knives (043dd 11-17, IFACE-T97-3m-043dd-1117.md r2 §3): aibd update --file. Each rule, broken,
# turns its own test red. Run by the shared crate-knives step (crates/aib-knife/run-knives.sh rust;
# default header: macOS, job rust); aib-xtask knife-anchors reads it.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
uk() { python3 -I scripts/knife.py crates/aibd/src/install/update.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "install::update::tests::$3"; }
ik() { python3 -I scripts/knife.py crates/aibd/src/install.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "install::update::tests::$3"; }
# UK1 the proof is checked against the first binding, not one computed again before the swap
uk '    let expect = binding_for(&file, &old_version, h)?;' '    let expect = first.clone(); // knife' \
   a_file_changed_after_approval_is_refused
# UK2 a cancelled approval goes on
uk '        Err(e) => return Ok(Out::fail(RC_REFUSED, format!("not approved: {e}"))),' \
   '        Err(e) => { let _ = e; String::new() } // knife' \
   a_cancelled_approval_changes_nothing
# UK3 a refused verify is dropped
uk '    if let Err(e) = u.ap.verify(&proof, &expect) {' \
   '    if let Err(e) = u.ap.verify(&proof, &expect).or(Ok::<(), String>(())) { // knife' \
   a_refused_verify_stops_the_update
# UK4 a platform without a hardware key goes on (and would fall back to less)
uk '    if h.os != "macos" {' '    if false && h.os != "macos" { // knife' \
   update_off_macos_refuses_no_hw_approval_before_anything
# UK5 the swap does not check the sha256 once more
uk '    if got != want {' '    if false && got != want { // knife' \
   the_swap_checks_the_sha256_once_more
# UK6 a failed selfcheck leaves the new build in place
uk '        roll_back(&rec, &data, &mut t, svc, &mut out, true)?;' '        let _ = &mut t; // knife' \
   a_failed_selfcheck_puts_the_old_build_and_database_back
# UK7 an ad-hoc installed build is not refused (5d33a r2 N1)
uk '        return Ok(Out::fail(RC_REFUSED, ADHOC));' '        let _ = ADHOC; return Ok(Out::default()); // knife' \
   an_ad_hoc_installed_build_is_refused_with_a_reinstall_hint
# UK8 another signer is let through (5d33a r1 L1)
uk '    if let Err(e) = u.cs.verify(&file, &req) {' \
   '    if let Err(e) = u.cs.verify(&file, &req).or(Ok::<(), String>(())) { // knife' \
   a_build_from_another_signer_is_refused_before_the_approval
# UK9 the database is copied while aibd still runs (5d33a r1 L2)
uk '        return Err("aibd still holds its lock; the database is not copied while it runs".into());' \
   '        let _ = "knife";' \
   the_database_is_not_copied_while_aibd_holds_its_lock
# UK10 the roll back leaves the new build's database
uk '        restore_db(data)?;' '        let _ = restore_db; // knife' \
   a_failed_selfcheck_puts_the_old_build_and_database_back
# UK11 start does not finish or undo a half-done update (5d33a r1 L3)
ik '    update::recover(&rec, &data, svc, &mut out)?;' \
   '    if false { update::recover(&rec, &data, svc, &mut out)?; } // knife' \
   a_half_done_swap_is_undone_by_start
# UK12 the record says swapped only after the installed file moved (5d33a ce253f6a M1)
uk '            step(&data, &mut t, "swapped")' '            Ok::<(), String>(()) // knife' \
   the_record_says_swapped_before_the_installed_file_moves
# UK13 the copy that goes in place is not checked for its signer (5d33a ce253f6a L1)
uk '    if let Err(e) = signer(&tmp) {' '    if let Err(e) = signer(&tmp).or(Ok::<(), String>(())) { // knife' \
   a_copy_that_fails_the_signer_check_is_not_installed
# UK14 a failure after the stop leaves aibd stopped (5d33a ce253f6a L2)
uk '        let again = register(Path::new(&rec.plist), &data, svc, &mut out).is_ok();' \
   '        let again = true; // knife' \
   a_failure_after_the_stop_starts_the_old_build_again
