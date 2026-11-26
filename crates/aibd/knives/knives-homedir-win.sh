#!/usr/bin/env bash
# home_dir on Windows: USERPROFILE before HOME (08de5 11-26; STEPS-win-claude-relogin r2 D, c2f0b REVIEW-winlogin-87d4dc30 N1, ebab8 03:4x): the page's login runs in the profile and claude keeps its login there, so "is there a login" reads the profile first; HOME (Git for Windows) only as a fallback. The test runs on every platform (home_dir_here takes the platform). Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# HD1 Windows reads HOME first again
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        ["USERPROFILE", "HOME"]' \
  '        ["HOME", "USERPROFILE"]' \
  --expect-fail 'seats::tests::on_windows_the_profile_comes_before_home' \
  -- cargo test -p aibd --locked --lib 'seats::tests::on_windows_the_profile_comes_before_home'
