#!/usr/bin/env bash
# A14 (DESIGN-a14-modeline-08de5-1123 r0, f163c REVIEW-a14 r0): every seat is told its mode at open, the page and the seat say the same network
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a14-same
python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs \
  '        "safe" => format!("你這一席是【安全】模式:只能聊天,不能改檔、不能跑指令;{net}。"),' \
  '        "safe" => format!("你這一席是【安全】模式:只能聊天,不能改檔、不能跑指令;可以上網。{}", net.len()),' \
  --expect-fail 'workspace::tests::the_page_and_the_seat_say_the_same_network' \
  -- cargo test -p aibd --locked --lib 'workspace::tests::the_page_and_the_seat_say_the_same_network'
# a14-head
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    let mut t = aib_seatopen::mode_note(mode, crate::workspace::work_net_blocked());' \
  '    let mut t = String::new();' \
  --expect-fail 'seats::tests::a_seat_is_told_its_mode_then_its_commands_before_its_card' \
  -- cargo test -p aibd --locked --lib 'seats::tests::a_seat_is_told_its_mode_then_its_commands_before_its_card'
# a14-codex
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    let mut t = seat_prompt_head(mode);' \
  '    let mut t = seat_prompt_head(&mode[..0]);' \
  --expect-fail 'seats::tests::a_codex_seat_is_told_its_mode_in_thread_start' \
  -- cargo test -p aibd --locked --lib 'seats::tests::a_codex_seat_is_told_its_mode_in_thread_start'
# a14-page
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '            "mode_net": mode_net(),' \
  '            "mode_net": json!({}),' \
  --expect-fail 'workspace::tests::the_page_and_the_seat_say_the_same_network' \
  -- cargo test -p aibd --locked --lib 'workspace::tests::the_page_and_the_seat_say_the_same_network'
