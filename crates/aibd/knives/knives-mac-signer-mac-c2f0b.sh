#!/usr/bin/env bash
# aib-knives: os=macos
# MS4–MS5 Mac first run, the signer on macOS only (3e53b PLAN-mac-firstrun-c2f0b-1126 r2 乙 h3, r3): a seat opens a
# listed program only when the real file it runs is signed by the matched entry's signer (real codesign: a renamed copy
# of sh is refused); a Mac policy the page writes always names the signer. On Linux the check is not compiled in and
# no signer is written, so these cannot bite there. Each knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
# MS4 the seat-open signer check is gone
kn allowlist.rs '            signed_by(&real, team)?;' '            signed_by(&real, team).or(Ok::<(), Refused>(()))?;' allowlist::tests::unix::with_a_signer_a_renamed_copy_of_sh_is_refused
# MS5 a Mac policy without a signer is written
kn session_policy_setup.rs '            return None;' '            let _ = ();' session_policy_setup::tests::an_unsigned_program_is_never_written_and_a_mac_policy_names_its_signer
exit "$fail"
