#!/usr/bin/env bash
# aib-knives: os=macos   # 90856 11-25 E3 r2: a test its knives expect red already fails on ubuntu unmutated (false bite there)
# PP1–PP11 (7b81e 11-19; 519d0 21:26, Andy could not open a seat in the parent's preview): a preview runs under the
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
ps_() { python3 -I scripts/knife.py crates/aibd/src/personas.rs "$1" "$2" --expect-fail "personas::tests::$3" \
  -- cargo test -p aibd --locked --lib "personas::tests::$3"; }
# PP1 the preview is started without the policy
pe '    hand_policy(&data, parent_policy().as_deref())?;' \
   '    hand_policy(&data, parent_policy().filter(|_| false).as_deref())?; // knife' \
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
# PP6–PP9 (519d0 21:32, 0eed7 EVID-mom-preview-0eed7-493aea977): the preview reads the persona cards its parent
# reads, and gets the parent's brain candidates
# PP6 aibd never says which cards it reads
se '    crate::preview::set_parent_cards(cfg.persona_cards.clone());' \
   '    let _ = &cfg.persona_cards; // knife' \
   the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data
# PP7 the preview is started without the parent's cards
pe '    if let Some((k, v)) = cards_env(PARENT_CARDS.get().and_then(Option::as_deref)) {' \
   '    if let Some((k, v)) = cards_env(None) { // knife' \
   the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data
# PP8 the brain candidates stay behind
pu '    let Some(text) = parent.and_then(|p| std::fs::read(p).ok()) else {' \
   '    let Some(text) = parent.and_then(|_| None::<Vec<u8>>) else { // knife' \
   a_preview_reads_its_parents_cards_and_gets_its_brain_candidates
# PP9 a relative cards path is handed on
pu '        .filter(|p| p.is_absolute())' \
   '        .filter(|_| true) // knife' \
   a_preview_reads_its_parents_cards_and_gets_its_brain_candidates
# PP10 a cards path that is not there is handed on (the preview would read no cards at all)
pu '        .filter(|p| p.is_file())' \
   '        .filter(|_| true) // knife' \
   a_preview_reads_its_parents_cards_and_gets_its_brain_candidates
# PP11 the preview's parent reads cards from its scratch HOME, not the person's real home
ps_ '        .filter(|h| Path::new(h).is_absolute())' \
    '        .filter(|h| Path::new(h).is_absolute() && false) // knife' \
    the_default_path_follows_the_environment
