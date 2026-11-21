//! A web seat reads the pending folder, read-only, through aibd (PREREAD-seat-pending-read-a9136-
//! 1121; 519d0 11-21 11:21: the first seats to move read their mail, handoffs and reviews there).
//! The seat's own file tools never reach the folder; two talk tools ask aibd instead:
//!
//! - `pending_list {prefix?}`: names of one level of the folder, newest first, at most
//!   [`LIST_MAX`], each `{file, bytes, mtime}`; only [`SEAT_KINDS`], no link, no folder.
//! - `pending_read {file}`: one of them as text, by the page's own wall ([`crate::docs::resolve_in`]:
//!   inside the folder after `canonicalize`, no secret name, a plain file of at most
//!   [`crate::docs::DOC_MAX`]), not through a link; a RELAY- / RECEIPT- text has its e-mail
//!   addresses masked. Each read is an event with the name, length and hash, never the text.
use crate::api::AppState;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::path::Path;

pub const LIST: &str = "pending_list";
pub const READ: &str = "pending_read";
/// The most names one list gives.
pub const LIST_MAX: usize = 200;

/// The kinds a seat may read: the page's ([`crate::docs::DOC_KINDS`]) and four a moved seat
/// needs (its memory pack, findings, knife tables, council papers).
pub const SEAT_EXTRA: &[&str] = &["MEMPACK-", "FINDING-", "KNIVES-", "COUNCIL-"];

/// One piece (no folder) starting with a kind a seat may read.
pub fn seat_listed(file: &str) -> bool {
    !file.contains(['/', '\\'])
        && crate::docs::DOC_KINDS
            .iter()
            .chain(SEAT_EXTRA)
            .any(|k| file.starts_with(k))
}

pub fn is_seat_act(body: &Value) -> bool {
    matches!(body.get("type").and_then(Value::as_str), Some(LIST | READ))
}

/// `file` in `root` as text: listed, not a link, then the page's wall.
pub fn read(root: &Path, file: &str) -> Result<(String, Vec<u8>), &'static str> {
    if !seat_listed(file) {
        return Err("pending_kind_not_listed");
    }
    match std::fs::symlink_metadata(root.join(file)) {
        Ok(m) if m.file_type().is_symlink() => return Err("pending_refused"),
        Ok(_) => {}
        Err(_) => return Err("pending_not_found"),
    }
    let real = crate::docs::resolve_in(root, file).map_err(|e| match e {
        crate::docs::DocErr::NotFound => "pending_not_found",
        crate::docs::DocErr::TooLarge => "pending_too_large",
        _ => "pending_refused",
    })?;
    let bytes = std::fs::read(real).map_err(|_| "pending_not_found")?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    let text = if crate::docs::masked_kind(file) {
        crate::docs::mask_mail(&text)
    } else {
        text
    };
    Ok((text, bytes))
}

/// The names in `root` (one level) a seat may read, starting with `prefix`, newest first.
pub fn list(root: &Path, prefix: &str) -> Vec<Value> {
    let Ok(rd) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut rows: Vec<(String, u64, u64)> = rd
        .filter_map(|e| {
            let e = e.ok()?;
            let name = e.file_name().to_str()?.to_owned();
            if !name.starts_with(prefix) || !seat_listed(&name) || crate::docs::secret_name(&name) {
                return None;
            }
            // DirEntry::metadata does not follow a link: a link is not listed
            let m = e.metadata().ok()?;
            if !m.file_type().is_file() {
                return None;
            }
            let t = m
                .modified()
                .ok()?
                .duration_since(std::time::UNIX_EPOCH)
                .ok()?
                .as_secs();
            Some((name, m.len(), t))
        })
        .collect();
    rows.sort_by(|a, b| b.2.cmp(&a.2).then_with(|| a.0.cmp(&b.0)));
    rows.truncate(LIST_MAX);
    rows.into_iter()
        .map(|(file, bytes, mtime)| json!({"file": file, "bytes": bytes, "mtime": mtime}))
        .collect()
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"code": code, "message": message, "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

/// The act, for the seat a token names.
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    let fields_ok = body.as_object().is_some_and(|o| {
        o.keys().all(|k| {
            k == "type" || (kind == LIST && k == "prefix") || (kind == READ && k == "file")
        })
    });
    if !fields_ok {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "pending_list takes prefix, pending_read takes file",
        );
    }
    let Some(root) = crate::docs::root() else {
        return refuse(
            StatusCode::NOT_FOUND,
            "pending_not_found",
            "no pending folder",
        );
    };
    if kind == LIST {
        let prefix = body
            .get("prefix")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let files = tokio::task::spawn_blocking(move || list(&root, &prefix))
            .await
            .unwrap_or_default();
        return Json(json!({"files": files})).into_response();
    }
    let Some(file) = body.get("file").and_then(Value::as_str).map(str::to_owned) else {
        return refuse(StatusCode::BAD_REQUEST, "invalid_request", "file is needed");
    };
    let f = file.clone();
    let got = tokio::task::spawn_blocking(move || read(&root, &f)).await;
    let (text, bytes) = match got {
        Ok(Ok(x)) => x,
        Ok(Err(code)) => {
            let status = if code == "pending_not_found" {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::FORBIDDEN
            };
            return refuse(status, code, "this file is not given to a seat");
        }
        Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "read failed"),
    };
    use sha2::Digest;
    let sha: String = sha2::Sha256::digest(&bytes)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect();
    let ev = crate::sessions::new_id("ev_")
        .ok()
        .map(|id| aib_store::NewEvent {
            event_id: id.clone(),
            aggregate_type: "doc".into(),
            aggregate_id: id,
            sequence: 1,
            event_type: "seat.pending_read".into(),
            correlation_id: None,
            occurred_at: crate::sessions::now_rfc3339(),
            payload: json!({"seat_id": seat_id, "file": file, "len": bytes.len(), "sha256": sha}),
        });
    let store = st.store.clone();
    let _ = tokio::task::spawn_blocking(move || ev.map(|ev| store.append(vec![ev]))).await;
    Json(json!({"file": file, "text": text})).into_response()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// PR1 / PR3: a listed kind only, the secret kinds refused, mail masked in a RELAY-.
    #[test]
    fn a_seat_reads_a_listed_kind_only_with_mail_masked() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        std::fs::write(r.join("MEMPACK-a9136-1121.md"), "記憶").unwrap();
        std::fs::write(r.join("RELAY-dm-x.txt"), "寄 someone@example.com").unwrap();
        std::fs::write(r.join("notes.txt"), "n").unwrap();
        std::fs::write(r.join("HANDOFF-secret-key.md"), "k").unwrap();
        assert_eq!(read(r, "MEMPACK-a9136-1121.md").unwrap().0, "記憶");
        assert!(
            !read(r, "RELAY-dm-x.txt")
                .unwrap()
                .0
                .contains("someone@example.com"),
            "reason: mail_not_masked"
        );
        assert_eq!(read(r, "notes.txt").err(), Some("pending_kind_not_listed"));
        assert_eq!(
            read(r, "HANDOFF-secret-key.md").err(),
            Some("pending_refused"),
            "reason: secret_read"
        );
        for bad in ["../HANDOFF-a.md", "sub/HANDOFF-a.md", "HANDOFF-a.md/.."] {
            assert!(read(r, bad).is_err(), "reason: path_taken {bad}");
        }
    }

    /// PR2: a link is not read, even to a listed name in the folder.
    #[cfg(unix)]
    #[test]
    fn a_link_in_the_pending_folder_is_not_read() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path().join("pending");
        std::fs::create_dir_all(&r).unwrap();
        std::fs::write(r.join("HANDOFF-real.md"), "h").unwrap();
        std::os::unix::fs::symlink(r.join("HANDOFF-real.md"), r.join("HANDOFF-link.md")).unwrap();
        assert_eq!(
            read(&r, "HANDOFF-link.md").err(),
            Some("pending_refused"),
            "reason: link_read"
        );
        let names: Vec<String> = list(&r, "")
            .iter()
            .map(|v| v["file"].as_str().unwrap().to_owned())
            .collect();
        assert_eq!(names, ["HANDOFF-real.md"], "reason: link_listed");
    }

    /// PR4 / PR5: the list is the listed kinds of one level, by prefix, at most LIST_MAX.
    #[test]
    fn the_list_gives_listed_names_by_prefix_and_no_more_than_the_cap() {
        let d = tempfile::tempdir().unwrap();
        let r = d.path();
        std::fs::write(r.join("notes.txt"), "n").unwrap();
        std::fs::write(r.join("SUMMARY-token-x.md"), "s").unwrap();
        std::fs::create_dir(r.join("HANDOFF-dir.md")).unwrap();
        for i in 0..(LIST_MAX + 5) {
            std::fs::write(r.join(format!("RELAY-{i:04}.txt")), "x").unwrap();
        }
        std::fs::write(r.join("HANDOFF-a.md"), "h").unwrap();
        let all = list(r, "");
        assert_eq!(all.len(), LIST_MAX, "reason: no_cap");
        let names: Vec<&str> = all.iter().filter_map(|v| v["file"].as_str()).collect();
        assert!(
            names.iter().all(|n| seat_listed(n) && !n.contains("token")),
            "reason: unlisted_named {names:?}"
        );
        assert!(list(r, "notes").is_empty(), "reason: unlisted_named");
        let h = list(r, "HANDOFF-");
        assert_eq!(h.len(), 1, "reason: prefix_ignored");
        assert_eq!(h[0]["file"], "HANDOFF-a.md");
        assert_eq!(h[0]["bytes"], 1);
    }
}
