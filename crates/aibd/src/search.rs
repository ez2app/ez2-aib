//! §18 `GET /api/v1/search?q=&limit=` (Andy 11-18 19:5x 「要有一個 搜尋框 搜尋出來 要分類」;
//! SPEC-workspace-multiseat §18, IFACE-search-a9136-1118): one query over the project tree's
//! items, the threads (by kind), the seats, the documents posts link and the 待人決策 list,
//! grouped in that order with a count each; a group with nothing found is left out.
//!
//! Who sees what (§18 point 4; 5d33a CHECK-search-iface M1–M3): a post's text and a 待人決策
//! item's own words only where [`crate::workspace::gives_words`] says words are given and the
//! pairing was not carried over an update (`full: true`); otherwise titles, members, ids and kinds
//! only, no snippet. A thread is listed only when the page could read it (the same rule as
//! reading it), and counted only then. A page held as a user page gets no documents group (GET
//! /api/v1/doc refuses it; the names alone would leak). The query is not written to the event log.
use crate::api::AppState;
use crate::guard::Caller;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use std::collections::HashMap;

/// The longest query, in bytes.
pub const Q_MAX: usize = 100;
/// Items a group gives back when `limit` is not sent, and the most it may ask for.
pub const LIMIT_DEFAULT: usize = 5;
pub const LIMIT_MAX: usize = 20;
/// The text kept around a match in a post (characters before and after).
pub const SNIPPET_AROUND: usize = 60;
/// Posts with a document read for the documents group.
pub const DOC_POSTS_MAX: usize = 5000;
/// The thread kinds, in the page's order; a thread with none is `general`.
pub const KINDS: [&str; 6] = [
    "council", "summary", "handoff", "drive", "shadow", "general",
];

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (status, Json(json!({"code": code, "message": message}))).into_response()
}

/// `q` matched in `text`, any case for ASCII (as SQLite LIKE matches it).
pub fn has(text: &str, q: &str) -> bool {
    text.to_ascii_lowercase().contains(&q.to_ascii_lowercase())
}

/// The text around the first match of `q`, at most [`SNIPPET_AROUND`] characters each side,
/// `…` where it was cut.
pub fn snippet(text: &str, q: &str) -> String {
    let low = text.to_ascii_lowercase();
    let Some(at) = low.find(&q.to_ascii_lowercase()) else {
        return text.chars().take(SNIPPET_AROUND * 2).collect();
    };
    let before: Vec<char> = text[..at].chars().collect();
    let start = before.len().saturating_sub(SNIPPET_AROUND);
    let mut s: String = before[start..].iter().collect();
    if start > 0 {
        s.insert(0, '…');
    }
    let rest = &text[at..];
    let keep = q.chars().count() + SNIPPET_AROUND;
    s.extend(rest.chars().take(keep));
    if rest.chars().count() > keep {
        s.push('…');
    }
    s
}

/// A group: its kind, how many were found, the first `limit`.
fn group(kind: &str, items: Vec<Value>, limit: usize) -> Option<Value> {
    if items.is_empty() {
        return None;
    }
    let count = items.len();
    Some(
        json!({"kind": kind, "count": count, "items": items.into_iter().take(limit).collect::<Vec<_>>()}),
    )
}

/// The project tree's items whose id or title has `q`.
pub fn nodes(tree: Option<&Value>, q: &str) -> Vec<Value> {
    let Some(ns) = tree.and_then(|t| t.get("nodes")).and_then(Value::as_array) else {
        return Vec::new();
    };
    ns.iter()
        .filter_map(|n| {
            let id = n.get("id").and_then(Value::as_str)?;
            let title = n.get("title").and_then(Value::as_str).unwrap_or("");
            let hit = if has(id, q) {
                "id"
            } else if has(title, q) {
                "title"
            } else {
                return None;
            };
            Some(json!({"id": id, "title": title, "station": n.get("status"), "hit": hit}))
        })
        .collect()
}

/// The seats (as GET /api/v1/seats shows them) whose persona name, shown name, provider or
/// code has `q`.
pub fn seats(views: &[Value], q: &str) -> Vec<Value> {
    views
        .iter()
        .filter_map(|v| {
            let id = v["seat_id"].as_str()?;
            let persona = v["persona"]["name"].as_str();
            let hit = if persona.is_some_and(|p| has(p, q)) {
                "persona"
            } else if has(v["display"].as_str().unwrap_or(""), q) {
                "suffix"
            } else if v["provider"].as_str().is_some_and(|p| has(p, q)) {
                "brand"
            } else if has(id, q) || has(v["name"].as_str().unwrap_or(""), q) {
                "id"
            } else {
                return None;
            };
            Some(
                json!({"seat_id": id, "persona": persona, "display": v["display"],
                "brand": v["provider"], "state": v["state"], "hit": hit}),
            )
        })
        .collect()
}

/// The documents posts link (`doc` in a post's item) whose file name or title has `q`.
pub fn docs(posts: &[(String, String, String)], q: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for (thread_id, message_id, item) in posts {
        let Ok(item) = serde_json::from_str::<Value>(item) else {
            continue;
        };
        for (n, d) in item["doc"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[])
            .iter()
            .enumerate()
        {
            let file = d["file"].as_str().unwrap_or("");
            let title = d["title"].as_str().unwrap_or("");
            let hit = if has(file, q) {
                "file"
            } else if has(title, q) {
                "title"
            } else {
                continue;
            };
            out.push(
                json!({"thread_id": thread_id, "message_id": message_id, "n": n,
                "file": file, "title": title, "hit": hit}),
            );
        }
    }
    out
}

/// The 待人決策 items open now whose id or card has `q`, and (`full`) whose own words have it.
pub fn andy_open(items: &[aib_drive::andy_open::Item], q: &str, full: bool) -> Vec<Value> {
    items
        .iter()
        .filter(|i| !i.done)
        .filter_map(|i| {
            let text = i.fields.values().cloned().collect::<Vec<_>>().join(" ");
            let hit = if has(&i.item, q) {
                "id"
            } else if has(&i.card, q) {
                "card"
            } else if full && has(&text, q) {
                "text"
            } else {
                return None;
            };
            let mut o = json!({"id": i.item, "card": i.card, "hit": hit});
            if full {
                o["text"] = json!(text);
            }
            Some(o)
        })
        .collect()
}

/// The threads group: each found thread with its kind, `by_category` counting all six.
pub fn threads(
    hits: &[aib_store::ThreadHit],
    kinds: &HashMap<String, String>,
    members: &HashMap<String, Vec<String>>,
    q: &str,
    full: bool,
    limit: usize,
) -> Option<Value> {
    if hits.is_empty() {
        return None;
    }
    let mut by: Map<String, Value> = KINDS.iter().map(|k| ((*k).to_owned(), json!(0))).collect();
    let items: Vec<Value> = hits
        .iter()
        .map(|h| {
            let kind = kinds
                .get(&h.thread.thread_id)
                .map_or("general", String::as_str);
            if let Some(n) = by.get_mut(kind) {
                *n = json!(n.as_u64().unwrap_or(0) + 1);
            }
            let mut o = json!({
                "thread_id": h.thread.thread_id,
                "last_at": crate::sessions::rfc3339_of_unix(h.thread.last_unix),
                "title": h.thread.title,
                "category": kind,
                "node": h.thread.node,
                "members": members.get(&h.thread.thread_id).cloned().unwrap_or_default(),
                "hit": h.hit,
            });
            if let (true, Some((id, body))) = (full, &h.post) {
                o["message_id"] = json!(id);
                o["snippet"] = json!(snippet(body, q));
            }
            o
        })
        .collect();
    Some(
        json!({"kind": "thread", "count": items.len(), "by_category": by,
        "items": items.into_iter().take(limit).collect::<Vec<_>>()}),
    )
}

/// `GET /api/v1/search?q=&limit=`.
pub async fn search(
    State(st): State<AppState>,
    Extension(c): Extension<Caller>,
    Query(qs): Query<HashMap<String, String>>,
) -> Response {
    let Caller::Page(page) = c else {
        return refuse(StatusCode::FORBIDDEN, "page_only", "search is the page's");
    };
    if qs.keys().any(|k| !matches!(k.as_str(), "q" | "limit")) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "the query takes q and limit only",
        );
    }
    let q = qs.get("q").map(|q| q.trim().to_owned()).unwrap_or_default();
    if q.is_empty() || q.len() > Q_MAX {
        return refuse(StatusCode::BAD_REQUEST, "q_bad", "q is 1–100 bytes");
    }
    let limit = match qs.get("limit").map(|l| l.parse::<usize>()) {
        None => LIMIT_DEFAULT,
        Some(Ok(n)) if (1..=LIMIT_MAX).contains(&n) => n,
        Some(_) => return refuse(StatusCode::BAD_REQUEST, "limit_bad", "limit is 1–20"),
    };
    // §18 point 4 (5d33a M1): the one rule the tree's words follow, and not a carried pairing
    let words = crate::workspace::gives_words(&c).await;
    let full = words && !page.carried;
    let reader = crate::threads::page_sender(&page).await;

    let mut groups: Vec<Value> = Vec::new();
    let mut partial: Vec<&str> = Vec::new();
    let tree = crate::workspace::current_tree();
    groups.extend(group("node", nodes(tree.as_ref(), &q), limit));

    let (db, q2) = (st.messages.clone(), q.clone());
    let found = crate::threads::blocking(move || {
        let hits = db.search_threads(&q2, full)?;
        let ids: Vec<String> = hits.iter().map(|h| h.thread.thread_id.clone()).collect();
        let kinds = db.categories_of(&ids)?;
        let mut members = HashMap::new();
        for id in &ids {
            let m: Vec<String> = db
                .thread_members(id)?
                .into_iter()
                .map(|m| m.member)
                .collect();
            members.insert(id.clone(), m);
        }
        let posts = db.posts_with_docs(DOC_POSTS_MAX)?;
        Ok((hits, kinds, members, posts))
    })
    .await;
    let doc_items = match found {
        Some((hits, kinds, members, posts)) => {
            // 5d33a M2: only what this page could read, as reading it decides (threads::load)
            let hits: Vec<aib_store::ThreadHit> = hits
                .into_iter()
                .filter(|h| {
                    reader.is_person()
                        || members
                            .get(&h.thread.thread_id)
                            .is_some_and(|m| m.contains(&reader.key))
                })
                .collect();
            let readable: std::collections::HashSet<&str> =
                hits.iter().map(|h| h.thread.thread_id.as_str()).collect();
            groups.extend(threads(&hits, &kinds, &members, &q, full, limit));
            // 5d33a M3: a page held as a user page cannot open a document, so gets no names
            if words {
                let posts: Vec<(String, String, String)> = posts
                    .into_iter()
                    .filter(|(t, ..)| readable.contains(t.as_str()) || reader.is_person())
                    .collect();
                docs(&posts, &q)
            } else {
                Vec::new()
            }
        }
        None => {
            partial.push("thread");
            partial.push("doc");
            Vec::new()
        }
    };

    let st2 = st.clone();
    match crate::threads::blocking(move || crate::seats::read_all(&st2)).await {
        Some(views) => groups.extend(group("seat", seats(&views, &q), limit)),
        None => partial.push("seat"),
    }
    groups.extend(group("doc", doc_items, limit));
    let andy = crate::slots::andy_items();
    groups.extend(group("andy_open", andy_open(&andy, &q, full), limit));

    let mut out = json!({"q": q, "full": full, "groups": groups});
    if !partial.is_empty() {
        out["partial"] = json!(partial);
    }
    Json(out).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_snippet_keeps_the_text_around_the_match() {
        let long = format!("{}overnight 驗收 M1{}", "前".repeat(100), "後".repeat(100));
        let s = snippet(&long, "OVERNIGHT");
        assert!(s.starts_with('…') && s.ends_with('…'), "{s}");
        assert!(s.contains("overnight 驗收 M1"), "reason: match_cut {s}");
        assert_eq!(s.chars().filter(|c| *c == '前').count(), SNIPPET_AROUND);
        assert_eq!(snippet("短 abc", "abc"), "短 abc");
    }

    #[test]
    fn nodes_and_seats_match_id_title_and_persona() {
        let tree = json!({"nodes": [
            {"id": "N-ws-search", "title": "搜尋框", "status": "開發中"},
            {"id": "T02", "title": "開席一步完成"},
        ]});
        let n = nodes(Some(&tree), "搜尋");
        assert_eq!(n.len(), 1);
        assert_eq!(
            (n[0]["id"].as_str(), n[0]["hit"].as_str()),
            (Some("N-ws-search"), Some("title"))
        );
        assert_eq!(nodes(Some(&tree), "t02")[0]["hit"], "id", "reason: id_case");
        let views = [
            json!({"seat_id": "a9136", "name": "x", "persona": {"name": "AIB2主線核心架構師"},
            "display": "AIB2主線核心架構師 · claude · dgrs", "provider": "claude", "state": "open"}),
        ];
        assert_eq!(seats(&views, "架構師")[0]["hit"], "persona");
        assert_eq!(seats(&views, "CLAUDE")[0]["hit"], "suffix");
        assert_eq!(seats(&views, "a913")[0]["hit"], "id");
        assert!(seats(&views, "codex").is_empty());
    }

    /// §18 point 4: a 待人決策 item's own words only with `full`.
    #[test]
    fn andy_open_words_are_searched_and_shown_only_in_full() {
        let mut fields = std::collections::BTreeMap::new();
        fields.insert("text".to_owned(), "要不要合 #860".to_owned());
        let items = [aib_drive::andy_open::Item {
            item: "ao_1".into(),
            item_kind: "decide".into(),
            card: "N-ws-integ-batch".into(),
            fields,
            since: "s".into(),
            done: false,
        }];
        let f = andy_open(&items, "#860", true);
        assert_eq!((f.len(), f[0]["hit"].as_str()), (1, Some("text")));
        assert!(f[0]["text"].as_str().unwrap().contains("#860"));
        assert!(
            andy_open(&items, "#860", false).is_empty(),
            "reason: words_to_user_page"
        );
        let by_card = andy_open(&items, "integ", false);
        assert_eq!(by_card[0]["hit"], "card");
        assert!(
            by_card[0].get("text").is_none(),
            "reason: words_to_user_page"
        );
    }
}
