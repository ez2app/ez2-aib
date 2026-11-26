#!/usr/bin/env bash
# aib-knives: os=unix
# aib-slice clean events CE1–CE11 (a9136 11-17, S107-product slice 2, IFACE-s107-product-a9136-1117 r1
# §2.1 §8 item 1): every target gc deletes is one clean event. Each rule, broken, turns its own test red.
# Run on macOS (the header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust).
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ge() { python3 -I scripts/knife.py crates/aib-slice/src/gc.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked "$3"; }
gm() { python3 -I scripts/knife.py crates/aib-slice/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked "$3"; }
# CE1 the events are made but never written
ge '        events::append_or_warn(state_root, EVENTS_PROG, ev, at_ms);' \
   '        let _ = (ev, EVENTS_PROG); // knife' \
   each_deleted_target_is_one_clean_event_and_a_dry_run_deletes_nothing
# CE2 a dry run says deleted
ge '            let outcome = match (&r.failed, dry_run) {' '            let outcome = match (&r.failed, false) { // knife' \
   each_cache_is_its_own_event_and_a_dry_run_is_skipped
# CE3 a target that changed when checked again counts as failed
ge '                (Some(e), false) if e.starts_with("changed when checked again") => {' \
   '                (Some(e), false) if e.is_empty() => { // knife' \
   a_clean_event_says_deleted_failed_or_skipped_and_why_in_a_word
# CE4 a tagged build cache is called merged
ge '        Verdict::Remove(w) if w.starts_with("a build cache") => "build_cache",' \
   '        Verdict::Remove(w) if w.is_empty() => "build_cache", // knife' \
   each_cache_is_its_own_event_and_a_dry_run_is_skipped
# CE5 a Windows path keeps its backslashes
ge "    .replace('\\\\', \"/\")" "    .replace('\\u{0}', \"/\") // knife" \
   paths_are_relative_with_slashes_on_windows_too
# CE6 a cache event carries the whole tree's size
ge '                    .map_or(0, |c| c.bytes);' '                    .map_or(r.tree.bytes, |_| r.tree.bytes); // knife' \
   each_cache_is_its_own_event_and_a_dry_run_is_skipped
# CE7 a dry run expires event months
ge '    if !dry_run {' '    if dry_run || !dry_run { // knife' \
   the_command_expires_old_event_months_and_records_it
# CE8 the free space comes from a disk the folder is not on
ge '        .filter(|d| p.starts_with(d.mount_point()))' \
   '        .filter(|d| p.starts_with(d.mount_point()) && d.mount_point().as_os_str().is_empty()) // knife' \
   the_free_space_of_a_real_folder_is_read
# CE9 the command never writes its events
gm '            gc::write_events(&sr, &seat, &root, &rows, dry, now.saturating_mul(1000));' \
   '            let _ = (&sr, &rows); // knife' \
   the_command_expires_old_event_months_and_records_it
# CE10 a failed removal's event carries the sentence with its absolute path
ge '                    extra.insert("err".into(), json!(err_word(e)));' \
   '                    extra.insert("err".into(), json!(e)); // knife' \
   a_failed_removal_says_a_fixed_word_never_a_path
# CE11 a taken cleanup lock is called a failed removal
ge '        "cleanup_lock"' '        "remove_failed" // knife' \
   a_failed_removal_says_a_fixed_word_never_a_path
