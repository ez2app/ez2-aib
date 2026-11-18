//! N-workspace (b4297, SPEC-workspace-multiseat §8): a seat not called (`@`) in a shared
//! conversation still learns what was said there. When a text starts a new turn of the seat
//! (it was not in one), aibd puts the posts of its conversations it has not seen in front of
//! that text, as a block of its own, and moves the seat's read mark there: "只需知悉", no
//! answer asked. Posts the seat wrote itself and posts that called it (those reach it as their
//! own delivery) are left out.
//!
//! The library is SQLite; this runs where the input is written (off the async runtime).

use aib_store::MessageDb;
use serde_json::Value;
use std::sync::Arc;

/// At most this many unseen posts per conversation (the newest ones), and this many bytes of
/// block in all: a seat that was away long reads the latest, not everything.
pub const PER_THREAD: u32 = 10;
pub const MAX_BLOCK: usize = 8 * 1024;
/// One post's text in the block is cut to this many characters.
pub const MAX_POST_CHARS: usize = 600;

/// What to put in front of a seat's next turn and where its read marks go once it is written.
pub struct Unseen {
    pub block: String,
    /// `(thread_id, last post id)`: set as the seat's read mark after the write.
    pub marks: Vec<(String, String)>,
}

/// What [`crate::sessions::Sessions`] asks before a text starts a turn.
pub trait CatchUp: Send + Sync {
    fn unseen(&self, seat_id: &str) -> Option<Unseen>;
    fn seen(&self, seat_id: &str, marks: &[(String, String)]);
}

/// The message library's catch-up; the seat store tells a seat's mode.
pub struct Library(pub Arc<MessageDb>, pub Arc<aib_store::Store>);

fn flag(p: &aib_store::MessageRow, k: &str) -> Option<bool> {
    p.item.get(k).and_then(Value::as_bool)
}

/// Who wrote a post: a seat's code, the person, or a user folder page (5d33a M1: an AI can
/// write a user page's words; they are never shown as the person's).
fn who(p: &aib_store::MessageRow) -> String {
    match p.from_key.strip_prefix("seat:") {
        Some(id) => format!("@{}", crate::seats::seat_code(id)),
        None if flag(p, "user_page") == Some(true) => "用戶頁".to_owned(),
        None => "真人".to_owned(),
    }
}

/// 5d33a M1: a dgrs seat is not told what a safe sender wrote (a safe seat or a user page
/// cannot reach it by calling it, nor by not calling it). A post that does not say (older
/// rows) counts as safe. 5d33a CHECKLIST-wave3 E1: and nothing reaches a dgrs seat while this
/// machine's switch is off (read now, each time: E2), the same rule as typing and sending.
fn reaches(p: &aib_store::MessageRow, to_dgrs: bool, dgrs_on: bool) -> bool {
    !to_dgrs || (dgrs_on && flag(p, "sender_safe") == Some(false))
}

fn cut(text: &str) -> String {
    let mut s: String = text.chars().take(MAX_POST_CHARS).collect();
    if s.len() < text.len() {
        s.push('…');
    }
    s.replace('\n', " ")
}

impl CatchUp for Library {
    fn unseen(&self, seat_id: &str) -> Option<Unseen> {
        let me = format!("seat:{seat_id}");
        // unknown mode: judged as dgrs (told the least)
        let to_dgrs = self
            .1
            .seat_meta(seat_id)
            .ok()
            .flatten()
            .and_then(|m| m.mode)
            .is_none_or(|m| m != "safe");
        // the threads it is in now: an item's thread by the item's assignments, worked out now
        // (a9136 b1a39a86; 5d33a PREREAD 7bfb32ab N1)
        let threads = crate::threads::threads_of(&self.0, Some(&me), None, 20).ok()?;
        let mut lines = Vec::new();
        let mut marks = Vec::new();
        let mut n = 0usize;
        for t in threads {
            let (posts, _) = self.0.posts_page(&t.thread_id, None, PER_THREAD).ok()?;
            let Some(last) = posts.last() else { continue };
            let mark = self.0.read_mark(&t.thread_id, &me).ok()?;
            let after = mark
                .as_deref()
                .and_then(|m| posts.iter().position(|p| p.message_id == m))
                .map_or(0, |i| i + 1);
            let fresh: Vec<_> = posts[after..].iter().filter(|p| p.from_key != me).collect();
            let ids: Vec<String> = fresh.iter().map(|p| p.message_id.clone()).collect();
            let called: Vec<String> = self
                .0
                .deliveries_of(&t.thread_id, &ids)
                .ok()?
                .into_iter()
                .filter(|d| d.to_key == me)
                .filter_map(|d| {
                    d.item
                        .get("post_id")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .collect();
            let told: Vec<_> = fresh
                .into_iter()
                .filter(|p| !called.contains(&p.message_id))
                .filter(|p| reaches(p, to_dgrs, crate::seat_input::dgrs_input_on()))
                .collect();
            marks.push((t.thread_id.clone(), last.message_id.clone()));
            if told.is_empty() {
                continue;
            }
            lines.push(format!("【{}】", t.title));
            for p in told {
                n += 1;
                lines.push(format!(
                    "{}:{}",
                    who(p),
                    cut(p.body.as_deref().unwrap_or(""))
                ));
            }
        }
        if n == 0 {
            return None;
        }
        let mut block = format!("[aibd] 共同對話裡你沒被點名、還沒看過的 {n} 則(只需知悉,不用回):");
        for l in lines {
            if block.len() + l.len() + 1 > MAX_BLOCK {
                block.push_str("\n…(其餘略)");
                break;
            }
            block.push('\n');
            block.push_str(&l);
        }
        Some(Unseen { block, marks })
    }

    fn seen(&self, seat_id: &str, marks: &[(String, String)]) {
        let me = format!("seat:{seat_id}");
        for (t, m) in marks {
            if let Err(e) = self.0.set_read_mark(t, &me, m) {
                tracing::warn!(thread = %t, error = %e, "catch-up read mark not kept");
            }
        }
    }
}

/// `line` (one stream-json user message) with `block` as its first text block; `None` when
/// the line is not a user message this can extend (it is then written as it is).
pub fn prepend(line: &[u8], block: &str) -> Option<Vec<u8>> {
    let mut v: Value = serde_json::from_slice(line).ok()?;
    let content = v.get_mut("message")?.get_mut("content")?;
    let first = serde_json::json!({"type": "text", "text": block});
    match content {
        Value::String(s) => {
            let text = serde_json::json!({"type": "text", "text": s.clone()});
            *content = Value::Array(vec![first, text]);
        }
        Value::Array(a) => a.insert(0, first),
        _ => return None,
    }
    let mut out = v.to_string().into_bytes();
    out.push(b'\n');
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_block_goes_first_whatever_shape_the_content_has() {
        let s = br#"{"type":"user","message":{"role":"user","content":"hi"}}"#;
        let v: Value = serde_json::from_slice(&prepend(s, "B").unwrap()).unwrap();
        assert_eq!(v["message"]["content"][0]["text"], "B");
        assert_eq!(v["message"]["content"][1]["text"], "hi");
        let a =
            br#"{"type":"user","message":{"role":"user","content":[{"type":"text","text":"x"}]}}"#;
        let out = prepend(a, "B").unwrap();
        assert!(out.ends_with(b"\n") && !out[..out.len() - 1].contains(&b'\n'));
        let v: Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(v["message"]["content"][0]["text"], "B");
        assert_eq!(v["message"]["content"][1]["text"], "x");
        assert!(prepend(b"not json", "B").is_none());
        assert!(prepend(br#"{"type":"user"}"#, "B").is_none());
    }

    fn post(item: Value) -> aib_store::MessageRow {
        aib_store::MessageRow {
            message_id: "p".into(),
            thread_id: "t".into(),
            from_key: "human:0".into(),
            to_key: String::new(),
            created: String::new(),
            created_unix: 0,
            updated: String::new(),
            rev: 1,
            item: item.as_object().cloned().unwrap_or_default(),
            body: Some("x".into()),
            body_sha256: String::new(),
            len: 1,
            idem_owner: None,
            idem_key: None,
            journaled: false,
        }
    }

    /// 5d33a M1 (f0074760) and CHECKLIST-wave3 E1 / E3: what reaches a dgrs seat's next turn.
    #[test]
    fn a_dgrs_seat_hears_only_unsafe_senders_and_only_with_the_switch_on() {
        let person = post(serde_json::json!({"sender_safe": false}));
        let safe = post(serde_json::json!({"sender_safe": true}));
        let older = post(serde_json::json!({}));
        assert!(reaches(&person, true, true));
        assert!(
            !reaches(&person, true, false),
            "reason: dgrs_reached_with_switch_off"
        );
        assert!(!reaches(&safe, true, true), "reason: safe_reached_dgrs");
        assert!(
            !reaches(&older, true, true),
            "reason: unmarked_counted_unsafe"
        );
        // a safe seat hears everyone
        assert!(reaches(&safe, false, false) && reaches(&person, false, false));
    }

    #[test]
    fn a_long_post_is_cut_and_kept_on_one_line() {
        let long = "字".repeat(MAX_POST_CHARS + 5) + "\n尾";
        let c = cut(&long);
        assert_eq!(c.chars().count(), MAX_POST_CHARS + 1);
        assert!(c.ends_with('…') && !c.contains('\n'));
        assert_eq!(cut("a\nb"), "a b");
    }
}
