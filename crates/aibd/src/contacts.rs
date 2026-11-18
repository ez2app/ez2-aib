//! S89-talk-2b (IFACE-S89-talk-2b-7b81e-1118): the contact center's own data, kept by aibd
//! for the built-in page. What the person set about each contact (friend, group, blocked,
//! muted, pinned, archived, note, tags) and the page's effect sliders (`contacts_fx`).
//!
//! - **One private file**, `<data>/contacts.json` (0600 in the 0700 data folder; written by
//!   [`crate::platform::write_private_file`]: locked down before the bytes go in, then renamed
//!   over). Not a store migration: a database newer than the binary refuses to start
//!   (`UnknownMigration`), which would break going back to an earlier aibd on this machine.
//! - **A broken file is never overwritten**: reads and writes answer 503 `store_unreadable`
//!   and the file stays as it is for a person to look at.
//! - **Every write names the revision it read** (`expected_revision`); another one is 409
//!   `revision_conflict`. One lock around read-change-write, so two writes never interleave.
//! - Blocked / muted change what the page shows only; they do not stop a seat's message
//!   (IFACE §0.3, decided with the seat-send path later).

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const CONTACTS_FILE: &str = "contacts.json";
pub const MAX_NOTE: usize = 200;
pub const MAX_TAGS: usize = 8;
pub const MAX_TAG: usize = 24;
pub const MAX_GROUP: usize = 16;
/// The one preference name this version knows (IFACE §4).
pub const FX: &str = "contacts_fx";

/// What the person set about one contact.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Entry {
    pub friend: bool,
    pub group: Option<String>,
    pub blocked: bool,
    pub muted: bool,
    /// RFC 3339 UTC (`…Z`); `None` with `muted` = muted until undone.
    pub mute_until: Option<String>,
    pub pinned: bool,
    pub archived_at: Option<String>,
    pub note: Option<String>,
    pub tags: Vec<String>,
    pub revision: u64,
}

impl Entry {
    /// Muted now: muted, and either for good or until a time still ahead.
    pub fn muted_at(&self, now: &str) -> bool {
        self.muted && self.mute_until.as_deref().is_none_or(|u| u > now)
    }
}

/// The page's five effect sliders, 0–100 in steps of 10 (SPEC-contacts-center-A §3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Fx {
    pub size: u8,
    pub speed: u8,
    pub count: u8,
    pub length: u8,
    pub alpha: u8,
}

impl Default for Fx {
    fn default() -> Self {
        Fx {
            size: 70,
            speed: 20,
            count: 10,
            length: 100,
            alpha: 30,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
struct Prefs {
    contacts_fx: Option<Fx>,
    contacts_fx_revision: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Doc {
    version: u32,
    #[serde(default)]
    contacts: BTreeMap<String, Entry>,
    #[serde(default)]
    prefs: Prefs,
}

impl Default for Doc {
    fn default() -> Self {
        Doc {
            version: 1,
            contacts: BTreeMap::new(),
            prefs: Prefs::default(),
        }
    }
}

/// Why a read or write of the file did not happen.
#[derive(Debug)]
pub enum BookError {
    /// The file is there but is not this version's JSON: left as it is.
    Unreadable(String),
    Io(std::io::Error),
}

/// Why a `contact_set` / `prefs_set` was refused (status, code, message).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused(pub u16, pub &'static str, pub String);

fn bad(message: impl Into<String>) -> Refused {
    Refused(400, "invalid_request", message.into())
}

/// The contacts file of one aibd. Nothing is read or written until asked.
pub struct Book {
    path: PathBuf,
    lock: Mutex<()>,
}

impl Book {
    /// `dir` = aibd's data folder (already private).
    pub fn new(dir: &Path) -> Self {
        Book {
            path: dir.join(CONTACTS_FILE),
            lock: Mutex::new(()),
        }
    }

    fn load(&self) -> Result<Doc, BookError> {
        let bytes = match std::fs::read(&self.path) {
            Ok(b) => b,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Doc::default()),
            Err(e) => return Err(BookError::Io(e)),
        };
        let doc: Doc = serde_json::from_slice(&bytes)
            .map_err(|e| BookError::Unreadable(format!("{CONTACTS_FILE}: {e}")))?;
        if doc.version != 1 {
            return Err(BookError::Unreadable(format!(
                "{CONTACTS_FILE}: version {} is not 1",
                doc.version
            )));
        }
        Ok(doc)
    }

    fn save(&self, doc: &Doc) -> Result<(), BookError> {
        let bytes =
            serde_json::to_vec_pretty(doc).map_err(|e| BookError::Io(std::io::Error::other(e)))?;
        crate::platform::write_private_file(&self.path, &bytes).map_err(BookError::Io)
    }

    fn guard(&self) -> std::sync::MutexGuard<'_, ()> {
        self.lock.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The person's keys made one (519d0 11-18): an entry kept under a `human:` key that is
    /// not in `keep` moves to `to` (the newest revision wins when both have one). Returns how
    /// many moved; nothing is written when none did.
    pub fn merge_humans(&self, keep: &[String], to: &str) -> Result<usize, BookError> {
        let _g = self.guard();
        let mut doc = self.load()?;
        let old: Vec<String> = doc
            .contacts
            .keys()
            .filter(|k| k.starts_with("human:") && !keep.contains(k) && k.as_str() != to)
            .cloned()
            .collect();
        if old.is_empty() {
            return Ok(0);
        }
        for k in &old {
            if let Some(e) = doc.contacts.remove(k) {
                let newer = doc
                    .contacts
                    .get(to)
                    .is_none_or(|cur| e.revision > cur.revision);
                if newer {
                    doc.contacts.insert(to.to_owned(), e);
                }
            }
        }
        self.save(&doc)?;
        Ok(old.len())
    }

    /// Everything set, by contact key.
    pub fn entries(&self) -> Result<BTreeMap<String, Entry>, BookError> {
        let _g = self.guard();
        Ok(self.load()?.contacts)
    }

    /// `contact_set`: apply `patch` to `key` if `expected` is its revision now (0 = never
    /// set). `known` says whether the key is one the list shows. Returns the new entry and
    /// the names of the fields that changed.
    pub fn set_contact(
        &self,
        key: &str,
        expected: u64,
        patch: &Patch,
        known: bool,
        now: &str,
    ) -> Result<Result<(Entry, Vec<&'static str>), Refused>, BookError> {
        let _g = self.guard();
        let mut doc = self.load()?;
        let old = doc.contacts.get(key).cloned().unwrap_or_default();
        if !known && old == Entry::default() {
            return Ok(Err(Refused(
                404,
                "contact_not_found",
                "no contact has this key".into(),
            )));
        }
        if old.revision != expected {
            return Ok(Err(Refused(
                409,
                "revision_conflict",
                format!("the contact is at revision {}", old.revision),
            )));
        }
        let mut new = old.clone();
        let changed = patch.apply(&mut new, now);
        if changed.is_empty() {
            return Ok(Ok((old, changed)));
        }
        new.revision = old.revision + 1;
        doc.contacts.insert(key.to_owned(), new.clone());
        self.save(&doc)?;
        Ok(Ok((new, changed)))
    }

    /// The effect sliders and their revision (defaults at revision 0 when never set).
    pub fn fx(&self) -> Result<(Fx, u64), BookError> {
        let _g = self.guard();
        let p = self.load()?.prefs;
        Ok((p.contacts_fx.unwrap_or_default(), p.contacts_fx_revision))
    }

    /// `prefs_set contacts_fx`: write `fx` if `expected` is the revision now.
    pub fn set_fx(&self, expected: u64, fx: Fx) -> Result<Result<u64, Refused>, BookError> {
        let _g = self.guard();
        let mut doc = self.load()?;
        if doc.prefs.contacts_fx_revision != expected {
            return Ok(Err(Refused(
                409,
                "revision_conflict",
                format!(
                    "the preference is at revision {}",
                    doc.prefs.contacts_fx_revision
                ),
            )));
        }
        doc.prefs.contacts_fx = Some(fx);
        doc.prefs.contacts_fx_revision += 1;
        self.save(&doc)?;
        Ok(Ok(doc.prefs.contacts_fx_revision))
    }
}

/// What a `contact_set` asks to change; `None` = leave it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Patch {
    pub friend: Option<bool>,
    /// `Some(None)` = take the group away.
    pub group: Option<Option<String>>,
    pub blocked: Option<bool>,
    pub muted: Option<bool>,
    /// `Some(None)` = muted until undone.
    pub mute_until: Option<Option<String>>,
    pub pinned: Option<bool>,
    pub archived: Option<bool>,
    pub note: Option<Option<String>>,
    pub tags: Option<Vec<String>>,
}

const PATCH_KEYS: &[&str] = &[
    "friend",
    "group",
    "blocked",
    "muted",
    "mute_until",
    "pinned",
    "archived",
    "note",
    "tags",
];

impl Patch {
    /// Change `e`; the names of the fields that really changed. Blocking takes the friend
    /// away (a blocked contact is never a friend; 1.x did the same).
    pub fn apply(&self, e: &mut Entry, now: &str) -> Vec<&'static str> {
        let before = e.clone();
        if let Some(f) = self.friend {
            e.friend = f;
        }
        if let Some(g) = &self.group {
            e.group = g.clone();
        }
        if let Some(b) = self.blocked {
            e.blocked = b;
        }
        if e.blocked {
            e.friend = false;
        }
        if let Some(m) = self.muted {
            e.muted = m;
            if !m {
                e.mute_until = None;
            }
        }
        if let Some(u) = &self.mute_until {
            e.muted = true;
            e.mute_until = u.clone();
        }
        if let Some(p) = self.pinned {
            e.pinned = p;
        }
        if let Some(a) = self.archived {
            e.archived_at = match (a, &before.archived_at) {
                (true, Some(at)) => Some(at.clone()),
                (true, None) => Some(now.to_owned()),
                (false, _) => None,
            };
        }
        if let Some(n) = &self.note {
            e.note = n.clone();
        }
        if let Some(t) = &self.tags {
            e.tags = t.clone();
        }
        let mut changed = Vec::new();
        let pairs: [(&'static str, bool); 9] = [
            ("friend", e.friend != before.friend),
            ("group", e.group != before.group),
            ("blocked", e.blocked != before.blocked),
            ("muted", e.muted != before.muted),
            ("mute_until", e.mute_until != before.mute_until),
            ("pinned", e.pinned != before.pinned),
            ("archived", e.archived_at != before.archived_at),
            ("note", e.note != before.note),
            ("tags", e.tags != before.tags),
        ];
        for (name, did) in pairs {
            if did {
                changed.push(name);
            }
        }
        changed
    }
}

fn group_ok(g: &str) -> bool {
    let n = g.chars().count();
    (1..=MAX_GROUP).contains(&n)
        && g.chars().all(|c| {
            c.is_ascii_alphanumeric()
                || c == '_'
                || c == '-'
                || ('\u{4e00}'..='\u{9fff}').contains(&c)
                || ('\u{3040}'..='\u{30ff}').contains(&c)
                || ('\u{ac00}'..='\u{d7af}').contains(&c)
        })
}

/// `YYYY-MM-DDTHH:MM:SS[.mmm]Z`, as aibd writes times; given back with `.mmm` so it
/// compares as a string with [`crate::sessions::now_rfc3339`].
pub fn utc_time(s: &str) -> Option<String> {
    let b = s.as_bytes();
    let digits =
        |r: std::ops::Range<usize>| b.get(r).is_some_and(|x| x.iter().all(u8::is_ascii_digit));
    let shape = b.len() >= 20
        && digits(0..4)
        && b[4] == b'-'
        && digits(5..7)
        && b[7] == b'-'
        && digits(8..10)
        && b[10] == b'T'
        && digits(11..13)
        && b[13] == b':'
        && digits(14..16)
        && b[16] == b':'
        && digits(17..19);
    if !shape {
        return None;
    }
    match &s[19..] {
        "Z" => Some(format!("{}.000Z", &s[..19])),
        rest if rest.len() == 5
            && rest.starts_with('.')
            && rest.ends_with('Z')
            && digits(20..23) =>
        {
            Some(s.to_owned())
        }
        _ => None,
    }
}

fn opt_text(v: &Value, name: &str, max: usize) -> Result<Option<String>, Refused> {
    match v {
        Value::String(s) if s.is_empty() => Ok(None),
        Value::String(s) if s.chars().count() <= max && !s.contains('\0') => Ok(Some(s.clone())),
        Value::String(_) => Err(bad(format!("{name}: at most {max} characters, no NUL"))),
        Value::Null => Ok(None),
        _ => Err(bad(format!("{name} must be a string"))),
    }
}

fn flag(v: &Value, name: &str) -> Result<bool, Refused> {
    v.as_bool()
        .ok_or_else(|| bad(format!("{name} must be true or false")))
}

fn unknown_field(k: &str) -> Refused {
    bad(format!(
        "unknown field {k}; known: {}",
        PATCH_KEYS.join(", ")
    ))
}

/// `{"type":"contact_set","key":…,"expected_revision":n, …}` ⇒ key, revision, patch. A key
/// this version does not know is refused, never dropped.
pub fn parse_set(body: &Value) -> Result<(String, u64, Patch), Refused> {
    let obj = body
        .as_object()
        .ok_or_else(|| bad("body must be an object"))?;
    let key = obj
        .get("key")
        .and_then(Value::as_str)
        .filter(|k| key_shape(k))
        .ok_or_else(|| bad("key must be seat:<seat id> or human:<16 hex>"))?
        .to_owned();
    let expected = obj
        .get("expected_revision")
        .and_then(Value::as_u64)
        .ok_or_else(|| bad("expected_revision is required"))?;
    let mut p = Patch::default();
    for (k, v) in obj {
        match k.as_str() {
            "type" | "key" | "expected_revision" => {}
            "friend" => p.friend = Some(flag(v, k)?),
            "blocked" => p.blocked = Some(flag(v, k)?),
            "muted" => p.muted = Some(flag(v, k)?),
            "pinned" => p.pinned = Some(flag(v, k)?),
            "archived" => p.archived = Some(flag(v, k)?),
            "group" => {
                let g = opt_text(v, k, MAX_GROUP)?;
                if g.as_deref().is_some_and(|g| !group_ok(g)) {
                    return Err(bad("group: 1-16 of letters, digits, _ - or CJK"));
                }
                p.group = Some(g);
            }
            "mute_until" => {
                p.mute_until = Some(match v {
                    Value::Null => None,
                    Value::String(s) => Some(
                        utc_time(s)
                            .ok_or_else(|| bad("mute_until must be YYYY-MM-DDTHH:MM:SSZ"))?,
                    ),
                    _ => return Err(bad("mute_until must be a time or null")),
                });
            }
            "note" => p.note = Some(opt_text(v, k, MAX_NOTE)?),
            "tags" => {
                let arr = v.as_array().ok_or_else(|| bad("tags must be a list"))?;
                if arr.len() > MAX_TAGS {
                    return Err(bad(format!("at most {MAX_TAGS} tags")));
                }
                let mut tags = Vec::new();
                for t in arr {
                    match opt_text(t, "tag", MAX_TAG)? {
                        Some(t) if !tags.contains(&t) => tags.push(t),
                        Some(_) => {}
                        None => return Err(bad("a tag is empty")),
                    }
                }
                p.tags = Some(tags);
            }
            _ => return Err(unknown_field(k)),
        }
    }
    Ok((key, expected, p))
}

/// `seat:<seat id>` (the seat ids aibd makes) or `human:<16 lowercase hex>`.
pub fn key_shape(k: &str) -> bool {
    if let Some(id) = k.strip_prefix("seat:") {
        return (1..=64).contains(&id.len())
            && id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-');
    }
    k.strip_prefix("human:").is_some_and(|h| {
        h.len() == 16
            && h.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// `{"type":"prefs_set","name":"contacts_fx","expected_revision":n,"value":{…}}`.
pub fn parse_fx(body: &Value) -> Result<(u64, Fx), Refused> {
    let obj = body
        .as_object()
        .ok_or_else(|| bad("body must be an object"))?;
    for k in obj.keys() {
        if !matches!(k.as_str(), "type" | "name" | "expected_revision" | "value") {
            return Err(bad(format!("unknown field {k}")));
        }
    }
    match obj.get("name").and_then(Value::as_str) {
        Some(FX) => {}
        _ => {
            return Err(Refused(
                404,
                "pref_not_found",
                format!("the only preference is {FX}"),
            ))
        }
    }
    let expected = obj
        .get("expected_revision")
        .and_then(Value::as_u64)
        .ok_or_else(|| bad("expected_revision is required"))?;
    let value = obj
        .get("value")
        .and_then(Value::as_object)
        .ok_or_else(|| bad("value must be an object"))?;
    let mut fx = Fx::default();
    for (k, v) in value {
        let n = v
            .as_u64()
            .filter(|n| *n <= 100 && n % 10 == 0)
            .ok_or_else(|| bad(format!("{k}: 0-100 in steps of 10")))?;
        let n = n as u8;
        match k.as_str() {
            "size" => fx.size = n,
            "speed" => fx.speed = n,
            "count" => fx.count = n,
            "length" => fx.length = n,
            "alpha" => fx.alpha = n,
            _ => return Err(bad(format!("unknown slider {k}"))),
        }
    }
    Ok((expected, fx))
}

/// One seat as the list shows it: what aibd measures, then what the person set.
pub struct SeatFacts {
    pub seat_id: String,
    pub name: String,
    pub persona_uid: Option<String>,
    pub provider: Option<String>,
    pub mode: Option<String>,
    /// `running` | `queued` | `stopped`
    pub presence: &'static str,
    pub queue_position: Option<usize>,
}

/// What the message library (S89-talk-2a) says about one contact, seen from the page that
/// asks (7b81e 11-18, ORDER-today-usable: unread counts and the latest message on the list).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct MsgCounts {
    /// Messages from this contact to the asking page that wait to be read.
    pub unread: u64,
    /// The newest message between this contact and the asking page (any message of the
    /// contact for the start-up token).
    pub last_message_at: Option<String>,
    /// `decide` messages from this contact to the asking page not closed yet.
    pub decide_open: u64,
    /// Messages to this contact (from anyone) it has not read yet: the seat's own inbox.
    pub inbox_unread: u64,
}

fn closed(item: &serde_json::Map<String, Value>) -> bool {
    matches!(
        item.get("status").and_then(Value::as_str),
        Some("done" | "archived")
    )
}

/// [`MsgCounts`] of `key` for the page `me` (`None` = the start-up token) from the library's
/// facts. The page's own row (`key == me`) counts what waits for the page from anyone.
pub fn message_counts(facts: &[aib_store::MessageFact], key: &str, me: Option<&str>) -> MsgCounts {
    let mut c = MsgCounts::default();
    for f in facts {
        let unread = crate::messages::is_unread(&f.item);
        if f.to_key == key && unread {
            c.inbox_unread += 1;
        }
        let between = match me {
            Some(me) if me == key => f.from_key == me || f.to_key == me,
            Some(me) => {
                (f.from_key == key && f.to_key == me) || (f.from_key == me && f.to_key == key)
            }
            None => f.from_key == key || f.to_key == key,
        };
        if !between {
            continue;
        }
        if c.last_message_at.as_deref() < Some(f.created.as_str()) {
            c.last_message_at = Some(f.created.clone());
        }
        let to_me = me.is_some_and(|m| f.to_key == m);
        if to_me && unread {
            c.unread += 1;
        }
        if to_me
            && f.item.get("talk_kind").and_then(Value::as_str) == Some("decide")
            && !closed(&f.item)
        {
            c.decide_open += 1;
        }
    }
    c
}

/// Most pairs `GET /api/v1/flows` returns (IFACE-S89-talk-2b §3).
pub const FLOW_PAIRS_MAX: usize = 200;

/// `GET /api/v1/flows` pairs from the library's facts in the window: per `(from, to)` the
/// count of each kind (`say` / `decide` / `shout`; anything else counts as `say`), failed
/// ones apart and not in their kind, and the newest time. No text, no length. The newest
/// [`FLOW_PAIRS_MAX`] pairs; the second value says whether more were cut.
pub fn flow_pairs(facts: &[aib_store::MessageFact]) -> (Vec<Value>, bool) {
    let mut by: Vec<((String, String), [u64; 4], String)> = Vec::new();
    for f in facts {
        let slot = if f.item.get("failed").and_then(Value::as_bool) == Some(true) {
            3
        } else {
            match f.item.get("talk_kind").and_then(Value::as_str) {
                Some("decide") => 1,
                Some("shout") => 2,
                _ => 0,
            }
        };
        let key = (f.from_key.clone(), f.to_key.clone());
        match by.iter_mut().find(|(k, _, _)| *k == key) {
            Some((_, n, last)) => {
                n[slot] += 1;
                if *last < f.created {
                    last.clone_from(&f.created);
                }
            }
            None => {
                let mut n = [0; 4];
                n[slot] = 1;
                by.push((key, n, f.created.clone()));
            }
        }
    }
    by.sort_by(|a, b| b.2.cmp(&a.2));
    let truncated = by.len() > FLOW_PAIRS_MAX;
    by.truncate(FLOW_PAIRS_MAX);
    let pairs = by
        .into_iter()
        .map(|((from, to), n, last)| {
            json!({"from": from, "to": to, "say": n[0], "decide": n[1], "shout": n[2],
                   "failed": n[3], "last_at": last})
        })
        .collect();
    (pairs, truncated)
}

/// A row of `GET /api/v1/contacts`.
pub fn row(s: &SeatFacts, e: &Entry, now: &str, m: &MsgCounts) -> Value {
    json!({
        "key": format!("seat:{}", s.seat_id),
        "kind": "seat",
        "name": s.name,
        "persona_uid": s.persona_uid,
        "provider": s.provider,
        "mode": s.mode,
        "presence": s.presence,
        "queue_position": s.queue_position,
        "last_message_at": m.last_message_at,
        "unread": m.unread,
        "decide_open": m.decide_open,
        "inbox_unread": m.inbox_unread,
        "friend": e.friend,
        "group": e.group,
        "blocked": e.blocked,
        "muted": e.muted_at(now),
        "mute_until": if e.muted_at(now) { e.mute_until.clone() } else { None },
        "pinned": e.pinned,
        "archived_at": e.archived_at,
        "note": e.note,
        "tags": e.tags,
        "revision": e.revision,
    })
}

/// The row of the page that asks (S89-talk-2c: the page needs its own human key to tell
/// "me" from the others). `key` = the person's key on this aibd ([`crate::person`]; the same
/// after pairing again), no longer the credential's hash.
pub fn self_row(key: &str, e: &Entry, now: &str, m: &MsgCounts) -> Value {
    json!({
        "key": key,
        "kind": "human",
        "self": true,
        "name": null,
        "persona_uid": null,
        "provider": null,
        "mode": null,
        "presence": "online",
        "queue_position": null,
        "last_message_at": m.last_message_at,
        "unread": m.unread,
        "decide_open": m.decide_open,
        "inbox_unread": m.inbox_unread,
        "friend": e.friend,
        "group": e.group,
        "blocked": e.blocked,
        "muted": e.muted_at(now),
        "mute_until": if e.muted_at(now) { e.mute_until.clone() } else { None },
        "pinned": e.pinned,
        "archived_at": e.archived_at,
        "note": e.note,
        "tags": e.tags,
        "revision": e.revision,
    })
}

/// What `GET /api/v1/contacts` filters by.
#[derive(Debug, Default, Clone)]
pub struct Query {
    pub q: Option<String>,
    pub presence: Option<String>,
    pub group: Option<String>,
    pub include_blocked: bool,
    /// `?all=1` (same word as the seat list; 043dd 04:54): archived rows too.
    pub all: bool,
}

/// Filter and order the rows: pinned, running, then by name (last message time joins in
/// with 2a).
pub fn select(rows: Vec<Value>, q: &Query) -> Vec<Value> {
    let needle =
        q.q.as_deref()
            .map(str::to_lowercase)
            .filter(|s| !s.is_empty());
    let mut out: Vec<Value> = rows
        .into_iter()
        .filter(|r| q.include_blocked || r["blocked"] != true || r["self"] == true)
        .filter(|r| q.all || r["archived_at"].is_null() || r["self"] == true)
        .filter(|r| q.presence.as_deref().is_none_or(|p| r["presence"] == p))
        .filter(|r| q.group.as_deref().is_none_or(|g| r["group"] == g))
        .filter(|r| {
            needle.as_deref().is_none_or(|n| {
                let hay = format!(
                    "{} {} {} {}",
                    r["name"].as_str().unwrap_or(""),
                    r["persona_uid"].as_str().unwrap_or(""),
                    r["note"].as_str().unwrap_or(""),
                    r["tags"]
                        .as_array()
                        .map(|t| t
                            .iter()
                            .filter_map(Value::as_str)
                            .collect::<Vec<_>>()
                            .join(" "))
                        .unwrap_or_default()
                );
                hay.to_lowercase().contains(n)
            })
        })
        .collect();
    out.sort_by(|a, b| {
        // the page's own row first, then pinned, then running
        let rank = |r: &Value| {
            (
                r["self"] != true,
                r["pinned"] != true,
                r["presence"] != "running",
            )
        };
        // then the newest message first (IFACE-S89-talk-2b §1), then the name
        rank(a)
            .cmp(&rank(b))
            .then_with(|| {
                b["last_message_at"]
                    .as_str()
                    .cmp(&a["last_message_at"].as_str())
            })
            .then_with(|| a["name"].as_str().cmp(&b["name"].as_str()))
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: &str = "2026-11-18T06:00:00.000Z";

    fn set(body: Value) -> Result<(String, u64, Patch), Refused> {
        parse_set(&body)
    }

    #[test]
    fn the_file_is_private_and_a_write_needs_the_revision_it_read() {
        let dir = tempfile::tempdir().unwrap();
        let b = Book::new(dir.path());
        let (_, _, p) =
            set(json!({"key":"seat:seat_1","expected_revision":0,"friend":true})).unwrap();
        let (e, changed) = b
            .set_contact("seat:seat_1", 0, &p, true, NOW)
            .unwrap()
            .unwrap();
        assert_eq!(e.revision, 1);
        assert_eq!(changed, vec!["friend"]);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.path().join(CONTACTS_FILE))
                .unwrap()
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600, "reason: contacts_file_not_private");
        }
        let again = b.set_contact("seat:seat_1", 0, &p, true, NOW).unwrap();
        assert_eq!(
            again.unwrap_err().1,
            "revision_conflict",
            "reason: stale_revision_written"
        );
        assert_eq!(b.entries().unwrap()["seat:seat_1"].revision, 1);
    }

    #[test]
    fn a_broken_file_is_refused_and_left_as_it_is() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(CONTACTS_FILE);
        std::fs::write(&path, b"{not json").unwrap();
        let b = Book::new(dir.path());
        assert!(matches!(b.entries(), Err(BookError::Unreadable(_))));
        let p = Patch {
            friend: Some(true),
            ..Patch::default()
        };
        assert!(matches!(
            b.set_contact("seat:seat_1", 0, &p, true, NOW),
            Err(BookError::Unreadable(_))
        ));
        assert_eq!(
            std::fs::read(&path).unwrap(),
            b"{not json",
            "reason: broken_file_overwritten"
        );
    }

    #[test]
    fn an_unknown_key_is_404_and_an_unknown_field_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let b = Book::new(dir.path());
        let p = Patch {
            pinned: Some(true),
            ..Patch::default()
        };
        let r = b.set_contact("seat:gone", 0, &p, false, NOW).unwrap();
        assert_eq!(r.unwrap_err().1, "contact_not_found");
        let r = set(json!({"key":"seat:seat_1","expected_revision":0,"avatar":"x"}));
        assert!(r.is_err(), "reason: unknown_field_dropped");
        assert!(
            set(json!({"key":"seat:seat_1","friend":true})).is_err(),
            "no revision"
        );
        assert!(set(json!({"key":"me","expected_revision":0})).is_err());
        assert!(set(json!({"key":"human:ABCDEF0123456789","expected_revision":0})).is_err());
        assert!(set(json!({"key":"human:abcdef0123456789","expected_revision":0})).is_ok());
    }

    #[test]
    fn blocking_takes_the_friend_away_and_an_old_mute_is_over() {
        let mut e = Entry {
            friend: true,
            ..Entry::default()
        };
        let p = Patch {
            blocked: Some(true),
            ..Patch::default()
        };
        let changed = p.apply(&mut e, NOW);
        assert!(!e.friend, "reason: blocked_still_friend");
        assert_eq!(changed, vec!["friend", "blocked"]);
        let (_, _, p) = set(json!({"key":"seat:s","expected_revision":0,
            "mute_until":"2026-11-18T05:00:00Z"}))
        .unwrap();
        let mut e = Entry::default();
        p.apply(&mut e, NOW);
        assert!(e.muted);
        assert!(!e.muted_at(NOW), "reason: expired_mute_still_muted");
        let (_, _, p) = set(json!({"key":"seat:s","expected_revision":0,
            "mute_until":"2026-11-18T07:00:00Z"}))
        .unwrap();
        p.apply(&mut e, NOW);
        assert!(e.muted_at(NOW));
        let row = row(
            &SeatFacts {
                seat_id: "s".into(),
                name: "n".into(),
                persona_uid: None,
                provider: None,
                mode: None,
                presence: "stopped",
                queue_position: None,
            },
            &e,
            "2026-11-18T08:00:00.000Z",
            &MsgCounts::default(),
        );
        assert_eq!(row["muted"], false);
        assert!(row["mute_until"].is_null());
    }

    #[test]
    fn the_sliders_take_only_tens_from_0_to_100_and_only_one_name() {
        let ok = parse_fx(&json!({"type":"prefs_set","name":FX,"expected_revision":0,
            "value":{"size":40,"alpha":0}}))
        .unwrap();
        assert_eq!(ok.1.size, 40);
        assert_eq!(ok.1.speed, 20, "a missing slider keeps its default");
        for bad_value in [json!(15), json!(110), json!(-10), json!("50")] {
            let r = parse_fx(&json!({"type":"prefs_set","name":FX,"expected_revision":0,
                "value":{"size":bad_value}}));
            assert!(r.is_err(), "reason: slider_value_taken {bad_value}");
        }
        let r =
            parse_fx(&json!({"type":"prefs_set","name":"theme","expected_revision":0,"value":{}}));
        assert_eq!(
            r.unwrap_err().1,
            "pref_not_found",
            "reason: any_pref_name_taken"
        );
        let r = parse_fx(&json!({"type":"prefs_set","name":FX,"expected_revision":0,
            "value":{"glow":10}}));
        assert!(r.is_err());
        let dir = tempfile::tempdir().unwrap();
        let b = Book::new(dir.path());
        assert_eq!(b.fx().unwrap(), (Fx::default(), 0));
        assert_eq!(b.set_fx(0, ok.1).unwrap().unwrap(), 1);
        assert_eq!(
            b.set_fx(0, ok.1).unwrap().unwrap_err().1,
            "revision_conflict"
        );
        assert_eq!(b.fx().unwrap(), (ok.1, 1));
    }

    #[test]
    fn notes_and_tags_have_limits_and_times_one_shape() {
        let long = "字".repeat(MAX_NOTE + 1);
        assert!(set(json!({"key":"seat:s","expected_revision":0,"note":long})).is_err());
        let tags: Vec<String> = (0..=MAX_TAGS).map(|i| format!("t{i}")).collect();
        assert!(set(json!({"key":"seat:s","expected_revision":0,"tags":tags})).is_err());
        assert!(set(
            json!({"key":"seat:s","expected_revision":0,"group":"這個組名有十七個字超過了十六字的限制"})
        )
        .is_err());
        assert!(set(json!({"key":"seat:s","expected_revision":0,"group":"工作"})).is_ok());
        assert_eq!(
            utc_time("2026-11-18T05:00:00Z").as_deref(),
            Some("2026-11-18T05:00:00.000Z")
        );
        assert_eq!(
            utc_time("2026-11-18T05:00:00.120Z").as_deref(),
            Some("2026-11-18T05:00:00.120Z")
        );
        assert!(utc_time("2026-11-18T05:00:00+08:00").is_none());
        assert!(utc_time("2026-11-18 05:00:00Z").is_none());
    }

    #[test]
    fn the_list_puts_pinned_then_running_first_and_hides_blocked_and_archived() {
        let r = |name: &str, presence: &str, pinned: bool, blocked: bool| {
            json!({"name":name,"presence":presence,"pinned":pinned,"blocked":blocked,
                   "archived_at":null,"group":null,"persona_uid":null,"note":null,"tags":[]})
        };
        let rows = vec![
            r("c", "stopped", false, false),
            r("b", "running", false, false),
            r("a", "stopped", true, false),
            r("x", "running", false, true),
        ];
        let got = select(rows.clone(), &Query::default());
        let names: Vec<&str> = got.iter().filter_map(|v| v["name"].as_str()).collect();
        assert_eq!(names, vec!["a", "b", "c"], "reason: order_or_blocked_shown");
        let q = Query {
            include_blocked: true,
            q: Some("X".into()),
            ..Query::default()
        };
        assert_eq!(select(rows, &q).len(), 1);
        let mut me = r("me", "online", false, true);
        me["self"] = json!(true);
        me["archived_at"] = json!("2026-11-18T00:00:00.000Z");
        let got = select(vec![r("a", "running", true, false), me], &Query::default());
        assert_eq!(got[0]["name"], "me", "reason: self_row_not_first_or_hidden");
    }

    fn fact(from: &str, to: &str, at: &str, status: &str, kind: &str) -> aib_store::MessageFact {
        let mut item = serde_json::Map::new();
        item.insert("status".into(), json!(status));
        item.insert("talk_kind".into(), json!(kind));
        aib_store::MessageFact {
            from_key: from.into(),
            to_key: to.into(),
            created: at.into(),
            created_unix: 0,
            item,
        }
    }

    #[test]
    fn the_counts_are_what_waits_for_the_page_and_for_the_seat() {
        let (me, s1, s2) = ("human:00000000000000aa", "seat:s1", "seat:s2");
        let facts = vec![
            fact(me, s1, "2026-11-18T07:00:00Z", "delivered", "say"),
            fact(s1, me, "2026-11-18T07:01:00Z", "delivered", "decide"),
            fact(s1, me, "2026-11-18T07:02:00Z", "read", "decide"),
            fact(s1, me, "2026-11-18T07:03:00Z", "done", "decide"),
            fact(s2, s1, "2026-11-18T07:09:00Z", "queued", "say"),
            fact(
                s2,
                "human:00000000000000bb",
                "2026-11-18T07:08:00Z",
                "delivered",
                "say",
            ),
        ];
        let c = message_counts(&facts, s1, Some(me));
        assert_eq!(c.unread, 1, "reason: unread_to_page {c:?}");
        assert_eq!(c.decide_open, 2, "read but not closed is open {c:?}");
        assert_eq!(c.inbox_unread, 2, "reason: seat_inbox {c:?}");
        assert_eq!(
            c.last_message_at.as_deref(),
            Some("2026-11-18T07:03:00Z"),
            "reason: last_not_between_the_two {c:?}"
        );
        let other = message_counts(&facts, s2, Some(me));
        assert_eq!(
            (other.unread, other.last_message_at.as_deref()),
            (0, None),
            "{other:?}"
        );
        let mine = message_counts(&facts, me, Some(me));
        assert_eq!((mine.unread, mine.decide_open), (1, 2), "{mine:?}");
        let startup = message_counts(&facts, s2, None);
        assert_eq!(
            (startup.unread, startup.last_message_at.as_deref()),
            (0, Some("2026-11-18T07:09:00Z"))
        );
    }

    #[test]
    fn flows_count_kinds_apart_from_failed_and_carry_no_text() {
        let mut facts = vec![
            fact(
                "seat:a",
                "seat:b",
                "2026-11-18T07:00:00Z",
                "delivered",
                "say",
            ),
            fact(
                "seat:a",
                "seat:b",
                "2026-11-18T07:05:00Z",
                "delivered",
                "decide",
            ),
            fact(
                "seat:a",
                "seat:b",
                "2026-11-18T07:01:00Z",
                "delivered",
                "whisper",
            ),
        ];
        let mut failed = fact(
            "seat:a",
            "seat:b",
            "2026-11-18T07:02:00Z",
            "queued",
            "shout",
        );
        failed.item.insert("failed".into(), json!(true));
        facts.push(failed);
        let (pairs, cut) = flow_pairs(&facts);
        assert!(!cut);
        assert_eq!(pairs.len(), 1, "one pair, one row");
        let p = &pairs[0];
        assert_eq!(
            (
                p["say"].as_u64(),
                p["decide"].as_u64(),
                p["shout"].as_u64(),
                p["failed"].as_u64()
            ),
            (Some(2), Some(1), Some(0), Some(1)),
            "reason: failed_counted_in_kind {p}"
        );
        assert_eq!(p["last_at"], "2026-11-18T07:05:00Z");
        let many: Vec<_> = (0..FLOW_PAIRS_MAX + 3)
            .map(|i| {
                fact(
                    &format!("seat:x{i:03}"),
                    "seat:y",
                    &format!("2026-11-18T07:{:02}:00Z", i % 60),
                    "queued",
                    "say",
                )
            })
            .collect();
        let (pairs, cut) = flow_pairs(&many);
        assert!(cut, "reason: not_truncated");
        assert_eq!(pairs.len(), FLOW_PAIRS_MAX);
        assert!(
            pairs
                .windows(2)
                .all(|w| w[0]["last_at"].as_str() >= w[1]["last_at"].as_str()),
            "reason: not_newest_first"
        );
    }
}
