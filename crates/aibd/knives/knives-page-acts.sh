#!/usr/bin/env bash
# PA1–PA3 (1a93b 11-20, 519d0 22:34 GAP-web-backend §8): GET /api/v1/version lists every act type a
# confirmed page may send (capabilities.acts = seat_input::PAGE_ACTS), and that list is exactly the
# page match's arms. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per file
pa() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
pw() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# PA1 an act the page match takes is left out of the list (the page would hide a working button)
pa '    crate::nodes::SET,' \
   '    // knife' \
   every_page_act_arm_is_listed
# PA2 a type no arm takes is listed (the page would show a button that answers unknown_act_type)
pa '    crate::seat_send::RESUME,' \
   '    crate::seat_send::RESUME, "seat_clear", // knife' \
   every_page_act_arm_is_listed
# PA3 the version body gives some other list than PAGE_ACTS
pw '            "acts": crate::seat_input::PAGE_ACTS,' \
   '            "acts": [crate::seat_input::KIND], // knife' \
   version_names_what_opens_and_what_a_page_types_into
# HO1–HO4 (1a93b 11-20, GAP-web-backend §6): a seat says its handoff note is written
# (act seat_handoff {path}); aibd keeps one absolute .md path per seat, shows it on the built-in
# page, hashes it for a user folder page, and forgets it when the seat stops.
ho() { python3 -I scripts/knife.py crates/aibd/src/seat_now.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# HO1 a stopped seat's handoff stays on the board
ho '        g.handoffs.retain(|k, _| k != seat_id);' \
   '        // knife' \
   a_handoff_is_shown_to_the_built_in_page_and_hashed_for_a_user_page_and_forgotten_on_stop
# HO2 a relative path is taken
ho "    let absolute = p.starts_with('/') || p.starts_with(\"\\\\\\\\\") || drive;" \
   '    let absolute = true; // knife' \
   a_handoff_is_one_absolute_md_path_and_nothing_else
# HO3 a path that is not a .md is taken
ho '    if !absolute || !p.to_ascii_lowercase().ends_with(".md") {' \
   '    if !absolute { // knife' \
   a_handoff_is_one_absolute_md_path_and_nothing_else
# HO4 a user folder page reads the path itself
ho '        json!({"len": h.path.chars().count(), "sha256": sha(&h.path)})' \
   '        json!(h.path) // knife' \
   a_handoff_is_shown_to_the_built_in_page_and_hashed_for_a_user_page_and_forgotten_on_stop
# NI1–NI4 (1a93b 11-20, GAP-web-backend §1/§2, 519d0 「idempotency_key 要真的擋重送」): node_add /
# node_set take an idempotency_key kept in the store, scoped to the act and the page, bound to the
# body without the key.
ni() { python3 -I scripts/knife.py crates/aibd/src/nodes.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
nt() { python3 -I scripts/knife.py crates/aibd/src/nodes.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
# NI1 the same key and body writes a second item
nt '                    return Ok(again);' \
   '                    let _ = again; // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
# NI2 another body under the same key replays the first answer
ni '        request: crate::idem::request_digest(&rest),' \
   '        request: crate::idem::request_digest(&json!({})), // knife' \
   an_idempotency_key_is_bound_to_the_act_the_page_and_the_body_without_it
# NI3 two pages share one key's answer
ni '        scope: format!("act {kind} {by}"),' \
   '        scope: format!("act {kind}"), // knife' \
   an_idempotency_key_is_bound_to_the_act_the_page_and_the_body_without_it
# NI4 a refused write keeps the key: the retry after the fix is refused as a conflict
nt '                Err(_) => st.store.idem_release(k).is_ok(),' \
   '                Err(_) => true, // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
