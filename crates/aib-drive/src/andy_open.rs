//! `aib-drive andy-open`: the 待人決策 list as events (S107-andyopen; DECISION-council-gaps-519d0-1117
//! R4; framework a9136 11-17 23:23). The list used to be a hand-kept file
//! (`state/drive/andy-open-<lead>.jsonl`) that lost a beat on every cleared conversation; now each
//! change is one `andy_open` event in aib-drive's own event folder
//! (`<state root>/state/aib-drive/events/YYYY-MM.jsonl`, aib-ledger events), and the list is a
//! replay of them:
//! - `open` starts an item, `update` changes some of its fields, `close` ends it;
//! - the old file's fields are renamed so the common event fields are never overwritten:
//!   `id` → `item` (the number that never changes; replay and dedupe use it), `kind` → `item_kind`,
//!   `card` stays `card` (an event that names it also puts it in the common `cards`), `text` / `rec` / `state` / `src` stay as they are, `since` is the
//!   open event's date (an imported row keeps its own);
//! - `list --json` prints the replayed items in the old file's shape (`id` `kind` `text` `card` `rec`
//!   `state` `src` `since` `done`), so the board only swaps its source and never replays in JS;
//! - `import --file` writes the old file once (an `open` per row, `imported: true` and its line
//!   number, then a `close` for a `done` row) and checks that the replay from empty equals every row;
//!   `check --file` runs only that comparison.
//!
//! Writes fail loudly (rc 2): unlike aib-drive's other events these are the list itself.

use aib_ledger::events::{self, Ctx};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::Path;

/// The event kind.
pub const KIND: &str = "andy_open";
/// The event folder's program name (aib-drive's own folder; budget reads only `drive_send`).
pub const PROG: &str = crate::send_events::PROG;
/// The item fields carried as they are.
pub const FIELDS: [&str; 4] = ["text", "rec", "state", "src"];

/// One item as the replay leaves it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub item: String,
    pub item_kind: String,
    pub card: String,
    pub fields: BTreeMap<String, String>,
    pub since: String,
    pub done: bool,
}

impl Item {
    /// The old file's shape.
    pub fn to_json(&self) -> Value {
        let mut o = Map::new();
        o.insert("id".into(), json!(self.item));
        o.insert("kind".into(), json!(self.item_kind));
        for k in FIELDS {
            o.insert(
                k.into(),
                json!(self.fields.get(k).map_or("", String::as_str)),
            );
        }
        o.insert("card".into(), json!(self.card));
        o.insert("since".into(), json!(self.since));
        if self.done {
            o.insert("done".into(), json!(true));
        }
        Value::Object(o)
    }
}

/// An item number: 1–32 ASCII letters, digits, `-` or `_`.
pub fn item_ok(s: &str) -> bool {
    (1..=32).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn text(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_owned()
}

/// The replay: items in the order they were opened, and what could not be applied (a second
/// `open` of an open item, an `update` / `close` of an unknown or closed item, a bad line).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Replay {
    pub items: Vec<Item>,
    pub problems: Vec<String>,
}

impl Replay {
    pub fn get(&self, item: &str) -> Option<&Item> {
        self.items.iter().find(|i| i.item == item)
    }
    fn get_mut(&mut self, item: &str) -> Option<&mut Item> {
        self.items.iter_mut().find(|i| i.item == item)
    }
}

/// Fold `andy_open` events in file order (other kinds are skipped). Order is the files' order,
/// never the `at` times (aib-ledger IFACE: clocks go back).
pub fn replay<'a>(evs: impl IntoIterator<Item = &'a Value>) -> Replay {
    let mut r = Replay::default();
    for (n, e) in evs.into_iter().enumerate() {
        if e.get("kind").and_then(Value::as_str) != Some(KIND) {
            continue;
        }
        let item = text(e, "item");
        let outcome = text(e, "outcome");
        if !item_ok(&item) {
            r.problems
                .push(format!("事件 {} item {item:?} 不合格", n + 1));
            continue;
        }
        let set_fields = |it: &mut Item| {
            for k in FIELDS {
                if let Some(v) = e.get(k).and_then(Value::as_str) {
                    it.fields.insert(k.to_owned(), v.to_owned());
                }
            }
            // only an event that names its card changes it: the common `cards` of an update / close
            // is the writing seat's own card, never the item's
            if let Some(c) = e.get("card").and_then(Value::as_str) {
                it.card = c.to_owned();
            }
        };
        match outcome.as_str() {
            "open" => {
                if r.get(&item).is_some_and(|i| !i.done) {
                    r.problems.push(format!("{item} 已開著又 open(略過)"));
                    continue;
                }
                let at = text(e, "at");
                let since = e
                    .get("since")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
                    .unwrap_or_else(|| at.get(5..10).unwrap_or("").to_owned());
                let mut it = Item {
                    item: item.clone(),
                    item_kind: text(e, "item_kind"),
                    card: String::new(),
                    fields: BTreeMap::new(),
                    since,
                    done: false,
                };
                set_fields(&mut it);
                r.items.retain(|i| i.item != item);
                r.items.push(it);
            }
            "update" | "close" => {
                let Some(it) = r.get_mut(&item).filter(|i| !i.done) else {
                    r.problems.push(format!("{item} 沒開著就 {outcome}(略過)"));
                    continue;
                };
                set_fields(it);
                if let Some(k) = e.get("item_kind").and_then(Value::as_str) {
                    it.item_kind = k.to_owned();
                }
                it.done = outcome == "close";
            }
            other => r.problems.push(format!("{item} outcome {other:?} 不認得")),
        }
    }
    r
}

/// Every `andy_open` event under the root, month files in name order, lines in file order.
/// A line that is not JSON is skipped and named.
pub fn read_events(root: &Path) -> Result<(Vec<Value>, Vec<String>), String> {
    let d = events::dir(root, PROG);
    let mut names: Vec<String> = match std::fs::read_dir(&d) {
        Ok(rd) => rd
            .filter_map(Result::ok)
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.len() == 13 && n.ends_with(".jsonl"))
            .collect(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(format!("{} 讀不到:{e}", d.display())),
    };
    names.sort();
    let (mut out, mut bad) = (Vec::new(), Vec::new());
    for n in names {
        let t = std::fs::read_to_string(d.join(&n)).map_err(|e| format!("{n} 讀不到:{e}"))?;
        for (i, l) in t.lines().enumerate() {
            if !l.contains(KIND) {
                continue;
            }
            match serde_json::from_str::<Value>(l) {
                Ok(v) if v.get("kind").and_then(Value::as_str) == Some(KIND) => out.push(v),
                Ok(_) => {}
                Err(_) => bad.push(format!("{n} 第 {} 行不是 JSON", i + 1)),
            }
        }
    }
    Ok((out, bad))
}

/// One event: `card` (if any) goes into the common `cards`, the item's own fields beside it.
pub fn event(
    ctx: &Ctx,
    outcome: &str,
    at_ms: i64,
    item: &str,
    own: Map<String, Value>,
    card: Option<&str>,
) -> Value {
    let mut c = ctx.clone();
    let mut extra = own;
    if let Some(card) = card {
        extra.insert("card".into(), json!(card));
        if !card.is_empty() {
            c.cards = vec![card.to_owned()];
        }
    }
    extra.insert("item".into(), json!(item));
    events::build(&c, KIND, outcome, at_ms, extra)
}

/// The rows of an old list file: `(line number, row)`. A row without a good `id` or a `kind`, a
/// repeated `id`, or a line that is not JSON is an error naming the line.
pub fn old_rows(text: &str) -> Result<Vec<(usize, Value)>, String> {
    let mut out: Vec<(usize, Value)> = Vec::new();
    for (i, l) in text.lines().enumerate() {
        if l.trim().is_empty() {
            continue;
        }
        let v: Value =
            serde_json::from_str(l).map_err(|e| format!("第 {} 行不是 JSON:{e}", i + 1))?;
        let id = text_of(&v, "id");
        if !item_ok(&id) || text_of(&v, "kind").is_empty() {
            return Err(format!("第 {} 行缺 id 或 kind", i + 1));
        }
        if out.iter().any(|(_, o)| text_of(o, "id") == id) {
            return Err(format!("第 {} 行 id {id} 重複", i + 1));
        }
        out.push((i + 1, v));
    }
    Ok(out)
}

fn text_of(v: &Value, k: &str) -> String {
    text(v, k)
}

/// The import events of the old rows: an `open` each (`imported`, `line`, its own `since`), then a
/// `close` for each `done` row.
pub fn import_events(ctx: &Ctx, rows: &[(usize, Value)], at_ms: i64) -> Vec<Value> {
    let mut out = Vec::new();
    for (line, r) in rows {
        let id = text_of(r, "id");
        let mut own = Map::new();
        own.insert("item_kind".into(), json!(text_of(r, "kind")));
        for k in FIELDS {
            own.insert(k.into(), json!(text_of(r, k)));
        }
        own.insert("since".into(), json!(text_of(r, "since")));
        own.insert("imported".into(), json!(true));
        own.insert("line".into(), json!(line));
        let card = text_of(r, "card");
        out.push(event(ctx, "open", at_ms, &id, own, Some(&card)));
        if r.get("done").and_then(Value::as_bool) == Some(true) {
            let mut own = Map::new();
            own.insert("imported".into(), json!(true));
            own.insert("line".into(), json!(line));
            out.push(event(ctx, "close", at_ms, &id, own, None));
        }
    }
    out
}

/// Replay vs the old rows: one line per difference (a missing item, a field that differs, an
/// item the file does not have). Empty = every row replays to itself.
pub fn compare(rep: &Replay, rows: &[(usize, Value)]) -> Vec<String> {
    let mut diffs = Vec::new();
    for (line, r) in rows {
        let id = text_of(r, "id");
        let Some(it) = rep.get(&id) else {
            diffs.push(format!("第 {line} 行 {id}:重播沒有"));
            continue;
        };
        let got = it.to_json();
        for k in ["kind", "text", "rec", "state", "src", "card", "since"] {
            if got[k] != json!(text_of(r, k)) {
                diffs.push(format!("第 {line} 行 {id}:{k} 不同"));
            }
        }
        let want_done = r.get("done").and_then(Value::as_bool) == Some(true);
        if it.done != want_done {
            diffs.push(format!("第 {line} 行 {id}:done 不同"));
        }
    }
    for it in &rep.items {
        if !rows.iter().any(|(_, r)| text_of(r, "id") == it.item) {
            diffs.push(format!("{}:舊檔沒有", it.item));
        }
    }
    diffs
}

pub const USAGE: &str = "usage: aib-drive andy-open list [--json] [--all]
       aib-drive andy-open open --item ID --item-kind K --text T [--rec R] [--state S] [--src S] [--card C]
       aib-drive andy-open update --item ID [--text T] [--rec R] [--state S] [--src S] [--card C] [--item-kind K]
       aib-drive andy-open close --item ID [--state S]
       aib-drive andy-open import --file OLD.jsonl [--dry-run]   (once; then checks the replay)
       aib-drive andy-open check --file OLD.jsonl                 (replay == every old row?)
";

fn flags(
    args: &[String],
    allowed: &[&str],
    bools: &[&str],
) -> Result<BTreeMap<String, String>, String> {
    let mut m = BTreeMap::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let k = a
            .strip_prefix("--")
            .ok_or_else(|| format!("多出來的參數 {a:?}"))?;
        if bools.contains(&k) {
            m.insert(k.to_owned(), String::new());
        } else if allowed.contains(&k) {
            let v = it.next().ok_or_else(|| format!("--{k} 要給值"))?;
            if m.insert(k.to_owned(), v.clone()).is_some() {
                return Err(format!("--{k} 給了兩次"));
            }
        } else {
            return Err(format!("不認得 --{k}"));
        }
    }
    Ok(m)
}

fn write_all(root: &Path, evs: &[Value], at_ms: i64) -> Result<(), String> {
    for (i, e) in evs.iter().enumerate() {
        events::append(root, PROG, e, at_ms)
            .map_err(|why| format!("第 {} 筆事件寫不進去(前面 {i} 筆已寫):{why}", i + 1))?;
    }
    Ok(())
}

/// `aib-drive andy-open …` → `(rc, stdout)`. rc 0 ok, 1 `check` / `import` found differences,
/// 2 usage, a refused change, or a write / read failure.
pub fn run(args: &[String], root: &Path, ctx: &Ctx, now_ms: i64) -> (i32, String) {
    let Some((sub, rest)) = args.split_first() else {
        return (2, USAGE.to_owned());
    };
    let fail = |why: String| (2, format!("andy-open:{why}\n"));
    let field_flags = ["item", "item-kind", "text", "rec", "state", "src", "card"];
    let (evs, bad) = match read_events(root) {
        Ok(x) => x,
        Err(why) => return fail(why),
    };
    let rep = replay(&evs);
    let warn = |out: &mut String| {
        for p in bad.iter().chain(&rep.problems) {
            out.push_str(&format!("注意:{p}\n"));
        }
    };
    match sub.as_str() {
        "list" => {
            let f = match flags(rest, &[], &["json", "all"]) {
                Ok(f) => f,
                Err(why) => return fail(why),
            };
            let shown: Vec<&Item> = rep
                .items
                .iter()
                .filter(|i| f.contains_key("all") || !i.done)
                .collect();
            if f.contains_key("json") {
                let arr: Vec<Value> = shown.iter().map(|i| i.to_json()).collect();
                return (0, format!("{}\n", Value::Array(arr)));
            }
            let mut out = String::new();
            for i in &shown {
                out.push_str(&format!(
                    "{}{} [{}] {} → {}\n",
                    i.item,
                    if i.done { "(結)" } else { "" },
                    i.item_kind,
                    i.fields.get("text").map_or("", String::as_str),
                    i.fields.get("state").map_or("", String::as_str)
                ));
            }
            if shown.is_empty() {
                out.push_str("待人決策:沒有開著的\n");
            }
            warn(&mut out);
            (0, out)
        }
        "open" | "update" | "close" => {
            let allowed: &[&str] = if sub == "close" {
                &["item", "state"]
            } else {
                &field_flags
            };
            let f = match flags(rest, allowed, &[]) {
                Ok(f) => f,
                Err(why) => return fail(why),
            };
            let item = f.get("item").cloned().unwrap_or_default();
            if !item_ok(&item) {
                return fail("--item 要 1–32 個英數、- 或 _".into());
            }
            let open_now = rep.get(&item).is_some_and(|i| !i.done);
            if sub == "open" && open_now {
                return fail(format!("{item} 已經開著(要改用 update)"));
            }
            if sub != "open" && !open_now {
                return fail(format!("{item} 沒有開著"));
            }
            if sub == "open" && (!f.contains_key("item-kind") || !f.contains_key("text")) {
                return fail("open 要 --item-kind 與 --text".into());
            }
            let mut own = Map::new();
            for (flag, key) in [
                ("item-kind", "item_kind"),
                ("text", "text"),
                ("rec", "rec"),
                ("state", "state"),
                ("src", "src"),
            ] {
                if let Some(v) = f.get(flag) {
                    own.insert(key.into(), json!(v));
                }
            }
            if sub == "update" && own.is_empty() && !f.contains_key("card") {
                return fail("update 至少要改一欄".into());
            }
            let ev = event(
                ctx,
                sub,
                now_ms,
                &item,
                own,
                f.get("card").map(String::as_str),
            );
            if let Err(why) = write_all(root, &[ev], now_ms) {
                return fail(why);
            }
            (0, format!("andy-open {sub} {item}:寫了\n"))
        }
        "import" | "check" => {
            let f = match flags(rest, &["file"], &["dry-run"]) {
                Ok(f) => f,
                Err(why) => return fail(why),
            };
            let Some(file) = f.get("file") else {
                return fail("要 --file".into());
            };
            let rows = match std::fs::read_to_string(file)
                .map_err(|e| format!("{file} 讀不到:{e}"))
                .and_then(|t| old_rows(&t))
            {
                Ok(r) => r,
                Err(why) => return fail(why),
            };
            let (rep, note) = if sub == "import" {
                if let Some(it) = rows.iter().find_map(|(_, r)| rep.get(&text_of(r, "id"))) {
                    return fail(format!("{} 已經有事件,不重匯(用 check 比對)", it.item));
                }
                let new = import_events(ctx, &rows, now_ms);
                let mut all = evs.clone();
                all.extend(new.iter().cloned());
                if !f.contains_key("dry-run") {
                    if let Err(why) = write_all(root, &new, now_ms) {
                        return fail(why);
                    }
                }
                let closes = new.len() - rows.len();
                (
                    replay(&all),
                    format!(
                        "匯入 {} 行(其中 {closes} 行已結,各加一筆 close)",
                        rows.len()
                    ),
                )
            } else {
                (rep, String::new())
            };
            let diffs = compare(&rep, &rows);
            let mut out = String::new();
            if !note.is_empty() {
                out.push_str(&note);
                out.push_str(if f.contains_key("dry-run") {
                    "(試跑,沒寫)\n"
                } else {
                    "\n"
                });
            }
            for d in &diffs {
                out.push_str(&format!("不同:{d}\n"));
            }
            out.push_str(&format!(
                "重播核對:{} 行,{} 處不同\n",
                rows.len(),
                diffs.len()
            ));
            (i32::from(!diffs.is_empty()), out)
        }
        _ => (2, USAGE.to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_928_000_000;
    /// Two rows copied from the real state/drive/andy-open-519d0.jsonl (one done, one open).
    const OLD: &str = r#"{"id": "a1", "kind": "決定", "text": "「AI 推進」按鈕全艦每天可花多少 token", "card": "S107-assist", "src": "DECISION-council-andyopen-519d0-1117.md", "since": "11-17", "rec": "先數次數、不數 token", "state": "已執行:定案並派 a9136;不用你做", "done": true}
{"id": "a25", "kind": "決定", "text": "自動推進要不要從 10 分鐘一輪改成 20 到 30 分鐘一輪", "card": "S79-live", "rec": "改。", "state": "遇事不決徵詢中", "src": "HANDOFF-519d0-1117-1045.md 建議第 1 條", "since": "11-17"}
"#;

    fn ctx() -> Ctx {
        Ctx {
            host: Some("hostx".into()),
            seat: Some("b4297".into()),
            cards: vec!["S107-andyopen".into()],
            status_item: None,
        }
    }
    fn tmp(tag: u32) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("aib-andyopen-{}-{tag}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| (*x).to_owned()).collect()
    }

    #[test]
    fn the_replay_from_empty_equals_every_old_row() {
        let rows = old_rows(OLD).unwrap();
        let evs = import_events(&ctx(), &rows, NOW);
        assert_eq!(evs.len(), 3, "reason: done_row_not_closed");
        let rep = replay(&evs);
        assert_eq!(compare(&rep, &rows), Vec::<String>::new());
        assert!(rep.get("a1").unwrap().done);
        assert!(!rep.get("a25").unwrap().done);
        assert_eq!(
            rep.get("a25").unwrap().card,
            "S79-live",
            "reason: card_lost"
        );
        assert_eq!(evs[0]["cards"], json!(["S107-assist"]));
        assert_eq!(evs[0]["imported"], true);
        assert_eq!(evs[0]["line"], 1);
    }

    #[test]
    fn the_old_fields_never_overwrite_the_common_ones() {
        let rows = old_rows(OLD).unwrap();
        let e = &import_events(&ctx(), &rows, NOW)[0];
        assert_eq!(e["kind"], KIND, "reason: kind_overwritten");
        assert_eq!(e["item"], "a1");
        assert_eq!(e["item_kind"], "決定");
        assert!(
            e["id"].as_str().unwrap().starts_with("hostx:"),
            "reason: id_overwritten"
        );
        assert_eq!(e["seat"], "b4297");
    }

    #[test]
    fn open_update_close_fold_in_file_order_and_bad_steps_are_named() {
        let c = ctx();
        let mut own = Map::new();
        own.insert("item_kind".into(), json!("決定"));
        own.insert("text".into(), json!("t1"));
        own.insert("state".into(), json!("等"));
        let o = event(&c, "open", NOW, "x1", own, Some("S1"));
        let mut own = Map::new();
        own.insert("state".into(), json!("改了"));
        let u = event(&c, "update", NOW, "x1", own, None);
        let cl = event(&c, "close", NOW, "x1", Map::new(), None);
        let stray = event(&c, "update", NOW, "x9", Map::new(), None);
        let rep = replay(&[o.clone(), u.clone()]);
        let it = rep.get("x1").unwrap();
        assert_eq!(it.fields["state"], "改了", "reason: update_not_applied");
        assert_eq!(it.fields["text"], "t1", "reason: update_wiped_fields");
        assert_eq!(it.card, "S1");
        assert_eq!(it.since, date_of(NOW));
        let rep = replay(&[o.clone(), u, cl.clone(), stray, cl, o.clone(), o]);
        assert!(!rep.get("x1").unwrap().done, "reason: reopen_ignored");
        assert_eq!(rep.problems.len(), 3, "{:?}", rep.problems);
    }

    pub(super) fn date_of(ms: i64) -> String {
        events::build(&Ctx::default(), "x", "x", ms, Map::new())["at"]
            .as_str()
            .unwrap()[5..10]
            .to_owned()
    }

    #[test]
    fn old_rows_refuse_a_repeated_id_or_a_row_without_kind() {
        let dup = format!("{}{}", OLD.lines().nth(1).unwrap(), "\n") + OLD.lines().nth(1).unwrap();
        assert!(
            old_rows(&dup).unwrap_err().contains("重複"),
            "reason: dup_id_taken"
        );
        assert!(old_rows("{\"id\": \"a\"}").unwrap_err().contains("缺"));
        assert!(old_rows("nope").is_err());
        assert_eq!(old_rows("\n\n").unwrap().len(), 0);
    }

    #[test]
    fn import_writes_once_then_list_and_check_agree_and_a_second_import_is_refused() {
        let root = tmp(line!());
        let old = root.join("old.jsonl");
        std::fs::write(&old, OLD).unwrap();
        let f = old.to_string_lossy().into_owned();
        let (rc, out) = run(
            &s(&["import", "--file", &f, "--dry-run"]),
            &root,
            &ctx(),
            NOW,
        );
        assert_eq!(rc, 0, "{out}");
        assert!(
            read_events(&root).unwrap().0.is_empty(),
            "reason: dry_run_wrote"
        );
        let (rc, out) = run(&s(&["import", "--file", &f]), &root, &ctx(), NOW);
        assert_eq!(rc, 0, "{out}");
        assert!(out.contains("0 處不同"), "{out}");
        let (rc, out) = run(&s(&["import", "--file", &f]), &root, &ctx(), NOW);
        assert_eq!(rc, 2, "reason: second_import_taken {out}");
        let (rc, out) = run(&s(&["check", "--file", &f]), &root, &ctx(), NOW);
        assert_eq!(rc, 0, "{out}");
        let (_, out) = run(&s(&["list", "--json"]), &root, &ctx(), NOW);
        let v: Value = serde_json::from_str(out.trim()).unwrap();
        assert_eq!(v.as_array().unwrap().len(), 1, "reason: closed_listed {v}");
        assert_eq!(v[0]["id"], "a25");
        let (_, out) = run(&s(&["list", "--json", "--all"]), &root, &ctx(), NOW);
        let all: Value = serde_json::from_str(out.trim()).unwrap();
        assert_eq!(all[0]["done"], true);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn check_names_a_row_that_changed_after_the_import() {
        let root = tmp(line!());
        let old = root.join("old.jsonl");
        std::fs::write(&old, OLD).unwrap();
        let f = old.to_string_lossy().into_owned();
        assert_eq!(run(&s(&["import", "--file", &f]), &root, &ctx(), NOW).0, 0);
        std::fs::write(
            &old,
            OLD.replace("\"state\": \"遇事不決徵詢中\"", "\"state\": \"定稿\""),
        )
        .unwrap();
        let (rc, out) = run(&s(&["check", "--file", &f]), &root, &ctx(), NOW);
        assert_eq!(rc, 1, "reason: diff_rc0 {out}");
        assert!(out.contains("a25:state 不同"), "{out}");
        std::fs::write(&old, OLD.replace(", \"done\": true", "")).unwrap();
        let (rc, out) = run(&s(&["check", "--file", &f]), &root, &ctx(), NOW);
        assert_eq!(rc, 1, "reason: done_diff_rc0 {out}");
        assert!(out.contains("a1:done 不同"), "{out}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn open_twice_update_closed_or_empty_are_refused() {
        let root = tmp(line!());
        let c = ctx();
        let open = s(&[
            "open",
            "--item",
            "a30",
            "--item-kind",
            "決定",
            "--text",
            "x",
            "--card",
            "S1",
        ]);
        assert_eq!(run(&open, &root, &c, NOW).0, 0);
        assert_eq!(run(&open, &root, &c, NOW).0, 2, "reason: open_twice_taken");
        assert_eq!(
            run(&s(&["update", "--item", "a30"]), &root, &c, NOW).0,
            2,
            "reason: empty_update_taken"
        );
        assert_eq!(
            run(
                &s(&["update", "--item", "a30", "--state", "s"]),
                &root,
                &c,
                NOW
            )
            .0,
            0
        );
        assert_eq!(run(&s(&["close", "--item", "a30"]), &root, &c, NOW).0, 0);
        assert_eq!(
            run(&s(&["close", "--item", "a30"]), &root, &c, NOW).0,
            2,
            "reason: close_closed_taken"
        );
        assert_eq!(
            run(
                &s(&["update", "--item", "a30", "--state", "s"]),
                &root,
                &c,
                NOW
            )
            .0,
            2
        );
        assert_eq!(
            run(
                &s(&[
                    "open",
                    "--item",
                    "bad id",
                    "--item-kind",
                    "k",
                    "--text",
                    "t"
                ]),
                &root,
                &c,
                NOW
            )
            .0,
            2
        );
        assert_eq!(
            run(
                &s(&["close", "--item", "a30", "--text", "t"]),
                &root,
                &c,
                NOW
            )
            .0,
            2
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn other_kinds_in_the_folder_are_left_alone() {
        let root = tmp(line!());
        let d = events::dir(&root, PROG);
        std::fs::create_dir_all(&d).unwrap();
        let send = events::build(&ctx(), "drive_send", "sent", NOW, Map::new());
        std::fs::write(
            d.join(events::month_file(NOW)),
            format!("{send}\nnot json andy_open\n"),
        )
        .unwrap();
        let (evs, bad) = read_events(&root).unwrap();
        assert!(evs.is_empty(), "reason: other_kind_read");
        assert_eq!(bad.len(), 1);
        let _ = std::fs::remove_dir_all(&root);
    }
}
