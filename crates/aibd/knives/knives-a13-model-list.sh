#!/usr/bin/env bash
# A13 (DESIGN-a13-model-list-08de5-1123 r1, f163c REVIEW-a13 r1): the page's model list, each brand from its own source
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a13-hidden
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '.filter(|m| m["visibility"] == "list")' \
  '.filter(|m| m["visibility"] != "x")' \
  --expect-fail 'model_list::tests::codex_lists_its_own_shown_models_only' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::codex_lists_its_own_shown_models_only'
# a13-shape
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '.filter(|s| id_ok(s))?;' \
  '.filter(|s| !s.is_empty())?;' \
  --expect-fail 'model_list::tests::codex_lists_its_own_shown_models_only' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::codex_lists_its_own_shown_models_only'
# a13-label
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '        .take(MAX_LABEL)' \
  '        .take(usize::MAX)' \
  --expect-fail 'model_list::tests::codex_lists_its_own_shown_models_only' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::codex_lists_its_own_shown_models_only'
# a13-follow
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  'nix::libc::O_NOFOLLOW | nix::libc::O_NONBLOCK' \
  'nix::libc::O_NONBLOCK' \
  --expect-fail 'model_list::tests::a_link_a_fifo_or_a_big_file_gives_nothing' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::a_link_a_fifo_or_a_big_file_gives_nothing'
# a13-cap
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '    if b.len() as u64 > MAX_BYTES {' \
  '    if b.len() as u64 > MAX_BYTES + 1 {' \
  --expect-fail 'model_list::tests::a_link_a_fifo_or_a_big_file_gives_nothing' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::a_link_a_fifo_or_a_big_file_gives_nothing'
# a13-default
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '    if let Some(d) = default.filter(|d| id_ok(d)) {' \
  '    if let Some(d) = default.filter(|d| id_ok(d) && d.is_empty()) {' \
  --expect-fail 'model_list::tests::the_default_comes_first_and_aliases_say_so' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::the_default_comes_first_and_aliases_say_so'
# a13-alias
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  'format!("{a}({ALIAS_WORDS})")' \
  'format!("{a}")' \
  --expect-fail 'model_list::tests::the_default_comes_first_and_aliases_say_so' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::the_default_comes_first_and_aliases_say_so'
# a13-only
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '            _ => (Vec::new(), "default_only"),' \
  '            _ => (vec![("x".to_owned(), "x".to_owned(), "codex_cache")], "codex_cache"),' \
  --expect-fail 'model_list::tests::no_codex_list_is_the_default_alone' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::no_codex_list_is_the_default_alone'
# a13-bidi (f163c REVIEW-a13code N1: a label keeps no bidirectional or zero-width character)
python3 -I scripts/knife.py crates/aibd/src/model_list.rs \
  '        .filter(|c| !c.is_control() && !format_char(*c))' \
  '        .filter(|c| !c.is_control() && !(format_char(*c) && c.is_control()))' \
  --expect-fail 'model_list::tests::a_label_has_no_invisible_turn' \
  -- cargo test -p aibd --locked --lib 'model_list::tests::a_label_has_no_invisible_turn'
