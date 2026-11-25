#!/usr/bin/env bash
# aib-knives: os=macos   # 90856 11-26: on ubuntu a knife here missed (a_new_folder_is_named_noindex_and_an_old_one_kept; #895 run 45610010326)
# NI1-NI4, NI6, NI7 shared folders and keeping indexers out (PREREAD-shared-folder-noindex-a9136-1121, 5d33a 0/0/2/2). NI5 (Windows attribute) is aib_sys::noindex's own test on the Windows cell.
set -euo pipefail
fo() { python3 -I scripts/knife.py crates/aibd/src/folders.rs "$1" "$2" --expect-fail "folders::tests::$3" \
        -- cargo test -p aibd --locked --lib "folders::tests::$3"; }
pl() { python3 -I scripts/knife.py crates/aibd/src/platform/mod.rs "$1" "$2" --expect-fail "folders::tests::$3" \
        -- cargo test -p aibd --locked --lib "folders::tests::$3"; }
# NI1 a link is used as our own pending folder
fo '        if plain_dir(&own) {' '        if own.exists() { // knife' \
   the_documents_folder_is_chosen_in_order
# NI2 a new folder has no .noindex name
fo '        parent.join(format!("{base}{SUFFIX}"))' '        parent.join(base) // knife' \
   a_new_folder_is_named_noindex_and_an_old_one_kept
# NI3 no .metadata_never_index
fo '        if std::fs::symlink_metadata(&m).is_err() {' '        if m.as_os_str().is_empty() { // knife' \
   a_prepared_folder_holds_the_mark
# NI4 an old folder is renamed
fo '    if cfg!(target_os = "macos") && std::fs::symlink_metadata(&old).is_err() {' '    if cfg!(target_os = "macos") { // knife' \
   a_new_folder_is_named_noindex_and_an_old_one_kept
# NI6 (5d33a L1) the override is used inside a guarded folder
fo '        real.starts_with(&g)' '        real.starts_with(g.join("knife-never")) // knife' \
   the_documents_folder_is_chosen_in_order
# NI6b (5d33a L1) the override is used when it is a link
fo '    if std::fs::symlink_metadata(p).is_ok() && !(plain_dir(p) && own(p)) {' '    if std::fs::symlink_metadata(p).is_ok() && !own(p) { // knife' \
   the_documents_folder_is_chosen_in_order
# NI6c (5d33a L1) a .git folder is used
fo '    if real.components().any(|c| c.as_os_str() == ".git") {' '    if real.as_os_str().is_empty() { // knife' \
   the_documents_folder_is_chosen_in_order
# NI7 (5d33a L2) a new Windows install roams
pl '    if roaming != local && roaming.exists() {' '    if roaming != local { // knife' \
   a_new_install_does_not_roam
