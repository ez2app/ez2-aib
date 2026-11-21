//! G13 (N-web-reply-in-thread; 0eed7 EVID-hp-0eed7-b8bb95a7 step 7; IFACE-reply-in-thread-a9136
//! -1119): a seat called in a shared conversation answers in its own window; at the end of that
//! turn aibd posts the answer back into the conversation, as that seat, through the same path as
//! its own thread_send (members, pace, safe → dgrs refused).
//!
//! - [`note`]: a thread post written into a seat's input (seat_send::redeliver) is that seat's
//!   newest origin `{thread, post_id, at}`.
//! - [`heard`]: a provider's turn-end line (Claude `type:"result"`) gives the answer text; it is
//!   sent to the task [`start`] runs, which posts it unless the seat already posted to that
//!   thread since the origin, the answer is empty or an error. Codex seats: next slice.
//! - A turn that ended in an error, a seat that could not start, a turn cut by a timeout or a
//!   stop ([`failed`]): a post 「沒做成:<原因>」 (`auto: "turn_failed"`, the reason cleaned:
//!   [`clean_reason`]) and a `seat.turn_failed` event, a 注意 card (519d0 05:2x; 0eed7
//!   VERIFY-brandhome 80b0cade: 「Not logged in」 never came back).
//! - The post carries `auto: "turn_end"` and `reply_to: <post_id>`, idem `turn:<post_id>`
//!   (one answer per origin); `@<seat>` in the text calls that member.
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

/// Where a seat's current turn came from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Origin {
    pub thread: String,
    pub post_id: String,
    pub at: i64,
}

fn origins() -> std::sync::MutexGuard<'static, HashMap<String, Origin>> {
    static O: OnceLock<Mutex<HashMap<String, Origin>>> = OnceLock::new();
    O.get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

/// The conversation `seat`'s current turn came from, if any (work_audit names it).
pub fn thread_of(seat: &str) -> Option<String> {
    origins().get(seat).map(|o| o.thread.clone())
}

/// A thread post was written into `seat`'s input.
pub fn note(seat: &str, thread: &str, post_id: &str, at: i64) {
    origins().insert(
        seat.to_owned(),
        Origin {
            thread: thread.to_owned(),
            post_id: post_id.to_owned(),
            at,
        },
    );
}

/// The origin of `seat`'s turn, taken (one answer per origin).
pub fn take(seat: &str) -> Option<Origin> {
    origins().remove(seat)
}

/// A turn's answer as the provider gave it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Answer {
    pub seat: String,
    pub text: String,
    /// The turn did not get done: why (already cleaned).
    pub failed: Option<String>,
}

/// The longest reason kept, in characters.
pub const REASON_MAX: usize = 200;

/// A failure reason fit to show in a shared conversation: one line, no control characters, at
/// most [`REASON_MAX`] characters; a word that could be a secret (24 or more key-like
/// characters), what follows `token` / `key` / `secret` / `password` / `bearer`, a URL's query
/// and a home folder's user name are masked.
pub fn clean_reason(raw: &str) -> String {
    let one: String = raw
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let mut out: Vec<String> = Vec::new();
    let mut mask_next = false;
    for w in one.split_whitespace() {
        if mask_next {
            out.push("…".into());
            mask_next = false;
            continue;
        }
        let lower = w.to_ascii_lowercase();
        let named = ["token", "key", "secret", "password", "bearer"]
            .iter()
            .find(|k| lower.contains(*k));
        let mut w = w.to_owned();
        if let Some(k) = named {
            // `token=abc` / `key:abc` keep the name; a bare `Bearer` masks the next word
            match w.find(['=', ':']) {
                Some(i) if i + 1 < w.len() => w = format!("{}…", &w[..=i]),
                _ if lower.trim_end_matches([':', '=']) == *k => mask_next = true,
                _ => {}
            }
        }
        if let Some(i) = w.find('?').filter(|_| w.contains("://")) {
            w = format!("{}?…", &w[..i]);
        }
        for home in ["/Users/", "/home/", "\\Users\\"] {
            if let Some(i) = w.find(home) {
                let rest = &w[i + home.len()..];
                let end = rest.find(['/', '\\']).unwrap_or(rest.len());
                w = format!("{}{home}…{}", &w[..i], &rest[end..]);
            }
        }
        let keyish = w
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || "_-+/=.".contains(*c))
            .count();
        if keyish >= 24 && keyish * 10 >= w.chars().count() * 9 {
            w = "…".into();
        }
        out.push(w);
    }
    let s = out.join(" ");
    if s.chars().count() <= REASON_MAX {
        return if s.is_empty() {
            "沒有說原因".into()
        } else {
            s
        };
    }
    let cut: String = s.chars().take(REASON_MAX - 1).collect();
    format!("{cut}…")
}

/// The reason in a Claude turn-end line that ended in an error.
pub fn error_of(line: &[u8]) -> Option<String> {
    let v: Value = serde_json::from_slice(line).ok()?;
    if v.get("type").and_then(Value::as_str) != Some("result") || v["is_error"] != json!(true) {
        return None;
    }
    let said = v
        .get("result")
        .and_then(Value::as_str)
        .filter(|t| !t.trim().is_empty())
        .or_else(|| v.get("subtype").and_then(Value::as_str))
        .unwrap_or("");
    Some(clean_reason(said))
}

/// `seat`'s turn will not be answered (it could not start, timed out, was stopped, ended
/// without an answer): when the turn came from a conversation, 「沒做成」 goes back to it.
/// Never blocks.
pub fn failed(seat: &str, why: &str) {
    if !origins().contains_key(seat) {
        return;
    }
    send(Answer {
        seat: seat.to_owned(),
        text: String::new(),
        failed: Some(clean_reason(why)),
    });
}

/// Why a session's end left its turn unanswered, in words for the conversation.
pub fn ended_why(state: &str, exit_code: Option<i32>) -> String {
    match (state, exit_code) {
        ("timeout", _) => "逾時,這一席被停下".into(),
        ("killed", _) => "這一席被停下".into(),
        ("stop_unconfirmed", _) => "這一席結束了、停不確定".into(),
        (_, Some(0)) => "這一席結束了、沒有回答".into(),
        (_, Some(c)) => format!("這一席異常結束(結束碼 {c})"),
        _ => "這一席結束了、沒有回答".into(),
    }
}

fn send(a: Answer) {
    if let Some(tx) = channel().get() {
        let _ = tx.send(a);
    }
}

fn channel() -> &'static OnceLock<tokio::sync::mpsc::UnboundedSender<Answer>> {
    static TX: OnceLock<tokio::sync::mpsc::UnboundedSender<Answer>> = OnceLock::new();
    &TX
}

/// The answer in a Claude turn-end line: its `result` text, unless the turn ended in an error
/// or said nothing.
pub fn answer_of(line: &[u8]) -> Option<String> {
    let v: Value = serde_json::from_slice(line).ok()?;
    if v.get("type").and_then(Value::as_str) != Some("result") || v["is_error"] == json!(true) {
        return None;
    }
    let t = v.get("result").and_then(Value::as_str)?.trim();
    (!t.is_empty()).then(|| t.to_owned())
}

/// A turn-end line of `seat` (whole, not cut): its answer goes to the posting task when the
/// turn came from a conversation. Never blocks.
pub fn heard(seat: &str, line: &[u8]) {
    let has_origin = origins().contains_key(seat);
    if !has_origin {
        return;
    }
    if let Some(why) = error_of(line) {
        send(Answer {
            seat: seat.to_owned(),
            text: String::new(),
            failed: Some(why),
        });
        return;
    }
    let Some(text) = answer_of(line) else {
        take(seat);
        return;
    };
    send(Answer {
        seat: seat.to_owned(),
        text,
        failed: None,
    });
}

/// The text kept in one post: at most `max` bytes, cut on a character, with a note.
pub fn fit(text: &str, max: usize) -> String {
    const MORE: &str = "\n…(完整內容在該席視窗)";
    if text.len() <= max {
        return text.to_owned();
    }
    let mut end = max.saturating_sub(MORE.len());
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}{MORE}", &text[..end])
}

/// The members `text` calls with `@<id>` (whole ids, the sender left out), as seat ids.
pub fn called(text: &str, members: &[String], sender: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for part in text.split('@').skip(1) {
        let id: String = part
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
            .collect();
        let key = format!("seat:{id}");
        if !id.is_empty() && key != sender && members.contains(&key) && !out.contains(&id) {
            out.push(id);
        }
    }
    out
}

/// Whether `seat` posted into the thread itself at or after `at` (then nothing is brought back).
pub fn answered_itself(posts: &[aib_store::MessageRow], seat: &str, at: i64) -> bool {
    let key = format!("seat:{seat}");
    posts
        .iter()
        .any(|p| p.from_key == key && p.created_unix >= at && p.item.get("auto").is_none())
}

/// Run the posting task (once, at aibd's start).
pub fn start(st: crate::api::AppState) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Answer>();
    if channel().set(tx).is_err() {
        return;
    }
    tokio::spawn(async move {
        while let Some(a) = rx.recv().await {
            post(st.clone(), a).await;
        }
    });
}

async fn post(st: crate::api::AppState, a: Answer) {
    let Some(o) = take(&a.seat) else {
        return;
    };
    let (db, t) = (st.messages.clone(), o.thread.clone());
    let read = tokio::task::spawn_blocking(move || {
        let posts = db.posts_page(&t, None, aib_store::PAGE_MAX).ok()?.0;
        // an item's thread is its assignments now, not the members written at open (0eed7
        // VERIFY-g13 M1: S73's thread had none written, so nobody was called)
        let row = db.thread_get(&t).ok()??;
        let members: Vec<String> = crate::threads::members_now(&row, db.thread_members(&t).ok()?)
            .into_iter()
            .map(|m| m.member)
            .collect();
        Some((posts, members))
    })
    .await;
    let Ok(Some((posts, members))) = read else {
        return;
    };
    if let Some(why) = &a.failed {
        // a 注意 card whether or not the post can be written
        let payload = json!({"seat_id": a.seat, "thread_id": o.thread, "reply_to": o.post_id,
            "reason": why});
        crate::seat_send::journal_event(&st, "seat.turn_failed", payload).await;
    }
    if answered_itself(&posts, &a.seat, o.at) {
        return;
    }
    // a failure calls nobody: its text is the reason, not the seat's words
    let (text, to) = match &a.failed {
        Some(why) => (format!("沒做成:{why}"), Vec::new()),
        None => {
            let text = fit(&a.text, crate::threads::BODY_MAX);
            let to = called(&text, &members, &format!("seat:{}", a.seat));
            (text, to)
        }
    };
    let body = json!({"type": crate::threads::SEND, "thread_id": o.thread, "body": text,
        "to": to, "idem_key": format!("turn:{}", o.post_id)});
    let r = crate::threads::seat_act_auto(st, &a.seat, body, &o.post_id, a.failed.clone()).await;
    tracing::info!(seat = %a.seat, thread = %o.thread, status = %r.status(), "turn answer brought back");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_answer_is_the_result_text_of_a_turn_that_did_not_fail() {
        let ok = r#"{"type":"result","subtype":"success","is_error":false,"result":" 好了 @5d33a 請審 "}"#;
        assert_eq!(
            answer_of(ok.as_bytes()).as_deref(),
            Some("好了 @5d33a 請審")
        );
        let err = br#"{"type":"result","is_error":true,"result":"boom"}"#;
        assert_eq!(answer_of(err), None, "reason: error_brought_back");
        assert_eq!(answer_of(br#"{"type":"result","result":"  "}"#), None);
        assert_eq!(answer_of(br#"{"type":"assistant","result":"x"}"#), None);
    }

    #[test]
    fn at_calls_only_members_other_than_the_sender() {
        let m: Vec<String> = ["seat:5d33a", "seat:a9136", "seat:043dd"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert_eq!(
            called(
                "@5d33a 請審，@a9136 自己,@zzzzz 不在,@5d33a 再一次",
                &m,
                "seat:a9136"
            ),
            vec!["5d33a".to_owned()],
            "reason: called_wrong"
        );
        assert!(called("沒有呼叫", &m, "seat:a9136").is_empty());
    }

    #[test]
    fn a_long_answer_is_cut_on_a_character_with_a_note() {
        let t = "字".repeat(20);
        let f = fit(&t, 40);
        assert!(f.len() <= 40, "reason: over_max {}", f.len());
        assert!(f.ends_with("(完整內容在該席視窗)"));
        assert_eq!(fit("短", 40), "短");
    }

    #[test]
    fn a_seat_that_posted_itself_since_the_call_gets_nothing_brought_back() {
        let row = |from: &str, at: i64, auto: bool| {
            let mut item = serde_json::Map::new();
            if auto {
                item.insert("auto".into(), json!("turn_end"));
            }
            aib_store::MessageRow {
                message_id: "q_x".into(),
                thread_id: "t".into(),
                from_key: from.into(),
                to_key: "t".into(),
                created: String::new(),
                created_unix: at,
                updated: String::new(),
                rev: 1,
                item,
                body_sha256: String::new(),
                len: 0,
                body: None,
                idem_owner: None,
                idem_key: None,
                journaled: false,
            }
        };
        let posts = vec![row("seat:a9136", 90, false), row("seat:5d33a", 120, false)];
        assert!(
            !answered_itself(&posts, "a9136", 100),
            "reason: older_post_counted"
        );
        let posts = vec![row("seat:a9136", 100, false)];
        assert!(
            answered_itself(&posts, "a9136", 100),
            "reason: own_post_missed"
        );
        let posts = vec![row("seat:a9136", 150, true)];
        assert!(
            !answered_itself(&posts, "a9136", 100),
            "reason: auto_counted_as_own"
        );
    }

    #[test]
    fn a_turn_that_ended_in_an_error_gives_its_cleaned_reason() {
        let l = br#"{"type":"result","subtype":"success","is_error":true,"result":"Not logged in \u00b7 Please run /login"}"#;
        assert_eq!(
            error_of(l).as_deref(),
            Some("Not logged in · Please run /login")
        );
        assert_eq!(
            error_of(br#"{"type":"result","is_error":true,"subtype":"error_max_turns"}"#)
                .as_deref(),
            Some("error_max_turns")
        );
        assert_eq!(
            error_of(br#"{"type":"result","is_error":false,"result":"ok"}"#),
            None
        );
    }

    #[test]
    fn a_reason_keeps_no_secret_path_or_query() {
        let r = clean_reason(
            "auth failed token=abcd1234 Bearer zzz key: q\n at /Users/someone/x.json \
             https://h.example/p?sig=1 sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        );
        for bad in ["abcd1234", "zzz", "someone", "sig=1", "AAAAAAAA", "\n"] {
            assert!(!r.contains(bad), "reason: kept {bad} in {r}");
        }
        assert!(
            r.contains("token=…") && r.contains("/Users/…/x.json"),
            "{r}"
        );
        assert_eq!(clean_reason(" \t "), "沒有說原因");
        assert_eq!(clean_reason(&"字".repeat(500)).chars().count(), REASON_MAX);
    }

    #[test]
    fn an_end_says_why_the_turn_was_not_answered() {
        assert_eq!(ended_why("timeout", None), "逾時,這一席被停下");
        assert_eq!(ended_why("exited", Some(1)), "這一席異常結束(結束碼 1)");
        assert_eq!(ended_why("exited", Some(0)), "這一席結束了、沒有回答");
    }

    #[test]
    fn a_failure_goes_back_only_for_a_turn_from_a_conversation() {
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Answer>();
        let _ = channel().set(tx);
        failed("f_none", "x");
        note("f_one", "t_1", "q_a", 5);
        failed("f_one", "逾時");
        let mut got = Vec::new();
        while let Ok(a) = rx.try_recv() {
            got.push(a);
        }
        // another test may have set the channel first: then only check nothing went for f_none
        assert!(
            !got.iter().any(|a| a.seat == "f_none"),
            "reason: no_origin_sent"
        );
        if let Some(a) = got.iter().find(|a| a.seat == "f_one") {
            assert_eq!(a.failed.as_deref(), Some("逾時"));
        }
        take("f_one");
    }

    #[test]
    fn one_answer_per_origin() {
        note("s1", "t_1", "q_a", 5);
        note("s1", "t_1", "q_b", 6);
        assert_eq!(
            take("s1").map(|o| o.post_id),
            Some("q_b".into()),
            "reason: newest_not_kept"
        );
        assert_eq!(take("s1"), None, "reason: taken_twice");
    }
}
