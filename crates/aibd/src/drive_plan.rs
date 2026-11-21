//! S79-rust 3b (DECISION-council-driveweb r3): the work orders aib-drive hands aibd for web
//! seats. aib-drive's act round, run by aibd's drive wake, prints one `AIBD-SEND {json}` line
//! per work order to a web seat (aib-drive source.rs `ToWeb`); aibd reads the child's stdout
//! (no file an AI could write a line into), keeps only what passes [`parse`], and hands the
//! plans to the executor the server installed ([`install`]). The executor sends them as the
//! system, the origin and the pacing decided there; drive decides nothing about either.

use serde_json::Value;
use std::collections::BTreeSet;
use std::sync::OnceLock;

/// The line prefix aib-drive prints (aib-drive `source::PLAN_PREFIX`).
pub const PREFIX: &str = "AIBD-SEND ";
/// What aibd tells aib-drive: this round is aibd's, a plan line has a reader.
pub const BY_AIBD_ENV: &str = "AIB_DRIVE_BY_AIBD";
/// The web seats aibd has now, comma-separated (aib-drive `source::WEB_SEATS_ENV`).
pub const WEB_SEATS_ENV: &str = "AIB_DRIVE_WEB_SEATS";
/// At most this much of the child's stdout is read; the rest is dropped unread.
pub const OUT_MAX: usize = 256 * 1024;
/// At most this many plans are taken from one round (the rest are dropped and said).
pub const ROUND_MAX: usize = 8;
/// A plan's text at most this long (the 1.x letter limit).
pub const TEXT_MAX: usize = 64 * 1024;
/// A plan's send_key at most this long (seat_send's SEND_KEY_MAX).
pub const KEY_MAX: usize = 128;

/// One work order for a web seat, as drive planned it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    /// The seat's five-hex code (a web seat aibd listed).
    pub to: String,
    pub text: String,
    /// drive's intent key: the same key again is the same letter.
    pub send_key: String,
}

/// What one round's stdout gave.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Read {
    pub plans: Vec<Plan>,
    /// Plan lines refused (not the shape, not a listed web seat, not a work order, too long,
    /// a key seen before this round, over [`ROUND_MAX`]).
    pub refused: usize,
}

fn five_hex(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The plans in `out` (an act round's stdout) for seats in `web`. Only a line that is the
/// prefix and one JSON object with exactly `to`, `text`, `send_key` and `action` counts, the
/// action a dispatch, the seat one aibd listed; any other plan line is refused, any other
/// line is not a plan.
pub fn parse(out: &[u8], web: &BTreeSet<String>) -> Read {
    let mut r = Read::default();
    let mut keys = BTreeSet::new();
    for line in out.split(|b| *b == b'\n') {
        let Some(rest) = line.strip_prefix(PREFIX.as_bytes()) else {
            continue;
        };
        let ok = serde_json::from_slice::<Value>(rest)
            .ok()
            .and_then(|v| v.as_object().cloned())
            .filter(|o| {
                o.len() == 4
                    && ["to", "text", "send_key", "action"]
                        .iter()
                        .all(|k| o.get(*k).is_some_and(Value::is_string))
            })
            .and_then(|o| {
                let s = |k: &str| {
                    o.get(k)
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned()
                };
                (s("action") == "dispatch").then(|| Plan {
                    to: s("to"),
                    text: s("text"),
                    send_key: s("send_key"),
                })
            })
            .filter(|p| {
                five_hex(&p.to)
                    && web.contains(&p.to)
                    && !p.text.trim().is_empty()
                    && p.text.len() <= TEXT_MAX
                    && !p.send_key.is_empty()
                    && p.send_key.len() <= KEY_MAX
                    && p.send_key.bytes().all(|b| b.is_ascii_graphic())
            });
        match ok {
            Some(p) if r.plans.len() < ROUND_MAX && keys.insert(p.send_key.clone()) => {
                r.plans.push(p);
            }
            _ => r.refused += 1,
        }
    }
    r
}

/// The web seats now (codes), as the server reads them.
pub type WebSeats = Box<dyn Fn() -> Vec<String> + Send + Sync>;
/// Where a round's plans go (the server's executor).
pub type Sink = Box<dyn Fn(Vec<Plan>) + Send + Sync>;

static WEB: OnceLock<WebSeats> = OnceLock::new();
static SINK: OnceLock<Sink> = OnceLock::new();

/// The server's side: how to list the web seats and where the plans go. Once; a second call
/// changes nothing (returns false).
pub fn install(web: WebSeats, sink: Sink) -> bool {
    let w = WEB.set(web).is_ok();
    w && SINK.set(sink).is_ok()
}

/// The web seats to tell drive, or `None` while nothing reads a plan (no executor installed):
/// then drive is not told any, and every seat gets its 1.x letter as before 3b.
pub fn web_now() -> Option<BTreeSet<String>> {
    SINK.get()?;
    let list = WEB.get()?;
    Some(list().into_iter().filter(|s| five_hex(s)).collect())
}

/// Hand a round's plans to the executor (none installed: dropped, said once by the caller).
pub fn hand(plans: Vec<Plan>) -> bool {
    match SINK.get() {
        Some(s) => {
            s(plans);
            true
        }
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn web() -> BTreeSet<String> {
        ["7ebca", "abcde"].iter().map(|s| (*s).to_owned()).collect()
    }

    fn line(v: Value) -> String {
        format!("{PREFIX}{v}\n")
    }

    #[test]
    fn only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan() {
        let good = serde_json::json!({"to": "7ebca", "text": "派工: N-1 → 7ebca", "send_key": "k:1", "action": "dispatch"});
        let mut out = String::from("some other line\n{\"to\":\"7ebca\"}\n");
        out += &line(good.clone());
        // not listed, a nudge, an extra field, a missing field, a key with a space, empty text
        let mut bad = good.clone();
        bad["to"] = "51234".into();
        out += &line(bad);
        let mut bad = good.clone();
        bad["action"] = "nudge".into();
        out += &line(bad);
        let mut bad = good.clone();
        bad["origin_seat"] = "519d0".into();
        out += &line(bad);
        let mut bad = good.clone();
        bad.as_object_mut().unwrap().remove("send_key");
        out += &line(bad);
        let mut bad = good.clone();
        bad["send_key"] = "k 2".into();
        out += &line(bad);
        let mut bad = good.clone();
        bad["text"] = " ".into();
        bad["send_key"] = "k:3".into();
        out += &line(bad);
        // the same key twice in one round is one plan
        out += &line(good);
        out += "AIBD-SEND not json\n";
        let r = parse(out.as_bytes(), &web());
        assert_eq!(
            r.plans,
            [Plan {
                to: "7ebca".into(),
                text: "派工: N-1 → 7ebca".into(),
                send_key: "k:1".into()
            }],
            "reason: wrong_plans"
        );
        assert_eq!(r.refused, 8, "reason: refused_count");
    }

    #[test]
    fn a_round_gives_at_most_round_max_plans() {
        let mut out = String::new();
        for i in 0..ROUND_MAX + 3 {
            out += &line(
                serde_json::json!({"to": "abcde", "text": "派工: x", "send_key": format!("k:{i}"), "action": "dispatch"}),
            );
        }
        let r = parse(out.as_bytes(), &web());
        assert_eq!(r.plans.len(), ROUND_MAX, "reason: over_round_max");
        assert_eq!(r.refused, 3);
        assert_eq!(r.plans[0].send_key, "k:0", "reason: not_in_order");
    }
}
