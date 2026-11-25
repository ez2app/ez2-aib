#!/bin/bash
# aib-knives: os=macos   # 90856 11-26: on ubuntu a knife here missed (a_codex_work_seat_writes_its_own_folder_only; #895 run 45610010326)
# N-ws-codex-work knives CO1-CO4 (7ebca 11-20, COUNCIL-worktree-gitdir-7ebca-1120 Q3): aibd opens a
# codex work seat in its own folder only and adds the folder half of its profile at start.
set -eo pipefail
co() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail a_codex_work_seat_writes_its_own_folder_only \
        -- cargo test -p aibd --locked --test seat_codex a_codex_work_seat_writes_its_own_folder_only; }
# CO1 a codex work seat let into a given folder (shares a .git with another seat or the person)
co crates/aibd/src/seats.rs \
   '    if r.provider == "codex" && r.mode == "work" && r.cwd.is_some() {' \
   '    if r.provider == "codex" && r.mode == "work" && r.cwd.is_none() {'
# CO2 the folder half never added (the profile names no folder)
co crates/aibd/src/seats.rs \
   '    if mode == "work" && argv.first().is_some_and(|p| p == "codex") {' \
   '    if mode == "dgrs" && argv.first().is_some_and(|p| p == "codex") {'
# CO3 the folder not as the file system names it (the sandbox compares real paths)
co crates/aibd/src/seats.rs \
   '        let real = std::fs::canonicalize(cwd).unwrap_or_else(|_| std::path::PathBuf::from(cwd));' \
   '        let real = std::path::PathBuf::from(cwd);'
# CO4 the page's open opens a tree off the author's folder before refusing (an orphan tree)
co crates/aibd/src/seats.rs \
   '        && (req.with_seat.is_some() || req.repo.is_some() || req.own_tree)' \
   '        && (req.with_seat.is_some() || req.repo.is_some()) && !req.own_tree'
