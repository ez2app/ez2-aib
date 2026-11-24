#!/usr/bin/env bash
# aib-knives: os=unix
# BR1–BR10 (7b81e 11-20; c12, Andy 11-19 22:2x「這個路徑 ~/Ez2/Vault-uid 也應該是 不寫死的」,
# DECISION-brain-root-c12-7b81e-1120): the second-brain library is found in a fixed order with no
# guessed folder, a bad setting stops there, the list comes from the library's own index, and a
# preview is handed the parent's library by path. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
rf() { python3 -I scripts/knife.py crates/aibd/src/registry.rs "$1" "$2" --expect-fail "registry::tests::the_brain_library_is_found_in_order_and_never_guessed" \
  -- cargo test -p aibd --locked --lib "registry::tests::the_brain_library_is_found_in_order_and_never_guessed"; }
rl() { python3 -I scripts/knife.py crates/aibd/src/registry.rs "$1" "$2" --expect-fail "registry::tests::the_library_lists_active_routed_folders_and_never_shows_a_key" \
  -- cargo test -p aibd --locked --lib "registry::tests::the_library_lists_active_routed_folders_and_never_shows_a_key"; }
pv() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" --expect-fail "preview::tests::a_preview_reads_its_parents_cards_and_gets_its_brain_candidates" \
  -- cargo test -p aibd --locked --lib "preview::tests::a_preview_reads_its_parents_cards_and_gets_its_brain_candidates"; }
# BR1 AIBD_BRAIN_ROOT is not looked at first
rf '    for var in ["AIBD_BRAIN_ROOT", "EZ2_VAULT_DIR", "EZ2_VAULT_ROOT"] {' \
   '    for var in ["EZ2_VAULT_DIR", "EZ2_VAULT_ROOT", "AIBD_BRAIN_ROOT"] { // knife'
# BR2 a bad setting falls through to the next one
rf '            return checked(var, &v);' \
   '            if let BrainRoot::At(..) = checked(var, &v) { return checked(var, &v); } // knife'
# BR3 a relative path is taken
rf '        if p.is_absolute() && p.is_dir() {' \
   '        if p.is_dir() || !p.is_absolute() { // knife'
# BR4 a folder under home is guessed when nothing is set
rf '        None => BrainRoot::Unset,' \
   '        None => checked("guess", &Path::new(&home).join("Ez2").join("Vault-uid").to_string_lossy()), // knife'
# BR5 vault-dir is read from the scratch HOME, not the real (seat) home
rf '    let home = lookup(crate::platform::session::SEAT_HOME_ENV)' \
   '    let home = lookup("AIBD_NO_SUCH_HOME") // knife'
# BR6 archived folders are listed
rl '        if i.get("status").and_then(Value::as_str) != Some("active") {' \
   '        if false { // knife'
# BR7 a route key listed twice shows twice
rl '        if out.iter().any(|c| c.route_key == key) {' \
   '        if false { // knife'
# BR8 no library reads as the demo
rl '    let mut b = candidates_body(Some(l.list()));' \
   '    let mut b = candidates_body(Some(l.list()).filter(|v| !v.is_empty())); // knife'
# BR9 a library with no index reads as an empty list, not unusable
rl '            index_candidates(&root).map_or(Listing::Unusable, Listing::Library)' \
   '            index_candidates(&root).map_or(Listing::Library(Vec::new()), Listing::Library) // knife'
# BR10 the preview is not handed the parent's library
pv '        crate::registry::BrainRoot::At(_, p) => Some(("AIBD_BRAIN_ROOT", p.clone())),' \
   '        crate::registry::BrainRoot::At(_, _) => None, // knife'
