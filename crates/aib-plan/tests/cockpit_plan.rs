// SPDX-License-Identifier: Apache-2.0
//! 座艙中欄(plan + hosts)進自動組裝(1ced7 11-13;Andy「要，寫進自動組裝」)。
//! 519d0 11-14:開關改由 a9136 的 `aib-seatview plan` / `filter-roles` 管(IFACE-seatview-plan-a9136-1114),
//! 這裡測座艙腳本怎麼接它;原本的 Python 測試改寫在這裡。開關本身的測試在 aib-seatview 那邊。
//!
//! P1 cockpit_pane_plan 一律問 `filter-roles`:有外框 session 帶 `--window S:cockpit`,沒有(--print-plan)不帶;
//!    stdin 是 panes.py 的格
//! P2 aib-seatview 找不到 / 跑不起來 / 印空 ⇒ 沿用 panes.py 的格(沒有中欄),rc 0、不讓開艙中止
//! P3 中欄開著:四種形狀都把 plan 接在所有格之後、從 hub 往右切,hosts 從 plan 往下切;關著 / 沒 hub ⇒ 沒有
//! P4 _grow_missing_plan_column:已開著的座艙只缺進度欄 ⇒ 原地補兩格、版面驗得過、hub 不動
//! P4b 只剩 hosts、plan 被收掉 ⇒ 收 hosts 再補兩格;P4c 開關關掉 ⇒ 原地收掉兩格
//! P5 形狀不對(多一格 / 缺右欄 / 已經有兩格 plan / 沒 hub)⇒ 不動手、回非 0
//! P6 _balance_plan_column:夠寬 ⇒ 調 hub 讓 plan = 100;再窄對半;只調 hub 不調 plan
//! P7 pane-plan.sh 找不到程式不退出、Ctrl-C 不死，未知名字 rc 2
//! P8 席碼一定找得到：看的窗 → 該窗席位檔 → 艦長窗 → 最近的席位檔
//! P9 aib plan on|off|status:交給 aib-seatview plan、rc 照傳;on / off 成功多一句「下次開席 / 自癒生效」,
//!    rc 3 補人話;找不到程式 rc 3、其他寫法 rc 2(043dd r2 L1、L2)
//!
//! 不碰真 tmux:被測函式從原檔逐字抽出來,outer_tmux 換成讀寫狀態檔的假函式;
//! panes.py 換成照 FAKE_ROLES 印格的假程式(凍結的 Python 不在這裡跑);
//! aib-seatview 換成照 IFACE 規則 1、2 回格的假程式($T/switch:on / off / fail,沒檔 = on)。
#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::Duration;

fn cockpit() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit")
}

/// `name() {` 那一行到下一個獨立的 `}` 那一行(逐字)
fn extract(file: &str, name: &str) -> String {
    let src = std::fs::read_to_string(cockpit().join(file)).expect("read script");
    let head = format!("{name}() {{");
    let mut out = String::new();
    let mut inside = false;
    for line in src.lines() {
        if !inside && line.starts_with(&head) {
            inside = true;
        }
        if inside {
            out.push_str(line);
            out.push('\n');
            if line == "}" {
                return out;
            }
        }
    }
    panic!("{name} not found in {file}");
}

const FAKE_TMUX: &str = r#"
STATE="$T/rows"
outer_tmux() {
  echo "$*" >> "$T/calls"
  case "$1" in
    list-panes)
      case "$*" in
        *pane_dead*) awk -F '|' '{ print $1 " " $2 " 0" }' "$STATE" ;;
        *) awk -F '|' '{ print $1 "|" $2 }' "$STATE" ;;
      esac ;;
    split-window)
      local n; n="%$(( $(wc -l < "$STATE") + 50 ))"
      echo "$n||0" >> "$STATE"; echo "$n" ;;
    set-option)
      awk -F '|' -v id="$4" -v r="$6" 'BEGIN{OFS="|"} $1==id{$2=r} {print}' "$STATE" > "$STATE.n" && mv "$STATE.n" "$STATE" ;;
    display|display-message)
      local id="" a
      for a in "$@"; do case "$a" in %*) id="$a" ;; esac; done
      awk -F '|' -v id="$id" '$1==id{print $3}' "$STATE" ;;
    kill-pane)
      awk -F '|' -v id="$3" '$1!=id' "$STATE" > "$STATE.n" && mv "$STATE.n" "$STATE" ;;
    resize-pane) ;;
  esac
}
"#;

/// 假 aib-seatview:記下參數與 stdin;filter-roles 照 IFACE 規則 1、2(開著且有 hub ⇒ 補 plan、hosts)
const FAKE_SEATVIEW: &str = r#"#!/bin/sh
echo "$*" >> "$T/sv-args"
if [ "$1" = plan ]; then rc=0; [ -f "$T/plan-rc" ] && rc=$(cat "$T/plan-rc"); [ "$rc" = 0 ] && echo "$2"; [ "$rc" = 3 ] && echo no_state_dir >&2; exit "$rc"; fi
[ "$1" = filter-roles ] || exit 2
sw=on; [ -f "$T/switch" ] && sw=$(cat "$T/switch")
[ "$sw" = fail ] && exit 4
roles=$(cat)
printf '%s\n' "$roles" > "$T/sv-stdin"
[ "$sw" = empty ] && exit 0
printf '%s\n' "$roles" | grep -v '^$'
if [ "$sw" = on ] && printf '%s\n' "$roles" | grep -qx hub; then echo plan; echo hosts; fi
"#;

struct T {
    d: tempfile::TempDir,
}

impl T {
    fn new() -> T {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().expect("cockpit_plan test setup");
        // 假 panes.py:照 FAKE_ROLES 一行印一格(參數不看)
        for (name, body) in [
            (
                "fake-py3",
                "#!/bin/sh\nfor r in $FAKE_ROLES; do echo \"$r\"; done\n",
            ),
            ("aib-seatview", FAKE_SEATVIEW),
        ] {
            let p = d.path().join(name);
            std::fs::write(&p, body).expect("cockpit_plan test setup");
            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755))
                .expect("cockpit_plan test setup");
        }
        T { d }
    }
    fn p(&self, n: &str) -> PathBuf {
        self.d.path().join(n)
    }
    /// 假開關:on / off / fail(rc 4)/ empty(rc 0 但沒印);None = 沒檔(= on)
    fn set_switch(&self, v: Option<&str>) {
        match v {
            Some(v) => std::fs::write(self.p("switch"), v).expect("cockpit_plan test setup"),
            None => {
                let _ = std::fs::remove_file(self.p("switch"));
            }
        }
    }
    fn sv_args(&self) -> String {
        std::fs::read_to_string(self.p("sv-args")).unwrap_or_default()
    }
    fn bash_with(&self, script: &str, roles: &str, seatview: &Path) -> Output {
        Command::new("bash")
            .args(["-c", script])
            .env("T", self.d.path())
            .env("C", cockpit())
            .env("EZ2_PY3", self.p("fake-py3"))
            .env("FAKE_ROLES", roles)
            .env("AIB_SEATVIEW_BIN", seatview)
            .env("HOME", self.d.path())
            .output()
            .expect("cockpit_plan test setup")
    }
    fn bash(&self, script: &str, roles: &str) -> Output {
        self.bash_with(script, roles, &self.p("aib-seatview"))
    }
    /// cockpit_pane_plan 的輸出(腳本路徑去掉);`s` = 外框 session(None = --print-plan)
    fn plan_lines_with(&self, roles: &str, s: Option<&str>, seatview: &Path) -> (i32, Vec<String>) {
        let script = format!(
            "{}{}{}cockpit_pane_plan",
            s.map(|s| format!("S={s}\n")).unwrap_or_default(),
            extract("cockpit-full.sh", "_aib_seatview_bin"),
            extract("cockpit-full.sh", "cockpit_pane_plan")
        );
        let o = self.bash_with(&script, roles, seatview);
        let c = format!("{}/", cockpit().display());
        let lines = String::from_utf8_lossy(&o.stdout)
            .lines()
            .map(|l| l.replace(&c, ""))
            .collect();
        (o.status.code().unwrap_or(-1), lines)
    }
    fn plan_lines(&self, roles: &str) -> (i32, Vec<String>) {
        self.plan_lines_with(roles, Some("sess"), &self.p("aib-seatview"))
    }
    fn write_rows(&self, rows: &[&str]) {
        let body: String = rows.iter().map(|r| format!("{r}\n")).collect();
        std::fs::write(self.p("rows"), body).expect("cockpit_plan test setup");
        std::fs::write(self.p("calls"), "").expect("cockpit_plan test setup");
    }
    fn rows(&self) -> Vec<String> {
        std::fs::read_to_string(self.p("rows"))
            .expect("cockpit_plan test setup")
            .split_whitespace()
            .map(str::to_string)
            .collect()
    }
    fn calls(&self) -> String {
        std::fs::read_to_string(self.p("calls")).expect("cockpit_plan test setup")
    }
    /// 回 (最後一行 rc=…, 列, 呼叫, stderr)
    fn grow(&self, rows: &[&str], roles: &str) -> (String, Vec<String>, String, String) {
        self.write_rows(rows);
        let mut script = FAKE_TMUX.to_string();
        for n in [
            "_aib_seatview_bin",
            "cockpit_pane_plan",
            "validate_cockpit_layout",
            "_assign_pane_role",
            "_grow_missing_plan_column",
            "cockpit_expected_roles",
        ] {
            script.push_str(&extract("cockpit-full.sh", n));
        }
        script.push_str(
            "S=sess\npane_command() { printf 'exec bash %s' \"$1\"; }\n_grow_missing_plan_column; echo \"rc=$?\"\n",
        );
        let o = self.bash(&script, roles);
        let out = String::from_utf8_lossy(&o.stdout);
        (
            out.trim().lines().last().unwrap_or("").to_string(),
            self.rows(),
            self.calls(),
            String::from_utf8_lossy(&o.stderr).into_owned(),
        )
    }
}

fn roles_of(rows: &[String]) -> Vec<String> {
    let mut r: Vec<String> = rows
        .iter()
        .map(|r| r.split('|').nth(1).unwrap_or("").to_string())
        .collect();
    r.sort();
    r
}

#[test]
fn p1_the_roles_always_go_through_filter_roles_with_the_window_when_there_is_one() {
    let t = T::new();
    let (rc, _) = t.plan_lines("left hub right");
    assert_eq!(rc, 0);
    assert_eq!(t.sv_args(), "filter-roles --window sess:cockpit\n");
    assert_eq!(
        std::fs::read_to_string(t.p("sv-stdin")).expect("cockpit_plan test setup"),
        "left\nhub\nright\n"
    );
    std::fs::remove_file(t.p("sv-args")).expect("cockpit_plan test setup");
    let (rc, lines) = t.plan_lines_with("hub right", None, &t.p("aib-seatview"));
    assert_eq!(rc, 0);
    assert_eq!(
        t.sv_args(),
        "filter-roles\n",
        "--print-plan 沒有外框:不帶 --window"
    );
    assert!(lines.iter().any(|l| l.starts_with("plan|")), "{lines:?}");
}

#[test]
fn p2_without_a_working_seatview_the_panes_roles_stay() {
    let t = T::new();
    let want_no_column = |(rc, lines): (i32, Vec<String>), why: &str| {
        assert_eq!(rc, 0, "{why}");
        assert!(
            lines.iter().any(|l| l.starts_with("right|hub|h|")),
            "{why}: {lines:?}"
        );
        assert!(
            !lines.iter().any(|l| l.starts_with("plan|")),
            "{why}: {lines:?}"
        );
    };
    want_no_column(
        t.plan_lines_with("hub right", Some("sess"), &t.p("missing")),
        "找不到",
    );
    t.set_switch(Some("fail"));
    want_no_column(t.plan_lines("hub right"), "rc 4");
    t.set_switch(Some("empty"));
    want_no_column(t.plan_lines("hub right"), "印空");
}

#[test]
fn p3_the_column_comes_last_cut_from_hub_and_hosts_from_plan() {
    let t = T::new();
    let tail = [
        "plan|hub|h|62%|pane-plan.sh",
        "hosts|plan|v|14|pane-hosts.sh",
    ];
    for roles in [
        "hub right",
        "left hub right",
        "left bottom hub right",
        "bottom hub right",
    ] {
        let (rc, lines) = t.plan_lines(roles);
        assert_eq!(rc, 0, "{roles}");
        assert_eq!(lines[lines.len() - 2..], tail, "{roles}");
        let right = lines
            .iter()
            .position(|l| l == "right|hub|h|-|pane-right-tabs.sh");
        let plan = lines.iter().position(|l| l == tail[0]);
        assert!(right < plan, "{roles}: {lines:?}");
    }
    let (_, lines) = t.plan_lines("right");
    assert!(!lines.join(" ").contains("plan"), "{lines:?}");

    t.set_switch(Some("off"));
    let (_, lines) = t.plan_lines("hub right");
    assert!(!lines.join(" ").contains("plan|"), "{lines:?}");
}

#[test]
fn p4_a_running_cockpit_grows_the_column_in_place() {
    let t = T::new();
    let (rc, rows, calls, err) = t.grow(&["%0|hub|54", "%1|right|40", "%2|review|54"], "hub right");
    assert_eq!(rc, "rc=0", "{err}");
    assert_eq!(roles_of(&rows), ["hosts", "hub", "plan", "review", "right"]);
    assert!(calls.contains("split-window -h -t %0 -l 62%"), "{calls}");
    let plan_id = rows
        .iter()
        .find(|r| r.split('|').nth(1) == Some("plan"))
        .and_then(|r| r.split('|').next())
        .expect("cockpit_plan test setup")
        .to_string();
    assert!(
        calls.contains(&format!("split-window -v -t {plan_id} -l 14")),
        "{calls}"
    );
    assert!(!calls.contains("kill"), "{calls}");

    // 只缺 hosts
    let (rc, _, calls, err) = t.grow(&["%0|hub|54", "%1|right|40", "%3|plan|100"], "hub right");
    assert_eq!(rc, "rc=0", "{err}");
    assert_eq!(calls.matches("split-window").count(), 1, "{calls}");
    assert!(calls.contains("split-window -v -t %3 -l 14"), "{calls}");
}

#[test]
fn p4b_hosts_left_without_plan_is_replaced() {
    let t = T::new();
    let (rc, rows, calls, err) = t.grow(&["%0|hub|54", "%1|right|40", "%4|hosts|100"], "hub right");
    assert_eq!(rc, "rc=0", "{err}");
    assert!(calls.contains("kill-pane -t %4"), "{calls}");
    assert_eq!(roles_of(&rows), ["hosts", "hub", "plan", "right"]);
}

#[test]
fn p4c_plan_off_removes_both_in_place() {
    let t = T::new();
    t.set_switch(Some("off"));
    let (rc, rows, calls, err) = t.grow(
        &[
            "%0|hub|54",
            "%1|right|40",
            "%3|plan|100",
            "%4|hosts|100",
            "%2|review|54",
        ],
        "hub right",
    );
    assert_eq!(rc, "rc=0", "{err}");
    assert_eq!(roles_of(&rows), ["hub", "review", "right"]);
    assert!(!calls.contains("split-window"), "{calls}");
    assert!(!calls.contains("kill-pane -t %0"), "{calls}");
}

#[test]
fn p5_a_wrong_shape_is_left_alone() {
    let t = T::new();
    for rows in [
        &["%0|hub|54"][..],
        &["%0|hub|54", "%1|right|40", "%9|left|30"][..],
        &["%0|hub|54", "%1|right|40", "%3|plan|9", "%4|plan|9"][..],
        &["%1|right|40"][..],
    ] {
        let (rc, _, calls, _) = t.grow(rows, "hub right");
        assert_ne!(rc, "rc=0", "{rows:?}");
        assert!(!calls.contains("split-window"), "{rows:?}: {calls}");
        assert!(!calls.contains("kill-pane"), "{rows:?}: {calls}");
    }
}

fn balance(t: &T, hw: u32, pw: u32) -> Vec<String> {
    std::fs::write(
        t.p("rows"),
        format!("%0|hub|{hw}\n%1|right|40\n%3|plan|{pw}\n"),
    )
    .expect("cockpit_plan test setup");
    std::fs::write(t.p("calls"), "").expect("cockpit_plan test setup");
    let mut script = FAKE_TMUX.to_string();
    for n in [
        "_resolve_role_pane",
        "read_geometry",
        "_balance_plan_column",
    ] {
        script.push_str(&extract("cockpit-resize.sh", n));
    }
    script.push_str("pane_absent() { [ -z \"${1:-}\" ]; }\nS=sess; P_HUB=%0\n_balance_plan_column; echo \"rc=$?\"\n");
    let o = t.bash(&script, "");
    assert!(
        String::from_utf8_lossy(&o.stdout).contains("rc=0"),
        "{}",
        String::from_utf8_lossy(&o.stderr)
    );
    t.calls()
        .lines()
        .filter(|l| l.starts_with("resize-pane"))
        .map(str::to_string)
        .collect()
}

#[test]
fn p6_balance_moves_hub_only() {
    let t = T::new();
    assert_eq!(balance(&t, 150, 60), ["resize-pane -t %0 -x 110"]);
    assert!(balance(&t, 54, 100).is_empty());
    assert_eq!(balance(&t, 80, 50), ["resize-pane -t %0 -x 50"]);
    assert_eq!(balance(&t, 50, 30), ["resize-pane -t %0 -x 40"]);
    for call in balance(&t, 150, 60) {
        assert!(!call.contains("%3"), "{call}");
    }
}

// ADR 0005 named exception: the wrapper is a separate process; the test waits real seconds for it
#[allow(clippy::disallowed_methods)]
#[test]
fn p7_the_wrapper_waits_for_a_missing_program_and_survives_ctrl_c() {
    use std::os::unix::process::CommandExt;
    let t = T::new();
    let o = Command::new("bash")
        .arg(cockpit().join("pane-plan.sh"))
        .arg("aib-nope")
        .output()
        .expect("cockpit_plan test setup");
    assert_eq!(o.status.code(), Some(2));

    let log = t.p("wrapper.out");
    let mut child = Command::new("bash")
        .arg(cockpit().join("pane-plan.sh"))
        .arg("aib-plan")
        .env("AIB_PLAN_BIN", t.p("missing"))
        .env("HOME", t.d.path())
        .env_remove("TMUX_PANE")
        .stdin(Stdio::null())
        .stdout(std::fs::File::create(&log).expect("cockpit_plan test setup"))
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .expect("cockpit_plan test setup");
    let pg = format!("-{}", child.id());
    let kill = |sig: &str| {
        let _ = Command::new("kill").args([sig, "--", &pg]).status();
    };
    std::thread::sleep(Duration::from_secs(3));
    let alive_after_wait = child.try_wait().expect("cockpit_plan test setup").is_none();
    kill("-INT");
    std::thread::sleep(Duration::from_secs(1));
    let alive_after_int = child.try_wait().expect("cockpit_plan test setup").is_none();
    kill("-KILL"); // 連 sleep 一起收，不留孤兒
    let _ = child.wait();
    assert!(alive_after_wait, "wrapper exited");
    assert!(alive_after_int, "wrapper died on SIGINT");
    let out = std::fs::read_to_string(&log).expect("cockpit_plan test setup");
    assert!(out.contains("aib-plan"), "{out}");
}

fn seat(t: &T, windows: &[&str], seat_files: &[(u32, &str)]) -> String {
    let home = t.p("inst");
    std::fs::create_dir_all(home.join("seats")).expect("cockpit_plan test setup");
    for e in std::fs::read_dir(home.join("seats")).expect("cockpit_plan test setup") {
        std::fs::remove_file(e.expect("cockpit_plan test setup").path())
            .expect("cockpit_plan test setup");
    }
    for (n, u) in seat_files {
        std::fs::write(
            home.join("seats").join(format!("w{n}.json")),
            format!("{{\"session_uuid\": \"{u}\"}}"),
        )
        .expect("cockpit_plan test setup");
    }
    let wins: String = windows.iter().map(|w| format!("{w}\n")).collect();
    std::fs::write(t.p("wins"), wins).expect("cockpit_plan test setup");
    let script = format!(
        "tmux() {{ [ \"$1 $2 $3\" = \"-L aihub-t list-windows\" ] || return 1; cat \"$T/wins\"; }}\nNAME=aib-plan\n{}{}_seat\n",
        extract("pane-plan.sh", "_seat"),
        extract("pane-plan.sh", "_seat_of_file")
    );
    let o = Command::new("bash")
        .args(["-c", &script])
        .env("T", t.d.path())
        .env("EZ2_AIHUB_SOCKET", "aihub-t")
        .env("EZ2_AIB_INSTANCE_HOME", &home)
        .output()
        .expect("cockpit_plan test setup");
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

#[test]
fn p8_the_seat_is_always_found() {
    let t = T::new();
    assert_eq!(seat(&t, &["1 1 @4 519d0", "1 0 @7 "], &[]), "519d0");
    assert_eq!(
        seat(&t, &["1 0 @4 519d0", "1 1 @7"], &[(7, "aaaa-bbbb-1ced7")]),
        "1ced7"
    );
    assert_eq!(seat(&t, &["1 0 @4 519d0", "1 1 @3"], &[]), "519d0");
    assert_eq!(seat(&t, &[], &[(6, "x-2240e")]), "2240e");
}

/// `aib` 腳本裡 `if [ "${1:-}" = "plan" ]; then` 到下一個獨立 `fi` 的那一段(逐字)
fn plan_dispatch() -> String {
    let src = std::fs::read_to_string(cockpit().join("aib")).expect("read aib");
    let mut out = String::new();
    let mut inside = false;
    for line in src.lines() {
        if !inside && line == "if [ \"${1:-}\" = \"plan\" ]; then" {
            inside = true;
        }
        if inside {
            out.push_str(line);
            out.push('\n');
            if line == "fi" {
                return out;
            }
        }
    }
    panic!("aib plan block not found");
}

#[test]
fn p9_aib_plan_hands_off_to_seatview_and_says_when_it_applies() {
    let t = T::new();
    let run = |args: &str, rc_file: Option<&str>, sv: &Path| -> (i32, String, String) {
        match rc_file {
            Some(rc) => std::fs::write(t.p("plan-rc"), rc).expect("cockpit_plan test setup"),
            None => {
                let _ = std::fs::remove_file(t.p("plan-rc"));
            }
        }
        // "環境設定|參數";座艙變數一律先清掉，免得跑測試的終端機本身在座艙裡
        let (pre, args) = args.rsplit_once('|').unwrap_or(("", args));
        let script = format!(
            "unset EZ2_COCKPIT_STATE_DIR EZ2_TMUX_SESSION EZ2_AIB_INSTANCE_HOME\n{pre}\nset -- {args}\n{}echo fell-through\n",
            plan_dispatch()
        );
        let o = t.bash_with(&script, "", sv);
        (
            o.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&o.stdout).into_owned(),
            String::from_utf8_lossy(&o.stderr).into_owned(),
        )
    };
    let sv = t.p("aib-seatview");

    let (rc, out, err) = run("plan off", None, &sv);
    assert_eq!((rc, out.as_str()), (0, "off\n"), "{err}");
    assert!(err.contains("下次開席"), "{err}");
    assert!(t.sv_args().ends_with("plan off\n"), "{}", t.sv_args());

    let (rc, out, err) = run("plan status", None, &sv);
    assert_eq!((rc, out.as_str()), (0, "status\n"));
    assert!(err.is_empty(), "status 不印生效提示: {err}");

    let (rc, out, err) = run("plan on", Some("3"), &sv);
    assert_eq!(rc, 3);
    assert!(out.is_empty(), "{out}");
    assert!(
        err.contains("no_state_dir") && err.contains("席位窗"),
        "{err}"
    );

    let (rc, _, err) = run("plan off", Some("4"), &sv);
    assert_eq!(rc, 4, "{err}");
    assert!(!err.contains("下次開席"), "{err}");

    let (rc, out, _) = run("plan on", None, &t.p("missing"));
    assert_eq!(rc, 3);
    assert!(!out.contains("fell-through"));

    for bad in ["plan", "plan toggle"] {
        let (rc, out, _) = run(bad, None, &sv);
        assert_eq!(rc, 2, "{bad}");
        assert!(!out.contains("fell-through"), "{bad}");
    }
    // 狀態夾:只有明設 EZ2_COCKPIT_STATE_DIR 才帶;其餘不帶，交給 aib-seatview 讀 @aib_state_dir(正本)
    let last = |t: &T| t.sv_args().lines().last().unwrap_or("").to_string();
    run(
        "export EZ2_COCKPIT_STATE_DIR=/c/sd EZ2_TMUX_SESSION=s EZ2_AIB_INSTANCE_HOME=/i|plan off",
        None,
        &sv,
    );
    assert_eq!(last(&t), "plan off --state-dir /c/sd");
    run(
        "export EZ2_TMUX_SESSION=s EZ2_AIB_INSTANCE_HOME=/i|plan on",
        None,
        &sv,
    );
    assert_eq!(last(&t), "plan on", "座艙內不自己推，讀 @aib_state_dir");
    run("export EZ2_AIB_INSTANCE_HOME=/i|plan status", None, &sv);
    assert_eq!(last(&t), "plan status", "不從 INSTANCE_HOME 推");

    // 不是 plan:整段不管，往下走
    let (rc, out, _) = run("left off", None, &sv);
    assert_eq!((rc, out.as_str()), (0, "fell-through\n"));
}
