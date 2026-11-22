//! aibd §8 合併類似, the pure parts of slice 8b (DESIGN-merge-6770f-1123.md r1 §3, §5, §6;
//! f163c REVIEW-merge-6770f-f163c-r1 passed): the plan's sha, what an undo / a crash rollback
//! does with one step, and the owner window's words. No store and no files here.
//!
//! - **plan_sha**: the page's plan is turned into one canonical byte form (object keys
//!   sorted at every depth, compact, no float re-printing) and hashed; `merge_run` only takes
//!   a plan whose sha aibd itself computed in a recent `merge_preview`.
//! - **step rollback** (r1 L1): before a step changes anything its `before` and its planned
//!   `after` are written with state `pending`; done ⇒ `done`. Rolling back:
//!   now == before ⇒ never happened, skip; now == planned after ⇒ put `before` back;
//!   anything else ⇒ changed by someone since, leave it and list it.
//! - **owner window** (r1 L2): never cut, never "and N more": counts + the plan_sha's first
//!   12 hex; longer than the window takes ⇒ no window at all.

use serde_json::Value;
use sha2::{Digest, Sha256};

/// The most characters the owner window shows (08de5 7a measured the window at 80).
pub const WINDOW_MAX: usize = 80;

/// `v` in canonical compact JSON: object keys sorted (by their UTF-8 bytes) at every depth.
pub fn canonical(v: &Value) -> String {
    let mut out = String::new();
    write_canon(v, &mut out);
    out
}

fn write_canon(v: &Value, out: &mut String) {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&Value::String((*k).clone()).to_string());
                out.push(':');
                write_canon(&m[k.as_str()], out);
            }
            out.push('}');
        }
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_canon(x, out);
            }
            out.push(']');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// The plan's sha-256, lower-case hex, over its canonical form.
pub fn plan_sha(plan: &Value) -> String {
    let d = Sha256::digest(canonical(plan).as_bytes());
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// A step's state in the journal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepState {
    /// `before` and the planned `after` written, the change maybe made or not.
    Pending,
    /// The change made.
    Done,
    /// Already put back.
    Undone,
}

/// What a rollback (failure, crash or the owner's undo) does with one step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Back {
    /// Nothing to do: never happened, or already put back.
    Skip,
    /// Put `before` back.
    Restore,
    /// The value now is neither: someone changed it since. Leave it, list it.
    Changed,
}

/// The decision for one step, comparing the value now with the step's `before` and
/// planned `after` (r1 L1). A `done` step whose value is back at `before` is skipped too
/// (put back by hand: nothing to do).
pub fn back(state: StepState, now: &Value, before: &Value, after: &Value) -> Back {
    if state == StepState::Undone {
        return Back::Skip;
    }
    if now == after && now != before {
        return Back::Restore;
    }
    if now == before {
        return Back::Skip;
    }
    Back::Changed
}

/// The kinds whose merge asks the owner (r1 §6): personas and brains; roles carry no
/// permission and do not.
pub fn needs_owner(kind: &str) -> bool {
    matches!(kind, "persona" | "brain")
}

/// The owner window's words for a merge or its undo, or `None` when they do not fit
/// [`WINDOW_MAX`] (the caller then opens no window and answers why; never cut).
/// `days` `None` = kept for good.
pub fn window_text(
    kind: &str,
    undo: bool,
    groups: usize,
    seats_next_open: usize,
    days: Option<u32>,
    sha: &str,
) -> Option<String> {
    let what = match kind {
        "persona" => "人格",
        "brain" => "第二大腦",
        _ => return None,
    };
    let sha12: String = sha.chars().take(12).collect();
    if sha12.len() != 12 || !sha12.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let keep = match days {
        Some(d) => format!("封存 {d} 天"),
        None => "永久封存".to_string(),
    };
    let t = if undo {
        format!("還原{what}合併 {groups} 組,{seats_next_open} 位下次開會換回。對照碼 {sha12}")
    } else {
        format!("{what}合併 {groups} 組,{seats_next_open} 位下次開會換,{keep}。對照碼 {sha12}")
    };
    (t.chars().count() <= WINDOW_MAX).then_some(t)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_sha_does_not_depend_on_key_order_but_on_every_value() {
        let a = json!({"kind":"persona","groups":[{"keep":"aaaaa1","merge":["aaaaa2"],"days":30}]});
        let b = json!({"groups":[{"days":30,"merge":["aaaaa2"],"keep":"aaaaa1"}],"kind":"persona"});
        assert_eq!(plan_sha(&a), plan_sha(&b));
        assert_eq!(canonical(&b), canonical(&a));
        let c = json!({"kind":"persona","groups":[{"keep":"aaaaa1","merge":["aaaaa2"],"days":7}]});
        assert_ne!(plan_sha(&a), plan_sha(&c));
        // array order is meaning (step order), so it counts
        let d = json!({"kind":"persona","groups":[{"keep":"aaaaa1","merge":["aaaaa3","aaaaa2"]}]});
        let e = json!({"kind":"persona","groups":[{"keep":"aaaaa1","merge":["aaaaa2","aaaaa3"]}]});
        assert_ne!(plan_sha(&d), plan_sha(&e));
        assert_eq!(plan_sha(&a).len(), 64);
    }

    #[test]
    fn canonical_escapes_keys_and_strings() {
        let v = json!({"b\"":"x\ny","a":[1,null,true]});
        assert_eq!(canonical(&v), r#"{"a":[1,null,true],"b\"":"x\ny"}"#);
    }

    #[test]
    fn mg9_a_pending_step_rolls_back_by_what_the_value_is_now() {
        let (b, a) = (json!("p_old"), json!("p_new"));
        // stopped before the change: nothing to do
        assert_eq!(back(StepState::Pending, &b, &b, &a), Back::Skip);
        // stopped after the change, before `done` was written: put back
        assert_eq!(back(StepState::Pending, &a, &b, &a), Back::Restore);
        // someone else changed it: leave and list
        assert_eq!(
            back(StepState::Pending, &json!("p_x"), &b, &a),
            Back::Changed
        );
    }

    #[test]
    fn mg12_a_done_step_changed_since_is_left_alone() {
        let (b, a) = (json!({"sha":"1"}), json!({"sha":"2"}));
        assert_eq!(back(StepState::Done, &a, &b, &a), Back::Restore);
        assert_eq!(
            back(StepState::Done, &json!({"sha":"3"}), &b, &a),
            Back::Changed
        );
        assert_eq!(back(StepState::Done, &b, &b, &a), Back::Skip);
        assert_eq!(back(StepState::Undone, &a, &b, &a), Back::Skip);
    }

    #[test]
    fn a_step_that_changes_nothing_is_skipped() {
        let v = json!(null);
        assert_eq!(back(StepState::Done, &v, &v, &v), Back::Skip);
    }

    #[test]
    fn only_personas_and_brains_ask_the_owner() {
        assert!(needs_owner("persona"));
        assert!(needs_owner("brain"));
        assert!(!needs_owner("role"));
        assert!(!needs_owner(""));
    }

    #[test]
    fn the_window_has_counts_and_the_sha12_and_is_never_cut() {
        let sha = "0123456789abcdef".repeat(4);
        let t = window_text("persona", false, 3, 2, Some(30), &sha).unwrap();
        assert!(
            t.contains("3 組") && t.contains("2 位") && t.contains("30 天"),
            "{t}"
        );
        assert!(t.ends_with("0123456789ab"), "{t}");
        assert!(t.chars().count() <= WINDOW_MAX);
        let t = window_text("brain", true, 1, 0, None, &sha).unwrap();
        assert!(t.starts_with("還原第二大腦合併"), "{t}");
        assert!(window_text("persona", false, 1, 0, None, &sha)
            .unwrap()
            .contains("永久"));
        // roles never open a window; a bad sha never makes one
        assert_eq!(window_text("role", false, 1, 0, Some(30), &sha), None);
        assert_eq!(window_text("persona", false, 1, 0, Some(30), "xyz"), None);
        // too long ⇒ no window, not a cut one
        let huge = window_text(
            "persona",
            false,
            usize::MAX,
            usize::MAX,
            Some(u32::MAX),
            &sha,
        );
        assert_eq!(huge, None);
    }
}
