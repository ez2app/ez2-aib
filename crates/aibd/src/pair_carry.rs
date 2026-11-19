//! S89-pair-carry (DECISION-council-pair-persist-519d0-1118, option 3): a confirmed page
//! pairing survives an update once, so the person does not pair again after every new build.
//!
//! How aibd tells an update from any other restart (5d33a 04:16 asked; nothing an install tool
//! or a seat leaves behind decides it): at start aibd hashes its own executable file; at a clean
//! stop it hashes the file at the same path again. Only when the two differ (a new build was put
//! in place while it ran) does it write the carry file. A manual restart with the same build,
//! a crash, a kill, a revoke: nothing written, so the page gets 401 and pairs again.
//!
//! The file (`<data dir>/pair-carry.json`, owner only):
//! `{"v":1,"written_at":<unix s>,"old_exe":<sha256>,"new_exe":<sha256>,"pairs":[{"digest":<sha256
//! of the credential>,"user_page":bool,"left":<s>}],"channel":<AIBD_PAIRING that confirmed them>}`
//! — digests only, never a credential. A credential is carried only into an aibd confirming
//! with the same channel: a weak `terminal-unverified` one does not live through the update that
//! turns a stronger channel on (5d33a batch-1 L1; pairing.rs: a weak credential does not
//! outlive it). A file without `channel` was written by a build that had only that one.
//! The next start reads it and deletes it before looking inside, then takes it only when
//! all of these hold, otherwise nothing (fail closed, 5d33a F5):
//! 1. the version is 1 and every field has its shape;
//! 2. it was written at most [`MAX_AGE`] ago (and not in the future past a small skew);
//! 3. the executable now running is the `new_exe` it names, and `old_exe` differs: the file
//!    belongs to this one update, so an old file put back is not taken (5d33a F2);
//! 4. the file is the user's own and nobody else may write it (unix).
//!
//! Each carried credential must be used within [`crate::pairing::CARRY_USE_BY`] and is never
//! written again ([`crate::pairing::Pairing::carry_out`]), so it is carried once. It keeps its
//! user-page mark (F3) and types to safe seats only; a dgrs seat's input and approvals want a
//! pairing confirmed in this run (DECISION §2).
//!
//! Boundary (same as pairing itself): an AI on the same account can replace the executable and
//! stop aibd cleanly, and so get a carry written; it could equally read the start-up token and
//! pair. The carry-over adds no power a same-account AI lacks, and only for a credential the
//! person confirmed.

use crate::pairing::{CarriedCred, Pairing};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};

/// The carry file's name in the data directory.
pub const FILE: &str = "pair-carry.json";
/// A carry file older than this is not taken (DECISION §1: 10 minutes).
pub const MAX_AGE: u64 = 600;
/// How far in the future `written_at` may be (clock skew between the two processes: none
/// expected, a little allowed).
const SKEW: u64 = 60;
const VERSION: u64 = 1;

/// Seconds since 1970 now (wall clock: the file crosses processes, where no monotonic clock
/// is shared).
pub fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn path(data_dir: &Path) -> PathBuf {
    data_dir.join(FILE)
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn unhex32(s: &str) -> Option<[u8; 32]> {
    if s.len() != 64
        || !s
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, o) in out.iter_mut().enumerate() {
        *o = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16).ok()?;
    }
    Some(out)
}

/// SHA-256 of a file's bytes, read in pieces.
pub fn file_digest(p: &Path) -> Option<String> {
    let mut f = std::fs::File::open(p).ok()?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Some(hex(&h.finalize()))
}

/// The running executable's path and digest; `None` when either cannot be read (then nothing
/// is carried either way).
pub fn exe() -> Option<(PathBuf, String)> {
    let p = std::env::current_exe().ok()?;
    let d = file_digest(&p)?;
    Some((p, d))
}

/// Why nothing was written or taken (for the log and the event; never a secret).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Skip {
    /// The executable did not change: a plain restart.
    SameBuild,
    /// No confirmed credential to carry.
    Nothing,
    /// The executable could not be read.
    NoExe,
    NoFile,
    Unreadable,
    NotOwned,
    BadShape,
    UnknownVersion,
    TooOld,
    Future,
    OtherBuild,
    /// Confirmed by another channel than the one this aibd confirms with (5d33a L1).
    OtherChannel,
    /// Every credential in the file was carried before (a copy put back; 0eed7 M1).
    Replayed,
}

impl Skip {
    pub fn as_str(self) -> &'static str {
        match self {
            Skip::SameBuild => "same_build",
            Skip::Nothing => "nothing",
            Skip::NoExe => "no_exe",
            Skip::NoFile => "no_file",
            Skip::Unreadable => "unreadable",
            Skip::NotOwned => "not_owned",
            Skip::BadShape => "bad_shape",
            Skip::UnknownVersion => "unknown_version",
            Skip::TooOld => "too_old",
            Skip::Future => "future",
            Skip::OtherBuild => "other_build",
            Skip::OtherChannel => "other_channel",
            Skip::Replayed => "replayed",
        }
    }
}

/// The carry document for `list`, written by `old_exe` for `new_exe` at `now`.
pub fn document(list: &[CarriedCred], old_exe: &str, new_exe: &str, now: u64) -> Value {
    json!({
        "v": VERSION,
        "written_at": now,
        "old_exe": old_exe,
        "new_exe": new_exe,
        "pairs": list.iter().map(|c| json!({
            "digest": hex(&c.digest),
            "user_page": c.user_page,
            "left": c.left,
        })).collect::<Vec<_>>(),
    })
}

/// At a clean stop: write the carry file when the executable at `exe_path` is no longer the
/// one that started (`started_exe`). Returns how many credentials were written.
pub fn write_at_stop(
    data_dir: &Path,
    pairing: &Pairing,
    exe_path: &Path,
    started_exe: &str,
    now: u64,
) -> Result<usize, Skip> {
    let Some(on_disk) = file_digest(exe_path) else {
        return Err(Skip::NoExe);
    };
    if on_disk == started_exe {
        return Err(Skip::SameBuild);
    }
    let list = pairing.carry_out();
    if list.is_empty() {
        return Err(Skip::Nothing);
    }
    let mut doc = document(&list, started_exe, &on_disk, now);
    doc["channel"] = Value::from(pairing.channel().as_str());
    crate::platform::write_private_file(&path(data_dir), doc.to_string().as_bytes())
        .map_err(|_| Skip::Unreadable)?;
    Ok(list.len())
}

/// Read `doc` for the executable `running_exe` at `now`: the credentials it carries, or why
/// not. Pure, so the rules are tested without files.
pub fn decide(doc: &Value, running_exe: &str, now: u64) -> Result<Vec<CarriedCred>, Skip> {
    let v = doc.get("v").and_then(Value::as_u64).ok_or(Skip::BadShape)?;
    if v != VERSION {
        return Err(Skip::UnknownVersion);
    }
    let at = doc
        .get("written_at")
        .and_then(Value::as_u64)
        .ok_or(Skip::BadShape)?;
    if at > now.saturating_add(SKEW) {
        return Err(Skip::Future);
    }
    if now.saturating_sub(at) > MAX_AGE {
        return Err(Skip::TooOld);
    }
    let old = doc
        .get("old_exe")
        .and_then(Value::as_str)
        .ok_or(Skip::BadShape)?;
    let new = doc
        .get("new_exe")
        .and_then(Value::as_str)
        .ok_or(Skip::BadShape)?;
    if unhex32(old).is_none() || unhex32(new).is_none() {
        return Err(Skip::BadShape);
    }
    if new != running_exe || old == running_exe {
        return Err(Skip::OtherBuild);
    }
    let pairs = doc
        .get("pairs")
        .and_then(Value::as_array)
        .ok_or(Skip::BadShape)?;
    let mut out = Vec::new();
    for p in pairs {
        let digest = p
            .get("digest")
            .and_then(Value::as_str)
            .and_then(unhex32)
            .ok_or(Skip::BadShape)?;
        let user_page = p
            .get("user_page")
            .and_then(Value::as_bool)
            .ok_or(Skip::BadShape)?;
        let left = p
            .get("left")
            .and_then(Value::as_u64)
            .ok_or(Skip::BadShape)?;
        out.push(CarriedCred {
            digest,
            user_page,
            left,
        });
    }
    Ok(out)
}

#[cfg(unix)]
fn owned_by_me(m: &std::fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    crate::allowlist::owner_ok(m.uid(), m.mode(), nix::unistd::getuid().as_raw()).is_ok()
        && m.mode() & 0o077 == 0
}

/// Windows: the data directory is the user's own (create_private_dir); ACL checks come with
/// the Windows install slice, as for the policy file.
#[cfg(not(unix))]
fn owned_by_me(_: &std::fs::Metadata) -> bool {
    true
}

/// The name a carried credential is recorded under in aibd's database: a credential comes
/// over once, ever (DECISION §4 "第二次用 401"; 0eed7 M1: a used carry file put back).
pub fn claim_key(digest: &[u8; 32]) -> String {
    format!("pair-carry:{}", hex(digest))
}

/// At start: take the carry file (deleted before it is looked into, whatever it says) into
/// `pairing` for the executable `running_exe`. `claim` records one credential as carried and
/// answers false when it was carried before (or could not be recorded): that credential stays
/// out. Returns how many credentials came over.
pub fn take_at_start(
    data_dir: &Path,
    pairing: &Pairing,
    running_exe: &str,
    now: u64,
    mut claim: impl FnMut(&[u8; 32]) -> bool,
) -> Result<usize, Skip> {
    let p = path(data_dir);
    let meta = match std::fs::symlink_metadata(&p) {
        Ok(m) => m,
        Err(_) => return Err(Skip::NoFile),
    };
    let owned = meta.file_type().is_file() && owned_by_me(&meta);
    let bytes = if owned { std::fs::read(&p).ok() } else { None };
    // one start reads it once: gone before anything in it is believed
    let _ = std::fs::remove_file(&p);
    if !owned {
        return Err(Skip::NotOwned);
    }
    let bytes = bytes.ok_or(Skip::Unreadable)?;
    if bytes.len() > 64 * 1024 {
        return Err(Skip::BadShape);
    }
    let doc: Value = serde_json::from_slice(&bytes).map_err(|_| Skip::BadShape)?;
    let list = decide(&doc, running_exe, now)?;
    // 5d33a L1: only into the channel that confirmed them (no field = the one channel there was)
    let channel = doc
        .get("channel")
        .and_then(Value::as_str)
        .unwrap_or("terminal-unverified");
    if channel != pairing.channel().as_str() {
        return Err(Skip::OtherChannel);
    }
    if list.is_empty() {
        return Err(Skip::Nothing);
    }
    let list: Vec<CarriedCred> = list.into_iter().filter(|c| claim(&c.digest)).collect();
    if list.is_empty() {
        return Err(Skip::Replayed);
    }
    Ok(pairing.carry_in(&list))
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    fn one() -> Vec<CarriedCred> {
        vec![CarriedCred {
            digest: [7u8; 32],
            user_page: true,
            left: 3600,
        }]
    }

    /// 5d33a L1: credentials confirmed by another channel are not taken; a file without the
    /// field counts as `terminal-unverified`.
    #[test]
    fn a_carry_confirmed_by_another_channel_is_not_taken() {
        let dir = tempfile::tempdir().unwrap();
        let p = Pairing::default().with_channel(crate::pairing::ConfirmChannel::TerminalUnverified);
        let mut d = document(&one(), A, B, 1000);
        d["channel"] = Value::from("hello");
        crate::platform::write_private_file(&path(dir.path()), d.to_string().as_bytes()).unwrap();
        assert_eq!(
            take_at_start(dir.path(), &p, B, 1000, |_| true),
            Err(Skip::OtherChannel)
        );
        assert!(!path(dir.path()).exists());
        let d = document(&one(), A, B, 1000);
        crate::platform::write_private_file(&path(dir.path()), d.to_string().as_bytes()).unwrap();
        assert_eq!(take_at_start(dir.path(), &p, B, 1000, |_| true), Ok(1));
    }

    #[test]
    fn a_fresh_file_for_this_build_is_taken_with_its_marks() {
        let d = document(&one(), A, B, 1000);
        assert_eq!(decide(&d, B, 1000 + MAX_AGE), Ok(one()));
    }

    #[test]
    fn old_future_other_build_and_unknown_version_are_not_taken() {
        let d = document(&one(), A, B, 1000);
        assert_eq!(decide(&d, B, 1000 + MAX_AGE + 1), Err(Skip::TooOld));
        assert_eq!(decide(&d, B, 1000 - SKEW - 1), Err(Skip::Future));
        // an old file put back for another build, or the same build restarted
        assert_eq!(decide(&d, A, 1000), Err(Skip::OtherBuild));
        let same = document(&one(), B, B, 1000);
        assert_eq!(decide(&same, B, 1000), Err(Skip::OtherBuild));
        let mut v2 = d.clone();
        v2["v"] = json!(2);
        assert_eq!(decide(&v2, B, 1000), Err(Skip::UnknownVersion));
        let mut bad = d;
        bad["pairs"][0]["digest"] = json!("XYZ");
        assert_eq!(decide(&bad, B, 1000), Err(Skip::BadShape));
    }

    #[test]
    fn the_file_holds_digests_only() {
        let d = document(&one(), A, B, 1000).to_string();
        assert!(d.contains(&"07".repeat(32)));
        for k in ["credential", "code", "short_code"] {
            assert!(!d.contains(k), "{k} in {d}");
        }
    }
}
