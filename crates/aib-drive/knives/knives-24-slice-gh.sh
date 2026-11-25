#!/usr/bin/env bash
# aib-knives: os=unix
# aib-drive refresh: the GitHub reader named for aib-slice (a9136 11-19, 519d0 10:52 S107-auto: this Mac's
# gh pages took 5–18 s and the 600 s scan timed out). Each rule, broken, turns its own test red.
# Run from the repo root; -eo pipefail like GitHub's `shell: bash`.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
sg() { python3 -I scripts/knife.py crates/aib-drive/src/main.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-drive --locked --bin aib-drive "$3"; }
# SG1 the named reader is not passed to aib-slice
sg '        a.push("--gh".to_owned());' '        a.push("--repo".to_owned()); // knife' \
   refresh_asks_one_fast_scan_for_both_files
# SG2 a reader that is not there is named anyway
sg '    std::path::Path::new(&p).is_file().then_some(p)' '    Some(p) // knife' \
   the_slice_reader_is_named_only_when_it_is_there
