#!/usr/bin/env bash
# §20 試用預覽版 knives PV1–PV6 (a9136 11-18, DESIGN-preview-a9136-1118 rev 2 / 5d33a CHECK-preview
# r1): only the built-in page agrees, every agreement is on the books, the preview gets its own
# HOME and data and nothing else of this aibd's. Run from the repo root.
# The hash is checked twice (before and on the copy): the first check has no knife of its own (the
# second would catch it); the second is PV7 through stage() directly.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pu() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "preview::tests::$3"; }
pe() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" \
        --expect-fail the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data \
        -- cargo test -p aibd --locked --test preview the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data; }
# PV1 a user folder page agrees for the person
pe '    if !words || page.carried {' \
   '    if page.carried && words { // knife'
# PV2 the preview shares this aibd's data folder
pu '        dir.join("data").to_string_lossy().into_owned(),' \
   '        dir.join("..").to_string_lossy().into_owned(), // knife' \
   the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
# PV3 the preview finds the real pending / tree / .claude from HOME (5d33a M1)
pu '    let home = dir.join("home").to_string_lossy().into_owned();' \
   '    let home = std::env::var("HOME").unwrap_or_else(|_| dir.display().to_string()); // knife' \
   the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
# PV4 an agreement is not on the books
pe '                "preview.consented",' \
   '                "preview.agreed_knife", // knife'
# PV5 a start without the window's 同意
pu '    if body.get("consent") != Some(&json!(true)) {' \
   '    if body.get("consent") == Some(&json!("knife")) { // knife' \
   a_start_needs_the_consent_and_the_hash_and_nothing_else
# PV6 everything of this aibd's environment goes to the preview
pu '        .filter(|(k, _)| keep(k) || k == "AIBD_PAIRING" || PREVIEW_PASS_ENV.contains(&k.as_str()))' \
   '        .filter(|(k, _)| keep(k) || !k.is_empty()) // knife' \
   the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
# PV7 the copy is run without hashing it again (5d33a PREREAD N1)
pu '    if hash_file(&copy).as_deref() != Some(sha256) {' \
   '    if hash_file(&copy).as_deref() != Some(sha256) && false {' \
   the_copy_runs_only_when_it_is_still_the_file_agreed_to
# PV8 a failed build counts as a newer version (N-pv-up P1)
pu '    let ready: Vec<&Version> = versions.iter().filter(|v| v.state == "ready").collect();' \
   '    let ready: Vec<&Version> = versions.iter().collect(); // knife' \
   a_running_build_is_behind_the_ready_versions_after_it
# PV9 the running build counts itself as newer
pu '        Some(i) => ready[i + 1..].to_vec(),' \
   '        Some(i) => ready[i..].to_vec(), // knife' \
   a_running_build_is_behind_the_ready_versions_after_it
# PV10 a stopped preview is shown 「有新版」
pu '    let Some(cur) = current else {' \
   '    let Some(cur) = current.or(Some("")) else { // knife' \
   a_running_build_is_behind_the_ready_versions_after_it
# PV11 each build of a preview runs from its own path: the pairing is not carried (§23c)
pu '    let place = run.join(bin_name());' \
   '    let place = run.join(format!("{}-{}", &sha256[..12], bin_name())); // knife' \
   every_build_of_a_preview_runs_from_the_same_path
# PV12 a moved head is not shown as changed
pu '        .filter(|n| !prev.nodes.iter().any(|p| p.id == n.id && p.head == n.head))' \
   '        .filter(|n| !prev.nodes.iter().any(|p| p.id == n.id)) // knife' \
   every_build_of_a_preview_runs_from_the_same_path
# 上預覽 (preview_tree.rs, 5d33a CHECK-pv-up M1)
pt() { python3 -I scripts/knife.py crates/aibd/src/preview_tree.rs "$1" "$2" --expect-fail \
        the_tree_takes_the_subtree_and_its_needs_once_with_the_worst_light_on_top \
        -- cargo test -p aibd --locked --lib preview_tree::tests::the_tree_takes_the_subtree_and_its_needs_once_with_the_worst_light_on_top; }
# PU1 a merge without the agreement's hash goes through
pt '    if !hex64 {' '    if !hex64 && false { // knife'
# PU2 the agreement's hash does not follow the heads
pt '                h["head"].as_str().unwrap_or("")' '                "" // knife'
# 上預覽合併 (preview_up.rs, 5d33a CHECK-pv-up M2 / L3)
pm() { python3 -I scripts/knife.py crates/aibd/src/preview_up.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "preview_up::tests::$3"; }
# PU3 a changed line is resolved by itself
pm '        if !closed || !saw_base || part != 2 || !base.is_empty() {' \
   '        if !closed || !saw_base || part != 2 { // knife' \
   only_two_sides_adding_lines_to_nothing_is_resolved_by_itself
# PU4 a conflict half-resolved is merged anyway
pm '        if files.is_empty() || fixed.len() != files.len() {' \
   '        if files.is_empty() { // knife' \
   heads_merge_into_the_previews_own_worktree
# PU5 git reads the system config
pm '        ("GIT_CONFIG_NOSYSTEM".to_owned(), "1".to_owned()),' \
   '        ("GIT_X".to_owned(), "1".to_owned()), // knife' \
   git_gets_only_the_fixed_environment
# PU6 a feature build is written as a fix (swapped in without the button)
pm '        if kind == "feature" { "feature" } else { "fix" }' \
   '        "fix" // knife' \
   the_version_text_carries_each_nodes_line_author_and_card
# PU7 the build is left half-placed (.next never renamed)
pm '    std::fs::rename(&next, dir.join(name)).map_err(|e| e.to_string())?;' \
   '    let _ = (&next, dir.join(name)); // knife' \
   a_placed_build_is_what_the_list_finds
# PU8 the build shares the person's target folder
pm '        "CARGO_TARGET_DIR".to_owned(),' \
   '        "CARGO_TARGET_DIR_X".to_owned(), // knife' \
   the_build_gets_cargo_homes_and_its_own_target
# PU9 a short head is agreed to as it is (5d33a CHECK-pv-up-p23 M1)
pm '    let full = out.trim().to_ascii_lowercase();' \
   '    let full = { let _ = &out; short.to_ascii_lowercase() }; // knife (out stays used: -D warnings)' \
   heads_merge_into_the_previews_own_worktree
# PV12 a parent run under another HOME hands its own HOME to the seats (519d0 11-19 08:36)
pu '    let real_home = named(crate::platform::session::SEAT_HOME_ENV)' \
   '    let real_home = None::<String>' \
   a_parent_with_a_seat_home_hands_that_on_not_its_own_home
# PV13 the documents folder, import state, old cabin, mirror and person's name stay behind
pu '    "AIBD_PLAN_PENDING",' '    "AIBD_PLAN_PENDING_KNIFE",' \
   the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
# PV-UPD a preview build may update the real aibd (5d33a PREREAD-integws2 M2)
pu '    matches!(cmd, "install" | "uninstall" | "start" | "stop" | "update")' \
   '    matches!(cmd, "install" | "uninstall" | "start" | "stop") // knife' \
   a_preview_refuses_every_command_that_changes_the_real_aibd
# PV-MAIN the binary's update arm no longer asks the preview list (5d33a PREREAD-integws2 r2 L8)
pmn() { python3 -I scripts/knife.py crates/aibd/src/main.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test install_cli "$3"; }
pmn '            if aibd::preview::is_preview() && aibd::preview::refuses_service_cmd("update") {' \
    '            if false { // knife' \
    a_preview_binary_refuses_install_start_stop_and_update
