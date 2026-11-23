//! S11-3 密語備援 wiring (DESIGN-persona-propose-osverify-c2f0b-1123 §11, IFACE-passphrase-c2f0b-1123):
//! where the record, the record aibd knows and the tries are kept, and the one lock every ask
//! goes through.
//!
//! - **One lock** (f163c REVIEW-passphrase2 N1): reading the tries, judging, deriving and writing
//!   the tries back happen under one `tokio` mutex, so two answers never race the count and only
//!   one derivation (up to 1 GiB, [`crate::passphrase::MAX`]) runs at a time; the derivation
//!   itself is on a blocking thread.
//! - **Only the owner's own sentence from outside** (N2): [`Keeper::set_owner`] always sets with
//!   [`Source::Owner`], so its strength check always runs; aibd's six words come in a later
//!   slice, from aibd itself.
//! - **The record aibd knows** (f163c REVIEW-passphrase L2): `passphrase.known` holds the
//!   [`record_sha`] written at set time; aibd reads it once when it starts and after that only
//!   believes its own memory, so a record swapped while aibd runs is `swapped` even if the
//!   known file is swapped with it. A swap of both while aibd is stopped is not seen: a local
//!   file is not a defence against the same user (GUIDE-same-user-ai-threat-model); the
//!   passphrase itself is.
//! - **Tries survive a restart**: written after every answer that changed them. A missing or
//!   unreadable tries file next to a record counts as locked ([`read_tries`]): deleting it is
//!   not a way to more tries.
//! - **No phrase over the web**: the page only gets [`STATE`] (what [`crate::passphrase::view`]
//!   shows); the phrase comes from the system's secure input in the window slice.

use crate::passphrase::{
    self as pp, record_sha, Answer, Kdf, NewPhrase, Record, Refused, SetError, Source, Tries,
};
use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

/// The built-in page reads the passphrase state (IFACE-passphrase §1).
pub const STATE: &str = "passphrase_state";

/// `<data>/owner/`: the passphrase files.
pub const DIR: &str = "owner";
pub const RECORD_FILE: &str = "passphrase.json";
pub const KNOWN_FILE: &str = "passphrase.known";
pub const TRIES_FILE: &str = "passphrase-tries.json";

/// The record as found on disk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OnDisk {
    None,
    Good(Record),
    /// There, but not readable as a record: treated as swapped.
    Bad,
}

pub fn read_record(dir: &Path) -> OnDisk {
    match std::fs::read(dir.join(RECORD_FILE)) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => OnDisk::None,
        Err(_) => OnDisk::Bad,
        Ok(b) => serde_json::from_slice(&b).map_or(OnDisk::Bad, OnDisk::Good),
    }
}

/// The tries on disk. No record and no file: none yet. A record without a readable tries
/// file: locked as after the fifth wrong answer, from `now` (fail closed).
pub fn read_tries(dir: &Path, record_there: bool, now: u64) -> Tries {
    match std::fs::read(dir.join(TRIES_FILE)) {
        Ok(b) => {
            if let Ok(t) = serde_json::from_slice::<Tries>(&b) {
                return t;
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && !record_there => {
            return Tries::default();
        }
        Err(_) => {}
    }
    Tries {
        wrong: pp::FREE_TRIES,
        locked_until: now.saturating_add(pp::FIRST_LOCK_SECS),
    }
}

fn write_tries(dir: &Path, t: &Tries) -> std::io::Result<()> {
    let b = serde_json::to_vec(t).map_err(std::io::Error::other)?;
    crate::platform::write_private_file(&dir.join(TRIES_FILE), &b)
}

fn read_known(dir: &Path) -> Option<String> {
    let s = std::fs::read_to_string(dir.join(KNOWN_FILE)).ok()?;
    let s = s.trim();
    (s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit())).then(|| s.to_owned())
}

/// The passphrase files of one data folder, and the one lock.
pub struct Keeper {
    dir: PathBuf,
    kdf: Arc<dyn Kdf + Send + Sync>,
    /// The record aibd knows; read from `passphrase.known` once, then only set by [`Keeper::set_owner`].
    known: tokio::sync::Mutex<Option<String>>,
}

impl Keeper {
    /// `dir` is `<data>/owner`.
    pub fn open(dir: PathBuf, kdf: Arc<dyn Kdf + Send + Sync>) -> Keeper {
        let known = read_known(&dir);
        if matches!(read_record(&dir), OnDisk::Good(_) | OnDisk::Bad) && known.is_none() {
            tracing::warn!("passphrase record without a known record: shown as swapped");
        }
        Keeper {
            dir,
            kdf,
            known: tokio::sync::Mutex::new(known),
        }
    }

    /// What the page shows ([`pp::view`]); a record that does not read is `swapped`.
    pub async fn view(&self, system_usable: bool, now: u64) -> Value {
        let known = self.known.lock().await;
        let rec = read_record(&self.dir);
        let tries = read_tries(&self.dir, rec != OnDisk::None, now);
        match rec {
            OnDisk::None => pp::view(None, known.as_deref(), &tries, system_usable, now),
            OnDisk::Good(r) => pp::view(Some(&r), known.as_deref(), &tries, system_usable, now),
            OnDisk::Bad => {
                let mut v = pp::view(None, known.as_deref(), &tries, system_usable, now);
                v["set"] = json!(true);
                v["swapped"] = json!(true);
                v
            }
        }
    }

    /// Set (or set again) the owner's own sentence. The caller has the owner's yes for the
    /// setting window. The tries start over.
    pub async fn set_owner(
        &self,
        phrase: String,
        again: String,
        account: String,
        computer: String,
        system_usable: bool,
        now: u64,
    ) -> Result<(), SetError> {
        let mut known = self.known.lock().await;
        let mut salt = [0u8; pp::SALT_LEN];
        getrandom::fill(&mut salt).map_err(|_| SetError::Kdf)?;
        let kdf = self.kdf.clone();
        let (r, t) = tokio::task::spawn_blocking(move || {
            let n = NewPhrase {
                phrase: &phrase,
                again: &again,
                source: Source::Owner,
                account: &account,
                computer: &computer,
            };
            pp::set(kdf.as_ref(), &n, system_usable, salt, now)
        })
        .await
        .map_err(|_| SetError::Kdf)??;
        let sha = record_sha(&r);
        let body = serde_json::to_vec(&r).map_err(|_| SetError::Store)?;
        crate::platform::create_private_dir(&self.dir).map_err(|_| SetError::Store)?;
        // the known record first: a crash between the two leaves `swapped`, not a record
        // aibd would take
        crate::platform::write_private_file(&self.dir.join(KNOWN_FILE), sha.as_bytes())
            .map_err(|_| SetError::Store)?;
        *known = Some(sha);
        crate::platform::write_private_file(&self.dir.join(RECORD_FILE), &body)
            .map_err(|_| SetError::Store)?;
        write_tries(&self.dir, &t).map_err(|_| SetError::Store)?;
        Ok(())
    }

    /// One answer ([`pp::answer`]), the tries read and written under the lock.
    pub async fn answer(&self, phrase: String, system_usable: bool, now: u64) -> Answer {
        let known = self.known.lock().await;
        let rec = match read_record(&self.dir) {
            OnDisk::None => None,
            OnDisk::Good(r) => Some(r),
            OnDisk::Bad if system_usable => return Answer::UseSystem,
            OnDisk::Bad => return Answer::Swapped,
        };
        let before = read_tries(&self.dir, rec.is_some(), now);
        let (kdf, k) = (self.kdf.clone(), (*known).clone());
        let got = tokio::task::spawn_blocking(move || {
            let mut t = before;
            let a = pp::answer(
                kdf.as_ref(),
                rec.as_ref(),
                k.as_deref(),
                &mut t,
                phrase.as_bytes(),
                system_usable,
                now,
            );
            (a, t)
        })
        .await;
        let Ok((a, after)) = got else {
            return Answer::Refused(Refused::Malformed);
        };
        if after != before {
            if let Err(e) = write_tries(&self.dir, &after) {
                // a count that is not kept is not a wrong answer forgiven: say locked
                tracing::warn!(error = %e, "passphrase tries not written");
                if a != Answer::Yes {
                    return Answer::Locked {
                        until: now.saturating_add(pp::FIRST_LOCK_SECS),
                    };
                }
            }
        }
        a
    }
}

static KEEPER: OnceLock<Keeper> = OnceLock::new();

/// At start: the data folder (server.rs, like [`crate::bridge_cfg::set_dir`]).
pub fn set_dir(data: &Path) {
    let _ = KEEPER.set(Keeper::open(data.join(DIR), Arc::new(pp::Argon2id)));
}

pub fn keeper() -> Option<&'static Keeper> {
    KEEPER.get()
}

/// The system's own owner check works here, so no passphrase (§11a). macOS: the owner
/// policy (login password or Touch ID) can be evaluated. Elsewhere: taken as usable until the
/// Windows Hello key is checked on a Hello laptop (W0) — the passphrase is not offered there
/// before that (fail closed: the weaker way stays off).
pub fn system_usable() -> bool {
    #[cfg(target_os = "macos")]
    {
        crate::owner_probe::probe(&crate::owner_probe::Mac::read())["owner"] == "ok"
    }
    #[cfg(not(target_os = "macos"))]
    {
        true
    }
}

fn refuse(status: StatusCode, code: &str) -> Response {
    (status, Json(json!({ "error": code, "code": code }))).into_response()
}

/// The built-in page in this pairing reads [`STATE`]; a user folder page or a carried
/// pairing does not (ebab8 11-23 09:11: tests/passphrase_door.rs sends it).
pub async fn page_act(page: &crate::pairing::PageAccess) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(page).await {
        return refuse(StatusCode::FORBIDDEN, "passphrase_built_in_page_only");
    }
    let Some(k) = keeper() else {
        return refuse(StatusCode::SERVICE_UNAVAILABLE, "passphrase_unavailable");
    };
    let usable = tokio::task::spawn_blocking(system_usable)
        .await
        .unwrap_or(true);
    Json(k.view(usable, crate::owner_ok::unix_now()).await).into_response()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// A stand-in KDF: SHA-256 over the costs, the salt and the phrase (tests only).
    struct Fake;
    impl Kdf for Fake {
        fn name(&self) -> &'static str {
            pp::ARGON2ID
        }
        fn derive(
            &self,
            phrase: &[u8],
            salt: &[u8; pp::SALT_LEN],
            c: pp::Costs,
        ) -> Option<[u8; pp::OUT_LEN]> {
            use sha2::{Digest, Sha256};
            let mut h = Sha256::new();
            h.update(format!("{}/{}/{}", c.m_kib, c.t, c.p));
            h.update(salt);
            h.update(phrase);
            let mut o = [0u8; pp::OUT_LEN];
            o.copy_from_slice(&h.finalize());
            Some(o)
        }
    }

    const GOOD: &str = "海 邊 那 間 老 書 店 的 貓";

    fn keep(dir: &Path) -> Keeper {
        Keeper::open(dir.join(DIR), Arc::new(Fake))
    }

    async fn set(k: &Keeper) {
        k.set_owner(
            GOOD.into(),
            GOOD.into(),
            "sample_acct".into(),
            "sample-host".into(),
            false,
            100,
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn set_answer_and_the_lock_survives_a_restart() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        set(&k).await;
        assert_eq!(k.answer(GOOD.into(), false, 200).await, Answer::Yes);
        for i in 1..=5u32 {
            let a = k.answer("不 對 的 那 一 句".into(), false, 200).await;
            assert!(matches!(a, Answer::No { wrong, .. } if wrong == i), "{a:?}");
        }
        // aibd restarts: the lock is still there, and the right sentence is not even tried
        let k2 = keep(d.path());
        assert!(matches!(
            k2.answer(GOOD.into(), false, 300).await,
            Answer::Locked { .. }
        ));
        let v = k2.view(false, 300).await;
        assert_eq!(
            (v["wrong"].as_u64(), v["locked"].as_bool()),
            (Some(5), Some(true))
        );
        // after the lock, right once: counted from zero again
        let later = 200 + pp::FIRST_LOCK_SECS + 1;
        assert_eq!(k2.answer(GOOD.into(), false, later).await, Answer::Yes);
        assert_eq!(keep(d.path()).view(false, later).await["wrong"], 0);
    }

    #[tokio::test]
    async fn a_record_swapped_while_aibd_runs_is_not_taken_even_with_its_known_file() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        set(&k).await;
        // the same sentence set somewhere else: a valid record, another salt
        let other = tempfile::tempdir().unwrap();
        set(&keep(other.path())).await;
        for f in [RECORD_FILE, KNOWN_FILE] {
            std::fs::copy(other.path().join(DIR).join(f), d.path().join(DIR).join(f)).unwrap();
        }
        assert_eq!(k.answer(GOOD.into(), false, 200).await, Answer::Swapped);
        assert_eq!(k.view(false, 200).await["swapped"], true);
        // swapped while aibd was stopped, with its known file: not seen (the module notes)
        assert_eq!(
            keep(d.path()).answer(GOOD.into(), false, 200).await,
            Answer::Yes
        );
        // set again in the window: taken
        set(&k).await;
        assert_eq!(k.answer(GOOD.into(), false, 200).await, Answer::Yes);
    }

    #[tokio::test]
    async fn a_record_that_does_not_read_is_swapped() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        set(&k).await;
        std::fs::write(d.path().join(DIR).join(RECORD_FILE), b"{not a record").unwrap();
        assert_eq!(k.answer(GOOD.into(), false, 200).await, Answer::Swapped);
        let v = k.view(false, 200).await;
        assert_eq!(
            (v["set"].as_bool(), v["swapped"].as_bool()),
            (Some(true), Some(true))
        );
    }

    #[tokio::test]
    async fn no_tries_file_next_to_a_record_is_locked() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        set(&k).await;
        std::fs::remove_file(d.path().join(DIR).join(TRIES_FILE)).unwrap();
        assert!(matches!(
            keep(d.path()).answer(GOOD.into(), false, 200).await,
            Answer::Locked { .. }
        ));
        // no record at all: nothing to lock
        let e = tempfile::tempdir().unwrap();
        assert_eq!(
            read_tries(&e.path().join(DIR), false, 200),
            Tries::default()
        );
        assert_eq!(
            keep(e.path()).answer(GOOD.into(), false, 200).await,
            Answer::NotSet
        );
    }

    #[tokio::test]
    async fn only_the_owners_own_sentence_is_set_and_never_where_the_system_check_works() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        // too short for an owner's sentence: the strength check ran (Source::Owner)
        let short = k
            .set_owner("短".into(), "短".into(), "a".into(), "b".into(), false, 100)
            .await;
        assert_eq!(short, Err(SetError::Weak(pp::Weak::TooShort)));
        let sys = k
            .set_owner(GOOD.into(), GOOD.into(), "a".into(), "b".into(), true, 100)
            .await;
        assert_eq!(sys, Err(SetError::UseSystem));
        assert_eq!(read_record(&d.path().join(DIR)), OnDisk::None);
        set(&k).await;
        assert_eq!(k.answer(GOOD.into(), true, 200).await, Answer::UseSystem);
        assert_eq!(k.view(true, 200).await["available"], false);
    }

    #[tokio::test]
    async fn the_view_never_carries_the_record() {
        let d = tempfile::tempdir().unwrap();
        let k = keep(d.path());
        set(&k).await;
        let s = k.view(false, 200).await.to_string();
        let r = std::fs::read_to_string(d.path().join(DIR).join(RECORD_FILE)).unwrap();
        let r: Record = serde_json::from_str(&r).unwrap();
        for secret in [&r.salt, &r.out, &record_sha(&r)] {
            assert!(!s.contains(secret.as_str()), "{s}");
        }
    }
}
