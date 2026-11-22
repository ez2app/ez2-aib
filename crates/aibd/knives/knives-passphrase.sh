#!/usr/bin/env bash
# PS1–PS9: the owner's passphrase record — costs below the floor or another KDF are refused, the comparison is whole, a short or personal sentence is not taken, five wrong lock fifteen minutes and double up to a day (DESIGN-persona-propose-osverify-c2f0b-1123 §11 r7; Andy DECISION 第十七節)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ps() { python3 -I scripts/knife.py crates/aibd/src/passphrase.rs "$1" "$2" --expect-fail "passphrase::tests::$3" \
        -- cargo test -p aibd --locked --lib "passphrase::tests::$3"; }
# PS1 a record with lower costs is used
ps '    if c.m_kib < MIN.m_kib || c.t < MIN.t || c.p < MIN.p {' \
   '    if c.m_kib < 1 {' \
   a_record_with_lower_costs_or_another_kdf_is_refused
# PS2 a record naming another KDF is used
ps '    if r.v != 1 || r.kdf != kdf.name() {' \
   '    if r.v != 1 {' \
   a_record_with_lower_costs_or_another_kdf_is_refused
# PS3 the comparison looks at the first byte only
ps '    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0' \
   '    a.iter().zip(b).take(1).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0' \
   same_is_byte_equality
# PS4 a short sentence is taken
ps '    if words < MIN_WORDS && chars < MIN_CHARS {' \
   '    if words < 1 && chars < 1 {' \
   a_short_or_personal_sentence_is_not_taken
# PS5 the account or computer name in the sentence is taken
ps '        if n.chars().count() >= 3 && low.contains(&n) {' \
   '        if n.chars().count() >= 3 && low == n {' \
   a_short_or_personal_sentence_is_not_taken
# PS6 no lock after five wrong
ps '    if wrong < FREE_TRIES {' \
   '    if wrong < 50 {' \
   five_wrong_lock_fifteen_minutes_then_double_up_to_a_day
# PS7 the lock has no ceiling
ps '        .min(MAX_LOCK_SECS)' \
   '        .min(u64::MAX)' \
   five_wrong_lock_fifteen_minutes_then_double_up_to_a_day
# PS8 (f163c e5f942dd L1) a record with costs above the ceiling is used
ps '    if c.m_kib > MAX.m_kib || c.t > MAX.t || c.p > MAX.p {' \
   '    if c.m_kib > u32::MAX {' \
   a_record_with_costs_above_the_ceiling_is_refused
# PS9 (f163c e5f942dd L3) the backend runs Argon2i, not Argon2id
ps '        let a = argon2::Argon2::new(argon2::Algorithm::Argon2id, argon2::Version::V0x13, params);' \
   '        let a = argon2::Argon2::new(argon2::Algorithm::Argon2i, argon2::Version::V0x13, params);' \
   the_argon2id_backend_gives_the_known_answer
