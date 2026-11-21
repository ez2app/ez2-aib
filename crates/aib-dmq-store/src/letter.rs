//! One queued 1.x letter, written the way aibd's `legacy_send` writes one (S79-rust, 5d33a
//! PREREAD-drive-rust 0/0/2/2): aibd's page bridge and aib-drive share this writer, so a letter
//! from either is the same bytes under the same locks the frozen Python writer takes.
//!
//! - [`item`]: the fields `_new_item` writes (aib-dm-queue-item/v2), status `queued`.
//! - [`relay`]: a text longer than the 1.x limit goes to a private file and the letter is the
//!   pointer line.
//! - [`enqueue`]: the item, then `order.json`, each through [`Store`] (folder lock: the very
//!   flock the Python `atomic_json` takes on Unix, `.dir.lock` on Windows).
//!
//! Delivery is not here: the 1.x drainer delivers a queued letter as it does any other.

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::store::{Store, StoreError};

/// The letter's fields, as `_new_item` writes them for a person's letter: `sender_seat` is
/// `hub`, `sender_kind` is `human`. A caller that sends as a seat or as the system changes
/// those keys after.
pub fn item(
    id: &str,
    seat: &str,
    msg: &str,
    sender_label: &str,
    human_id: &str,
    hub: &str,
    now: i64,
) -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in [
        ("schema", json!("aib-dm-queue-item/v2")),
        ("id", json!(id)),
        ("ts", json!(now)),
        ("when", json!(crate::recv::taipei_stamp(now))),
        ("priority", json!("normal")),
        ("origin", json!("live")),
        ("status", json!("queued")),
        ("claimed_at", Value::Null),
        ("claim_id", Value::Null),
        ("seat", json!(seat)),
        ("session_uuid", json!("")),
        ("wid", json!("")),
        ("provider", json!("")),
        ("hub", json!(hub)),
        ("sender_hub", json!(hub)),
        ("sender_origin", json!(human_id)),
        ("sender_label", json!(sender_label)),
        ("sender_kind", json!("human")),
        ("msg", json!(msg)),
        ("rev", json!(1)),
    ] {
        m.insert(k.into(), v);
    }
    for (k, v) in aib_dmq::norm::v2_defaults() {
        m.entry(k).or_insert(v);
    }
    for (k, v) in [
        ("thread", Value::Null),
        ("kind", json!("other")),
        ("sender_seat", json!("hub")),
        ("human_id", json!(human_id)),
        ("sender_source", Value::Null),
        ("sent_at", json!(now)),
        ("expires", Value::Null),
        ("attempts", json!(0)),
    ] {
        m.insert(k.into(), v);
    }
    m
}

/// R-30 and L1 over the whole text (aibd legacy_send, aib-drive): the first problem refuses
/// the letter, nothing is fixed. `(code, why)`: `invalid_request`, `text_too_long`, `lint_refused`.
pub fn check_text(text: &str, max: usize) -> Result<(), (&'static str, String)> {
    if text.trim().is_empty() {
        return Err(("invalid_request", "text is empty".into()));
    }
    if text.len() > max {
        return Err(("text_too_long", format!("text is over {max} bytes")));
    }
    if let Some(f) = aib_dmq::lint::r30::lint(text).first() {
        return Err((
            "lint_refused",
            format!(
                "R-30: line {} column {} {} {} ({})",
                f.line, f.col, f.codepoint, f.label, f.code
            ),
        ));
    }
    // 5d33a PREREAD-integws2 L1: a line is cut at \n only, so any other break (\r, \u{85},
    // \u{2028}, \u{2029}) or control character would hide a header-shaped line from the check
    if let Some(c) = text.chars().find(|c| {
        (c.is_control() && !matches!(c, '\n' | '\t')) || matches!(c, '\u{2028}' | '\u{2029}')
    }) {
        return Err((
            "lint_refused",
            format!("U+{:04X} is not allowed in a letter", c as u32),
        ));
    }
    for (n, line) in text.split('\n').enumerate() {
        if line.contains(" >> ")
            || line.contains("長訊已落檔")
            || aib_dmq::header::parse(line).is_some()
        {
            return Err((
                "lint_refused",
                format!("line {} looks like a letter's header", n + 1),
            ));
        }
    }
    Ok(())
}

/// The 1.x pointer line a long letter is replaced by.
pub fn pointer_line(bytes: usize, path: &Path) -> String {
    format!("長訊已落檔({bytes} 位元組),請讀:{}", path.display())
}

/// `text` as the letter carries it: itself when at most `at` bytes; otherwise written to a new
/// private file `<dir>/<prefix>-<seat>-<now>-<tail>.txt` (`tail`: the caller's random hex;
/// never an existing file) and the letter is the pointer line (the path is returned).
pub fn relay(
    dir: &Path,
    prefix: &str,
    seat: &str,
    now: i64,
    tail: &str,
    text: &str,
    at: usize,
) -> std::io::Result<(String, Option<PathBuf>)> {
    if text.len() <= at {
        return Ok((text.to_owned(), None));
    }
    let path = dir.join(format!("{prefix}-{seat}-{now}-{tail}.txt"));
    let mut f = crate::fsx::create_private(&path, true)?;
    std::io::Write::write_all(&mut f, text.as_bytes())?;
    Ok((pointer_line(text.len(), &path), Some(path)))
}

/// Why [`enqueue`] did not finish.
#[derive(Debug)]
pub enum Enqueue {
    /// Nothing was written.
    NotWritten(String),
    /// The item is in place but `order.json` was not changed: the drainer may not see it.
    ItemOnly(String),
}

/// Write `item` (its `id`) into `seat`'s folder, then add the id to `order.json`.
pub fn enqueue(store: &Store, seat: &str, item: &Map<String, Value>) -> Result<(), Enqueue> {
    let id = item
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| Enqueue::NotWritten("the item has no id".into()))?;
    store
        .write_item(seat, item)
        .map_err(|e: StoreError| Enqueue::NotWritten(format!("letter: {e}")))?;
    store
        .order_append(seat, id)
        .map_err(|e| Enqueue::ItemOnly(format!("order: {e}")))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// LT1 (5d33a L1): letters written at once from several writers, each with its own Store
    /// (its own folder lock), all land in order.json once each.
    #[test]
    fn letters_written_together_all_reach_the_order() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        std::fs::create_dir_all(root.join("state/dm-queue/519d0")).unwrap();
        let mut hands = Vec::new();
        for w in 0..4 {
            let root = root.to_path_buf();
            hands.push(std::thread::spawn(move || {
                let store = Store::new(&root, &root);
                for n in 0..10 {
                    let id = format!("q_{w:02x}{n:014x}");
                    let it = item(&id, "519d0", "x", "l", "", "", 1_700_000_000);
                    enqueue(&store, "519d0", &it).unwrap();
                }
            }));
        }
        for h in hands {
            h.join().unwrap();
        }
        let store = Store::new(root, root);
        let mut ids = store.order_ids("519d0").unwrap();
        assert_eq!(ids.len(), 40, "reason: order_lost_a_letter");
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), 40, "reason: order_twice");
        for id in &ids {
            assert!(root
                .join(format!("state/dm-queue/519d0/{id}.json"))
                .is_file());
        }
    }

    /// LT2: a long text goes to a new private file and the letter is the pointer line; a short
    /// one is itself.
    #[test]
    fn a_long_text_is_relayed_and_a_short_one_is_not() {
        let d = tempfile::tempdir().unwrap();
        let (m, p) = relay(d.path(), "RELAY-t", "519d0", 7, "ab", "short", 10).unwrap();
        assert_eq!((m.as_str(), p), ("short", None));
        let long = "長".repeat(10);
        let (m, p) = relay(d.path(), "RELAY-t", "519d0", 7, "ab", &long, 10).unwrap();
        let p = p.expect("reason: long_not_relayed");
        assert_eq!(std::fs::read_to_string(&p).unwrap(), long);
        assert_eq!(m, pointer_line(30, &p));
        let name = p.file_name().unwrap().to_string_lossy().into_owned();
        assert_eq!(name, "RELAY-t-519d0-7-ab.txt");
        assert!(
            relay(d.path(), "RELAY-t", "519d0", 7, "ab", &long, 10).is_err(),
            "reason: relay_over_an_existing_file"
        );
    }

    /// LT3: an item without an id writes nothing.
    #[test]
    fn an_item_without_an_id_writes_nothing() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        std::fs::create_dir_all(root.join("state/dm-queue/519d0")).unwrap();
        let store = Store::new(root, root);
        let mut it = item("q_0123456789abcdef", "519d0", "x", "l", "", "", 1);
        it.remove("id");
        assert!(matches!(
            enqueue(&store, "519d0", &it),
            Err(Enqueue::NotWritten(_))
        ));
        assert!(store.order_ids("519d0").unwrap().is_empty());
    }
}
