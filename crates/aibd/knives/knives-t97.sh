#!/usr/bin/env bash
# T97-1m knives (043dd 11-17, IFACE-T97-1m-043dd-1117.md r2 §9): aibd install --no-start / status /
# uninstall. Each rule, broken, turns its own test red. Called from the shared aibd knife step in
# .github/workflows/aibd.yml (after knives-p11.sh); aib-xtask knife-anchors reads it. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ik() { python3 -I scripts/knife.py crates/aibd/src/install.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked "${@:4}" "$3"; }
# IK1 the plist loses ProcessType Standard (launchd clamps a Background job, 11-16)
ik '    kv(&mut s, "ProcessType", "Standard");' '    kv(&mut s, "ProcessType", "Background"); // knife' \
   install_status_uninstall_leave_the_data_as_it_was --test install_cli
# IK2 an old plist key that was not given again is dropped (the real aibd loses AIB_DRIVE)
ik '            env.insert(k.clone(), v.clone());' '            let _ = (k, v); // knife' \
   reinstall_keeps_old_env_keys_unless_unset --test install_cli
# IK3 another build in the target is overwritten instead of refused
ik '        if there != me {' '        if false && there != me { // knife' \
   another_build_in_the_target_or_the_recorded_place_is_refused --test install_cli
# IK4 uninstall takes the data folder with it
ik '    std::fs::remove_file(record_path(&data)).map_err(|e| format!("remove record: {e}"))?;' \
   '    std::fs::remove_dir_all(&data).map_err(|e| format!("remove record: {e}"))?; // knife' \
   install_status_uninstall_leave_the_data_as_it_was --test install_cli
# IK5 a label that points at another aibd is rewritten (5d33a M1)
ik '        if !same_path(&points, &target_s) && !ours {' '        if false && !same_path(&points, &target_s) && !ours { // knife' \
   a_label_that_points_elsewhere_is_refused_and_adopt_only_writes_the_record --test install_cli
# IK6 moving with --dir installs over a different recorded build (5d33a M2)
ik '        if !same_path(&r.exe, &target_s) && r.build_sha256 != me {' \
   '        if false && !same_path(&r.exe, &target_s) && r.build_sha256 != me { // knife' \
   another_build_in_the_target_or_the_recorded_place_is_refused --test install_cli
# IK7 install starts without --no-start being asked for
ik '    if !o.no_start {' '    if false && !o.no_start { // knife' \
   install_without_no_start_is_refused --test install_cli
# IK8 the fingerprint stops skipping run/ and install/
ik '        return FINGERPRINT_SKIP_DIRS.contains(&rel);' '        return false && FINGERPRINT_SKIP_DIRS.contains(&rel); // knife' \
   tests::the_fingerprint_skips_runtime_files_and_the_record --lib
# IK9 install acts on another label's record in the same data folder (5d33a M1)
ik '    if let Some(why) = old_rec.as_ref().and_then(|r| not_ours(r, &pl)) {' \
   '    if let Some(why) = old_rec.as_ref().and_then(|r| not_ours(r, &pl)).filter(|_| false) { // knife' \
   another_label_sharing_the_data_folder_is_refused --test install_cli
# IK10 uninstall removes another label's aibd and plist (5d33a M1)
ik '    if let Some(why) = not_ours(&rec, &pl) {' '    if let Some(why) = not_ours(&rec, &pl).filter(|_| false) { // knife' \
   another_label_sharing_the_data_folder_is_refused --test install_cli
