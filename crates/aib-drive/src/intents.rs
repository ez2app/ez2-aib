//! S79 E4 the intents ledger (IFACE-s79 §3): every message act really sends goes through
//! `intent → recheck → sending → send → done / abandoned`, written to
//! `<state root>/drive/intents.jsonl` with aib-ledger (one fixed lock, one whole line per
//! write, replayed in file order; an incomplete ledger refuses every write).
//!
//! Killed and run again, act never sends one message twice:
//! * only `intent` written (killed before or during the recheck): carried on — recheck, then
//!   send. Nothing went out yet.
//! * `sending` written but no `done` (killed while sending): it may have gone out. It is
//!   not sent again; it is closed as `abandoned` with that reason, for a person to look at.
//! * `done` or `abandoned`: already handled, nothing is done.
//!
//! The key is act's dedupe key (`action:PR or node:head:seat`, act::key). The crate does
//! the bookkeeping only; what "recheck" and "send" mean is the caller's (act, E5).
//! Lines are `{"v":1,"kind":"intent|sending|done|abandoned","key","at_ms",…}`; a line out
//! of order for its key (done before sending, a second intent) is a ledger problem.

use std::collections::BTreeMap;
use std::path::Path;
use std::time::Duration;

use aib_ledger::Refused;
use serde_json::{json, Map, Value};

/// The ledger and its lock, inside `<state root>/drive/`.
pub const FILE: &str = "intents.jsonl";
pub const LOCK: &str = "intents.lock";
/// Longest line (aib-ledger refuses a longer one instead of cutting it).
pub const MAX_LINE: usize = 16 * 1024;
/// How long a write waits for the lock.
pub const WAIT: Duration = Duration::from_secs(5);

/// Where one key stands.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Open,
    Sending,
    Done,
    Abandoned(String),
}

/// What [`carry_out`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Rechecked, sent and written `done`.
    Sent,
    /// The recheck said no: written `abandoned` with its reason, nothing sent.
    Dropped(String),
    /// The send failed: written `abandoned`, nothing (known to be) sent.
    SendFailed(String),
    /// A run before was killed while sending: not sent again, written `abandoned`.
    NotResent,
    /// `done` already: nothing done.
    AlreadyDone,
    /// `abandoned` already (its reason): nothing done.
    AlreadyAbandoned(String),
}

/// Every key's state from the ledger text, and its problems (`line <n>: …`).
pub fn load(text: &str) -> (BTreeMap<String, State>, Vec<String>) {
    let mut m: BTreeMap<String, State> = BTreeMap::new();
    let errors = aib_ledger::replay(text, MAX_LINE, |o| {
        let key = o
            .get("key")
            .and_then(Value::as_str)
            .filter(|k| !k.is_empty())
            .ok_or("no key")?;
        let kind = o.get("kind").and_then(Value::as_str).ok_or("no kind")?;
        let next = match (kind, m.get(key)) {
            ("intent", None) => State::Open,
            ("sending", Some(State::Open)) => State::Sending,
            ("done", Some(State::Sending)) => State::Done,
            ("abandoned", Some(State::Open | State::Sending)) => State::Abandoned(
                o.get("why")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
            ),
            (k, cur) => return Err(format!("{k} after {cur:?} for {key}")),
        };
        m.insert(key.to_owned(), next);
        Ok(())
    });
    (m, errors)
}

fn line(kind: &str, key: &str, now_ms: i64, extra: &Map<String, Value>) -> String {
    let mut o = Map::new();
    o.insert("v".into(), json!(1));
    o.insert("kind".into(), json!(kind));
    o.insert("key".into(), json!(key));
    o.insert("at_ms".into(), json!(now_ms));
    for (k, v) in extra {
        o.entry(k.clone()).or_insert_with(|| v.clone());
    }
    Value::Object(o).to_string()
}

fn why(text: &str) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("why".into(), json!(text));
    m
}

fn refused(r: Refused<String>) -> String {
    match r {
        Refused::Lock(e) | Refused::Read(e) | Refused::Rule(e) => format!("{FILE}: {e}"),
        Refused::Incomplete(v) => format!("{FILE} incomplete: {}", v.join("; ")),
        Refused::Append(a) => format!("{FILE}: append {a:?}"),
    }
}

/// One write under the lock: `decide` sees every key's state and returns the line to add
/// (or none) and what to hand back.
fn write<T>(
    dir: &Path,
    decide: impl FnOnce(&BTreeMap<String, State>) -> Result<(Option<String>, T), String>,
) -> Result<T, String> {
    aib_ledger::write_if_complete(
        &dir.join(LOCK),
        &dir.join(FILE),
        WAIT,
        MAX_LINE,
        load,
        decide,
    )
    .map_err(refused)
}

/// The state `key` must still be in for the next step; anything else is a second writer.
fn must_be(m: &BTreeMap<String, State>, key: &str, want: &State) -> Result<(), String> {
    match m.get(key) {
        Some(s) if s == want => Ok(()),
        s => Err(format!(
            "{key} is {s:?}, not {want:?} (another act wrote it)"
        )),
    }
}

enum Step {
    Go,
    Stop(Outcome),
}

/// Send one message at most once. `fields` go on the intent line (to, first_line, …);
/// `recheck` looks again at what the message depends on (Err = the reason not to send);
/// `send` sends it (Ok = what to keep on the `done` line, such as the delivery stage and the
/// dm id; Err = it failed). Err from here = the ledger could not be written or
/// read cleanly; then nothing was sent.
pub fn carry_out(
    dir: &Path,
    key: &str,
    fields: &Map<String, Value>,
    now_ms: i64,
    recheck: impl FnOnce() -> Result<(), String>,
    send: impl FnOnce() -> Result<Map<String, Value>, String>,
) -> Result<Outcome, String> {
    let first = write(dir, |m| {
        Ok(match m.get(key) {
            None => (Some(line("intent", key, now_ms, fields)), Step::Go),
            Some(State::Open) => (None, Step::Go),
            Some(State::Sending) => (
                Some(line(
                    "abandoned",
                    key,
                    now_ms,
                    &why("interrupted while sending: it may have gone out, not sent again"),
                )),
                Step::Stop(Outcome::NotResent),
            ),
            Some(State::Done) => (None, Step::Stop(Outcome::AlreadyDone)),
            Some(State::Abandoned(w)) => (None, Step::Stop(Outcome::AlreadyAbandoned(w.clone()))),
        })
    })?;
    if let Step::Stop(o) = first {
        return Ok(o);
    }
    if let Err(no) = recheck() {
        return write(dir, |m| {
            must_be(m, key, &State::Open)?;
            Ok((
                Some(line("abandoned", key, now_ms, &why(&no))),
                Outcome::Dropped(no.clone()),
            ))
        });
    }
    write(dir, |m| {
        must_be(m, key, &State::Open)?;
        Ok((Some(line("sending", key, now_ms, &Map::new())), ()))
    })?;
    match send() {
        Ok(receipt) => write(dir, |m| {
            must_be(m, key, &State::Sending)?;
            Ok((Some(line("done", key, now_ms, &receipt)), Outcome::Sent))
        }),
        Err(e) => write(dir, |m| {
            must_be(m, key, &State::Sending)?;
            Ok((
                Some(line(
                    "abandoned",
                    key,
                    now_ms,
                    &why(&format!("send failed: {e}")),
                )),
                Outcome::SendFailed(e.clone()),
            ))
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::fs;
    use std::path::PathBuf;

    const K: &str = "nudge:dm/q_1:-:a9136";
    const NOW: i64 = 1_794_700_800_000;

    struct Dir(PathBuf);
    impl Dir {
        fn new(tag: &str) -> Dir {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos());
            let p = std::env::temp_dir().join(format!(
                "aib-drive-intents-{tag}-{}-{nanos} 有 空白",
                std::process::id()
            ));
            fs::create_dir_all(&p).expect("mkdir");
            Dir(p)
        }
        fn kinds(&self) -> Vec<String> {
            fs::read_to_string(self.0.join(FILE))
                .unwrap_or_default()
                .lines()
                .map(|l| {
                    let v: Value = serde_json::from_str(l).expect("json");
                    v["kind"].as_str().unwrap_or_default().to_owned()
                })
                .collect()
        }
        fn seed(&self, kinds: &[&str]) {
            let text: String = kinds
                .iter()
                .map(|k| format!("{}\n", line(k, K, NOW, &Map::new())))
                .collect();
            fs::write(self.0.join(FILE), text).expect("seed");
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn fields() -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("to".into(), json!("a9136"));
        m.insert(
            "first_line".into(),
            json!("派工: 修 #757:跟 main 衝突 → a9136"),
        );
        m
    }

    /// Runs carry_out and counts the sends.
    fn run(d: &Dir, recheck: Result<(), String>, send: Result<(), String>) -> (Outcome, u32) {
        let sent = Cell::new(0);
        let o = carry_out(
            &d.0,
            K,
            &fields(),
            NOW,
            || recheck,
            || {
                sent.set(sent.get() + 1);
                send.map(|()| {
                    let mut r = Map::new();
                    r.insert("stage".into(), json!("delivered"));
                    r
                })
            },
        )
        .expect("ledger");
        (o, sent.get())
    }

    #[test]
    fn a_message_is_sent_once_and_a_second_run_does_nothing() {
        let d = Dir::new("once");
        assert_eq!(run(&d, Ok(()), Ok(())), (Outcome::Sent, 1));
        assert_eq!(d.kinds(), ["intent", "sending", "done"]);
        assert_eq!(run(&d, Ok(()), Ok(())), (Outcome::AlreadyDone, 0));
        assert_eq!(d.kinds().len(), 3);
        let text = fs::read_to_string(d.0.join(FILE)).expect("read");
        assert!(
            text.contains("派工: 修 #757"),
            "the intent keeps its fields: {text}"
        );
    }

    /// E4 (驗收表:殺掉重跑不重發): killed while sending ⇒ not sent again on the next run.
    #[test]
    fn killed_while_sending_is_not_sent_again() {
        let d = Dir::new("killed");
        d.seed(&["intent", "sending"]);
        assert_eq!(run(&d, Ok(()), Ok(())), (Outcome::NotResent, 0));
        assert_eq!(d.kinds(), ["intent", "sending", "abandoned"]);
        assert_eq!(
            run(&d, Ok(()), Ok(())),
            (
                Outcome::AlreadyAbandoned(
                    "interrupted while sending: it may have gone out, not sent again".into()
                ),
                0
            )
        );
    }

    /// Killed after the intent, before anything went out ⇒ carried on: sent once.
    #[test]
    fn killed_before_sending_is_carried_on() {
        let d = Dir::new("carry");
        d.seed(&["intent"]);
        assert_eq!(run(&d, Ok(()), Ok(())), (Outcome::Sent, 1));
        assert_eq!(d.kinds(), ["intent", "sending", "done"]);
    }

    #[test]
    fn a_recheck_that_says_no_sends_nothing_and_stays_closed() {
        let d = Dir::new("recheck");
        assert_eq!(
            run(&d, Err("seat now busy".into()), Ok(())),
            (Outcome::Dropped("seat now busy".into()), 0)
        );
        assert_eq!(d.kinds(), ["intent", "abandoned"]);
        assert_eq!(
            run(&d, Ok(()), Ok(())),
            (Outcome::AlreadyAbandoned("seat now busy".into()), 0)
        );
    }

    #[test]
    fn a_failed_send_is_abandoned_not_done() {
        let d = Dir::new("fail");
        assert_eq!(
            run(&d, Ok(()), Err("aib dm rc 1".into())),
            (Outcome::SendFailed("aib dm rc 1".into()), 1)
        );
        assert_eq!(d.kinds(), ["intent", "sending", "abandoned"]);
    }

    #[test]
    fn an_incomplete_or_out_of_order_ledger_sends_nothing() {
        let d = Dir::new("broken");
        fs::write(d.0.join(FILE), "not json\n").expect("seed");
        let sent = Cell::new(false);
        let e = carry_out(
            &d.0,
            K,
            &fields(),
            NOW,
            || Ok(()),
            || {
                sent.set(true);
                Ok(Map::new())
            },
        )
        .expect_err("incomplete");
        assert!(e.contains("incomplete"), "{e}");
        assert!(!sent.get());
        let (_, errors) = load(&format!("{}\n", line("done", K, NOW, &Map::new())));
        assert_eq!(errors.len(), 1, "done before sending: {errors:?}");
    }
}
