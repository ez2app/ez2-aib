#!/usr/bin/env bash
# aib-knives: os=unix
# U2 (PLAN-win-upgrade-path-90856-1126; 3e53b 11-26 11:2x): which running aibd.exe the Windows
# setup may end. winstop::pick is plain Rust, tested on every platform; each knife must turn its
# own test red. 3e53b's three: another path / another user / two matches ended ⇒ red.
# Every knife keeps the code warning-free (RUSTFLAGS has -D warnings in CI). Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ws() { python3 -I scripts/knife.py crates/aib-sys/src/winstop.rs "$1" "$2" --expect-fail "winstop::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "winstop::tests::$3"; }
# WP1 (3e53b knife 1) a copy at another path is ended
ws '        .filter(|(_, s)| s.path == path && s.sid == sid && s.session == session)' \
   '        .filter(|(_, s)| !path.as_os_str().is_empty() && s.sid == sid && s.session == session)' \
   a_copy_at_another_path_is_not_picked
# WP2 (3e53b knife 2) another user's copy is ended
ws '        .filter(|(_, s)| s.path == path && s.sid == sid && s.session == session)' \
   '        .filter(|(_, s)| s.path == path && !sid.is_empty() && s.session == session)' \
   another_users_copy_is_not_picked
# WP3 a copy in another session is ended
ws '        .filter(|(_, s)| s.path == path && s.sid == sid && s.session == session)' \
   '        .filter(|(_, s)| s.path == path && s.sid == sid && (s.session == session || session > 0))' \
   a_copy_in_another_session_is_not_picked
# WP4 (3e53b knife 3) two matching copies: the first is ended
ws '        many => Pick::Many(many.len()),' \
   '        many => Pick::One(many[0]),' \
   two_matching_copies_end_none
