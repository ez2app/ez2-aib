//! The one place a persona card and the 1.x card list are written (ebab8 11-23 04:01: one
//! shared write for §1 人格改, c2f0b S2-2 寫回人格 and 6770f persona_edit;
//! DESIGN-persona-propose-osverify-c2f0b-1123 r4 §9 / r5 §10, f163c r4 / r5). Callers use
//! [`write`] only, after the owner said yes; it writes three things, in this order:
//! 1. the card file (c2f0b 2842d9fd32: compare and swap on the approved base, only a file that
//!    is this persona's own card, a fresh file beside it and a rename, read back);
//! 2. `cards.json`: that card's `persona_sha`, nothing else (the 64 hex characters swapped in
//!    place, so every other byte stays what 1.x `_dump` wrote);
//! 3. its provenance: `cards_json_sha256` (the whole file's bytes) and `cards_fingerprint`
//!    (the cards as 1.x `json.dumps(sort_keys=True, ensure_ascii=False)`), swapped the same way.
//!
//! After the three, 1.x `card_registry.py refresh` says `current` (r4 §9 S3).
//!
//! Before anything: an archived persona (`persona_archived`) or an old uid merged into another
//! (`persona_aliased`) is not written (6770f §8 / §9). A copy of the three files goes to
//! `<data>/persona-writes/<at>-<uid>/` (the newest 20 kept); `pending-<uid>.json` says a write
//! is under way, and aibd finishes or drops it when it starts ([`finish_pending`]). One lock in
//! the process: every card shares `cards.json`. 1.x takes no lock; `cards.json` is read again
//! right before its rename, and a list scanned in between is `persona_registry_changed`.
//!
//! Where the file is comes from the card list, which a seat can write. So the path alone is
//! not trusted; the file must look like this persona's card (f163c PREREAD r0, D4):
//! - a plain `.md` file, not a link, its real path the path itself (no linked folder on the
//!   way), and on unix one name only (a hard link would write another file too);
//! - its text now hashes to the base the owner approved (compare and swap: a card changed
//!   since the proposal is `persona_changed`, nothing written);
//! - that text opens with front matter whose `uid` is the persona's: a file that is not a
//!   persona card (a key list, a config) has none, so it is never written here.

use std::io::Write;
use std::path::{Path, PathBuf};

/// Why a card was not written; the page shows the code.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteError {
    /// The card's text is not the base the owner approved.
    Changed,
    Unreadable,
    /// Not a `.md` file at its own real path, or not this persona's card.
    Outside,
    Symlink,
    /// Read back, the file is not the new text.
    Mismatch,
    Io,
    /// An archived persona (6770f §8).
    Archived,
    /// An old uid merged into another: the caller uses the new one.
    Aliased,
    /// `cards.json` is not what its provenance says (edited by hand, or half written).
    RegistryEdited,
    /// `cards.json` changed while it was being written (a 1.x scan ran): scan, then again.
    RegistryChanged,
}

impl WriteError {
    pub fn code(self) -> &'static str {
        match self {
            WriteError::Changed => "persona_changed",
            WriteError::Unreadable => "persona_unreadable",
            WriteError::Outside => "persona_path_outside",
            WriteError::Symlink => "persona_path_symlink",
            WriteError::Mismatch => "persona_write_mismatch",
            WriteError::Io => "persona_write_io",
            WriteError::Archived => "persona_archived",
            WriteError::Aliased => "persona_aliased",
            WriteError::RegistryEdited => "persona_registry_edited",
            WriteError::RegistryChanged => "persona_registry_changed",
        }
    }
}

/// The card written: its new sha and where.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Written {
    pub new_sha: String,
    pub card_path: PathBuf,
}

/// sha256 of `b`, 64 lowercase hex (1.x `persona_sha` is the card file's).
pub fn sha_hex(b: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

/// The front matter as written: from a first line `---` through the next `---` line, ends of
/// line included; `None` without one (or never closed).
pub fn front_block(text: &str) -> Option<&str> {
    let first = text.split_inclusive('\n').next()?;
    if first.trim_end() != "---" {
        return None;
    }
    let mut end = first.len();
    for l in text[first.len()..].split_inclusive('\n') {
        end += l.len();
        if l.trim_end() == "---" {
            return Some(&text[..end]);
        }
    }
    None
}

/// The value of `key:` in the text's front matter (between a first line `---` and the next).
pub fn front_value(text: &str, key: &str) -> Option<String> {
    let mut lines = text.lines();
    if lines.next()?.trim_end() != "---" {
        return None;
    }
    for l in lines {
        if l.trim_end() == "---" {
            return None;
        }
        if let Some((k, v)) = l.split_once(':') {
            if k.trim() == key {
                return Some(v.trim().trim_matches(|c| c == '"' || c == '\'').to_owned());
            }
        }
    }
    None
}

/// `real` (from `canonicalize`) is `path` itself. Windows gives `\\?\C:\…` and its paths
/// ignore case: compared without that prefix, case folded (a Mac and Windows rule, Andy 11-14).
fn same_path(real: &Path, path: &Path) -> bool {
    if cfg!(windows) {
        let plain = |p: &Path| {
            let t = p.to_string_lossy().into_owned();
            if let Some(x) = t.strip_prefix(r"\\?\").filter(|x| !x.starts_with("UNC")) {
                return x.to_owned();
            }
            t
        };
        plain(real).eq_ignore_ascii_case(&plain(path))
    } else {
        real == path
    }
}

fn read_checked(path: &Path, uid: &str, base_sha: &str) -> Result<Vec<u8>, WriteError> {
    let meta = std::fs::symlink_metadata(path).map_err(|_| WriteError::Unreadable)?;
    if meta.file_type().is_symlink() {
        return Err(WriteError::Symlink);
    }
    if !meta.is_file() || path.extension().is_none_or(|e| e != "md") {
        return Err(WriteError::Outside);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() != 1 {
            return Err(WriteError::Outside);
        }
    }
    // a linked folder on the way: the real path is somewhere else
    let real = std::fs::canonicalize(path).map_err(|_| WriteError::Unreadable)?;
    if !same_path(&real, path) {
        return Err(WriteError::Symlink);
    }
    let text = std::fs::read(path).map_err(|_| WriteError::Unreadable)?;
    if text.len() as u64 > crate::personas::MAX_PERSONA_TEXT {
        return Err(WriteError::Outside);
    }
    if sha_hex(&text) != base_sha.to_ascii_lowercase() {
        return Err(WriteError::Changed);
    }
    let own = std::str::from_utf8(&text)
        .ok()
        .and_then(|t| {
            front_block(t)?;
            front_value(t, "uid")
        })
        .is_some_and(|u| u == uid);
    if !own {
        return Err(WriteError::Outside);
    }
    Ok(text)
}

/// Step 1: replace the card at `card_path` (persona `uid`) whose text hashes to `base_sha`
/// with `new_text`. `card_path` must be absolute.
fn write_card_file(
    card_path: &Path,
    uid: &str,
    base_sha: &str,
    new_text: &[u8],
) -> Result<Written, WriteError> {
    if !card_path.is_absolute() {
        return Err(WriteError::Outside);
    }
    read_checked(card_path, uid, base_sha)?;
    let dir = card_path.parent().ok_or(WriteError::Outside)?;
    let name = card_path.file_name().ok_or(WriteError::Outside)?;
    let tmp = dir.join(format!(
        ".{}.aibd-{}.tmp",
        name.to_string_lossy(),
        std::process::id()
    ));
    let mode = std::fs::metadata(card_path)
        .map_err(|_| WriteError::Io)?
        .permissions();
    let wrote = (|| -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        f.write_all(new_text)?;
        f.sync_all()?;
        std::fs::set_permissions(&tmp, mode)?;
        Ok(())
    })();
    if wrote.is_err() {
        let _ = std::fs::remove_file(&tmp);
        return Err(WriteError::Io);
    }
    // once more right before: the card may have changed while the new file was written
    if let Err(e) = read_checked(card_path, uid, base_sha) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    if std::fs::rename(&tmp, card_path).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return Err(WriteError::Io);
    }
    let back = std::fs::read(card_path).map_err(|_| WriteError::Mismatch)?;
    if back != new_text {
        return Err(WriteError::Mismatch);
    }
    Ok(Written {
        new_sha: sha_hex(new_text),
        card_path: card_path.to_path_buf(),
    })
}

// ---- steps 2 and 3: the 1.x card list ---------------------------------------------------

/// A JSON value as 1.x `json.dumps(v, ensure_ascii=False, sort_keys=True)` writes it: `", "`
/// and `": "` between, keys in code point order, text as is (only `"`, `\` and controls
/// escaped, which is serde_json's escaping too). Cards hold no numbers but ints (none today).
pub fn py_compact(v: &serde_json::Value, out: &mut String) {
    use serde_json::Value as V;
    match v {
        V::Null => out.push_str("null"),
        V::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        V::Number(n) => out.push_str(&n.to_string()),
        V::String(s) => out.push_str(&serde_json::to_string(s).unwrap_or_default()),
        V::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                py_compact(x, out);
            }
            out.push(']');
        }
        V::Object(o) => {
            let mut keys: Vec<&String> = o.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&serde_json::to_string(k).unwrap_or_default());
                out.push_str(": ");
                py_compact(&o[k], out);
            }
            out.push('}');
        }
    }
}

/// 1.x `cards_fingerprint`: the sha of the cards alone, compact.
pub fn cards_fingerprint(doc: &serde_json::Value) -> String {
    let mut s = String::new();
    py_compact(&doc["cards"], &mut s);
    sha_hex(s.as_bytes())
}

/// `text` with the one `"key": "old"` whose swap gives `want` (parsed) swapped to `new`: every
/// other byte stays. `None` when no place, or more than one, gives it.
fn swap_one(
    text: &str,
    key: &str,
    old: &str,
    new: &str,
    want: &serde_json::Value,
) -> Option<String> {
    let pat = format!("\"{key}\": \"{old}\"");
    let mut hit = None;
    for (at, _) in text.match_indices(&pat) {
        let cand = format!(
            "{}\"{key}\": \"{new}\"{}",
            &text[..at],
            &text[at + pat.len()..]
        );
        if serde_json::from_str::<serde_json::Value>(&cand)
            .ok()
            .as_ref()
            == Some(want)
        {
            if hit.is_some() {
                return None;
            }
            hit = Some(cand);
        }
    }
    hit
}

fn provenance_of(cards_json: &Path) -> PathBuf {
    let mut n = cards_json.file_name().unwrap_or_default().to_os_string();
    n.push(".provenance.json");
    cards_json.with_file_name(n)
}

fn read_json(p: &Path) -> Result<(Vec<u8>, serde_json::Value), WriteError> {
    let b = std::fs::read(p).map_err(|_| WriteError::Unreadable)?;
    let v = serde_json::from_slice(&b).map_err(|_| WriteError::Unreadable)?;
    Ok((b, v))
}

/// A fresh file beside `path` with `text`, the same mode; renamed over it by the caller.
fn fresh_beside(path: &Path, text: &[u8]) -> Result<PathBuf, WriteError> {
    let name = path.file_name().ok_or(WriteError::Outside)?;
    let tmp = path.with_file_name(format!(
        ".{}.aibd-{}.tmp",
        name.to_string_lossy(),
        std::process::id()
    ));
    let mode = std::fs::metadata(path)
        .map_err(|_| WriteError::Io)?
        .permissions();
    let wrote = (|| -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&tmp)?;
        f.write_all(text)?;
        f.sync_all()?;
        std::fs::set_permissions(&tmp, mode)
    })();
    if wrote.is_err() {
        let _ = std::fs::remove_file(&tmp);
        return Err(WriteError::Io);
    }
    Ok(tmp)
}

/// `cards.json` is what its provenance says (1.x `refresh` would not say `edited_by_hand`).
fn registry_clean(cards_json: &Path) -> Result<(), WriteError> {
    let (t, doc) = read_json(cards_json)?;
    let (_, p) = read_json(&provenance_of(cards_json))?;
    let ok = p["cards_json_sha256"].as_str() == Some(&sha_hex(&t))
        && p["cards_fingerprint"].as_str() == Some(&cards_fingerprint(&doc));
    if ok {
        Ok(())
    } else {
        Err(WriteError::RegistryEdited)
    }
}

/// Steps 2 and 3: `cards.json` names `new_sha` for `uid`, its provenance names the file and
/// its cards. `repair`: the list already names it (aibd stopped between the two renames) and
/// only the provenance is behind (aibd's own pending write, [`finish_pending`]).
fn registry_point(
    cards_json: &Path,
    uid: &str,
    new_sha: &str,
    repair: bool,
) -> Result<(), WriteError> {
    let prov = provenance_of(cards_json);
    for p in [cards_json, prov.as_path()] {
        let m = std::fs::symlink_metadata(p).map_err(|_| WriteError::Unreadable)?;
        if !m.is_file() {
            return Err(WriteError::Symlink);
        }
    }
    let (t0, doc0) = read_json(cards_json)?;
    let (_, p0) = read_json(&prov)?;
    let old = doc0["cards"][uid]["persona_sha"]
        .as_str()
        .ok_or(WriteError::Unreadable)?
        .to_owned();
    let sha0 = sha_hex(&t0);
    let behind = repair && old == new_sha;
    if !behind
        && (p0["cards_json_sha256"].as_str() != Some(&sha0)
            || p0["cards_fingerprint"].as_str() != Some(&cards_fingerprint(&doc0)))
    {
        return Err(WriteError::RegistryEdited);
    }
    if old == new_sha && !behind {
        return Ok(());
    }
    let text0 = std::str::from_utf8(&t0).map_err(|_| WriteError::Unreadable)?;
    let mut doc1 = doc0.clone();
    doc1["cards"][uid]["persona_sha"] = serde_json::Value::from(new_sha);
    let t1 = if old == new_sha {
        text0.to_owned()
    } else {
        swap_one(text0, "persona_sha", &old, new_sha, &doc1).ok_or(WriteError::Mismatch)?
    };
    let (sha1, fp1) = (sha_hex(t1.as_bytes()), cards_fingerprint(&doc1));
    let (pt0, _) = read_json(&prov)?;
    let ptext0 = std::str::from_utf8(&pt0).map_err(|_| WriteError::Unreadable)?;
    let mut p1 = p0.clone();
    p1["cards_json_sha256"] = serde_json::Value::from(sha1.as_str());
    let half = {
        let mut h = p0.clone();
        h["cards_json_sha256"] = serde_json::Value::from(sha1.as_str());
        h
    };
    p1["cards_fingerprint"] = serde_json::Value::from(fp1.as_str());
    let old_sha = p0["cards_json_sha256"]
        .as_str()
        .ok_or(WriteError::Unreadable)?;
    let old_fp = p0["cards_fingerprint"]
        .as_str()
        .ok_or(WriteError::Unreadable)?;
    let ptext1 = swap_one(ptext0, "cards_json_sha256", old_sha, &sha1, &half)
        .and_then(|h| swap_one(&h, "cards_fingerprint", old_fp, &fp1, &p1))
        .or_else(|| (old_sha == sha1 && old_fp == fp1).then(|| ptext0.to_owned()))
        .ok_or(WriteError::Mismatch)?;
    let ct = if old == new_sha {
        None
    } else {
        Some(fresh_beside(cards_json, t1.as_bytes())?)
    };
    let pt = match fresh_beside(&prov, ptext1.as_bytes()) {
        Ok(p) => p,
        Err(e) => {
            if let Some(c) = &ct {
                let _ = std::fs::remove_file(c);
            }
            return Err(e);
        }
    };
    let drop_both = || {
        if let Some(c) = &ct {
            let _ = std::fs::remove_file(c);
        }
        let _ = std::fs::remove_file(&pt);
    };
    // 1.x takes no lock: the list once more right before (a scan in between is not overwritten)
    if std::fs::read(cards_json).ok().map(|b| sha_hex(&b)) != Some(sha0) {
        drop_both();
        return Err(WriteError::RegistryChanged);
    }
    if let Some(c) = &ct {
        if std::fs::rename(c, cards_json).is_err() {
            drop_both();
            return Err(WriteError::Io);
        }
    }
    if std::fs::rename(&pt, &prov).is_err() {
        let _ = std::fs::remove_file(&pt);
        return Err(WriteError::Io);
    }
    let (b1, d1) = read_json(cards_json)?;
    let (_, q1) = read_json(&prov)?;
    let ok = d1["cards"][uid]["persona_sha"].as_str() == Some(new_sha)
        && q1["cards_json_sha256"].as_str() == Some(&sha_hex(&b1))
        && q1["cards_fingerprint"].as_str() == Some(&cards_fingerprint(&d1));
    if ok {
        Ok(())
    } else {
        Err(WriteError::Mismatch)
    }
}

// ---- the write, its copies, its pending record ------------------------------------------

/// Copies kept in `persona-writes/` (the newest).
pub const COPIES_KEPT: usize = 20;

static DIR: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
/// One write at a time in aibd: every card shares `cards.json` (f163c r5 L1).
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// aibd sets its `<data>/persona-writes` once, when it starts (first call wins).
pub fn set_dir(dir: PathBuf) {
    let _ = DIR.set(dir);
}

fn now_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

/// Copy the card, `cards.json` and its provenance before they are written; keep the newest
/// [`COPIES_KEPT`] copies.
fn keep_copy(dir: &Path, uid: &str, card: &Path, cards_json: &Path) -> Result<(), WriteError> {
    // the time, then a count in this process: two copies in one millisecond stay two, in order
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let at = dir.join(format!("{:020}-{seq:08}-{uid}", now_ms()));
    std::fs::create_dir_all(&at).map_err(|_| WriteError::Io)?;
    let prov = provenance_of(cards_json);
    for (from, name) in [
        (card, "card.md"),
        (cards_json, "cards.json"),
        (prov.as_path(), "cards.json.provenance.json"),
    ] {
        std::fs::copy(from, at.join(name)).map_err(|_| WriteError::Io)?;
    }
    let mut copies: Vec<PathBuf> = std::fs::read_dir(dir)
        .map_err(|_| WriteError::Io)?
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .map(|e| e.path())
        .collect();
    copies.sort();
    let extra = copies.len().saturating_sub(COPIES_KEPT);
    for old in &copies[..extra] {
        let _ = std::fs::remove_dir_all(old);
    }
    Ok(())
}

fn pending_path(dir: &Path, uid: &str) -> PathBuf {
    dir.join(format!("pending-{uid}.json"))
}

/// The three writes, under the lock: what [`write`] does once the refusals are past.
fn write_at(
    cards_json: &Path,
    dir: &Path,
    uid: &str,
    base_sha: &str,
    new_text: &[u8],
    by: &str,
) -> Result<Written, WriteError> {
    let _one = LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let text = std::fs::read_to_string(cards_json).map_err(|_| WriteError::Unreadable)?;
    let cards = crate::personas::parse_cards(&text).map_err(|_| WriteError::Unreadable)?;
    let card = cards
        .iter()
        .find(|c| c.uid == uid)
        .and_then(|c| c.text_path.clone())
        .ok_or(WriteError::Unreadable)?;
    read_checked(&card, uid, base_sha)?;
    // a list edited by hand is found before the card is touched
    registry_clean(cards_json)?;
    std::fs::create_dir_all(dir).map_err(|_| WriteError::Io)?;
    keep_copy(dir, uid, &card, cards_json)?;
    let new_sha = sha_hex(new_text);
    let pending = serde_json::json!({
        "uid": uid, "card_path": card.to_string_lossy(), "base_sha": base_sha,
        "new_sha": new_sha, "by": by, "at_ms": now_ms().to_string(),
    });
    std::fs::write(pending_path(dir, uid), pending.to_string()).map_err(|_| WriteError::Io)?;
    let w = match write_card_file(&card, uid, base_sha, new_text) {
        Ok(w) => w,
        Err(e) => {
            let _ = std::fs::remove_file(pending_path(dir, uid));
            return Err(e);
        }
    };
    // a failure here keeps the pending record: aibd finishes it when it starts
    registry_point(cards_json, uid, &w.new_sha, false)?;
    let _ = std::fs::remove_file(pending_path(dir, uid));
    Ok(w)
}

/// Why `uid` is not written at all (6770f §8 / §9): archived, or merged into another uid.
pub fn refused(db: &aib_store::MessageDb, uid: &str) -> Option<WriteError> {
    match db.archived("persona", uid) {
        Ok(Some(_)) => return Some(WriteError::Archived),
        Ok(None) => {}
        Err(_) => return Some(WriteError::Io),
    }
    match db.alias_resolve(uid) {
        Ok(u) if u == uid => None,
        Ok(_) => Some(WriteError::Aliased),
        Err(_) => Some(WriteError::Io),
    }
}

/// The one write of a persona card (§1 人格改, S2-2, persona_edit): the caller has the
/// owner's yes; the card list and its provenance follow the card.
pub async fn write(
    st: &crate::api::AppState,
    uid: &str,
    base_sha: &str,
    new_text: Vec<u8>,
    by: &str,
) -> Result<Written, WriteError> {
    let cards_json = st
        .personas
        .cards_json()
        .map(Path::to_path_buf)
        .ok_or(WriteError::Unreadable)?;
    let dir = DIR.get().cloned().ok_or(WriteError::Io)?;
    let (db, uid, base, by) = (
        st.messages.clone(),
        uid.to_owned(),
        base_sha.to_owned(),
        by.to_owned(),
    );
    tokio::task::spawn_blocking(move || {
        if let Some(e) = refused(&db, &uid) {
            return Err(e);
        }
        write_at(&cards_json, &dir, &uid, &base, &new_text, &by)
    })
    .await
    .map_err(|_| WriteError::Io)?
}

/// What aibd did with one pending write when it started.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Finished {
    /// The card had its new text: the list and provenance were brought up to it.
    Completed(String),
    /// The card still had its old text: nothing had been written.
    Dropped(String),
    /// Neither, or the list could not be written: kept as `failed-<uid>.json`; scan again.
    Failed(String),
}

/// aibd, when it starts (r5 L1): each `pending-<uid>.json` left by a write that stopped.
pub fn finish_pending(cards_json: &Path, dir: &Path) -> Vec<Finished> {
    let _one = LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Ok(list) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = list
        .filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| n.starts_with("pending-") && n.ends_with(".json"))
        .collect();
    names.sort();
    let mut out = Vec::new();
    for n in names {
        let p = dir.join(&n);
        let rec: Option<serde_json::Value> = std::fs::read(&p)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok());
        let s = |k: &str| rec.as_ref().and_then(|r| r[k].as_str()).map(str::to_owned);
        let (Some(uid), Some(card), Some(base), Some(new)) =
            (s("uid"), s("card_path"), s("base_sha"), s("new_sha"))
        else {
            let _ = std::fs::rename(&p, dir.join(format!("failed-{n}")));
            out.push(Finished::Failed(n));
            continue;
        };
        let now = std::fs::read(&card).ok().map(|b| sha_hex(&b));
        let fail = |out: &mut Vec<Finished>| {
            let _ = std::fs::rename(&p, dir.join(format!("failed-{uid}.json")));
            out.push(Finished::Failed(uid.clone()));
        };
        if now.as_deref() == Some(new.as_str()) {
            if registry_point(cards_json, &uid, &new, true).is_ok() {
                let _ = std::fs::remove_file(&p);
                out.push(Finished::Completed(uid));
            } else {
                fail(&mut out);
            }
        } else if now.as_deref() == Some(base.as_str()) {
            let _ = std::fs::remove_file(&p);
            out.push(Finished::Dropped(uid));
        } else {
            fail(&mut out);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use sha_hex;

    const OLD: &[u8] =
        b"---\nuid: lvkson\ncode: owner-sec\n---\n# \xe4\xba\xba\xe6\xa0\xbc\nrule\n";

    fn card(dir: &Path) -> PathBuf {
        let real = std::fs::canonicalize(dir).unwrap();
        let p = real.join("card.md");
        std::fs::write(&p, OLD).unwrap();
        p
    }

    fn new_text() -> Vec<u8> {
        [OLD, b"rule two\n"].concat()
    }

    #[test]
    fn the_card_is_replaced_and_read_back() {
        let d = tempfile::tempdir().unwrap();
        let p = card(d.path());
        let w = write_card_file(&p, "lvkson", &sha_hex(OLD), &new_text()).unwrap();
        assert_eq!(std::fs::read(&p).unwrap(), new_text());
        assert_eq!(w.new_sha, sha_hex(&new_text()));
        // nothing left beside it
        assert_eq!(std::fs::read_dir(p.parent().unwrap()).unwrap().count(), 1);
    }

    #[test]
    fn a_card_changed_since_the_proposal_is_not_written() {
        let d = tempfile::tempdir().unwrap();
        let p = card(d.path());
        let base = sha_hex(OLD);
        std::fs::write(&p, [OLD, b"x\n"].concat()).unwrap();
        assert_eq!(
            write_card_file(&p, "lvkson", &base, &new_text()),
            Err(WriteError::Changed)
        );
        assert_eq!(std::fs::read(&p).unwrap(), [OLD, b"x\n"].concat());
    }

    /// D4: the card list names the path; a file that is not this persona's card is never
    /// written, even when its sha is what the list says.
    #[test]
    fn a_file_that_is_not_this_personas_card_is_not_written() {
        let d = tempfile::tempdir().unwrap();
        let real = std::fs::canonicalize(d.path()).unwrap();
        let keys = real.join("keys.md");
        std::fs::write(&keys, b"ssh-ed25519 AAAA\n").unwrap();
        assert_eq!(
            write_card_file(&keys, "lvkson", &sha_hex(b"ssh-ed25519 AAAA\n"), b"x\n"),
            Err(WriteError::Outside)
        );
        let other = card(d.path());
        assert_eq!(
            write_card_file(&other, "other1", &sha_hex(OLD), &new_text()),
            Err(WriteError::Outside)
        );
        let txt = real.join("card.txt");
        std::fs::write(&txt, OLD).unwrap();
        assert_eq!(
            write_card_file(&txt, "lvkson", &sha_hex(OLD), &new_text()),
            Err(WriteError::Outside)
        );
        assert_eq!(
            write_card_file(Path::new("card.md"), "lvkson", &sha_hex(OLD), &new_text()),
            Err(WriteError::Outside)
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_link_or_a_second_name_is_not_written() {
        let d = tempfile::tempdir().unwrap();
        let p = card(d.path());
        let real = p.parent().unwrap().to_path_buf();
        let link = real.join("link.md");
        std::os::unix::fs::symlink(&p, &link).unwrap();
        assert_eq!(
            write_card_file(&link, "lvkson", &sha_hex(OLD), &new_text()),
            Err(WriteError::Symlink)
        );
        let sub = real.join("sub");
        std::fs::create_dir(&sub).unwrap();
        let via = real.join("via");
        std::os::unix::fs::symlink(&sub, &via).unwrap();
        std::fs::write(sub.join("c.md"), OLD).unwrap();
        assert_eq!(
            write_card_file(&via.join("c.md"), "lvkson", &sha_hex(OLD), &new_text()),
            Err(WriteError::Symlink)
        );
        let hard = real.join("hard.md");
        std::fs::hard_link(&p, &hard).unwrap();
        assert_eq!(
            write_card_file(&p, "lvkson", &sha_hex(OLD), &new_text()),
            Err(WriteError::Outside)
        );
        assert_eq!(std::fs::read(&p).unwrap(), OLD);
    }

    // ---- the shared write (ebab8 04:01; c2f0b r5 §10) ----------------------------------
    // The 1.x checks run where the 1.x card list runs: unix (6770f 04:1x: no python3 on the
    // Windows cell). PW-R6 runs everywhere.

    #[cfg(unix)]
    const CARD1: &str =
        "---\nuid: \"abc001\"\ncode: t0\nname: \"測試員0\"\nupdated: 2026-10-17T08:40:42.123Z\n---\n你是測試。\n";

    #[cfg(unix)]
    fn card_of(uid: &str, i: usize) -> String {
        CARD1
            .replace("abc001", uid)
            .replace("t0", &format!("t{i}"))
            .replace("測試員0", &format!("測試員{i}"))
    }

    /// A 1.x card library in a folder: two cards, the route contract, `cards.json` and its
    /// provenance made by 1.x `scan` itself. `None` when python3 is not here (CI: a failure).
    #[cfg(unix)]
    struct Lib {
        _t: tempfile::TempDir,
        root: PathBuf,
        out: PathBuf,
        dir: PathBuf,
    }

    #[cfg(unix)]
    const PY: &str = r#"
import sys, json
sys.path.insert(0, sys.argv[5])
import card_registry as CR
mode, out, vault, contract = sys.argv[1:5]
if mode == "scan":
    _v, _l, doc, src = CR.project(vault, contract)
    CR.write_registry(out, doc, src)
    print("scanned")
elif mode == "refresh":
    print(CR.refresh(out, vault, contract)["status"])
else:
    print(json.dumps(json.load(open(out, encoding="utf-8")), ensure_ascii=False, sort_keys=True), end="")
"#;

    #[cfg(unix)]
    impl Lib {
        fn py(&self, mode: &str, out: &Path) -> Option<String> {
            let mem = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/memory");
            let o = std::process::Command::new("python3")
                .arg("-I")
                .arg("-c")
                .arg(PY)
                .arg(mode)
                .arg(out)
                .arg(self.root.join("Vault"))
                .arg(self.root.join("contract.json"))
                .arg(&mem)
                .env("EZ2_VAULT_DIR", self.root.join("Vault"))
                .env("EZ2_ROUTE_REGISTRY", self.root.join("contract.json"))
                .env("EZ2_ROUTE_CONTRACT_SPEC", self.root.join("contract.json"))
                .env("EZ2_AIB_STATE_ROOT", self.root.join("state"))
                .env("HOME", &self.root)
                .output();
            match o {
                Ok(o) if o.status.success() => Some(
                    String::from_utf8_lossy(&o.stdout)
                        .trim_end_matches('\n')
                        .to_owned(),
                ),
                Ok(o) => panic!("1.x {mode}: {}", String::from_utf8_lossy(&o.stderr)),
                Err(e) if std::env::var_os("CI").is_some() => {
                    panic!("reason: no_python3_on_ci {e}")
                }
                Err(e) => {
                    eprintln!("skip: python3 not found ({e}); the 1.x check did not run");
                    None
                }
            }
        }

        fn refresh(&self) -> Option<String> {
            self.py("refresh", &self.out)
        }

        fn card(&self, uid: &str) -> PathBuf {
            self.root.join("Vault/10-Roles").join(uid).join("角色.md")
        }

        fn new() -> Option<Lib> {
            let t = tempfile::tempdir().unwrap();
            let root = std::fs::canonicalize(t.path()).unwrap();
            for (i, uid) in ["abc001", "abc002"].iter().enumerate() {
                let d = root.join("Vault/10-Roles").join(uid);
                std::fs::create_dir_all(&d).unwrap();
                std::fs::write(d.join("角色.md"), card_of(uid, i)).unwrap();
            }
            std::fs::write(
                root.join("contract.json"),
                r#"{"routes": [{"routeKey": "product.expert.agent_library", "folder": "10-Roles"}]}"#,
            )
            .unwrap();
            std::fs::create_dir_all(root.join("state/identity")).unwrap();
            let out = root.join("state/identity/cards.json");
            let lib = Lib {
                dir: root.join("data/persona-writes"),
                out,
                root,
                _t: t,
            };
            lib.py("scan", &lib.out)?;
            assert_eq!(
                lib.refresh().as_deref(),
                Some("current"),
                "reason: fixture_not_current"
            );
            Some(lib)
        }
    }

    #[cfg(unix)]
    fn reg_sha(out: &Path, uid: &str) -> String {
        let v: serde_json::Value = serde_json::from_slice(&std::fs::read(out).unwrap()).unwrap();
        v["cards"][uid]["persona_sha"].as_str().unwrap().to_owned()
    }

    /// PW-R1: the card, its `persona_sha` and the provenance are written, and 1.x says current;
    /// one copy kept, no pending record left.
    #[cfg(unix)]
    #[test]
    fn the_three_writes_leave_the_1x_list_current() {
        let Some(l) = Lib::new() else { return };
        let old = std::fs::read(l.card("abc001")).unwrap();
        let new = [old.as_slice(), "多一條規則。\n".as_bytes()].concat();
        let w = write_at(&l.out, &l.dir, "abc001", &sha_hex(&old), &new, "page:t").unwrap();
        assert_eq!(std::fs::read(l.card("abc001")).unwrap(), new);
        assert_eq!(reg_sha(&l.out, "abc001"), w.new_sha);
        assert_eq!(
            l.refresh().as_deref(),
            Some("current"),
            "reason: list_not_current"
        );
        assert!(
            !pending_path(&l.dir, "abc001").exists(),
            "reason: pending_left"
        );
        let copies = std::fs::read_dir(&l.dir)
            .unwrap()
            .filter(|e| e.as_ref().unwrap().path().is_dir())
            .count();
        assert_eq!(copies, 1);
    }

    /// PW-R2 (f163c r5 L1): two threads write two cards at once; both are in, 1.x current.
    #[cfg(unix)]
    #[test]
    fn two_writes_at_once_both_stay() {
        let Some(l) = Lib::new() else { return };
        let go = |uid: &'static str| {
            let (out, dir, card) = (l.out.clone(), l.dir.clone(), l.card(uid));
            std::thread::spawn(move || {
                let old = std::fs::read(&card).unwrap();
                let new = [old.as_slice(), format!("{uid} 改了。\n").as_bytes()].concat();
                write_at(&out, &dir, uid, &sha_hex(&old), &new, "page:t").map(|w| w.new_sha)
            })
        };
        let (a, b) = (go("abc001"), go("abc002"));
        let (a, b) = (a.join().unwrap().unwrap(), b.join().unwrap().unwrap());
        assert_eq!(reg_sha(&l.out, "abc001"), a, "reason: first_write_lost");
        assert_eq!(reg_sha(&l.out, "abc002"), b, "reason: second_write_lost");
        assert_eq!(l.refresh().as_deref(), Some("current"));
    }

    /// PW-R3: a list edited by hand is not written, and neither is the card.
    #[cfg(unix)]
    #[test]
    fn a_hand_edited_list_writes_nothing() {
        let Some(l) = Lib::new() else { return };
        let t = std::fs::read_to_string(&l.out).unwrap() + " ";
        std::fs::write(&l.out, &t).unwrap();
        let old = std::fs::read(l.card("abc001")).unwrap();
        let new = [old.as_slice(), b"x\n"].concat();
        assert_eq!(
            write_at(&l.out, &l.dir, "abc001", &sha_hex(&old), &new, "page:t"),
            Err(WriteError::RegistryEdited)
        );
        assert_eq!(
            std::fs::read(l.card("abc001")).unwrap(),
            old,
            "reason: card_written"
        );
        assert_eq!(std::fs::read_to_string(&l.out).unwrap(), t);
    }

    /// PW-R4 (r5 L1): a write that stopped after the card is finished when aibd starts; one
    /// that stopped before the card is dropped.
    #[cfg(unix)]
    #[test]
    fn a_stopped_write_is_finished_or_dropped_at_start() {
        let Some(l) = Lib::new() else { return };
        std::fs::create_dir_all(&l.dir).unwrap();
        let rec = |uid: &str, base: &str, new: &str| {
            let card = l.card(uid);
            let v = serde_json::json!({"uid": uid, "card_path": card.to_string_lossy(),
                                       "base_sha": base, "new_sha": new});
            std::fs::write(pending_path(&l.dir, uid), v.to_string()).unwrap();
        };
        let old = std::fs::read(l.card("abc001")).unwrap();
        let new = [old.as_slice(), b"y\n"].concat();
        rec("abc001", &sha_hex(&old), &sha_hex(&new));
        write_card_file(&l.card("abc001"), "abc001", &sha_hex(&old), &new).unwrap();
        assert_eq!(l.refresh().as_deref(), Some("stale"));
        let old2 = std::fs::read(l.card("abc002")).unwrap();
        rec("abc002", &sha_hex(&old2), &sha_hex(b"never"));
        let done = finish_pending(&l.out, &l.dir);
        assert_eq!(
            done,
            vec![
                Finished::Completed("abc001".into()),
                Finished::Dropped("abc002".into())
            ]
        );
        assert_eq!(reg_sha(&l.out, "abc001"), sha_hex(&new));
        assert_eq!(
            l.refresh().as_deref(),
            Some("current"),
            "reason: not_finished"
        );
        assert!(
            !pending_path(&l.dir, "abc001").exists() && !pending_path(&l.dir, "abc002").exists()
        );
    }

    /// PW-R5: the newest copies only.
    #[cfg(unix)]
    #[test]
    fn only_the_newest_copies_are_kept() {
        let Some(l) = Lib::new() else { return };
        std::fs::create_dir_all(&l.dir).unwrap();
        for _ in 0..COPIES_KEPT + 3 {
            keep_copy(&l.dir, "abc001", &l.card("abc001"), &l.out).unwrap();
        }
        let n = std::fs::read_dir(&l.dir).unwrap().count();
        assert_eq!(n, COPIES_KEPT, "reason: copies_not_trimmed");
    }

    /// PW-R6 (6770f §8 / §9): an archived persona and a merged old uid are refused.
    #[test]
    fn an_archived_or_merged_persona_is_refused() {
        let d = tempfile::tempdir().unwrap();
        let db = aib_store::MessageDb::open(&d.path().join("m.db")).unwrap();
        assert_eq!(refused(&db, "abc001"), None);
        db.archive_add(aib_store::ArchivedItem {
            kind: "persona".into(),
            uid: "abc001".into(),
            archived_at: 1,
            keep_until: None,
            reason: "archive".into(),
            batch_id: None,
            copy_dir: None,
            sha256: None,
            restored_at: None,
        })
        .unwrap();
        assert_eq!(refused(&db, "abc001"), Some(WriteError::Archived));
        db.alias_set("abc002", "abc003", "mb_1", 2).unwrap();
        assert_eq!(refused(&db, "abc002"), Some(WriteError::Aliased));
        assert_eq!(refused(&db, "abc003"), None);
    }

    /// PW-R7: the fingerprint's text is 1.x's `json.dumps(sort_keys, ensure_ascii=False)`.
    #[cfg(unix)]
    #[test]
    fn the_compact_text_is_the_1x_text() {
        let Some(l) = Lib::new() else { return };
        let p = l.root.join("odd.json");
        std::fs::write(
            &p,
            r#"{"b": [1, null, true, "引號\"與\\斜線\n\u0001"], "a": {"z": [], "é": {}, "A": "x"}}"#,
        )
        .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&std::fs::read(&p).unwrap()).unwrap();
        let mut s = String::new();
        py_compact(&v, &mut s);
        assert_eq!(l.py("dumps", &p).as_deref(), Some(s.as_str()));
    }
}
