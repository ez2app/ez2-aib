#!/usr/bin/env bash
# PP1–PP5 (7b81e 11-19; 519d0 21:26, Andy could not open a seat in the parent's preview): a preview runs under the
# session policy its parent runs under, wherever that came from, owner-only; with none in effect, the preview's own
# file is left alone.
# Each, broken, turns its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function: one wrapper per file and test target
pe() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" --expect-fail "$3" \
  -- cargo test -p aibd --locked --test preview "$3"; }
se() { python3 -I scripts/knife.py crates/aibd/src/server.rs "$1" "$2" --expect-fail "$3" \
  -- cargo test -p aibd --locked --test preview "$3"; }
pu() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" --expect-fail "preview::tests::$3" \
  -- cargo test -p aibd --locked --lib "preview::tests::$3"; }
au() { python3 -I scripts/knife.py crates/aibd/src/allowlist.rs "$1" "$2" --expect-fail "preview::tests::$3" \
  -- cargo test -p aibd --locked --lib "preview::tests::$3"; }
# PP1 the preview is started without the policy
pe '    hand_policy(&data, PARENT_POLICY.get().map(Arc::as_ref))?;' \
   '    hand_policy(&data, None)?; // knife' \
   the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data
# PP2 aibd never says which policy it runs under
se '    crate::preview::set_parent_policy(session_policy.clone());' \
   '    let _ = session_policy.clone(); // knife' \
   the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data
# PP3 an empty policy here wipes the one a person put in the preview
pu '    let Some(p) = policy.filter(|p| !p.programs.is_empty()) else {' \
   '    let Some(p) = policy else { // knife' \
   a_preview_gets_the_policy_its_parent_runs_under
# PP4 the copy is readable by others
pu '    std::os::unix::fs::OpenOptionsExt::mode(&mut o, 0o600);' \
   '    std::os::unix::fs::OpenOptionsExt::mode(&mut o, 0o644); // knife' \
   a_preview_gets_the_policy_its_parent_runs_under
# PP5 the signer is dropped from the copy
au '                    o["signer"] = serde_json::json!(s);' \
   '                    o["signer"] = serde_json::json!(null); let _ = s; // knife' \
   a_preview_gets_the_policy_its_parent_runs_under
