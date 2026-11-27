#!/usr/bin/env bash
# Windows claude relogin + not_logged_in before the window (08de5 11-26; Andy 02:0x「用那台既有的登入,過期或沒有一樣彈出綁定視窗」; c2f0b W-10; fc7f9 f40246bf6b): on Windows only claude logs in from the page; the login runs in the user's profile (USERPROFILE, any case, empty = none); a page open of a brand with no login is 409 not_logged_in with the brand, before any fingerprint window, and opens once the login is there. Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# wl-win-all (Windows refuses claude again)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '    if on_windows && !WINDOWS_LOGINS.contains(&p) {' \
  '    if on_windows {' \
  --expect-fail 'provider_login::tests::a_round_starts_only_in_the_parent_one_at_a_time' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::a_round_starts_only_in_the_parent_one_at_a_time'
# wl-win-none (Windows lets every brand log in, untried ones too)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '    if on_windows && !WINDOWS_LOGINS.contains(&p) {' \
  '    if on_windows && p == "none" {' \
  --expect-fail 'provider_login::tests::a_round_starts_only_in_the_parent_one_at_a_time' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::a_round_starts_only_in_the_parent_one_at_a_time'
# wl-profile (the Windows login runs in HOME, not the user's profile)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '    let name = if windows { "USERPROFILE" } else { "HOME" };' \
  '    let name = "HOME";' \
  --expect-fail 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows'
# wl-profile-case (the name read in one case only, as unix does)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '                k.to_str().is_some_and(|k| k.eq_ignore_ascii_case(name))' \
  '                k == name' \
  --expect-fail 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows'
# wl-profile-empty (an empty profile is taken as the folder)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '        .filter(|v| !v.is_empty())' \
  '        .filter(|_| true)' \
  --expect-fail 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::the_login_runs_in_the_users_profile_on_windows'
# wl-open-unchecked (a page opens a brand with no login: the window is asked, a seat written)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    if !tokio::task::spawn_blocking(move || crate::workspace::login_now(&brand))' \
  '    if false && !tokio::task::spawn_blocking(move || crate::workspace::login_now(&brand))' \
  --expect-fail a_brand_with_no_login_is_refused_before_any_window \
  -- cargo test -p aibd --locked --test seats_page_open_nologin a_brand_with_no_login_is_refused_before_any_window
# wl-open-code (the refusal's code is not the one the page reads)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        "code": "not_logged_in",' \
  '        "code": "conflict",' \
  --expect-fail a_brand_with_no_login_is_refused_before_any_window \
  -- cargo test -p aibd --locked --test seats_page_open_nologin a_brand_with_no_login_is_refused_before_any_window
# wl-open-brand (the refusal does not say which brand: the page cannot match it, f163c L1;
#   still reads `provider`, or -D warnings makes it a compile error = fake bite, 90856 stack11 receipt)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        "provider": provider,' \
  '        "provider": if provider.is_empty() { provider } else { "claude" },' \
  --expect-fail a_brand_with_no_login_is_refused_before_any_window \
  -- cargo test -p aibd --locked --test seats_page_open_nologin a_brand_with_no_login_is_refused_before_any_window
