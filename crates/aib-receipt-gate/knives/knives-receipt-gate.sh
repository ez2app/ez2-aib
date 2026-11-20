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
# slice 3b-1 (run.rs, b4297): where a run lives, its state, what status / fetch say
ru() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/run.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "run::tests::$3"; }
# RU1 an .out that is a link is read (any file of the account goes into the receipt)
ru '        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)' '        .custom_flags(libc::O_NONBLOCK) // knife' \
   an_out_that_is_a_link_is_not_followed
# RU2 a run folder put aside for a link is read through
ru '    if !std::fs::symlink_metadata(&work).is_ok_and(|m| m.is_dir()) {' '    if !work.is_dir() { // knife' \
   an_out_that_is_a_link_is_not_followed
# RU3 an .out over the cap is kept
ru '    if bytes.len() as u64 > MAX_OUT {' '    if bytes.is_empty() { // knife' \
   an_out_over_the_cap_is_not_kept
# RU4 a kept .out is replaced by a later one
ru '        .create_new(true)' '        .create(true).truncate(true) // knife' \
   status_and_fetch_say_fixed_words_and_the_out_only_when_done
# RU5 a refusal's words are said as they are (a path)
ru '            return Some(Err(if code_ok(code) {' '            return Some(Err(if !code.is_empty() { // knife' \
   a_note_says_the_last_step_or_the_refusal
# RU6 an .out left behind beats the gate's refusal
ru '    if let Some(Err(code)) = says {' '    if let (Some(Err(code)), false) = (says.clone(), plain_file(&l.kept_out(run_id))) { // knife' \
   a_runs_state_is_read_from_its_files
# RU7 what the tested code wrote in the run folder makes the run done
ru '    if plain_file(&l.kept_out(run_id)) {' '    if plain_file(&l.out(run_id)) { // knife' \
   a_runs_state_is_read_from_its_files
# RU8 a run whose supervisor is gone reads under way
ru '        (Some(p), Some(Ok(step))) if alive(p) => State::UnderWay(step),' '        (Some(_), Some(Ok(step))) => State::UnderWay(step), // knife' \
   a_runs_state_is_read_from_its_files
# RU9 fetch hands back the run folder's .out
ru '            let path = l.kept_out(run_id);' '            let path = l.out(run_id); // knife' \
   status_and_fetch_say_fixed_words_and_the_out_only_when_done
# RU10 a note that is a link is written through
ru '    if std::fs::symlink_metadata(&p).is_ok_and(|m| !m.is_file()) {' '    if p.as_os_str().is_empty() { // knife' \
   an_out_that_is_a_link_is_not_followed
# RU11 pid 1 (or 0) in the pid file is signalled as a group
ru '        .map_while(|t| t.trim().parse::<u32>().ok().filter(|p| *p > 1))' '        .map_while(|t| t.trim().parse::<u32>().ok()) // knife' \
   a_runs_state_is_read_from_its_files
# slice 3b-2a (plan.rs, b4297): every program the gate starts for one run
pl() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/plan.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "plan::tests::$3"; }
# PL1 a hook of the repository runs
pl '    "core.hooksPath=/dev/null",' '    "core.hooksPath=.git/hooks", // knife' \
   every_git_runs_with_the_gates_own_settings
# PL2 every transport is open
pl '    "protocol.allow=never",' '    "protocol.allow=always", // knife' \
   every_git_runs_with_the_gates_own_settings
# PL3 the account's keychain helper is asked
pl '    "credential.helper=",' '    "credential.helper=osxkeychain", // knife' \
   every_git_runs_with_the_gates_own_settings
# PL4 git's home is not the empty folder
pl '        ("HOME", home),' '        ("HOME", &home[..0]), // knife' \
   every_git_runs_with_the_gates_own_settings
# PL5 the account's git settings are read
pl '        ("GIT_CONFIG_GLOBAL", "/dev/null"),' '        ("GIT_CONFIG_GLOBAL", ""), // knife' \
   every_git_runs_with_the_gates_own_settings
# PL6 the tree shares the base's object files
pl '                "--no-hardlinks",' '                "--local", // knife' \
   the_tree_is_a_copy_with_nothing_checked_out_until_it_is_judged
# PL7 the tree is checked out before it is judged
pl '                "--no-checkout",' '                "--quiet", // knife' \
   the_tree_is_a_copy_with_nothing_checked_out_until_it_is_judged
# PL8 the base is fetched over ssh (the account's keys)
pl '            Some("https"),' '            Some("ssh"), // knife' \
   a_transport_is_open_only_for_the_step_that_needs_it
# PL9 a setting given twice is taken
pl '        if got[at].replace(v).is_some() {' '        if got[at].replace(v).is_some() && v.is_empty() { // knife' \
   the_pinned_settings_are_whole_or_nothing
# PL10 a toolchain name that is a path is taken
pl '    if !paths_ok || !hex64(sha) || !toolchain_ok(toolchain) {' '    if !paths_ok || !hex64(sha) || !toolchain_ok(toolchain) && false { // knife' \
   the_pinned_settings_are_whole_or_nothing
# PL11 a changed path that is not text is skipped, not refused
pl '        .map(|p| std::str::from_utf8(p).map_err(|_| "tree_refused"))' '        .map(|p| Ok(std::str::from_utf8(p).unwrap_or("x"))) // knife' \
   changed_paths_are_read_whole
# PL13 the head is judged against main's tip (a head cut from an older main is refused for main's own changes)
pl '            &format!("{BASE_REF}...{RUN_REF}"),' '            &format!("{BASE_REF}..{RUN_REF}"), // knife' \
   the_tree_is_a_copy_with_nothing_checked_out_until_it_is_judged
# PL12 receipt-run takes its own lease and fetch inside the sandbox
pl '    s.env.push(("RR_GATE".into(), "1".into()));' '    s.env.push(("RR_GATE".into(), "0".into())); // knife' \
   the_run_is_the_machines_receipt_run_whole_inside_the_sandbox
# slice 3b-2b (serve.rs, b4297): serving a request
sv() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/serve.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "serve::tests::$3"; }
# SV1 a run asked again is started a second time
sv '    if now != State::None {' '    if now == State::Done { // knife' \
   a_run_asked_again_is_told_how_it_stands_and_not_started
# SV2 one crate (or a knife table) is taken by the first version
sv '    if krate != "ws" || knives_len != 0 {' '    if krate != "ws" && knives_len != 0 { // knife' \
   the_first_version_takes_the_whole_workspace_and_no_knives
# SV3 a receipt-run that is not the pinned one runs
sv '    if sha256_of(Path::new(&cfg.receipt_run)).as_deref() != Some(cfg.receipt_run_sha256.as_str()) {' \
   '    if sha256_of(Path::new(&cfg.receipt_run)).is_none() { // knife' \
   a_machine_that_is_not_set_up_runs_nothing
# SV4 the run folder (tree, target) is left behind
sv '        l.work(run_id),' '        l.tmp(run_id), // knife' \
   cleaning_leaves_the_note_and_the_kept_out
# SV5 free disk is read from another column
sv '        .nth(3)?' '        .nth(2)? // knife' \
   df_is_read_by_its_free_column
