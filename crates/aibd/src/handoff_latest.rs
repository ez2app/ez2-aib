//! S6 (DESIGN-s6-handofflatest-08de5-1123; ebab8 11-23 02:39): the built-in page asks which
//! handoff note and memory pack of a seat code are the newest, so 「接回」 brings them along.
//!
//! - `handoff_latest {code}` from a confirmed built-in page (a user folder page is refused by
//!   the caller; a seat's token never reaches it). Read only.
//! - The folder is [`crate::docs::root`] (the checked `AIBD_PLAN_PENDING`, else the person's
//!   pending folder); its first level only; the code's own names picked by name first, then plain files
//!   only (a link or a folder is not listed: `DirEntry::metadata` does not follow links), no
//!   secret name; more than [`NAMES_MAX`] of the code's own is `pending_too_big`, never an
//!   older one. The newest is by modified time: anyone who can write the folder can touch a
//!   file newer (same-user threat, not guarded; the page shows the name and time to the person).
//! - The code is exactly five lowercase hex ([`crate::seat_plan_move::code_ok`]); the newest
//!   is picked by [`crate::seat_plan_move::newest`] (`HANDOFF-a91360-…` is not a9136's).
//! - The answer is each file's name and modified time, nothing else: the page reads the text
//!   through `GET /api/v1/doc`, which has its own list and masking.

use crate::api::AppState;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use std::path::Path;
use std::time::SystemTime;

pub const KIND: &str = "handoff_latest";

/// The most of one code's handoffs and memory packs looked at (f163c r0 M1: the cap counts the
/// code's own names only, never the folder; this person's pending folder holds 32,392 files).
pub const NAMES_MAX: usize = 5000;

/// More than [`NAMES_MAX`] of the code's own names: refused, never an older one answered.
#[derive(Debug, PartialEq, Eq)]
pub struct TooMany;

/// Whether `name` is one of `code`'s (`HANDOFF-<code>-…` / `MEMPACK-<code>-…`), by its name only.
fn of_code(name: &str, code: &str) -> bool {
    [crate::seat_carry::HANDOFF, crate::seat_carry::MEMPACK]
        .iter()
        .any(|p| {
            name.strip_prefix(p)
                .and_then(|r| r.strip_prefix(code))
                .is_some_and(|r| r.starts_with('-'))
        })
}

/// `code`'s plain files in `root`'s first level, with their modified times: picked by name
/// first, then no link, no folder, no secret name; more than [`NAMES_MAX`] of them is
/// [`TooMany`].
pub fn names(root: &Path, code: &str) -> Result<Vec<(String, SystemTime)>, TooMany> {
    let Ok(rd) = std::fs::read_dir(root) else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for e in rd.flatten() {
        let Some(n) = e.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        if !of_code(&n, code) || crate::docs::secret_name(&n) {
            continue;
        }
        // DirEntry::metadata does not follow a link: a link is not a plain file
        let Ok(m) = e.metadata() else { continue };
        if !m.file_type().is_file() {
            continue;
        }
        let Ok(t) = m.modified() else { continue };
        if out.len() == NAMES_MAX {
            return Err(TooMany);
        }
        out.push((n, t));
    }
    Ok(out)
}

fn secs(t: SystemTime) -> u64 {
    t.duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

/// The answer for `code` from `names`: the newest handoff and memory pack, each its name and
/// modified time, or `null`.
pub fn answer(names: &[(String, SystemTime)], code: &str) -> Value {
    let one = |prefix: &str| -> Value {
        crate::seat_plan_move::newest(names, prefix, code)
            .and_then(|file| {
                let t = names.iter().find(|(n, _)| *n == file)?.1;
                Some(json!({"file": file, "mtime": secs(t)}))
            })
            .unwrap_or(Value::Null)
    };
    json!({
        "code": code,
        "handoff": one(crate::seat_carry::HANDOFF),
        "mempack": one(crate::seat_carry::MEMPACK),
    })
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error": {"code": code, "message": message}})),
    )
        .into_response()
}

/// The code a body asks for: only `type` and `code`, the code five lowercase hex.
pub fn parse(body: &Value) -> Option<String> {
    let fields_ok = body
        .as_object()
        .is_some_and(|o| o.keys().all(|k| k == "type" || k == "code"));
    let code = body
        .get("code")
        .and_then(Value::as_str)
        .filter(|c| crate::seat_plan_move::code_ok(c))
        .map(str::to_owned)?;
    fields_ok.then_some(code)
}

/// `handoff_latest {code}` from the built-in page.
pub async fn act(_st: AppState, body: Value) -> Response {
    let Some(code) = parse(&body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "code_shape",
            "handoff_latest takes code: five lowercase hex",
        );
    };
    let Some(root) = crate::docs::root() else {
        return refuse(
            StatusCode::NOT_FOUND,
            "pending_not_found",
            "no pending folder",
        );
    };
    match tokio::task::spawn_blocking(move || names(&root, &code).map(|n| answer(&n, &code))).await
    {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(TooMany)) => refuse(
            StatusCode::CONFLICT,
            "pending_too_big",
            "這一席的交接單太多,先整理交接夾再接回",
        ),
        Err(_) => refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "list failed"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn t(s: u64) -> SystemTime {
        std::time::UNIX_EPOCH + Duration::from_secs(1_795_000_000 + s)
    }

    /// S6-1: the code is exactly five lowercase hex.
    #[test]
    fn only_a_five_hex_code_is_taken() {
        // the body: only type and code, the code five lowercase hex
        assert_eq!(
            parse(&json!({"type": KIND, "code": "a9136"})).as_deref(),
            Some("a9136")
        );
        for bad in [
            json!({"type": KIND, "code": "a91360"}),
            json!({"type": KIND, "code": "../a9"}),
            json!({"type": KIND, "code": 9136}),
            json!({"type": KIND}),
            json!({"type": KIND, "code": "a9136", "path": "/etc"}),
        ] {
            assert_eq!(parse(&bad), None, "reason: body_taken {bad}");
        }
        for bad in ["a91360", "A9136", "a913", "../a9", "a9136/", "", "g9136"] {
            assert!(
                !crate::seat_plan_move::code_ok(bad),
                "reason: code_taken {bad:?}"
            );
        }
        assert!(crate::seat_plan_move::code_ok("a9136"));
    }

    /// S6-2: the newest of the seat's own; another code that starts the same is not its; no
    /// memory pack is null.
    #[test]
    fn the_newest_of_the_seats_own_is_given() {
        let n = vec![
            ("HANDOFF-a9136-1120-2237.md".to_owned(), t(10)),
            ("HANDOFF-a9136-1121-0910.md".to_owned(), t(20)),
            ("HANDOFF-a91360-1121-0910.md".to_owned(), t(30)),
            ("HANDOFF-6f1cb-1121-0001.md".to_owned(), t(40)),
        ];
        let v = answer(&n, "a9136");
        assert_eq!(v["handoff"]["file"], "HANDOFF-a9136-1121-0910.md");
        assert_eq!(v["handoff"]["mtime"], secs(t(20)));
        assert_eq!(v["mempack"], Value::Null, "reason: mempack_made_up");
        let n2 = vec![
            ("MEMPACK-a9136-1121.md".to_owned(), t(5)),
            ("MEMPACK-a9136-1122.md".to_owned(), t(6)),
        ];
        assert_eq!(
            answer(&n2, "a9136")["mempack"]["file"],
            "MEMPACK-a9136-1122.md"
        );
        assert_eq!(answer(&n2, "a9136")["handoff"], Value::Null);
    }

    /// S6-3: a link (or a folder) in the folder is not listed, so the older plain file is the
    /// newest given.
    #[cfg(unix)]
    #[test]
    fn a_link_is_not_listed() {
        let d = tempfile::tempdir().unwrap();
        let away = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("HANDOFF-a9136-1121-0910.md"), "old").unwrap();
        std::fs::write(away.path().join("x.md"), "elsewhere").unwrap();
        std::os::unix::fs::symlink(
            away.path().join("x.md"),
            d.path().join("HANDOFF-a9136-9999-9999.md"),
        )
        .unwrap();
        std::fs::create_dir(d.path().join("HANDOFF-a9136-9999-9998.md")).unwrap();
        // a secret name is not listed either
        std::fs::write(d.path().join("HANDOFF-a9136-9999-token.md"), "x").unwrap();
        let n = names(d.path(), "a9136").unwrap();
        assert!(
            n.iter().all(|(f, _)| !f.contains("9999")),
            "reason: link_or_folder_listed {n:?}"
        );
        assert_eq!(
            answer(&n, "a9136")["handoff"]["file"],
            "HANDOFF-a9136-1121-0910.md"
        );
    }

    /// S6-5 (f163c r0 M1): a folder far bigger than the cap still gives the newest of the
    /// code's own: 6000 unrelated files and the one handoff; more than the cap of the code's
    /// own is refused, not answered with an older one.
    #[test]
    fn a_big_folder_still_gives_the_newest() {
        let d = tempfile::tempdir().unwrap();
        for i in 0..6000 {
            std::fs::write(d.path().join(format!("RECEIPT-x-{i}.out")), "").unwrap();
        }
        std::fs::write(d.path().join("HANDOFF-a9136-1123-0300.md"), "new").unwrap();
        std::fs::write(d.path().join("HANDOFF-6f1cb-1123-0300.md"), "other").unwrap();
        let n = names(d.path(), "a9136").expect("reason: refused_a_big_folder");
        assert_eq!(n.len(), 1, "reason: others_listed {}", n.len());
        assert_eq!(
            answer(&n, "a9136")["handoff"]["file"],
            "HANDOFF-a9136-1123-0300.md"
        );
        let e = tempfile::tempdir().unwrap();
        for i in 0..=NAMES_MAX {
            std::fs::write(e.path().join(format!("HANDOFF-a9136-{i}.md")), "").unwrap();
        }
        assert_eq!(
            names(e.path(), "a9136"),
            Err(TooMany),
            "reason: over_cap_answered"
        );
    }

    /// S6-4: the answer is the code and two names with their times, nothing else.
    #[test]
    fn only_names_and_times_are_given() {
        let n = vec![
            ("HANDOFF-a9136-1121-0910.md".to_owned(), t(20)),
            ("MEMPACK-a9136-1121.md".to_owned(), t(21)),
        ];
        let v = answer(&n, "a9136");
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(keys, ["code", "handoff", "mempack"], "reason: answer_grew");
        for k in ["handoff", "mempack"] {
            let mut inner: Vec<&str> = v[k]
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect();
            inner.sort_unstable();
            assert_eq!(inner, ["file", "mtime"], "reason: {k}_grew");
        }
    }
}
