//! S79 sig 席位事實(IFACE-s79-drive-519d0-1115 §1)。整合前 seats.rs 還沒進 lib.rs,
//! 這裡直接把那個檔編進來測(整合時改成 `aib_drive::seats`)。

use aib_drive::seats;

use seats::*;
use std::collections::BTreeMap;

const NOW: u64 = 1_794_672_000;
const HUB: &str = "aihub-20261104-121013-f457";

#[derive(Default)]
struct Fake {
    seats: Vec<String>,
    now: BTreeMap<String, Known<SeatNow>>,
    beat: BTreeMap<String, u64>,
    dms: BTreeMap<String, Known<Vec<DmItem>>>,
    windows: Option<BTreeMap<String, u64>>,
    lease: Option<String>,
    lease_unknown: bool,
}

impl Source for Fake {
    fn seats(&self, _now: u64) -> Result<Vec<String>, String> {
        Ok(self.seats.clone())
    }
    fn seat_now(&self, seat: &str) -> Known<SeatNow> {
        self.now
            .get(seat)
            .cloned()
            .unwrap_or(Known::Unknown("no file".into()))
    }
    fn beat(&self, seat: &str) -> Option<u64> {
        self.beat.get(seat).copied()
    }
    fn dms(&self, seat: &str) -> Known<Vec<DmItem>> {
        self.dms
            .get(seat)
            .cloned()
            .unwrap_or(Known::Known(Vec::new()))
    }
    fn windows(&self, _hub: &str) -> Known<BTreeMap<String, u64>> {
        match &self.windows {
            Some(w) => Known::Known(w.clone()),
            None => Known::Unknown("tmux not run".into()),
        }
    }
    fn lease_owner(&self, _now: u64) -> Known<Option<String>> {
        if self.lease_unknown {
            Known::Unknown("lease file broken".into())
        } else {
            Known::Known(self.lease.clone())
        }
    }
}

fn dm(status: &str, wid: &str, sent: u64, read: bool) -> DmItem {
    DmItem {
        status: status.into(),
        sent_at: sent,
        read_at: read.then_some(sent + 10),
        done_at: None,
        hub: Some(HUB.into()),
        wid: Some(wid.into()),
    }
}

fn state(s: Option<&str>) -> Known<SeatNow> {
    Known::Known(SeatNow {
        at: Some(NOW - 60),
        state: s.map(str::to_string),
    })
}

/// One seat in window @4, quiet for `quiet_min`, with these dms and this state.
fn one(seat: &str, quiet_min: u64, dms: Vec<DmItem>, st: Option<&str>) -> Fake {
    let mut f = Fake {
        seats: vec![seat.into()],
        windows: Some(BTreeMap::from([("@4".to_string(), NOW - quiet_min * 60)])),
        ..Fake::default()
    };
    f.now.insert(seat.into(), state(st));
    f.dms.insert(seat.into(), Known::Known(dms));
    f
}

fn seat_of(f: &Fake) -> Seat {
    report(f, NOW).seats.remove(0)
}

#[test]
fn a_quiet_seat_with_nothing_queued_is_free_and_dispatchable_for_the_mainline() {
    let s = seat_of(&one(
        "a9136",
        12,
        vec![dm("read", "@4", NOW - 900, true)],
        None,
    ));
    assert_eq!(s.alive.v, "yes", "{s:?}");
    assert_eq!(s.hands.v, "free", "{s:?}");
    assert_eq!(s.free_since_min, Some(12));
    assert_eq!(s.unread, Some(0));
    assert_eq!(s.cabin.as_deref(), Some("f457"));
    assert_eq!(s.waiting_on, "none");
    assert_eq!((s.dispatchable, s.size), ("yes", "mainline"));
}

#[test]
fn recent_output_or_a_queued_dm_means_the_seat_is_in_a_turn() {
    let busy = seat_of(&one(
        "a9136",
        2,
        vec![dm("read", "@4", NOW - 900, true)],
        None,
    ));
    assert_eq!(busy.hands.v, "in_turn");
    assert_eq!(busy.dispatchable, "no");
    let queued = seat_of(&one(
        "a9136",
        30,
        vec![
            dm("read", "@4", NOW - 900, true),
            dm("queued", "@4", NOW - 100, false),
        ],
        None,
    ));
    assert_eq!(queued.hands.v, "in_turn", "{queued:?}");
    assert_eq!(queued.dispatchable, "no");
}

#[test]
fn ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle() {
    let many: Vec<DmItem> = (0..UNREAD_LOST as u64)
        .map(|i| dm("delivered", "@4", NOW - 100 - i, false))
        .collect();
    let r = report(&one("c477a", 30, many, None), NOW);
    assert_eq!(r.seats[0].alive.v, "no");
    assert_eq!(r.seats[0].dispatchable, "no");
    assert_eq!(r.lost(), vec!["c477a".to_string()]);

    let mut gone = one("2240e", 30, vec![dm("read", "@9", NOW - 900, true)], None);
    gone.windows = Some(BTreeMap::from([("@4".to_string(), NOW)]));
    let s = seat_of(&gone);
    assert_eq!((s.alive.v, s.alive.why.as_str()), ("no", "window gone"));

    let mut stale = one(
        "2240e",
        LOST_HOURS * 60 + 5,
        vec![dm("read", "@4", NOW - 9000, true)],
        None,
    );
    stale
        .now
        .insert("2240e".into(), Known::Known(SeatNow::default()));
    let s = seat_of(&stale);
    assert_eq!(s.alive.v, "no", "{s:?}");
}

#[test]
fn what_cannot_be_read_is_unknown_and_never_free() {
    // tmux cannot be asked
    let mut f = one("a9136", 30, vec![dm("read", "@4", NOW - 900, true)], None);
    f.windows = None;
    let r = report(&f, NOW);
    let s = &r.seats[0];
    assert_eq!(
        (s.alive.v, s.hands.v, s.dispatchable),
        ("unknown", "unknown", "unknown")
    );
    assert!(!r.complete);
    // no dm ever came: we do not know where the seat is
    let s = seat_of(&one("a9136", 30, vec![], None));
    assert_eq!(s.hands.v, "unknown");
    assert_eq!(s.dispatchable, "unknown");
    // the dm queue cannot be read
    let mut f = one("a9136", 30, vec![], None);
    f.dms
        .insert("a9136".into(), Known::Unknown("permission".into()));
    let s = seat_of(&f);
    assert_eq!(s.unread, None);
    assert_eq!(s.dispatchable, "unknown");
    // the lease file is broken: the round is not complete
    let mut f = one("a9136", 30, vec![dm("read", "@4", NOW - 900, true)], None);
    f.lease_unknown = true;
    assert!(!report(&f, NOW).complete);
}

#[test]
fn waiting_on_comes_from_the_lease_and_the_state_and_sets_the_size() {
    let mut f = one(
        "a9136",
        30,
        vec![dm("read", "@4", NOW - 900, true)],
        Some("reply"),
    );
    f.lease = Some("a9136".into());
    let s = seat_of(&f);
    assert_eq!((s.waiting_on, s.size), ("receipt", "review"));
    let s = seat_of(&one(
        "a9136",
        30,
        vec![dm("read", "@4", NOW - 900, true)],
        Some("reply"),
    ));
    assert_eq!((s.waiting_on, s.size), ("seat", "slice"));
    let s = seat_of(&one(
        "a9136",
        30,
        vec![dm("read", "@4", NOW - 900, true)],
        Some("decide"),
    ));
    assert_eq!((s.waiting_on, s.size), ("human", "slice"));
    let mut f = one("a9136", 30, vec![dm("read", "@4", NOW - 900, true)], None);
    f.now.remove("a9136");
    let s = seat_of(&f);
    assert_eq!(s.waiting_on, "unknown");
}

#[test]
fn the_coordinator_is_never_development_capacity() {
    let s = seat_of(&one(
        COORDINATOR,
        30,
        vec![dm("read", "@4", NOW - 900, true)],
        None,
    ));
    assert_eq!(s.role, "coordinator");
    assert_eq!(s.hands.v, "free");
    assert_eq!((s.dispatchable, s.size), ("no", "none"));
}

#[test]
fn json_has_the_iface_shape_and_one_idle_line_per_seat() {
    let r = report(
        &one("a9136", 12, vec![dm("read", "@4", NOW - 900, true)], None),
        NOW,
    );
    let v = to_json(&r, "~/.ez2-aib/state/drive/idle.jsonl");
    assert_eq!(v["schema"], SCHEMA);
    assert_eq!(v["seats"][0]["alive"]["v"], "yes");
    assert_eq!(v["seats"][0]["hands"]["free_since_min"], 12);
    assert_eq!(v["seats"][0]["capacity"]["unread"], 0);
    assert!(v["seats"][0]["capacity"]["usage_pct"].is_null());
    assert_eq!(v["seats"][0]["waiting_on"]["v"], "none");
    assert_eq!(v["lost"], serde_json::json!([]));
    assert_eq!(iso(NOW), "2026-11-14T16:00:00.000Z");
    let lines = idle_lines(&r);
    assert_eq!(lines.lines().count(), 1);
    let l: serde_json::Value = serde_json::from_str(lines.trim()).unwrap();
    assert_eq!(
        (l["seat"].as_str(), l["hands"].as_str()),
        (Some("a9136"), Some("free"))
    );

    let dir = std::env::temp_dir().join(format!("aib-drive-seats-{}", std::process::id()));
    let p = dir.join("drive").join("idle.jsonl");
    append_idle(&p, &lines).unwrap();
    append_idle(&p, &lines).unwrap();
    assert_eq!(
        std::fs::read_to_string(&p).unwrap().lines().count(),
        2,
        "appended"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn files_tmux_and_lease_parse_as_written() {
    assert_eq!(
        parse_windows("@4 1794672801\n@12 1794672000\nbad\n"),
        BTreeMap::from([
            ("@12".to_string(), 1_794_672_000),
            ("@4".to_string(), 1_794_672_801)
        ])
    );
    let v = serde_json::json!({"owner": "@ab093", "expiry_epoch": NOW + 60});
    assert_eq!(lease_from(&v, NOW), Some("ab093".into()));
    assert_eq!(lease_from(&v, NOW + 61), None, "expired");
    assert!(is_seat_code("b433b") && !is_seat_code("gate-seats"));

    // the real file layout, in a temp state root
    let root = std::env::temp_dir().join(format!("aib-drive-seats-fs-{}", std::process::id()));
    let sn = root.join("state/seat-now");
    std::fs::create_dir_all(&sn).unwrap();
    std::fs::write(
        sn.join("b433b.json"),
        format!(r#"{{"at": {}, "state": "reply"}}"#, NOW - 60),
    )
    .unwrap();
    std::fs::write(
        sn.join("33e28.json"),
        format!(r#"{{"at": {}}}"#, NOW - 30 * 86_400),
    )
    .unwrap();
    std::fs::write(sn.join("b433b.beat"), format!(r#"{{"at": {}}}"#, NOW - 5)).unwrap();
    let q = root.join("state/dm-queue/b433b");
    std::fs::create_dir_all(&q).unwrap();
    std::fs::write(
        q.join("q_1.json"),
        format!(r#"{{"status":"delivered","sent_at":{},"read_at":null,"done_at":null,"hub":"{HUB}","wid":"@12"}}"#, NOW - 50),
    )
    .unwrap();
    std::fs::write(q.join("q_2.json"), r#"{"status":"archived","sent_at":1}"#).unwrap();
    std::fs::write(q.join("audit.jsonl"), "x").unwrap();
    let src = FsSource { root: root.clone() };
    assert_eq!(
        src.seats(NOW).unwrap(),
        vec!["b433b".to_string()],
        "old seats are not listed"
    );
    assert_eq!(src.beat("b433b"), Some(NOW - 5));
    let Known::Known(d) = src.dms("b433b") else {
        panic!()
    };
    assert_eq!(d.len(), 1, "archived is left out");
    assert_eq!(d[0].wid.as_deref(), Some("@12"));
    assert_eq!(src.dms("nobody"), Known::Known(vec![]));
    assert_eq!(src.lease_owner(NOW), Known::Known(None));
    let _ = std::fs::remove_dir_all(&root);
}
