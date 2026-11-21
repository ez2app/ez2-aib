//! N-ws-doc-link (SPEC-workspace-multiseat §17 point 4; 5d33a CHECKLIST-doc-link D1–D4):
//! `GET /api/v1/doc?thread_id=&message_id=&n=` gives the document a summary / hand-over post
//! names, as plain text.
//!
//! - Who: a paired page that is not a user folder page (a carried pairing may read); the
//!   reader must read the thread too (the same check as reading it). A seat token never
//!   reaches it (the guard), the start-up token and a user folder page get 403.
//! - Which file: only the post's own `doc` entry (`{file, title}`, or a list of them; set by
//!   aibd / the import, never by `thread_send`). The request names no path. The file must sit
//!   in the pending folder after `canonicalize` (compared by path components), be a regular file
//!   of at most [`DOC_MAX`] bytes, and not be a secret kind.
//! - N-web-oneline 3 (5d33a CHECK-docroot-5d33a-1119): the name must start with a [`DOC_KINDS`]
//!   entry and be one piece (no sub-folder), the file must not be a symbolic link
//!   (`doc_kind_not_listed` / `doc_refused`), and a RELAY- / RECEIPT- text has its e-mail
//!   addresses masked before the page gets it.
//! - What: `text/plain; charset=utf-8` with `nosniff`; errors say `doc_not_found` /
//!   `doc_refused` / `doc_too_large` only, never a path. The `doc.read` event keeps the name,
//!   length and hash, not the text.

use crate::api::AppState;
use crate::guard::{request_id, Caller};
use aib_store::NewEvent;
use axum::extract::{Query, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// The largest document read (5d33a D2 "例 1 MiB").
pub const DOC_MAX: u64 = 1 << 20;

/// Name pieces that mark a secret kind (5d33a D3); matched on the lower-cased file name.
pub const SECRET_MARKS: &[&str] = &[
    "名單",
    "roster",
    "secret",
    "token",
    "credential",
    ".env",
    ".key",
    "-key",
    "_key",
    "apikey",
    "password",
];

/// N-web-oneline 3 (5d33a CHECK-docroot-5d33a-1119 條件 1): the only kinds of document given to
/// the page, by the start of the file name, case as written. A program constant, not a setting
/// (a setting is a file an AI can change); adding one is a code change sent for review.
pub const DOC_KINDS: &[&str] = &[
    "RECEIPT-",
    "RELAY-",
    "SUMMARY-",
    "HANDOFF-",
    "DECISION-",
    "SPEC-",
    "PLAN-",
    "REVIEW-",
    "PREREAD-",
    "CHECK-",
    "VERIFY-",
    "DEEPREVIEW-",
    "SURVEY-",
    "DESIGN-",
    "IFACE-",
    "ORDER-",
    "BOUNDARY-",
];

/// A file name the page may be given: one piece (no folder) starting with a [`DOC_KINDS`] entry.
pub fn listed(file: &str) -> bool {
    !file.contains(['/', '\\']) && DOC_KINDS.iter().any(|k| file.starts_with(k))
}

/// The kinds whose text has e-mail addresses masked before the page sees it (條件 4).
pub fn masked_kind(file: &str) -> bool {
    file.starts_with("RELAY-") || file.starts_with("RECEIPT-")
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

#[derive(Debug, PartialEq, Eq)]
pub enum DocErr {
    NotFound,
    Refused,
    TooLarge,
    /// not a [`DOC_KINDS`] name, or not one piece
    NotListed,
}

/// The white-listed folders a post's `doc.root` may name (a9136 DESIGN-thread-cat: a code, not
/// a path; one table in aibd, which the import and this reader both use).
pub const DOC_ROOTS: &[&str] = &["pending"];

/// The folder a `doc.root` code names (`None` for any other code).
pub fn doc_root(code: &str) -> Option<PathBuf> {
    match code {
        "pending" => root(),
        _ => None,
    }
}

/// Every mail address in `text` as 〈信箱 長度 n〉 (n = its length in characters; 5d33a
/// CHECK-docroot-5d33a-1119 條件 4). One place for the import and the document reading.
/// An address: ASCII local part (letters, digits, `._%+-`), `@`, a domain with a dot and a
/// last part of two letters or more. `@a9136` (a seat call) is not one.
pub fn mask_mail(text: &str) -> String {
    let local = |c: char| c.is_ascii_alphanumeric() || "._%+-".contains(c);
    let domain = |c: char| c.is_ascii_alphanumeric() || c == '.' || c == '-';
    let cs: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    let mut start_free = 0;
    while i < cs.len() {
        if cs[i] != '@' {
            i += 1;
            continue;
        }
        let mut a = i;
        while a > start_free && local(cs[a - 1]) {
            a -= 1;
        }
        let mut b = i + 1;
        while b < cs.len() && domain(cs[b]) {
            b += 1;
        }
        // a domain does not end in a dot or a dash (sentence end)
        while b > i + 1 && (cs[b - 1] == '.' || cs[b - 1] == '-') {
            b -= 1;
        }
        let dom: String = cs[i + 1..b].iter().collect();
        let tld_ok = dom.rsplit_once('.').is_some_and(|(h, t)| {
            !h.is_empty() && t.len() >= 2 && t.chars().all(|c| c.is_ascii_alphabetic())
        });
        if a < i && tld_ok {
            out.extend(&cs[start_free..a]);
            out.push_str(&format!("〈信箱 長度 {}〉", b - a));
            start_free = b;
            i = b;
        } else {
            i += 1;
        }
    }
    out.extend(&cs[start_free..]);
    out
}

/// The folder documents are read from ([`crate::folders::docs_root`]: a checked
/// `AIBD_PLAN_PENDING`, `~/.ez2-aib/pending`, or `<aibd data>/docs(.noindex)`).
pub fn root() -> Option<PathBuf> {
    crate::folders::docs_root()
}

/// A secret kind by its name (5d33a D3).
pub fn secret_name(file: &str) -> bool {
    let low = file.to_lowercase();
    SECRET_MARKS.iter().any(|m| low.contains(m))
}

/// Where `file` (as the post registered it, relative to `root`) really is, once it is known to
/// stay in `root`, to be a regular file no larger than [`DOC_MAX`] and not a secret kind.
pub fn resolve(root: &Path, file: &str) -> Result<PathBuf, DocErr> {
    let rel = Path::new(file);
    if file.is_empty() || rel.is_absolute() || rel.has_root() {
        return Err(DocErr::Refused);
    }
    // 5d33a CHECK-docroot 條件 1–3: a listed kind, one piece in the folder itself, not a link;
    // then every older rule below as it was ([`resolve_in`], kept as a second wall)
    if !listed(file) {
        return Err(DocErr::NotListed);
    }
    let base = root.canonicalize().map_err(|_| DocErr::NotFound)?;
    // a symbolic link is not followed at all (條件 3)
    match std::fs::symlink_metadata(base.join(rel)) {
        Ok(m) if m.file_type().is_symlink() => return Err(DocErr::Refused),
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(DocErr::NotFound),
        Err(_) => return Err(DocErr::Refused),
    }
    resolve_in(root, file)
}

/// The rules before N-web-oneline 3 (5d33a CHECKLIST-doc-link D1–D3, PREREAD N2): inside the
/// folder after `canonicalize`, no secret name in any piece, a regular file within [`DOC_MAX`].
/// [`resolve`] reaches it only for a listed one-piece name that is not a link, so it is a second
/// wall; its tests call it directly so each rule keeps a knife that bites.
pub fn resolve_in(root: &Path, file: &str) -> Result<PathBuf, DocErr> {
    let rel = Path::new(file);
    if file.is_empty() || rel.is_absolute() || rel.has_root() {
        return Err(DocErr::Refused);
    }
    if secret_name(file) {
        return Err(DocErr::Refused);
    }
    let base = root.canonicalize().map_err(|_| DocErr::NotFound)?;
    let real = match base.join(rel).canonicalize() {
        Ok(p) => p,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(DocErr::NotFound),
        Err(_) => return Err(DocErr::Refused),
    };
    // compared by components: `/pending-x` is not under `/pending`
    if !real.starts_with(&base) || real == base {
        return Err(DocErr::Refused);
    }
    // every piece under the folder, not only the last (5d33a PREREAD-import-doclink N2): a
    // plain name linked into a secret-named folder is refused too
    // (staying inside the folder is the starts_with check above; this only names the pieces)
    let under = real.strip_prefix(&base).unwrap_or(&real);
    if under
        .components()
        .any(|c| secret_name(&c.as_os_str().to_string_lossy()))
    {
        return Err(DocErr::Refused);
    }
    let meta = std::fs::metadata(&real).map_err(|_| DocErr::NotFound)?;
    if !meta.is_file() {
        return Err(DocErr::Refused);
    }
    if meta.len() > DOC_MAX {
        return Err(DocErr::TooLarge);
    }
    Ok(real)
}

/// The `n`th document a post registered: `doc` is one `{root?, file, title}` or a list of
/// them; `root` is a [`DOC_ROOTS`] code (`pending` when absent). Gives (root, file, title).
pub fn doc_of(item: &serde_json::Map<String, Value>, n: usize) -> Option<(String, String, String)> {
    let d = item.get("doc")?;
    let one = match d {
        Value::Array(a) => a.get(n)?,
        Value::Object(_) if n == 0 => d,
        _ => return None,
    };
    let root = match one.get("root") {
        None | Some(Value::Null) => "pending".to_string(),
        Some(r) => r.as_str()?.to_string(),
    };
    let file = one.get("file")?.as_str()?.to_string();
    let title = one
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    Some((root, file, title))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Whether `file` is what an open ledger card points at (`open.doc`, [`crate::ledger_cards`]):
/// only those are read by name, so `?file=` reaches no other document in the folder.
pub fn ledger_card_file(file: &str, now: i64) -> bool {
    crate::ledger_cards::cards(now).iter().any(|c| {
        c.open
            .as_ref()
            .and_then(|o| o.get("doc"))
            .and_then(Value::as_str)
            == Some(file)
    })
}

/// `GET /api/v1/doc?thread_id=&message_id=&n=`, or `?file=<name>` for the file an open ledger
/// card opens (043dd 11-19 06:41; the same listing, path and masking checks).
pub async fn read(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let Caller::Page(page) = c else {
        return refuse(
            StatusCode::FORBIDDEN,
            "doc_built_in_page_only",
            "a document is read by the built-in page",
        );
    };
    if crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "doc_built_in_page_only",
            "a document is read by the built-in page",
        );
    }
    if q.keys()
        .any(|k| !matches!(k.as_str(), "thread_id" | "message_id" | "n" | "file"))
    {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "the query takes thread_id, message_id and n, or file alone",
        );
    }
    let gone = || refuse(StatusCode::NOT_FOUND, "doc_not_found", "no such document");
    let (base, file, title, message_id) = if let Some(f) = q.get("file") {
        if q.len() != 1 {
            return refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "file is given alone",
            );
        }
        let (f2, now) = (f.clone(), now_secs());
        let open = tokio::task::spawn_blocking(move || ledger_card_file(&f2, now))
            .await
            .unwrap_or(false);
        if !open {
            return gone();
        }
        let Some(base) = doc_root("pending") else {
            return gone();
        };
        (base, f.clone(), String::new(), None)
    } else {
        match by_post(&st, &page, &q).await {
            Ok(x) => x,
            Err(r) => return *r,
        }
    };
    read_doc(&st, &page, base, file, title, message_id).await
}

fn now_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
}

/// (root, file, title, message id) of the `n`th document a post the reader may see registered.
async fn by_post(
    st: &AppState,
    page: &crate::pairing::PageAccess,
    q: &HashMap<String, String>,
) -> Result<(PathBuf, String, String, Option<String>), Box<Response>> {
    let (Some(thread_id), Some(message_id)) = (q.get("thread_id"), q.get("message_id")) else {
        return Err(Box::new(refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "thread_id and message_id are needed",
        )));
    };
    let n = match q.get("n").map(|v| v.parse::<usize>()) {
        None => 0,
        Some(Ok(n)) if n < 16 => n,
        Some(_) => {
            return Err(Box::new(refuse(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "n is 0–15",
            )))
        }
    };
    let s = crate::threads::page_sender(page).await;
    // the reader of the thread only (5d33a D1): the same check reading the thread makes
    let (t, _) = crate::threads::load(st, &s, thread_id).await?;
    let (db, tid, mid) = (st.messages.clone(), t.thread_id.clone(), message_id.clone());
    let got = crate::threads::blocking(move || {
        Ok(db
            .get(&mid)?
            .filter(|p| p.thread_id == tid && p.to_key == tid))
    })
    .await
    .flatten();
    let gone = || {
        Box::new(refuse(
            StatusCode::NOT_FOUND,
            "doc_not_found",
            "no such document",
        ))
    };
    let post = got.ok_or_else(gone)?;
    let (code, file, title) = doc_of(&post.item, n).ok_or_else(gone)?;
    // an unknown root code is refused, never read as a path
    let Some(base) = doc_root(&code) else {
        return Err(Box::new(refuse(
            StatusCode::FORBIDDEN,
            "doc_refused",
            "this document is not given to the page",
        )));
    };
    Ok((base, file, title, Some(post.message_id)))
}

/// Read, log and give one document (masked when its kind carries mail).
async fn read_doc(
    st: &AppState,
    page: &crate::pairing::PageAccess,
    base: PathBuf,
    file: String,
    title: String,
    message_id: Option<String>,
) -> Response {
    let gone = || refuse(StatusCode::NOT_FOUND, "doc_not_found", "no such document");
    let read = tokio::task::spawn_blocking(move || {
        let p = resolve(&base, &file)?;
        let bytes = std::fs::read(&p).map_err(|_| DocErr::NotFound)?;
        if bytes.len() as u64 > DOC_MAX {
            return Err(DocErr::TooLarge);
        }
        Ok((file, bytes))
    })
    .await;
    let (file, bytes) = match read {
        Ok(Ok(x)) => x,
        Ok(Err(DocErr::TooLarge)) => {
            return refuse(
                StatusCode::PAYLOAD_TOO_LARGE,
                "doc_too_large",
                "the document is larger than aibd gives",
            )
        }
        Ok(Err(DocErr::Refused)) => {
            return refuse(
                StatusCode::FORBIDDEN,
                "doc_refused",
                "this document is not given to the page",
            )
        }
        Ok(Err(DocErr::NotListed)) => {
            return refuse(
                StatusCode::FORBIDDEN,
                "doc_kind_not_listed",
                "this kind of document is not given to the page",
            )
        }
        Ok(Err(DocErr::NotFound)) | Err(_) => return gone(),
    };
    let sha = hex(&Sha256::digest(&bytes));
    let ev = crate::sessions::new_id("ev_").ok().map(|id| NewEvent {
        event_id: id.clone(),
        aggregate_type: "doc".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: "doc.read".into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload: json!({"message_id": message_id, "doc": file, "title_len": title.chars().count(),
                        "reader": hex(&page.digest[..8]), "len": bytes.len(), "sha256": sha}),
    });
    let store = st.store.clone();
    let _ = tokio::task::spawn_blocking(move || ev.map(|ev| store.append(vec![ev]))).await;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let text = if masked_kind(&file) {
        mask_mail(&text)
    } else {
        text
    };
    let mut r = text.into_response();
    let h = r.headers_mut();
    h.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    h.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    r
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn tree() -> (tempfile::TempDir, PathBuf) {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("pending");
        std::fs::create_dir_all(root.join("sub")).unwrap();
        std::fs::create_dir_all(root.join("SUMMARY-dir")).unwrap();
        std::fs::write(root.join("SUMMARY-a.md"), "總結").unwrap();
        std::fs::write(root.join("sub").join("HANDOFF-b.md"), "交接").unwrap();
        std::fs::write(root.join("SUMMARY-roster-x.md"), "名單").unwrap();
        std::fs::write(root.join("LIST-pii-sites.md"), "客戶").unwrap();
        std::fs::create_dir_all(root.join("HANDOFF-sub")).unwrap();
        std::fs::write(root.join("HANDOFF-sub").join("HANDOFF-c.md"), "子夾").unwrap();
        std::fs::write(d.path().join("outside.md"), "外面").unwrap();
        std::fs::create_dir_all(d.path().join("pending-x")).unwrap();
        std::fs::write(d.path().join("pending-x").join("y.md"), "旁邊").unwrap();
        (d, root)
    }

    #[test]
    fn a_file_by_name_is_only_an_open_ledger_cards() {
        // no legacy cabin set in tests: no ledger cards, so no name is read
        assert!(!ledger_card_file(
            "REVIEW-touchid-7b81e-5d33a-8ec571dc.md",
            0
        ));
        assert!(!ledger_card_file("../outside.md", 0));
    }

    #[test]
    fn a_registered_file_in_the_folder_is_read() {
        let (_d, root) = tree();
        assert!(resolve(&root, "SUMMARY-a.md").is_ok());
        assert_eq!(resolve(&root, "SUMMARY-nope.md"), Err(DocErr::NotFound));
    }

    #[test]
    fn nothing_outside_the_folder_or_of_a_secret_kind_is_read() {
        let (d, root) = tree();
        assert_eq!(
            resolve_in(&root, "../outside.md"),
            Err(DocErr::Refused),
            "reason: dotdot"
        );
        assert_eq!(
            resolve_in(&root, "../pending-x/y.md"),
            Err(DocErr::Refused),
            "reason: prefix_twin"
        );
        let abs = d.path().join("outside.md");
        assert_eq!(
            resolve_in(&root, abs.to_str().unwrap()),
            Err(DocErr::Refused),
            "reason: absolute"
        );
        assert_eq!(
            resolve_in(&root, "SUMMARY-roster-x.md"),
            Err(DocErr::Refused),
            "reason: secret_kind"
        );
        assert_eq!(
            resolve_in(&root, "SUMMARY-dir"),
            Err(DocErr::Refused),
            "reason: directory"
        );
        assert_eq!(resolve_in(&root, ""), Err(DocErr::Refused));
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(d.path().join("outside.md"), root.join("SUMMARY-link.md"))
                .unwrap();
            assert_eq!(
                resolve_in(&root, "SUMMARY-link.md"),
                Err(DocErr::Refused),
                "reason: symlink_out"
            );
            std::os::unix::fs::symlink(root.join("SUMMARY-roster-x.md"), root.join("SUMMARY-p.md"))
                .unwrap();
            assert_eq!(
                resolve_in(&root, "SUMMARY-p.md"),
                Err(DocErr::Refused),
                "reason: symlink_to_secret"
            );
            std::fs::create_dir_all(root.join("tokens")).unwrap();
            std::fs::write(root.join("tokens").join("a.md"), "x").unwrap();
            std::os::unix::fs::symlink(root.join("tokens").join("a.md"), root.join("SUMMARY-b.md"))
                .unwrap();
            assert_eq!(
                resolve_in(&root, "SUMMARY-b.md"),
                Err(DocErr::Refused),
                "reason: link_into_secret_folder"
            );
        }
    }

    #[test]
    fn a_file_over_the_limit_is_too_large() {
        let (_d, root) = tree();
        std::fs::write(
            root.join("SUMMARY-big.md"),
            vec![b'a'; (DOC_MAX + 1) as usize],
        )
        .unwrap();
        assert_eq!(resolve(&root, "SUMMARY-big.md"), Err(DocErr::TooLarge));
    }

    /// 5d33a CHECK-docroot 條件 1–4 (knives DR1–DR4).
    #[test]
    fn only_listed_kinds_in_the_folder_itself_are_given() {
        let (_d, root) = tree();
        assert_eq!(
            resolve(&root, "LIST-pii-sites.md"),
            Err(DocErr::NotListed),
            "reason: unlisted_kind"
        );
        assert_eq!(
            resolve(&root, "HANDOFF-sub/HANDOFF-c.md"),
            Err(DocErr::NotListed),
            "reason: sub_folder"
        );
        assert_eq!(resolve(&root, "summary-a.md"), Err(DocErr::NotListed));
        assert_eq!(DOC_KINDS.len(), 17);
    }

    #[cfg(unix)]
    #[test]
    fn a_link_inside_the_folder_is_not_followed() {
        let (_d, root) = tree();
        std::os::unix::fs::symlink(root.join("SUMMARY-a.md"), root.join("SUMMARY-same.md"))
            .unwrap();
        assert_eq!(
            resolve(&root, "SUMMARY-same.md"),
            Err(DocErr::Refused),
            "reason: link_followed"
        );
    }

    #[test]
    fn mail_addresses_in_a_relay_or_receipt_are_masked() {
        assert!(masked_kind("RELAY-dm-x.txt") && masked_kind("RECEIPT-x.out"));
        assert!(!masked_kind("SUMMARY-x.md"));
    }
    #[test]
    fn the_post_names_its_documents_by_index() {
        let one: serde_json::Map<String, Value> =
            serde_json::from_value(json!({"doc": {"file": "A.md", "title": "甲"}})).unwrap();
        assert_eq!(
            doc_of(&one, 0),
            Some(("pending".into(), "A.md".into(), "甲".into()))
        );
        assert_eq!(doc_of(&one, 1), None);
        let two: serde_json::Map<String, Value> = serde_json::from_value(
            json!({"doc": [{"file": "A.md"}, {"file": "B.md", "title": "乙"}]}),
        )
        .unwrap();
        assert_eq!(
            doc_of(&two, 1),
            Some(("pending".into(), "B.md".into(), "乙".into()))
        );
        let rooted: serde_json::Map<String, Value> = serde_json::from_value(
            json!({"doc": {"root": "pending", "file": "C.md", "title": "丙"}}),
        )
        .unwrap();
        assert_eq!(doc_of(&rooted, 0).unwrap().0, "pending");
        assert!(doc_root("/etc").is_none(), "reason: root_code_is_a_path");
        assert!(doc_root("pending").is_some());
        assert_eq!(doc_of(&serde_json::Map::new(), 0), None);
    }

    #[test]
    fn mail_addresses_are_masked_and_seat_calls_are_not() {
        assert_eq!(
            super::mask_mail("寄 ab.c+1@ex-ample.com.tw。@a9136 看"),
            "寄 〈信箱 長度 22〉。@a9136 看"
        );
        assert_eq!(
            super::mask_mail("x@y 與 a@b.c 與 @5d33a"),
            "x@y 與 a@b.c 與 @5d33a"
        );
        assert_eq!(
            super::mask_mail("寫給 me@host.io."),
            "寫給 〈信箱 長度 10〉."
        );
    }
}
