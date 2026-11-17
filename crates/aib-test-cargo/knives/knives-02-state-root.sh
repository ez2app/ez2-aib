#!/usr/bin/env bash
# aib-test-rs ledger folder (043dd S69-u1, after #833): the ledger follows the aib state root the way
# aib-drive finds it (EZ2_AIB_STATE_ROOT, EZ2_AIB_HOME, ~/.ez2-aib). Each rule, broken, turns its own test
# red. RULES §21: knives in crates/<crate>/knives/. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
sk() { python3 -I scripts/knife.py "crates/aib-test-cargo/src/main.rs" "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-test-cargo --locked --bin aib-test-rs "$3"; }
# SR1 EZ2_AIB_STATE_ROOT is not read
sk '    let aib = if let Some(r) = env_root.filter(|r| !r.is_empty()) {' \
   '    let aib = if let Some(r) = env_root.filter(|_| false) { // knife' \
   the_ledger_follows_the_aib_state_root_like_aib_drive
# SR2 an empty EZ2_AIB_HOME counts as set
sk '    } else if let Some(h) = aib_home.filter(|h| !h.is_empty()) {' \
   '    } else if let Some(h) = aib_home { // knife' \
   the_ledger_follows_the_aib_state_root_like_aib_drive
# SR3 --state is not taken as given
sk '        return s.to_path_buf();' '        let _ = s; // knife' \
   the_ledger_follows_the_aib_state_root_like_aib_drive
