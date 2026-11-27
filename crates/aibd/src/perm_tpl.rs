//! aibd §7 permission templates (DESIGN-brainnote-08de5-1123 §2; DECISION-council-aipartner-
//! ebab8-1122 §一-5, §一-8, §一-9; SPEC-ai-partner-open-ebab8-1122 §4).
//!
//! - A template is a base mode (`safe` / `work` / `dgrs`; `host` once §5 is in) and each of
//!   [`ITEMS`] on or off, kept in the message library ([`aib_store::PermTemplate`]), never a
//!   settings file. The four modes are not one line (§一-8): `host` is its own axis.
//! - What needs the owner's fingerprint is decided here only ([`needs_owner_add`],
//!   [`needs_owner_change`]): adding one that holds a dangerous item or base, and every change
//!   that turns an item on or moves the base up. Lowering, renaming and removing do not.
//! - The window lists the items as they come out, not only the template's name, and what this
//!   change turns on comes last (the window keeps the end of a long line).
//! - aibd never takes a template's name or a page's list of items on trust when a seat opens:
//!   it reads the template at the revision the page saw and expands it itself (slice 7b).
//! - An item a base cannot hold today is not dropped quietly: [`not_enforceable`] names it, and
//!   opening or applying such a template is refused (slice 7b). Keeping it is allowed.

use crate::api::AppState;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

pub const LIST: &str = "perm_template_list";
pub const ADD: &str = "perm_template_add";
pub const SET: &str = "perm_template_set";
pub const REMOVE: &str = "perm_template_remove";
pub const LOG: &str = "perm_template_log";

/// The fingerprint operation of a template change that raises (owner_ok WIRED).
pub const OP: &str = "perm_template.raise";

/// The items, in the page's order (MOCK-ai-partner app.js PERMS), with the window's word.
pub const ITEMS: [(&str, &str); 11] = [
    ("chat", "對話"),
    ("code", "寫程式"),
    ("own", "自己資料夾"),
    ("cmd", "名單指令"),
    ("persona", "人格提案"),
    ("brain", "第二大腦"),
    ("any", "資料夾外"),
    ("anycmd", "任何指令"),
    ("net", "外網"),
    ("push", "推送"),
    ("host", "連主機"),
];

/// Items whose being on asks the fingerprint when a template is added (SPEC §4).
pub const DANGEROUS: [&str; 4] = ["any", "anycmd", "push", "host"];

/// The bases a template may name.
pub const BASES: [&str; 4] = ["safe", "work", "dgrs", "host"];

/// The names a template may not take: the four modes, in words and in code.
pub const RESERVED: [&str; 8] = [
    "討論研究",
    "沙盒開發",
    "管理主機",
    "完全權限",
    "safe",
    "work",
    "host",
    "dgrs",
];

/// The push item's key (an array of item words ending in it would read as a git push to
/// sent_trail's count of pushes).
pub const PUSH: &str = "push";

const DGRS_GIVES: [&str; 9] = [
    "chat", "code", "own", "cmd", "brain", "any", "anycmd", "net", PUSH,
];

/// What each base gives a seat today, as aibd opens it (aib-seatopen; DESIGN §2a). `host` is
/// not opened yet.
pub fn gives_now(base: &str) -> &'static [&'static str] {
    match base {
        "safe" => &["chat"],
        "work" => &["chat", "code", "own", "cmd", "brain"],
        // the push item through a constant: sent_trail counts `"push",` + newline as git pushes
        "dgrs" => &DGRS_GIVES,
        _ => &[],
    }
}

/// Items aibd can take away from what a base gives today (the brain tool is given apart).
pub const DROPPABLE_NOW: [&str; 1] = ["brain"];

/// The items, each on or off, in [`ITEMS`] order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Items([bool; ITEMS.len()]);

impl Items {
    pub fn on(&self, key: &str) -> bool {
        ITEMS
            .iter()
            .position(|(k, _)| *k == key)
            .is_some_and(|i| self.0[i])
    }

    pub fn keys_on(&self) -> Vec<&'static str> {
        ITEMS
            .iter()
            .zip(self.0)
            .filter(|(_, on)| *on)
            .map(|((k, _), _)| *k)
            .collect()
    }

    /// The normal form kept and hashed: every item in [`ITEMS`] order.
    pub fn json(&self) -> String {
        let mut m = String::from("{");
        for (i, ((k, _), on)) in ITEMS.iter().zip(self.0).enumerate() {
            if i > 0 {
                m.push(',');
            }
            m.push_str(&format!("\"{k}\":{on}"));
        }
        m.push('}');
        m
    }

    /// The items of a page's body or a kept row: an object with every item exactly once, each
    /// a bool, and nothing else.
    pub fn parse(v: &Value) -> Option<Items> {
        let o: &Map<String, Value> = v.as_object()?;
        if o.len() != ITEMS.len() {
            return None;
        }
        let mut out = [false; ITEMS.len()];
        for (i, (k, _)) in ITEMS.iter().enumerate() {
            out[i] = o.get(*k)?.as_bool()?;
        }
        Some(Items(out))
    }

    pub fn from_keys(keys: &[&str]) -> Items {
        let mut out = [false; ITEMS.len()];
        for (i, (k, _)) in ITEMS.iter().enumerate() {
            out[i] = keys.contains(k);
        }
        Items(out)
    }
}

fn word(key: &str) -> &'static str {
    ITEMS
        .iter()
        .find(|(k, _)| *k == key)
        .map_or("?", |(_, w)| w)
}

fn base_rank(base: &str) -> Option<u8> {
    match base {
        "safe" => Some(0),
        "work" => Some(1),
        "dgrs" => Some(2),
        _ => None,
    }
}

/// Whether going from base `old` to `new` is a raise: up the safe < work < dgrs line, or any
/// move into, out of or along the `host` axis.
pub fn base_raised(old: &str, new: &str) -> bool {
    if old == new {
        return false;
    }
    match (base_rank(old), base_rank(new)) {
        (Some(a), Some(b)) => b > a,
        _ => true,
    }
}

/// Adding a template asks the fingerprint when its base is `dgrs` / `host` or a
/// [`DANGEROUS`] item is on.
pub fn needs_owner_add(base: &str, items: &Items) -> bool {
    matches!(base, "dgrs" | "host") || DANGEROUS.iter().any(|k| items.on(k))
}

/// The items `new` turns on that `old` had off.
pub fn turned_on(old: &Items, new: &Items) -> Vec<&'static str> {
    ITEMS
        .iter()
        .map(|(k, _)| *k)
        .filter(|k| new.on(k) && !old.on(k))
        .collect()
}

/// A change asks the fingerprint when it turns an item on or raises the base.
pub fn needs_owner_change(old_base: &str, old: &Items, new_base: &str, new: &Items) -> bool {
    base_raised(old_base, new_base) || !turned_on(old, new).is_empty()
}

/// What a seat opened with this base and these items would not get as written, today: an item
/// on that the base does not give, an item off that the base gives and aibd cannot take away,
/// or `base:host` while that mode is not opened. Empty: it can be held as written.
pub fn not_enforceable(base: &str, items: &Items) -> Vec<String> {
    if base == "host" {
        return vec!["base:host".into()];
    }
    let gives = gives_now(base);
    ITEMS
        .iter()
        .map(|(k, _)| *k)
        .filter(|k| {
            let on = items.on(k);
            let given = gives.contains(k);
            (on && !given) || (!on && given && !DROPPABLE_NOW.contains(k))
        })
        .map(str::to_string)
        .collect()
}

/// A template's name: control characters out, trimmed, 1–20 characters, not one of
/// [`RESERVED`] (case kept apart for the codes).
pub fn clean_name(s: &str) -> Option<String> {
    let n = crate::registry::clean(s, 20)?;
    (!RESERVED.iter().any(|r| r.eq_ignore_ascii_case(&n))).then_some(n)
}

/// The window's line: the base first (aibd's word), the template's name after it cut to
/// [`crate::owner_ok::NAME_SHOWN`] (ebab8 11-25 14:01, SCAN-owner-window-words 2: a long
/// name pushed the base into the part the window cuts), what it holds, and what this change
/// turns on last (the window keeps the end of a long line).
pub fn window_line(name: &str, base: &str, items: &Items, raised: &[&str]) -> String {
    let held: Vec<&str> = items.keys_on().into_iter().map(word).collect();
    let head = crate::owner_ok::fixed_then_name(&[&format!("底 {base}"), "權限模板"], name);
    let mut s = format!("{head}:{}", held.join("、"));
    if !raised.is_empty() {
        let r: Vec<&str> = raised.iter().copied().map(word).collect();
        s.push_str(&format!(";這次多開 {}", r.join("、")));
    }
    s
}

fn ask(op_params: Value, resource: String, by: String) -> crate::owner_ok::Ask {
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource,
        params: serde_json::to_vec(&op_params).unwrap_or_default(),
        by,
    }
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error": {"code": code, "message": message}})),
    )
        .into_response()
}

fn shown(t: &aib_store::PermTemplate) -> Value {
    let items = serde_json::from_str::<Value>(&t.items_json).unwrap_or(Value::Null);
    let parsed = Items::parse(&items);
    json!({
        "tpl_uid": t.tpl_uid,
        "name": t.name,
        "base": t.base,
        "items": items,
        "rev": t.rev,
        "updated_at": t.updated_at,
        "not_enforceable": parsed.map(|i| not_enforceable(&t.base, &i)),
    })
}

/// `perm_template_list {}`.
pub async fn list(st: AppState) -> Response {
    let db = st.messages.clone();
    match tokio::task::spawn_blocking(move || db.perm_templates()).await {
        Ok(Ok(v)) => Json(json!({
            "templates": v.iter().map(shown).collect::<Vec<_>>(),
            "items": ITEMS.iter().map(|(k, w)| json!({"key": k, "word": w})).collect::<Vec<_>>(),
        }))
        .into_response(),
        _ => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "templates unreadable",
        ),
    }
}

/// `perm_template_log {tpl_uid}`.
pub async fn log(st: AppState, body: Value) -> Response {
    let Some(uid) = body
        .get("tpl_uid")
        .and_then(Value::as_str)
        .filter(|u| aib_store::ranks::key_ok(u))
        .map(str::to_owned)
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "tpl_uid is needed",
        );
    };
    let db = st.messages.clone();
    match tokio::task::spawn_blocking(move || db.perm_template_log(&uid)).await {
        Ok(Ok(v)) => Json(json!({"log": v.iter().map(|l| json!({
            "rev": l.rev, "action": l.action, "at": l.at, "by": l.by,
            "from": l.from_json.as_deref().and_then(|s| serde_json::from_str::<Value>(s).ok()),
            "to": l.to_json.as_deref().and_then(|s| serde_json::from_str::<Value>(s).ok()),
        })).collect::<Vec<_>>()}))
        .into_response(),
        _ => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "log unreadable",
        ),
    }
}

/// The name, base and items of an add / set body.
fn parse_shape(body: &Value) -> Result<(String, String, Items), (&'static str, &'static str)> {
    let name = body
        .get("name")
        .and_then(Value::as_str)
        .and_then(clean_name)
        .ok_or((
            "template_name",
            "name: 1-20 characters, not one of the four modes",
        ))?;
    let base = body
        .get("base")
        .and_then(Value::as_str)
        .filter(|b| BASES.contains(b))
        .ok_or(("template_base", "base is safe, work, dgrs or host"))?
        .to_string();
    let items = body.get("items").and_then(Items::parse).ok_or((
        "template_items",
        "items: every item once, each true or false",
    ))?;
    Ok((name, base, items))
}

/// The fingerprint gate of a raise, for add and set alike (f163c permtpl code L1): `Some` is
/// the answer to give now (no ticket: asked; a ticket for other words: not approved).
fn owner_gate(body: &Value, a: &crate::owner_ok::Ask) -> Option<Response> {
    let Some(t) = ticket_of(body) else {
        return Some(crate::repos::asked(a));
    };
    if !crate::owner_ok::approved_for(&t, a) {
        return Some(crate::repos::not_approved());
    }
    None
}

fn ticket_of(body: &Value) -> Option<String> {
    body.get("ticket")
        .and_then(Value::as_str)
        .map(str::to_owned)
}

fn written(
    w: Result<Result<aib_store::TplWrite, aib_store::StoreError>, tokio::task::JoinError>,
    uid: &str,
) -> Response {
    use aib_store::TplWrite;
    match w {
        Ok(Ok(TplWrite::Done(rev))) => Json(json!({"done": true, "tpl_uid": uid, "rev": rev})).into_response(),
        Ok(Ok(TplWrite::Unknown)) => refuse(StatusCode::NOT_FOUND, "template_unknown", "no such template"),
        Ok(Ok(TplWrite::RevChanged(r))) => (
            StatusCode::CONFLICT,
            Json(json!({"error": {"code": "template_changed", "message": "the template changed since it was read", "rev": r}})),
        )
            .into_response(),
        Ok(Ok(TplWrite::NameTaken)) => refuse(StatusCode::CONFLICT, "template_name_taken", "another template has this name"),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "template not written"),
    }
}

/// `perm_template_add {name, base, items, ticket?}`.
pub async fn add(st: AppState, body: Value, by: String) -> Response {
    let (name, base, items) = match parse_shape(&body) {
        Ok(x) => x,
        Err((c, m)) => return refuse(StatusCode::BAD_REQUEST, c, m),
    };
    if needs_owner_add(&base, &items) {
        let a = ask(
            json!({"act": ADD, "name": name, "base": base, "items": items.json()}),
            window_line(&name, &base, &items, &items.keys_on()),
            by.clone(),
        );
        if let Some(r) = owner_gate(&body, &a) {
            return r;
        }
    }
    let db = st.messages.clone();
    let taken = tokio::task::spawn_blocking({
        let db = db.clone();
        move || {
            db.perm_templates()
                .map(|v| v.into_iter().map(|t| t.tpl_uid).collect::<Vec<_>>())
        }
    })
    .await;
    let Ok(Ok(taken)) = taken else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "templates unreadable",
        );
    };
    let taken: Vec<&str> = taken.iter().map(String::as_str).collect();
    let Ok(uid) = crate::registry::new_uid("tpl_", &taken, getrandom::fill) else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "uid");
    };
    let (u, n, b, i, by2, at) = (
        uid.clone(),
        name.clone(),
        base.clone(),
        items.json(),
        by.clone(),
        crate::sessions::now_rfc3339(),
    );
    let w =
        tokio::task::spawn_blocking(move || db.perm_template_add(&u, &n, &b, &i, &by2, &at)).await;
    let ok = matches!(w, Ok(Ok(aib_store::TplWrite::Done(_))));
    if ok {
        crate::seats::side_event(
            &st,
            ADD,
            "perm_template.add",
            json!({"tpl_uid": uid, "base": base, "items": items.keys_on(), "by": by}),
        )
        .await;
    }
    written(w, &uid)
}

/// `perm_template_set {tpl_uid, rev, name, base, items, ticket?}`.
pub async fn set(st: AppState, body: Value, by: String) -> Response {
    let (name, base, items) = match parse_shape(&body) {
        Ok(x) => x,
        Err((c, m)) => return refuse(StatusCode::BAD_REQUEST, c, m),
    };
    let (Some(uid), Some(rev)) = (
        body.get("tpl_uid")
            .and_then(Value::as_str)
            .filter(|u| aib_store::ranks::key_ok(u))
            .map(str::to_owned),
        body.get("rev").and_then(Value::as_i64),
    ) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "tpl_uid and rev are needed",
        );
    };
    let db = st.messages.clone();
    let u = uid.clone();
    let old = match tokio::task::spawn_blocking(move || db.perm_template(&u)).await {
        Ok(Ok(Some(t))) => t,
        Ok(Ok(None)) => {
            return refuse(
                StatusCode::NOT_FOUND,
                "template_unknown",
                "no such template",
            )
        }
        _ => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "template unreadable",
            )
        }
    };
    if old.rev != rev {
        return written(Ok(Ok(aib_store::TplWrite::RevChanged(old.rev))), &uid);
    }
    let Some(old_items) = serde_json::from_str::<Value>(&old.items_json)
        .ok()
        .as_ref()
        .and_then(Items::parse)
    else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "kept items unreadable",
        );
    };
    if needs_owner_change(&old.base, &old_items, &base, &items) {
        let raised = turned_on(&old_items, &items);
        let mut line = window_line(&name, &base, &items, &raised);
        if base_raised(&old.base, &base) {
            line.push_str(&format!(";底 {} → {base}", old.base));
        }
        let a = ask(
            json!({"act": SET, "tpl_uid": uid, "rev": rev, "name": name, "base": base,
                   "items": items.json()}),
            line,
            by.clone(),
        );
        if let Some(r) = owner_gate(&body, &a) {
            return r;
        }
    }
    let db = st.messages.clone();
    let (u, n, b, i, by2, at) = (
        uid.clone(),
        name.clone(),
        base.clone(),
        items.json(),
        by.clone(),
        crate::sessions::now_rfc3339(),
    );
    let w =
        tokio::task::spawn_blocking(move || db.perm_template_set(&u, rev, &n, &b, &i, &by2, &at))
            .await;
    if matches!(w, Ok(Ok(aib_store::TplWrite::Done(_)))) {
        crate::seats::side_event(
            &st,
            SET,
            "perm_template.set",
            json!({"tpl_uid": uid, "from_base": old.base, "base": base,
                   "from_items": old_items.keys_on(), "items": items.keys_on(), "by": by}),
        )
        .await;
    }
    written(w, &uid)
}

/// `perm_template_remove {tpl_uid, rev}` (no fingerprint: it only lowers; seats already open
/// keep what they were opened with).
pub async fn remove(st: AppState, body: Value, by: String) -> Response {
    let (Some(uid), Some(rev)) = (
        body.get("tpl_uid")
            .and_then(Value::as_str)
            .filter(|u| aib_store::ranks::key_ok(u))
            .map(str::to_owned),
        body.get("rev").and_then(Value::as_i64),
    ) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "tpl_uid and rev are needed",
        );
    };
    let (db, u, b, at) = (
        st.messages.clone(),
        uid.clone(),
        by.clone(),
        crate::sessions::now_rfc3339(),
    );
    let w = tokio::task::spawn_blocking(move || db.perm_template_remove(&u, rev, &b, &at)).await;
    if matches!(w, Ok(Ok(aib_store::TplWrite::Done(_)))) {
        crate::seats::side_event(
            &st,
            REMOVE,
            "perm_template.remove",
            json!({"tpl_uid": uid, "by": by}),
        )
        .await;
    }
    written(w, &uid)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn all_off() -> Items {
        Items::from_keys(&[])
    }

    /// PT1: every single item turned on asks the fingerprint, on any base; turning one off,
    /// renaming (same items) and staying put do not.
    #[test]
    fn every_item_turned_on_asks_the_owner() {
        for base in ["safe", "work", "dgrs"] {
            for (k, _) in ITEMS {
                let new = Items::from_keys(&[k]);
                assert!(
                    needs_owner_change(base, &all_off(), base, &new),
                    "reason: item_raise_not_asked {base} {k}"
                );
                assert!(
                    !needs_owner_change(base, &new, base, &all_off()),
                    "reason: item_lower_asked {base} {k}"
                );
                assert!(
                    !needs_owner_change(base, &new, base, &new),
                    "reason: no_change_asked {base} {k}"
                );
            }
        }
    }

    /// PT2: the base raises up safe < work < dgrs and on every move touching host; going down
    /// the line does not.
    #[test]
    fn the_base_raises_up_the_line_and_on_the_host_axis() {
        let i = all_off();
        for (a, b) in [("safe", "work"), ("work", "dgrs"), ("safe", "dgrs")] {
            assert!(
                needs_owner_change(a, &i, b, &i),
                "reason: up_not_asked {a}->{b}"
            );
            assert!(
                !needs_owner_change(b, &i, a, &i),
                "reason: down_asked {b}->{a}"
            );
        }
        for o in ["safe", "work", "dgrs"] {
            assert!(base_raised(o, "host"), "reason: into_host {o}");
            assert!(base_raised("host", o), "reason: out_of_host {o}");
        }
        assert!(!base_raised("host", "host"));
    }

    /// PT3: adding asks for a dangerous item or a dgrs / host base only.
    #[test]
    fn adding_asks_for_dangerous_items_or_bases() {
        for k in DANGEROUS {
            assert!(
                needs_owner_add("safe", &Items::from_keys(&[k])),
                "reason: dangerous_add_not_asked {k}"
            );
        }
        assert!(needs_owner_add("dgrs", &all_off()));
        assert!(needs_owner_add("host", &all_off()));
        assert!(!needs_owner_add(
            "work",
            &Items::from_keys(&["chat", "code", "own", "cmd", "brain", "net", "persona"])
        ));
    }

    /// PT4: items parse only with every item once, each a bool; the normal form is fixed.
    #[test]
    fn items_parse_strictly_and_have_one_form() {
        let i = Items::from_keys(&["chat", "push"]);
        let back = Items::parse(&serde_json::from_str(&i.json()).unwrap()).unwrap();
        assert_eq!(back, i);
        assert!(i.json().starts_with("{\"chat\":true,\"code\":false,"));
        let mut v: Value = serde_json::from_str(&i.json()).unwrap();
        v["extra"] = json!(true);
        assert_eq!(Items::parse(&v), None, "reason: extra_key_taken");
        let mut v: Value = serde_json::from_str(&i.json()).unwrap();
        v.as_object_mut().unwrap().remove("net");
        assert_eq!(Items::parse(&v), None, "reason: missing_key_taken");
        let mut v: Value = serde_json::from_str(&i.json()).unwrap();
        v["net"] = json!(1);
        assert_eq!(Items::parse(&v), None, "reason: number_taken");
    }

    /// PT5: what a base cannot hold today is named, never dropped quietly.
    #[test]
    fn what_a_base_cannot_hold_is_named() {
        let work = Items::from_keys(gives_now("work"));
        assert!(not_enforceable("work", &work).is_empty());
        let no_brain = Items::from_keys(&["chat", "code", "own", "cmd"]);
        assert!(
            not_enforceable("work", &no_brain).is_empty(),
            "reason: brain_not_droppable"
        );
        let with_net = Items::from_keys(&["chat", "code", "own", "cmd", "brain", "net"]);
        assert_eq!(not_enforceable("work", &with_net), ["net"]);
        let no_code = Items::from_keys(&["chat", "own", "cmd", "brain"]);
        assert_eq!(not_enforceable("work", &no_code), ["code"]);
        assert_eq!(not_enforceable("host", &all_off()), ["base:host"]);
    }

    /// PT6: the four modes' names are not a template's; the window ends with what is turned on.
    #[test]
    fn reserved_names_and_the_window_line() {
        for r in RESERVED {
            assert_eq!(clean_name(r), None, "reason: reserved_taken {r}");
        }
        assert_eq!(clean_name("WORK"), None);
        assert_eq!(clean_name(" 寫稿 "), Some("寫稿".into()));
        assert_eq!(clean_name(&"字".repeat(21)), None);
        let line = window_line(
            "寫稿",
            "work",
            &Items::from_keys(&["chat", "push"]),
            &["push"],
        );
        assert!(
            line.ends_with(";這次多開 推送"),
            "reason: raise_not_last {line}"
        );
        assert!(line.contains("對話、推送"));
    }

    /// ebab8 11-25 14:01 (SCAN-owner-window-words 2): the template's name is the writer's
    /// word. However long, or written to look like a base, the window shows the real base
    /// first and the name after it, cut.
    #[test]
    fn a_template_name_never_stands_before_the_base() {
        let it = Items::from_keys(&["chat", "code", "own", "cmd", "brain", "net", "push"]);
        for name in [
            "很長很長很長很長很長很長很長很長很長很長的模板名字",
            "底 safe 權限模板",
        ] {
            let line = window_line(name, "dgrs", &it, &["push"]);
            let shown = crate::owner_ok::object_shown(&line);
            crate::owner_ok::fixed_first(&shown, "底 dgrs · 權限模板 · ", &["safe"]).unwrap();
            assert!(line.contains('…'), "reason: name_not_cut {line}");
            assert!(
                line.ends_with(";這次多開 推送"),
                "reason: raise_not_last {line}"
            );
        }
    }
}
