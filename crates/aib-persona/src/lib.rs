// SPDX-License-Identifier: Apache-2.0
//! Persona evidence ledger (`<candidate root>/<persona code>/evidence.jsonl`).
//!
//! The ledger is append-only. A line is named by where it is and what it holds:
//! `e<line number>-<first 16 hex of sha256(raw line bytes)>`. Nothing is ever rewritten; an
//! entry is withdrawn by appending a void line that names it. A line whose bytes no longer match
//! its id is a sign the file was edited by hand, and void refuses to act on it.
//!
//! This ledger is **not a security boundary**: any program running as the same user can write
//! the file. `by` is self-reported. A persona card only takes effect with the owner's approval
//! receipt; the ledger only collects candidates. What this crate checks is that a well-meaning
//! writer points at something that exists, and can take back a mistake.

pub mod pin;
pub mod pointer;
pub mod sha256;

use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const EVIDENCE_V1: &str = "aib-persona-evidence/v1";
pub const EVIDENCE_V2: &str = "aib-persona-evidence/v2";
pub const VOID_V1: &str = "aib-persona-evidence-void/v1";
/// Same cap as the Python ledger (ledgers.MAX_BYTES).
pub const MAX_LEDGER_BYTES: usize = 1_000_000;
/// SOP §3.1: a trait needs at least this many groups before it can go on a card.
pub const MIN_GROUPS_FOR_CARD: usize = 2;

#[derive(Debug, Clone, PartialEq)]
pub enum Kind {
    Evidence,
    Void,
}

#[derive(Debug, Clone)]
pub struct Line {
    /// 1-based physical line number in the file.
    pub no: usize,
    pub id: String,
    pub kind: Kind,
    pub v: Map<String, Value>,
}

#[derive(Debug, Clone, Default)]
pub struct Ledger {
    pub lines: Vec<Line>,
    /// The file did not end in a newline: the last line was never finished and is not counted.
    pub torn_tail: bool,
    /// Physical line number the next append gets.
    pub next_no: usize,
}

/// `e<no>-<sha16>` for a raw line (without its newline).
pub fn line_id(no: usize, raw: &[u8]) -> String {
    format!("e{no}-{}", &sha256::hex(raw)[..16])
}

/// Split an id back into its line number; `None` if it is not shaped like one.
pub fn id_line_no(id: &str) -> Option<usize> {
    let rest = id.strip_prefix('e')?;
    let (no, hash) = rest.split_once('-')?;
    if hash.len() != 16
        || !hash
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        return None;
    }
    if no.is_empty()
        || no.len() > 9
        || !no.bytes().all(|b| b.is_ascii_digit())
        || no.starts_with('0')
    {
        return None;
    }
    no.parse().ok()
}

/// Parse a whole ledger. Any line that is not a JSON object with a known schema makes the whole
/// ledger unusable (fail closed, as the Python reader does). Blank lines are skipped but keep
/// their number.
pub fn parse(bytes: &[u8]) -> Result<Ledger, String> {
    if bytes.len() > MAX_LEDGER_BYTES {
        return Err(format!(
            "ledger over {MAX_LEDGER_BYTES} bytes; refuse to read"
        ));
    }
    let mut out = Ledger::default();
    let segs: Vec<&[u8]> = bytes.split(|b| *b == b'\n').collect();
    let n = segs.len();
    for (i, raw) in segs.into_iter().enumerate() {
        let no = i + 1;
        if i + 1 == n {
            // after the last newline: empty when the file ends cleanly
            if !raw.is_empty() {
                out.torn_tail = true;
            }
            out.next_no = no;
            break;
        }
        if raw.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let v: Value =
            serde_json::from_slice(raw).map_err(|e| format!("line {no} corrupt: {e}"))?;
        let Value::Object(v) = v else {
            return Err(format!("line {no} not an object"));
        };
        let kind = match v.get("schema").and_then(Value::as_str) {
            Some(EVIDENCE_V1) | Some(EVIDENCE_V2) => Kind::Evidence,
            Some(VOID_V1) => Kind::Void,
            other => return Err(format!("line {no}: unknown schema {other:?}")),
        };
        out.lines.push(Line {
            no,
            id: line_id(no, raw),
            kind,
            v,
        });
    }
    if out.next_no == 0 {
        out.next_no = 1;
    }
    Ok(out)
}

impl Ledger {
    /// Evidence id → the void line that withdrew it (first one wins).
    pub fn voided(&self) -> BTreeMap<String, &Line> {
        let mut m = BTreeMap::new();
        for l in self.lines.iter().filter(|l| l.kind == Kind::Void) {
            if let Some(t) = l.v.get("void_of").and_then(Value::as_str) {
                m.entry(t.to_string()).or_insert(l);
            }
        }
        m
    }

    pub fn by_no(&self, no: usize) -> Option<&Line> {
        self.lines.iter().find(|l| l.no == no)
    }
}

// ------------------------------------------------------------------ field checks

/// `^(?!\.{1,2}$)[A-Za-z0-9._-]{1,64}$` — the Python ledgers._CODE_RE.
pub fn persona_code_ok(c: &str) -> bool {
    !c.is_empty()
        && c.len() <= 64
        && c != "."
        && c != ".."
        && c.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

/// `^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$` — the Python gates.LANE_RE.
pub fn lane_ok(l: &str) -> bool {
    let b = l.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|c| c.is_ascii_alphanumeric() || b"._/-".contains(c))
}

/// YYYY-MM-DD and a real calendar date (2026-99-99 and 2026-02-30 are refused).
pub fn day_ok(d: &str) -> bool {
    let b = d.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return false;
    }
    let num = |s: &str| -> Option<u32> {
        if s.bytes().all(|c| c.is_ascii_digit()) {
            s.parse().ok()
        } else {
            None
        }
    };
    let (Some(y), Some(m), Some(dd)) = (num(&d[..4]), num(&d[5..7]), num(&d[8..])) else {
        return false;
    };
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let days = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => return false,
    };
    y >= 2000 && (1..=days).contains(&dd)
}

/// `seat:<code>` or `human:<name>` — who says they wrote the line. Self-reported.
pub fn by_ok(b: &str) -> bool {
    let Some((k, v)) = b.split_once(':') else {
        return false;
    };
    matches!(k, "seat" | "human")
        && !v.is_empty()
        && v.len() <= 32
        && v.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
}

/// Every `q_<16 hex>` in a text.
pub fn dm_ids_in(text: &str) -> Vec<String> {
    let b = text.as_bytes();
    let mut out = vec![];
    let mut i = 0;
    while i + 18 <= b.len() {
        let before_ok = i == 0 || !b[i - 1].is_ascii_alphanumeric();
        let hex_ok = b[i..].starts_with(b"q_")
            && b[i + 2..i + 18]
                .iter()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase());
        let after_ok = i + 18 == b.len() || !b[i + 18].is_ascii_alphanumeric();
        if before_ok && hex_ok && after_ok {
            out.push(text[i..i + 18].to_string());
            i += 18;
        } else {
            i += 1;
        }
    }
    out
}

// ------------------------------------------------------------------ void

#[derive(Debug, PartialEq)]
pub enum VoidPlan {
    /// Append this void line.
    Write(Value),
    /// Already withdrawn: nothing to write, here is the void line that did it.
    Already(String),
}

/// Decide a void inside the writer's lock. `dm_exists` answers whether a `q_` id is in the dm
/// queue. Rules (a63ab 11-10 09:14): by + why required; only an earlier evidence line of this
/// ledger whose bytes still match the id; never a void line; voiding a line another writer wrote
/// (or a line with no `by`) needs `why` to cite a dm id that exists.
pub fn plan_void(
    led: &Ledger,
    persona_code: &str,
    id: &str,
    by: &str,
    why: &str,
    at_ms: u64,
    dm_exists: &dyn Fn(&str) -> bool,
) -> Result<VoidPlan, String> {
    if !by_ok(by) {
        return Err(format!("bad --by {by:?} (seat:<code> or human:<name>)"));
    }
    let why = why.trim();
    if why.chars().count() < 4 {
        return Err("--why is required (at least 4 characters)".into());
    }
    let no = id_line_no(id).ok_or_else(|| format!("bad id {id:?} (e<line>-<16 hex>)"))?;
    let target = led
        .by_no(no)
        .ok_or_else(|| format!("no line {no} in this ledger"))?;
    if target.id != id {
        return Err(format!(
            "line {no} is now {} — the file changed since that id was read",
            target.id
        ));
    }
    if target.kind != Kind::Evidence {
        return Err(format!("{id} is a void line; a void cannot be voided"));
    }
    if let Some(v) = led.voided().get(id) {
        return Ok(VoidPlan::Already(v.id.clone()));
    }
    let owner = target.v.get("by").and_then(Value::as_str);
    if owner != Some(by) {
        let cited: Vec<String> = dm_ids_in(why);
        if !cited.iter().any(|q| dm_exists(q)) {
            return Err(format!(
                "{id} was written by {} — voiding another writer's line needs --why to cite a dm id (q_…) that exists",
                owner.unwrap_or("(no by: written before by was recorded)")
            ));
        }
    }
    Ok(VoidPlan::Write(json!({
        "schema": VOID_V1,
        "persona_code": persona_code,
        "void_of": id,
        "why": why.chars().take(400).collect::<String>(),
        "by": by,
        "at_ms": at_ms,
    })))
}

// ------------------------------------------------------------------ groups

/// SOP §3.1 group count, as gates.count_groups with three changes (DECISION 11-10):
/// voided lines are skipped, the day must be a real date, and one pointer counts once (in the
/// first (lane, day) it appears in) so the same commit filed under two lanes is one group.
pub fn groups(led: &Ledger) -> Value {
    let voided = led.voided();
    let mut keys: Vec<(String, String)> = vec![];
    let mut seen_ptr: BTreeSet<String> = BTreeSet::new();
    let (mut basis, mut rejected) = (vec![], vec![]);
    let mut evidence = 0usize;
    for l in led.lines.iter().filter(|l| l.kind == Kind::Evidence) {
        evidence += 1;
        if voided.contains_key(&l.id) {
            continue;
        }
        let s = |k: &str| l.v.get(k).and_then(Value::as_str).unwrap_or("");
        let (lane, day, ptr) = (s("lane"), s("day"), s("pointer").trim());
        let kind = match s("schema") {
            EVIDENCE_V2 => pointer::Kind::from_stored(s("pointer_kind")),
            _ => pointer::legacy_kind(ptr),
        };
        let why = if !lane_ok(lane) {
            Some("bad_lane")
        } else if !day_ok(day) {
            Some("bad_day")
        } else if kind.is_none() {
            Some("pointer_not_verifiable")
        } else {
            None
        };
        if let Some(why) = why {
            rejected.push(json!({"id": l.id, "why": why}));
            continue;
        }
        // the real path stands for the file; a line in it is its own pointer (043dd #627 M2:
        // two lines of one ledger are two events, as the v1 key `path:line` already had it)
        let ptr_key = match (
            l.v.get("resolved").and_then(Value::as_str),
            l.v.get("line_no").and_then(Value::as_u64),
        ) {
            (Some(r), Some(n)) => format!("{r}:{n}"),
            (Some(r), None) => r.to_string(),
            // `dm:q_X` and `q_X` are one dm (ebb36 #627 M1)
            (None, _) => pointer::dm_alias(ptr).unwrap_or(ptr).to_string(),
        };
        if !seen_ptr.insert(ptr_key) {
            rejected.push(json!({"id": l.id, "why": "duplicate_pointer"}));
            continue;
        }
        let key = (lane.to_string(), day.to_string());
        if !keys.contains(&key) {
            keys.push(key);
            basis.push(json!({
                "lane": lane, "day": day, "first_pointer": ptr,
                "kind": kind.map(|k| k.name()), "id": l.id,
            }));
        }
    }
    json!({
        "groups": keys.len(),
        "basis": basis,
        "rejected": rejected,
        "card_eligible_by_count": keys.len() >= MIN_GROUPS_FOR_CARD,
        "evidence_count": evidence,
        "voided_count": voided.len(),
        "torn_tail": led.torn_tail,
        "counting_key": "lane+day",
        "min_groups_for_card": MIN_GROUPS_FOR_CARD,
    })
}

/// One output row for `list`: the stored fields plus `id`, `line` and, when withdrawn, `voided_by`.
pub fn list_row(l: &Line, voided: &BTreeMap<String, &Line>) -> Value {
    let mut m = l.v.clone();
    m.insert("id".into(), json!(l.id));
    m.insert("line".into(), json!(l.no));
    if let Some(v) = voided.get(&l.id) {
        m.insert("voided_by".into(), json!(v.id));
    }
    Value::Object(m)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(lane: &str, day: &str, ptr: &str, by: Option<&str>) -> String {
        let mut v = json!({"schema": EVIDENCE_V1, "persona_code": "p1", "lane": lane,
                           "day": day, "pointer": ptr, "note": "", "at_ms": 1});
        if let Some(b) = by {
            v["by"] = json!(b);
        }
        serde_json::to_string(&v).unwrap_or_default()
    }

    fn led(lines: &[String]) -> Ledger {
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        parse(text.as_bytes()).expect("parse")
    }

    #[test]
    fn ids_name_the_line_and_its_bytes() {
        let a = ev("x", "2026-11-09", "deadbee", None);
        let l = led(&[a.clone(), a.clone()]);
        assert_eq!(l.lines.len(), 2);
        // same bytes, different lines: different ids
        assert_ne!(l.lines[0].id, l.lines[1].id);
        assert_eq!(l.lines[0].id, line_id(1, a.as_bytes()));
        assert_eq!(id_line_no(&l.lines[1].id), Some(2));
        assert_eq!(l.next_no, 3);
        for bad in [
            "e0-0123456789abcdef",
            "e1-0123456789ABCDEF",
            "e1-0123",
            "x1-0123456789abcdef",
            "e01-0123456789abcdef",
        ] {
            assert_eq!(id_line_no(bad), None, "{bad}");
        }
    }

    #[test]
    fn an_unfinished_last_line_is_not_counted() {
        let a = ev("x", "2026-11-09", "deadbee", None);
        let text = format!("{a}\n{{\"schema\":\"aib-persona-evidence/v1\"");
        let l = parse(text.as_bytes()).expect("torn tail is not corrupt");
        assert!(l.torn_tail);
        assert_eq!(l.lines.len(), 1);
        // a finished line that is not json is still corrupt
        assert!(parse(b"{nope}\n").is_err());
        assert!(parse(b"{\"schema\":\"other/v1\"}\n").is_err());
        assert!(parse(b"[1]\n").is_err());
    }

    #[test]
    fn blank_lines_keep_their_numbers() {
        let a = ev("x", "2026-11-09", "deadbee", None);
        let l = led(&[String::new(), a.clone()]);
        assert_eq!(l.lines[0].no, 2);
        assert_eq!(l.lines[0].id, line_id(2, a.as_bytes()));
    }

    #[test]
    fn real_dates_only() {
        for ok in ["2026-11-09", "2024-02-29", "2026-12-31"] {
            assert!(day_ok(ok), "{ok}");
        }
        for bad in [
            "2026-99-99",
            "2026-02-29",
            "2026-11-31",
            "2026-11-9",
            "20261109",
            "1999-01-01",
            "2026-1a-09",
        ] {
            assert!(!day_ok(bad), "{bad}");
        }
    }

    #[test]
    fn one_pointer_counts_once_across_lanes() {
        let l = led(&[
            ev("a", "2026-11-09", "deadbee", None),
            ev("b", "2026-11-09", "deadbee", None),
        ]);
        let g = groups(&l);
        assert_eq!(g["groups"], 1, "{g}");
        assert_eq!(g["rejected"][0]["why"], "duplicate_pointer", "{g}");
    }

    /// ebb36 #627 M1: `q_X` in one lane and `dm:q_X` in another are one dm.
    #[test]
    fn a_dm_id_counts_once_with_or_without_its_prefix() {
        let mut bare: Value = json!({"schema": EVIDENCE_V2, "persona_code": "p1", "lane": "a",
            "day": "2026-11-09", "pointer": "q_00000000000000aa", "pointer_kind": "dm", "at_ms": 1});
        let l = led(&[
            serde_json::to_string(&bare).unwrap_or_default(),
            ev("b", "2026-11-10", "dm:q_00000000000000aa", None),
        ]);
        let g = groups(&l);
        assert_eq!(g["groups"], 1, "{g}");
        assert_eq!(g["rejected"][0]["why"], "duplicate_pointer", "{g}");
        bare["lane"] = json!("c");
        bare["pointer"] = json!("q_00000000000000bb");
        let l = led(&[
            serde_json::to_string(&bare).unwrap_or_default(),
            ev("b", "2026-11-10", "dm:q_00000000000000aa", None),
        ]);
        assert_eq!(groups(&l)["groups"], 2);
    }

    /// 043dd #627 M2: two lines of one file are two pointers; the same line twice is one.
    #[test]
    fn two_lines_of_one_file_are_two_pointers() {
        let v2 = |lane: &str, day: &str, line: u64| {
            serde_json::to_string(&json!({
                "schema": EVIDENCE_V2, "persona_code": "p1", "lane": lane, "day": day,
                "pointer": format!("~/x/REVIEW.md:{line}"), "pointer_kind": "file_line",
                "resolved": "/h/x/REVIEW.md", "line_no": line, "by": "seat:1ced7",
                "note": "", "at_ms": 1,
            }))
            .unwrap_or_default()
        };
        let g = groups(&led(&[v2("a", "2026-11-09", 3), v2("b", "2026-11-10", 40)]));
        assert_eq!(g["groups"], 2, "{g}");
        let g = groups(&led(&[v2("a", "2026-11-09", 3), v2("b", "2026-11-10", 3)]));
        assert_eq!(g["groups"], 1, "{g}");
        assert_eq!(g["rejected"][0]["why"], "duplicate_pointer", "{g}");
    }

    #[test]
    fn a_fake_date_is_not_a_group() {
        let l = led(&[
            ev("a", "2026-11-09", "deadbee", None),
            ev("b", "2026-99-99", "cafef00d", None),
        ]);
        let g = groups(&l);
        assert_eq!(g["groups"], 1, "{g}");
        assert_eq!(g["card_eligible_by_count"], false);
    }

    #[test]
    fn a_voided_line_leaves_the_count() {
        let a = ev("a", "2026-11-09", "deadbee", Some("seat:1ced7"));
        let b = ev("b", "2026-11-09", "cafef00d", Some("seat:1ced7"));
        let l = led(&[a.clone(), b.clone()]);
        assert_eq!(groups(&l)["groups"], 2);
        let id = l.lines[1].id.clone();
        let plan =
            plan_void(&l, "p1", &id, "seat:1ced7", "wrong lane", 5, &|_| false).expect("plan");
        let VoidPlan::Write(v) = plan else {
            panic!("expected a write")
        };
        let l2 = led(&[a, b, serde_json::to_string(&v).unwrap_or_default()]);
        let g = groups(&l2);
        assert_eq!(g["groups"], 1, "{g}");
        assert_eq!(g["voided_count"], 1);
        assert_eq!(g["evidence_count"], 2);
        // voiding it again writes nothing and names the first void
        let again =
            plan_void(&l2, "p1", &id, "seat:1ced7", "wrong lane", 6, &|_| false).expect("again");
        assert_eq!(again, VoidPlan::Already(l2.lines[2].id.clone()));
    }

    #[test]
    fn void_refusals() {
        let a = ev("a", "2026-11-09", "deadbee", Some("seat:aaaaa"));
        let legacy = ev("b", "2026-11-09", "cafef00d", None);
        let l = led(&[a, legacy]);
        let (mine, old) = (l.lines[0].id.clone(), l.lines[1].id.clone());
        let no = |_: &str| false;
        let yes = |q: &str| q == "q_0123456789abcdef";
        // missing why / bad by
        assert!(plan_void(&l, "p1", &mine, "seat:aaaaa", " ", 1, &no).is_err());
        assert!(plan_void(&l, "p1", &mine, "aaaaa", "oops", 1, &no).is_err());
        // another writer's line, or a line with no by: needs an existing dm id in why
        assert!(plan_void(&l, "p1", &mine, "seat:bbbbb", "not mine", 1, &no).is_err());
        assert!(plan_void(
            &l,
            "p1",
            &old,
            "seat:bbbbb",
            "per q_0123456789abcdef",
            1,
            &no
        )
        .is_err());
        assert!(plan_void(
            &l,
            "p1",
            &old,
            "seat:bbbbb",
            "per q_0123456789abcdef",
            1,
            &yes
        )
        .is_ok());
        assert!(plan_void(&l, "p1", &mine, "seat:aaaaa", "my own mistake", 1, &no).is_ok());
        // an id whose bytes do not match, a line that does not exist
        let wrong = format!("e1-{}", "0".repeat(16));
        assert!(plan_void(&l, "p1", &wrong, "seat:aaaaa", "my own mistake", 1, &no).is_err());
        assert!(plan_void(
            &l,
            "p1",
            "e9-0123456789abcdef",
            "seat:aaaaa",
            "my own mistake",
            1,
            &no
        )
        .is_err());
    }

    #[test]
    fn a_void_line_cannot_be_voided() {
        let a = ev("a", "2026-11-09", "deadbee", Some("seat:aaaaa"));
        let l = led(std::slice::from_ref(&a));
        let VoidPlan::Write(v) = plan_void(
            &l,
            "p1",
            &l.lines[0].id,
            "seat:aaaaa",
            "dup line",
            1,
            &|_| false,
        )
        .expect("plan") else {
            panic!("expected a write")
        };
        let l2 = led(&[a, serde_json::to_string(&v).unwrap_or_default()]);
        let vid = l2.lines[1].id.clone();
        assert!(plan_void(&l2, "p1", &vid, "seat:aaaaa", "undo it", 1, &|_| false).is_err());
    }

    #[test]
    fn dm_ids_are_found_whole() {
        assert_eq!(
            dm_ids_in("per q_0123456789abcdef."),
            vec!["q_0123456789abcdef"]
        );
        assert!(dm_ids_in("xq_0123456789abcdef").is_empty());
        assert!(dm_ids_in("q_0123456789abcdef0").is_empty());
        assert!(dm_ids_in("q_0123456789ABCDEF").is_empty());
    }

    #[test]
    fn field_checks() {
        assert!(persona_code_ok("75kbgj") && !persona_code_ok("..") && !persona_code_ok("a/b"));
        assert!(lane_ok("fix/a-b_1.2") && !lane_ok("-x") && !lane_ok("a b"));
        assert!(by_ok("seat:1ced7") && by_ok("human:andy") && !by_ok("1ced7") && !by_ok("seat:"));
    }
}
