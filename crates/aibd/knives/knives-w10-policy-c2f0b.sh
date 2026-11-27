#!/usr/bin/env bash
# aib-knives: os=unix   # the act's own test is cfg(unix) (the seat-input harness); the lib tests run anywhere
# WP1–WP10 (W-10, IFACE-w10-session-policy-c2f0b-1125 r5): the page writes the session policy only with native
# programs found by aibd, after a fresh owner check, never over a file already there, from a body that names
# nothing, and the policy is in force at once. Each, broken, turns its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function: one wrapper per file and test target
wi() { python3 -I scripts/knife.py crates/aibd/src/session_policy_setup.rs "$1" "$2" --expect-fail "$3" \
  -- cargo test -p aibd --locked --test session_policy_setup "$3"; }
wl() { python3 -I scripts/knife.py crates/aibd/src/session_policy_setup.rs "$1" "$2" --expect-fail "session_policy_setup::tests::$3" \
  -- cargo test -p aibd --locked --lib "session_policy_setup::tests::$3"; }
# WP1 a node script or text file named like the program is taken as native
wl '            match check(name, &exe) {' \
   '            match check(name, &exe).or(Ok::<Option<String>, (LookKind, String)>(None)) { // knife' \
   a_look_takes_only_a_native_executable
# WP2 an npm launcher (or anything found but not native) is written
wi '    for l in looks.iter().filter(|l| l.kind == LookKind::Native) {' \
   '    for l in looks.iter().filter(|l| l.path.is_some()) { // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP3 written without the owner's check
wi '        None => return crate::repos::asked(&ask),' \
   '        None => {} // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP4 written but not put in force until a restart
wi '            st.sessions.set_policy(policy.clone());' \
   '            let _ = &st.sessions; // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP5 the write replaces a file already there
wl '        .and_then(|_| std::fs::hard_link(&temporary, path));' \
   '        .and_then(|_| std::fs::rename(&temporary, path)); // knife' \
   write_new_never_replaces_a_file
# WP6 a body that names a path or the programs is taken
wi '    if o.keys().any(|k| k != "type" && k != "ticket") {' \
   '    if o.keys().any(|k| k == "never-a-field") { // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP7 (too strict) the resend with its ticket is refused as a bad body
wi '    if o.keys().any(|k| k != "type" && k != "ticket") {' \
   '    if o.keys().any(|k| k != "type") { // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP8 a key name aibd has set is written into the page's policy (Andy 11-26: use the login already there)
wl '    let env = allowlist::prefill_env(|k| !is_key(k) && is_set(k));' \
   '    let env = allowlist::prefill_env(|k| is_set(k) || is_key(k)); // knife' \
   a_policy_the_page_writes_names_no_key
# WP9 (3e53b L2) the owner approves a fixed word, not the text found: a look that finds more at the resend is written
wi '        params: text.clone().into_bytes(),' \
   '        params: b"session_policy".to_vec(), // knife' \
   the_page_writes_the_policy_once_after_a_fresh_owner_check
# WP10 (3e53b L1) a file swapped between the write and the read back is put in force
wl '    let same = std::fs::read(path).is_ok_and(|b| b == text.as_bytes());' \
   '    let same = std::fs::read(path).is_ok(); // knife' \
   only_the_approved_text_is_put_in_force
# WP11 (f163c L1) a temporary file that cannot be removed after the link is made fails the whole write
wl '    if let Err(e) = removed {' \
   '    if let Err(e) = removed { return Err(e); } if let Err(e) = std::io::Result::<()>::Ok(()) {' \
   a_temporary_file_held_by_someone_else_is_no_failure
