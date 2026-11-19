//! N-ws-thread-cat §17-3: `aibd import <summary|handoff> [--dry-run | --undo]` brings the real
//! conversations kept as files into the message library, so the page's 總結 / 交接 tabs show
//! them (a9136 DESIGN-thread-cat §3).
//!
//! - Read only: the files under [`crate::docs::root`] named `SUMMARY-*` / `HANDOFF-*`; one
//!   [`crate::docs::resolve`] refuses (a secret kind by name, 5d33a D3, among them) is skipped
//!   and counted.
//! - One thread per kind (a fixed id from the kind, its category set once); one post per file:
//!   the sender is the seat the file names (`seat:<5 hex>`, `seat:import` when it names none),
//!   never a person; `sender_safe` true (a dgrs seat does not read it); `doc` registers the file
//!   (`{root: "pending", file, title}`, read through GET /api/v1/doc); `source` says where it
//!   came from. The body is the file's start, cut.
//! - Run again: nothing new — a post's idem key is `<kind>:<file>` under the owner `import`.
//! - `--undo` takes a kind's import back whole: its posts (owner `import`, key `<kind>:`), and the
//!   thread when nothing else is in it (519d0 §17-3 「可整批撤回」).
use aib_store::{Inserted, MessageDb, MessageRow, Opened, ThreadRow};
use serde_json::{json, Map, Value};
use std::path::Path;

/// The owner of an imported post's idempotency key.
pub const OWNER: &str = "import";

/// What `aibd import` did.
#[derive(Debug, PartialEq, Eq)]
pub enum Done {
    Imported(Counts),
    /// posts removed, and whether the kind's thread went with them
    Undone {
        posts: usize,
        thread: bool,
    },
}

/// The body kept from a file (its start; the rest is read through its doc link).
pub const BODY_MAX: usize = 1500;

/// What one run did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub new: usize,
    pub already: usize,
    pub skipped: usize,
}

/// A kind this command imports: (argument, file prefix, category, thread title).
const KINDS: [(&str, &str, &str, &str); 2] = [
    ("summary", "SUMMARY-", "summary", "總結(匯入)"),
    ("handoff", "HANDOFF-", "handoff", "交接(匯入)"),
];

/// The seat a file name names: the first `-<5 lower hex>-` or `-<5 lower hex>.` piece.
pub fn seat_of(file: &str) -> Option<String> {
    let stem = file.strip_suffix(".md").unwrap_or(file);
    // five lower hex with a letter in it (a date like 11180 is not a seat)
    stem.split('-')
        .find(|p| {
            p.len() == 5
                && p.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                && p.bytes().any(|b| b.is_ascii_alphabetic())
        })
        .map(str::to_owned)
}

/// The title: the file's first `# ` heading, else its name.
pub fn title_of(file: &str, text: &str) -> String {
    text.lines()
        .find_map(|l| l.strip_prefix("# "))
        .map(|t| t.trim().chars().take(120).collect())
        .unwrap_or_else(|| file.to_owned())
}

/// The kind's thread id: fixed, so every run writes into the same thread.
pub fn thread_of(kind: &str) -> String {
    let h = digest(&format!("aibd-import:{kind}"));
    format!("t_{}", &h[..16])
}

/// A text's sha256 (hex).
pub fn digest_of(t: &str) -> String {
    digest(t)
}

fn digest(t: &str) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(t.as_bytes())
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

fn cut(text: &str) -> String {
    let mut s: String = text.chars().take(BODY_MAX).collect();
    if s.len() < text.len() {
        s.push('…');
    }
    s
}

/// `aibd import ...`: the arguments after `import`.
pub fn run(args: &[&str], data_dir: &Path) -> Result<Done, String> {
    // Andy 11-19 04:3x: the records put by card (import_regroup.rs)
    if args.first() == Some(&"regroup") {
        return crate::import_regroup::run(&args[1..], data_dir);
    }
    let (kind, dry, undo) =
        match args {
            [k] => (*k, false, false),
            [k, "--dry-run"] | ["--dry-run", k] => (*k, true, false),
            [k, "--undo"] | ["--undo", k] => (*k, false, true),
            _ => return Err(
                "usage: aibd import <summary|handoff|dm|council|drive|shadow> [--dry-run | --undo]"
                    .into(),
            ),
        };
    // Andy 11-19 01:1x: the four other real records (import_more.rs)
    if crate::import_more::KINDS.contains(&kind) {
        return crate::import_more::run(kind, dry, undo, data_dir);
    }
    let Some(&(_, prefix, category, title)) = KINDS.iter().find(|(a, ..)| *a == kind) else {
        return Err(format!(
            "unknown kind {kind:?}: summary, handoff, dm, council, drive or shadow"
        ));
    };
    if undo {
        let db =
            MessageDb::open(&data_dir.join(aib_store::MESSAGES_FILE)).map_err(|e| e.to_string())?;
        return undo_kind(&db, category);
    }
    let root = crate::docs::doc_root("pending").ok_or("no documents folder")?;
    let db = if dry {
        None
    } else {
        Some(MessageDb::open(&data_dir.join(aib_store::MESSAGES_FILE)).map_err(|e| e.to_string())?)
    };
    import_from(&root, prefix, category, title, db.as_ref()).map(Done::Imported)
}

/// `--undo`: every post this command wrote for `category`, and its thread when left empty.
pub fn undo_kind(db: &MessageDb, category: &str) -> Result<Done, String> {
    let (posts, thread) = db
        .undo_import(thread_of(category), OWNER.into(), format!("{category}:"))
        .map_err(|e| e.to_string())?;
    Ok(Done::Undone { posts, thread })
}

/// The import itself (no database ⇒ a dry run: counted, nothing written).
pub fn import_from(
    root: &Path,
    prefix: &str,
    category: &str,
    title: &str,
    db: Option<&MessageDb>,
) -> Result<Counts, String> {
    let mut names: Vec<String> = std::fs::read_dir(root)
        .map_err(|e| format!("{}: {e}", root.display()))?
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter(|n| n.starts_with(prefix) && n.ends_with(".md"))
        .collect();
    names.sort();
    let thread_id = thread_of(category);
    if let Some(db) = db {
        let now = crate::sessions::now_rfc3339();
        let at = crate::revive::unix_of(&now).unwrap_or(0);
        let t = ThreadRow {
            thread_id: thread_id.clone(),
            node: None,
            title: title.to_owned(),
            opened_by: "seat:import".into(),
            created: now,
            created_unix: at,
            last_unix: at,
        };
        match db.thread_open(t, Vec::new()).map_err(|e| e.to_string())? {
            Opened::New | Opened::Taken | Opened::Existing(_) => {}
        }
        db.thread_set_category(&thread_id, category)
            .map_err(|e| e.to_string())?;
    }
    let mut c = Counts::default();
    for file in names {
        // a secret kind is refused by resolve (by name and by every piece of the path), the one
        // guard; knives-23-doc DL2 and knives-24-import IM2 keep it (no second copy here: a
        // knife on a copy could not tell it apart)
        let Ok(path) = crate::docs::resolve(root, &file) else {
            c.skipped += 1;
            continue;
        };
        let Ok(text) = std::fs::read_to_string(&path) else {
            c.skipped += 1;
            continue;
        };
        let Some(db) = db else {
            c.new += 1;
            continue;
        };
        let at = std::fs::metadata(&path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
            .and_then(|d| i64::try_from(d.as_secs()).ok())
            .unwrap_or(0);
        let row = post(&thread_id, category, &file, &text, at)?;
        match db.post_insert(row, Vec::new()).map_err(|e| e.to_string())? {
            Inserted::New => c.new += 1,
            Inserted::Existing(_) => c.already += 1,
        }
    }
    Ok(c)
}

/// One file's post.
pub fn post(
    thread_id: &str,
    category: &str,
    file: &str,
    text: &str,
    at: i64,
) -> Result<MessageRow, String> {
    let id = crate::sessions::new_id("q_").map_err(|e| e.to_string())?;
    let from = format!("seat:{}", seat_of(file).unwrap_or_else(|| "import".into()));
    let title = title_of(file, text);
    let when = crate::sessions::rfc3339_of_unix(at);
    let body = cut(text);
    let mut item: Map<String, Value> = aib_dmq::norm::v2_defaults();
    for (k, v) in [
        ("id", json!(id)),
        ("status", json!("delivered")),
        ("talk_kind", json!("say")),
        ("sender_seat", json!(from)),
        ("sender_kind", json!("seat")),
        ("thread", json!(thread_id)),
        ("sent_at", json!(at)),
        ("to", json!([])),
        ("cc", json!([])),
        ("sender_safe", json!(true)),
        ("user_page", json!(false)),
        (
            "doc",
            json!([{"root": "pending", "file": file, "title": title}]),
        ),
        ("source", json!({"kind": category, "id": file, "at": when})),
    ] {
        item.insert(k.into(), v);
    }
    Ok(MessageRow {
        message_id: id,
        thread_id: thread_id.to_owned(),
        from_key: from,
        // a post's to_key is its thread (posts_page and GET /api/v1/doc read it so; 5d33a
        // PREREAD 4ad11ad7 M1)
        to_key: thread_id.to_owned(),
        created: when.clone(),
        created_unix: at,
        updated: when,
        rev: 1,
        item,
        body_sha256: digest(&body),
        len: i64::try_from(body.len()).unwrap_or(i64::MAX),
        body: Some(body),
        idem_owner: Some(OWNER.into()),
        idem_key: Some(format!("{category}:{file}")),
        journaled: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_files_seat_and_title_are_read_from_it() {
        assert_eq!(
            seat_of("SUMMARY-1d-043dd-1113.md").as_deref(),
            Some("043dd")
        );
        assert_eq!(
            seat_of("HANDOFF-wsa-7b81e-1118.md").as_deref(),
            Some("7b81e")
        );
        assert_eq!(
            seat_of("HANDOFF-a9136-1117-1316.md").as_deref(),
            Some("a9136")
        );
        assert_eq!(
            seat_of("SUMMARY-0c105-1021-night.md").as_deref(),
            Some("0c105")
        );
        // a date is not a seat
        assert_eq!(
            seat_of("SUMMARY-night-11180.md"),
            None,
            "reason: date_read_as_seat"
        );
        assert_eq!(
            title_of("x.md", "---\nstatus: a\n---\n# 標題 一\n## 二"),
            "標題 一"
        );
        assert_eq!(title_of("x.md", "no heading"), "x.md");
        assert!(crate::threads::thread_id_ok(&thread_of("summary")));
        assert_ne!(thread_of("summary"), thread_of("handoff"));
    }

    /// §17-3: an import writes one post per file into its kind's thread, from the seat the
    /// file names (never a person), safe, with its doc; a secret kind is skipped; run again,
    /// nothing new.
    #[test]
    fn an_import_writes_each_file_once_from_its_seat() {
        let d = tempfile::tempdir().unwrap();
        let pending = d.path().join("pending");
        std::fs::create_dir(&pending).unwrap();
        std::fs::write(pending.join("SUMMARY-1d-043dd-1113.md"), "# 一天總結\n內容").unwrap();
        std::fs::write(pending.join("SUMMARY-token-a9136-1118.md"), "# 機密").unwrap();
        std::fs::write(pending.join("SUMMARY-night.md"), "沒有標題").unwrap();
        std::fs::write(pending.join("HANDOFF-a9136-1117-1316.md"), "# 交接").unwrap();
        let db = MessageDb::open(&d.path().join(aib_store::MESSAGES_FILE)).unwrap();
        let c = import_from(&pending, "SUMMARY-", "summary", "總結(匯入)", Some(&db)).unwrap();
        assert_eq!(
            c,
            Counts {
                new: 2,
                already: 0,
                skipped: 1
            },
            "reason: secret_not_skipped"
        );
        let again = import_from(&pending, "SUMMARY-", "summary", "總結(匯入)", Some(&db)).unwrap();
        assert_eq!(
            again,
            Counts {
                new: 0,
                already: 2,
                skipped: 1
            },
            "reason: run_again_wrote"
        );
        let t = thread_of("summary");
        let kinds = db.categories_of(std::slice::from_ref(&t)).unwrap();
        assert_eq!(kinds.get(&t).map(String::as_str), Some("summary"));
        let (posts, _) = db.posts_page(&t, None, 10).unwrap();
        assert_eq!(posts.len(), 2);
        let p = posts
            .iter()
            .find(|p| p.from_key == "seat:043dd")
            .expect("reason: seat_not_named");
        assert!(
            posts.iter().all(|p| !p.from_key.starts_with("human:")),
            "reason: written_as_person"
        );
        assert_eq!(p.item["sender_safe"], json!(true));
        assert_eq!(p.item["doc"][0]["file"], "SUMMARY-1d-043dd-1113.md");
        assert_eq!(p.item["doc"][0]["title"], "一天總結");
        assert!(posts.iter().any(|p| p.from_key == "seat:import"));
        // a dry run writes nothing and counts the same
        let dry = import_from(&pending, "HANDOFF-", "handoff", "交接(匯入)", None).unwrap();
        assert_eq!(dry.new, 1);
        assert!(
            db.thread_get(&thread_of("handoff")).unwrap().is_none(),
            "reason: dry_run_wrote"
        );
    }

    /// 519d0 §17-3 「可整批撤回」: --undo takes a kind's import back whole, leaves another kind
    /// and a post someone wrote into the thread, and the thread goes only once empty.
    #[test]
    fn an_import_is_taken_back_whole() {
        let d = tempfile::tempdir().unwrap();
        let pending = d.path().join("pending");
        std::fs::create_dir(&pending).unwrap();
        std::fs::write(pending.join("SUMMARY-1d-043dd-1113.md"), "# 一\n").unwrap();
        std::fs::write(pending.join("SUMMARY-2d-519d0-1114.md"), "# 二\n").unwrap();
        std::fs::write(pending.join("HANDOFF-a9136-1117-1316.md"), "# 交接").unwrap();
        let db = MessageDb::open(&d.path().join(aib_store::MESSAGES_FILE)).unwrap();
        import_from(&pending, "SUMMARY-", "summary", "總結(匯入)", Some(&db)).unwrap();
        import_from(&pending, "HANDOFF-", "handoff", "交接(匯入)", Some(&db)).unwrap();
        let (s, h) = (thread_of("summary"), thread_of("handoff"));
        // someone answered in the summary thread
        let mut mine = post(&s, "summary", "SUMMARY-x.md", "回一句", 1).unwrap();
        mine.idem_owner = Some("seat:a9136".into());
        mine.idem_key = Some("summary:reply".into());
        db.post_insert(mine, Vec::new()).unwrap();

        assert_eq!(
            undo_kind(&db, "summary").unwrap(),
            Done::Undone {
                posts: 2,
                thread: false
            },
            "reason: undo_count"
        );
        let (left, _) = db.posts_page(&s, None, 10).unwrap();
        assert_eq!(left.len(), 1, "reason: other_post_removed");
        assert_eq!(left[0].idem_owner.as_deref(), Some("seat:a9136"));
        assert_eq!(
            db.posts_page(&h, None, 10).unwrap().0.len(),
            1,
            "reason: other_kind_removed"
        );
        // an emptied thread goes with its kind
        assert_eq!(
            undo_kind(&db, "handoff").unwrap(),
            Done::Undone {
                posts: 1,
                thread: true
            }
        );
        assert!(
            db.thread_get(&h).unwrap().is_none(),
            "reason: empty_thread_kept"
        );
        assert!(db
            .categories_of(std::slice::from_ref(&h))
            .unwrap()
            .is_empty());
        // and the import runs again from nothing
        let c = import_from(&pending, "HANDOFF-", "handoff", "交接(匯入)", Some(&db)).unwrap();
        assert_eq!(c.new, 1, "reason: reimport_after_undo {c:?}");
    }
}
