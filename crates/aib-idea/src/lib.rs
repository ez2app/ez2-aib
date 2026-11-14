//! aib-idea: the suggestion ledger (S76, DESIGN-S76-idea-ledger-519d0-1114, Andy 11-14 10:3x:
//! 「只要是優化、不會造成重大錯誤的就自動記錄、同意、可查詢，也要在每次列出來的建議之後，
//! 列出真人未審的內容，可以一併後審同意」).
//!
//! A seat adds each suggestion with the classes it touches. One that touches a red-line class
//! ([`RED`]) or names none needs Andy's yes; the rest are agreed automatically and listed as
//! "not yet reviewed by a human" until Andy agrees to the lot or turns one down.
//!
//! This file is pure: it parses ledger lines, replays them and renders the lists. Reading
//! and writing the file, the lock and the clock live in `main.rs`.
//!
//! The verdict sorts work between seats; it is **not a defence**: an AI on the same account
//! can edit the ledger. The red lines stay with Touch ID and signatures.

use serde_json::{json, Map, Value};

/// Red-line classes (DESIGN S76 "自動同意的判定"): any one of them ⇒ needs_human.
/// Written into the code on purpose: changing it goes through a PR and a review.
pub const RED: [&str; 9] = [
    "money",
    "sign",
    "prod",
    "delete",
    "customer_text",
    "seat",
    "machine",
    "security",
    "rule",
];

/// Classes that do not stop an automatic yes. `none` = the seat says it touches nothing listed.
pub const PLAIN: [&str; 7] = ["none", "code", "docs", "test", "ui", "perf", "tooling"];

/// What the ledger says about one suggestion now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    /// agreed automatically, Andy has not looked yet
    Auto,
    /// waits for Andy's explicit yes
    NeedsHuman,
    /// Andy agreed
    Ok,
    /// Andy turned it down
    No,
}

impl Status {
    pub fn code(self) -> &'static str {
        match self {
            Status::Auto => "auto",
            Status::NeedsHuman => "needs_human",
            Status::Ok => "ok",
            Status::No => "no",
        }
    }
    pub fn parse(s: &str) -> Option<Status> {
        match s {
            "auto" => Some(Status::Auto),
            "needs_human" => Some(Status::NeedsHuman),
            "ok" => Some(Status::Ok),
            "no" => Some(Status::No),
            _ => None,
        }
    }
}

/// The classes a suggestion touches, as given to `--touches`. Err = a word that is neither
/// red nor plain (a typo must not become an automatic yes), or `none` next to another class.
pub fn parse_touches(arg: &str) -> Result<Vec<String>, String> {
    let mut out: Vec<String> = Vec::new();
    for w in arg.split(',').map(str::trim) {
        if w.is_empty() {
            continue;
        }
        if !RED.contains(&w) && !PLAIN.contains(&w) {
            return Err(format!("touches_unknown: {w}"));
        }
        if !out.iter().any(|x| x == w) {
            out.push(w.to_string());
        }
    }
    if out.iter().any(|w| w == "none") && out.len() > 1 {
        return Err("touches_none_with_others".into());
    }
    Ok(out)
}

/// The first verdict of a new suggestion. Nothing given ⇒ needs_human (conservative default).
pub fn classify(touches: Option<&[String]>) -> Status {
    match touches {
        None => Status::NeedsHuman,
        Some([]) => Status::NeedsHuman,
        Some(t) if t.iter().any(|w| RED.contains(&w.as_str())) => Status::NeedsHuman,
        Some(_) => Status::Auto,
    }
}

/// `i_` + 8 lower-case hex.
pub fn id_ok(id: &str) -> bool {
    id.strip_prefix("i_")
        .is_some_and(|h| h.len() == 8 && h.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
}

/// A seat code: 5 lower-case hex.
pub fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Review {
    pub ok: bool,
    pub by: String,
    pub quote: String,
    pub why: Option<String>,
    pub at: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Idea {
    pub id: String,
    pub at: i64,
    pub seat: String,
    pub title: String,
    pub now: String,
    pub suggest: String,
    pub benefit: String,
    pub cost: String,
    pub touches: Option<Vec<String>>,
    /// the verdict written when it was added
    pub first: Status,
    pub node: Option<String>,
    pub reviews: Vec<Review>,
}

impl Idea {
    /// The verdict now: the last review decides; none yet ⇒ the first verdict.
    pub fn status(&self) -> Status {
        match self.reviews.last() {
            Some(r) if r.ok => Status::Ok,
            Some(_) => Status::No,
            None => self.first,
        }
    }
    pub fn unreviewed(&self) -> bool {
        self.reviews.is_empty()
    }
    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id, "at": self.at, "seat": self.seat, "title": self.title,
            "now": self.now, "suggest": self.suggest, "benefit": self.benefit, "cost": self.cost,
            "touches": self.touches, "first": self.first.code(), "status": self.status().code(),
            "node": self.node, "unreviewed": self.unreviewed(),
            "reviews": self.reviews.iter().map(|r| json!({
                "verdict": if r.ok { "ok" } else { "no" }, "by": r.by, "quote": r.quote,
                "why": r.why, "at": r.at,
            })).collect::<Vec<_>>(),
        })
    }
}

/// The ledger line for a new suggestion.
pub fn add_line(i: &Idea) -> String {
    json!({
        "ev": "add", "id": i.id, "at": i.at, "seat": i.seat, "title": i.title, "now": i.now,
        "suggest": i.suggest, "benefit": i.benefit, "cost": i.cost, "touches": i.touches,
        "status": i.first.code(), "node": i.node,
    })
    .to_string()
}

/// The ledger line for one review.
pub fn review_line(id: &str, r: &Review) -> String {
    json!({
        "ev": "review", "id": id, "verdict": if r.ok { "ok" } else { "no" }, "by": r.by,
        "quote": r.quote, "why": r.why, "at": r.at,
    })
    .to_string()
}

/// Everything the ledger says, replayed in file order.
#[derive(Debug, Default)]
pub struct Ledger {
    /// in the order they were added
    pub ideas: Vec<Idea>,
    /// `line <n>: <why>` for every line that could not be used; the rest still counts
    pub errors: Vec<String>,
}

impl Ledger {
    pub fn get(&self, id: &str) -> Option<&Idea> {
        self.ideas.iter().find(|i| i.id == id)
    }
}

fn s_field(o: &Map<String, Value>, k: &str) -> Result<String, String> {
    match o.get(k).and_then(Value::as_str) {
        Some(v) if !v.trim().is_empty() => Ok(v.to_string()),
        _ => Err(format!("missing {k}")),
    }
}

fn opt_s(o: &Map<String, Value>, k: &str) -> Result<Option<String>, String> {
    match o.get(k) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s.clone())),
        Some(_) => Err(format!("wrong type {k}")),
    }
}

fn i_field(o: &Map<String, Value>, k: &str) -> Result<i64, String> {
    o.get(k)
        .and_then(Value::as_i64)
        .ok_or_else(|| format!("missing {k}"))
}

fn parse_add(o: &Map<String, Value>) -> Result<Idea, String> {
    let id = s_field(o, "id")?;
    if !id_ok(&id) {
        return Err(format!("bad id {id}"));
    }
    let seat = s_field(o, "seat")?;
    if !seat_ok(&seat) {
        return Err(format!("bad seat {seat}"));
    }
    let touches = match o.get("touches") {
        None | Some(Value::Null) => None,
        Some(Value::Array(a)) => {
            let words: Option<Vec<String>> =
                a.iter().map(|v| v.as_str().map(str::to_string)).collect();
            let words = words.ok_or("wrong type touches")?;
            Some(parse_touches(&words.join(","))?)
        }
        Some(_) => return Err("wrong type touches".into()),
    };
    // the verdict is recomputed from touches, never taken from the line: a line that says
    // "auto" next to a red class is wrong, not a way in
    let first = classify(touches.as_deref());
    let written = s_field(o, "status")?;
    if Status::parse(&written) != Some(first) {
        return Err(format!("status {written} does not follow from touches"));
    }
    Ok(Idea {
        id,
        at: i_field(o, "at")?,
        seat,
        title: s_field(o, "title")?,
        now: s_field(o, "now")?,
        suggest: s_field(o, "suggest")?,
        benefit: s_field(o, "benefit")?,
        cost: s_field(o, "cost")?,
        touches,
        first,
        node: opt_s(o, "node")?,
        reviews: Vec::new(),
    })
}

fn parse_review(o: &Map<String, Value>) -> Result<(String, Review), String> {
    let id = s_field(o, "id")?;
    let ok = match s_field(o, "verdict")?.as_str() {
        "ok" => true,
        "no" => false,
        v => return Err(format!("bad verdict {v}")),
    };
    let r = Review {
        ok,
        by: s_field(o, "by")?,
        quote: s_field(o, "quote")?,
        why: opt_s(o, "why")?,
        at: i_field(o, "at")?,
    };
    if !r.ok && r.why.as_deref().is_none_or(|w| w.trim().is_empty()) {
        return Err("a no without why".into());
    }
    Ok((id, r))
}

/// Replay the ledger text. A bad line is listed in `errors` and skipped; it never stops the
/// lines after it.
pub fn replay(text: &str) -> Ledger {
    let mut l = Ledger::default();
    for (n, line) in text.lines().enumerate() {
        let n = n + 1;
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(e) => {
                l.errors.push(format!("line {n}: bad json: {e}"));
                continue;
            }
        };
        let Some(o) = v.as_object() else {
            l.errors.push(format!("line {n}: not an object"));
            continue;
        };
        match o.get("ev").and_then(Value::as_str) {
            Some("add") => match parse_add(o) {
                Ok(i) if l.get(&i.id).is_some() => {
                    l.errors.push(format!("line {n}: id {} added twice", i.id))
                }
                Ok(i) => l.ideas.push(i),
                Err(e) => l.errors.push(format!("line {n}: {e}")),
            },
            Some("review") => match parse_review(o) {
                Ok((id, r)) => match l.ideas.iter_mut().find(|i| i.id == id) {
                    Some(i) => i.reviews.push(r),
                    None => l
                        .errors
                        .push(format!("line {n}: review of unknown id {id}")),
                },
                Err(e) => l.errors.push(format!("line {n}: {e}")),
            },
            _ => l.errors.push(format!("line {n}: unknown ev")),
        }
    }
    l
}

/// What `footer` lists and what `review --ok-all-unreviewed` agrees to: the oldest `max`
/// unreviewed suggestions of the seat (or every seat), in the order they were added.
pub fn footer_items<'a>(l: &'a Ledger, seat: Option<&str>, max: usize) -> (Vec<&'a Idea>, usize) {
    let all: Vec<&Idea> = l
        .ideas
        .iter()
        .filter(|i| i.unreviewed() && seat.is_none_or(|s| i.seat == s))
        .collect();
    let more = all.len().saturating_sub(max);
    (all.into_iter().take(max).collect(), more)
}

/// The ids `--ok-all-unreviewed` agrees to: only the automatic ones among the footer's list.
/// A needs_human one is agreed by its id, one at a time, so one 「同意」 never covers a red line.
pub fn ok_all_ids(l: &Ledger, seat: &str, max: usize) -> Vec<String> {
    footer_items(l, Some(seat), max)
        .0
        .into_iter()
        .filter(|i| i.first == Status::Auto)
        .map(|i| i.id.clone())
        .collect()
}

/// `HH:MM` (or `MM-DD HH:MM` when not today) in the cabin's time zone.
pub fn clock(t: i64, now: i64, tz_offset_min: i32) -> String {
    let off = i64::from(tz_offset_min) * 60;
    let (lt, ln) = (t.saturating_add(off), now.saturating_add(off));
    let (day, sec) = (lt.div_euclid(86_400), lt.rem_euclid(86_400));
    let hm = format!("{:02}:{:02}", sec / 3600, sec % 3600 / 60);
    if day == ln.div_euclid(86_400) {
        return hm;
    }
    let (_, m, d) = civil(day);
    format!("{m:02}-{d:02} {hm}")
}

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian (Howard Hinnant's).
fn civil(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

/// The block written after a reply's suggestions. Empty ledger part ⇒ empty string.
pub fn footer(l: &Ledger, seat: Option<&str>, max: usize, now: i64, tz_offset_min: i32) -> String {
    let (items, more) = footer_items(l, seat, max);
    if items.is_empty() {
        return String::new();
    }
    let n = items.len() + more;
    let mut out = format!("真人未審({n} 條,回「同意」一次全收 auto;needs_human 要逐條):\n");
    for i in &items {
        let node = i
            .node
            .as_deref()
            .map(|s| format!("{s} "))
            .unwrap_or_default();
        out.push_str(&format!(
            "- {} {node}{}({},{})\n",
            i.id,
            i.title,
            i.first.code(),
            clock(i.at, now, tz_offset_min)
        ));
    }
    if more > 0 {
        out.push_str(&format!("- 還有 {more} 條\n"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_794_623_580; // 2026-11-14 10:33 Taipei

    fn idea(id: &str, seat: &str, touches: Option<&str>) -> Idea {
        let touches = touches.map(|t| parse_touches(t).expect("touches"));
        Idea {
            id: id.into(),
            at: T0,
            seat: seat.into(),
            title: "加隊序變動行".into(),
            now: "隊序變了沒人知道".into(),
            suggest: "sweep 印一行".into(),
            benefit: "少等".into(),
            cost: "10 分".into(),
            first: classify(touches.as_deref()),
            touches,
            node: Some("S72".into()),
            reviews: vec![],
        }
    }

    fn review(ok: bool) -> Review {
        Review {
            ok,
            by: "andy".into(),
            quote: if ok { "同意".into() } else { "不要".into() },
            why: (!ok).then(|| "先不做".to_string()),
            at: T0 + 60,
        }
    }

    #[test]
    fn each_red_class_needs_a_human() {
        for c in RED {
            let t = parse_touches(&format!("code,{c}")).expect("parse");
            assert_eq!(
                classify(Some(t.as_slice())),
                Status::NeedsHuman,
                "reason: red_class_auto {c}"
            );
        }
    }

    #[test]
    fn nothing_given_needs_a_human_and_plain_classes_are_auto() {
        assert_eq!(
            classify(None),
            Status::NeedsHuman,
            "reason: no_touches_auto"
        );
        assert_eq!(
            classify(Some(&[][..])),
            Status::NeedsHuman,
            "reason: empty_touches_auto"
        );
        for c in PLAIN {
            let t = parse_touches(c).expect("parse");
            assert_eq!(
                classify(Some(t.as_slice())),
                Status::Auto,
                "reason: plain_class_held {c}"
            );
        }
    }

    #[test]
    fn an_unknown_class_or_none_with_others_is_refused() {
        assert!(parse_touches("code,mony").is_err(), "reason: typo_accepted");
        assert!(
            parse_touches("none,money").is_err(),
            "reason: none_with_red_accepted"
        );
        assert_eq!(
            parse_touches(" code , docs,code ,"),
            Ok(vec!["code".to_string(), "docs".to_string()]),
            "reason: touches_not_cleaned"
        );
    }

    #[test]
    fn the_last_review_decides() {
        let i = idea("i_0000000a", "043dd", Some("code"));
        let text = [
            add_line(&i),
            review_line(&i.id, &review(true)),
            review_line(&i.id, &review(false)),
        ]
        .join("\n");
        let l = replay(&text);
        assert!(
            l.errors.is_empty(),
            "reason: clean_ledger_errors {:?}",
            l.errors
        );
        assert_eq!(
            l.ideas[0].status(),
            Status::No,
            "reason: replay_order_wrong"
        );
        let text = [
            add_line(&i),
            review_line(&i.id, &review(false)),
            review_line(&i.id, &review(true)),
        ]
        .join("\n");
        assert_eq!(
            replay(&text).ideas[0].status(),
            Status::Ok,
            "reason: replay_order_wrong"
        );
    }

    #[test]
    fn a_bad_line_is_listed_and_the_rest_still_counts() {
        let a = idea("i_0000000a", "043dd", Some("code"));
        let b = idea("i_0000000b", "043dd", Some("money"));
        let text = [
            add_line(&a),
            "{not json".to_string(),
            r#"{"ev":"review","id":"i_0000000f","verdict":"ok","by":"andy","quote":"同意","at":1}"#
                .to_string(),
            add_line(&b),
            add_line(&a),
            review_line(&b.id, &review(true)),
        ]
        .join("\n");
        let l = replay(&text);
        assert_eq!(l.ideas.len(), 2, "reason: good_lines_lost");
        assert_eq!(
            l.errors.len(),
            3,
            "reason: bad_lines_not_listed {:?}",
            l.errors
        );
        assert!(
            l.errors[0].starts_with("line 2:"),
            "reason: line_number_wrong"
        );
        assert_eq!(l.get(&b.id).map(Idea::status), Some(Status::Ok));
    }

    #[test]
    fn a_line_cannot_say_auto_next_to_a_red_class() {
        let i = idea("i_0000000a", "043dd", Some("money"));
        let line = add_line(&i).replace("\"needs_human\"", "\"auto\"");
        let l = replay(&line);
        assert!(l.ideas.is_empty(), "reason: forged_auto_accepted");
        assert_eq!(l.errors.len(), 1);
    }

    #[test]
    fn a_review_needs_a_quote_and_a_no_needs_a_why() {
        let i = idea("i_0000000a", "043dd", Some("code"));
        let mut r = review(true);
        r.quote = " ".into();
        let l = replay(&[add_line(&i), review_line(&i.id, &r)].join("\n"));
        assert!(
            l.ideas[0].unreviewed(),
            "reason: review_without_quote_counted"
        );
        let mut r = review(false);
        r.why = None;
        let l = replay(&[add_line(&i), review_line(&i.id, &r)].join("\n"));
        assert!(l.ideas[0].unreviewed(), "reason: no_without_why_counted");
        r.why = Some("  ".into());
        let l = replay(&[add_line(&i), review_line(&i.id, &r)].join("\n"));
        assert!(l.ideas[0].unreviewed(), "reason: no_with_blank_why_counted");
    }

    #[test]
    fn the_footer_lists_the_oldest_unreviewed_and_ok_all_takes_only_auto() {
        let mut lines = Vec::new();
        for n in 0..12u32 {
            let touches = if n == 1 { "prod" } else { "code" };
            let seat = if n == 2 { "a9136" } else { "043dd" };
            let mut i = idea(&format!("i_{n:08x}"), seat, Some(touches));
            i.at = T0 + i64::from(n);
            lines.push(add_line(&i));
        }
        lines.push(review_line("i_00000000", &review(true)));
        let l = replay(&lines.join("\n"));
        let (items, more) = footer_items(&l, Some("043dd"), 10);
        let ids: Vec<&str> = items.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(
            ids.first(),
            Some(&"i_00000001"),
            "reason: footer_not_oldest_first"
        );
        assert!(!ids.contains(&"i_00000000"), "reason: reviewed_in_footer");
        assert!(!ids.contains(&"i_00000002"), "reason: other_seat_in_footer");
        assert_eq!((items.len(), more), (10, 0));
        let ok = ok_all_ids(&l, "043dd", 5);
        assert!(
            !ok.contains(&"i_00000001".to_string()),
            "reason: ok_all_took_needs_human"
        );
        assert_eq!(ok.len(), 4, "reason: ok_all_beyond_footer {ok:?}");
        let text = footer(&l, Some("043dd"), 5, T0 + 100, 480);
        assert!(
            text.starts_with("真人未審(10 條"),
            "reason: footer_count {text}"
        );
        assert!(
            text.contains("- i_00000001 S72 加隊序變動行(needs_human,10:33)"),
            "{text}"
        );
        assert!(
            text.ends_with("- 還有 5 條\n"),
            "reason: footer_more {text}"
        );
        assert_eq!(footer(&Ledger::default(), None, 10, T0, 480), "");
    }

    #[test]
    fn clock_is_taipei_and_dates_other_days() {
        assert_eq!(clock(T0, T0, 480), "10:33");
        assert_eq!(clock(T0 - 86_400, T0, 480), "11-13 10:33");
    }

    #[test]
    fn ids_and_seats_have_their_shape() {
        assert!(id_ok("i_3fa9c201"));
        for bad in ["i_3FA9C201", "i_3fa9c20", "x_3fa9c201", "i_3fa9c2011"] {
            assert!(!id_ok(bad), "reason: bad_id_accepted {bad}");
        }
        assert!(seat_ok("043dd") && !seat_ok("043DD") && !seat_ok("43dd"));
    }
}
