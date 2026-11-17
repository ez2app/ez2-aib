#!/usr/bin/env bash
# T97-1m / T97-2m knives (043dd 11-17, IFACE-T97-1m r2 §9, IFACE-T97-2m r2 §4): aibd install / status /
# uninstall / start / stop. Each rule, broken, turns its own test red. Run by the shared crate-knives step
# (crates/aib-knife/run-knives.sh rust; default header: macOS, job rust); aib-xtask knife-anchors reads it.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ik() { python3 -I scripts/knife.py crates/aibd/src/install.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked "$4" "$3"; }
# IK1 the plist loses ProcessType Standard (launchd clamps a Background job, 11-16)
ik '    kv(&mut s, "ProcessType", "Standard");' '    kv(&mut s, "ProcessType", "Background"); // knife' \
   install_status_uninstall_leave_the_data_as_it_was --test=install_cli
# IK2 an old plist key that was not given again is dropped (the real aibd loses AIB_DRIVE)
ik '            env.insert(k.clone(), v.clone());' '            let _ = (k, v); // knife' \
   reinstall_keeps_old_env_keys_unless_unset --test=install_cli
# IK3 another build in the target is overwritten instead of refused
ik '        if there != me {' '        if false && there != me { // knife' \
   another_build_in_the_target_or_the_recorded_place_is_refused --test=install_cli
# IK4 uninstall takes the data folder with it
ik '    std::fs::remove_file(record_path(&data)).map_err(|e| format!("remove record: {e}"))?;' \
   '    std::fs::remove_dir_all(&data).map_err(|e| format!("remove record: {e}"))?; // knife' \
   install_status_uninstall_leave_the_data_as_it_was --test=install_cli
# IK5 a label that points at another aibd is rewritten (5d33a M1)
ik '        if !same_path(&points, &target_s) && !ours {' '        if false && !same_path(&points, &target_s) && !ours { // knife' \
   a_label_that_points_elsewhere_is_refused_and_adopt_only_writes_the_record --test=install_cli
# IK6 moving with --dir installs over a different recorded build (5d33a M2)
ik '        if !same_path(&r.exe, &target_s) && r.build_sha256 != me {' \
   '        if false && !same_path(&r.exe, &target_s) && r.build_sha256 != me { // knife' \
   another_build_in_the_target_or_the_recorded_place_is_refused --test=install_cli
# IK7 --adopt without --no-start goes on (it would register a record-only install)
ik '    if o.adopt && !o.no_start {' '    if false && o.adopt && !o.no_start { // knife' \
   adopt_without_no_start_is_refused --test=install_cli
# IK8 the fingerprint stops skipping run/ and install/
ik '        return FINGERPRINT_SKIP_DIRS.contains(&rel);' '        return false && FINGERPRINT_SKIP_DIRS.contains(&rel); // knife' \
   tests::the_fingerprint_skips_runtime_files_and_the_record --lib
# IK9 install acts on another label's record in the same data folder (5d33a M1)
ik '    if let Some(why) = old_rec.as_ref().and_then(|r| not_ours(r, &pl)) {' \
   '    if let Some(why) = old_rec.as_ref().and_then(|r| not_ours(r, &pl)).filter(|_| false) { // knife' \
   another_label_sharing_the_data_folder_is_refused --test=install_cli
# IK10 uninstall removes another label's aibd and plist (5d33a M1)
ik '    if let Some(why) = not_ours(&rec, &pl) {' '    if let Some(why) = not_ours(&rec, &pl).filter(|_| false) { // knife' \
   another_label_sharing_the_data_folder_is_refused --test=install_cli
# ---- T97-2m (IFACE-T97-2m-043dd-1117.md r2 §4): launchd, with stand-ins
# IK11 uninstall removes the files without taking the label out of launchd first (5d33a T97-1m N2)
ik '    if let Err(e) = unload(&rec.label, &data, svc, &mut out, false) {' \
   '    if let Err(e) = { let _ = svc; Ok::<(), Out>(()) } { // knife (RUSTFLAGS -D warnings: svc stays used)' \
   tests::uninstall_boots_out_before_removing_and_no_such_process_is_already_out --lib
# IK12 the old instance answering health counts as started (codex-O9)
ik '            if Some(id.as_str()) != old {' '            if Some(id.as_str()) != old || true { // knife (old stays used)' \
   tests::the_old_instance_answering_is_not_started --lib
# IK13 bootstrap target loses the gui/ domain
python3 -I scripts/knife.py crates/aibd/src/platform/macos_launchd.rs \
  '        format!("gui/{}", nix::unistd::getuid().as_raw())' \
  '        format!("{}", nix::unistd::getuid().as_raw()) // knife' \
  --expect-fail tests::the_real_domain_is_gui_and_the_user_id \
  -- cargo test -p aibd --locked --lib tests::the_real_domain_is_gui_and_the_user_id
# IK14 the wait for the lock is not the ExitTimeOut + grace limit
ik '    let steps = (EXIT_TIMEOUT_S + STOP_GRACE_S) * 1000 / STEP_MS;' \
   '    let steps = (EXIT_TIMEOUT_S + STOP_GRACE_S) * 4000 / STEP_MS; // knife' \
   tests::stop_gives_up_at_the_limit_while_the_lock_is_held --lib
# IK15 install bootstraps over a loaded label without bootout (5d33a r1 M1 / L3)
ik '            if let Err(e) = unload(&pl.label, &data, svc, &mut out, true) {' \
   '            if let Err(e) = { let _ = &data; Ok::<(), Out>(()) } { // knife (data stays used)' \
   tests::install_takes_a_loaded_label_out_then_bootstraps_without_kickstart --lib
# IK16 bootout "No such process" stops uninstall instead of counting as already out (L3)
ik '                Some(3) => out.say(format!("ALREADY-OUT {label}")),' \
   '                Some(3) => return Err(Out::fail(RC_REFUSED, "knife")),' \
   tests::uninstall_boots_out_before_removing_and_no_such_process_is_already_out --lib
# IK17 a kickstart after bootstrap (RunAtLoad already started it, L1)
ik '    out.say(format!("BOOTSTRAP {} {}", svc.ld.domain(), plist.display()));' \
   '    let _ = svc.ld.kickstart("knife"); out.say(format!("BOOTSTRAP {} {}", svc.ld.domain(), plist.display()));' \
   tests::install_takes_a_loaded_label_out_then_bootstraps_without_kickstart --lib
# IK18 sudo (uid 0) is not refused (N1)
ik '    if h.uid == Some(0) {' '    if false && h.uid == Some(0) { // knife' \
   tests::root_is_refused_before_any_launchctl --lib
