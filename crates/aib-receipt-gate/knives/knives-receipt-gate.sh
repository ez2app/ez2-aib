#!/usr/bin/env bash
# The receipt gate's reading of SSH_ORIGINAL_COMMAND (DESIGN-receipt-gate-a9136-1120 slice 1)
set -u
rg() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "tests::$3"; }
# RG1 an interactive login (no command) is served
rg '    let w = words(command.ok_or("no_command")?)?;' '    let w = words(command.unwrap_or("status web-a9136-012345678"))?; // knife' \
   only_four_verbs_of_plain_words_are_read
# RG2 a shell character in a word is taken
rg '        .all(|b| b == b'"'"' '"'"' || (b.is_ascii_graphic() && !b"\"'"'"'`\\$;&|<>()*?[]{}~#!".contains(&b)))' \
   '        .all(|b| b == b'"'"' '"'"' || b.is_ascii_graphic() || b == b'"'"'\t'"'"' || !b.is_ascii()) // knife' \
   only_four_verbs_of_plain_words_are_read
# RG3 a doubled or edge space is taken
rg '    if w.iter().any(|x| x.is_empty()) {' '    if w.is_empty() { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG4 a long line is read
rg '    if line.len() > MAX_LINE {' '    if line.len() > MAX_LINE * 100 { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG5 the run id may name another head
rg '            if head9_of(id) != head.get(..9) {' '            if id.is_empty() { // knife' \
   a_run_names_its_head_and_its_sizes
# RG6 a bundle over the limit is taken
rg '    s.parse::<u64>().ok().filter(|n| *n <= max)' '    s.parse::<u64>().ok() // knife' \
   a_run_names_its_head_and_its_sizes
# RG7 a run id of another shape is taken
rg '    if web != Some("web") || !seat_ok || !again_ok || end.is_some() {' '    if end.is_some() { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG8 knives with the whole workspace are taken
rg '            if *krate == "ws" && knives_len > 0 {' '            if *krate == "ws" && knives_len > MAX_KNIVES { // knife' \
   a_run_names_its_head_and_its_sizes
# RG9 a zero-length bundle is taken
rg '                .filter(|n| *n > 0)' '                .filter(|_| true) // knife' \
   a_run_names_its_head_and_its_sizes
# slice 2 (recv.rs): stdin files, knife table, base repository
rv() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/recv.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "recv::tests::$3"; }
# RV1 a file whose digest differs is kept
rv '        if hex != sha256 {' '        if hex.is_empty() { // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV2 a refused file is left on disk
rv '        let _ = std::fs::remove_file(out);' '        let _ = out; // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV3 an existing file is written over
rv '            .create_new(true)' '            .create(true).truncate(true) // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV4 a short stream is taken as whole
rv '            if n == 0 {' '            if n == usize::MAX { // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV5 a knife of another crate is taken
rv '        if *c != krate {' '        if c.is_empty() { // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV6 shell words in the test arguments are taken
rv '        if !plain(name, b"-_") || !plain(targs, b"-_: ") {' '        if !plain(name, b"-_") { // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV7 a knife file outside the tree is taken
rv '                .any(|p| p.is_empty() || p == "." || p == "..");' '                .any(|p| p.is_empty()); // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV8 a base with a hook or alternates is used
rv '    if !hooks_empty || git_dir.join("objects/info/alternates").exists() {' '    if !hooks_empty { // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV9 an unknown config section is taken
rv '                _ => return false,' '                _ => "core", // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV10 any core key is taken (hooksPath, fsmonitor)
rv '            "core" => CORE.contains(&k.as_str()) && plain(v, b""),' '            "core" => true, // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV11 the remote may point anywhere
rv '                (k == "url" && v == BASE_URL)' '                (k == "url")' \
   a_base_repo_the_last_run_touched_is_rebuilt
# slice 3a (sandbox.rs, b4297 design r3 §3): what a sent tree may change, the profile, the fetch
sb() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/sandbox.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "sandbox::tests::$3"; }
# SB1 .Cargo/Config.toml (another case) is let through
sb '        let p = part.to_ascii_lowercase();' '        let p = part.to_owned(); // knife' \
   a_tree_that_picks_its_toolchain_or_cargo_settings_is_refused
# SB2 a changed .cargo folder is let through
sb '        p == ".cargo" || p == "rust-toolchain" || p == "rust-toolchain.toml"' '        p == "rust-toolchain" || p == "rust-toolchain.toml" // knife' \
   a_tree_that_picks_its_toolchain_or_cargo_settings_is_refused
# SB3 one refused path among others is let through
sb '    if changed.into_iter().any(path_refused) {' '    if changed.into_iter().all(path_refused) { // knife' \
   a_tree_that_picks_its_toolchain_or_cargo_settings_is_refused
# SB4 a registry that is not crates.io is let through
sb '        .filter(|l| l.starts_with("source"))' '        .filter(|l| l.starts_with("source = \"git")) // knife' \
   a_lock_with_a_source_that_is_not_crates_io_is_refused
# SB5 a quote in a place goes into the profile
sb '            .all(|b| b.is_ascii_alphanumeric() || b"/._-+".contains(&b))' '            .all(|b| b.is_ascii_graphic()) // knife' \
   a_place_is_a_plain_absolute_path
# SB6 a run folder outside the home is taken
sb '        || !below(work, home)' '        || !below(work, "") // knife' \
   a_place_is_a_plain_absolute_path
# SB7 reads are open
sb '        format!("(allow file-read*{})", quoted(&walked, "literal")),' '        format!("(allow file-read*){}", &quoted(&walked, "literal")[..0]), // knife' \
   the_profile_allows_by_list_and_writes_only_the_run_folder
# SB8 the home is writable
sb '        format!("(allow file-write* (subpath \"{work}\"))"),' '        format!("(allow file-write* (subpath \"{home}\"))"), // knife' \
   the_profile_allows_by_list_and_writes_only_the_run_folder
# SB9 the keychain service is found
sb '        format!("(allow mach-lookup{mach})"),' '        format!("(allow mach-lookup{mach} (global-name \"com.apple.SecurityServer\"))"), // knife' \
   the_profile_keeps_mach_the_network_and_exec_closed
# SB10 the network is open
sb '        "(allow network-outbound (remote ip \"localhost:*\"))".to_owned(),' '        "(allow network-outbound)".to_owned(), // knife' \
   the_profile_keeps_mach_the_network_and_exec_closed
# SB11 this machine's sshd and aibd are reached
sb '        .map(|port| format!(" (remote ip \"localhost:{port}\")"))' '        .map(|port| format!(" (remote ip \"localhost:1{port}\")")) // knife' \
   the_profile_keeps_mach_the_network_and_exec_closed
# SB12 only the first program is refused
sb '        format!("(deny process-exec{})", quoted(&no_exec, "literal")),' '        format!("(deny process-exec{})", quoted(&no_exec[..1], "literal")), // knife' \
   the_profile_keeps_mach_the_network_and_exec_closed
# SB13 the fetch goes through the rustup proxy
sb '            format!("{tc}/bin/cargo"),' '            format!("{}/.cargo/bin/cargo", p.home), // knife' \
   the_fetch_is_the_pinned_cargo_from_an_empty_folder
# SB14 the fetch starts in the tree
sb '        cwd: empty.to_owned(),' '        cwd: format!("{}/tree{}", p.work, &empty[..0]), // knife' \
   the_fetch_is_the_pinned_cargo_from_an_empty_folder
# SB15 a program of the run starts with no profile
sb '        "/usr/bin/sandbox-exec".to_owned(),' '        "/usr/bin/env".to_owned(), // knife' \
   a_program_of_the_run_starts_under_the_profile
