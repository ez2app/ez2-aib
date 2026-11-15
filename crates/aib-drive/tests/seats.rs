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
    nodes: Option<Vec<PlanNode>>,
    prs: Option<Vec<String>>,
    trees: BTreeMap<String, Vec<Tree>>,
    seat_win: BTreeMap<String, (String, String)>,
    memory: BTreeMap<String, usize>,
    handoff: BTreeMap<String, Option<u64>>,
    /// seats whose seat-now file is not there at all
    gone: Vec<String>,
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
    fn plan_nodes(&self) -> Known<Vec<PlanNode>> {
        match &self.nodes {
            Some(n) => Known::Known(n.clone()),
            None => Known::Unknown("plan not given".into()),
        }
    }
    fn open_pr_heads(&self) -> Known<Vec<String>> {
        match &self.prs {
            Some(p) => Known::Known(p.clone()),
            None => Known::Unknown("gh not run".into()),
        }
    }
    fn seat_windows(&self) -> Known<BTreeMap<String, (String, String)>> {
        Known::Known(self.seat_win.clone())
    }
    fn trees(&self, seat: &str) -> Known<Vec<Tree>> {
        match self.trees.get(seat) {
            Some(t) => Known::Known(t.clone()),
            None => Known::Unknown("no cache".into()),
        }
    }
    fn memory_lines(&self, seat: &str) -> Known<usize> {
        match self.memory.get(seat) {
            Some(n) => Known::Known(*n),
            None => Known::Unknown("no memory file".into()),
        }
    }
    fn seat_now_gone(&self, seat: &str) -> bool {
        self.gone.iter().any(|g| g == seat)
    }
    fn handoff_open(&self, seat: &str) -> Known<Option<u64>> {
        match self.handoff.get(seat) {
            Some(h) => Known::Known(*h),
            None => Known::Unknown("no handoff files".into()),
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
    // the lease file is broken: waiting_on goes unknown, the round stays complete (r7 3)
    let mut f = one("a9136", 30, vec![dm("read", "@4", NOW - 900, true)], None);
    f.lease_unknown = true;
    let r = report(&f, NOW);
    assert!(r.complete);
    assert!(
        r.partial.iter().any(|p| p.starts_with("lease:")),
        "{:?}",
        r.partial
    );
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
    let v = to_json(&r, "~/.ez2-aib/state/drive/idle.jsonl", &[]);
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
    let src = FsSource::new(root.clone());
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

// ---------- sig-b:B3 容量、B7 先做、A4 剛變空、7b81e L1–L3 ----------

fn node(id: &str, owner: &str, status: &str) -> PlanNode {
    PlanNode {
        id: id.into(),
        owner: Some(owner.into()),
        status: Some(status.into()),
    }
}

/// A tree whose last commit was 3 hours ago (quiet long enough to collect).
fn tree(path: &str, ahead: u64, dirty: u64) -> Tree {
    Tree {
        path: path.into(),
        branch: format!("feat/{path}"),
        ahead,
        dirty,
        last_ct: Some(NOW - 3 * 3600),
    }
}

fn free_seat() -> Fake {
    one("a9136", 12, vec![dm("read", "@4", NOW - 900, true)], None)
}

#[test]
fn capacity_comes_from_the_plan_tree_and_the_open_prs() {
    let mut f = free_seat();
    f.nodes = Some(vec![
        node("S1", "a9136", "審過未推"),
        node("S2", "a9136", "未開始"),
        node("S3", "a9136", "已同意未做"),
        node("S4", "a9136", "開發中"),
        node("S5", "b433b", "審過未推"),
    ]);
    f.prs = Some(vec![
        "feat/drive-n1-a9136-1115".into(),
        "integ-src/x-a9136".into(),
        "feat/a9136x-1115".into(),
        "feat/sig-b433b-1115".into(),
    ]);
    let s = seat_of(&f);
    assert_eq!(s.wip_unpushed, Some(vec!["S1".to_string()]));
    assert_eq!(s.planned, Some(vec!["S2".to_string(), "S3".to_string()]));
    assert_eq!(s.on_deck, None, "no merge-queue source today");
    assert_eq!(s.open_prs, Some(2), "a9136x is not the seat a9136");
    assert_eq!(s.usage_pct, None, "no usage source today");
    assert_eq!((s.dispatchable, s.size), ("yes", "mainline"));
    assert!(branch_of_seat("integ-src/dmqrecv2-1ced7", "1ced7"));
    assert!(!branch_of_seat("feat/a1ced7b", "1ced7"));

    // WIP_MAX reviewed-not-pushed slices: only a review fits
    let mut f = free_seat();
    f.nodes = Some(
        (0..WIP_MAX)
            .map(|i| node(&format!("W{i}"), "a9136", WIP_STATUS))
            .collect(),
    );
    f.prs = Some(vec![]);
    let s = seat_of(&f);
    assert_eq!((s.dispatchable, s.size), ("yes", "review"), "{s:?}");
}

#[test]
fn capacity_that_cannot_be_read_is_null_and_partial_not_a_block() {
    let f = free_seat();
    let r = report(&f, NOW);
    let s = &r.seats[0];
    assert_eq!(
        (s.wip_unpushed.as_ref(), s.open_prs, s.planned.as_ref()),
        (None, None, None)
    );
    assert_eq!(s.dispatchable, "yes", "capacity alone never blocks");
    assert!(
        r.complete,
        "capacity sources do not make the round incomplete"
    );
    assert!(r.errors.is_empty(), "{:?}", r.errors);
    assert!(
        r.partial.iter().any(|e| e.starts_with("plan:")),
        "{:?}",
        r.partial
    );
    assert!(
        r.partial.iter().any(|e| e.starts_with("open prs:")),
        "{:?}",
        r.partial
    );
    let v = to_json(&r, "x", &[]);
    assert!(v["seats"][0]["capacity"]["wip_unpushed"].is_null());
    assert!(v["seats"][0]["capacity"]["open_prs"].is_null());
    assert!(
        v["seats"][0]["capacity"]["on_deck"].is_null(),
        "a bool or null, never a number"
    );
    let errs: Vec<&str> = v["errors"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|e| e.as_str())
        .collect();
    assert!(
        errs.iter().any(|e| e.starts_with("partial: plan:")),
        "{errs:?}"
    );
}

#[test]
fn first_is_iface_codes_and_the_detail_lists_dm_commit_push_tidy() {
    let trees = Known::Known(vec![tree("a", 2, 0), tree("b", 1, 3), tree("c", 0, 0)]);
    let got = first_things(Some(2), &trees, &["S1".to_string()], NOW);
    assert_eq!(
        got,
        vec![
            "dm: 2 unread".to_string(),
            "commit b: 3 files not committed".into(),
            "push S1: reviewed, not pushed".into(),
            "tidy c: nothing beyond main".into(),
        ]
    );
    assert!(first_things(Some(0), &Known::Unknown("x".into()), &[], NOW).is_empty());
    let many: Vec<String> = (0..20).map(|i| format!("S{i}")).collect();
    assert_eq!(first_things(None, &trees, &many, NOW).len(), FIRST_MAX);

    // a quiet merged tree ⇒ clean_target; planned nodes are new work, never first
    let mut f = free_seat();
    f.nodes = Some(vec![node("S9", "a9136", "未開始")]);
    f.prs = Some(vec![]);
    f.trees.insert("a9136".into(), vec![tree("c", 0, 0)]);
    let s = seat_of(&f);
    assert_eq!(s.first, vec!["clean_target"]);
    assert!(s.first.iter().all(|c| FIRST_CODES.contains(c)));
    assert_eq!(
        s.first_detail,
        vec!["tidy c: nothing beyond main".to_string()]
    );
    assert!(to_text(&report(&f, NOW)).contains("先做:tidy c"));
    let v = to_json(&report(&f, NOW), "x", &[]);
    assert_eq!(v["seats"][0]["first"], serde_json::json!(["clean_target"]));
    assert_eq!(v["seats"][0]["capacity"]["planned"], 1);
}

#[test]
fn a_fresh_tree_is_not_collected() {
    let mut fresh = tree("new", 0, 0);
    fresh.last_ct = Some(NOW - 600);
    assert!(!stale_clean(&fresh, NOW), "opened 10 minutes ago");
    assert!(stale_clean(&tree("old", 0, 0), NOW));
    let mut f = free_seat();
    f.trees.insert("a9136".into(), vec![fresh]);
    assert!(seat_of(&f).first.is_empty());
}

#[test]
fn idle_is_under_drive_and_only_the_json_round_writes_it() {
    let root = std::env::temp_dir().join(format!("aib-drive-seats-idle-{}", std::process::id()));
    let idle = idle_path(&root);
    assert_eq!(idle, root.join("drive").join("idle.jsonl"));
    let f = free_seat();
    let (_, text) = run_with(&f, &idle, false, NOW);
    assert!(text.contains("剛變空:a9136"), "{text}");
    assert!(!idle.exists(), "a hand-run text view writes nothing");
    let (rc, js) = run_with(&f, &idle, true, NOW);
    assert_eq!(rc, 0, "{js}");
    let v: serde_json::Value = serde_json::from_str(js.trim()).unwrap();
    assert_eq!(v["newly_free"], serde_json::json!(["a9136"]));
    assert_eq!(std::fs::read_to_string(&idle).unwrap().lines().count(), 1);
    let (_, js) = run_with(&f, &idle, true, NOW + 60);
    let v: serde_json::Value = serde_json::from_str(js.trim()).unwrap();
    assert_eq!(
        v["newly_free"],
        serde_json::json!([]),
        "once per free stretch"
    );
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn four_quiet_minutes_is_still_a_turn_five_is_free() {
    let s = seat_of(&one(
        "a9136",
        HANDS_FREE_MIN - 1,
        vec![dm("read", "@4", NOW - 900, true)],
        None,
    ));
    assert_eq!(s.hands.v, "in_turn", "{s:?}");
    let s = seat_of(&one(
        "a9136",
        HANDS_FREE_MIN,
        vec![dm("read", "@4", NOW - 900, true)],
        None,
    ));
    assert_eq!(
        (s.hands.v, s.free_since_min),
        ("free", Some(HANDS_FREE_MIN))
    );
}

#[test]
fn a_lease_that_cannot_be_read_makes_waiting_on_unknown() {
    let mut f = free_seat();
    f.lease_unknown = true;
    let s = seat_of(&f);
    assert_eq!((s.waiting_on, s.size), ("unknown", "none"), "{s:?}");
    // the seat itself says what it waits on: that stands
    let mut f = one(
        "a9136",
        12,
        vec![dm("read", "@4", NOW - 900, true)],
        Some("reply"),
    );
    f.lease_unknown = true;
    assert_eq!(seat_of(&f).waiting_on, "seat");
}

#[test]
fn the_seat_file_window_wins_over_the_latest_dm() {
    // the latest dm points at @9 (gone), the seat file at @4 (alive)
    let mut f = one("a9136", 12, vec![dm("read", "@9", NOW - 900, true)], None);
    f.seat_win
        .insert("a9136".into(), (HUB.to_string(), "@4".to_string()));
    let s = seat_of(&f);
    assert_eq!((s.alive.v, s.hands.v), ("yes", "free"), "{s:?}");
    assert!(
        s.reasons.iter().any(|r| r.contains("seat file says")),
        "{:?}",
        s.reasons
    );

    let v = serde_json::json!({"session_uuid": "5a920699-5f46-4725-b303-77177e8b433b",
        "socket": HUB, "window_id": "@12", "last_used_epoch": 7});
    assert_eq!(
        seat_file_window(&v, "b433b"),
        Some((7, (HUB.to_string(), "@12".to_string())))
    );
    assert_eq!(seat_file_window(&v, "a9136"), None);
}

#[test]
fn newly_free_names_a_seat_once_per_free_stretch() {
    let r = report(&free_seat(), NOW);
    assert_eq!(
        newly_free(&BTreeMap::new(), &r),
        vec!["a9136".to_string()],
        "first sight"
    );
    let prev = last_hands(&idle_lines(&r));
    assert_eq!(prev.get("a9136").map(String::as_str), Some("free"));
    assert!(
        newly_free(&prev, &r).is_empty(),
        "already free last round: not again"
    );
    let busy = BTreeMap::from([("a9136".to_string(), "in_turn".to_string())]);
    assert_eq!(newly_free(&busy, &r), vec!["a9136".to_string()]);
    let c = report(
        &one(
            COORDINATOR,
            30,
            vec![dm("read", "@4", NOW - 900, true)],
            None,
        ),
        NOW,
    );
    assert!(
        newly_free(&BTreeMap::new(), &c).is_empty(),
        "coordinator never"
    );
    assert_eq!(
        to_json(&r, "x", &["a9136".into()])["newly_free"][0],
        "a9136"
    );
}

#[test]
fn plan_and_worktree_cache_parse_as_written() {
    let plan = serde_json::json!({"nodes": [
        {"id": "S68", "owner": "b433b", "status": "審過未推"},
        {"id": "V", "owner": "Andy", "status": null},
        {"title": "no id"}]});
    let n = parse_plan(&plan).unwrap();
    assert_eq!(n.len(), 2);
    assert_eq!(n[0], node("S68", "b433b", "審過未推"));
    assert!(parse_plan(&serde_json::json!({})).is_err());
    let cache = serde_json::json!({"sections": {"dev": {"data": {"trees": [
        {"path": "/w/a", "branch": "feat/a", "ahead": 2, "dirty": 1, "last_ct": 7},
        {"path": "/w/b", "branch": "feat/b", "ahead": 0}]}}}});
    let t = parse_trees(&cache).unwrap();
    assert_eq!((t[0].dirty, t[0].last_ct), (1, Some(7)));
    assert_eq!((t[1].ahead, t[1].dirty), (0, 0));
    assert!(parse_trees(&serde_json::json!({"sections": {}})).is_err());
}

#[test]
fn a_lost_seat_does_not_make_the_round_incomplete() {
    // a9136 free and fully read; 8aa49 quiet for a day with no dm that names a window
    let mut f = free_seat();
    f.nodes = Some(vec![]);
    f.prs = Some(vec![]);
    f.seats.push("8aa49".into());
    f.now.insert(
        "8aa49".into(),
        Known::Known(SeatNow {
            at: Some(NOW - 18 * 3600),
            state: None,
        }),
    );
    let r = report(&f, NOW);
    let lost = r.seats.iter().find(|s| s.seat == "8aa49").unwrap();
    assert_eq!((lost.alive.v, lost.hands.v), ("no", "unknown"), "{lost:?}");
    assert!(r.complete, "{:?}", r.errors);
    assert_eq!(r.lost(), vec!["8aa49".to_string()]);
}

// ---------- sig-c(519d0 11-15 07:41):B7 其餘三個代碼、B3 --repo 資料夾 ----------

#[test]
fn first_lists_all_four_codes_in_iface_order_each_only_when_due() {
    let mut f = free_seat();
    f.nodes = Some(vec![
        node("S5", "a9136", "未驗"),
        node("S6", "b433b", "未驗"),
        node("S7", "a9136", "實查通過"),
    ]);
    f.prs = Some(vec![]);
    f.trees.insert("a9136".into(), vec![tree("c", 0, 0)]);
    f.memory.insert("a9136".into(), MEMORY_LINES_MAX + 1);
    f.handoff.insert("a9136".into(), Some(NOW - 600));
    let s = seat_of(&f);
    assert_eq!(s.first, FIRST_CODES.to_vec());
    assert_eq!(
        s.first_detail,
        vec![
            "tidy c: nothing beyond main".to_string(),
            "plan S5: 未驗, verify and set its status".into(),
            format!(
                "memory: context.md {} lines (over {MEMORY_LINES_MAX}), tidy it",
                MEMORY_LINES_MAX + 1
            ),
            format!(
                "handoff: collected at {}, no receipt after it",
                iso(NOW - 600)
            ),
        ],
        "another seat's 未驗 node and a verified node are not this seat's closing work"
    );
    let v = to_json(&report(&f, NOW), "x", &[]);
    assert_eq!(v["seats"][0]["first"], serde_json::json!(FIRST_CODES));
}

#[test]
fn first_codes_stay_off_at_the_limit_and_when_a_source_cannot_be_read() {
    let mut f = free_seat();
    f.nodes = Some(vec![node("S5", "a9136", "開發中")]);
    f.memory.insert("a9136".into(), MEMORY_LINES_MAX);
    f.handoff.insert("a9136".into(), None);
    let s = seat_of(&f);
    assert!(s.first.is_empty(), "{:?}", s.first);
    assert!(s.first_detail.is_empty(), "{:?}", s.first_detail);

    // nothing read ⇒ nothing listed, and the reasons say why (never a guess)
    let f = free_seat();
    let s = seat_of(&f);
    assert!(s.first.is_empty());
    assert!(
        s.reasons.iter().any(|r| r.starts_with("memory: ")),
        "{:?}",
        s.reasons
    );
    assert!(
        s.reasons.iter().any(|r| r.starts_with("handoff: ")),
        "{:?}",
        s.reasons
    );
}

#[test]
fn a_handoff_is_open_only_while_no_receipt_is_as_new_as_the_collect() {
    assert_eq!(handoff_left_open(100, None), Some(100));
    assert_eq!(handoff_left_open(100, Some(99)), Some(100));
    assert_eq!(handoff_left_open(100, Some(100)), None);
    assert_eq!(handoff_left_open(100, Some(101)), None);
    let jsonl = "{\"ts\": 50.5}\nnot json\n{\"ts\": 1794580402.166284}\n{\"no\": 1}\n";
    assert_eq!(last_receipt_ts(jsonl), Some(1_794_580_402));
    assert_eq!(last_receipt_ts(""), None);
}

#[test]
fn the_file_source_reads_memory_lines_and_an_open_handoff() {
    let root = std::env::temp_dir().join(format!("aib-drive-sigc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let mem = root.join("memory/b433b");
    std::fs::create_dir_all(&mem).unwrap();
    std::fs::write(mem.join("context.md"), "line\n".repeat(95)).unwrap();
    let src = FsSource::new(root.clone());
    assert_eq!(src.memory_lines("b433b"), Known::Known(95));
    // no memory file yet = nothing to tidy; one that cannot be read stays unknown (87167 L1)
    assert_eq!(src.memory_lines("nobody"), Known::Known(0));
    std::fs::create_dir_all(root.join("memory/abcde/context.md")).unwrap();
    assert!(matches!(src.memory_lines("abcde"), Known::Unknown(_)));
    // no collect ever ⇒ no handoff started
    assert_eq!(src.handoff_open("b433b"), Known::Known(None));
    let h = root.join("state/handoff");
    std::fs::create_dir_all(&h).unwrap();
    std::fs::write(h.join("b433b.collect.json"), r#"{"ts": 1794580365.299685}"#).unwrap();
    assert_eq!(
        src.handoff_open("b433b"),
        Known::Known(Some(1_794_580_365)),
        "collected, no receipt file"
    );
    std::fs::write(h.join("b433b.receipts.jsonl"), "{\"ts\": 1794580300.0}\n").unwrap();
    assert_eq!(
        src.handoff_open("b433b"),
        Known::Known(Some(1_794_580_365)),
        "the receipt is older"
    );
    std::fs::write(
        h.join("b433b.receipts.jsonl"),
        "{\"ts\": 1794580402.166284}\n",
    )
    .unwrap();
    assert_eq!(
        src.handoff_open("b433b"),
        Known::Known(None),
        "written after the collect"
    );
    std::fs::write(h.join("b433b.collect.json"), "{}").unwrap();
    assert!(
        matches!(src.handoff_open("b433b"), Known::Unknown(_)),
        "a collect with no ts is not read"
    );
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn repo_names_come_from_a_github_remote_and_nothing_else() {
    for (url, want) in [
        ("git@github.com:ez2app/ez2-aib.git", Some("ez2app/ez2-aib")),
        ("https://github.com/ez2app/ez2-aib", Some("ez2app/ez2-aib")),
        (
            "https://github.com/ez2app/ez2-aib.git/",
            Some("ez2app/ez2-aib"),
        ),
        (
            "ssh://git@github.com/ez2app/ez2-aib.git",
            Some("ez2app/ez2-aib"),
        ),
        ("https://gitlab.com/ez2app/ez2-aib.git", None),
        ("git@github.com:ez2app/ez2-aib/extra.git", None),
        ("git@github.com:ez2app/..", None),
        ("", None),
    ] {
        assert_eq!(owner_name_from_url(url).as_deref(), want, "{url}");
    }
    assert!(is_owner_name("ez2app/ez2-aib"));
    assert!(!is_owner_name("/Users/x/ez2-aib"));
    assert!(!is_owner_name("a/b/c"));
}

#[test]
fn a_repo_folder_is_turned_into_owner_name_before_gh_sees_it() {
    assert_eq!(gh_repo("ez2app/ez2-aib").as_deref(), Ok("ez2app/ez2-aib"));
    assert!(gh_repo("/no/such/folder/for/aib-drive").is_err());
    let root = std::env::temp_dir().join(format!("aib-drive-sigc-repo-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let dir = root.to_string_lossy().into_owned();
    let git = |args: &[&str]| {
        let mut a = vec!["-C".to_string(), dir.clone()];
        a.extend(args.iter().map(|s| s.to_string()));
        git_ok(&a)
    };
    git(&["init", "-q"]);
    assert!(gh_repo(&dir).is_err(), "no origin");
    git(&[
        "remote",
        "add",
        "origin",
        "git@github.com:ez2app/ez2-aib.git",
    ]);
    assert_eq!(gh_repo(&dir).as_deref(), Ok("ez2app/ez2-aib"));
    let _ = std::fs::remove_dir_all(&root);
}

/// test setup: run git with a fixed argument list (no shell)
fn git_ok(args: &[String]) {
    let st = std::process::Command::new("git")
        .args(args)
        .stdin(std::process::Stdio::null())
        .status()
        .expect("test setup: git runs");
    assert!(st.success(), "test setup: git {args:?}");
}

#[test]
fn a_seat_with_no_seat_now_file_but_a_live_dm_queue_is_listed_and_judged_lost() {
    let root = std::env::temp_dir().join(format!("aib-drive-sigc-b1-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let q = root.join("state/dm-queue/c477a");
    std::fs::create_dir_all(&q).unwrap();
    for i in 0..UNREAD_LOST {
        std::fs::write(
            q.join(format!("q_{i}.json")),
            format!(r#"{{"status":"delivered","sent_at":{},"read_at":null,"done_at":null,"hub":"{HUB}","wid":"@9"}}"#, NOW - 600),
        )
        .unwrap();
    }
    std::fs::create_dir_all(root.join("state/dm-queue/not-a-seat")).unwrap();
    std::fs::write(root.join("state/dm-queue/abcde"), "a file, not a queue").unwrap();
    std::fs::create_dir_all(root.join("state/seat-now")).unwrap();
    let src = FsSource::new(root.clone());
    assert_eq!(src.seats(NOW).unwrap(), vec!["c477a".to_string()]);
    let r = report(&src, NOW);
    assert_eq!(r.lost(), vec!["c477a".to_string()]);
    // tmux on the test machine decides which comes first: no such window, or the unread count
    let why = &r.seats[0].alive.why;
    assert!(
        why == "window gone" || *why == format!("unread {UNREAD_LOST}"),
        "{why}"
    );
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn a_seat_with_no_seat_now_for_two_days_is_lost_even_when_window_and_unread_cannot_say() {
    // 87167 M1 on 1cbf03ba: cabin gone (tmux cannot be asked), one unread, no seat-now file
    let mut f = one(
        "a63ab",
        30,
        vec![dm("delivered", "@4", NOW - 600, false)],
        None,
    );
    f.windows = None;
    f.now.clear();
    f.gone.push("a63ab".into());
    let r = report(&f, NOW);
    let s = &r.seats[0];
    assert_eq!(
        (s.alive.v, s.alive.why.as_str()),
        ("no", "no seat-now for 2 days"),
        "{s:?}"
    );
    assert_eq!(r.lost(), vec!["a63ab".to_string()]);
    assert!(
        r.complete,
        "a lost seat is a known fact, the round stays complete"
    );
    // a seat-now file older than LIST_DAYS says the same
    let mut f = one("6903a", 30, vec![], None);
    f.windows = None;
    f.now.insert(
        "6903a".into(),
        Known::Known(SeatNow {
            at: Some(NOW - LIST_DAYS * 86_400 - 60),
            state: None,
        }),
    );
    assert_eq!(seat_of(&f).alive.v, "no");
    // a fresh beat means it is not that long: still unknown, not lost
    let mut f = one("6903a", 30, vec![], None);
    f.windows = None;
    f.now.clear();
    f.gone.push("6903a".into());
    f.beat.insert("6903a".into(), NOW - 600);
    assert_eq!(seat_of(&f).alive.v, "unknown");
    // a seat-now that cannot be read (not gone) is not taken as gone
    let mut f = one("6903a", 30, vec![], None);
    f.windows = None;
    f.now
        .insert("6903a".into(), Known::Unknown("permission".into()));
    assert_eq!(seat_of(&f).alive.v, "unknown");
}

#[test]
fn a_queue_only_seat_with_few_unread_or_an_empty_queue_is_lost_and_the_round_complete() {
    let root = std::env::temp_dir().join(format!("aib-drive-sigc-m1-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    // a63ab: one unread, its cabin is not there; 6903a: an empty queue
    let q = root.join("state/dm-queue/a63ab");
    std::fs::create_dir_all(&q).unwrap();
    std::fs::write(
        q.join("q_0.json"),
        format!(r#"{{"status":"delivered","sent_at":{},"read_at":null,"done_at":null,"hub":"aihub-gone-b433b-test","wid":"@9"}}"#, NOW - 600),
    )
    .unwrap();
    std::fs::create_dir_all(root.join("state/dm-queue/6903a")).unwrap();
    std::fs::create_dir_all(root.join("state/seat-now")).unwrap();
    let src = FsSource::new(root.clone());
    assert!(src.seat_now_gone("a63ab"));
    let r = report(&src, NOW);
    assert_eq!(r.lost(), vec!["6903a".to_string(), "a63ab".to_string()]);
    let empty = r.seats.iter().find(|s| s.seat == "6903a").unwrap();
    assert_eq!(empty.alive.why, "no seat-now for 2 days");
    assert!(r.complete, "{r:?}");
    let _ = std::fs::remove_dir_all(&root);
}

/// 5d33a 11:06 (519d0 11:05 ruling): open PRs that cannot be read (e.g. run outside a git repo)
/// keep the round complete and the seat dispatchable, and the seat's reasons say so; read PRs
/// add no such line.
#[test]
fn open_prs_that_cannot_be_read_are_named_in_the_seats_reasons() {
    let f = free_seat();
    let r = report(&f, NOW);
    let s = &r.seats[0];
    assert!(r.complete && s.dispatchable == "yes", "{s:?}");
    assert!(
        s.reasons.iter().any(|x| x.starts_with("open_prs 讀不到:")),
        "{:?}",
        s.reasons
    );
    let mut f = free_seat();
    f.prs = Some(vec![]);
    let s = seat_of(&f);
    assert!(
        !s.reasons.iter().any(|x| x.starts_with("open_prs")),
        "{:?}",
        s.reasons
    );
}
