// SPDX-License-Identifier: Apache-2.0
//! S73a `aib-slice sweep` (519d0 DESIGN-S73-merge-sweep, Andy 11-14): before any merge, list every
//! branch that may still need merging and put it in one of six classes. Read-only: it fetches,
//! reads git, reads open PRs through `gh api` (GET only) and reads the review / receipt files in
//! the pending folder. It never pushes, opens a PR or deletes a tree.
//!
//! The output is one JSON object, `aib-slice-sweep.v1`, fixed by `schema/sweep.v1.json`
//! ([`SCHEMA`]). Readers (f1fae S67b / S73b / S73e) code against that file; [`validate`] checks
//! an output against it, and the tests make a missing or an extra field red.
//!
//! Nothing here is a gate by itself: reviews and receipts are same-user files (GUIDE same-user
//! threat model). `--gate` only stops a merge script that chose to ask.

use serde_json::{json, Map, Value};

pub mod scan;

/// The output contract (JSON Schema, the subset [`validate`] reads).
pub const SCHEMA: &str = include_str!("../schema/sweep.v1.json");
pub const SCHEMA_ID: &str = "aib-slice-sweep.v1";

#[derive(Debug, Clone, PartialEq)]
pub struct OpenPr {
    pub number: u64,
    pub head_ref: String,
    pub head: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PrInfo {
    pub number: u64,
    pub open: bool,
    pub merged: bool,
}

/// First line of a REVIEW-*.md: `結論: X 頭: <40> H/M/L/N: a/b/c/d …`
#[derive(Debug, Clone, PartialEq)]
pub struct Review {
    pub head: String,
    pub verdict: String,
    pub h: u32,
    pub m: u32,
    pub l: u32,
    pub n: u32,
    pub file: String,
    pub at_head: bool,
}

impl Review {
    pub fn passed(&self) -> bool {
        self.verdict == "通過" && self.h == 0 && self.m == 0
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Receipt {
    pub head: String,
    pub file: String,
    /// `xxx_rc=N` / `STEP xxx rc=N` keys that are not 0
    pub nonzero: Vec<String>,
    pub at_head: bool,
}

impl Receipt {
    pub fn green(&self) -> bool {
        self.nonzero.is_empty()
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Branch {
    pub branch: String,
    pub seat: Option<String>,
    pub head: String,
    pub local: bool,
    pub remote: Option<String>,
    pub worktree: Option<String>,
    pub dirty: Option<bool>,
    pub last_commit_at: i64,
    pub age_h: i64,
    pub unique: usize,
    pub unique_not_in_prs: usize,
    pub in_prs: Vec<u64>,
    pub pushed_to: Vec<String>,
    pub pr: Option<PrInfo>,
    pub review: Option<Review>,
    pub receipt: Option<Receipt>,
    pub stacked_on: Vec<String>,
    pub conflicts_with: Vec<u64>,
    /// reason from `<pending>/sweep-ack.txt` (the author gave it up / it went elsewhere)
    pub acked: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub generated_at: i64,
    pub main: String,
    pub errors: Vec<String>,
    pub open_prs: Vec<OpenPr>,
    pub branches: Vec<Branch>,
}

/// The six classes of DESIGN-S73 (number, name). Checked top to bottom.
pub fn classify(b: &Branch, hours: i64) -> (u8, &'static str, String) {
    let pr_open = b.pr.as_ref().is_some_and(|p| p.open);
    let review_at = b.review.as_ref().filter(|r| r.at_head);
    let receipt_at = b.receipt.as_ref().filter(|r| r.at_head);
    if b.unique_not_in_prs == 0 {
        let why = if b.unique == 0 {
            "all in main".to_owned()
        } else {
            format!("all in open PR {}", nums(&b.in_prs))
        };
        return (5, "merged", why);
    }
    if let Some(a) = &b.acked {
        return (6, "stale", format!("ack: {a}"));
    }
    if pr_open && !b.conflicts_with.is_empty() {
        return (
            3,
            "fix",
            format!("conflicts with open PR {}", nums(&b.conflicts_with)),
        );
    }
    if let Some(r) = review_at.filter(|r| r.h + r.m > 0) {
        return (3, "fix", format!("review at head has H/M {}/{}", r.h, r.m));
    }
    if let Some(r) = receipt_at.filter(|r| !r.green()) {
        return (
            3,
            "fix",
            format!("receipt at head not green: {}", r.nonzero.join(",")),
        );
    }
    let reviewed = review_at.is_some_and(Review::passed);
    let green = receipt_at.is_some_and(Receipt::green);
    if reviewed && green && !b.pushed_to.is_empty() && b.conflicts_with.is_empty() && pr_open {
        return (
            1,
            "merge_first",
            "reviewed + green at head, PR open".to_owned(),
        );
    }
    if review_at.is_some() || receipt_at.is_some() {
        if b.pushed_to.is_empty() {
            return (
                2,
                "push_or_pr",
                "review / receipt at head but not pushed".to_owned(),
            );
        }
        if !pr_open {
            return (
                2,
                "push_or_pr",
                "review / receipt at head but no open PR".to_owned(),
            );
        }
    }
    if b.age_h > hours && !pr_open {
        return (
            6,
            "stale",
            format!("no commit for {}h and no open PR", b.age_h),
        );
    }
    (
        4,
        "in_progress",
        "no review / receipt at head yet".to_owned(),
    )
}

fn nums(n: &[u64]) -> String {
    n.iter()
        .map(|x| format!("#{x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Classes `--gate` stops on.
pub fn blocks(class: u8) -> bool {
    matches!(class, 1..=3)
}

/// The seat code at the end of a branch name: `…-<5 hex>` or `…-<5 hex>-<4 digits>`.
pub fn seat_of(branch: &str) -> Option<String> {
    let last = branch.rsplit('/').next().unwrap_or(branch);
    let parts: Vec<&str> = last.split('-').collect();
    let hex5 = |s: &str| s.len() == 5 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'));
    let digits4 = |s: &str| s.len() == 4 && s.bytes().all(|c| c.is_ascii_digit());
    let n = parts.len();
    if n >= 3 && digits4(parts[n - 1]) && hex5(parts[n - 2]) {
        return Some(parts[n - 2].to_owned());
    }
    if n >= 2 && hex5(parts[n - 1]) {
        return Some(parts[n - 1].to_owned());
    }
    None
}

fn is_sha40(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

/// `結論: X 頭: <40> H/M/L/N: a/b/c/d …` ⇒ (verdict, head, h, m, l, n). Anything else = None.
pub fn parse_review_line(line: &str) -> Option<(String, String, [u32; 4])> {
    let rest = line.trim().strip_prefix("結論:")?;
    let (verdict, rest) = rest.split_once("頭:")?;
    let rest = rest.trim_start();
    let head = rest.get(..40)?;
    if !is_sha40(head) {
        return None;
    }
    let (_, counts) = rest.split_once("H/M/L/N:")?;
    let counts = counts.split_whitespace().next()?;
    let v: Vec<u32> = counts
        .split('/')
        .map(|x| x.parse::<u32>().ok())
        .collect::<Option<Vec<_>>>()?;
    let [h, m, l, n] = v.as_slice() else {
        return None;
    };
    Some((verdict.trim().to_owned(), head.to_owned(), [*h, *m, *l, *n]))
}

/// Heads a receipt names in its first five lines (`head <40>`, `HEAD <40>`, `頭 <40>`, …).
pub fn receipt_heads(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in text.lines().take(5) {
        for tok in line.split(|c: char| c.is_whitespace() || c == '=' || c == ':') {
            if is_sha40(tok) && !out.iter().any(|h| h == tok) {
                out.push(tok.to_owned());
            }
        }
    }
    out
}

/// Keys of a receipt whose rc is not 0: `name_rc=N` and `STEP name rc=N`. Not counted: `KNIFE`
/// lines (a knife biting is rc 101 by design), `head_rc` / `base_rc` (red tests rerun at head
/// and at main for comparison), and `py_suite_rc` when the receipt has that main comparison.
pub fn receipt_nonzero(text: &str) -> Vec<String> {
    let compared_with_main = text.contains("base_rc=");
    let mut out = Vec::new();
    for line in text.lines() {
        if line.trim_start().starts_with("KNIFE") {
            continue;
        }
        let toks: Vec<&str> = line
            .split(|c: char| c.is_whitespace() || c == ';')
            .filter(|t| !t.is_empty())
            .collect();
        for (i, t) in toks.iter().enumerate() {
            let Some((key, val)) = t.split_once('=') else {
                continue;
            };
            let key = if key == "rc" {
                match (i.checked_sub(2).map(|j| toks[j]), i.checked_sub(1)) {
                    (Some("STEP"), Some(j)) => toks[j],
                    _ => continue,
                }
            } else if let Some(k) = key.strip_suffix("_rc") {
                if k.is_empty() {
                    continue;
                }
                key
            } else {
                continue;
            };
            if matches!(key, "head_rc" | "base_rc") || (key == "py_suite_rc" && compared_with_main)
            {
                continue;
            }
            let digits: String = val.chars().take_while(char::is_ascii_digit).collect();
            match digits.parse::<i64>() {
                Ok(0) => {}
                Ok(_) => {
                    if !out.iter().any(|k| k == key) {
                        out.push(key.to_owned());
                    }
                }
                Err(_) => {}
            }
        }
    }
    out
}

fn opt<T, F: Fn(&T) -> Value>(v: &Option<T>, f: F) -> Value {
    v.as_ref().map_or(Value::Null, f)
}

/// The report as `aib-slice-sweep.v1`.
pub fn to_json(r: &Report, hours: i64) -> Value {
    let branches: Vec<Value> = r
        .branches
        .iter()
        .map(|b| {
            let (class, name, why) = classify(b, hours);
            json!({
                "branch": b.branch,
                "seat": b.seat,
                "head": b.head,
                "local": b.local,
                "remote": b.remote,
                "worktree": b.worktree,
                "dirty": b.dirty,
                "last_commit_at": b.last_commit_at,
                "age_h": b.age_h,
                "unique": b.unique,
                "unique_not_in_prs": b.unique_not_in_prs,
                "in_prs": b.in_prs,
                "pushed_to": b.pushed_to,
                "pr": opt(&b.pr, |p| json!({"number": p.number, "state": if p.open { "open" } else { "closed" }, "merged": p.merged})),
                "review": opt(&b.review, |x| json!({"head": x.head, "verdict": x.verdict, "h": x.h, "m": x.m, "l": x.l, "n": x.n, "file": x.file, "at_head": x.at_head})),
                "receipt": opt(&b.receipt, |x| json!({"head": x.head, "file": x.file, "nonzero": x.nonzero, "green": x.green(), "at_head": x.at_head})),
                "stacked_on": b.stacked_on,
                "conflicts_with": b.conflicts_with,
                "class": class,
                "class_name": name,
                "why": why,
            })
        })
        .collect();
    json!({
        "schema": SCHEMA_ID,
        "generated_at": r.generated_at,
        "main": r.main,
        "complete": r.errors.is_empty(),
        "errors": r.errors,
        "open_prs": r.open_prs.iter().map(|p| json!({"number": p.number, "head_ref": p.head_ref, "head": p.head})).collect::<Vec<_>>(),
        "branches": branches,
    })
}

/// Checks `v` against the JSON Schema subset the contract uses: `type` (one or a list),
/// `enum`, `required`, `properties`, `additionalProperties: false`, `items`. Returns one line
/// per problem, with the path; empty = valid. Keywords outside the subset are refused so the
/// contract cannot silently grow a rule nobody checks.
pub fn validate(schema: &Value, v: &Value) -> Vec<String> {
    let mut errs = Vec::new();
    check(schema, v, "$", &mut errs);
    errs
}

const KNOWN: [&str; 11] = [
    "$schema",
    "$id",
    "title",
    "description",
    "type",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "default",
];

fn type_ok(t: &str, v: &Value) -> bool {
    match t {
        "object" => v.is_object(),
        "array" => v.is_array(),
        "string" => v.is_string(),
        "integer" => v.is_i64() || v.is_u64(),
        "boolean" => v.is_boolean(),
        "null" => v.is_null(),
        _ => false,
    }
}

fn check(s: &Value, v: &Value, path: &str, errs: &mut Vec<String>) {
    let Some(s) = s.as_object() else {
        errs.push(format!("{path}: schema is not an object"));
        return;
    };
    for k in s.keys() {
        if !KNOWN.contains(&k.as_str()) {
            errs.push(format!("{path}: schema keyword {k} not supported"));
        }
    }
    if let Some(t) = s.get("type") {
        let types: Vec<&str> = match t {
            Value::String(x) => vec![x.as_str()],
            Value::Array(a) => a.iter().filter_map(Value::as_str).collect(),
            _ => Vec::new(),
        };
        if !types.iter().any(|t| type_ok(t, v)) {
            errs.push(format!("{path}: want {}, got {v}", types.join("|")));
            return;
        }
    }
    if v.is_null() {
        return;
    }
    if let Some(Value::Array(e)) = s.get("enum") {
        if !e.contains(v) {
            errs.push(format!("{path}: {v} not in enum"));
        }
    }
    if let Some(o) = v.as_object() {
        let props = s.get("properties").and_then(Value::as_object);
        let empty = Map::new();
        let props = props.unwrap_or(&empty);
        if let Some(Value::Array(req)) = s.get("required") {
            for k in req.iter().filter_map(Value::as_str) {
                if !o.contains_key(k) {
                    errs.push(format!("{path}: missing {k}"));
                }
            }
        }
        let closed = s.get("additionalProperties") == Some(&Value::Bool(false));
        for (k, x) in o {
            match props.get(k) {
                Some(ps) => check(ps, x, &format!("{path}.{k}"), errs),
                None if closed => errs.push(format!("{path}: extra field {k}")),
                None => {}
            }
        }
    }
    if let (Some(a), Some(items)) = (v.as_array(), s.get("items")) {
        for (i, x) in a.iter().enumerate() {
            check(items, x, &format!("{path}[{i}]"), errs);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> Value {
        serde_json::from_str(SCHEMA).expect("schema parses")
    }

    fn sample() -> Report {
        Report {
            generated_at: 1,
            main: "a".repeat(40),
            errors: Vec::new(),
            open_prs: vec![OpenPr {
                number: 7,
                head_ref: "integ/x".into(),
                head: "b".repeat(40),
            }],
            branches: vec![
                Branch {
                    branch: "feat/x-1ced7-1114".into(),
                    seat: seat_of("feat/x-1ced7-1114"),
                    head: "c".repeat(40),
                    local: true,
                    unique: 2,
                    unique_not_in_prs: 2,
                    pushed_to: vec!["origin/integ-src/x-1ced7".into()],
                    pr: Some(PrInfo {
                        number: 8,
                        open: true,
                        merged: false,
                    }),
                    review: Some(Review {
                        head: "c".repeat(40),
                        verdict: "通過".into(),
                        h: 0,
                        m: 0,
                        l: 1,
                        n: 0,
                        file: "REVIEW-x.md".into(),
                        at_head: true,
                    }),
                    receipt: Some(Receipt {
                        head: "c".repeat(40),
                        file: "TEST-x.txt".into(),
                        nonzero: Vec::new(),
                        at_head: true,
                    }),
                    ..Branch::default()
                },
                Branch {
                    branch: "fix/y".into(),
                    head: "d".repeat(40),
                    ..Branch::default()
                },
            ],
        }
    }

    #[test]
    fn the_output_matches_the_schema() {
        let v = to_json(&sample(), 72);
        assert_eq!(validate(&schema(), &v), Vec::<String>::new());
        assert_eq!(v["branches"][0]["class"], 1);
        assert_eq!(v["branches"][0]["seat"], "1ced7");
        assert_eq!(v["branches"][1]["class_name"], "merged");
    }

    #[test]
    fn a_missing_or_extra_field_is_red() {
        let s = schema();
        let mut v = to_json(&sample(), 72);
        v["branches"][0]
            .as_object_mut()
            .expect("object")
            .remove("why");
        assert_eq!(validate(&s, &v), vec!["$.branches[0]: missing why"]);
        let mut v = to_json(&sample(), 72);
        v["branches"][1]["extra"] = json!(1);
        assert_eq!(validate(&s, &v), vec!["$.branches[1]: extra field extra"]);
        let mut v = to_json(&sample(), 72);
        v["open_prs"][0]["number"] = json!("7");
        assert_eq!(validate(&s, &v).len(), 1);
        let mut v = to_json(&sample(), 72);
        v["branches"][0]["class_name"] = json!("ready");
        assert_eq!(
            validate(&s, &v),
            vec![r#"$.branches[0].class_name: "ready" not in enum"#]
        );
        v["schema"] = json!("aib-slice-sweep.v2");
        assert_eq!(validate(&s, &v).len(), 2);
    }

    #[test]
    fn a_schema_keyword_the_checker_does_not_read_is_refused() {
        let s = json!({"type": "object", "minProperties": 1});
        assert_eq!(
            validate(&s, &json!({})),
            vec!["$: schema keyword minProperties not supported"]
        );
    }

    #[test]
    fn seats_come_from_the_name_tail() {
        assert_eq!(
            seat_of("feat/aib-dmq-cli-1ced7-1114").as_deref(),
            Some("1ced7")
        );
        assert_eq!(
            seat_of("integ-src/planpane-1ced7").as_deref(),
            Some("1ced7")
        );
        assert_eq!(
            seat_of("integ/aib2-wave2-519d0-1113").as_deref(),
            Some("519d0")
        );
        assert_eq!(seat_of("fix/tui-paste"), None);
        assert_eq!(seat_of("feat/x-1CED7"), None);
        assert_eq!(seat_of("feat/x-1ced7-11"), None);
    }

    #[test]
    fn the_review_first_line_is_read() {
        let h = "8174730016794a5fe9d272d4c4d8044339e61fca";
        let l = format!("結論: 通過 頭: {h} H/M/L/N: 0/0/2/1 範圍: 全文 x..y");
        assert_eq!(
            parse_review_line(&l),
            Some(("通過".into(), h.into(), [0, 0, 2, 1]))
        );
        assert_eq!(
            parse_review_line(&format!("結論: 要改 頭: {h} H/M/L/N: 0/1/0")),
            None
        );
        assert_eq!(
            parse_review_line(&format!("# 結論: 通過 頭: {h} H/M/L/N: 0/0/0/0")),
            None
        );
        assert_eq!(
            parse_review_line("結論: 通過 頭: abc H/M/L/N: 0/0/0/0"),
            None
        );
    }

    #[test]
    fn receipts_name_their_head_and_their_red_keys() {
        let h = "8174730016794a5fe9d272d4c4d8044339e61fca";
        let t = format!(
            "head {h} want {h}\nfmt_rc=0\nclippy_ws_rc=101\nSTEP plan_tests rc=1 :: x\nKNIFE k rc=101 bites\n\
             py_suite_rc=1\nhead_rc=1 a.py\nbase_rc=1 a.py\n"
        );
        assert_eq!(receipt_heads(&t), vec![h.to_owned()]);
        assert_eq!(receipt_nonzero(&t), vec!["clippy_ws_rc", "plan_tests"]);
        // without the main comparison a red Python suite counts
        assert_eq!(receipt_nonzero("py_suite_rc=1\n"), vec!["py_suite_rc"]);
        assert_eq!(
            receipt_nonzero("fmt_rc=0; win_clippy_ws_rc=0\nrc=3\n_rc=1\n"),
            Vec::<String>::new()
        );
    }

    fn base() -> Branch {
        Branch {
            branch: "feat/x-1ced7-1114".into(),
            head: "c".repeat(40),
            unique: 1,
            unique_not_in_prs: 1,
            ..Branch::default()
        }
    }

    fn review(h: u32, at_head: bool) -> Option<Review> {
        Some(Review {
            head: "c".repeat(40),
            verdict: "通過".into(),
            h,
            m: 0,
            l: 0,
            n: 0,
            file: "R".into(),
            at_head,
        })
    }

    fn receipt(red: bool) -> Option<Receipt> {
        Some(Receipt {
            head: "c".repeat(40),
            file: "T".into(),
            nonzero: if red {
                vec!["fmt_rc".into()]
            } else {
                Vec::new()
            },
            at_head: true,
        })
    }

    fn open_pr() -> Option<PrInfo> {
        Some(PrInfo {
            number: 9,
            open: true,
            merged: false,
        })
    }

    #[test]
    fn the_six_classes_in_order() {
        let c = |b: &Branch| classify(b, 72).0;
        // 5: nothing left outside main / open PRs, even if old or acked
        let mut b = base();
        b.unique_not_in_prs = 0;
        b.in_prs = vec![745];
        b.acked = Some("x".into());
        assert_eq!(
            classify(&b, 72),
            (5, "merged", "all in open PR #745".into())
        );
        // 6 by ack, before anything else
        let mut b = base();
        b.acked = Some("gave up".into());
        b.review = review(1, true);
        assert_eq!(c(&b), 6);
        // 3: conflict on an open PR / H at head / red receipt at head
        let mut b = base();
        b.pr = open_pr();
        b.conflicts_with = vec![745];
        assert_eq!(c(&b), 3);
        let mut b = base();
        b.review = review(1, true);
        assert_eq!(c(&b), 3);
        let mut b = base();
        b.receipt = receipt(true);
        assert_eq!(c(&b), 3);
        // a review on an older commit says nothing about the head
        let mut b = base();
        b.review = review(1, false);
        assert_eq!(c(&b), 4);
        // 1: reviewed + green + pushed + PR open + no conflict
        let mut b = base();
        b.review = review(0, true);
        b.receipt = receipt(false);
        b.pushed_to = vec!["origin/x".into()];
        b.pr = open_pr();
        assert_eq!(c(&b), 1);
        // 2: same but not pushed, or no PR
        b.pr = None;
        assert_eq!(
            classify(&b, 72).2,
            "review / receipt at head but no open PR"
        );
        b.pushed_to.clear();
        assert_eq!(
            classify(&b, 72).2,
            "review / receipt at head but not pushed"
        );
        // 6 by age, 4 otherwise
        let mut b = base();
        b.age_h = 73;
        assert_eq!(c(&b), 6);
        b.pr = open_pr();
        assert_eq!(c(&b), 4);
        assert!(blocks(1) && blocks(2) && blocks(3) && !blocks(4) && !blocks(5) && !blocks(6));
    }
}
