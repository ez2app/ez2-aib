//! Which window the pairing key is asked to sign with on a Mac with Touch ID: the
//! fingerprint only (the default), or the login password too, chosen by the owner
//! (ebab8 11-26 21:15, Andy 「要」; PLAN-owner-method-c2f0b-1126 r2 / r2b / r3, 3e53b
//! pre-read). This module is the pure part: the records, the signed message, and the rule.
//!
//! The real limit is the key's ACL (biometry-current-set or user-presence). The records only
//! decide which window opens, and a window that takes the password opens only when every one
//! of these holds:
//! 1. Touch ID works now (elsewhere the key's own window is used, as before);
//! 2. the last record of the key chain says `to: password`;
//! 3. its signature checks out with its `old_pub`;
//! 4. its `new_pub_sha256` is the current `key.pub`'s and is the pin;
//! 5. the pin was moved by this very record (`record_sha256`), not written at a pairing.
//!
//! The pin moves only by a record signed by the pinned key ([`may_repin`]), up or down. A pin
//! written at a pairing (`record_sha256: null`) never opens the password window, so an AI that
//! deletes both files, puts in a key of its own and waits for the next pairing gets the
//! fingerprint window (r3).
//!
//! Honest limit: an AI of the same account that writes the pin file too (with its own
//! `record_sha256`) can still move this Mac to the password window; then a person must still
//! type the login password in it. It is not an approval by the AI itself.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// `<data>/pair-key-pin.json`.
pub const PIN_FILE: &str = "pair-key-pin.json";
/// `<data>/pair-key-method.json`: the key chain's last record.
pub const RECORD_FILE: &str = "pair-key-method.json";
pub const PIN_SCHEMA: &str = "aib-pair-key-pin/v1";
pub const RECORD_SCHEMA: &str = "aib-pair-key-method/v1";
/// The event of each change.
pub const EVENT: &str = "owner.mode";

/// How the owner check is given.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    /// The fingerprint only.
    Biometry,
    /// The fingerprint or the login password.
    Password,
}

impl Method {
    pub fn word(self) -> &'static str {
        match self {
            Method::Biometry => "biometry",
            Method::Password => "password",
        }
    }
    pub fn parse(s: &str) -> Option<Method> {
        match s {
            "biometry" => Some(Method::Biometry),
            "password" => Some(Method::Password),
            _ => None,
        }
    }
}

pub fn sha256_hex(b: &[u8]) -> String {
    hex(&Sha256::digest(b))
}

pub fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

pub fn unhex(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok())
        .collect()
}

/// The pin: the key the owner paired with, or the one a signed record moved it to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pin {
    pub pub_sha256: String,
    /// `None` when written at a pairing; the record's sha256 when a record moved it.
    pub record_sha256: Option<String>,
    pub at: u64,
}

impl Pin {
    pub fn to_value(&self) -> Value {
        json!({"schema": PIN_SCHEMA, "pub_sha256": self.pub_sha256,
               "record_sha256": self.record_sha256, "at": self.at})
    }
    pub fn from_value(v: &Value) -> Option<Pin> {
        if v["schema"] != PIN_SCHEMA {
            return None;
        }
        let record_sha256 = match &v["record_sha256"] {
            Value::Null => None,
            Value::String(s) if is_sha(s) => Some(s.clone()),
            _ => return None,
        };
        let pub_sha256 = v["pub_sha256"].as_str().filter(|s| is_sha(s))?.to_owned();
        Some(Pin {
            pub_sha256,
            record_sha256,
            at: v["at"].as_u64()?,
        })
    }
}

fn is_sha(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// One link of the key chain: the key `old_pub` signed that the next key is `new_pub_sha256`,
/// made for `to`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub to: Method,
    pub old_pub: Vec<u8>,
    pub new_pub_sha256: String,
    pub at: u64,
    pub sig: Vec<u8>,
}

/// The bytes the old key signs: a version tag, then each part on its own line.
pub fn message(to: Method, old_pub: &[u8], new_pub_sha256: &str, at: u64) -> Vec<u8> {
    format!(
        "aibd/owner-method/v1\n{}\n{}\n{new_pub_sha256}\n{at}\n",
        to.word(),
        hex(old_pub)
    )
    .into_bytes()
}

impl Record {
    pub fn message(&self) -> Vec<u8> {
        message(self.to, &self.old_pub, &self.new_pub_sha256, self.at)
    }
    pub fn to_value(&self) -> Value {
        json!({"schema": RECORD_SCHEMA, "to": self.to.word(), "old_pub": hex(&self.old_pub),
               "new_pub_sha256": self.new_pub_sha256, "at": self.at, "sig": hex(&self.sig)})
    }
    pub fn from_value(v: &Value) -> Option<Record> {
        if v["schema"] != RECORD_SCHEMA {
            return None;
        }
        let new_pub_sha256 = v["new_pub_sha256"].as_str().filter(|s| is_sha(s))?;
        let sig = unhex(v["sig"].as_str()?)?;
        let old_pub = unhex(v["old_pub"].as_str()?)?;
        if sig.is_empty() || old_pub.is_empty() {
            return None;
        }
        Some(Record {
            to: Method::parse(v["to"].as_str()?)?,
            old_pub,
            new_pub_sha256: new_pub_sha256.to_owned(),
            at: v["at"].as_u64()?,
            sig,
        })
    }
    /// The sha256 of the record as written: what a pin it moved names.
    pub fn sha(&self) -> String {
        sha256_hex(self.to_value().to_string().as_bytes())
    }
}

/// Whether `r` may move `pin` to the current key `current_pub`: the pin is `r`'s old key,
/// `r` is signed by it, and `r` names the current key (r2b 1, 2). `verify(pub, msg, sig)`.
pub fn may_repin(
    pin: &Pin,
    r: &Record,
    current_pub: &[u8],
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> bool {
    let pinned_old = pin.pub_sha256 == sha256_hex(&r.old_pub);
    let names_current = r.new_pub_sha256 == sha256_hex(current_pub);
    pinned_old && names_current && verify(&r.old_pub, &r.message(), &r.sig)
}

/// What this aibd reads of the files at start, or after a change.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reading {
    /// The password window may open (all five hold).
    Password,
    /// The fingerprint window, everything consistent.
    Biometry,
    /// The fingerprint window, and selfcheck says why (`owner_key_not_as_paired`, ...).
    BiometryBecause(&'static str),
}

impl Reading {
    pub fn password_window(&self) -> bool {
        *self == Reading::Password
    }
    /// The short word a page / selfcheck shows.
    pub fn word(&self) -> &'static str {
        match self {
            Reading::Password => "password",
            Reading::Biometry => "biometry",
            Reading::BiometryBecause(_) => "unknown",
        }
    }
}

/// The rule (r3 §12): see the module doc. `pin` / `record` are what the files held (None: no
/// file or not readable), `current_pub` the key aibd checked at start.
pub fn read(
    pin: Option<&Pin>,
    record: Option<&Record>,
    current_pub: &[u8],
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> Reading {
    let Some(pin) = pin else {
        return match record {
            None => Reading::Biometry,
            Some(_) => Reading::BiometryBecause("owner_method_no_pin"),
        };
    };
    let current = sha256_hex(current_pub);
    if pin.pub_sha256 != current {
        return Reading::BiometryBecause("owner_key_not_as_paired");
    }
    let Some(r) = record else {
        return Reading::Biometry;
    };
    let signed = verify(&r.old_pub, &r.message(), &r.sig);
    if !signed || r.new_pub_sha256 != current {
        return Reading::BiometryBecause("owner_method_record_not_as_paired");
    }
    let moved_by_it = pin.record_sha256.as_deref() == Some(r.sha().as_str());
    match (r.to, moved_by_it) {
        (Method::Password, true) => Reading::Password,
        (Method::Password, false) => Reading::BiometryBecause("owner_method_pin_not_moved"),
        (Method::Biometry, _) => Reading::Biometry,
    }
}

/// After a crash between the swap and the pin (r2b 2): the pin is still the record's old key,
/// the record is signed by it and names the current key ⇒ the pin is moved now, by this
/// record. Anything else: nothing to do.
pub fn repin_at_start(
    pin: &Pin,
    record: &Record,
    current_pub: &[u8],
    at: u64,
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> Option<Pin> {
    may_repin(pin, record, current_pub, verify).then(|| Pin {
        pub_sha256: sha256_hex(current_pub),
        record_sha256: Some(record.sha()),
        at,
    })
}

/// The pin written when the owner pairs and no pin is there yet.
pub fn pin_at_pairing(current_pub: &[u8], at: u64) -> Pin {
    Pin {
        pub_sha256: sha256_hex(current_pub),
        record_sha256: None,
        at,
    }
}

/// `owner_method_set` body: `{type, to}` only.
pub fn parse_set(body: &Value) -> Option<Method> {
    let o = body.as_object()?;
    if o.keys().any(|k| !matches!(k.as_str(), "type" | "to")) {
        return None;
    }
    Method::parse(o.get("to")?.as_str()?)
}

// ---------------------------------------------------------------- aibd's own state

use std::path::{Path, PathBuf};

use crate::touch::{KeyDevice, KeyFiles, SharedPresence, PUBLIC_KEY_LEN};

/// What this aibd read of the files (at start, after a pairing, after a change), and the pin
/// it compares the signing key with before each signature (3e53b r3 must-add). Memory only:
/// a file changed while aibd runs counts from its next start.
struct Memory {
    reading: Reading,
    pin_sha: Option<String>,
}

static MEMORY: std::sync::Mutex<Option<Memory>> = std::sync::Mutex::new(None);
static DATA: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
/// The pairing key and the one-window gate, set at start where the key passed its check.
static OWNER: std::sync::OnceLock<(SharedPresence, std::sync::Arc<crate::pairing::Pairing>)> =
    std::sync::OnceLock::new();

fn remember(reading: Reading, pin: Option<&Pin>) {
    if let Reading::BiometryBecause(why) = reading {
        tracing::warn!(
            why,
            "owner check: the fingerprint only (the key or its records are not as paired)"
        );
    }
    if let Ok(mut m) = MEMORY.lock() {
        *m = Some(Memory {
            reading,
            pin_sha: pin.map(|p| p.pub_sha256.clone()),
        });
    }
}

/// What the owner check gives now: `biometry`, `password`, `unknown` (the key or its records
/// are not as paired: the fingerprint only), or `None` before anything was read.
pub fn reading_now() -> Option<Reading> {
    MEMORY.lock().ok()?.as_ref().map(|m| m.reading.clone())
}

/// [`crate::owner_ok`]'s `owner.answer.method`: how a yes was given here.
pub fn method_word() -> &'static str {
    if cfg!(windows) {
        return "windows_hello";
    }
    reading_now().map_or("biometry", |r| {
        if r.password_window() {
            "password"
        } else {
            "biometry"
        }
    })
}

/// Whether the key `public` is about to sign with the password window: the owner chose it and
/// `public` is the pinned key. A key that is not the pin: the fingerprint, and said.
pub fn password_window_for(public: &[u8]) -> bool {
    let Ok(m) = MEMORY.lock() else {
        return false;
    };
    m.as_ref()
        .is_some_and(|m| allows(&m.reading, m.pin_sha.as_deref(), public))
}

/// [`password_window_for`] of what was read: the password chosen, and the key about to sign
/// is the pinned one (3e53b r3 must-add: a key not the pin signs with the fingerprint only).
pub fn allows(reading: &Reading, pin_sha: Option<&str>, public: &[u8]) -> bool {
    let chosen = reading.password_window();
    let pinned = pin_sha == Some(sha256_hex(public).as_str());
    if chosen && !pinned {
        tracing::warn!(
            why = "owner_key_not_as_paired",
            "owner check: this signature takes the fingerprint only"
        );
    }
    chosen && pinned
}

fn read_json(p: &Path) -> Option<Value> {
    let m = std::fs::symlink_metadata(p).ok()?;
    if !m.file_type().is_file() {
        return None;
    }
    serde_json::from_slice(&std::fs::read(p).ok()?).ok()
}

pub fn read_pin(data: &Path) -> Option<Pin> {
    Pin::from_value(&read_json(&data.join(PIN_FILE))?)
}

pub fn read_record(data: &Path) -> Option<Record> {
    Record::from_value(&read_json(&data.join(RECORD_FILE))?)
}

fn write_json(p: &Path, v: &Value) -> Result<(), &'static str> {
    crate::platform::write_private_file(p, v.to_string().as_bytes())
        .map_err(|_| "owner_method_store")
}

const NEW_DIR: &str = "pair-key.new";
const OLD_PREFIX: &str = "pair-key.old.";

/// At start, before the key is read (r2b): a change cut short leaves `pair-key.old.<at>` and
/// no `pair-key` ⇒ the newest old one is put back; a `pair-key.new` is never taken and is
/// removed.
pub fn mend_dirs(data: &Path) {
    let is_dir = |p: &Path| std::fs::symlink_metadata(p).is_ok_and(|m| m.file_type().is_dir());
    let new = data.join(NEW_DIR);
    if is_dir(&new) {
        let _ = std::fs::remove_dir_all(&new);
    }
    let key = crate::touch::key_dir(data);
    if std::fs::symlink_metadata(&key).is_ok() {
        return;
    }
    let mut olds: Vec<PathBuf> = std::fs::read_dir(data)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(OLD_PREFIX))
                && is_dir(p)
        })
        .collect();
    olds.sort();
    if let Some(back) = olds.pop() {
        let _ = std::fs::rename(&back, &key);
        tracing::warn!("owner check: a change of method was cut short; the old key is back");
    }
}

/// At start, after the key passed its check: mend a pin a crash left behind (r2b 2), then read.
pub fn read_at_start(
    data: &Path,
    current_pub: &[u8],
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) {
    let _ = DATA.set(data.to_path_buf());
    let mut pin = read_pin(data);
    let record = read_record(data);
    if let (Some(p), Some(r)) = (pin.as_ref(), record.as_ref()) {
        if let Some(moved) =
            repin_at_start(p, r, current_pub, crate::pair_carry::now_unix(), &verify)
        {
            if write_json(&data.join(PIN_FILE), &moved.to_value()).is_ok() {
                pin = Some(moved);
            }
        }
    }
    let reading = read(pin.as_ref(), record.as_ref(), current_pub, &verify);
    remember(reading, pin.as_ref());
}

/// The key and the gate owner_method_set signs with (server.rs, where the key passed).
pub fn set_owner(p: SharedPresence, gate: std::sync::Arc<crate::pairing::Pairing>) {
    let _ = OWNER.set((p, gate));
}

/// A pairing the owner confirmed: where no pin is there yet, the key it was confirmed with is
/// pinned (`record_sha256: null`, so it never opens the password window by itself).
pub fn after_pairing(current_pub: &[u8]) {
    let Some(data) = DATA.get() else {
        return;
    };
    if read_pin(data).is_some() {
        return;
    }
    let pin = pin_at_pairing(current_pub, crate::pair_carry::now_unix());
    if write_json(&data.join(PIN_FILE), &pin.to_value()).is_ok() {
        let record = read_record(data);
        let verify = |k: &[u8], m: &[u8], s: &[u8]| verify_any(k, m, s);
        remember(
            read(Some(&pin), record.as_ref(), current_pub, verify),
            Some(&pin),
        );
    }
}

#[cfg(target_os = "macos")]
fn verify_any(public: &[u8], msg: &[u8], sig: &[u8]) -> bool {
    aib_sys::presence_macos::verify(public, msg, sig) == Ok(true)
}

#[cfg(not(target_os = "macos"))]
fn verify_any(_: &[u8], _: &[u8], _: &[u8]) -> bool {
    false
}

/// One change of method (r2b / r3 order): a new key in `pair-key.new` made and checked → the
/// current (pinned) key signs `{to, old_pub, new_pub_sha256, at}` → the record → the swap
/// (`pair-key` to `pair-key.old.<at>`, `.new` to `pair-key`) → the key in use → the pin → the
/// old folder gone. Any failure before the swap removes `.new` and keeps the old key and its
/// records. Returns the method it was.
/// Checks a signature: `(public key, message, signature)`.
pub type VerifyFn = dyn Fn(&[u8], &[u8], &[u8]) -> bool;
/// Makes a key for a method: its blob and public key.
pub type CreateFn = dyn Fn(Method) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), String>;

pub struct Change<'a> {
    pub data: &'a Path,
    pub to: Method,
    pub presence: &'a dyn crate::touch::Presence,
    pub create: &'a CreateFn,
    pub dev: &'a dyn KeyDevice,
    pub verify: &'a VerifyFn,
    pub at: u64,
    pub reason: String,
    pub limit: std::time::Duration,
}

pub fn change(c: &Change<'_>) -> Result<Method, &'static str> {
    let current = c.presence.public().ok_or("owner_method_not_here")?;
    let pin = read_pin(c.data).ok_or("owner_key_not_as_paired")?;
    if pin.pub_sha256 != sha256_hex(&current) {
        return Err("owner_key_not_as_paired");
    }
    let record = read_record(c.data);
    let from = match read(Some(&pin), record.as_ref(), &current, c.verify) {
        Reading::Password => Method::Password,
        Reading::Biometry => Method::Biometry,
        Reading::BiometryBecause(why) => return Err(why),
    };
    if from == c.to {
        return Ok(from);
    }
    let new_dir = c.data.join(NEW_DIR);
    let _ = std::fs::remove_dir_all(&new_dir);
    let drop_new = || {
        let _ = std::fs::remove_dir_all(&new_dir);
    };
    if crate::touch::make_pair_key(&new_dir, || (c.create)(c.to), c.dev, |_| {}).is_err() {
        drop_new();
        return Err("owner_method_new_key");
    }
    let new = match crate::touch::load(&new_dir) {
        Ok(k) => k,
        Err(why) => {
            drop_new();
            return Err(why);
        }
    };
    let new_sha = sha256_hex(&new.public);
    let msg = message(c.to, &current, &new_sha, c.at);
    let mut sig = Vec::new();
    if let Err(why) = c
        .presence
        .ask_until_keep(&c.reason, &msg, c.limit, &mut sig)
    {
        drop_new();
        return Err(why);
    }
    if !(c.verify)(&current, &msg, &sig) {
        drop_new();
        return Err("pair_touch_bad_signature");
    }
    let r = Record {
        to: c.to,
        old_pub: current.to_vec(),
        new_pub_sha256: new_sha,
        at: c.at,
        sig,
    };
    if let Err(why) = write_json(&c.data.join(RECORD_FILE), &r.to_value()) {
        drop_new();
        return Err(why);
    }
    swap_in(c, &new_dir, new, &pin, &r)?;
    Ok(from)
}

fn swap_in(
    c: &Change<'_>,
    new_dir: &Path,
    new: KeyFiles,
    pin: &Pin,
    r: &Record,
) -> Result<(), &'static str> {
    let key = crate::touch::key_dir(c.data);
    let old = c.data.join(format!("{OLD_PREFIX}{}", c.at));
    std::fs::rename(&key, &old).map_err(|_| "owner_method_swap")?;
    if std::fs::rename(new_dir, &key).is_err() {
        let _ = std::fs::rename(&old, &key);
        let _ = std::fs::remove_dir_all(new_dir);
        return Err("owner_method_swap");
    }
    let public = new.public;
    if let Err(why) = c.presence.replace_key(new) {
        // the key in use did not change: its folder back, the new one away
        let _ = std::fs::rename(&key, new_dir);
        let _ = std::fs::rename(&old, &key);
        let _ = std::fs::remove_dir_all(new_dir);
        return Err(why);
    }
    // a crash here leaves pin = old key: mended at the next start (repin_at_start)
    let moved = repin_at_start(pin, r, &public, c.at, c.verify).ok_or("owner_method_pin")?;
    write_json(&c.data.join(PIN_FILE), &moved.to_value())?;
    remember(read(Some(&moved), Some(r), &public, c.verify), Some(&moved));
    let _ = std::fs::remove_dir_all(&old);
    Ok(())
}

// ---------------------------------------------------------------- the page's acts

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;

pub const SET: &str = "owner_method_set";
pub const STATUS: &str = "owner_method_status";

fn refuse(status: StatusCode, reason: &str) -> Response {
    (
        status,
        Json(json!({"code": if status == StatusCode::BAD_REQUEST { "invalid_request" } else { "conflict" },
                    "reason": reason, "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

/// `owner_method_status`: `{method, can_change, why?}` (the settings page shows the method as
/// it is, 3e53b 21:19). Windows: `windows_hello`, nothing to choose.
pub async fn status() -> Response {
    if cfg!(windows) {
        return Json(json!({"method": "windows_hello", "can_change": false})).into_response();
    }
    let r = reading_now();
    let why = match &r {
        Some(Reading::BiometryBecause(w)) => json!(w),
        _ => Value::Null,
    };
    Json(json!({
        "method": r.as_ref().map_or("unknown", Reading::word),
        "can_change": cfg!(target_os = "macos") && OWNER.get().is_some() && why.is_null() && r.is_some(),
        "why": why,
    }))
    .into_response()
}

/// `owner_method_set {to}`, for the built-in page `by`: the pairing key's own window signs the
/// change (the fingerprint for a fingerprint key), then the key is swapped (r2b / r3).
pub async fn set(st: crate::api::AppState, body: Value, by: String) -> Response {
    let Some(to) = parse_set(&body) else {
        return refuse(StatusCode::BAD_REQUEST, "owner_method_body");
    };
    if !cfg!(target_os = "macos") {
        return refuse(StatusCode::CONFLICT, "owner_method_not_here");
    }
    let Some((presence, gate)) = OWNER.get().cloned() else {
        return refuse(StatusCode::CONFLICT, "owner_method_not_here");
    };
    let data = crate::registry::data_of(&st);
    let done = tokio::task::spawn_blocking(move || {
        gate.window_open()?;
        let got = change_here(&data, to, presence.as_ref());
        gate.window_closed(crate::touch::ends_quietly(&got.map(|_| ())));
        got
    })
    .await
    .unwrap_or(Err("owner_method_store"));
    match done {
        Ok(from) => {
            let at = crate::pair_carry::now_unix();
            if from != to {
                crate::seats::side_event(
                    &st,
                    SET,
                    EVENT,
                    json!({"from": from.word(), "to": to.word(), "by": by, "at": at}),
                )
                .await;
            }
            Json(json!({"done": true, "method": to.word()})).into_response()
        }
        Err(why) => refuse(StatusCode::CONFLICT, why),
    }
}

/// The window's words: what the change is, said as is (3e53b r1 c).
pub fn reason_words(to: Method) -> &'static str {
    match to {
        Method::Password => {
            "把 AIB 的本人確認改成「指紋或登入密碼」(較弱：知道登入密碼的人也能核准)"
        }
        Method::Biometry => "把 AIB 的本人確認改回「只用指紋」",
    }
}

#[cfg(target_os = "macos")]
fn change_here(
    data: &Path,
    to: Method,
    presence: &dyn crate::touch::Presence,
) -> Result<Method, &'static str> {
    use aib_sys::presence_macos::{create, Acl};
    let make = |m: Method| {
        let acl = match m {
            Method::Biometry => Acl::BiometryCurrentSet,
            Method::Password => Acl::UserPresence,
        };
        create(acl).map_err(|e| e.code().to_owned())
    };
    change(&Change {
        data,
        to,
        presence,
        create: &make,
        dev: &crate::touch::MacSe,
        verify: &verify_any,
        at: crate::pair_carry::now_unix(),
        reason: reason_words(to).to_owned(),
        limit: std::time::Duration::from_secs(120),
    })
}

#[cfg(not(target_os = "macos"))]
fn change_here(
    _: &Path,
    _: Method,
    _: &dyn crate::touch::Presence,
) -> Result<Method, &'static str> {
    Err("owner_method_not_here")
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use crate::touch::Presence;

    const OLD: &[u8] = b"old-key-public";
    const NEW: &[u8] = b"new-key-public";

    /// A stand-in signature: the key's bytes then the message, so it checks only for that key.
    fn sign(public: &[u8], msg: &[u8]) -> Vec<u8> {
        [public, msg].concat()
    }
    pub(super) fn verify(public: &[u8], msg: &[u8], sig: &[u8]) -> bool {
        sig == sign(public, msg).as_slice()
    }

    fn record(to: Method, old: &[u8], new: &[u8]) -> Record {
        let new_sha = sha256_hex(new);
        let sig = sign(old, &message(to, old, &new_sha, 7));
        Record {
            to,
            old_pub: old.to_vec(),
            new_pub_sha256: new_sha,
            at: 7,
            sig,
        }
    }

    fn moved(r: &Record, new: &[u8]) -> Pin {
        Pin {
            pub_sha256: sha256_hex(new),
            record_sha256: Some(r.sha()),
            at: 8,
        }
    }

    #[test]
    fn the_password_window_opens_only_for_a_pin_moved_by_the_signed_record() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(
            read(Some(&moved(&r, NEW)), Some(&r), NEW, verify),
            Reading::Password
        );
        // no record, or no files at all: the fingerprint
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(read(Some(&paired), None, NEW, verify), Reading::Biometry);
        assert_eq!(read(None, None, NEW, verify), Reading::Biometry);
        // back to the fingerprint: a record too, and the fingerprint
        let back = record(Method::Biometry, NEW, OLD);
        assert_eq!(
            read(Some(&moved(&back, OLD)), Some(&back), OLD, verify),
            Reading::Biometry
        );
    }

    #[test]
    fn each_signature_takes_the_password_only_with_the_pinned_key() {
        let pin = sha256_hex(NEW);
        assert!(allows(&Reading::Password, Some(&pin), NEW));
        assert!(
            !allows(&Reading::Password, Some(&pin), b"swapped while running"),
            "reason: unpinned_key_password"
        );
        assert!(!allows(&Reading::Password, None, NEW));
        assert!(!allows(&Reading::Biometry, Some(&pin), NEW));
    }

    #[test]
    fn a_pin_written_at_a_pairing_never_opens_the_password_window() {
        // r3: both files gone, a key of the AI's own, the next pairing pins it, then a record
        // signed by a key of the AI's own (old_pub is only a public key)
        let r = record(Method::Password, b"any-software-key", NEW);
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(
            read(Some(&paired), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_pin_not_moved")
        );
    }

    #[test]
    fn a_record_not_signed_by_its_old_key_is_the_fingerprint() {
        let mut r = record(Method::Password, OLD, NEW);
        let pin = moved(&r, NEW);
        r.sig = sign(b"another", &r.message());
        assert_eq!(
            read(Some(&pin), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_record_not_as_paired")
        );
        assert!(!may_repin(&pin_at_pairing(OLD, 1), &r, NEW, verify));
    }

    #[test]
    fn a_key_other_than_the_pinned_one_is_the_fingerprint_and_said() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(
            read(Some(&moved(&r, NEW)), Some(&r), b"swapped", verify),
            Reading::BiometryBecause("owner_key_not_as_paired")
        );
        assert_eq!(
            read(None, Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_no_pin")
        );
        assert_eq!(read(None, Some(&r), NEW, verify).word(), "unknown");
    }

    #[test]
    fn the_pin_moves_only_from_the_records_old_key_to_the_current_one() {
        let r = record(Method::Password, OLD, NEW);
        let pin = pin_at_pairing(OLD, 1);
        assert!(may_repin(&pin, &r, NEW, verify));
        // the pin is not the record's old key
        assert!(!may_repin(&pin_at_pairing(b"other", 1), &r, NEW, verify));
        // the record names another key than the current one
        assert!(!may_repin(&pin, &r, b"other", verify));
    }

    #[test]
    fn a_crash_between_the_swap_and_the_pin_is_mended_at_start_only_when_consistent() {
        let r = record(Method::Password, OLD, NEW);
        let got = repin_at_start(&pin_at_pairing(OLD, 1), &r, NEW, 9, verify).unwrap();
        assert_eq!(got, moved(&r, NEW).clone_at(9));
        assert_eq!(read(Some(&got), Some(&r), NEW, verify), Reading::Password);
        // not consistent: nothing mended, and the reading says so
        assert_eq!(
            repin_at_start(&pin_at_pairing(b"x", 1), &r, NEW, 9, verify),
            None
        );
        assert_eq!(
            read(Some(&pin_at_pairing(OLD, 1)), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_key_not_as_paired")
        );
    }

    #[test]
    fn the_records_go_through_their_files_and_nothing_else_is_read() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(Record::from_value(&r.to_value()), Some(r.clone()));
        let p = moved(&r, NEW);
        assert_eq!(Pin::from_value(&p.to_value()), Some(p.clone()));
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(Pin::from_value(&paired.to_value()), Some(paired));
        let mut v = r.to_value();
        v["to"] = json!("anything");
        assert_eq!(Record::from_value(&v), None);
        let mut v = p.to_value();
        v["record_sha256"] = json!("ABC");
        assert_eq!(Pin::from_value(&v), None);
    }

    #[test]
    fn a_set_body_names_to_and_nothing_else() {
        assert_eq!(
            parse_set(&json!({"type": "owner_method_set", "to": "password"})),
            Some(Method::Password)
        );
        assert_eq!(
            parse_set(&json!({"type": "owner_method_set", "to": "biometry"})),
            Some(Method::Biometry)
        );
        for bad in [
            json!({"type": "owner_method_set"}),
            json!({"type": "owner_method_set", "to": "password", "acl": "user_presence"}),
            json!({"type": "owner_method_set", "to": "pin"}),
        ] {
            assert_eq!(parse_set(&bad), None, "{bad}");
        }
    }

    /// A device whose public key is the blob's first byte, repeated.
    struct Dev {
        sign_ok: bool,
        probe_ok: bool,
    }

    impl crate::touch::KeyDevice for Dev {
        fn probe(&self, _: &[u8]) -> Result<(), &'static str> {
            if self.probe_ok {
                Ok(())
            } else {
                Err("weak")
            }
        }
        fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
            Ok([blob[0]; PUBLIC_KEY_LEN])
        }
        fn sign(&self, blob: &[u8], _: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
            if self.sign_ok {
                Ok(sign(&[blob[0]; PUBLIC_KEY_LEN], msg))
            } else {
                Err("user_cancel")
            }
        }
        fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
            super::tests::verify(public, msg, sig)
        }
    }

    fn put_key(dir: &Path, b: u8) {
        std::fs::create_dir_all(dir).unwrap();
        for (n, v) in [("key.blob", vec![b]), ("key.pub", vec![b; PUBLIC_KEY_LEN])] {
            crate::platform::write_private_file(&dir.join(n), &v).unwrap();
        }
    }

    /// A data folder with key 1 in pair-key, pinned at a pairing, and its presence.
    fn machine(sign_ok: bool) -> (tempfile::TempDir, crate::touch::SwapPresence) {
        let t = tempfile::tempdir().unwrap();
        put_key(&crate::touch::key_dir(t.path()), 1);
        let pin = pin_at_pairing(&[1; PUBLIC_KEY_LEN], 1);
        write_json(&t.path().join(PIN_FILE), &pin.to_value()).unwrap();
        let p = crate::touch::SwapPresence::new(
            Box::new(move || {
                Box::new(Dev {
                    sign_ok,
                    probe_ok: true,
                }) as Box<dyn KeyDevice>
            }),
            KeyFiles {
                blob: vec![1],
                public: [1; PUBLIC_KEY_LEN],
            },
        )
        .unwrap();
        (t, p)
    }

    fn run(
        data: &Path,
        to: Method,
        p: &dyn crate::touch::Presence,
        made: &std::cell::Cell<u32>,
        new_probe_ok: bool,
    ) -> Result<Method, &'static str> {
        let create = |m: Method| {
            made.set(made.get() + 1);
            let b = if m == Method::Password { 2 } else { 3 };
            Ok((vec![b], [b; PUBLIC_KEY_LEN]))
        };
        change(&Change {
            data,
            to,
            presence: p,
            create: &create,
            dev: &Dev {
                sign_ok: true,
                probe_ok: new_probe_ok,
            },
            verify: &verify,
            at: 50,
            reason: reason_words(to).into(),
            limit: std::time::Duration::from_secs(1),
        })
    }

    fn reading_of(data: &Path) -> Reading {
        let public = std::fs::read(crate::touch::key_dir(data).join("key.pub")).unwrap();
        read(
            read_pin(data).as_ref(),
            read_record(data).as_ref(),
            &public,
            verify,
        )
    }

    #[test]
    fn a_change_is_signed_by_the_pinned_key_then_swapped_and_pinned() {
        let (t, p) = machine(true);
        let made = std::cell::Cell::new(0);
        assert_eq!(
            run(t.path(), Method::Password, &p, &made, true),
            Ok(Method::Biometry)
        );
        let d = t.path();
        assert_eq!(
            std::fs::read(crate::touch::key_dir(d).join("key.pub")).unwrap(),
            vec![2; PUBLIC_KEY_LEN]
        );
        assert_eq!(
            p.public(),
            Some([2; PUBLIC_KEY_LEN]),
            "reason: key_in_use_not_swapped"
        );
        assert_eq!(reading_of(d), Reading::Password);
        let r = read_record(d).unwrap();
        assert_eq!(
            r.old_pub,
            vec![1; PUBLIC_KEY_LEN],
            "reason: not_signed_by_the_pinned_key"
        );
        assert!(!d.join(NEW_DIR).exists() && !d.join(format!("{OLD_PREFIX}50")).exists());
        // and back: signed by the password key this time (r2b 1)
        assert_eq!(
            run(d, Method::Biometry, &p, &made, true),
            Ok(Method::Password)
        );
        assert_eq!(reading_of(d), Reading::Biometry);
        assert_eq!(read_record(d).unwrap().old_pub, vec![2; PUBLIC_KEY_LEN]);
        // the same again: nothing made, nothing asked
        let before = made.get();
        assert_eq!(
            run(d, Method::Biometry, &p, &made, true),
            Ok(Method::Biometry)
        );
        assert_eq!(made.get(), before, "reason: same_method_made_a_key");
    }

    #[test]
    fn a_window_said_no_to_keeps_the_old_key_and_its_records() {
        let (t, p) = machine(false);
        let made = std::cell::Cell::new(0);
        assert_eq!(
            run(t.path(), Method::Password, &p, &made, true),
            Err("user_cancel")
        );
        let d = t.path();
        assert_eq!(
            std::fs::read(crate::touch::key_dir(d).join("key.pub")).unwrap(),
            vec![1; PUBLIC_KEY_LEN]
        );
        assert!(read_record(d).is_none(), "reason: record_without_the_owner");
        assert!(!d.join(NEW_DIR).exists(), "reason: new_key_left");
        assert_eq!(reading_of(d), Reading::Biometry);
    }

    #[test]
    fn a_new_key_that_fails_its_check_keeps_the_old_one() {
        let (t, p) = machine(true);
        let made = std::cell::Cell::new(0);
        assert_eq!(
            run(t.path(), Method::Password, &p, &made, false),
            Err("owner_method_new_key")
        );
        let d = t.path();
        assert_eq!(
            std::fs::read(crate::touch::key_dir(d).join("key.pub")).unwrap(),
            vec![1; PUBLIC_KEY_LEN]
        );
        assert!(!d.join(NEW_DIR).exists());
        assert_eq!(p.public(), Some([1; PUBLIC_KEY_LEN]));
    }

    #[test]
    fn a_key_other_than_the_pinned_one_cannot_change_the_method() {
        let (t, p) = machine(true);
        let other = pin_at_pairing(&[9; PUBLIC_KEY_LEN], 1);
        write_json(&t.path().join(PIN_FILE), &other.to_value()).unwrap();
        let made = std::cell::Cell::new(0);
        assert_eq!(
            run(t.path(), Method::Password, &p, &made, true),
            Err("owner_key_not_as_paired")
        );
        assert_eq!(made.get(), 0, "reason: key_made_for_an_unpinned_key");
        std::fs::remove_file(t.path().join(PIN_FILE)).unwrap();
        assert_eq!(
            run(t.path(), Method::Password, &p, &made, true),
            Err("owner_key_not_as_paired")
        );
    }

    #[test]
    fn a_change_cut_short_puts_the_old_key_back_and_never_takes_the_new_one() {
        let t = tempfile::tempdir().unwrap();
        let d = t.path();
        put_key(&d.join(format!("{OLD_PREFIX}40")), 1);
        put_key(&d.join(NEW_DIR), 2);
        mend_dirs(d);
        assert_eq!(
            std::fs::read(crate::touch::key_dir(d).join("key.pub")).unwrap(),
            vec![1; PUBLIC_KEY_LEN]
        );
        assert!(!d.join(NEW_DIR).exists(), "reason: new_key_kept");
        // a pair-key there: left as it is, .new still removed
        put_key(&d.join(NEW_DIR), 2);
        mend_dirs(d);
        assert!(!d.join(NEW_DIR).exists());
        assert_eq!(
            std::fs::read(crate::touch::key_dir(d).join("key.pub")).unwrap(),
            vec![1; PUBLIC_KEY_LEN]
        );
    }

    impl Pin {
        fn clone_at(&self, at: u64) -> Pin {
            Pin { at, ..self.clone() }
        }
    }
}
