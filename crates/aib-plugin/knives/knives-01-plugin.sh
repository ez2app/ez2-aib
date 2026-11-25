#!/usr/bin/env bash
# aib-knives: os=unix
# The plugin area (aib-plugin; Andy 11-23: one fixed place for plugins, on by default, can be turned off).
# Each rule of "when is a plugin's command run", broken, turns its own test in tests/plugin.rs red. Run on
# macOS (the header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust). Run from
# the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
pk() { python3 -I scripts/knife.py crates/aib-plugin/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plugin --locked --test plugin "$3"; }
# PG1 a plugin that is switched off is run
pk '    if !switched_on(m, set) {' '    if !switched_on(m, set) && false { // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG2 a paid plugin is run without the licence being read
pk '    if m.paid {' '    if m.paid && false { // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG3 a file changed after it was installed is run
pk '    if &got != want {' '    if &got != want && false { // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG4 a switches file that cannot be read is told as "switched off" (the user would look for a switch
# they never set instead of the broken file)
pk '        .map_err(|e| No::SwitchesUnread(e.clone()))?;' '        .map_err(|_| No::Off)?; // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG5 a file that says another name than its folder is read as that plugin
pk '    if name != folder {' '    if name != folder && false { // knife' \
   a_plugin_file_is_read_whole_under_its_own_folders_name_or_not_at_all
# PG6 an executable outside the plugin'"'"'s folder is taken
pk '        if !inside(&bin) {' '        if !inside(&bin) && false { // knife' \
   a_plugin_file_is_read_whole_under_its_own_folders_name_or_not_at_all
# PG7 the user'"'"'s switch loses to the plugin'"'"'s own default
pk '    set.get(&m.name).copied().unwrap_or(m.default_on)' '    m.default_on || set.get(&m.name).copied().unwrap_or(false) // knife' \
   the_switch_is_what_the_user_set_else_the_plugins_default
# PG8 a folder whose file cannot be read drops out of the list
pk '        out.push(Found { folder, manifest });' '        if manifest.is_ok() { out.push(Found { folder, manifest }); } // knife' \
   every_folder_is_listed_and_an_unreadable_one_says_why
# PG9 an executable a link leads out of the plugin's folder is run when its sum fits
pk '    if !real.starts_with(&root) {' '    if !real.starts_with(&root) && false { // knife' \
   an_executable_a_link_leads_out_of_the_folder_is_not_run_whatever_its_sum
# PG10 a key that comes twice is read (the later one wins without a word)
pk '                    if m.insert(k.clone(), v).is_some() {' '                    if m.insert(k.clone(), v).is_some() && false { // knife' \
   a_plugin_file_is_read_whole_under_its_own_folders_name_or_not_at_all
# PG11 on Windows the executable is looked for without .exe
pk '        name.push(".exe");' '        name.push(""); // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG12 a lock that could not be taken is ignored: the switch is written while another holds it
pk '        .map_err(|e| format!("{e}; nothing is changed"))?;' '        .map_err(|e| format!("{e}; nothing is changed")); // knife: not waited for' \
   a_switch_is_set_whole_and_a_name_that_is_no_plugin_is_said
# PG13 a folder (or a pipe) at the executable's path is read as if it were the program
pk '    if !real.metadata().is_ok_and(|m| m.is_file()) {' '    if !real.metadata().is_ok_and(|m| m.is_file()) && false { // knife' \
   a_command_runs_only_when_every_question_is_answered_yes_and_each_no_has_its_word
# PG14 a switch for a folder that holds no readable plugin is not said to be stray
pk '                .any(|f| f.manifest.as_ref().is_ok_and(|m| &m.name == *k))' '                .any(|f| &f.folder == *k) // knife' \
   a_switch_is_set_whole_and_a_name_that_is_no_plugin_is_said
