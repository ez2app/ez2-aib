#!/usr/bin/env bash
# A14 (DESIGN-a14-modeline-08de5-1123 r0, f163c REVIEW-a14 r0): every seat is told its mode at open, the page and the seat say the same network
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a14-name
python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs \
  '        "safe" => format!("你這一席是【安全】模式:只能聊天,不能改檔、不能跑指令;{net}。"),' \
  '        "safe" => format!("只能聊天,不能改檔、不能跑指令;{net}。"),' \
  --expect-fail 'tests::each_mode_is_told_by_name_with_its_network' \
  -- cargo test -p aib-seatopen --locked --lib 'tests::each_mode_is_told_by_name_with_its_network'
