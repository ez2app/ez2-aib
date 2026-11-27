#!/usr/bin/env bash
# aib-knives: os=unix
# MF1–MF4 Mac first run, pairing key (ebab8 11-26 第四包; 3e53b PLAN-mac-firstrun-c2f0b-1126 r1 a–c): aibd install makes
# the pairing key itself, of the kind the owner check reads now, never a password key when Touch ID cannot be used now;
# a key already there must pass aibd's start-up check; a failure installs nothing. Each knife keeps the code
# warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
# MF1 a key already there is taken without the start-up check
kn touch.rs '        check(dev, &k).map_err(|why| key_there_failed(dir, why))?;' '        let _ = (dev, &k);' touch::tests::install_makes_the_pairing_key_or_checks_the_one_there
# MF2 Touch ID not usable now makes a login-password key instead
kn touch.rs '    let k = kind().map_err(|why| format!("{why}: {}", pair_key_not_now_words(why)))?;' '    let k = kind().unwrap_or(crate::owner_probe::KeyKind::LoginPassword);' touch::tests::install_makes_the_pairing_key_or_checks_the_one_there
# MF3 the key folder is open to others
kn touch.rs '            b.mode(0o700);' '            b.mode(0o755);' touch::tests::install_makes_the_pairing_key_or_checks_the_one_there
# MF4 a failed pairing key does not stop the install
kn install.rs '            Err(why) => return Err(format!("pairing key: {why}")),' '            Err(why) => out.say(why),' install::tests::a_pairing_key_that_fails_installs_nothing
exit "$fail"
