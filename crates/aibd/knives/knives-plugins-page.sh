#!/usr/bin/env bash
# The plugin area on the page (PLAN-skipstop-master H1b, c5d88 11-23): each rule loosened must
# turn its own test red. From the repo root; -eo pipefail like GitHub's shell.
set -euo pipefail
pp() { python3 -I scripts/knife.py crates/aibd/src/plugins_page.rs "$1" "$2" --expect-fail "plugins_page::tests::$3" \
        -- cargo test -p aibd --locked --lib "plugins_page::tests::$3"; }
# PPK1 the body may carry more than the name and the switch
pp '        .any(|k| !matches!(k.as_str(), "type" | "name" | "on"))' '        .any(|k| k.is_empty())' the_switch_is_moved_for_an_installed_plugin_only_and_the_user_s_word_wins
# PPK2 a name that could be a path is taken
pp '    aib_plugin::name_ok(name).then(|| (name.to_string(), on))' '    Some((name.to_string(), on))' the_switch_is_moved_for_an_installed_plugin_only_and_the_user_s_word_wins
# PPK3 a switch is written for a name that is no installed plugin
pp '        .any(|f| f.manifest.as_ref().is_ok_and(|m| m.name == name))' '        .any(|f| f.manifest.as_ref().is_ok_and(|m| m.name == name) || f.manifest.is_ok())' the_switch_is_moved_for_an_installed_plugin_only_and_the_user_s_word_wins
# PPK4 a folder whose file is not understood is left out of the list
pp '        .map(|f| match &f.manifest {' '        .filter(|f| f.manifest.is_ok()).map(|f| match &f.manifest {' the_list_names_every_folder_with_its_switch_and_whether_it_runs
# PPK5 with a switches file that cannot be read a plugin is still shown as on or off
pp '                    "on": switches.as_ref().ok().map(|s| aib_plugin::switched_on(m, s)),' '                    "on": Some(switches.as_ref().is_ok_and(|s| aib_plugin::switched_on(m, s))),' the_list_names_every_folder_with_its_switch_and_whether_it_runs
si() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "seat_input::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_input::tests::$3"; }
# PPK6 a user folder page may switch a plugin
si '            let not_this_built_in_page = held_as_user_page(&page).await || page.carried;' '            let not_this_built_in_page = page.carried;' a_plugin_is_switched_from_the_built_in_page_in_this_pairing_only
# PPK7 the same, asked over HTTP: a carried pairing or a user folder page is let through
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs '            if not_this_built_in_page {' '            if not_this_built_in_page && page.carried {' \
  --expect-fail the_page_gets_the_plugin_s_boxes_and_nobody_else_does \
  -- cargo test -p aibd --locked --test time_saved_page
