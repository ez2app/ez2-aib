#!/usr/bin/env bash
# TA1-TA6 (b4297 11-21; DESIGN-add-test-host-b4297-1121): the machines offered, the read of a
# machine, the pinned list, and the two fixed scripts. Every mutant keeps its names used.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ta() { python3 -I scripts/knife.py crates/aibd/src/test_host_add.rs "$1" "$2" \
  --expect-fail "test_host_add::tests::$3" \
  -- cargo test -p aibd --locked --lib "test_host_add::tests::$3"; }
# TA1 a pattern or an option is offered as a machine
ta '            if pick_ok(p) && !names.iter().any(|n| n == p) {' '            if (pick_ok(p) || !p.is_empty()) && !names.iter().any(|n| n == p) { // knife' \
   only_the_names_of_single_machines_are_offered
# TA2 any host key type is taken
ta '        (Some("ssh-ed25519"), Some(b)) => format!("ssh-ed25519 {b}"),' '        (Some(t), Some(b)) => format!("{t} {b}"), // knife' \
   the_read_gives_the_key_without_its_comment_and_refuses_a_machine_that_is_no_mac
# TA3 a machine added again is a second entry
ta '    if let Some(o) = out.iter_mut().find(|o| o.name == h.name) {' '    if let Some(o) = out.iter_mut().find(|o| o.name == h.name && o.port == 0) { // knife' \
   a_machine_is_added_once_replaced_by_name_and_taken_away
# TA4 the line is added again when it is there
ta 'grep -qxF -- "$L" "$F" || printf "%s\n" "$L" >> "$F" || exit 4' 'printf "%s\n" "$L" >> "$F" || exit 4' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# TA5 a file without its last newline gets the line glued on
ta 'if [ -s "$F" ] && [ -n "$(tail -c1 "$F")" ]; then printf "\n" >> "$F" || exit 4; fi' 'if false; then printf "\n" >> "$F" || exit 4; fi' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# TA6 the removal empties the file
ta 'grep -vF -- "$M" "$F" > "$T"' ': > "$T"' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# RH1 a machine reached through a ProxyJump / ProxyCommand is offered
ta '            if REFUSED.contains(&k.as_str()) && !v.eq_ignore_ascii_case("none") {' '            if REFUSED.contains(&k.as_str()) && v.is_empty() { // knife' \
   only_the_names_of_single_machines_are_offered
# RH1b a config with Match / Include is read as if it had none
ta '            "match" | "include" => whole_file_refused = true,' '            "match" | "include" => whole_file_refused = whole_file_refused || k.is_empty(), // knife' \
   only_the_names_of_single_machines_are_offered
# RH4 the removal takes another key's marked line too
ta 'M=" $K aibd-delegate"' 'M=" aibd-delegate"' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# TA7 a line of the key without aibd's mark is added
ta 'case "$L" in restrict,command=*" aibd-delegate") ;; *) exit 3;; esac' 'case "$L" in restrict,command=*) ;; *) exit 3;; esac' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# RH5 a key file in aibd's own delegate folder is taken as the owner's login (5d33a r1 N5)
ta '    if !own_keys.is_empty() && std::path::Path::new(&m.identity_file).starts_with(own) {' '    if !own_keys.is_empty() && std::path::Path::new(&m.identity_file).starts_with(own) && own_keys.is_empty() { // knife' \
   only_the_names_of_single_machines_are_offered
# RH6 a value with another expansion is taken half expanded (5d33a r1 N4)
ta "        .any(|v| v.is_empty() || v.starts_with('~') || v.contains(EXPANSIONS))" "        .any(|v| v.is_empty() || v.contains(EXPANSIONS)) // knife" \
   only_the_names_of_single_machines_are_offered
# RH2 the ticket is not bound to the port the window shows
ta '        "hostname": m.hostname, "port": m.port, "user": m.user,' '        "hostname": m.hostname, "port": 0, "user": m.user, // knife m.port' \
   the_window_and_its_ticket_hold_the_real_address_and_host_key
# RH2b the login reads the owner's ssh config
ta '    let mut a: Vec<String> = vec!["-F".into(), "none".into()];' '    let mut a: Vec<String> = vec!["-q".into(), "-q".into()]; // knife' \
   the_login_takes_no_config_and_spells_every_value_out
# RH3 a machine is pinned although the gate did not answer
ta '    if !gate_answered(status_out) {' '    if !gate_answered(status_out) && status_out.is_empty() { // knife' \
   a_machine_is_pinned_only_when_the_gate_answered_aibds_own_key
# TW-N1 the window shows a line whose from= the writer would refuse (5d33a ee27b561 N1)
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '                    .filter(|f| crate::receipt_link::from_ok(f))?;' \
  '                    .filter(|f| !f.is_empty())?; // knife' \
  --expect-fail "test_host_add::tests::the_window_and_its_ticket_hold_the_real_address_and_host_key" \
  -- cargo test -p aibd --locked --lib "test_host_add::tests::the_window_and_its_ticket_hold_the_real_address_and_host_key"
# TW-N2 a child keeps aibd's whole environment
python3 -I scripts/knife.py crates/aibd/src/test_host_web.rs \
  '    cmd.env_clear();' \
  '    cmd.env("AIBD_KNIFE", "1"); // knife' \
  --expect-fail "test_host_web::tests::a_child_keeps_no_environment_but_windows_system_names" \
  -- cargo test -p aibd --locked --lib "test_host_web::tests::a_child_keeps_no_environment_but_windows_system_names"
# TW-N3 the temporary list is written through a link someone put there
python3 -I scripts/knife.py crates/aibd/src/test_host_web.rs \
  '    match std::fs::remove_file(p) {' \
  '    match Ok::<(), std::io::Error>(()) { // knife' \
  --expect-fail "test_host_web::tests::the_list_is_written_beside_itself_and_none_left_is_no_file" \
  -- cargo test -p aibd --locked --lib "test_host_web::tests::the_list_is_written_beside_itself_and_none_left_is_no_file"
