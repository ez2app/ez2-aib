//! §23 上預覽, 能看 stage (Andy 11-18 23:2x–23:4x 「預覽一個功能 … 要能看到上下的樹狀程式,每一
//! 個支點都要處於預覽或足以支持預覽成功才能進入預覽」「用替身(假資料)撐著 可算『足以支持』」;
//! SPEC-workspace-multiseat §23; 519d0 dispatch N-pv-up): `GET /api/v1/nodes/{id}/preview-tree`
//! is the item's tree — its subtree plus everything they `needs` — with each 支點's light.
//!
//! Rough on purpose until Andy 定版 (519d0): a head that is an ancestor of a listed preview's head
//! is 已在預覽; a head, a stand-in (`stand_in` on the node) or a node already in the formal build
//! is 足以支持; anything else is 不足 and names its owner. Merging / building / updating
//! (`preview_up`), version history and not interrupting a viewer come after 定版.
use crate::guard::Caller;
use axum::extract::Path as UrlPath;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

/// The three lights, worst last.
pub const LIGHTS: [&str; 3] = ["in_preview", "enough", "short"];
/// An item's preview state (§23 4.); the 能看 stage only says the first and 可看.
pub const STATES: [&str; 12] = [
    "not_up",
    "queued",
    "merging",
    "building",
    "viewable",
    "conflict",
    "build_failed",
    "newer",
    "fixed",
    "usable",
    "released",
    "retired",
];
/// Stations that count as already in the formal build.
const FORMAL: [&str; 2] = ["已進正式", "實查通過"];

fn s<'a>(n: &'a Value, k: &str) -> &'a str {
    n.get(k).and_then(Value::as_str).unwrap_or("")
}

/// A head-looking string: 7–40 lowercase hex (never an option or a path to git).
pub fn is_head(h: &str) -> bool {
    (7..=40).contains(&h.len())
        && h.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// What the node leans on (`needs`: ids), for 7b81e's column; a string is one id.
fn needs(n: &Value) -> Vec<String> {
    match n.get("needs") {
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
        Some(Value::String(one)) if !one.is_empty() => vec![one.clone()],
        _ => Vec::new(),
    }
}

fn stand_in(n: &Value) -> bool {
    match n.get("stand_in") {
        Some(Value::Bool(b)) => *b,
        Some(Value::String(t)) => !t.trim().is_empty(),
        _ => false,
    }
}

/// One 支點's light and why.
pub fn light_of(n: &Value, in_preview: &dyn Fn(&str) -> bool) -> (&'static str, String) {
    let head = s(n, "head");
    let status = s(n, "status");
    if is_head(head) && in_preview(head) {
        return (
            LIGHTS[0],
            format!("頭 {} 已在預覽", &head[..head.len().min(9)]),
        );
    }
    if FORMAL.contains(&status) {
        return (LIGHTS[1], format!("已在正式區({status})"));
    }
    if stand_in(n) {
        return (LIGHTS[1], "替身(假資料)撐著".into());
    }
    if is_head(head) {
        return (
            LIGHTS[1],
            format!("有頭 {} 可帶上", &head[..head.len().min(9)]),
        );
    }
    (LIGHTS[2], "沒有頭".into())
}

/// The tree of `root`: its subtree, then whatever those need, each once. `None` when there is
/// no such node.
pub fn compute(tree: &Value, root: &str, in_preview: &dyn Fn(&str) -> bool) -> Option<Value> {
    let nodes: Vec<&Value> = tree.get("nodes")?.as_array()?.iter().collect();
    let by_id: BTreeMap<&str, &Value> = nodes.iter().map(|n| (s(n, "id"), *n)).collect();
    by_id.get(root)?;
    let mut kids: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for n in &nodes {
        kids.entry(s(n, "parent")).or_default().push(s(n, "id"));
    }
    // (id, how it came in)
    let mut order: Vec<(String, &'static str, usize)> = Vec::new();
    let mut seen = BTreeSet::new();
    let mut stack = vec![(root.to_owned(), "root", 0usize)];
    while let Some((id, via, depth)) = stack.pop() {
        if !seen.insert(id.clone()) {
            continue;
        }
        let Some(n) = by_id.get(id.as_str()) else {
            // a need that is not in the tree: shown as short
            order.push((id, via, depth));
            continue;
        };
        for k in kids.get(id.as_str()).into_iter().flatten().rev() {
            stack.push(((*k).to_owned(), "child", depth + 1));
        }
        for d in needs(n).into_iter().rev() {
            stack.push((d, "needs", depth + 1));
        }
        order.push((id, via, depth));
    }
    let mut worst = 0;
    let mut partial = false;
    let mut blocked = Vec::new();
    let rows: Vec<Value> = order
        .iter()
        .map(|(id, via, depth)| {
            let Some(n) = by_id.get(id.as_str()) else {
                worst = 2;
                blocked.push(id.clone());
                return json!({"id": id, "via": via, "depth": depth, "light": LIGHTS[2],
                    "reason": "不在專案樹裡", "preview_state": STATES[0]});
            };
            let (light, reason) = light_of(n, in_preview);
            let rank = LIGHTS.iter().position(|l| *l == light).unwrap_or(2);
            worst = worst.max(rank);
            let si = stand_in(n) && rank == 1;
            partial |= si;
            let mut o = Map::new();
            for k in ["id", "title", "parent", "owner", "head", "status"] {
                o.insert(k.into(), n.get(k).cloned().unwrap_or(Value::Null));
            }
            o.insert("via".into(), json!(via));
            o.insert("depth".into(), json!(depth));
            o.insert("needs".into(), json!(needs(n)));
            o.insert("light".into(), json!(light));
            o.insert("reason".into(), json!(reason));
            o.insert("stand_in".into(), json!(si));
            o.insert(
                "preview_state".into(),
                json!(if rank == 0 { STATES[4] } else { STATES[0] }),
            );
            if rank == 2 {
                blocked.push(id.clone());
                o.insert(
                    "blocker_seat".into(),
                    n.get("owner").cloned().unwrap_or(Value::Null),
                );
            }
            Value::Object(o)
        })
        .collect();
    let heads: Vec<Value> = rows
        .iter()
        .filter(|r| is_head(r["head"].as_str().unwrap_or("")))
        .map(|r| json!({"id": r["id"], "head": r["head"]}))
        .collect();
    let heads_sha256 = heads_digest(&heads);
    Some(json!({
        "root": root,
        "heads": heads,
        "heads_sha256": heads_sha256,
        "light": LIGHTS[worst],
        "ready": worst < 2,
        "partial_stand_in": partial,
        "preview_state": if worst == 0 { STATES[4] } else { STATES[0] },
        "blocked_by": blocked,
        "nodes": rows,
        "lights": LIGHTS,
        "states": STATES,
    }))
}

/// The agreement's hash (5d33a CHECK-pv-up M1): sha256 of `id\thead\n` per support point with
/// a head, in the tree's order. Any seat can move a card's head, so a merge recomputes it and a
/// different one is 409 and a new window.
pub fn heads_digest(heads: &[Value]) -> String {
    let text: String = heads
        .iter()
        .map(|h| {
            format!(
                "{}\t{}\n",
                h["id"].as_str().unwrap_or(""),
                h["head"].as_str().unwrap_or("")
            )
        })
        .collect();
    crate::preview::sha256_hex(text.as_bytes())
}

/// The heads of the previews on the list (not taken down).
fn preview_heads() -> Vec<String> {
    crate::workspace::data_file(crate::preview::REGISTRY)
        .map(|r| crate::preview::load(&r))
        .unwrap_or_default()
        .into_iter()
        .filter(|e| !e.retired && is_head(&e.head))
        .map(|e| e.head)
        .collect()
}

/// `head` is in one of `previews` (an ancestor of its head, in the repo aib-plan finds).
fn in_any(head: &str, previews: &[String], repo: Option<&std::path::Path>) -> bool {
    previews.iter().any(|p| {
        p.starts_with(head)
            || head.starts_with(p.as_str())
            || repo.is_some_and(|r| {
                std::process::Command::new("git")
                    .arg("-C")
                    .arg(r)
                    .args(["merge-base", "--is-ancestor", head, p])
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .status()
                    .is_ok_and(|s| s.success())
            })
    })
}

/// `GET /api/v1/nodes/{id}/preview-tree`.
pub async fn tree(Extension(c): Extension<Caller>, UrlPath(id): UrlPath<String>) -> Response {
    if !matches!(c, Caller::Page(_)) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"code": "page_only", "message": "the preview tree is the page's"})),
        )
            .into_response();
    }
    let got = tokio::task::spawn_blocking(move || {
        let t = crate::workspace::current_tree()?;
        let heads = preview_heads();
        let repo = crate::nodes::facts().repo;
        let in_preview = |h: &str| in_any(h, &heads, repo.as_deref());
        let mut v = compute(&t, &id, &in_preview)?;
        // 5d33a CHECK-pv-up-p23 M1: the window shows, and the hash covers, full heads
        let rp = crate::preview_up::repo().or(repo);
        crate::preview_up::expand_heads(&mut v, rp.as_deref());
        Some(v)
    })
    .await;
    match got {
        Ok(Some(v)) => Json(v).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(json!({"code": "node_not_found", "message": "no such node"})),
        )
            .into_response(),
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"code": "internal", "message": "preview tree failed"})),
        )
            .into_response(),
    }
}

/// 043dd 7a750ca6 page's shape of the same tree: lights in / ok / stub / no, `who` the owner,
/// `need` true for what came in through `needs`.
pub fn page_shape(v: &Value) -> Value {
    let tree: Vec<Value> = v["nodes"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|n| {
                    let light = match (n["light"].as_str(), n["stand_in"] == true) {
                        (Some("in_preview"), _) => "in",
                        (Some("enough"), true) => "stub",
                        (Some("enough"), false) => "ok",
                        _ => "no",
                    };
                    json!({"id": n["id"], "title": n["title"], "depth": n["depth"],
                        "light": light, "why": n["reason"], "who": n["owner"],
                        "head": n["head"], "need": n["via"] == "needs"})
                })
                .collect()
        })
        .unwrap_or_default();
    json!({"node": v["root"], "state": v["preview_state"], "ready": v["ready"],
        "blocked_by": v["blocked_by"], "tree": tree, "heads": v["heads"],
        "heads_sha256": v["heads_sha256"]})
}

#[derive(serde::Deserialize)]
pub struct UpQuery {
    node: String,
}

/// `GET /api/v1/preview_up?node=<id>`: the same tree in the page's shape.
pub async fn up_tree(
    c: Extension<Caller>,
    axum::extract::Query(q): axum::extract::Query<UpQuery>,
) -> Response {
    let r = tree(c, UrlPath(q.node)).await;
    if r.status() != StatusCode::OK {
        return r;
    }
    let Ok(bytes) = axum::body::to_bytes(r.into_body(), 4 << 20).await else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(v) => Json(page_shape(&v)).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// Why an up body is refused before the tree is read (5d33a CHECK-pv-up M1: no agreement, no
/// merge).
pub fn up_problem(body: &Value) -> Option<&'static str> {
    if !body
        .get("node")
        .and_then(Value::as_str)
        .is_some_and(|n| !n.is_empty())
    {
        return Some("node_required");
    }
    if body.get("consent") != Some(&json!(true)) {
        return Some("consent_required");
    }
    let sha = body
        .get("heads_sha256")
        .and_then(Value::as_str)
        .unwrap_or("");
    let hex64 = sha.len() == 64
        && sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    if !hex64 {
        return Some("heads_sha256_required");
    }
    None
}

/// `act preview_up {node, preview_id?, consent: true, heads_sha256}`: the built-in page a
/// person paired, after the window showed the heads. The tree is read again: not ready ⇒ 409
/// with what blocks it; other heads than the ones agreed to ⇒ 409 `heads_changed` with the new
/// list (a new window). A passed check picks the preview (asked for / the node's card / a new
/// one) and runs merge → build → place in the background, answering `merging`; a failure lands
/// in that preview's version record (`conflict` / `build_failed`).
pub async fn up(page: crate::pairing::PageAccess, body: Value) -> Response {
    let refuse = |status: StatusCode, code: &str, extra: Value| {
        let mut v = json!({"code": code});
        if let (Value::Object(m), Value::Object(e)) = (&mut v, extra) {
            m.extend(e);
        }
        (status, Json(v)).into_response()
    };
    let words = crate::workspace::gives_words(&Caller::Page(page)).await;
    if page.carried || !words || crate::preview::is_preview() {
        return refuse(
            StatusCode::FORBIDDEN,
            "preview_needs_built_in_page",
            json!({}),
        );
    }
    if let Some(code) = up_problem(&body) {
        return refuse(StatusCode::BAD_REQUEST, code, json!({}));
    }
    let node = body["node"].as_str().unwrap_or("").to_owned();
    let got = tokio::task::spawn_blocking(move || {
        let t = crate::workspace::current_tree()?;
        let heads = preview_heads();
        let repo = crate::nodes::facts().repo;
        let in_preview = |h: &str| in_any(h, &heads, repo.as_deref());
        let mut v = compute(&t, &node, &in_preview)?;
        let rp = crate::preview_up::repo().or(repo);
        crate::preview_up::expand_heads(&mut v, rp.as_deref());
        Some(v)
    })
    .await;
    let Ok(Some(v)) = got else {
        return refuse(StatusCode::NOT_FOUND, "node_not_found", json!({}));
    };
    if v["unresolved"].as_array().is_some_and(|u| !u.is_empty()) {
        return refuse(
            StatusCode::CONFLICT,
            "head_unresolved",
            json!({"unresolved": v["unresolved"]}),
        );
    }
    if v["ready"] != true {
        return refuse(
            StatusCode::CONFLICT,
            "preview_blocked",
            json!({"blocked_by": v["blocked_by"]}),
        );
    }
    if v["heads_sha256"] != body["heads_sha256"] {
        return refuse(
            StatusCode::CONFLICT,
            "heads_changed",
            json!({"heads": v["heads"], "heads_sha256": v["heads_sha256"]}),
        );
    }
    let Some(repo) = crate::preview_up::repo() else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "preview_repo_unset",
            json!({}),
        );
    };
    let node = body["node"].as_str().unwrap_or("").to_owned();
    let want = crate::preview::id_of(&body).to_owned();
    let title = v["nodes"][0]["title"].as_str().unwrap_or("").to_owned();
    let picked = tokio::task::spawn_blocking(move || pick_preview(&want, &node, &title)).await;
    let Ok(Ok(id)) = picked else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "preview_not_listed",
            json!({}),
        );
    };
    if let Some(state) = crate::preview_up::running(&id) {
        return refuse(
            StatusCode::CONFLICT,
            "preview_busy",
            json!({"preview_id": id, "state": state}),
        );
    }
    let Some(dir) = crate::preview::preview_dir(&id) else {
        return refuse(StatusCode::SERVICE_UNAVAILABLE, "no_data_dir", json!({}));
    };
    // the agreed heads with each one's line, author and card (§23d)
    let heads: Vec<Value> = v["nodes"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|n| is_head(n["head"].as_str().unwrap_or("")))
                .map(|n| json!({"id": n["id"], "head": n["head"], "title": n["title"], "owner": n["owner"]}))
                .collect()
        })
        .unwrap_or_default();
    let summary = crate::preview::clean_text(body.get("summary"));
    let kind = if body["kind"] == "feature" {
        "feature"
    } else {
        "fix"
    };
    let run_id = id.clone();
    std::thread::spawn(move || {
        crate::preview_up::run(&repo, &dir, &run_id, &heads, &summary, kind);
    });
    Json(json!({"state": STATES[2], "done": false, "preview_id": id,
        "heads_sha256": v["heads_sha256"]}))
    .into_response()
}

/// The preview an item goes into: the one asked for, else the listed one whose card is the
/// node, else a new one named after the node. Its id.
fn pick_preview(want: &str, node: &str, title: &str) -> Result<String, ()> {
    use crate::preview::{edit_list, load, registry, save, ADD, REG};
    let reg = registry().ok_or(())?;
    let _one = REG.lock().unwrap_or_else(|e| e.into_inner());
    let mut list = load(&reg);
    let live = |e: &&crate::preview::Entry| !e.retired;
    if !want.is_empty() {
        return list
            .iter()
            .filter(live)
            .find(|e| e.id == want)
            .map(|e| e.id.clone())
            .ok_or(());
    }
    if let Some(e) = list.iter().filter(live).find(|e| e.card == node) {
        return Ok(e.id.clone());
    }
    let new_id = crate::sessions::new_id("pv_").map_err(|_| ())?;
    let name = if title.is_empty() {
        node.to_owned()
    } else {
        format!("{title}({node})")
    };
    let now = crate::sessions::now_rfc3339();
    edit_list(
        &mut list,
        ADD,
        &json!({"name": name, "card": node}),
        "preview_up",
        &now,
        &new_id,
    )
    .map_err(|_| ())?;
    let dir = crate::preview::preview_dir(&new_id).ok_or(())?;
    std::fs::create_dir_all(dir).map_err(|_| ())?;
    save(&reg, list).map_err(|_| ())?;
    Ok(new_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tree_takes_the_subtree_and_its_needs_once_with_the_worst_light_on_top() {
        let t = json!({"nodes": [
            {"id": "F", "title": "功能", "owner": "a9136", "head": "aaaaaaa1"},
            {"id": "F1", "parent": "F", "owner": "043dd", "head": "bbbbbbb2", "needs": ["S"]},
            {"id": "F2", "parent": "F", "owner": "7b81e", "needs": "S"},
            {"id": "S", "owner": "b4297", "stand_in": "假清單"},
            {"id": "X", "owner": "zzzzz", "head": "ccccccc3"},
        ]});
        let inp = |h: &str| h == "aaaaaaa1";
        let v = compute(&t, "F", &inp).unwrap();
        let ids: Vec<&str> = v["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["id"].as_str().unwrap())
            .collect();
        assert_eq!(
            ids,
            ["F", "F1", "S", "F2"],
            "reason: subtree_or_needs_wrong"
        );
        let light = |id: &str| {
            v["nodes"]
                .as_array()
                .unwrap()
                .iter()
                .find(|n| n["id"] == id)
                .unwrap()["light"]
                .clone()
        };
        assert_eq!(light("F"), "in_preview");
        assert_eq!(light("F1"), "enough");
        assert_eq!(light("S"), "enough", "reason: stand_in_not_enough");
        assert_eq!(light("F2"), "short");
        assert_eq!(v["light"], "short");
        assert_eq!(v["ready"], false);
        assert_eq!(v["blocked_by"], json!(["F2"]));
        assert_eq!(v["partial_stand_in"], true);
        let f2 = v["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == "F2")
            .unwrap();
        assert_eq!(f2["blocker_seat"], "7b81e");
        // a child item brings what it leans on, not its siblings
        let v = compute(&t, "F1", &inp).unwrap();
        assert_eq!(v["nodes"].as_array().unwrap().len(), 2);
        assert_eq!(v["ready"], true);
        assert!(compute(&t, "nope", &inp).is_none());
        let full = compute(&t, "F", &inp).unwrap();
        let p = page_shape(&full);
        let lights: Vec<&str> = p["tree"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n["light"].as_str().unwrap())
            .collect();
        assert_eq!(lights, ["in", "ok", "stub", "no"]);
        assert_eq!(p["tree"][2]["need"], true);
        assert_eq!(p["tree"][2]["depth"], 2);
        assert!(!is_head("--all") && !is_head("abc") && is_head("0969c6e1"));
        // M1: the agreement is on these heads; moving one changes the hash
        let ready = compute(&t, "F1", &inp).unwrap();
        assert_eq!(ready["heads"].as_array().unwrap().len(), 1);
        let mut t2 = t.clone();
        t2["nodes"][1]["head"] = json!("bbbbbbb9");
        let moved = compute(&t2, "F1", &inp).unwrap();
        assert_ne!(
            ready["heads_sha256"], moved["heads_sha256"],
            "reason: hash_ignores_heads"
        );
        let sha = ready["heads_sha256"].as_str().unwrap();
        let ok = json!({"node": "F1", "consent": true, "heads_sha256": sha});
        assert_eq!(up_problem(&ok), None);
        let mut v = ok.clone();
        v["consent"] = json!(false);
        assert_eq!(up_problem(&v), Some("consent_required"));
        let mut v = ok;
        v.as_object_mut().unwrap().remove("heads_sha256");
        assert_eq!(
            up_problem(&v),
            Some("heads_sha256_required"),
            "reason: no_hash_merges"
        );
    }
}
