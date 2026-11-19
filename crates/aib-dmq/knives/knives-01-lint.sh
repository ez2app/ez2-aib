#!/usr/bin/env bash
# B1dm-lint knives DL1–DL5 (b4297 11-18, 519d0 07:34): the R-30 and calling-position checks
# translated from r30_lint.py / vocative_lint.py keep the old answers. Each rule, broken, turns
# its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
dl() { python3 -I scripts/knife.py crates/aib-dmq/src/lint.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-dmq --locked --test lint "$3"; }
# DL1 the tree connectors of the canonical format are refused too
dl '        ALLOW_CODEPOINTS.iter().any(|(c, _)| *c == cp)' \
   '        ALLOW_CODEPOINTS.iter().any(|(c, _)| *c == cp) && false // knife' \
   the_exception_is_what_the_canonical_format_uses
# DL2 full-width letters get no ASCII suggestion
dl '            0xFF10..=0xFF19 | 0xFF21..=0xFF3A | 0xFF41..=0xFF5A => shift(ch, 0xFEE0, 0),' \
   '            0xFF10..=0xFF19 | 0xFF21..=0xFF3A | 0xFF41..=0xFF5A => shift(ch, 0xFEE0, 0).filter(|_| false), // knife' \
   r30_ranges_and_suggestions
# DL3 the enumeration comma no longer opens a calling position
dl '    pub const PREFIX: &str = "\n \u{3000}，。:;：；？！、（）,.?!()";' \
   '    pub const PREFIX: &str = "\n \u{3000}，。:;：；？！（）,.?!()"; // knife' \
   the_vocative_selftest_cases_hold
# DL4 code and quotes are not left out
dl '                    && !spans.iter().any(|(s, e)| s <= off && off < e)' \
   '                    && !spans.iter().any(|(s, e)| s <= off && off < e && false) // knife' \
   the_vocative_selftest_cases_hold
# DL5 a quoted line is checked
dl "                if t.get(start) == Some(&'>') {" \
   "                if t.get(start) == Some(&'>') && false { // knife" \
   the_same_answers_as_the_python_checks
# DL6 the full-width question mark no longer opens a calling position (5d33a L1)
dl '    pub const PREFIX: &str = "\n \u{3000}，。:;：；？！、（）,.?!()";' \
   '    pub const PREFIX: &str = "\n \u{3000}，。:;：；！、（）,.?!()"; // knife' \
   every_prefix_character_opens_a_calling_position
