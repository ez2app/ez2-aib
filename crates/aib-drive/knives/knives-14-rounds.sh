#!/usr/bin/env bash
# The "aib-drive rounds knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/;IFACE-drive-shadow-519d0-1117 §5; #804's shape). aibd.yml runs every
# crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors reads them.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79-own o5 rounds ledger (b433b): who started a round and its pacing; each knife takes one check away.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
nk() { python3 -I scripts/knife.py crates/aib-drive/src/rounds.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test rounds "$3"; }
# RN1 the transcript's trigger is not used
nk '        (None, Some(t)) => (t, "transcript"),' '        (None, Some(_)) => (TRIGGER_UNKNOWN, "none"), // knife' \
   a_round_line_carries_counts_own_dms_and_the_transcripts_trigger
# RN2 none without a why is written
nk '        (true, None) => return Err("--own none 要帶 --why \"<這輪為什麼沒做>\"".into()),' '        (true, None) => None, // knife' \
   a_round_with_nothing_done_needs_a_why_and_is_written
# RN4 a second round done in the same turn is written
nk '        return Err(format!("這一輪({turn_id})已經記過了;一輪一筆"));' '        // knife' \
   a_turn_is_written_once
# RN5 a round whose starter cannot be told is counted as a person
nk '            _ => unknown += 1,' '            _ => { human += 1; unknown += 0 } // knife' \
   the_log_lists_every_round_and_counts_the_ones_it_cannot_tell
