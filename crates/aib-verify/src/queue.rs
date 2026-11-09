//! `aib-verify queue`: the session queue acceptance (a63ab 11-10 00:06, a9136's queue,
//! ADR 0005 §5) on one aibd with one slot. Five checks, in order, each one row:
//!
//! - Q1 `202`: with the slot taken, a POST is 202 `pending`, pid 0;
//! - Q2 auto-start: when the holder ends, the waiting one starts by itself;
//! - Q3 cancel: DELETE of a waiting one is `cancelled` and it never started;
//! - Q4 full: with [`MAX_QUEUE`] waiting, the next POST is 429 `session_queue_full`;
//! - Q5 restart: waiting ones at shutdown are `interrupted` after a restart.
//!
//! The holder is `aib-fixture-tree smoke-tree <marker> <stop>`: it runs until the
//! stop file exists, on every platform. rc 0 通過 / 1 不通過 / 2 usage / 3 could not run.

use crate::daemon::Daemon;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// aibd's queue length (crates/aibd/src/sessions.rs MAX_QUEUE).
pub const MAX_QUEUE: usize = 64;
const SLOT_WAIT: Duration = Duration::from_secs(45);

#[derive(Debug, PartialEq)]
pub struct Opts {
    pub head: String,
    pub bin: PathBuf,
    pub fixture: PathBuf,
    pub out: PathBuf,
    pub host: Option<String>,
}

impl Opts {
    pub fn parse(args: &[String]) -> Result<Opts, String> {
        let mut o = Opts {
            head: String::new(),
            bin: PathBuf::new(),
            fixture: PathBuf::new(),
            out: PathBuf::new(),
            host: None,
        };
        let mut it = args.iter();
        while let Some(a) = it.next() {
            let mut val = || {
                it.next()
                    .cloned()
                    .ok_or_else(|| format!("{a} needs a value"))
            };
            match a.as_str() {
                "--head" => o.head = val()?,
                "--bin" => o.bin = val()?.into(),
                "--fixture" => o.fixture = val()?.into(),
                "--out" => o.out = val()?.into(),
                "--host" => o.host = Some(val()?),
                other => return Err(format!("unknown option {other}")),
            }
        }
        if o.head.len() != 40 || !o.head.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("--head must be the 40-hex commit the aibd was built from".into());
        }
        if o.out.as_os_str().is_empty() {
            return Err("--out is required".into());
        }
        for (name, p) in [("--bin", &o.bin), ("--fixture", &o.fixture)] {
            if !p.is_absolute() {
                return Err(format!("{name} must be an absolute path"));
            }
        }
        Ok(o)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Check {
    pub name: &'static str,
    pub want: String,
    pub got: String,
    pub pass: bool,
}

fn check(name: &'static str, want: &str, got: String, pass: bool) -> Check {
    Check {
        name,
        want: want.to_string(),
        got,
        pass,
    }
}

pub fn first_line(checks: &[Check], head: &str, host: &str) -> String {
    let pass = !checks.is_empty() && checks.iter().all(|c| c.pass);
    let not_run: Vec<&str> = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        .into_iter()
        .filter(|q| !checks.iter().any(|c| c.name == *q))
        .collect();
    format!(
        "驗收: {} 頭: {head} 機: {host} 樣本: queue {} 沒驗: {}",
        if pass && not_run.is_empty() {
            "通過"
        } else {
            "不通過"
        },
        checks.len(),
        if not_run.is_empty() {
            "無".to_string()
        } else {
            not_run.join("、")
        }
    )
}

pub fn table(checks: &[Check]) -> String {
    let mut out = String::from("| 項 | 要 | 實際 | 判定 |\n|---|---|---|---|\n");
    for c in checks {
        out.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            c.name,
            c.want.replace('|', "/"),
            c.got.replace('|', "/"),
            if c.pass { "過" } else { "不過" }
        ));
    }
    out
}

pub fn run(o: &Opts) -> i32 {
    std::fs::create_dir_all(&o.out).ok();
    let mut checks = Vec::new();
    let res = run_checks(o, &mut checks);
    let host = o.host.clone().unwrap_or_else(crate::g1::hostname);
    let line = first_line(&checks, &o.head, &host);
    let mut md = format!("{line}\n");
    if let Err(e) = &res {
        md.push_str(&format!("中斷: {e}\n"));
    }
    md.push_str(&format!(
        "\n# 排隊驗收(aib-verify queue)\n\naibd `{}`,fixture `{}`,上限 1,排隊上限 {MAX_QUEUE}\n\n{}",
        o.bin.display(),
        o.fixture.display(),
        table(&checks)
    ));
    let report = o.out.join("VERIFY-queue.md");
    if let Err(e) = std::fs::write(&report, md) {
        eprintln!("aib-verify queue: {}: {e}", report.display());
        return 3;
    }
    println!("{line}");
    println!("{}", report.display());
    match res {
        Err(e) if checks.is_empty() => {
            eprintln!("aib-verify queue: {e}");
            3
        }
        _ => i32::from(!(checks.len() == 5 && checks.iter().all(|c| c.pass))),
    }
}

fn policy(o: &Opts) -> Value {
    json!({"programs": [{"name": "fixture", "path": o.fixture}], "env": []})
}

/// A holder: runs until `<dir>/stop-<n>` exists.
fn holder(o: &Opts, dir: &Path, n: &str) -> (Vec<String>, PathBuf) {
    let stop = dir.join(format!("stop-{n}"));
    let _ = std::fs::remove_file(&stop);
    let argv = vec![
        o.fixture.display().to_string(),
        "smoke-tree".to_string(),
        dir.join(format!("marker-{n}")).display().to_string(),
        stop.display().to_string(),
    ];
    (argv, stop)
}

fn id_of(v: &Value) -> Result<String, String> {
    v["id"]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| format!("no id in {v}"))
}

fn short(v: &Value) -> String {
    format!(
        "state={} pid={} holds_slot={}",
        v["state"].as_str().unwrap_or("-"),
        v["pid"],
        v["holds_slot"]
    )
}

/// Poll until `pred` holds (Some(view)) or `within` runs out (Err with the last view).
fn wait_for(
    d: &Daemon,
    id: &str,
    within: Duration,
    pred: impl Fn(&Value) -> bool,
) -> Result<Value, Value> {
    let end = Instant::now() + within;
    let mut last = Value::Null;
    while Instant::now() < end {
        if let Ok(v) = d.session(id) {
            if pred(&v) {
                return Ok(v);
            }
            last = v;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    Err(last)
}

fn run_checks(o: &Opts, checks: &mut Vec<Check>) -> Result<(), String> {
    let dir = o.out.join("queue");
    let pol = policy(o);
    let d = Daemon::start(&o.bin, &dir, &pol, 1)?;
    eprintln!("queue: aibd {} (log {})", o.bin.display(), d.log.display());
    let seat = d.seat("aib-verify-queue")?;

    // Q1: the slot is taken ⇒ 202 pending, pid 0
    let (argv_a, stop_a) = holder(o, &dir, "a");
    let (c, a) = d.start_session(&seat, &argv_a)?;
    if c != 201 {
        return Err(format!("the holder did not start: {c} {a}"));
    }
    let a = id_of(&a)?;
    let (argv_b, stop_b) = holder(o, &dir, "b");
    let (c, b) = d.start_session(&seat, &argv_b)?;
    let q1 = c == 202 && b["state"] == "pending" && b["pid"] == 0;
    checks.push(check(
        "Q1",
        "202 state=pending pid=0",
        format!("{c} {}", short(&b)),
        q1,
    ));
    let b = id_of(&b)?;

    // Q2: the holder ends ⇒ the waiting one starts by itself (pid > 0)
    std::fs::write(&stop_a, b"").map_err(|e| format!("stop file: {e}"))?;
    let t0 = Instant::now();
    let started = wait_for(&d, &b, Duration::from_secs(10) + SLOT_WAIT, |v| {
        v["state"] == "running" && v["pid"].as_u64().is_some_and(|p| p > 0)
    });
    let a_end = d.session(&a).map(|v| short(&v)).unwrap_or_default();
    match &started {
        Ok(v) => checks.push(check(
            "Q2",
            "前一個結束後,排隊的自己開(running、pid>0)",
            format!(
                "{:.1} 秒後 {};前一個 {a_end}",
                t0.elapsed().as_secs_f64(),
                short(v)
            ),
            true,
        )),
        Err(last) => checks.push(check(
            "Q2",
            "前一個結束後,排隊的自己開(running、pid>0)",
            format!(
                "{} 秒內沒開:{};前一個 {a_end}",
                10 + SLOT_WAIT.as_secs(),
                short(last)
            ),
            false,
        )),
    }

    // Q3: with b holding the slot, c waits; DELETE c ⇒ cancelled, never started
    let (argv_c, _) = holder(o, &dir, "c");
    let (c, cv) = d.start_session(&seat, &argv_c)?;
    if c == 202 {
        let cid = id_of(&cv)?;
        let (dc, _) = d.stop_session(&cid)?;
        let after = d.session(&cid)?;
        let ok = (200..300).contains(&dc) && after["state"] == "cancelled" && after["pid"] == 0;
        checks.push(check(
            "Q3",
            "DELETE 排隊中的 ⇒ cancelled、pid 0",
            format!("DELETE {dc} 後 {}", short(&after)),
            ok,
        ));
    } else {
        checks.push(check(
            "Q3",
            "先要 202 才能測取消",
            format!("POST {c} {}", short(&cv)),
            false,
        ));
    }

    // Q4: MAX_QUEUE waiting ⇒ the next is 429 session_queue_full
    let mut waiting = Vec::new();
    let mut odd = None;
    for i in 0..MAX_QUEUE {
        let (argv, _) = holder(o, &dir, &format!("q{i}"));
        let (c, v) = d.start_session(&seat, &argv)?;
        if c != 202 {
            odd = Some(format!("第 {} 個回 {c} {}", i + 1, v["code"]));
            break;
        }
        waiting.push(id_of(&v)?);
    }
    let (argv, _) = holder(o, &dir, "over");
    let (c, v) = d.start_session(&seat, &argv)?;
    let full = odd.is_none() && c == 429 && v["code"] == "session_queue_full";
    checks.push(check(
        "Q4",
        "排滿 64 個後,下一個 429 session_queue_full",
        odd.unwrap_or_else(|| format!("{} 個 202 後:{c} {}", waiting.len(), v["code"])),
        full,
    ));

    // Q5: shut down with ones waiting ⇒ after a restart they are interrupted
    let probe: Vec<String> = waiting.iter().take(3).cloned().collect();
    drop(d);
    let _ = std::fs::write(&stop_b, b"");
    let d2 = Daemon::restart(&o.bin, &dir, &pol, 1, "aibd-restart.log")?;
    let mut states = Vec::new();
    for id in &probe {
        states.push(
            d2.session(id)
                .map(|v| v["state"].as_str().unwrap_or("-").to_string())
                .unwrap_or_else(|e| e),
        );
    }
    let q5 = !probe.is_empty() && states.iter().all(|s| s == "interrupted");
    checks.push(check(
        "Q5",
        "重啟後,關機時還在排的都是 interrupted",
        format!("抽 {} 個:{}", probe.len(), states.join(", ")),
        q5,
    ));
    drop(d2);
    Ok(())
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    fn c(name: &'static str, pass: bool) -> Check {
        check(name, "w", "g".into(), pass)
    }

    #[test]
    fn all_five_must_run_and_pass() {
        let h = "f".repeat(40);
        let all: Vec<Check> = ["Q1", "Q2", "Q3", "Q4", "Q5"]
            .into_iter()
            .map(|n| c(n, true))
            .collect();
        assert_eq!(
            first_line(&all, &h, "m"),
            format!("驗收: 通過 頭: {h} 機: m 樣本: queue 5 沒驗: 無")
        );
        let mut one_bad = all.clone();
        one_bad[2].pass = false;
        assert!(first_line(&one_bad, &h, "m").starts_with("驗收: 不通過 "));
        // stopped after Q2: the rest are listed as not checked, and it is not a pass
        let l = first_line(&all[..2], &h, "m");
        assert_eq!(
            l,
            format!("驗收: 不通過 頭: {h} 機: m 樣本: queue 2 沒驗: Q3、Q4、Q5")
        );
        assert!(first_line(&[], &h, "m").starts_with("驗收: 不通過 "));
    }

    #[test]
    fn the_table_escapes_pipes() {
        let t = table(&[check("Q1", "a|b", "c".into(), false)]);
        assert!(t.contains("| Q1 | a/b | c | 不過 |"), "{t}");
    }

    #[test]
    fn options() {
        let h = "0123456789abcdef0123456789abcdef01234567";
        let a = |s: String| s.split_whitespace().map(str::to_string).collect::<Vec<_>>();
        assert!(Opts::parse(&a(format!("--head {h} --bin /a --fixture /b --out o"))).is_ok());
        assert!(Opts::parse(&a(format!("--head {h} --bin a --fixture /b --out o"))).is_err());
        assert!(Opts::parse(&a(format!("--head {h} --bin /a --fixture /b"))).is_err());
        assert!(Opts::parse(&a("--head 12 --bin /a --fixture /b --out o".into())).is_err());
    }
}
