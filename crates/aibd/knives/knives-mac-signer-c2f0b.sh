#!/usr/bin/env bash
# aib-knives: os=unix
# MS1–MS3 Mac first run, the signer (3e53b PLAN-mac-firstrun-c2f0b-1126 r1 f g, r2 h3 h4): the page writes only a native
# program its maker signed; a Team ID is 10 capitals or digits before it goes into a codesign requirement; the terminal
# and the page check the same Team IDs. Each knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
# MS1 an unsigned native program is written
kn session_policy_setup.rs '        Kind::Native => signed(name, &real).map_err(|why| (LookKind::Unsigned, why)),' '        Kind::Native => Ok(signed(name, &real).ok().flatten()),' session_policy_setup::tests::an_unsigned_program_is_never_written_and_a_mac_policy_names_its_signer
# MS2 any non-empty text is taken as a Team ID
kn allowlist.rs '    t.len() == 10' '    !t.is_empty()' allowlist::tests::a_team_id_is_ten_capitals_or_digits
# MS3 codex checked against another team
kn provider_maker.rs '("codex", "2DC432GLL2")' '("codex", "Q6L2SF6YDW")' provider_maker::tests::a_policy_program_has_its_makers_team_id
exit "$fail"
