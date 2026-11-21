//! N-ws-thread-cat, Andy 11-19 01:1x 「如何把正式資料全部接回網頁對話中」: the four kinds of real
//! records still outside the message library (519d0 01:12): the old seat mail ledger, the council
//! decisions, the mail 自動推進 sent, and the 影子續跑 records.
//!
//! - `aibd import <dm|council|drive|shadow> [--dry-run | --undo]`, read only on the sources:
//!   - `dm`: `<state>/dm-queue/<seat>/q_*.json` (deleted ones skipped); one thread per seat
//!     pair, no category; a 「長訊已落檔」 line is replaced by the relay file it names.
//!   - `council`: `<pending>/DECISION-council-*.md`, thread category `council`, with its doc.
//!   - `drive`: `<state>/aib-drive/events/*.jsonl` lines `drive_send` sent (not a dry run), the
//!     mail itself read from the ledger by its id; category `drive`.
//!   - `shadow`: `<state>/shadow-explain/<seat>.json`, one post per record time; category
//!     `shadow`.
//! - Every post: `source {kind, id, at}` (where and when it really was), the original time as
//!   its time, the sender the source names as `seat:<5 hex>` (`seat:import` when it names
//!   none; never a person), `sender_safe`, and `card`: the first plan node id named (the drive
//!   line's own cards first), none when no id is named.
//! - Run again: nothing new (idem key `<kind>:<id>` under the owner `import`). `--undo` takes
//!   the kind back whole.
use crate::import::{digest_of, Counts, Done, BODY_MAX, OWNER};
use aib_store::{Inserted, MessageDb, MessageRow, Opened, ThreadRow};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

/// The kinds this file imports.
pub const KINDS: [&str; 4] = ["dm", "council", "drive", "shadow"];

/// One record to post.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub thread: String,
    pub thread_title: String,
    pub category: Option<&'static str>,
    pub key: String,
    pub from: String,
    pub at: i64,
    pub body: String,
    pub source: Value,
    pub card: Option<String>,
    /// The card is [`FALLBACK_NODE`], not one the record named.
    pub card_fallback: bool,
    pub doc: Option<Value>,
}

/// Where the records are: `<state>` (`AIBD_IMPORT_STATE` or `~/.ez2-aib/state`) and `<pending>`.
#[derive(Debug, Clone)]
pub struct Roots {
    pub state: PathBuf,
    pub pending: PathBuf,
    /// `hub_seat` in `<data>/bridge.json` (7ebca 9583ec32): the person's code in the old
    /// ledger; mail to or from it is in the 「<席> ↔ hub」 thread.
    pub hub: Option<String>,
}

impl Roots {
    /// `<state>`: `import_state` in `<data>/bridge.json` (the same file 7ebca's mirror reads; a
    /// preview's environment is cleared), else `AIBD_IMPORT_STATE`, else `~/.ez2-aib/state`.
    pub fn from_env(data_dir: &Path) -> Option<Roots> {
        let pending = crate::docs::root()?;
        let state = state_in(data_dir)
            .or_else(|| std::env::var_os("AIBD_IMPORT_STATE").map(PathBuf::from))
            .or_else(|| {
                std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".ez2-aib").join("state"))
            })?;
        Some(Roots {
            state,
            pending,
            hub: hub_in(data_dir),
        })
    }
}

/// `hub_seat` in `<data>/bridge.json`: five of `[0-9a-z]`, else none (as 7ebca reads it).
pub fn hub_in(data_dir: &Path) -> Option<String> {
    read_json(&data_dir.join("bridge.json"))?
        .get("hub_seat")
        .and_then(Value::as_str)
        .filter(|h| {
            h.len() == 5
                && h.bytes()
                    .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
        })
        .map(str::to_owned)
}

/// `import_state` in `<data>/bridge.json`, when it is a folder.
pub fn state_in(data_dir: &Path) -> Option<PathBuf> {
    read_json(&data_dir.join("bridge.json"))?
        .get("import_state")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
}

/// A seat code as the ledgers write it: five lower hex ([`crate::seats::is_code`]).
pub fn seat_code(s: &str) -> Option<&str> {
    let s = s.trim().trim_start_matches('@');
    crate::seats::is_code(s).then_some(s)
}

/// The seat a ledger mail was from, when the ledger says a seat sent it: its sender seat, or
/// the seat its own label names (one judgement for dm and drive, 0eed7 daaafb770 L7).
fn ledger_sender(d: &Value) -> Option<String> {
    let field = |k: &str| d.get(k).and_then(Value::as_str).unwrap_or("");
    if field("sender_kind") != "seat" {
        return None;
    }
    seat_code(field("sender_seat"))
        .map(str::to_owned)
        .or_else(|| crate::import_regroup::label_seat(field("sender_label")))
}

fn from_of(seat: Option<&str>) -> String {
    format!("seat:{}", seat.and_then(seat_code).unwrap_or("import"))
}

/// The first plan node id `text` names (whole words of letters, digits, `-`, `_`).
pub fn card_in(text: &str, ids: &BTreeSet<String>) -> Option<String> {
    text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '-' || c == '_'))
        .find(|w| !w.is_empty() && ids.contains(*w))
        .map(str::to_owned)
}

/// The plan's node ids.
pub fn node_ids(tree: Option<&Value>) -> BTreeSet<String> {
    tree.and_then(|t| t.get("nodes"))
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|n| n.get("id").and_then(Value::as_str))
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn cut(text: &str) -> String {
    let mut s: String = text.chars().take(BODY_MAX).collect();
    if s.len() < text.len() {
        s.push('…');
    }
    s
}

/// Text as the page may show it (7ebca CHECK-importdm N2, 5d33a BOUNDARY §5): terminal escape
/// sequences (CSI, OSC) removed, then every character the person cannot see or that turns the
/// line around (aib_approve::text::is_bad_char), newlines and tabs kept.
pub fn clean(t: &str) -> String {
    let mut out = String::with_capacity(t.len());
    let mut it = t.chars().peekable();
    while let Some(c) = it.next() {
        if c == '\u{1b}' {
            match it.next() {
                Some('[') => {
                    for d in it.by_ref() {
                        if ('@'..='~').contains(&d) {
                            break;
                        }
                    }
                }
                Some(']') => {
                    while let Some(d) = it.next() {
                        if d == '\u{7}' || (d == '\u{1b}' && it.next_if_eq(&'\\').is_some()) {
                            break;
                        }
                    }
                }
                _ => {}
            }
            continue;
        }
        if c == '\n' || c == '\t' || !aib_approve::text::is_bad_char(c) {
            out.push(c);
        }
    }
    out
}

/// The most this import reads of one ledger file (0eed7 daaafb770 L6).
pub const READ_MAX: u64 = 64 * 1024 * 1024;

/// A ledger file's text: a regular file (a link is not followed) of at most [`READ_MAX`].
pub fn read_text(p: impl AsRef<Path>) -> std::io::Result<String> {
    let p = p.as_ref();
    let m = std::fs::symlink_metadata(p)?;
    if !m.file_type().is_file() || m.len() > READ_MAX {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "not a ledger file (a link, not a file, or too big)",
        ));
    }
    std::fs::read_to_string(p)
}

/// The note a relayed mail gets when its file is gone (7ebca CHECK-importdm N1).
pub const RELAY_GONE: &str = "〔原文已不在〕";

/// How a mail whose text was put in a file opens.
pub const RELAY_OPENING: &str = "長訊已落檔";

/// A 「長訊已落檔 … 請讀:<path>/RELAY-…txt」 mail's text: the relay file's, when it is in
/// `pending` and readable; the mail marked [`RELAY_GONE`] when it names one that is not; the
/// mail as it is otherwise.
///
/// Only a mail that opens with 「長訊已落檔」 is one (0eed7 daaafb770 M3: 337 mails only quote
/// another seat's relay file; replacing them put that seat's words under this sender). A path
/// is cut at `/` and `\\` alike (L4: Windows).
pub fn expand_relay(msg: &str, pending: &Path) -> String {
    if !msg.trim_start().starts_with(RELAY_OPENING) {
        return msg.to_owned();
    }
    let name = msg
        .split(|c: char| c.is_whitespace() || c == ':' || c == ':')
        .filter_map(|w| w.rsplit(['/', '\\']).next())
        .find(|n| n.starts_with("RELAY-") && n.ends_with(".txt"));
    let Some(n) = name else {
        return msg.to_owned();
    };
    crate::docs::resolve(pending, n)
        .ok()
        .and_then(|p| read_text(p).ok())
        .map(|t| format!("〔長訊 {n}〕\n{t}"))
        .unwrap_or_else(|| format!("{RELAY_GONE}{msg}"))
}

/// Who an unproven sender said it was, as one short line (5d33a CHECK-dm-import-key L1): no
/// line breaks (a label could carry a fake 「519d0:准了」 line), no 〔〕, at most 60 characters.
pub fn said_as(label: &str) -> String {
    clean(label)
        .chars()
        .map(|c| if c == '\n' || c == '\t' { ' ' } else { c })
        .filter(|c| *c != '〔' && *c != '〕')
        .take(60)
        .collect::<String>()
        .trim()
        .to_owned()
}

fn thread_id(key: &str) -> String {
    crate::import::thread_of(key)
}

fn read_json(p: &Path) -> Option<Value> {
    serde_json::from_str(&read_text(p).ok()?).ok()
}

fn sorted_files(dir: &Path, keep: impl Fn(&str) -> bool) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_str().is_some_and(&keep))
        .map(|e| e.path())
        .collect();
    v.sort();
    v
}

fn mtime(p: &Path) -> i64 {
    std::fs::metadata(p)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
        .and_then(|d| i64::try_from(d.as_secs()).ok())
        .unwrap_or(0)
}

/// The old mail ledger: one thread per seat pair.
pub fn dm_items(r: &Roots, ids: &BTreeSet<String>, skipped: &mut usize) -> Vec<Item> {
    let mut out = Vec::new();
    let q = r.state.join("dm-queue");
    let hub = r.hub.as_deref();
    let is_hub = |c: &str| hub == Some(c);
    for seat_dir in sorted_files(&q, |n| seat_code(n).is_some() || is_hub(n)) {
        let to = seat_dir
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_owned();
        let to = if is_hub(&to) { "hub".to_owned() } else { to };
        for f in sorted_files(&seat_dir, |n| n.starts_with("q_") && n.ends_with(".json")) {
            let Some(d) = read_json(&f) else {
                *skipped += 1;
                continue;
            };
            let field = |k: &str| d.get(k).and_then(Value::as_str).unwrap_or("");
            if field("status") == "deleted" || field("id").is_empty() {
                *skipped += 1;
                continue;
            }
            // 7ebca CHECK-importdm M1: a seat only when the ledger says the sender was one
            // (unknown / seat_claimed name a seat they may not be); the rest are seat:import
            // with who they said they were in front. The pair (thread) stays as named.
            // 519d0 06:13 (real ledger): 2,045 letters say sender_kind seat with no
            // sender_seat, the seat being in the ledger's own label 「@a9136(…)」
            let label_seat = crate::import_regroup::label_seat(field("sender_label"));
            let sender_owned = ledger_sender(&d);
            let sender = sender_owned.as_deref();
            // who it says it is from, for putting it on a card only (never its sender):
            // the seat, the label, the sender seat field, or a body that opens with a seat
            let known = sender
                .map(str::to_owned)
                .or_else(|| label_seat.clone())
                .or_else(|| seat_code(field("sender_seat")).map(str::to_owned));
            // read off the words only: 「f1fae:…」 may be who it is to (519d0 08:27)
            let opened = known.is_none();
            let said_seat = known.or_else(|| crate::import_regroup::opening_seat(field("msg")));
            let system = said_seat.is_none()
                && crate::import_regroup::is_system(field("sender_label"), field("msg"));
            // 7ebca 02:10: a person's mail (sender_kind human with a human_id) is in the
            // 「<席> ↔ hub」 thread, as the mirror and legacy_send put it
            let person = field("sender_kind") == "human" && !field("human_id").is_empty();
            // 0eed7 daaafb770 M5: the seat taken as sender (its label too) names the pair
            let named = if person || is_hub(field("sender_seat")) {
                "hub"
            } else {
                sender
                    .or_else(|| seat_code(field("sender_seat")))
                    .unwrap_or("import")
            };
            let mut pair = [named.to_owned(), to.clone()];
            pair.sort();
            let mut body = clean(&expand_relay(field("msg"), &r.pending));
            if sender.is_none() {
                let said = match said_as(field("sender_label")) {
                    l if l.is_empty() => said_as(field("sender_seat")),
                    l => l,
                };
                body = format!("〔原寄件:{said}〕{body}");
            }
            let at = d.get("sent_at").and_then(Value::as_i64).unwrap_or(0);
            out.push(Item {
                thread: thread_id(&format!("dm:{}:{}", pair[0], pair[1])),
                thread_title: format!("{} ↔ {}(信件匯入)", pair[0], pair[1]),
                category: None,
                key: format!("dm:{}", field("id")),
                from: from_of(sender),
                at,
                card: card_in(&body, ids),
                source: {
                    let mut s = json!({"kind": "dm", "id": field("id"), "to": to,
                        "at": crate::sessions::rfc3339_of_unix(at)});
                    // import_regroup rule `reply`: the ledger's own thread and what it replaces
                    // import_regroup NOTE r3 §3b: the ledger's state of the letter
                    s["status"] = json!(field("status"));
                    // import_regroup rule seat_day / system
                    if let Some(seat) = &said_seat {
                        s["said_seat"] = json!(seat);
                        if opened {
                            s["said_from"] = json!("opening");
                        }
                    }
                    if system {
                        s["system"] = json!(true);
                    }
                    for k in ["thread", "supersedes"] {
                        if !field(k).is_empty() {
                            let name = if k == "thread" { "ledger_thread" } else { k };
                            s[name] = json!(field(k));
                        }
                    }
                    s
                },
                body,
                doc: None,
                card_fallback: false,
            });
        }
    }
    out
}

/// The council decisions (files).
pub fn council_items(r: &Roots, ids: &BTreeSet<String>, skipped: &mut usize) -> Vec<Item> {
    let mut out = Vec::new();
    for p in sorted_files(&r.pending, |n| {
        n.starts_with("DECISION-council-") && n.ends_with(".md")
    }) {
        let file = p
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_owned();
        if crate::docs::secret_name(&file) || crate::docs::resolve(&r.pending, &file).is_err() {
            *skipped += 1;
            continue;
        }
        let Ok(text) = read_text(&p) else {
            *skipped += 1;
            continue;
        };
        let at = mtime(&p);
        let title = crate::import::title_of(&file, &text);
        out.push(Item {
            thread: thread_id("council"),
            thread_title: "遇事不決定稿(匯入)".into(),
            category: Some("council"),
            key: format!("council:{file}"),
            from: from_of(crate::import::seat_of(&file).as_deref()),
            at,
            card: card_in(&text, ids),
            source: json!({"kind": "council", "id": file, "at": crate::sessions::rfc3339_of_unix(at)}),
            body: clean(&text),
            doc: Some(json!([{"root": "pending", "file": file, "title": title}])),
            card_fallback: false,
        });
    }
    out
}

/// The mail 自動推進 sent: its event lines, the mail read from the ledger.
pub fn drive_items(r: &Roots, ids: &BTreeSet<String>, skipped: &mut usize) -> Vec<Item> {
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    let ev = r.state.join("aib-drive").join("events");
    for f in sorted_files(&ev, |n| n.ends_with(".jsonl")) {
        let text = read_text(&f).unwrap_or_default();
        for line in text.lines() {
            let Ok(e) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            let field = |k: &str| e.get(k).and_then(Value::as_str).unwrap_or("");
            if field("kind") != "drive_send"
                || field("outcome") != "sent"
                || e.get("dry_run") == Some(&json!(true))
            {
                continue;
            }
            let (to, id) = (field("to"), field("dm_id"));
            let Some(to) = seat_code(to) else {
                *skipped += 1;
                continue;
            };
            if id.is_empty() || !seen.insert(id.to_owned()) {
                continue;
            }
            // a ledger file (q_*.json) is no page document kind: the older walls only
            // (inside the folder, no secret name, a regular file within the size) — 7ebca 05:09:
            // resolve's kind list (N-web-oneline 3) refused every one, so drive mail was 0
            let Some(d) =
                crate::docs::resolve_in(&r.state.join("dm-queue").join(to), &format!("{id}.json"))
                    .ok()
                    .and_then(|p| read_json(&p))
            else {
                *skipped += 1;
                continue;
            };
            let body = clean(&expand_relay(
                d.get("msg").and_then(Value::as_str).unwrap_or(""),
                &r.pending,
            ));
            let at = d.get("sent_at").and_then(Value::as_i64).unwrap_or(0);
            let own = e
                .get("cards")
                .and_then(Value::as_array)
                .and_then(|c| {
                    c.iter()
                        .filter_map(Value::as_str)
                        .find(|c| ids.contains(*c))
                })
                .map(str::to_owned);
            out.push(Item {
                thread: thread_id("drive"),
                thread_title: "自動推進的信(匯入)".into(),
                category: Some("drive"),
                key: format!("drive:{id}"),
                from: from_of(ledger_sender(&d).as_deref()),
                at,
                card: own.or_else(|| card_in(&body, ids)),
                source: json!({"kind": "drive", "id": id, "to": to,
                    "at": crate::sessions::rfc3339_of_unix(at)}),
                body,
                doc: None,
                card_fallback: false,
            });
        }
    }
    out
}

/// One 影子續跑 record as lines a person reads.
pub fn shadow_text(d: &Value) -> String {
    let mut t = format!(
        "{}\n",
        d.get("kind").and_then(Value::as_str).unwrap_or("影子續跑")
    );
    for (k, label) in [
        ("required", "必跑"),
        ("next_actions", "下一步"),
        ("savings", "省下"),
    ] {
        if let Some(a) = d.get(k).and_then(Value::as_array).filter(|a| !a.is_empty()) {
            t.push_str(&format!("{label}:\n"));
            for v in a {
                let line = match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                t.push_str(&format!("- {line}\n"));
            }
        }
    }
    t
}

/// The 影子續跑 records: one per seat file and record time.
pub fn shadow_items(r: &Roots, ids: &BTreeSet<String>, skipped: &mut usize) -> Vec<Item> {
    let mut out = Vec::new();
    for p in sorted_files(&r.state.join("shadow-explain"), |n| n.ends_with(".json")) {
        let stem = p.file_stem().and_then(|n| n.to_str()).unwrap_or("");
        let Some(d) = read_json(&p) else {
            *skipped += 1;
            continue;
        };
        #[allow(clippy::cast_possible_truncation)]
        let at = d.get("at").and_then(Value::as_f64).unwrap_or(0.0) as i64;
        let body = clean(&shadow_text(&d));
        out.push(Item {
            thread: thread_id("shadow"),
            thread_title: "影子續跑紀錄(匯入)".into(),
            category: Some("shadow"),
            key: format!("shadow:{stem}:{at}"),
            from: from_of(Some(stem)),
            at,
            card: card_in(&body, ids),
            source: json!({"kind": "shadow", "id": stem, "at": crate::sessions::rfc3339_of_unix(at)}),
            body,
            doc: None,
            card_fallback: false,
        });
    }
    out
}

/// The node a record that names none is put under (519d0 01:33: V 總綱, until a seat → main
/// item table exists); used only when the plan has it.
pub const FALLBACK_NODE: &str = "V";

/// Records that name no card go under [`FALLBACK_NODE`], marked so.
pub fn with_fallback(items: &mut [Item], ids: &BTreeSet<String>) {
    if !ids.contains(FALLBACK_NODE) {
        return;
    }
    for it in items.iter_mut().filter(|i| i.card.is_none()) {
        it.card = Some(FALLBACK_NODE.to_owned());
        it.card_fallback = true;
    }
}

/// The items of `kind`; how many records were skipped.
pub fn items_of(kind: &str, r: &Roots, ids: &BTreeSet<String>) -> (Vec<Item>, usize) {
    let mut skipped = 0;
    let v = match kind {
        "dm" => dm_items(r, ids, &mut skipped),
        "council" => council_items(r, ids, &mut skipped),
        "drive" => drive_items(r, ids, &mut skipped),
        "shadow" => shadow_items(r, ids, &mut skipped),
        _ => Vec::new(),
    };
    (v, skipped)
}

/// One item's post.
pub fn row_of(it: &Item) -> Result<MessageRow, String> {
    let id = crate::sessions::new_id("q_").map_err(|e| e.to_string())?;
    let when = crate::sessions::rfc3339_of_unix(it.at);
    // 5d33a CHECK-docroot 條件 4: no mail address reaches the page
    let body = cut(&crate::docs::mask_mail(&it.body));
    let mut item: Map<String, Value> = aib_dmq::norm::v2_defaults();
    for (k, v) in [
        ("id", json!(id)),
        ("status", json!("delivered")),
        ("talk_kind", json!("say")),
        ("sender_seat", json!(it.from)),
        ("sender_kind", json!("seat")),
        ("thread", json!(it.thread)),
        ("sent_at", json!(it.at)),
        ("to", json!([])),
        ("cc", json!([])),
        ("sender_safe", json!(true)),
        ("user_page", json!(false)),
        ("source", it.source.clone()),
        ("card", json!(it.card)),
        ("card_fallback", json!(it.card_fallback)),
    ] {
        item.insert(k.into(), v);
    }
    if let Some(doc) = &it.doc {
        item.insert("doc".into(), doc.clone());
    }
    Ok(MessageRow {
        message_id: id,
        thread_id: it.thread.clone(),
        from_key: it.from.clone(),
        to_key: it.thread.clone(),
        created: when.clone(),
        created_unix: it.at,
        updated: when,
        rev: 1,
        item,
        body_sha256: digest_of(&body),
        len: i64::try_from(body.len()).unwrap_or(i64::MAX),
        body: Some(body),
        idem_owner: Some(OWNER.into()),
        idem_key: Some(it.key.clone()),
        journaled: false,
    })
}

/// Write `items` (no database ⇒ counted only).
pub fn write(items: &[Item], skipped: usize, db: Option<&MessageDb>) -> Result<Counts, String> {
    let mut c = Counts {
        skipped,
        ..Counts::default()
    };
    let Some(db) = db else {
        c.new = items.len();
        return Ok(c);
    };
    let mut opened = BTreeSet::new();
    for it in items {
        if opened.insert(it.thread.clone()) {
            let now = crate::sessions::now_rfc3339();
            let at = crate::revive::unix_of(&now).unwrap_or(0);
            let t = ThreadRow {
                thread_id: it.thread.clone(),
                node: None,
                title: it.thread_title.clone(),
                opened_by: "seat:import".into(),
                created: now,
                created_unix: at,
                last_unix: at,
            };
            match db.thread_open(t, Vec::new()).map_err(|e| e.to_string())? {
                Opened::New | Opened::Taken | Opened::Existing(_) => {}
            }
            if let Some(cat) = it.category {
                db.thread_set_category(&it.thread, cat)
                    .map_err(|e| e.to_string())?;
            }
        }
        match db
            .post_insert(row_of(it)?, Vec::new())
            .map_err(|e| e.to_string())?
        {
            Inserted::New => c.new += 1,
            Inserted::Existing(_) => c.already += 1,
        }
    }
    Ok(c)
}

/// `--undo`: every post of `kind` in every thread its items go to, and each thread left empty.
pub fn undo(kind: &str, items: &[Item], db: &MessageDb) -> Result<Done, String> {
    let threads: BTreeSet<&str> = items.iter().map(|i| i.thread.as_str()).collect();
    let (mut posts, mut gone) = (0, true);
    for t in threads {
        let (n, empty) = db
            .undo_import(t.to_owned(), OWNER.into(), format!("{kind}:"))
            .map_err(|e| e.to_string())?;
        posts += n;
        gone &= empty;
    }
    Ok(Done::Undone {
        posts,
        thread: gone,
    })
}

/// `aibd import <dm|council|drive|shadow> [--dry-run | --undo]`.
pub fn run(kind: &str, dry: bool, undo_it: bool, data_dir: &Path) -> Result<Done, String> {
    let r = Roots::from_env(data_dir).ok_or("no documents folder")?;
    let ids = node_ids(crate::workspace::current_tree().as_ref());
    let (mut items, skipped) = items_of(kind, &r, &ids);
    with_fallback(&mut items, &ids);
    if dry {
        return write(&items, skipped, None).map(Done::Imported);
    }
    let db =
        MessageDb::open(&data_dir.join(aib_store::MESSAGES_FILE)).map_err(|e| e.to_string())?;
    if undo_it {
        return undo(kind, &items, &db);
    }
    write(&items, skipped, Some(&db)).map(Done::Imported)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roots(d: &Path) -> Roots {
        let r = Roots {
            state: d.join("state"),
            pending: d.join("pending"),
            hub: Some("hub0z".into()),
        };
        std::fs::create_dir_all(&r.pending).unwrap();
        r
    }

    fn put(p: &Path, text: &str) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, text).unwrap();
    }

    #[test]
    fn a_card_is_the_first_node_id_named() {
        let ids: BTreeSet<String> = ["N-pv-up", "S91"].iter().map(|s| (*s).to_owned()).collect();
        assert_eq!(card_in("卡 S91 與 N-pv-up", &ids).as_deref(), Some("S91"));
        assert_eq!(
            card_in("N-pv-upx S910", &ids),
            None,
            "reason: part_of_word_matched"
        );
        assert_eq!(seat_code("@a9136"), Some("a9136"));
        assert_eq!(
            seat_code("34396"),
            Some("34396"),
            "reason: digit_seat_refused"
        );
        assert_eq!(seat_code("1118"), None);
        assert_eq!(seat_code("1118g"), None);
        let d = tempfile::tempdir().unwrap();
        assert_eq!(state_in(d.path()), None);
        let st = d.path().join("st");
        std::fs::create_dir(&st).unwrap();
        put(
            &d.path().join("bridge.json"),
            &json!({"import_state": st}).to_string(),
        );
        assert_eq!(state_in(d.path()), Some(st), "reason: bridge_json_not_read");
        let mut v = vec![Item {
            thread: "t".into(),
            thread_title: String::new(),
            category: None,
            key: "k".into(),
            from: "seat:import".into(),
            at: 0,
            body: String::new(),
            source: json!({}),
            card: None,
            card_fallback: false,
            doc: None,
        }];
        with_fallback(&mut v, &ids);
        assert_eq!(v[0].card, None, "reason: fallback_not_in_plan");
        let mut ids = ids;
        ids.insert("V".into());
        with_fallback(&mut v, &ids);
        assert_eq!(
            (v[0].card.as_deref(), v[0].card_fallback),
            (Some("V"), true)
        );
    }

    /// 0eed7 daaafb770 M3 / L4: only a 「長訊已落檔」 mail is replaced by its file, a Windows
    /// path too; a mail quoting another seat's relay file stays as written.
    #[test]
    fn only_a_relay_mail_is_replaced_by_its_file() {
        let d = tempfile::tempdir().unwrap();
        put(&d.path().join("RELAY-dm-y-1.txt"), "別席的長訊");
        let quote = "回你那封 /x/pending/RELAY-dm-y-1.txt 說的";
        assert_eq!(
            expand_relay(quote, d.path()),
            quote,
            "reason: quote_replaced"
        );
        let relay = "長訊已落檔(9 位元組),請讀:C:\\x\\RELAY-dm-y-1.txt";
        assert_eq!(
            expand_relay(relay, d.path()),
            "〔長訊 RELAY-dm-y-1.txt〕\n別席的長訊",
            "reason: windows_path_missed"
        );
    }

    /// 0eed7 daaafb770 L6: a ledger file that is a link, or too big, is not read.
    #[cfg(unix)]
    #[test]
    fn a_ledger_link_or_a_huge_file_is_not_read() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real.json");
        put(&real, "{}");
        let link = d.path().join("q_1.json");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert!(read_text(&real).is_ok(), "reason: file_refused");
        assert!(read_text(&link).is_err(), "reason: link_followed");
        let big = d.path().join("big.json");
        std::fs::File::create(&big)
            .unwrap()
            .set_len(READ_MAX + 1)
            .unwrap();
        assert!(read_text(&big).is_err(), "reason: huge_read");
    }

    /// 0eed7 daaafb770 M4 / M5: a seat named only in its label sends and names the pair; a
    /// label naming two seats names none.
    #[test]
    fn a_seat_named_only_in_its_label_sends_and_pairs_as_that_seat() {
        let d = tempfile::tempdir().unwrap();
        let r = roots(d.path());
        let q = r.state.join("dm-queue").join("519d0");
        put(
            &q.join("q_7.json"),
            &json!({"id": "q_7", "status": "read", "sender_kind": "seat", "sender_seat": "",
            "sender_label": "@7ebca(訊息線)", "sent_at": 1_794_752_770, "msg": "好"})
            .to_string(),
        );
        put(
            &q.join("q_8.json"),
            &json!({"id": "q_8", "status": "read", "sender_kind": "seat", "sender_seat": "",
            "sender_label": "@7ebca(接手 @a9136)", "sent_at": 1_794_752_771, "msg": "好"})
            .to_string(),
        );
        let mut skipped = 0;
        let items = dm_items(&r, &BTreeSet::new(), &mut skipped);
        let one = |k: &str| items.iter().find(|i| i.key == k).unwrap();
        assert_eq!(one("dm:q_7").from, "seat:7ebca", "reason: label_seat_lost");
        assert_eq!(
            one("dm:q_7").thread,
            thread_id("dm:519d0:7ebca"),
            "reason: pair_is_import"
        );
        assert_eq!(
            one("dm:q_8").from,
            "seat:import",
            "reason: second_at_trusted"
        );
    }

    /// The old ledger: one thread per pair, the relay file's text, the sender's seat, the
    /// original time; a deleted mail skipped; run again, nothing new; undo takes it all back.
    #[test]
    fn old_mail_goes_into_its_pairs_thread_once() {
        let d = tempfile::tempdir().unwrap();
        let r = roots(d.path());
        put(&r.pending.join("RELAY-dm-x-1.txt"), "長的內容 N-pv-up");
        let q = r.state.join("dm-queue");
        put(
            &q.join("a9136").join("q_1.json"),
            &json!({"id": "q_1", "status": "archived", "sender_kind": "seat",
            "sender_seat": "519d0", "sent_at": 1_794_752_770,
            "msg": "長訊已落檔,請讀:/x/pending/RELAY-dm-x-1.txt"})
            .to_string(),
        );
        put(
            &q.join("519d0").join("q_2.json"),
            &json!({"id": "q_2", "status": "read", "sender_kind": "seat",
            "sender_seat": "a9136", "sent_at": 1_794_752_800,
            "msg": "長訊已落檔,請讀:C:\\x\\pending\\RELAY-dm-gone-2.txt"})
            .to_string(),
        );
        put(
            &q.join("519d0").join("q_4.json"),
            &json!({"id": "q_4", "status": "archived", "sender_kind": "unknown",
            "sender_seat": "570a9", "sender_label": "agent:\u{202e}offhub", "sent_at": 7,
            "msg": "冒名?\u{1b}[31m紅\u{1b}[0m"})
            .to_string(),
        );
        put(
            &q.join("519d0").join("q_3.json"),
            &json!({"id": "q_3", "status": "deleted",
            "sender_seat": "a9136", "sent_at": 1, "msg": "x"})
            .to_string(),
        );
        put(
            &q.join("a9136").join("q_5.json"),
            &json!({"id": "q_5", "status": "read", "sender_kind": "human",
            "human_id": "h1", "sender_seat": "", "sender_label": "Andy", "sent_at": 9,
            "msg": "看一下"})
            .to_string(),
        );
        // to the person's hub code, and its answer: both in 「a9136 ↔ hub」
        put(
            &q.join("hub0z").join("q_6.json"),
            &json!({"id": "q_6", "status": "read", "sender_kind": "seat",
            "sender_seat": "a9136", "sent_at": 10, "msg": "回你"})
            .to_string(),
        );
        // the real shapes (519d0 06:13): a seat in the label only, an opening seat, a reminder
        for (id, v) in [
            (
                "q_7",
                json!({"sender_kind": "seat", "sender_seat": "",
                "sender_label": "@7ebca(AIB2訊息線直接翻工程師)", "msg": "定版頭"}),
            ),
            (
                "q_8",
                json!({"sender_kind": "unknown", "sender_label": "agent:offhub",
                "msg": "@abb89 → @a9136 派工"}),
            ),
            (
                "q_9",
                json!({"sender_kind": "unknown",
                "sender_label": "unattested:auto-find-work-dm",
                "msg": "提醒: 你有一封還沒收的信"}),
            ),
        ] {
            let mut v = v;
            v["id"] = json!(id);
            v["status"] = json!("read");
            v["sent_at"] = json!(11);
            put(&q.join("a6b1c").join(format!("{id}.json")), &v.to_string());
        }
        let ids: BTreeSet<String> = ["N-pv-up", "S91"].iter().map(|s| (*s).to_owned()).collect();
        let (items, skipped) = items_of("dm", &r, &ids);
        let shape = |k: &str| {
            let i = items.iter().find(|i| i.key == k).unwrap();
            (
                i.from.clone(),
                i.source
                    .get("said_seat")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                i.source.get("system") == Some(&json!(true)),
            )
        };
        assert_eq!(
            shape("dm:q_7"),
            ("seat:7ebca".into(), Some("7ebca".into()), false),
            "reason: label_seat_not_read"
        );
        assert_eq!(
            shape("dm:q_8"),
            ("seat:import".into(), Some("abb89".into()), false),
            "reason: opening_seat_not_read"
        );
        let said_from = |k: &str| {
            let i = items.iter().find(|i| i.key == k).unwrap();
            i.source
                .get("said_from")
                .and_then(Value::as_str)
                .map(str::to_owned)
        };
        assert_eq!(
            said_from("dm:q_8").as_deref(),
            Some("opening"),
            "reason: guess_unmarked"
        );
        assert_eq!(said_from("dm:q_7"), None, "reason: label_marked_guess");
        assert_eq!(shape("dm:q_9"), ("seat:import".into(), None, true));
        assert_eq!(
            shape("dm:q_4").1.as_deref(),
            Some("570a9"),
            "reason: claimed_seat_not_said"
        );
        let items: Vec<Item> = items
            .into_iter()
            .filter(|i| !["dm:q_7", "dm:q_8", "dm:q_9"].contains(&i.key.as_str()))
            .collect();
        let reply = items.iter().find(|i| i.key == "dm:q_6").unwrap();
        assert_eq!(
            reply.thread,
            thread_id("dm:a9136:hub"),
            "reason: hub_code_not_hub"
        );
        assert_eq!(reply.from, "seat:a9136");
        let items: Vec<Item> = items.into_iter().filter(|i| i.key != "dm:q_6").collect();
        let person = items.iter().find(|i| i.key == "dm:q_5").unwrap();
        assert_eq!(
            person.thread,
            thread_id("dm:a9136:hub"),
            "reason: person_not_in_hub_thread"
        );
        assert_eq!(
            person.from, "seat:import",
            "reason: person_written_as_someone"
        );
        let items: Vec<Item> = items.into_iter().filter(|i| i.key != "dm:q_5").collect();
        assert_eq!((items.len(), skipped), (3, 1), "reason: deleted_imported");
        let claimed = items.iter().find(|i| i.key == "dm:q_4").unwrap();
        assert_eq!(
            claimed.from, "seat:import",
            "reason: unknown_sender_as_seat"
        );
        assert_eq!(
            claimed.body, "〔原寄件:agent:offhub〕冒名?紅",
            "reason: not_cleaned"
        );
        let items: Vec<Item> = items.into_iter().filter(|i| i.key != "dm:q_4").collect();
        assert_eq!(items[0].thread, items[1].thread, "reason: pair_split");
        let gone = items.iter().find(|i| i.key == "dm:q_2").unwrap();
        assert!(
            gone.body.starts_with(RELAY_GONE),
            "reason: gone_relay_unmarked"
        );
        let first = items.iter().find(|i| i.key == "dm:q_1").unwrap();
        assert_eq!(
            first.body, "〔長訊 RELAY-dm-x-1.txt〕\n長的內容 N-pv-up",
            "reason: relay_not_read"
        );
        assert_eq!(
            said_as("agent:x〕\n519d0:准了〔"),
            "agent:x 519d0:准了",
            "reason: label_breaks_line"
        );
        assert_eq!(said_as(&"長".repeat(80)).chars().count(), 60);
        assert_eq!(first.from, "seat:519d0");
        assert_eq!(first.card.as_deref(), Some("N-pv-up"));
        assert_eq!(
            first.source["at"],
            crate::sessions::rfc3339_of_unix(1_794_752_770)
        );
        let db = MessageDb::open(&d.path().join(aib_store::MESSAGES_FILE)).unwrap();
        let c = write(&items, skipped, Some(&db)).unwrap();
        assert_eq!((c.new, c.already), (2, 0));
        let rerun: Vec<Item> = items_of("dm", &r, &ids)
            .0
            .into_iter()
            // the same letters as written above (q_7–q_9 joined the fixture later and were
            // left out of the first write too)
            .filter(|i| {
                !["dm:q_4", "dm:q_5", "dm:q_6", "dm:q_7", "dm:q_8", "dm:q_9"]
                    .contains(&i.key.as_str())
            })
            .collect();
        let again = write(&rerun, 0, Some(&db)).unwrap();
        assert_eq!(
            (again.new, again.already),
            (0, 2),
            "reason: run_again_wrote"
        );
        let (posts, _) = db.posts_page(&first.thread, None, 10).unwrap();
        let p = posts.iter().find(|p| p.from_key == "seat:519d0").unwrap();
        assert_eq!(p.created_unix, 1_794_752_770, "reason: time_not_original");
        assert_eq!(p.item["card"], "N-pv-up");
        assert_eq!(p.item["source"]["kind"], "dm");
        assert_eq!(
            undo("dm", &items, &db).unwrap(),
            Done::Undone {
                posts: 2,
                thread: true
            }
        );
    }

    /// 自動推進's mail: sent lines only (not a dry run, not twice), the mail from the ledger,
    /// the line's own card first.
    #[test]
    fn drive_mail_is_read_from_the_ledger_with_its_card() {
        let d = tempfile::tempdir().unwrap();
        let r = roots(d.path());
        let ev = r
            .state
            .join("aib-drive")
            .join("events")
            .join("2026-11.jsonl");
        let line = |id: &str, outcome: &str, dry: bool| {
            json!({"kind": "drive_send", "outcome": outcome, "dry_run": dry, "to": "a9136",
                "dm_id": id, "cards": ["S91"], "at": "2026-11-17T17:03:44Z"})
            .to_string()
        };
        put(
            &ev,
            &[
                line("q_a", "sent", false),
                line("q_a", "sent", false),
                line("q_b", "sent", true),
                line("q_c", "failed", false),
            ]
            .join("\n"),
        );
        put(
            &r.state.join("dm-queue").join("a9136").join("q_a.json"),
            &json!({"id": "q_a", "sender_kind": "seat", "sender_seat": "f1fae", "sent_at": 5,
                "msg": "派工: N-pv-up"})
            .to_string(),
        );
        let ids: BTreeSet<String> = ["N-pv-up", "S91"].iter().map(|s| (*s).to_owned()).collect();
        let (items, _) = items_of("drive", &r, &ids);
        assert_eq!(items.len(), 1, "reason: dry_or_failed_or_twice");
        assert_eq!(
            items[0].card.as_deref(),
            Some("S91"),
            "reason: own_card_not_first"
        );
        assert_eq!(items[0].from, "seat:f1fae");
        assert_eq!(items[0].category, Some("drive"));
    }

    #[test]
    fn council_and_shadow_records_are_posted_with_their_seat() {
        let d = tempfile::tempdir().unwrap();
        let r = roots(d.path());
        put(
            &r.pending.join("DECISION-council-x-519d0-1117.md"),
            "# 定稿\n卡 S91",
        );
        put(&r.pending.join("DECISION-other-519d0.md"), "# 不是");
        put(
            &r.state.join("shadow-explain").join("043dd.json"),
            &json!({"at": 1_794_181_849.5, "kind": "aib test", "required": ["a"],
                "next_actions": [], "savings": [{"s": 1}]})
            .to_string(),
        );
        let ids: BTreeSet<String> = ["S91"].iter().map(|s| (*s).to_owned()).collect();
        let (c, _) = items_of("council", &r, &ids);
        assert_eq!(c.len(), 1);
        assert_eq!(
            (c[0].from.as_str(), c[0].card.as_deref()),
            ("seat:519d0", Some("S91"))
        );
        assert_eq!(c[0].doc.as_ref().unwrap()[0]["title"], "定稿");
        let (s, _) = items_of("shadow", &r, &ids);
        assert_eq!(s[0].key, "shadow:043dd:1794181849");
        assert!(s[0].body.contains("必跑:\n- a\n"), "{}", s[0].body);
        assert_eq!(s[0].from, "seat:043dd");
    }
}
