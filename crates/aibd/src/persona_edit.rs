//! A1 (DESIGN-a1-edit-archive-08de5-1123 r0; f163c REVIEW-a1 r0 0/0/2/1; SPEC r2 §7.1): the
//! built-in page reads and changes a persona card's three sections, renames a brain or a role
//! the person added, and archives / restores any of the three. The caller (`seat_input::act`)
//! lets in the built-in page of this pairing only.
//!
//! - `persona_read {persona_uid}`: the card's name, `base_sha` and its three sections. The
//!   sections never go to a log or an error (f163c N1: 紅線 often holds internal rules).
//! - `persona_set {persona_uid, base_sha, duty?, redlines?, knowledge?, ticket?}`: the owner's
//!   fingerprint first (a card is what the partner is told), the window naming which sections
//!   change and their lengths; then the one write, `crate::persona_write::write`. A section a
//!   page did not send is left as it is. `name` is refused (`name_not_yet`): 1.x shows a card
//!   under its labels.json title, not its `name:`, so renaming a persona needs that file too.
//! - `brain_set {uid, name}` / `role_set {uid, name?, what?}`: aibd's own registry.json only;
//!   a built-in role is locked; a name another entry of that kind has (archived too) is
//!   refused `name_taken` (f163c L2).
//! - `<kind>_archive {uid}` / `<kind>_restore {uid}`: `aib_store::registry_archive` (6770f §8);
//!   no file is touched, so no fingerprint; a built-in role is not archived.

use crate::api::AppState;
use crate::persona_card as card;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

pub const PERSONA_READ: &str = "persona_read";
pub const PERSONA_SET: &str = "persona_set";
pub const BRAIN_SET: &str = "brain_set";
pub const ROLE_SET: &str = "role_set";
pub const PERSONA_ARCHIVE: &str = "persona_archive";
pub const BRAIN_ARCHIVE: &str = "brain_archive";
pub const ROLE_ARCHIVE: &str = "role_archive";
pub const PERSONA_RESTORE: &str = "persona_restore";
pub const BRAIN_RESTORE: &str = "brain_restore";
pub const ROLE_RESTORE: &str = "role_restore";
/// The fingerprint a card's change asks.
pub const OP_SET: &str = "persona.set";

/// Every act of this module, for `seat_input::PAGE_ACTS`.
pub const ACTS: [&str; 10] = [
    PERSONA_READ,
    PERSONA_SET,
    BRAIN_SET,
    ROLE_SET,
    PERSONA_ARCHIVE,
    BRAIN_ARCHIVE,
    ROLE_ARCHIVE,
    PERSONA_RESTORE,
    BRAIN_RESTORE,
    ROLE_RESTORE,
];

fn err(code: StatusCode, reason: &str, message: &str) -> Response {
    (
        code,
        Json(
            json!({"code": if code == StatusCode::CONFLICT { "conflict" } else { "bad_request" },
            "reason": reason, "message": message, "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

fn bad(reason: &str, message: &str) -> Response {
    err(StatusCode::BAD_REQUEST, reason, message)
}

/// Only these keys in a body; anything else is refused (a page cannot slip a field in).
fn only(body: &Value, keys: &[&str]) -> Result<(), Box<Response>> {
    let o = body
        .as_object()
        .ok_or_else(|| Box::new(bad("body", "the body is an object")))?;
    match o.keys().find(|k| !keys.contains(&k.as_str())) {
        Some(k) => Err(Box::new(bad("field", &format!("unknown field {k}")))),
        None => Ok(()),
    }
}

/// A uid as aibd's lists give it: 1-16 of `a-z 0-9 _` (the act and the window's words use
/// the same rule, f163c pre N1).
fn uid_ok(u: &str) -> bool {
    (1..=16).contains(&u.len())
        && u.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

fn uid_of(body: &Value, key: &str) -> Result<String, Box<Response>> {
    body.get(key)
        .and_then(Value::as_str)
        .filter(|u| uid_ok(u))
        .map(str::to_owned)
        .ok_or_else(|| Box::new(bad(key, "a uid from aibd's list")))
}

/// The card of `uid` and its text as the list names it.
async fn card_now(st: &AppState, uid: &str) -> Result<(String, String), Box<Response>> {
    let src = st.personas.clone();
    let u = uid.to_owned();
    let got = tokio::task::spawn_blocking(move || {
        let c = src.cards().ok()?.into_iter().find(|c| c.uid == u)?;
        let path = c.text_path.clone()?;
        let text = crate::personas::card_text(&path, &c.persona_sha).ok()?;
        Some((c.persona_sha, String::from_utf8(text).ok()?))
    })
    .await
    .ok()
    .flatten();
    got.ok_or_else(|| {
        Box::new(err(
            StatusCode::NOT_FOUND,
            "persona_unreadable",
            "no such persona, or its card cannot be read as the list names it",
        ))
    })
}

/// `persona_read`.
pub async fn read(st: AppState, body: Value) -> Response {
    if let Err(r) = only(&body, &["type", "persona_uid", "idem_key"]) {
        return *r;
    }
    let uid = match uid_of(&body, "persona_uid") {
        Ok(u) => u,
        Err(r) => return *r,
    };
    let (sha, text) = match card_now(&st, &uid).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    let mut out = json!({
        "persona_uid": uid,
        "base_sha": sha,
        "name": crate::persona_write::front_value(&text, "name"),
    });
    for (field, heading) in card::SECTIONS {
        out[field] = json!(card::section(&text, heading));
    }
    Json(out).into_response()
}

/// The sections a change touches: `(heading, characters before, characters after)`.
pub type Changed = Vec<(&'static str, usize, usize)>;

/// What `persona_set` changes: the new text and, per section sent, `(heading, before, after)`
/// lengths in characters (the window's words).
pub fn new_text(text: &str, body: &Value) -> Result<(String, Changed), card::EditError> {
    let mut t = text.to_owned();
    let mut changed = Vec::new();
    for (field, heading) in card::SECTIONS {
        let Some(v) = body.get(field).and_then(Value::as_str) else {
            continue;
        };
        let clean = card::clean_section(field, v)?;
        let before = card::section(&t, heading);
        if before != clean {
            changed.push((heading, before.chars().count(), clean.chars().count()));
            t = card::with_section(&t, heading, &clean);
        }
    }
    Ok((t, changed))
}

/// The fingerprint a card's change asks: the card, the base it was read at, the new text's
/// hash and which sections change.
pub fn ask_of(
    uid: &str,
    base_sha: &str,
    new: &str,
    changed: &[(&str, usize, usize)],
    by: String,
) -> crate::owner_ok::Ask {
    let params = json!({
        "persona_uid": uid, "base_sha": base_sha,
        "new_sha": crate::persona_write::sha_hex(new.as_bytes()),
        "sections": changed.iter().map(|(h, b, a)| json!({"name": h, "before": b, "after": a})).collect::<Vec<_>>(),
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_SET,
        resource: format!("人格 {uid}"),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// The window's sentence for [`OP_SET`]: `改人格 <uid> 的 紅線(原 12 字 → 30 字)…`.
pub fn plain(params: &Value) -> Option<String> {
    let uid = params["persona_uid"].as_str().filter(|u| uid_ok(u))?;
    let mut parts = Vec::new();
    for s in params["sections"].as_array()? {
        let name = s["name"].as_str()?;
        if !card::SECTIONS.iter().any(|(_, h)| *h == name) {
            return None;
        }
        let (before, after) = (s["before"].as_u64()?, s["after"].as_u64()?);
        parts.push(format!("{name}(原 {before} 字 → {after} 字)"));
    }
    (!parts.is_empty()).then(|| format!("改人格 {uid} 的 {}", parts.join("、")))
}

/// `persona_set`.
pub async fn set(st: AppState, body: Value, by: String) -> Response {
    if let Err(r) = only(
        &body,
        &[
            "type",
            "persona_uid",
            "base_sha",
            "name",
            "duty",
            "redlines",
            "knowledge",
            "ticket",
            "idem_key",
        ],
    ) {
        return *r;
    }
    if body.get("name").is_some() {
        return bad(
            "name_not_yet",
            "a persona is shown under its 1.x labels title; renaming it is not in this version",
        );
    }
    let uid = match uid_of(&body, "persona_uid") {
        Ok(u) => u,
        Err(r) => return *r,
    };
    let Some(base) = body
        .get("base_sha")
        .and_then(Value::as_str)
        .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .map(str::to_ascii_lowercase)
    else {
        return bad("base_sha", "the base_sha persona_read gave");
    };
    if let Some(e) = crate::persona_write::refused(&st.messages, &uid) {
        return bad(e.code(), "this persona is not written");
    }
    let (sha, text) = match card_now(&st, &uid).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    if !sha.eq_ignore_ascii_case(&base) {
        return err(
            StatusCode::CONFLICT,
            "persona_changed",
            "the card changed since it was read: read it again",
        );
    }
    let (new, changed) = match new_text(&text, &body) {
        Ok(x) => x,
        Err(e) => {
            return bad(
                e.code(),
                "a section cannot start a heading or a --- line, nor be over 4000 characters",
            )
        }
    };
    if changed.is_empty() {
        return Json(json!({"persona_uid": uid, "new_sha": sha, "changed": false})).into_response();
    }
    let ask = ask_of(&uid, &base, &new, &changed, by.clone());
    match body.get("ticket").and_then(Value::as_str) {
        None => return crate::repos::asked(&ask),
        Some(t) if !crate::owner_ok::approved_for(t, &ask) => return crate::repos::not_approved(),
        Some(_) => {}
    }
    match crate::persona_write::write(&st, &uid, &base, new.into_bytes(), &by).await {
        Ok(w) => {
            Json(json!({"persona_uid": uid, "new_sha": w.new_sha, "changed": true})).into_response()
        }
        Err(e) => {
            let code = match e {
                crate::persona_write::WriteError::Changed
                | crate::persona_write::WriteError::RegistryChanged => StatusCode::CONFLICT,
                _ => StatusCode::BAD_REQUEST,
            };
            err(code, e.code(), "the card was not written")
        }
    }
}

/// A1: the uids of `kind` archived now (unread: none, so a list is never emptied by a read
/// error; an open still checks each uid with [`archived_now`]).
pub fn archived_uids(db: &aib_store::MessageDb, kind: &str) -> std::collections::BTreeSet<String> {
    db.archived_list(kind)
        .map(|v| v.into_iter().map(|a| a.uid).collect())
        .unwrap_or_default()
}

/// A1: why an open may not use these: the first archived of persona / brain / role
/// (`<kind>_archived`); a read error refuses too (hold less, never more).
pub fn archived_now(
    db: &aib_store::MessageDb,
    persona: &str,
    brain: Option<&str>,
    role: Option<&str>,
) -> Option<&'static str> {
    let checks = [
        ("persona", Some(persona), "persona_archived"),
        ("brain", brain, "brain_archived"),
        ("role", role, "role_archived"),
    ];
    for (kind, uid, why) in checks {
        let Some(u) = uid else { continue };
        match db.archived(kind, u) {
            Ok(None) => {}
            Ok(Some(_)) => return Some(why),
            Err(_) => return Some("archive_unreadable"),
        }
    }
    None
}

/// The kind an archive / restore / set act is about.
fn kind_of(act: &str) -> &'static str {
    if act.starts_with("persona") {
        "persona"
    } else if act.starts_with("brain") {
        "brain"
    } else {
        "role"
    }
}

fn now_s() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0))
}

/// `<kind>_archive` / `<kind>_restore`.
pub async fn archive(st: AppState, act: &str, body: Value) -> Response {
    if let Err(r) = only(&body, &["type", "uid", "idem_key"]) {
        return *r;
    }
    let uid = match uid_of(&body, "uid") {
        Ok(u) => u,
        Err(r) => return *r,
    };
    let kind = kind_of(act);
    if kind == "role"
        && crate::registry::BUILTIN_ROLES
            .iter()
            .any(|(u, _, _)| *u == uid)
    {
        return bad("role_builtin", "the six built-in roles are locked");
    }
    let restore = act.ends_with("_restore");
    let (db, u) = (st.messages.clone(), uid.clone());
    let done = tokio::task::spawn_blocking(move || {
        if restore {
            return db.archive_restore(kind, &u, now_s()).map(|r| {
                if r {
                    "restored"
                } else {
                    "not_archived"
                }
            });
        }
        if db.archived(kind, &u)?.is_some() {
            return Ok("already");
        }
        db.archive_add(aib_store::ArchivedItem {
            kind: kind.to_owned(),
            uid: u,
            archived_at: now_s(),
            // a plain archive keeps no copy (no file is touched): kept until restored
            keep_until: None,
            reason: "archive".to_owned(),
            batch_id: None,
            copy_dir: None,
            sha256: None,
            restored_at: None,
        })
        .map(|()| "archived")
    })
    .await;
    match done {
        Ok(Ok("not_archived")) => err(StatusCode::NOT_FOUND, "not_archived", "it is not archived"),
        Ok(Ok(w)) => Json(json!({"uid": uid, "kind": kind, "state": w})).into_response(),
        _ => err(
            StatusCode::INTERNAL_SERVER_ERROR,
            "archive_io",
            "the archive could not be read or written",
        ),
    }
}

/// `brain_set` / `role_set`: aibd's registry.json; names unique within a kind, archived ones
/// included (f163c L2).
pub async fn rename(st: AppState, act: &str, body: Value) -> Response {
    let role = act == ROLE_SET;
    let keys: &[&str] = if role {
        &["type", "uid", "name", "what", "idem_key"]
    } else {
        &["type", "uid", "name", "idem_key"]
    };
    if let Err(r) = only(&body, keys) {
        return *r;
    }
    let uid = match uid_of(&body, "uid") {
        Ok(u) => u,
        Err(r) => return *r,
    };
    if role
        && crate::registry::BUILTIN_ROLES
            .iter()
            .any(|(u, _, _)| *u == uid)
    {
        return bad("role_builtin", "the six built-in roles are locked");
    }
    let name = match body.get("name").and_then(Value::as_str) {
        Some(n) => match crate::registry::clean(n, crate::registry::NAME_MAX) {
            Some(n) => Some(n),
            None => return bad("name", "name must be 1-20 characters"),
        },
        None => None,
    };
    let what = match body.get("what").and_then(Value::as_str) {
        Some(w) => match crate::registry::clean(w, crate::registry::WHAT_MAX) {
            Some(w) => Some(w),
            None => return bad("what", "what is too long"),
        },
        None => None,
    };
    let data = crate::registry::data_of(&st);
    crate::registry::edit(&data, |r| {
        let (list, builtin): (&mut Vec<crate::registry::Entry>, Vec<&str>) = if role {
            (
                &mut r.roles,
                crate::registry::BUILTIN_ROLES
                    .iter()
                    .map(|(_, n, _)| *n)
                    .collect(),
            )
        } else {
            (&mut r.brains, Vec::new())
        };
        if let Some(n) = &name {
            let taken =
                builtin.contains(&n.as_str()) || list.iter().any(|e| e.uid != uid && e.name == *n);
            if taken {
                return Err((
                    "name_taken",
                    "another one already has that name (archived ones too)",
                ));
            }
        }
        let e = list
            .iter_mut()
            .find(|e| e.uid == uid)
            .ok_or(("uid", "no such entry the person added"))?;
        if let Some(n) = name.clone() {
            e.name = n;
        }
        if let Some(w) = what.clone() {
            e.what = w;
        }
        Ok(())
    })
    .await
    .map_or_else(
        |(reason, message)| bad(reason, message),
        |()| Json(json!({"uid": uid})).into_response(),
    )
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const CARD: &str = "---\nuid: \"un0eba\"\nname: \"架構師\"\n---\n## 職責\n寫 aibd。\n\n## 紅線\n不 push。\n\n## 知識域\nRust。\n";

    /// A1-1: only the sections sent change, and the window's words say which and how long.
    #[test]
    fn only_the_sections_sent_change_and_the_window_says_which() {
        let (t, ch) = new_text(CARD, &json!({"redlines": "不 push。\n不合 main。"})).unwrap();
        assert_eq!(card::section(&t, "紅線"), "不 push。\n不合 main。");
        assert_eq!(card::section(&t, "職責"), "寫 aibd。");
        assert_eq!(ch, vec![("紅線", 7, 15)]);
        let (same, none) = new_text(CARD, &json!({"duty": "寫 aibd。"})).unwrap();
        assert_eq!(
            (same.as_str(), none.len()),
            (CARD, 0),
            "reason: unchanged_written"
        );
        let a = ask_of("un0eba", &"a".repeat(64), &t, &ch, "page:x".into());
        let w = plain(&serde_json::from_slice(&a.params).unwrap()).unwrap();
        assert_eq!(w, "改人格 un0eba 的 紅線(原 7 字 → 15 字)");
        assert!(crate::owner_ok::WIRED.contains(&OP_SET));
    }

    /// A1-5 (f163c L1): a heading or a --- line in a section is refused, as the page refuses.
    #[test]
    fn a_heading_or_a_rule_line_in_a_section_is_refused() {
        for bad in ["# x", "  ## y", "###### z", "---", "  ---  "] {
            let r = new_text(CARD, &json!({"duty": format!("a\n{bad}\nb")}));
            assert_eq!(
                r.map(|_| ()),
                Err(card::EditError::Heading("duty")),
                "reason: heading_let_in {bad:?}"
            );
        }
        assert!(new_text(
            CARD,
            &json!({"duty": "#標籤 不是標題\n-- 兩槓\t全形\u{3000}空白"})
        )
        .is_ok());
        // f163c pre L1: what a reader would not see is refused, not dropped
        for bad in ["\u{202e}", "\u{200b}", "\u{2066}", "\u{feff}"] {
            let r = new_text(CARD, &json!({"redlines": format!("不 push{bad}。")}));
            assert_eq!(
                r.map(|_| ()),
                Err(card::EditError::Hidden("redlines")),
                "reason: hidden_char_let_in {bad:?}"
            );
        }
    }

    /// A1-2: the window's words say nothing for a section name that is not one of the three.
    #[test]
    fn the_window_says_only_known_sections() {
        let p = json!({"persona_uid": "un0eba", "sections": [{"name": "寫作規則", "before": 1, "after": 2}]});
        assert!(plain(&p).is_none());
        let p =
            json!({"persona_uid": "../x", "sections": [{"name": "紅線", "before": 1, "after": 2}]});
        assert!(plain(&p).is_none());
        // pre N1: a uid the act takes (with `_`) is named in the window, not dropped
        let p = json!({"persona_uid": "p_ab1", "sections": [{"name": "紅線", "before": 1, "after": 2}]});
        assert_eq!(
            plain(&p).as_deref(),
            Some("改人格 p_ab1 的 紅線(原 1 字 → 2 字)")
        );
    }
}
