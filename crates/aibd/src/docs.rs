//! N-ws-doc-link (SPEC-workspace-multiseat §17 point 4; 5d33a CHECKLIST-doc-link D1–D4):
//! `GET /api/v1/doc?thread_id=&message_id=&n=` gives the document a summary / hand-over post
//! names, as plain text.
//!
//! - Who: a paired page that is not a user folder page (a carried pairing may read); the
//!   reader must read the thread too (the same check as reading it). A seat token never
//!   reaches it (the guard), the start-up token and a user folder page get 403.
//! - Which file: only the post's own `doc` entry (`{file, title}`, or a list of them; set by
//!   aibd / the import, never by `thread_send`). The request names no path. The file must sit
//!   in the pending folder after `canonicalize` (symbolic links followed, compared by path
//!   components), be a regular file of at most [`DOC_MAX`] bytes, and not be a secret kind.
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

/// The folder documents are read from: `AIBD_PLAN_PENDING` or `~/.ez2-aib/pending`.
pub fn root() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("AIBD_PLAN_PENDING") {
        return Some(PathBuf::from(p));
    }
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(|h| PathBuf::from(h).join(".ez2-aib").join("pending"))
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

/// `GET /api/v1/doc?thread_id=&message_id=&n=`.
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
        .any(|k| !matches!(k.as_str(), "thread_id" | "message_id" | "n"))
    {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "the query takes thread_id, message_id and n only",
        );
    }
    let (Some(thread_id), Some(message_id)) = (q.get("thread_id"), q.get("message_id")) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "thread_id and message_id are needed",
        );
    };
    let n = match q.get("n").map(|v| v.parse::<usize>()) {
        None => 0,
        Some(Ok(n)) if n < 16 => n,
        Some(_) => return refuse(StatusCode::BAD_REQUEST, "invalid_request", "n is 0–15"),
    };
    let s = crate::threads::page_sender(&page).await;
    // the reader of the thread only (5d33a D1): the same check reading the thread makes
    let (t, _) = match crate::threads::load(&st, &s, thread_id).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    let (db, tid, mid) = (st.messages.clone(), t.thread_id.clone(), message_id.clone());
    let got = crate::threads::blocking(move || {
        Ok(db
            .get(&mid)?
            .filter(|p| p.thread_id == tid && p.to_key == tid))
    })
    .await
    .flatten();
    let gone = || refuse(StatusCode::NOT_FOUND, "doc_not_found", "no such document");
    let Some(post) = got else {
        return gone();
    };
    let Some((code, file, title)) = doc_of(&post.item, n) else {
        return gone();
    };
    // an unknown root code is refused, never read as a path
    let Some(base) = doc_root(&code) else {
        return refuse(
            StatusCode::FORBIDDEN,
            "doc_refused",
            "this document is not given to the page",
        );
    };
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
        payload: json!({"message_id": post.message_id, "doc": file, "title_len": title.chars().count(),
                        "reader": hex(&page.digest[..8]), "len": bytes.len(), "sha256": sha}),
    });
    let store = st.store.clone();
    let _ = tokio::task::spawn_blocking(move || ev.map(|ev| store.append(vec![ev]))).await;
    let text = String::from_utf8_lossy(&bytes).into_owned();
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
        std::fs::write(root.join("SUMMARY-a.md"), "總結").unwrap();
        std::fs::write(root.join("sub").join("HANDOFF-b.md"), "交接").unwrap();
        std::fs::write(root.join("roster-x.md"), "名單").unwrap();
        std::fs::write(d.path().join("outside.md"), "外面").unwrap();
        std::fs::create_dir_all(d.path().join("pending-x")).unwrap();
        std::fs::write(d.path().join("pending-x").join("y.md"), "旁邊").unwrap();
        (d, root)
    }

    #[test]
    fn a_registered_file_in_the_folder_is_read() {
        let (_d, root) = tree();
        assert!(resolve(&root, "SUMMARY-a.md").is_ok());
        assert!(resolve(&root, "sub/HANDOFF-b.md").is_ok());
        assert_eq!(resolve(&root, "nope.md"), Err(DocErr::NotFound));
    }

    #[test]
    fn nothing_outside_the_folder_or_of_a_secret_kind_is_read() {
        let (d, root) = tree();
        assert_eq!(
            resolve(&root, "../outside.md"),
            Err(DocErr::Refused),
            "reason: dotdot"
        );
        assert_eq!(
            resolve(&root, "../pending-x/y.md"),
            Err(DocErr::Refused),
            "reason: prefix_twin"
        );
        let abs = d.path().join("outside.md");
        assert_eq!(
            resolve(&root, abs.to_str().unwrap()),
            Err(DocErr::Refused),
            "reason: absolute"
        );
        assert_eq!(
            resolve(&root, "roster-x.md"),
            Err(DocErr::Refused),
            "reason: secret_kind"
        );
        assert_eq!(
            resolve(&root, "sub"),
            Err(DocErr::Refused),
            "reason: directory"
        );
        assert_eq!(resolve(&root, ""), Err(DocErr::Refused));
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(d.path().join("outside.md"), root.join("link.md")).unwrap();
            assert_eq!(
                resolve(&root, "link.md"),
                Err(DocErr::Refused),
                "reason: symlink_out"
            );
            std::os::unix::fs::symlink(root.join("roster-x.md"), root.join("plain.md")).unwrap();
            assert_eq!(
                resolve(&root, "plain.md"),
                Err(DocErr::Refused),
                "reason: symlink_to_secret"
            );
            std::fs::create_dir_all(root.join("tokens")).unwrap();
            std::fs::write(root.join("tokens").join("a.md"), "x").unwrap();
            std::os::unix::fs::symlink(root.join("tokens").join("a.md"), root.join("b.md"))
                .unwrap();
            assert_eq!(
                resolve(&root, "b.md"),
                Err(DocErr::Refused),
                "reason: link_into_secret_folder"
            );
        }
    }

    #[test]
    fn a_file_over_the_limit_is_too_large() {
        let (_d, root) = tree();
        std::fs::write(root.join("big.md"), vec![b'a'; (DOC_MAX + 1) as usize]).unwrap();
        assert_eq!(resolve(&root, "big.md"), Err(DocErr::TooLarge));
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
}
