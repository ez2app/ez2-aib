//! `aib-mergecheck ready` over fixture worlds (IFACE-mergecheck-ready-7b81e-1117 r2 §8). No
//! network, no git: every read comes from the fixture.

use aib_mergecheck::fsworld::{is_macos_label, state_root, FsWorld};
use aib_mergecheck::*;
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const MAIN: &str = "1111111111111111111111111111111111111111";
const WF: &str = ".github/workflows/aibd.yml";
const WF_TEXT: &str = "on: push\njobs:\n  rust:\n    runs-on: x\n    steps:\n      - uses: actions/checkout@v4\n      - name: build\n        run: cargo build\n      - name: test\n        run: cargo test\n";
const REQ: [&str; 2] = ["rust (macos-latest)", "rust (ubuntu-latest)"];
const T0: &str = "2026-11-17T10:00:00Z";
const NOW: i64 = 1_794_909_600; // 2026-11-17T10:00:00Z

fn h(c: char) -> String {
    c.to_string().repeat(40)
}

fn cell(name: &str, conclusion: &str, started: Option<&str>) -> Value {
    let status = if conclusion.is_empty() {
        "IN_PROGRESS"
    } else {
        "COMPLETED"
    };
    json!({"name": name, "status": status, "conclusion": conclusion, "startedAt": started.unwrap_or("")})
}

fn green() -> Vec<Value> {
    REQ.iter().map(|n| cell(n, "SUCCESS", Some(T0))).collect()
}

fn pr(n: u32, head: &str, branch: &str, base: &str, draft: bool, checks: Vec<Value>) -> Value {
    json!({"number": n, "title": format!("pr {n}"), "headRefName": branch, "headRefOid": head,
           "baseRefName": base, "mergeable": "MERGEABLE", "statusCheckRollup": checks,
           "commits": [{"oid": head}], "isDraft": draft, "body": ""})
}

fn review(head: &str) -> String {
    format!("結論: 通過 頭: {head} H/M/L/N: 0/0/0/0 範圍: 全文 {MAIN}..{head}\n")
}

fn receipt(head: &str) -> String {
    format!("== HEAD={head}\nHEAD-END {head}\nRC fmt=0\nRC test=0\nKNIVES-RC 0\nMISSES-COUNT 0\nTEST-OK\n")
}

#[derive(Clone)]
struct Fake {
    prs: Result<String, String>,
    required: Result<Vec<String>, String>,
    clash: BTreeSet<(String, String)>,
    /// pairs that clash only once the first is in main (merge_after), not head against head
    after_clash: BTreeSet<(String, String)>,
    refresh: Result<(), String>,
    /// the order the reads came in
    calls: RefCell<Vec<&'static str>>,
    lock_after: Option<String>,
    wf: Vec<(String, u64)>,
    merged_text: Option<String>,
    changed: BTreeMap<String, Vec<(String, String)>>,
    merges: BTreeMap<String, usize>,
    pending: BTreeMap<String, String>,
    plan: String,
    jobs: Vec<CiJob>,
    mac: Result<MacLoad, String>,
    runners: Vec<Runner>,
    sides: Vec<(String, String)>,
}

impl Fake {
    /// a world with these PRs; every head `x` gets a card S-x (owner 7b81e) whose record names
    /// the PR, a passing review by 5d33a and a green receipt of that head
    fn new(prs: Vec<Value>) -> Fake {
        let mut pending = BTreeMap::new();
        let mut nodes = Vec::new();
        for p in &prs {
            let n = p["number"].as_u64().expect("fixture pr has a number");
            let head = p["headRefOid"]
                .as_str()
                .expect("fixture pr has a head")
                .to_owned();
            let rv = format!("REVIEW-{n}-5d33a-{}.md", &head[..8]);
            let rc = format!("RECEIPT-andymini-7b81e-{n}-{}.out", &head[..9]);
            pending.insert(rv.clone(), review(&head));
            pending.insert(rc.clone(), receipt(&head));
            nodes.push(
                json!({"id": format!("S-{n}"), "owner": "7b81e", "status": "審過排隊",
                              "evidence": format!("#{n} {head} {rv} {rc}")}),
            );
        }
        Fake {
            prs: Ok(Value::Array(prs).to_string()),
            required: Ok(REQ.iter().map(|s| (*s).to_owned()).collect()),
            clash: BTreeSet::new(),
            after_clash: BTreeSet::new(),
            refresh: Ok(()),
            calls: RefCell::new(Vec::new()),
            lock_after: None,
            wf: vec![(WF.into(), 280_354)],
            merged_text: None,
            changed: BTreeMap::new(),
            merges: BTreeMap::new(),
            pending,
            plan: json!({"nodes": nodes}).to_string(),
            jobs: Vec::new(),
            mac: Ok(MacLoad::default()),
            runners: Vec::new(),
            sides: Vec::new(),
        }
    }
    fn clashes(mut self, a: &str, b: &str) -> Fake {
        self.clash.insert((a.to_owned(), b.to_owned()));
        self.clash.insert((b.to_owned(), a.to_owned()));
        self.after_clash.insert((a.to_owned(), b.to_owned()));
        self.after_clash.insert((b.to_owned(), a.to_owned()));
        self
    }
    fn changes(mut self, head: &str, list: &[(&str, &str)]) -> Fake {
        let v = list
            .iter()
            .map(|(s, p)| ((*s).to_owned(), (*p).to_owned()))
            .collect();
        self.changed.insert(head.to_owned(), v);
        self
    }
}

impl World for Fake {
    fn refresh(&self) -> Result<(), String> {
        self.calls.borrow_mut().push("refresh");
        self.refresh.clone()
    }
    fn open_prs(&self) -> Result<String, String> {
        self.calls.borrow_mut().push("open_prs");
        self.prs.clone()
    }
    fn required_checks(&self) -> Result<Vec<String>, String> {
        self.required.clone()
    }
    fn main_head(&self) -> Result<String, String> {
        self.calls.borrow_mut().push("main_head");
        Ok(MAIN.into())
    }
    fn merge_tree(&self, a: &str, b: &str) -> Merge {
        if self.clash.contains(&(a.to_owned(), b.to_owned())) {
            Merge::Conflict(vec!["x.rs".into()])
        } else {
            Merge::Clean(format!("t{}", &b[..6]))
        }
    }
    fn merge_after(&self, _base: &str, a: &str, b: &str) -> Merge {
        if self.after_clash.contains(&(a.to_owned(), b.to_owned())) {
            Merge::Conflict(vec!["x.rs".into()])
        } else {
            Merge::Clean(format!("u{}", &b[..6]))
        }
    }
    fn workflows(&self, _tree: &str) -> Result<Vec<(String, u64)>, String> {
        Ok(self.wf.clone())
    }
    fn show(&self, rev: &str, path: &str) -> Option<String> {
        if path == "Cargo.lock" {
            let base = "[[package]]\nname = \"aib-core\"\nversion = \"0\"\n";
            return Some(match (rev.starts_with('t'), &self.lock_after) {
                (true, Some(l)) => l.clone(),
                _ => base.to_owned(),
            });
        }
        if rev.starts_with('t') {
            Some(self.merged_text.clone().unwrap_or_else(|| WF_TEXT.into()))
        } else {
            Some(WF_TEXT.into())
        }
    }
    fn changed(&self, _base: &str, head: &str) -> Result<Vec<(String, String)>, String> {
        Ok(self.changed.get(head).cloned().unwrap_or_default())
    }
    fn first_parent_merges(&self, _base: &str, head: &str) -> Result<usize, String> {
        Ok(self.merges.get(head).copied().unwrap_or(0))
    }
    fn side_heads(&self) -> Result<Vec<(String, String)>, String> {
        Ok(self.sides.clone())
    }
    fn pending(&self, name: &str) -> Option<String> {
        self.pending.get(name).cloned()
    }
    fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
        Some(a == b)
    }
    fn plan(&self) -> Result<String, String> {
        Ok(self.plan.clone())
    }
    fn ci_jobs(&self) -> Result<Vec<CiJob>, String> {
        Ok(self.jobs.clone())
    }
    fn receipt_runs(&self) -> Result<Vec<ReceiptRun>, String> {
        Ok(Vec::new())
    }
    fn mac_load(&self) -> Result<MacLoad, String> {
        self.mac.clone()
    }
    fn runners(&self) -> Result<Vec<Runner>, String> {
        Ok(self.runners.clone())
    }
    fn releasing(&self) -> Result<bool, String> {
        Ok(false)
    }
    fn pr_merge_tree(&self, _pr: u32) -> Option<String> {
        None
    }
    fn now_s(&self) -> i64 {
        NOW
    }
}

fn run(w: &Fake) -> Report {
    ready(w, &Cfg::default(), &BTreeSet::new())
}

fn one(r: &Report, n: u32) -> &PrReport {
    r.prs.iter().find(|p| p.pr == n).expect("pr in report")
}

#[test]
fn a_clean_reviewed_green_pr_is_mergeable() {
    let r = run(&Fake::new(vec![pr(
        810,
        &h('a'),
        "feat/x-7b81e-1117",
        "main",
        false,
        green(),
    )]));
    let p = one(&r, 810);
    assert!(p.mergeable, "{:?}", p.items);
    assert_eq!(p.card.as_deref(), Some("S-810"));
    assert_eq!(r.order, vec![810]);
    assert_eq!(r.load.verdict, "現在合");
    assert_eq!(r.rc(), 0);
}

#[test]
fn a_pr_based_on_a_side_branch_is_not_mergeable_and_says_the_base() {
    let r = run(&Fake::new(vec![pr(
        829,
        &h('a'),
        "feat/x",
        "feat/old",
        false,
        green(),
    )]));
    let p = one(&r, 829);
    assert!(!p.mergeable);
    assert_eq!(p.items["base"], V::Fail("基底是 feat/old".into()));
}

#[test]
fn a_green_draft_is_not_mergeable() {
    let r = run(&Fake::new(vec![pr(
        800,
        &h('a'),
        "feat/x",
        "main",
        true,
        green(),
    )]));
    assert!(!one(&r, 800).mergeable);
    assert_eq!(one(&r, 800).items["draft"], V::Fail("還是草稿".into()));
}

#[test]
fn two_clean_prs_that_clash_are_both_mergeable_and_marked_not_together() {
    let (a, b) = (h('a'), h('b'));
    let w = Fake::new(vec![
        pr(1, &a, "feat/a", "main", false, green()),
        pr(2, &b, "feat/b", "main", false, green()),
    ])
    .clashes(&a, &b);
    let r = run(&w);
    assert!(one(&r, 1).mergeable && one(&r, 2).mergeable);
    assert_eq!(one(&r, 1).not_with, vec![2]);
    assert_eq!(one(&r, 2).not_with, vec![1]);
    assert!(one(&r, 1).never_auto.iter().any(|s| s.contains("照班次")));
    assert!(!r.load.verdict.starts_with("連合"), "{}", r.load.verdict);
}

#[test]
fn gh_returning_nothing_or_timing_out_is_unread_and_rc_3() {
    let mut w = Fake::new(vec![]);
    w.prs = Ok(String::new());
    let r = run(&w);
    assert_eq!(r.rc(), 3);
    assert!(r.prs.is_empty() && !r.unread.is_empty());
    assert!(
        r.load.verdict.starts_with("讀不到,不判"),
        "{}",
        r.load.verdict
    );
    w.prs = Err("gh: deadline 60s passed".into());
    // a busy macOS must not hide that nothing was read (0eed7 3d92c6b6 N1)
    w.mac = Ok(MacLoad {
        running_main: 1,
        ..MacLoad::default()
    });
    let r = run(&w);
    assert_eq!(r.rc(), 3);
    assert!(r.unread[0].contains("deadline"));
    assert!(
        r.load.verdict.starts_with("讀不到,不判"),
        "{}",
        r.load.verdict
    );
    assert!(!r.load.verdict.contains("macOS"), "{}", r.load.verdict);
}

#[test]
fn two_samples_print_unknown() {
    let mut w = Fake::new(vec![pr(
        5,
        &h('a'),
        "feat/a",
        "main",
        false,
        vec![
            cell(REQ[0], "", Some(T0)),
            cell(REQ[1], "SUCCESS", Some(T0)),
        ],
    )]);
    w.jobs = (1..=2)
        .map(|run| CiJob {
            run,
            job: REQ[0].into(),
            event: "pull_request".into(),
            branch: "feat/a".into(),
            conclusion: "success".into(),
            dur_s: 3_000,
            wait_s: 60,
            macos: true,
        })
        .collect();
    let r = run(&w);
    let e = one(&r, 5)
        .until_green
        .expect("a running cell has an estimate");
    assert_eq!((e.secs, e.n), (None, 2));
    // ordered by run id, which is not a time (review 9fae2967 N3)
    assert_eq!(e.last_at_ms, None);
    assert!(to_text(&r).contains("未知(樣本 2 次)"));
}

#[test]
fn the_same_fixture_prints_the_same_bytes_with_either_separator() {
    let a = Fake::new(vec![pr(810, &h('a'), "feat/x", "main", false, green())]);
    let mut b = a.clone();
    b.wf = vec![(".github\\workflows\\aibd.yml".into(), 280_354)];
    let (ra, rb) = (run(&a), run(&b));
    assert_eq!(to_json(&ra, T0).to_string(), to_json(&rb, T0).to_string());
    assert_eq!(to_text(&ra), to_text(&rb));
}

#[test]
fn the_11_17_1950_state_lists_837_810_766_812_and_holds_800_as_draft() {
    // hand-written from 519d0's 19:5x reading (not a recording): five PRs green and clean
    // against main and each other; #800 was still a draft and changed a workflow file
    let heads = [(837, 'c'), (810, 'd'), (800, 'e'), (766, 'f'), (812, '9')];
    let prs: Vec<Value> = heads
        .iter()
        .map(|(n, c)| {
            pr(
                *n,
                &h(*c),
                &format!("feat/p{n}"),
                "main",
                *n == 800,
                green(),
            )
        })
        .collect();
    let mut w = Fake::new(prs);
    w.wf = vec![
        (WF.into(), 280_354),
        (".github/workflows/ci.yml".into(), 279_088),
    ];
    w.changed.insert(h('e'), vec![("M".into(), WF.into())]);
    let r = run(&w);
    let mut ok: Vec<u32> = r.prs.iter().filter(|p| p.mergeable).map(|p| p.pr).collect();
    ok.sort_unstable();
    assert_eq!(ok, vec![766, 810, 812, 837]);
    assert!(!one(&r, 800).mergeable);
    assert!(r.prs.iter().all(|p| p.not_with.is_empty()));
    assert_eq!(one(&r, 810).workflow_max, Some((WF.into(), 280_354)));
    assert_eq!(r.load.verdict, "連合 4 張(只留最後一輪,約 ? 分)");
}

#[test]
fn an_unread_receipt_host_never_yields_merge_now() {
    let w = Fake::new(vec![pr(810, &h('a'), "feat/x", "main", false, green())]);
    let cfg = Cfg {
        receipt_hosts: vec!["andymini".into()],
        ..Cfg::default()
    };
    let r = ready(&w, &cfg, &BTreeSet::new());
    assert!(one(&r, 810).mergeable);
    assert!(
        r.load.verdict.starts_with("不給現在合"),
        "{}",
        r.load.verdict
    );
    assert_ne!(r.load.verdict, "現在合");
}

#[test]
fn a_cancelled_check_is_listed_as_cancelled_not_red_or_green() {
    let checks = vec![
        cell(REQ[0], "CANCELLED", Some(T0)),
        cell(REQ[1], "SUCCESS", Some(T0)),
    ];
    let r = run(&Fake::new(vec![pr(
        788,
        &h('a'),
        "feat/x",
        "main",
        false,
        checks,
    )]));
    let v = &one(&r, 788).items["checks"];
    assert!(matches!(v, V::Cancelled(w) if w.contains(REQ[0])), "{v:?}");
    assert!(!one(&r, 788).mergeable);
}

#[test]
fn review_item_calls_the_station_judge_not_a_copy() {
    let a = h('a');
    let mut w = Fake::new(vec![pr(810, &a, "feat/x", "main", false, green())]);
    // the station judge refuses a review that is not 通過; the same words come back here
    let rv = format!("REVIEW-810-5d33a-{}.md", &a[..8]);
    w.pending.insert(
        rv.clone(),
        format!("結論: 不通過 頭: {a} H/M/L/N: 0/1/0/0 範圍: 全文 {MAIN}..{a}\n"),
    );
    let r = run(&w);
    match &one(&r, 810).items["review"] {
        V::Fail(why) => assert!(why.contains(&format!("{rv} 結論不是通過")), "{why}"),
        v => panic!("{v:?}"),
    }
}

#[test]
fn a_review_of_an_older_head_is_not_review_pass() {
    // the card reviewed and receipted head a; the PR has since moved on to head b
    let (a, b) = (h('a'), h('b'));
    let w0 = Fake::new(vec![pr(810, &a, "feat/x", "main", false, green())]);
    let mut w = w0.clone();
    w.prs = Ok(Value::Array(vec![pr(810, &b, "feat/x", "main", false, green())]).to_string());
    let r = run(&w);
    match &one(&r, 810).items["review"] {
        V::Fail(why) => assert!(why.contains(&b[..8]), "{why}"),
        v => panic!("an older head's review passed: {v:?}"),
    }
}

#[test]
fn an_integration_pr_is_unread_and_points_at_its_check_file() {
    let (a, b) = (h('a'), h('b'));
    let mut w = Fake::new(vec![
        pr(838, &a, "integ/stack-a9136-1117", "main", false, green()),
        pr(839, &b, "feat/b", "main", false, green()),
    ]);
    w.merges.insert(b.clone(), 2);
    let r = run(&w);
    for n in [838, 839] {
        match &one(&r, n).items["review"] {
            V::Unread(why) => assert!(why.contains("整合 PR") && why.contains("CHECK-"), "{why}"),
            v => panic!("#{n}: {v:?}"),
        }
    }
    assert_eq!(r.rc(), 3);
}

#[test]
fn green_mergeable_prs_say_chain_merge_not_stack() {
    let prs = ['a', 'b', 'c']
        .iter()
        .enumerate()
        .map(|(i, c)| {
            pr(
                i as u32 + 1,
                &h(*c),
                &format!("feat/{c}"),
                "main",
                false,
                green(),
            )
        })
        .collect();
    let r = run(&Fake::new(prs));
    assert!(
        r.load.verdict.starts_with("連合 3 張"),
        "{}",
        r.load.verdict
    );
    assert!(!r.load.verdict.contains("疊"));
}

#[test]
fn a_skipped_required_cell_counts_as_passed_and_is_listed() {
    let checks = vec![
        cell(REQ[0], "SKIPPED", Some(T0)),
        cell(REQ[1], "SUCCESS", Some(T0)),
    ];
    let r = run(&Fake::new(vec![pr(
        9,
        &h('a'),
        "docs/x",
        "main",
        false,
        checks,
    )]));
    let p = one(&r, 9);
    assert_eq!(p.items["checks"], V::Pass);
    assert_eq!(p.skipped_cells, vec![REQ[0].to_owned()]);
    assert!(to_text(&r).contains("skip 1 格"));
}

#[test]
fn a_rerun_cell_is_judged_by_its_latest_run() {
    let later = "2026-11-17T11:00:00Z";
    let old_red = vec![
        cell(REQ[0], "FAILURE", Some(T0)),
        cell(REQ[0], "SUCCESS", Some(later)),
        cell(REQ[1], "SUCCESS", Some(T0)),
    ];
    let r = run(&Fake::new(vec![pr(
        9,
        &h('a'),
        "feat/x",
        "main",
        false,
        old_red,
    )]));
    assert_eq!(one(&r, 9).items["checks"], V::Pass);
    let new_red = vec![
        cell(REQ[0], "SUCCESS", Some(T0)),
        cell(REQ[0], "FAILURE", Some(later)),
        cell(REQ[1], "SUCCESS", Some(T0)),
    ];
    let r = run(&Fake::new(vec![pr(
        9,
        &h('a'),
        "feat/x",
        "main",
        false,
        new_red,
    )]));
    assert!(matches!(one(&r, 9).items["checks"], V::Fail(_)));
}

#[test]
fn a_step_lost_in_a_hand_merge_fails_the_step_item() {
    let mut w = Fake::new(vec![pr(9, &h('a'), "feat/x", "main", false, green())]);
    w.merged_text = Some(WF_TEXT.replace("      - name: test\n        run: cargo test\n", ""));
    let r = run(&w);
    match &one(&r, 9).items["steps"] {
        V::Fail(why) => assert!(why.contains("rust/test"), "{why}"),
        v => panic!("{v:?}"),
    }
}

#[test]
fn a_step_removed_with_a_matching_allow_line_passes() {
    let a = h('a');
    let mut p = pr(9, &a, "feat/x", "main", false, green());
    p["body"] = json!("drop the test step\nstep-removed: rust/test\n");
    let mut w = Fake::new(vec![p]);
    w.merged_text = Some(WF_TEXT.replace("      - name: test\n        run: cargo test\n", ""));
    assert_eq!(one(&run(&w), 9).items["steps"], V::Pass);
}

#[test]
fn an_allow_that_matches_nothing_fails() {
    let mut p = pr(9, &h('a'), "feat/x", "main", false, green());
    p["body"] = json!("step-removed: rust/gone\n");
    let r = run(&Fake::new(vec![p]));
    match &one(&r, 9).items["steps"] {
        V::Fail(why) => assert!(why.contains("對不上的 step-removed rust/gone"), "{why}"),
        v => panic!("{v:?}"),
    }
}

#[test]
fn a_new_gate_pr_says_people_can_merge_it_early() {
    let a = h('a');
    let mut w = Fake::new(vec![pr(9, &a, "feat/x", "main", false, green())]);
    w.changed.insert(a, vec![("M".into(), WF.into())]);
    let r = run(&w);
    assert!(one(&r, 9).mergeable);
    assert!(one(&r, 9)
        .never_auto
        .iter()
        .any(|s| s.starts_with("新閘(") && s.ends_with("人按可提前")));
}

#[test]
fn a_busy_macos_says_wait() {
    let mut w = Fake::new(vec![pr(9, &h('a'), "feat/x", "main", false, green())]);
    w.mac = Ok(MacLoad {
        running_main: 1,
        ..MacLoad::default()
    });
    assert!(run(&w).load.verdict.starts_with("等 macOS 空"));
}

#[test]
fn the_pending_dir_comes_from_the_config() {
    let dir = std::env::temp_dir().join(format!("aib-mergecheck-pending-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("REVIEW-1-5d33a-aaaaaaaa.md"), "結論: 通過\n").unwrap();
    let cfg = Cfg::parse(&json!({"pending_dir": dir.to_string_lossy()}).to_string()).unwrap();
    assert_eq!(cfg.pending_dir.as_deref(), Some(&*dir.to_string_lossy()));
    let w = FsWorld::new(
        ".".into(),
        "o/n".into(),
        "main".into(),
        cfg.pending_dir.as_ref().unwrap().into(),
        dir.join("data.json"),
        dir.clone(),
    );
    assert_eq!(
        World::pending(&w, "REVIEW-1-5d33a-aaaaaaaa.md").as_deref(),
        Some("結論: 通過\n")
    );
    // a name with a folder is never read
    assert_eq!(World::pending(&w, "../REVIEW-1-5d33a-aaaaaaaa.md"), None);
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn an_unknown_config_key_is_refused() {
    assert!(Cfg::parse(r#"{"mian":"main"}"#).is_err());
    assert_eq!(Cfg::parse("{}").unwrap(), Cfg::default());
}

#[test]
fn newly_ready_lists_only_a_pr_head_not_mergeable_last_time() {
    let a = h('a');
    let w = Fake::new(vec![pr(9, &a, "feat/x", "main", false, green())]);
    let first = ready(&w, &Cfg::default(), &BTreeSet::new());
    assert_eq!(first.newly_ready, vec![9]);
    let prev = parse_prev(&prev_json(&first));
    let second = ready(&w, &Cfg::default(), &prev);
    assert!(second.newly_ready.is_empty());
}

// ---- review 9fae2967 (5d33a 0/1/4/3) ----

#[test]
fn refresh_runs_before_main_is_read() {
    let w = Fake::new(vec![pr(9, &h('a'), "feat/x", "main", false, green())]);
    let _ = run(&w);
    let calls = w.calls.borrow();
    assert_eq!(calls.first(), Some(&"refresh"), "{calls:?}");
    let at = |n: &str| calls.iter().position(|c| *c == n);
    assert!(
        at("refresh") < at("main_head") && at("refresh") < at("open_prs"),
        "{calls:?}"
    );
}

#[test]
fn a_failed_refresh_lists_nothing_mergeable_and_is_rc_3() {
    let mut w = Fake::new(vec![pr(9, &h('a'), "feat/x", "main", false, green())]);
    w.refresh = Err("git fetch: deadline 60s passed".into());
    let r = run(&w);
    assert!(r.prs.iter().all(|p| !p.mergeable));
    assert_eq!(r.rc(), 3);
    assert!(r.unread[0].contains("主線沒更新"), "{:?}", r.unread);
}

#[test]
fn never_auto_lists_each_category_with_its_own_word() {
    let a = h('a');
    let mut w = Fake::new(vec![pr(9, &a, "feat/x", "main", false, green())]).changes(
        &a,
        &[
            ("M", "crates/aib-plan/src/card.rs"),
            ("D", "docs/old.md"),
            ("A", "crates/aib-review/knives/knives-02.sh"),
            ("M", "clippy.toml"),
            ("M", "crates/aib-own-gate/src/lib.rs"),
        ],
    );
    w.lock_after = Some(
        "[[package]]\nname = \"aib-core\"\nversion = \"0\"\n\n[[package]]\nname = \"left-pad\"\nversion = \"1\"\n"
            .into(),
    );
    let r = run(&w);
    let n = &one(&r, 9).never_auto;
    for cat in [
        "煞車(",
        "刪檔(docs/old.md)",
        "煞車(刀)(",
        "工具鏈(",
        "核准類(",
        "Cargo.lock 新套件(left-pad)",
    ] {
        assert!(
            n.iter()
                .any(|s| s.starts_with(cat) && s.ends_with("人按可提前")),
            "{cat}: {n:?}"
        );
    }
    assert!(!n.iter().any(|s| s.starts_with("新閘")), "{n:?}");
}

#[test]
fn a_brake_pr_is_not_counted_into_a_chain_merge() {
    // tonight's gate-card: aib-own-gate, green and clean, next to two plain PRs
    let (a, b, c) = (h('a'), h('b'), h('c'));
    let w = Fake::new(vec![
        pr(1, &a, "feat/a", "main", false, green()),
        pr(2, &b, "feat/b", "main", false, green()),
        pr(3, &c, "feat/gate-card", "main", false, green()),
    ])
    .changes(&c, &[("M", "crates/aib-own-gate/src/lib.rs")]);
    let r = run(&w);
    assert!(!r.load.verdict.starts_with("連合"), "{}", r.load.verdict);
}

#[test]
fn a_self_hosted_macos_label_counts_as_macos() {
    assert!(is_macos_label("macOS"));
    assert!(is_macos_label("macos-latest"));
    assert!(!is_macos_label("self-hosted"));
    assert!(!is_macos_label("ARM64"));
}

#[test]
fn two_prs_are_compared_with_the_first_merged_into_main() {
    // head against head is clean, but once #1 is in main #2 conflicts (IFACE §2)
    let (a, b) = (h('a'), h('b'));
    let mut w = Fake::new(vec![
        pr(1, &a, "feat/a", "main", false, green()),
        pr(2, &b, "feat/b", "main", false, green()),
    ]);
    w.after_clash.insert((a.clone(), b.clone()));
    let r = run(&w);
    assert_eq!(one(&r, 1).not_with, vec![2]);
    assert_eq!(one(&r, 2).not_with, vec![1]);
}

#[test]
fn a_release_word_elsewhere_on_the_card_does_not_release_an_m() {
    let a = h('a');
    let mut w = Fake::new(vec![pr(810, &a, "feat/x", "main", false, green())]);
    let rv = format!("REVIEW-810-5d33a-{}.md", &a[..8]);
    w.pending.insert(
        rv.clone(),
        format!("結論: 通過 頭: {a} H/M/L/N: 0/1/0/0 範圍: 全文 {MAIN}..{a}\n"),
    );
    let ev = |extra: &str| {
        let rc = format!("RECEIPT-andymini-7b81e-810-{}.out", &a[..9]);
        json!({"nodes": [{"id": "S-810", "owner": "7b81e", "status": "審過排隊",
            "evidence": format!("11-17 舊的一句：請 f1fae 手動放行 #700 | #810 {a} {rv} {rc}{extra}")}]})
        .to_string()
    };
    w.plan = ev("");
    match &one(&run(&w), 810).items["review"] {
        V::Fail(_) => {}
        v => panic!("an old 放行 on the card let an M pass: {v:?}"),
    }
    // the same M with 放行 written next to this head passes
    w.plan = ev(" M1 放行");
    assert_eq!(one(&run(&w), 810).items["review"], V::Pass);
}

#[test]
fn record_for_head_keeps_only_the_pieces_naming_the_head() {
    let a = h('a');
    let rec = format!("x 放行 | y {} z\nw {a}", &a[..8]);
    assert_eq!(
        record_for_head(&rec, &a),
        format!("y {} z | w {a}", &a[..8])
    );
    assert_eq!(record_for_head("only 放行", &a), "");
}

#[test]
fn the_state_root_follows_aib_drives_order() {
    let home = Path::new("/h");
    assert_eq!(
        state_root(Some("/r"), Some("/a"), Some(home)),
        Some("/r".into())
    );
    assert_eq!(
        state_root(Some(""), Some("/a"), Some(home)),
        Some("/a".into())
    );
    assert_eq!(
        state_root(None, Some(""), Some(home)),
        Some(home.join(".ez2-aib"))
    );
    assert_eq!(state_root(None, None, None), None);
}

#[test]
fn a_config_never_auto_map_replaces_the_default() {
    let c = Cfg::parse(r#"{"never_auto_paths": {"src/": "自訂"}}"#).expect("parses");
    assert_eq!(
        c.never_auto_paths,
        vec![("src/".to_owned(), "自訂".to_owned())]
    );
    assert!(Cfg::parse(r#"{"never_auto_paths": [".github/"]}"#).is_err());
    assert!(path_matches("*/knives/", "crates/x/knives/a.sh"));
    assert!(!path_matches("crates/aib-plan/", "crates/aib-planx/a.rs"));
}
