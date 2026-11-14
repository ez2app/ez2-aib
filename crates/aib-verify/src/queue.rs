//! `aib-verify queue`: the session queue acceptance (a63ab 11-10 00:06, a9136's queue,
//! ADR 0005 §5) on one aibd with one slot. Five checks, in order, each one row:
//!
//! - Q1 `202`: with the slot taken, a POST is 202 `pending`, pid 0, and its GET
//!   shows `queue {position, slots, held_by}`;
//! - Q2 auto-start: when the holder ends, the waiting one starts by itself, and
//!   `aibd ctl watch` on it kept waiting through `pending`;
//! - Q3 cancel: DELETE of a waiting one is `cancelled` and it never started;
//! - Q4 full: with [`MAX_QUEUE`] waiting, the next POST is 429 `session_queue_full`;
//! - Q5 restart: waiting ones at shutdown have `session.interrupted` (from
//!   `pending`, never started) in their events; GET is 404 by design.
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
    let pending = c == 202 && b["state"] == "pending" && b["pid"] == 0;
    let b = id_of(&b)?;
    // a9136 11-10: GET of a pending session says where it stands and who holds the slot
    let view = d.session(&b).unwrap_or_default();
    let qv = &view["queue"];
    let q1 =
        pending && qv["position"] == 1 && qv["slots"] == 1 && qv["held_by"][0]["id"] == a.as_str();
    checks.push(check(
        "Q1",
        "202 state=pending pid=0;GET 帶 queue{position 1, slots 1, held_by[0]=佔著的那個}",
        format!(
            "{c} pending={pending};GET queue position={} slots={} held_by[0]={}(佔著的 {a})",
            qv["position"], qv["slots"], qv["held_by"][0]["id"]
        ),
        q1,
    ));
    // `aibd ctl watch` on the pending one must keep waiting (not end on `pending`)
    let watch_log = dir.join("ctl-watch.log");
    let wout = std::fs::File::create(&watch_log).map_err(|e| format!("ctl watch log: {e}"))?;
    let werr = wout
        .try_clone()
        .map_err(|e| format!("ctl watch log: {e}"))?;
    // Reap: killed and waited for on every way out, including an early `?` below (043dd #604 L1)
    let mut watch = Reap(
        std::process::Command::new(&o.bin)
            .args(["ctl", "watch", &b])
            .env("AIBD_DATA_DIR", dir.join("data"))
            .stdin(std::process::Stdio::null())
            .stdout(wout)
            .stderr(werr)
            .spawn()
            .map_err(|e| format!("ctl watch: {e}"))?,
    );
    std::thread::sleep(Duration::from_secs(2));
    let watch_waiting_pending = matches!(watch.0.try_wait(), Ok(None));

    // Q2: the holder ends ⇒ the waiting one starts by itself (pid > 0)
    std::fs::write(&stop_a, b"").map_err(|e| format!("stop file: {e}"))?;
    let t0 = Instant::now();
    let started = wait_for(&d, &b, Duration::from_secs(10) + SLOT_WAIT, |v| {
        v["state"] == "running" && v["pid"].as_u64().is_some_and(|p| p > 0)
    });
    let a_end = d.session(&a).map(|v| short(&v)).unwrap_or_default();
    let watch_still = matches!(watch.0.try_wait(), Ok(None));
    let watch_ok = watch_waiting_pending && watch_still;
    let watch_note = format!(
        "ctl watch:pending 時{}、開跑後{}(log {})",
        if watch_waiting_pending {
            "還在等"
        } else {
            "已退出"
        },
        if watch_still {
            "還在跟"
        } else {
            "已退出"
        },
        watch_log.display()
    );
    drop(watch);
    match &started {
        Ok(v) => checks.push(check(
            "Q2",
            "前一個結束後,排隊的自己開(running、pid>0);ctl watch 在 pending 時不退出",
            format!(
                "{:.1} 秒後 {};前一個 {a_end};{watch_note}",
                t0.elapsed().as_secs_f64(),
                short(v)
            ),
            watch_ok,
        )),
        Err(last) => checks.push(check(
            "Q2",
            "前一個結束後,排隊的自己開(running、pid>0);ctl watch 在 pending 時不退出",
            format!(
                "{} 秒內沒開:{}(GET queue.held_by={});前一個 {a_end};{watch_note}",
                10 + SLOT_WAIT.as_secs(),
                short(last),
                last["queue"]["held_by"]
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

    // Q4: MAX_QUEUE waiting ⇒ the next is 429 session_queue_full. The bound
    // counts every waiting one: if Q2's session never started (a slot kept by
    // a stop that could not look everywhere, the G1 residual) it is still in
    // the queue and takes one place.
    let already = usize::from(d.session(&b).is_ok_and(|v| v["state"] == "pending"));
    let mut waiting = Vec::new();
    let mut odd = None;
    for i in 0..MAX_QUEUE - already {
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
        odd.unwrap_or_else(|| {
            format!(
                "{} 個 202 後(之前已在排 {already} 個):{c} {}",
                waiting.len(),
                v["code"]
            )
        }),
        full,
    ));

    // Q5: shut down with ones waiting ⇒ after a restart they are interrupted
    let probe: Vec<String> = waiting.iter().take(3).cloned().collect();
    drop(d);
    let _ = std::fs::write(&stop_b, b"");
    let d2 = Daemon::restart(&o.bin, &dir, &pol, 1, "aibd-restart.log")?;
    // An ended session of the last run is not a session of this aibd (GET 404,
    // as aibd's own test a_session_left_pending_by_a_crash_...); what it became
    // is in its events: `session.interrupted` from `pending`, never started.
    let mut seen = Vec::new();
    let mut ok = !probe.is_empty();
    for id in &probe {
        let (get, _) = d2.session_raw(id)?;
        let evs = d2.events_for(id)?;
        let intr = evs.iter().find(|e| e["type"] == "session.interrupted");
        let started = evs.iter().any(|e| e["type"] == "session.started");
        let prev = intr.map_or("-", |e| {
            e["payload"]["previous_state"].as_str().unwrap_or("-")
        });
        let reason = intr.map_or("-", |e| e["payload"]["reason"].as_str().unwrap_or("-"));
        ok &= intr.is_some() && prev == "pending" && !started;
        seen.push(format!(
            "{id}: GET {get}、interrupted={} previous_state={prev} reason={reason} started={started}",
            intr.is_some()
        ));
    }
    checks.push(check(
        "Q5",
        "重啟後,關機時還在排的都有 session.interrupted(previous_state=pending)、沒開過",
        format!("抽 {} 個:{}", probe.len(), seen.join(";")),
        ok,
    ));
    drop(d2);
    Ok(())
}

/// A child that is killed and waited for when this is dropped.
struct Reap(std::process::Child);

impl Drop for Reap {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
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
        // tokens that start with `/` get a drive on Windows, where `/a` is not absolute
        let a = |s: String| {
            s.split_whitespace()
                .map(|t| {
                    if cfg!(windows) && t.starts_with('/') {
                        format!("C:{t}")
                    } else {
                        t.to_owned()
                    }
                })
                .collect::<Vec<_>>()
        };
        assert!(Opts::parse(&a(format!("--head {h} --bin /a --fixture /b --out o"))).is_ok());
        assert!(Opts::parse(&a(format!("--head {h} --bin a --fixture /b --out o"))).is_err());
        assert!(Opts::parse(&a(format!("--head {h} --bin /a --fixture /b"))).is_err());
        assert!(Opts::parse(&a("--head 12 --bin /a --fixture /b --out o".into())).is_err());
    }
}
