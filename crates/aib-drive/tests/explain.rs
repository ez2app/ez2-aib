//! S79-explain(a9136 11-17):`aib-drive explain <席>` 每個事實帶來源、時間、期限,
//! 再列自動推進寄信那關擋在哪。

use aib_drive::act::{History, Switches, SEAT_GAP_MIN};
use aib_drive::explain::{explain, to_text, Gate};
use aib_drive::seats::*;
use serde_json::Value;
use std::collections::BTreeMap;

const NOW: u64 = 1_794_672_000;
const HUB: &str = "aihub-20261104-121013-f457";
const SEAT: &str = "a9136";

#[derive(Default)]
struct Fake {
    now: Option<Known<SeatNow>>,
    beat: Option<u64>,
    dms: Vec<DmItem>,
    windows: Option<BTreeMap<String, u64>>,
}

impl Source for Fake {
    fn seats(&self, _now: u64) -> Result<Vec<String>, String> {
        Ok(vec![SEAT.into()])
    }
    fn seat_now(&self, _seat: &str) -> Known<SeatNow> {
        self.now.clone().unwrap_or(Known::Unknown("no file".into()))
    }
    fn beat(&self, _seat: &str) -> Option<u64> {
        self.beat
    }
    fn dms(&self, _seat: &str) -> Known<Vec<DmItem>> {
        Known::Known(self.dms.clone())
    }
    fn windows(&self, _hub: &str) -> Known<BTreeMap<String, u64>> {
        match &self.windows {
            Some(w) => Known::Known(w.clone()),
            None => Known::Unknown("tmux not run".into()),
        }
    }
    fn lease_owner(&self, _now: u64) -> Known<Option<String>> {
        Known::Known(None)
    }
}

fn dm(status: &str, sent: u64, read: bool) -> DmItem {
    DmItem {
        status: status.into(),
        sent_at: sent,
        read_at: read.then_some(sent + 10),
        done_at: None,
        hub: Some(HUB.into()),
        wid: Some("@4".into()),
    }
}

/// Window @4 last printed `quiet_min` ago; seat-now reply a minute ago; one read dm.
fn seat(quiet_min: u64) -> Fake {
    Fake {
        now: Some(Known::Known(SeatNow {
            at: Some(NOW - 60),
            state: Some("reply".into()),
        })),
        beat: None,
        dms: vec![dm("read", NOW - 3600, true)],
        windows: Some(BTreeMap::from([("@4".to_string(), NOW - quiet_min * 60)])),
    }
}

fn all_on() -> Switches {
    let mut s = Switches::default();
    for n in ["nudge", "open_review", "dispatch", "notify_fix"] {
        s.set(n, true);
    }
    s
}

fn gate(sw: Switches, last_sent_ms: Option<i64>) -> Gate {
    let mut h = History::default();
    if let Some(l) = last_sent_ms {
        h.last_sent_ms.insert(SEAT.into(), l);
    }
    Gate {
        switches: Some(Ok(sw)),
        history: Some(Ok(h)),
    }
}

fn run(f: &Fake, g: &Gate) -> Value {
    let shared = Shared::read(f, NOW);
    explain(f, SEAT, NOW, &shared, g)
}

fn fact<'a>(v: &'a Value, name: &str) -> &'a Value {
    v["facts"]
        .as_array()
        .expect("facts")
        .iter()
        .find(|f| f["name"] == name)
        .expect("fact")
}

fn blockers(v: &Value) -> Vec<String> {
    v["send"]["blockers"]
        .as_array()
        .expect("blockers")
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

#[test]
fn a_free_seat_says_alive_until_two_hours_after_its_last_sign_and_nothing_blocks_a_dm() {
    let v = run(&seat(10), &gate(all_on(), None));
    assert_eq!(v["schema"], "aib-drive.explain.v1");
    let alive = fact(&v, "alive");
    assert_eq!(alive["v"], "yes");
    // last sign = seat-now a minute ago (newer than the window's 10 minutes)
    assert_eq!(alive["valid_until"], iso(NOW - 60 + LOST_HOURS * 3600));
    let hands = fact(&v, "hands");
    assert_eq!(hands["v"], "free");
    assert_eq!(hands["valid_until"], Value::Null);
    assert_eq!(fact(&v, "waiting_on")["v"], "seat");
    assert_eq!(v["verdict"]["dispatchable"], "yes");
    assert!(blockers(&v).is_empty(), "{:?}", blockers(&v));
    // every source is listed with its time
    let names: Vec<&str> = v["sources"]
        .as_array()
        .expect("sources")
        .iter()
        .filter_map(|s| s["name"].as_str())
        .collect();
    assert_eq!(names, ["seat-now", "beat", "window", "dm-queue", "lease"]);
    let win = v["sources"]
        .as_array()
        .expect("sources")
        .iter()
        .find(|s| s["name"] == "window")
        .expect("window");
    assert_eq!(win["at"], iso(NOW - 600));
    assert_eq!(win["from"], "latest dm");
}

#[test]
fn a_seat_in_its_turn_says_when_it_may_turn_free() {
    let v = run(&seat(2), &gate(all_on(), None));
    let hands = fact(&v, "hands");
    assert_eq!(hands["v"], "in_turn");
    assert_eq!(hands["valid_until"], iso(NOW - 120 + HANDS_FREE_MIN * 60));
    assert_eq!(v["verdict"]["dispatchable"], "no");
}

#[test]
fn a_seat_sent_a_dm_ten_minutes_ago_is_blocked_until_the_gap_has_passed() {
    let last = (NOW as i64 - 600) * 1000;
    let v = run(&seat(10), &gate(all_on(), Some(last)));
    assert_eq!(
        blockers(&v),
        [format!("sent 10 min ago (one per {SEAT_GAP_MIN} min)")]
    );
    assert_eq!(
        v["send"]["next_ok"],
        aib_drive::act::rfc3339_ms(last + SEAT_GAP_MIN * 60_000)
    );
    // past the gap nothing blocks
    let old = (NOW as i64 - SEAT_GAP_MIN * 60 - 1) * 1000;
    assert!(blockers(&run(&seat(10), &gate(all_on(), Some(old)))).is_empty());
}

#[test]
fn ten_unread_dms_say_lost_until_read_and_block_a_dm() {
    let mut f = seat(10);
    f.dms = (0..UNREAD_LOST as u64)
        .map(|i| dm("delivered", NOW - 100 - i, false))
        .collect();
    let v = run(&f, &gate(all_on(), None));
    let alive = fact(&v, "alive");
    assert_eq!(alive["v"], "no");
    assert_eq!(alive["valid_until"], Value::Null);
    assert_eq!(alive["until"], format!("unread below {UNREAD_LOST}"));
    let b = blockers(&v);
    assert!(b.iter().any(|x| x.starts_with("unread 10")), "{b:?}");
    assert!(b.iter().any(|x| x == "alive no"), "{b:?}");
}

#[test]
fn paused_and_an_unreadable_window_are_said_not_guessed() {
    let mut sw = all_on();
    sw.set("paused", true);
    let mut f = seat(10);
    f.windows = None;
    let v = run(&f, &gate(sw, None));
    assert!(blockers(&v).iter().any(|x| x == "paused"));
    let win = v["sources"]
        .as_array()
        .expect("sources")
        .iter()
        .find(|s| s["name"] == "window")
        .expect("window");
    assert_eq!(win["unknown"], "tmux not run");
    assert_eq!(fact(&v, "hands")["v"], "unknown");
    // the text form names the blockers
    let t = to_text(&v);
    assert!(t.contains("擋:"), "{t}");
    assert!(t.contains("paused"), "{t}");
}
