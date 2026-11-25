#!/usr/bin/env bash
# aib-knives: os=unix
# A1 (DESIGN-a1-edit-archive-08de5-1123 r0, f163c REVIEW-a1 r0 + pre): persona sections changed with the owner, brains and roles renamed, archived and restored
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a1-heading
python3 -I scripts/knife.py crates/aibd/src/persona_card.rs \
  '    ((1..=6).contains(&hashes) && t[hashes..].starts_with(['"'"' '"'"', '"'"'\t'"'"'])) || t.trim_end() == "---"' \
  '    hashes == 2 && t[hashes..].starts_with('"'"' '"'"')' \
  --expect-fail persona_edit::tests::a_heading_or_a_rule_line_in_a_section_is_refused \
  -- cargo test -p aibd --locked --lib persona_edit::tests::a_heading_or_a_rule_line_in_a_section_is_refused
# a1-hidden
python3 -I scripts/knife.py crates/aibd/src/persona_card.rs \
  '    if b.chars().any(unseen) {' \
  '    if b.is_empty() && b.chars().any(unseen) {' \
  --expect-fail persona_edit::tests::a_heading_or_a_rule_line_in_a_section_is_refused \
  -- cargo test -p aibd --locked --lib persona_edit::tests::a_heading_or_a_rule_line_in_a_section_is_refused
# a1-tail
python3 -I scripts/knife.py crates/aibd/src/persona_card.rs \
  '            let tail = if e == text.len() { "\n" } else { "\n\n" };' \
  '            let tail = if e == text.len() { "\n" } else { "\n" };' \
  --expect-fail persona_card::tests::a_section_is_replaced_and_nothing_else_moves \
  -- cargo test -p aibd --locked --lib persona_card::tests::a_section_is_replaced_and_nothing_else_moves
# a1-only
python3 -I scripts/knife.py crates/aibd/src/persona_edit.rs \
  '        if before != clean {' \
  '        if before != clean || clean.is_empty() || !clean.is_empty() {' \
  --expect-fail persona_edit::tests::only_the_sections_sent_change_and_the_window_says_which \
  -- cargo test -p aibd --locked --lib persona_edit::tests::only_the_sections_sent_change_and_the_window_says_which
# a1-window
python3 -I scripts/knife.py crates/aibd/src/persona_edit.rs \
  '        if !card::SECTIONS.iter().any(|(_, h)| *h == name) {' \
  '        if card::SECTIONS.is_empty() {' \
  --expect-fail persona_edit::tests::the_window_says_only_known_sections \
  -- cargo test -p aibd --locked --lib persona_edit::tests::the_window_says_only_known_sections
# a1-taken
python3 -I scripts/knife.py crates/aibd/src/persona_edit.rs \
  '            if taken {' \
  '            if taken && name.is_none() {' \
  --expect-fail archived_ones_leave_the_picker_and_open_nothing_until_restored \
  -- cargo test -p aibd --locked --test persona_edit_acts archived_ones_leave_the_picker_and_open_nothing_until_restored
# a1-builtin
python3 -I scripts/knife.py crates/aibd/src/persona_edit.rs \
  '    if kind == "role"' \
  '    if kind == "none"' \
  --expect-fail archived_ones_leave_the_picker_and_open_nothing_until_restored \
  -- cargo test -p aibd --locked --test persona_edit_acts archived_ones_leave_the_picker_and_open_nothing_until_restored
# a1-list
python3 -I scripts/knife.py crates/aibd/src/registry.rs \
  '        .partition(|r| r["uid"].as_str().is_some_and(|u| gone.contains(u)));' \
  '        .partition(|r| r["uid"].as_str().is_some_and(|u| gone.is_empty() && u.is_empty()));' \
  --expect-fail archived_ones_leave_the_picker_and_open_nothing_until_restored \
  -- cargo test -p aibd --locked --test persona_edit_acts archived_ones_leave_the_picker_and_open_nothing_until_restored
# a1-open
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        return Refusal(why, "it is archived: restore it first").into_response();' \
  '        let _ = Refusal(why, "it is archived: restore it first");' \
  --expect-fail archived_ones_leave_the_picker_and_open_nothing_until_restored \
  -- cargo test -p aibd --locked --test persona_edit_acts archived_ones_leave_the_picker_and_open_nothing_until_restored
