#!/usr/bin/env bash
# Grok / Copilot login from the page (08de5 11-25, ebab8 condition 5; stacked on ae921 685ec7261d): the login words as each CLI prints them, logged in judged by more than a file (grok: its own status words; copilot: a user named in its config, the token in the credential store), the page handed the address and the code only. Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# o-grok-host (grok's sign-in address may be on any x.ai host)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '    ("grok", "accounts.x.ai"),' \
  '    ("grok", "x.ai"),' \
  --expect-fail 'provider_login::tests::only_the_providers_own_sign_in_address_is_handed_on' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::only_the_providers_own_sign_in_address_is_handed_on'
# o-cop-words (copilot logs in with other words than its device login)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '        "copilot" => Some(&["login", "--device-code"]),' \
  '        "copilot" => Some(&["login", "--web-flow"]),' \
  --expect-fail 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words'
# o-grok-status (grok has no status words: logged in falls back to nothing)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '        "grok" => Some(&["models"]),' \
  '        "grok" => None,' \
  --expect-fail 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words'
# o-grok-exit (grok exit 0 counts as logged in)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '        "grok" => code == Some(0) && grok_says_logged_in(&out),' \
  '        "grok" => code == Some(0),' \
  --expect-fail 'provider_login::tests::a_grok_or_copilot_login_hands_on_the_address_and_the_code_only' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::a_grok_or_copilot_login_hands_on_the_address_and_the_code_only'
# o-grok-line (the words anywhere in the output count)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '        .find(|l| !l.trim().is_empty())' \
  '        .find(|l| l.contains("You are logged in"))' \
  --expect-fail 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::grok_and_copilot_logins_are_read_from_their_own_words'
# o-cop-code (copilot address handed on before its code)
python3 -I scripts/knife.py crates/aibd/src/provider_login.rs \
  '            if whole && (!matches!(provider, "codex" | "grok" | "copilot") || code.is_some()) {' \
  '            if whole && (!matches!(provider, "codex" | "grok") || code.is_some()) {' \
  --expect-fail 'provider_login::tests::a_grok_or_copilot_login_hands_on_the_address_and_the_code_only' \
  -- cargo test -p aibd --locked --lib 'provider_login::tests::a_grok_or_copilot_login_hands_on_the_address_and_the_code_only'
