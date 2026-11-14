//! `aib-verify g1`: the G1 acceptance run on a desktop Mac (PLAN-approvals-to-rust C1/C2).
//!
//! Groups, each on its own fresh aibd with one slot (`AIBD_MAX_SESSIONS=1`):
//! - `claude` (only with `--claude`): real Claude, one after another;
//! - `burst`: `aib-fixture-tree setuid-ps-burst` (30 setuid `ps` runs, no API);
//! - `stuck`: `aib-fixture-tree setuid-ps-stuck`, stopped while its setuid `ps` is
//!   blocked; the implication in `judge::judge_stuck`;
//! - with `--baseline-bin`: burst and stuck again on a head known to be broken, run
//!   first; they must go wrong somewhere, or the probes did not bite (see `judge`).
//!
//! Writes `<out>/VERIFY-g1.md` (first line, then the table) and `<out>/rows.json`;
//! prints the first line. rc 0 通過 / 1 不通過 / 2 usage / 3 could not run.

use crate::daemon::{alive, signal, Daemon};
use crate::judge::{self, Group, Row};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// How long a session may run before the run gives up on it (real Claude takes 2–25 s).
const RUN_WAIT: Duration = Duration::from_secs(180);
/// How long after the end the slot may take to come back (aibd re-checks every 30 s).
const SLOT_WAIT: Duration = Duration::from_secs(45);

#[derive(Debug, PartialEq)]
pub struct Opts {
    pub head: String,
    pub bin: PathBuf,
    pub fixture: PathBuf,
    pub out: PathBuf,
    pub baseline_bin: Option<PathBuf>,
    pub claude: Option<PathBuf>,
    pub claude_signer: String,
    pub claude_runs: u32,
    pub burst_runs: u32,
    pub stuck_runs: u32,
    pub host: Option<String>,
}

impl Opts {
    pub fn parse(args: &[String]) -> Result<Opts, String> {
        let mut o = Opts {
            head: String::new(),
            bin: PathBuf::new(),
            fixture: PathBuf::new(),
            out: PathBuf::new(),
            baseline_bin: None,
            claude: None,
            claude_signer: "Q6L2SF6YDW".into(),
            claude_runs: 10,
            burst_runs: 10,
            stuck_runs: 3,
            host: None,
        };
        let mut it = args.iter();
        while let Some(a) = it.next() {
            let mut val = || {
                it.next()
                    .cloned()
                    .ok_or_else(|| format!("{a} needs a value"))
            };
            let num = |v: String| {
                v.parse::<u32>()
                    .map_err(|_| format!("{a}: not a number: {v}"))
            };
            match a.as_str() {
                "--head" => o.head = val()?,
                "--bin" => o.bin = val()?.into(),
                "--fixture" => o.fixture = val()?.into(),
                "--out" => o.out = val()?.into(),
                "--baseline-bin" => o.baseline_bin = Some(val()?.into()),
                "--claude" => o.claude = Some(val()?.into()),
                "--claude-signer" => o.claude_signer = val()?,
                "--claude-runs" => o.claude_runs = num(val()?)?,
                "--burst-runs" => o.burst_runs = num(val()?)?,
                "--stuck-runs" => o.stuck_runs = num(val()?)?,
                "--host" => o.host = Some(val()?),
                other => return Err(format!("unknown option {other}")),
            }
        }
        if o.head.len() != 40 || !o.head.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("--head must be the 40-hex commit the aibd was built from".into());
        }
        for (name, p) in [
            ("--bin", &o.bin),
            ("--fixture", &o.fixture),
            ("--out", &o.out),
        ] {
            if p.as_os_str().is_empty() {
                return Err(format!("{name} is required"));
            }
        }
        for (name, p) in [
            ("--bin", Some(&o.bin)),
            ("--fixture", Some(&o.fixture)),
            ("--claude", o.claude.as_ref()),
        ] {
            if let Some(p) = p {
                if !p.is_absolute() {
                    return Err(format!(
                        "{name} must be an absolute path (it goes into the aibd policy)"
                    ));
                }
            }
        }
        if o.burst_runs == 0 || o.stuck_runs == 0 {
            return Err("--burst-runs and --stuck-runs must be at least 1".into());
        }
        Ok(o)
    }
}

pub fn run(o: &Opts) -> i32 {
    match run_inner(o) {
        Ok(pass) => i32::from(!pass),
        Err(e) => {
            eprintln!("aib-verify g1: {e}");
            3
        }
    }
}

fn run_inner(o: &Opts) -> Result<bool, String> {
    std::fs::create_dir_all(&o.out).map_err(|e| format!("{}: {e}", o.out.display()))?;
    let host = o.host.clone().unwrap_or_else(hostname);
    let mut rows = Vec::new();
    if let Some(base) = &o.baseline_bin {
        rows.extend(slot_group(
            o,
            base,
            Group::BaselineBurst,
            burst_argv(o),
            o.burst_runs.min(3),
        )?);
        rows.extend(stuck_group(
            o,
            base,
            Group::BaselineStuck,
            o.stuck_runs.min(2),
        )?);
    }
    if let Some(claude) = &o.claude {
        rows.extend(slot_group(
            o,
            &o.bin,
            Group::Claude,
            claude_argv(claude),
            o.claude_runs,
        )?);
    }
    rows.extend(slot_group(
        o,
        &o.bin,
        Group::Burst,
        burst_argv(o),
        o.burst_runs,
    )?);
    rows.extend(stuck_group(o, &o.bin, Group::Stuck, o.stuck_runs)?);
    let s = judge::summarize(&rows, o.claude.is_some(), o.baseline_bin.is_some());
    let line = judge::first_line(&s, &o.head, &host, &rows);
    let mut md = format!("{line}\n");
    if !s.why.is_empty() {
        md.push_str(&format!("原因: {}\n", s.why.join(";")));
    }
    md.push_str(&format!(
        "\n# G1 驗收(aib-verify g1)\n\naibd `{}`,fixture `{}`{}\n\n{}",
        o.bin.display(),
        o.fixture.display(),
        o.baseline_bin
            .as_ref()
            .map(|b| format!(",壞頭對照 `{}`", b.display()))
            .unwrap_or_default(),
        judge::table(&rows)
    ));
    let report = o.out.join("VERIFY-g1.md");
    std::fs::write(&report, md).map_err(|e| format!("{}: {e}", report.display()))?;
    let json_rows: Vec<Value> = rows.iter().map(row_json).collect();
    let rows_file = o.out.join("rows.json");
    std::fs::write(&rows_file, Value::Array(json_rows).to_string())
        .map_err(|e| format!("{}: {e}", rows_file.display()))?;
    println!("{line}");
    println!("{}", report.display());
    Ok(s.pass)
}

fn policy(o: &Opts) -> Value {
    let mut programs = vec![json!({"name": "fixture", "path": o.fixture})];
    if let Some(c) = &o.claude {
        programs.push(json!({"name": "claude", "path": c, "signer": o.claude_signer}));
    }
    json!({ "programs": programs, "env": [] })
}

fn burst_argv(o: &Opts) -> Vec<String> {
    vec![o.fixture.display().to_string(), "setuid-ps-burst".into()]
}

fn claude_argv(claude: &Path) -> Vec<String> {
    [
        "-p",
        "--setting-sources=",
        "--strict-mcp-config",
        "--model",
        "haiku",
        "--output-format",
        "stream-json",
        "--verbose",
        "--",
        "reply OK",
    ]
    .iter()
    .fold(vec![claude.display().to_string()], |mut v, a| {
        v.push((*a).to_string());
        v
    })
}

fn group_daemon(o: &Opts, bin: &Path, g: Group) -> Result<(Daemon, String), String> {
    let d = Daemon::start(bin, &o.out.join(g.name()), &policy(o), 1)?;
    eprintln!(
        "{}: aibd {} (log {})",
        g.name(),
        bin.display(),
        d.log.display()
    );
    let seat = d.seat(&format!("aib-verify-{}", g.name()))?;
    Ok((d, seat))
}

fn slot_group(
    o: &Opts,
    bin: &Path,
    g: Group,
    argv: Vec<String>,
    n: u32,
) -> Result<Vec<Row>, String> {
    let (d, seat) = group_daemon(o, bin, g)?;
    let mut rows = Vec::new();
    for i in 1..=n {
        let mut r = Row::new(g, i);
        slot_run(&d, &seat, &argv, &mut r);
        r.bad = r.bad.take().or_else(|| judge::judge_slot(&r));
        eprintln!("{} #{i}: {}", g.name(), r.bad.as_deref().unwrap_or("過"));
        rows.push(r);
    }
    Ok(rows)
}

/// Start, wait for the end, then wait for the slot. Fills `r`; an API failure goes to `r.bad`.
fn slot_run(d: &Daemon, seat: &str, argv: &[String], r: &mut Row) {
    let (c, v) = match d.start_session(seat, argv) {
        Ok(x) => x,
        Err(e) => {
            r.bad = Some(e);
            return;
        }
    };
    r.start = c;
    if c != 201 {
        r.code = v["code"].as_str().unwrap_or_default().to_string();
        return;
    }
    let Some(id) = v["id"].as_str().map(str::to_string) else {
        r.bad = Some(format!("201 without an id: {v}"));
        return;
    };
    match wait_end(d, &id, RUN_WAIT).and_then(|_| wait_slot(d, &id, SLOT_WAIT)) {
        Ok((last, secs)) => {
            r.fill(&last);
            r.release_secs = secs;
        }
        Err(e) => r.bad = Some(e),
    }
}

fn stuck_group(o: &Opts, bin: &Path, g: Group, n: u32) -> Result<Vec<Row>, String> {
    let (d, seat) = group_daemon(o, bin, g)?;
    let dir = o.out.join(g.name());
    let mut rows = Vec::new();
    for i in 1..=n {
        let mut r = Row::new(g, i);
        stuck_run(o, &d, &seat, &dir.join(format!("stuck-{i}.pid")), &mut r);
        eprintln!("{} #{i}: {}", g.name(), r.bad.as_deref().unwrap_or("過"));
        rows.push(r);
    }
    Ok(rows)
}

fn stuck_run(o: &Opts, d: &Daemon, seat: &str, pidfile: &Path, r: &mut Row) {
    let _ = std::fs::remove_file(pidfile);
    let argv = vec![
        o.fixture.display().to_string(),
        "setuid-ps-stuck".to_string(),
        pidfile.display().to_string(),
    ];
    let (c, v) = match d.start_session(seat, &argv) {
        Ok(x) => x,
        Err(e) => {
            r.bad = Some(e);
            return;
        }
    };
    r.start = c;
    if c != 201 {
        r.code = v["code"].as_str().unwrap_or_default().to_string();
        r.bad = judge::judge_slot(r);
        return;
    }
    let Some(id) = v["id"].as_str().map(str::to_string) else {
        r.bad = Some(format!("201 without an id: {v}"));
        return;
    };
    let Some(ps) = read_pid(pidfile, Duration::from_secs(10)) else {
        r.bad = Some("the fixture wrote no ps pid in 10 s".into());
        let _ = d.stop_session(&id);
        return;
    };
    std::thread::sleep(Duration::from_secs(1));
    if !alive(ps) {
        r.bad = Some(format!(
            "fixture did not hold: ps {ps} already gone before the stop"
        ));
        let _ = d.stop_session(&id);
        return;
    }
    if let Err(e) = d.stop_session(&id) {
        r.bad = Some(format!("DELETE: {e}"));
    }
    let last = match wait_end(d, &id, Duration::from_secs(60)) {
        Ok(v) => v,
        Err(e) => {
            r.bad = Some(e);
            kill_and_check(ps, r);
            return;
        }
    };
    r.fill(&last);
    let alive_after = alive(ps);
    let (bad, branch) = judge::judge_stuck(alive_after, &r.reason, &last["stop"]["signals"]);
    r.branch = Some(branch);
    if r.bad.is_none() {
        r.bad = bad;
    }
    if let judge::Branch::Gone { kill_seen: false } = branch {
        r.add_note("ps 已不在,但 stop 紀錄沒有帶目標的 SIGKILL");
    }
    // my own clean-up: the stuck ps only ends with SIGKILL
    if alive_after {
        kill_and_check(ps, r);
        r.add_note(&format!("ps {ps} 是我收的(KILL)"));
    }
    match wait_slot(d, &id, SLOT_WAIT) {
        Ok((v, secs)) => {
            r.holds_slot = v["holds_slot"].as_bool();
            r.release_secs = secs;
            if secs.is_none() {
                r.add_note("收掉 ps 後名額 45 秒內沒回來");
            }
        }
        Err(e) => r.add_note(&e),
    }
}

fn kill_and_check(ps: u32, r: &mut Row) {
    if !alive(ps) {
        return;
    }
    let _ = signal(ps, "KILL");
    let end = Instant::now() + Duration::from_secs(5);
    while alive(ps) && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(100));
    }
    if alive(ps) {
        r.add_note(&format!("ps {ps} 收不掉,請手動處理"));
    }
}

fn read_pid(f: &Path, within: Duration) -> Option<u32> {
    let end = Instant::now() + within;
    loop {
        if let Some(p) = std::fs::read_to_string(f)
            .ok()
            .and_then(|s| s.trim().parse().ok())
        {
            return Some(p);
        }
        if Instant::now() > end {
            return None;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Poll until the session is no longer running (or `within` ran out: then the last view).
fn wait_end(d: &Daemon, id: &str, within: Duration) -> Result<Value, String> {
    let end = Instant::now() + within;
    loop {
        let v = d.session(id)?;
        if v["state"] != "running" || Instant::now() > end {
            return Ok(v);
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// Poll until the slot is back. `None` seconds = it was not back within `within`.
fn wait_slot(d: &Daemon, id: &str, within: Duration) -> Result<(Value, Option<f64>), String> {
    let t0 = Instant::now();
    loop {
        let v = d.session(id)?;
        if v["state"] != "running" && v["holds_slot"] == false {
            return Ok((v, Some(t0.elapsed().as_secs_f64())));
        }
        if t0.elapsed() > within {
            return Ok((v, None));
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

fn row_json(r: &Row) -> Value {
    json!({
        "group": r.group.name(), "i": r.i, "start": r.start, "code": r.code,
        "state": r.state, "holds_slot": r.holds_slot, "release_secs": r.release_secs,
        "reason": r.reason, "survivors": r.survivors, "tree_size": r.tree_size,
        "orphan_candidates_extra": r.orphan_extra, "candidates_unreadable": r.unreadable,
        "branch": r.branch.map(|b| format!("{b:?}")), "bad": r.bad, "note": r.note,
    })
}

pub(crate) fn hostname() -> String {
    std::process::Command::new("/bin/hostname")
        .arg("-s")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|h| !h.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    /// An absolute path on this platform: `/a/fx` here, `C:/a/fx` on Windows
    /// (where a path without a drive is not absolute).
    fn abs(p: &str) -> String {
        if cfg!(windows) {
            format!("C:{p}")
        } else {
            p.to_owned()
        }
    }

    /// Split an argument line; tokens that start with `/` become [`abs`].
    fn args(s: &str) -> Vec<String> {
        s.split_whitespace()
            .map(|t| {
                if t.starts_with('/') {
                    abs(t)
                } else {
                    t.to_owned()
                }
            })
            .collect()
    }

    #[test]
    fn options_need_a_full_head_and_absolute_paths() {
        let h = "0123456789abcdef0123456789abcdef01234567";
        let ok = Opts::parse(&args(&format!(
            "--head {h} --bin /a/aibd --fixture /a/fx --out o --claude-runs 2"
        )))
        .unwrap();
        assert_eq!(ok.claude_runs, 2);
        assert_eq!(ok.burst_runs, 10);
        assert_eq!(ok.stuck_runs, 3);
        assert_eq!(ok.claude, None);
        assert!(Opts::parse(&args("--head abc --bin /a --fixture /b --out o")).is_err());
        assert!(Opts::parse(&args(&format!(
            "--head {h} --bin aibd --fixture /b --out o"
        )))
        .is_err());
        assert!(Opts::parse(&args(&format!("--head {h} --fixture /b --out o"))).is_err());
        assert!(Opts::parse(&args(&format!(
            "--head {h} --bin /a --fixture /b --out o --burst-runs 0"
        )))
        .is_err());
        assert!(Opts::parse(&args(&format!(
            "--head {h} --bin /a --fixture /b --out o --bogus 1"
        )))
        .is_err());
        assert!(Opts::parse(&args(&format!(
            "--head {h} --bin /a --fixture /b --out o --claude-runs"
        )))
        .is_err());
    }

    #[test]
    fn the_policy_lists_the_fixture_and_claude_only_when_asked() {
        let h = "0123456789abcdef0123456789abcdef01234567";
        let o = Opts::parse(&args(&format!(
            "--head {h} --bin /a/aibd --fixture /a/fx --out o"
        )))
        .unwrap();
        assert_eq!(
            policy(&o),
            json!({"programs": [{"name": "fixture", "path": abs("/a/fx")}], "env": []})
        );
        let o = Opts::parse(&args(&format!(
            "--head {h} --bin /a/aibd --fixture /a/fx --out o --claude /c/claude"
        )))
        .unwrap();
        assert_eq!(
            policy(&o)["programs"][1],
            json!({"name": "claude", "path": abs("/c/claude"), "signer": "Q6L2SF6YDW"})
        );
        assert_eq!(
            claude_argv(Path::new("/c/claude"))[..3],
            ["/c/claude", "-p", "--setting-sources="]
        );
        assert_eq!(burst_argv(&o), [abs("/a/fx"), "setuid-ps-burst".to_owned()]);
    }
}
