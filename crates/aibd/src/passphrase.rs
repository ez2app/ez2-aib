//! The owner's passphrase, for a machine with no fingerprint, login password or Windows Hello
//! (Andy DECISION 第十七節「要密語備援」; DESIGN-persona-propose-osverify-c2f0b-1123 §11 r7,
//! f163c PREREAD r6 / r7). This is S11-2's first part: pure functions only, not wired to any
//! window or owner ask.
//!
//! - [`Record`]: what is kept — the KDF's name and costs, a 32-byte salt, the 32-byte result,
//!   when it was set. Never the passphrase. A record with costs below [`MIN`] is refused when
//!   read (a seat that lowers them in the file gets nothing: the passphrase is asked again).
//! - [`Kdf`]: the key derivation is passed in; [`Argon2id`] is the backend (argon2 =0.5.3, m =
//!   256 MiB, t = 3, p = 1, about a second, f163c r7 L1). Its Cargo.lock lines come from 90856
//!   on a test machine (this one runs no cargo); a new external crate: f163c reviews it.
//! - [`check_new`]: the rules for a sentence the owner thinks up (§11h M1 fallback): at least
//!   5 words or 24 characters, not the account or computer name.
//! - [`lock_after`]: the software lock — 5 wrong in a row lock 15 minutes, each further wrong
//!   doubles it, at most 24 hours. Only a brake on guessing through aibd: a seat can delete the
//!   count, so it is no defence against guessing offline (§11e).
//! - [`same`]: the comparison takes the same time wherever the bytes differ.

use serde::{Deserialize, Serialize};

/// The KDF a record names.
pub const ARGON2ID: &str = "argon2id";

/// The lowest costs a kept record may have (§11b layer 3: 256 MiB, 3 passes, 1 lane).
pub const MIN: Costs = Costs {
    m_kib: 256 * 1024,
    t: 3,
    p: 1,
};

/// The highest costs a kept record may name (f163c PREREAD e5f942dd L1): a seat that raises
/// them in the file would make every check take all memory and stop aibd.
pub const MAX: Costs = Costs {
    m_kib: 1024 * 1024,
    t: 10,
    p: 4,
};

pub const SALT_LEN: usize = 32;
pub const OUT_LEN: usize = 32;

/// Wrong answers in a row before the lock.
pub const FREE_TRIES: u32 = 5;
pub const FIRST_LOCK_SECS: u64 = 15 * 60;
pub const MAX_LOCK_SECS: u64 = 24 * 60 * 60;

pub const MIN_WORDS: usize = 5;
pub const MIN_CHARS: usize = 24;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Costs {
    pub m_kib: u32,
    pub t: u32,
    pub p: u32,
}

/// What is kept for the passphrase.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    pub v: u32,
    pub kdf: String,
    pub costs: Costs,
    /// hex
    pub salt: String,
    /// hex
    pub out: String,
    pub set_at: u64,
}

/// The key derivation: `out` from the passphrase, the salt and the costs.
pub trait Kdf {
    fn name(&self) -> &'static str;
    fn derive(&self, phrase: &[u8], salt: &[u8; SALT_LEN], c: Costs) -> Option<[u8; OUT_LEN]>;
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn unhex<const N: usize>(s: &str) -> Option<[u8; N]> {
    if s.len() != N * 2 {
        return None;
    }
    let mut out = [0u8; N];
    for (i, o) in out.iter_mut().enumerate() {
        *o = u8::from_str_radix(s.get(i * 2..i * 2 + 2)?, 16).ok()?;
    }
    Some(out)
}

/// Equal, in time that does not depend on where the bytes differ.
pub fn same(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Why a record is not used.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refused {
    /// Not this version, not this KDF, or costs below [`MIN`].
    Weak,
    /// Costs above [`MAX`].
    TooCostly,
    Malformed,
}

/// A kept record, only when it is the version, the KDF and at least the costs this aibd asks.
pub fn usable(r: &Record, kdf: &dyn Kdf) -> Result<([u8; SALT_LEN], [u8; OUT_LEN]), Refused> {
    if r.v != 1 || r.kdf != kdf.name() {
        return Err(Refused::Weak);
    }
    let c = r.costs;
    if c.m_kib < MIN.m_kib || c.t < MIN.t || c.p < MIN.p {
        return Err(Refused::Weak);
    }
    if c.m_kib > MAX.m_kib || c.t > MAX.t || c.p > MAX.p {
        return Err(Refused::TooCostly);
    }
    let salt = unhex::<SALT_LEN>(&r.salt).ok_or(Refused::Malformed)?;
    let out = unhex::<OUT_LEN>(&r.out).ok_or(Refused::Malformed)?;
    Ok((salt, out))
}

/// The record's own hash (f163c PREREAD e5f942dd L2): aibd keeps the one it read at start and
/// the one its setting window wrote; a record that changed otherwise is shown red and not used
/// until the owner sets it again in the window (wired in the window's part).
pub fn record_sha(r: &Record) -> String {
    use sha2::{Digest, Sha256};
    let line = format!(
        "aibd/passphrase-record/v1\n{}\n{}\n{}/{}/{}\n{}\n{}\n{}\n",
        r.v, r.kdf, r.costs.m_kib, r.costs.t, r.costs.p, r.salt, r.out, r.set_at
    );
    hex(&Sha256::digest(line.as_bytes()))
}

/// The record for a new passphrase (the salt from the caller's random source).
pub fn record(kdf: &dyn Kdf, phrase: &[u8], salt: [u8; SALT_LEN], now: u64) -> Option<Record> {
    let out = kdf.derive(phrase, &salt, MIN)?;
    Some(Record {
        v: 1,
        kdf: kdf.name().to_owned(),
        costs: MIN,
        salt: hex(&salt),
        out: hex(&out),
        set_at: now,
    })
}

/// Whether `phrase` is the kept one. A refused record is never a yes.
pub fn verify(r: &Record, kdf: &dyn Kdf, phrase: &[u8]) -> bool {
    let Ok((salt, out)) = usable(r, kdf) else {
        return false;
    };
    kdf.derive(phrase, &salt, r.costs)
        .is_some_and(|got| same(&got, &out))
}

/// Why a sentence the owner thought up is not taken.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Weak {
    TooShort,
    /// It holds the account or the computer name.
    Personal,
}

/// The rules for a sentence the owner thinks up (§11h M1 fallback). Its real strength cannot
/// be measured (f163c r7 L1): the page says so whatever this returns.
pub fn check_new(phrase: &str, account: &str, computer: &str) -> Result<(), Weak> {
    let words = phrase.split_whitespace().count();
    let chars = phrase.chars().filter(|c| !c.is_whitespace()).count();
    if words < MIN_WORDS && chars < MIN_CHARS {
        return Err(Weak::TooShort);
    }
    let low = phrase.to_lowercase();
    for name in [account, computer] {
        let n = name.trim().to_lowercase();
        if n.chars().count() >= 3 && low.contains(&n) {
            return Err(Weak::Personal);
        }
    }
    Ok(())
}

/// After `wrong` wrong answers in a row, how long no answer is taken (0: not locked).
pub fn lock_after(wrong: u32) -> u64 {
    if wrong < FREE_TRIES {
        return 0;
    }
    let doublings = (wrong - FREE_TRIES).min(16);
    FIRST_LOCK_SECS
        .saturating_mul(1u64 << doublings)
        .min(MAX_LOCK_SECS)
}

/// The Argon2id backend (argon2 0.5, version 0x13), at the costs the record names.
pub struct Argon2id;

impl Kdf for Argon2id {
    fn name(&self) -> &'static str {
        ARGON2ID
    }
    fn derive(&self, phrase: &[u8], salt: &[u8; SALT_LEN], c: Costs) -> Option<[u8; OUT_LEN]> {
        let params = argon2::Params::new(c.m_kib, c.t, c.p, Some(OUT_LEN)).ok()?;
        let a = argon2::Argon2::new(argon2::Algorithm::Argon2id, argon2::Version::V0x13, params);
        let mut out = [0u8; OUT_LEN];
        a.hash_password_into(phrase, salt, &mut out).ok()?;
        Some(out)
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// A stand-in KDF: SHA-256 over the costs, the salt and the phrase (tests only).
    struct Fake;
    impl Kdf for Fake {
        fn name(&self) -> &'static str {
            ARGON2ID
        }
        fn derive(&self, phrase: &[u8], salt: &[u8; SALT_LEN], c: Costs) -> Option<[u8; OUT_LEN]> {
            use sha2::{Digest, Sha256};
            let mut h = Sha256::new();
            h.update(format!("{}/{}/{}", c.m_kib, c.t, c.p));
            h.update(salt);
            h.update(phrase);
            let mut o = [0u8; OUT_LEN];
            o.copy_from_slice(&h.finalize());
            Some(o)
        }
    }

    fn rec() -> Record {
        record(&Fake, "正確 的 那 一 句".as_bytes(), [7; SALT_LEN], 100).unwrap()
    }

    #[test]
    fn the_right_phrase_is_a_yes_and_anything_else_a_no() {
        let r = rec();
        assert!(!r.out.is_empty() && !r.out.contains("正確"));
        assert!(verify(&r, &Fake, "正確 的 那 一 句".as_bytes()));
        assert!(!verify(&r, &Fake, "正確 的 那 一 字".as_bytes()));
        assert!(!verify(&r, &Fake, b""));
    }

    /// A seat that lowers the costs (or names another KDF) in the kept file gets no yes.
    #[test]
    fn a_record_with_lower_costs_or_another_kdf_is_refused() {
        for c in [
            Costs {
                m_kib: MIN.m_kib / 2,
                ..MIN
            },
            Costs { t: 1, ..MIN },
            Costs { p: 0, ..MIN },
        ] {
            let mut r = rec();
            r.costs = c;
            assert_eq!(usable(&r, &Fake), Err(Refused::Weak), "{c:?}");
            assert!(!verify(&r, &Fake, "正確 的 那 一 句".as_bytes()));
        }
        let mut r = rec();
        r.kdf = "sha256".into();
        assert_eq!(usable(&r, &Fake), Err(Refused::Weak));
        let mut r = rec();
        r.v = 0;
        assert_eq!(usable(&r, &Fake), Err(Refused::Weak));
        let mut r = rec();
        r.salt.pop();
        assert_eq!(usable(&r, &Fake), Err(Refused::Malformed));
    }

    /// RFC 9106 §5.3 Argon2id test vector shape is not used here (it needs secret / ad); this
    /// checks the backend is Argon2id at small costs, deterministic, salt- and phrase-bound.
    #[test]
    fn the_argon2id_backend_is_deterministic_and_bound_to_salt_and_phrase() {
        let small = Costs {
            m_kib: 64,
            t: 1,
            p: 1,
        };
        let a = Argon2id.derive(b"phrase", &[1; SALT_LEN], small).unwrap();
        assert_eq!(
            a,
            Argon2id.derive(b"phrase", &[1; SALT_LEN], small).unwrap()
        );
        assert_ne!(
            a,
            Argon2id.derive(b"phrase", &[2; SALT_LEN], small).unwrap()
        );
        assert_ne!(
            a,
            Argon2id.derive(b"phrasf", &[1; SALT_LEN], small).unwrap()
        );
        assert_ne!(
            a,
            Argon2id
                .derive(b"phrase", &[1; SALT_LEN], Costs { t: 2, ..small })
                .unwrap()
        );
        assert_eq!(Argon2id.name(), ARGON2ID);
    }

    /// L1: costs above the ceiling are refused before anything is derived.
    #[test]
    fn a_record_with_costs_above_the_ceiling_is_refused() {
        for c in [
            Costs {
                m_kib: 4 * 1024 * 1024,
                ..MIN
            },
            Costs { t: 1000, ..MIN },
            Costs { p: 64, ..MIN },
        ] {
            let mut r = rec();
            r.costs = c;
            assert_eq!(usable(&r, &Fake), Err(Refused::TooCostly), "{c:?}");
        }
        assert!(usable(&rec(), &Fake).is_ok());
    }

    /// L2: any field changed changes the record's hash.
    #[test]
    fn the_record_hash_moves_with_every_field() {
        let base = record_sha(&rec());
        assert_eq!(base, record_sha(&rec()));
        let mut r = rec();
        r.out.replace_range(0..2, "zz");
        assert_ne!(record_sha(&r), base);
        let mut r = rec();
        r.salt.replace_range(0..2, "zz");
        assert_ne!(record_sha(&r), base);
        let mut r = rec();
        r.set_at += 1;
        assert_ne!(record_sha(&r), base);
    }

    #[test]
    fn same_is_byte_equality() {
        assert!(same(b"abc", b"abc"));
        assert!(!same(b"abc", b"abd"));
        assert!(!same(b"xbc", b"abc"));
        assert!(!same(b"ab", b"abc"));
    }

    #[test]
    fn a_short_or_personal_sentence_is_not_taken() {
        assert_eq!(check_new("太短了", "andy", "mac"), Err(Weak::TooShort));
        assert_eq!(
            check_new("one two three four", "x1", "y1"),
            Err(Weak::TooShort)
        );
        assert!(check_new("one two three four five", "x1", "y1").is_ok());
        assert!(check_new("河邊那棵老榕樹下第三塊石頭底下的鑰匙", "x1", "y1").is_ok());
        assert_eq!(
            check_new("my name is Andy and this is long", "andy", "pro16"),
            Err(Weak::Personal)
        );
        assert_eq!(
            check_new("pro16 is the best laptop i own", "zz9", "Pro16"),
            Err(Weak::Personal)
        );
    }

    #[test]
    fn five_wrong_lock_fifteen_minutes_then_double_up_to_a_day() {
        assert_eq!(lock_after(0), 0);
        assert_eq!(lock_after(4), 0);
        assert_eq!(lock_after(5), 15 * 60);
        assert_eq!(lock_after(6), 30 * 60);
        assert_eq!(lock_after(7), 60 * 60);
        assert_eq!(lock_after(11), 16 * 60 * 60);
        assert_eq!(lock_after(12), MAX_LOCK_SECS);
        assert_eq!(lock_after(u32::MAX), MAX_LOCK_SECS);
    }
}
