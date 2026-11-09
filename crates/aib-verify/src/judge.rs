//! What one run looked like, whether it passes, and the report (first line + table).
//! Pure functions: no aibd, no processes, so every rule here is unit-tested.
//!
//! Rules from the 11-09 G1 nights (VERIFY-G1-1ced7-*.md):
//! - a slot run passes only when it started (201) and gave its slot back within the wait;
//! - the counterexample is an implication, and the report says which side was reached:
//!   ps still alive after the stop ⇒ the reason must not be `observed_clean`;
//!   ps gone ⇒ fine (a missing KILL record is noted, not failed);
//! - a field aibd did not report is "沒記錄", never 0 (a9136 asked for
//!   `orphan_candidates_extra`; the only 0 in the log was the start-up self-check);
//! - a baseline (a head known to be broken) that does not go wrong makes the whole run
//!   invalid: the probes did not bite, so a clean result on the new head proves nothing.

use serde_json::Value;

pub const MISSING: &str = "沒記錄";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Group {
    Claude,
    Burst,
    Stuck,
    BaselineBurst,
    BaselineStuck,
}

impl Group {
    pub fn name(self) -> &'static str {
        match self {
            Group::Claude => "claude",
            Group::Burst => "burst",
            Group::Stuck => "stuck",
            Group::BaselineBurst => "baseline-burst",
            Group::BaselineStuck => "baseline-stuck",
        }
    }
    pub fn is_baseline(self) -> bool {
        matches!(self, Group::BaselineBurst | Group::BaselineStuck)
    }
}

/// Which side of the counterexample implication a stuck run reached.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Branch {
    /// ps still alive after the stop (the side that catches a false "clean").
    Alive,
    /// ps gone after the stop; `kill_seen` = the stop record lists a SIGKILL with targets.
    Gone { kill_seen: bool },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub group: Group,
    pub i: u32,
    pub start: u16,
    pub code: String,
    pub state: String,
    pub holds_slot: Option<bool>,
    /// Seconds from the end of the session (or my KILL, for a stuck run) to the slot coming back.
    pub release_secs: Option<f64>,
    pub reason: String,
    pub survivors: Option<usize>,
    pub tree_size: Option<u64>,
    pub orphan_extra: Option<String>,
    pub unreadable: Option<String>,
    pub branch: Option<Branch>,
    /// None = passes; Some(why) = does not.
    pub bad: Option<String>,
    pub note: String,
}

impl Row {
    pub fn new(group: Group, i: u32) -> Row {
        Row {
            group,
            i,
            start: 0,
            code: String::new(),
            state: String::new(),
            holds_slot: None,
            release_secs: None,
            reason: String::new(),
            survivors: None,
            tree_size: None,
            orphan_extra: None,
            unreadable: None,
            branch: None,
            bad: None,
            note: String::new(),
        }
    }

    /// Copy what aibd reported for the session (GET /api/v1/sessions/{id}).
    pub fn fill(&mut self, v: &Value) {
        let s = |k: &str| v[k].as_str().unwrap_or_default().to_string();
        self.state = s("state");
        self.holds_slot = v["holds_slot"].as_bool();
        let stop = &v["stop"];
        self.reason = stop["reason"].as_str().unwrap_or_default().to_string();
        self.survivors = stop["survivors"].as_array().map(Vec::len);
        self.tree_size = stop["tree_size"].as_u64();
        self.orphan_extra = reported(stop, "orphan_candidates_extra");
        self.unreadable = reported(stop, "candidates_unreadable");
    }

    pub fn add_note(&mut self, n: &str) {
        if !self.note.is_empty() {
            self.note.push_str("; ");
        }
        self.note.push_str(n);
    }
}

/// A stop field as text, or None when aibd did not report it (null counts as not reported).
fn reported(stop: &Value, key: &str) -> Option<String> {
    match stop.get(key) {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => Some(other.to_string()),
    }
}

/// A slot run (claude / burst): it must start and give its slot back.
pub fn judge_slot(r: &Row) -> Option<String> {
    if r.start != 201 {
        return Some(
            format!("start {} {}", r.start, r.code)
                .trim_end()
                .to_string(),
        );
    }
    if r.state == "running" {
        return Some("still running at the end of the wait".into());
    }
    if r.holds_slot != Some(false) {
        return Some(format!("slot not given back ({})", nonempty(&r.reason)));
    }
    None
}

/// The counterexample. `alive_after` = the stuck ps was still alive when the stop ended.
pub fn judge_stuck(alive_after: bool, reason: &str, signals: &Value) -> (Option<String>, Branch) {
    if alive_after {
        let bad = (reason == "observed_clean")
            .then(|| "ps still alive after the stop, but reported observed_clean".to_string());
        return (bad, Branch::Alive);
    }
    let kill_seen = signals.as_array().is_some_and(|a| {
        a.iter().any(|s| {
            s["signal"]
                .as_str()
                .is_some_and(|n| n.starts_with("SIGKILL"))
                && s["targets"].as_array().is_some_and(|t| !t.is_empty())
        })
    });
    (None, Branch::Gone { kill_seen })
}

/// A baseline bit = at least one of its runs went wrong.
pub fn baseline_bit(rows: &[Row]) -> bool {
    rows.iter()
        .any(|r| r.group.is_baseline() && r.bad.is_some())
}

pub struct Summary {
    pub pass: bool,
    pub why: Vec<String>,
    pub not_checked: Vec<String>,
}

/// `claude_asked` / `baseline_asked` = the run was told to do that group.
pub fn summarize(rows: &[Row], claude_asked: bool, baseline_asked: bool) -> Summary {
    let mut why = Vec::new();
    let mut not_checked = Vec::new();
    for r in rows.iter().filter(|r| !r.group.is_baseline()) {
        if let Some(b) = &r.bad {
            why.push(format!("{} #{}: {b}", r.group.name(), r.i));
        }
    }
    if baseline_asked && !baseline_bit(rows) {
        why.push("對照無效:壞頭上沒有一次出錯,探針沒咬到".into());
    }
    if !claude_asked {
        not_checked.push("真 claude".to_string());
    }
    if !baseline_asked {
        not_checked.push("壞頭對照".to_string());
    }
    let stuck: Vec<&Row> = rows.iter().filter(|r| r.group == Group::Stuck).collect();
    if !stuck.iter().any(|r| r.branch == Some(Branch::Alive)) {
        not_checked.push("反例「stop 後 ps 仍活著」那一支沒走到".to_string());
    }
    let counted: Vec<&Row> = rows
        .iter()
        .filter(|r| !r.group.is_baseline() && r.start == 201)
        .collect();
    if counted.iter().all(|r| r.orphan_extra.is_none()) {
        not_checked.push("orphan_candidates_extra(aibd 沒記錄)".to_string());
    }
    Summary {
        pass: why.is_empty(),
        why,
        not_checked,
    }
}

pub fn first_line(s: &Summary, head: &str, host: &str, rows: &[Row]) -> String {
    let n = |g: Group| rows.iter().filter(|r| r.group == g).count();
    let mut sample = Vec::new();
    for g in [Group::Claude, Group::Burst, Group::Stuck] {
        if n(g) > 0 {
            sample.push(format!("{} {}", g.name(), n(g)));
        }
    }
    let base = n(Group::BaselineBurst) + n(Group::BaselineStuck);
    if base > 0 {
        sample.push(format!("baseline {base}"));
    }
    let nc = if s.not_checked.is_empty() {
        "無".to_string()
    } else {
        s.not_checked.join("、")
    };
    format!(
        "驗收: {} 頭: {head} 機: {host} 樣本: {} 沒驗: {nc}",
        if s.pass { "通過" } else { "不通過" },
        sample.join(" ")
    )
}

fn nonempty(s: &str) -> &str {
    if s.is_empty() {
        "-"
    } else {
        s
    }
}

fn opt<T: ToString>(v: &Option<T>) -> String {
    v.as_ref().map_or_else(|| MISSING.to_string(), T::to_string)
}

/// The per-run table (C2), markdown.
pub fn table(rows: &[Row]) -> String {
    let mut out = String::from(
        "| 組 | # | 開始 | state | holds_slot | 放名額秒數 | reason | survivors | tree_size | orphan_candidates_extra | 讀不到的候選 | 反例那一支 | 判定 | 備註 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n",
    );
    for r in rows {
        let branch = match r.branch {
            None => "-".to_string(),
            Some(Branch::Alive) => "仍活著".to_string(),
            Some(Branch::Gone { kill_seen: true }) => "已不在(有 KILL)".to_string(),
            Some(Branch::Gone { kill_seen: false }) => "已不在(沒記到 KILL)".to_string(),
        };
        let start = if r.code.is_empty() {
            r.start.to_string()
        } else {
            format!("{} {}", r.start, r.code)
        };
        let release = r
            .release_secs
            .map_or_else(|| "-".to_string(), |s| format!("{s:.1}"));
        out.push_str(&format!(
            "| {} | {} | {start} | {} | {} | {release} | {} | {} | {} | {} | {} | {branch} | {} | {} |\n",
            r.group.name(),
            r.i,
            nonempty(&r.state),
            r.holds_slot.map_or_else(|| "-".to_string(), |b| b.to_string()),
            nonempty(&r.reason),
            opt(&r.survivors),
            opt(&r.tree_size),
            opt(&r.orphan_extra),
            opt(&r.unreadable),
            r.bad.as_deref().unwrap_or("過"),
            r.note.replace('|', "/"),
        ));
    }
    out
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;
    use serde_json::json;

    fn ended(group: Group, i: u32, v: Value) -> Row {
        let mut r = Row::new(group, i);
        r.start = 201;
        r.fill(&v);
        r
    }

    fn clean() -> Value {
        json!({"state": "stop_unconfirmed", "holds_slot": false,
               "stop": {"reason": "observed_clean", "survivors": [], "tree_size": 14}})
    }

    #[test]
    fn a_field_aibd_did_not_report_is_missing_not_zero() {
        let r = ended(Group::Burst, 1, clean());
        assert_eq!(r.orphan_extra, None);
        assert_eq!(r.unreadable, None);
        assert_eq!(opt(&r.orphan_extra), MISSING);
        let mut v = clean();
        v["stop"]["orphan_candidates_extra"] = json!(0);
        v["stop"]["candidates_unreadable"] = json!([{"pid": 7, "comm": "ps"}]);
        let r = ended(Group::Burst, 1, v);
        assert_eq!(r.orphan_extra.as_deref(), Some("0"));
        assert_eq!(r.unreadable.as_deref(), Some(r#"[{"comm":"ps","pid":7}]"#));
        let mut v = clean();
        v["stop"]["orphan_candidates_extra"] = Value::Null;
        assert_eq!(ended(Group::Burst, 1, v).orphan_extra, None);
    }

    #[test]
    fn a_slot_run_must_start_end_and_give_the_slot_back() {
        assert_eq!(judge_slot(&ended(Group::Burst, 1, clean())), None);
        let mut held = clean();
        held["holds_slot"] = json!(true);
        held["stop"]["reason"] = json!("candidates_unreadable");
        assert_eq!(
            judge_slot(&ended(Group::Claude, 5, held)).unwrap(),
            "slot not given back (candidates_unreadable)"
        );
        let mut refused = Row::new(Group::Claude, 6);
        refused.start = 429;
        refused.code = "too_many_sessions".into();
        assert_eq!(judge_slot(&refused).unwrap(), "start 429 too_many_sessions");
        let mut running = clean();
        running["state"] = json!("running");
        assert!(judge_slot(&ended(Group::Burst, 1, running)).is_some());
    }

    #[test]
    fn the_counterexample_is_an_implication_and_names_its_side() {
        // efbe548d 11-09: alive after the stop and reported clean ⇒ fails
        let (bad, b) = judge_stuck(
            true,
            "observed_clean",
            &json!([{"signal": "SIGTERM(group)", "targets": [5919]}]),
        );
        assert!(bad.is_some());
        assert_eq!(b, Branch::Alive);
        // alive and honestly not clean ⇒ passes, the alive side was reached
        let (bad, b) = judge_stuck(true, "candidates_unreadable", &json!([]));
        assert_eq!(bad, None);
        assert_eq!(b, Branch::Alive);
        // cbfaa52f 11-09: gone, with a group SIGKILL ⇒ passes on the gone side
        let sig = json!([{"signal": "SIGTERM(group)", "targets": [61774]},
                         {"signal": "SIGKILL(group)", "targets": [61774]},
                         {"signal": "SIGKILL", "targets": []}]);
        assert_eq!(
            judge_stuck(false, "observed_clean", &sig),
            (None, Branch::Gone { kill_seen: true })
        );
        // gone, but no KILL with targets on record ⇒ passes, noted in the table
        let sig = json!([{"signal": "SIGKILL", "targets": []}]);
        assert_eq!(
            judge_stuck(false, "observed_clean", &sig),
            (None, Branch::Gone { kill_seen: false })
        );
    }

    #[test]
    fn a_baseline_that_does_not_bite_makes_the_run_invalid() {
        let mut rows = vec![
            ended(Group::Burst, 1, clean()),
            ended(Group::BaselineBurst, 1, clean()),
        ];
        let s = summarize(&rows, false, true);
        assert!(!s.pass);
        assert!(
            s.why.iter().any(|w| w.starts_with("對照無效")),
            "{:?}",
            s.why
        );
        rows[1].bad = Some("slot not given back".into());
        let s = summarize(&rows, false, true);
        assert!(s.pass, "{:?}", s.why);
        // a baseline failure never fails the new head
        assert!(s.why.is_empty());
    }

    #[test]
    fn what_was_not_run_is_listed_in_the_first_line() {
        let mut stuck = ended(Group::Stuck, 1, clean());
        stuck.branch = Some(Branch::Gone { kill_seen: true });
        let rows = vec![ended(Group::Burst, 1, clean()), stuck];
        let s = summarize(&rows, false, false);
        assert!(s.pass);
        let l = first_line(&s, &"a".repeat(40), "mac", &rows);
        assert_eq!(
            l,
            format!(
                "驗收: 通過 頭: {} 機: mac 樣本: burst 1 stuck 1 沒驗: 真 claude、壞頭對照、反例「stop 後 ps 仍活著」那一支沒走到、orphan_candidates_extra(aibd 沒記錄)",
                "a".repeat(40)
            )
        );
    }

    #[test]
    fn a_failed_run_fails_the_whole_and_the_table_shows_it() {
        let mut held = clean();
        held["holds_slot"] = json!(true);
        held["stop"]["reason"] = json!("candidates_unreadable");
        let mut r = ended(Group::Claude, 5, held);
        r.bad = judge_slot(&r);
        let mut alive = ended(Group::Stuck, 1, clean());
        alive.branch = Some(Branch::Alive);
        alive.orphan_extra = Some("0".into());
        let rows = vec![r, alive];
        let s = summarize(&rows, true, false);
        assert!(!s.pass);
        assert_eq!(
            s.why,
            ["claude #5: slot not given back (candidates_unreadable)"]
        );
        assert_eq!(s.not_checked, ["壞頭對照"]);
        let l = first_line(&s, &"b".repeat(40), "mac", &rows);
        assert!(l.starts_with("驗收: 不通過 頭: "), "{l}");
        let t = table(&rows);
        assert!(
            t.contains("| claude | 5 | 201 | stop_unconfirmed | true |"),
            "{t}"
        );
        assert!(t.contains(&format!("| {MISSING} |")), "{t}");
        assert!(t.contains("| 仍活著 |"), "{t}");
    }
}
