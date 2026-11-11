// SPDX-License-Identifier: Apache-2.0
//! D7: is a run short, and light enough to go ahead of a long one?
//! (DECISION-test-classify-a63ab-1110 v1.1, items 1–4; Andy 11-10 00:1x "要能先判斷測試是長的、
//! 還是短的,短的不影嚮效能的先跑".)
//!
//! A pure function of the steps, the host and the D5 history. It only orders work: a short
//! run still takes the lease and runs the same commands through the same gates.
//!
//! * **Fingerprint**: host + each step as words, with commit ids, `--rev` values and temp
//!   paths taken out. A step with shell syntax (`;`, `|`, `&&`, `$`, redirections, globs…) has
//!   no fingerprint: what it runs cannot be told from the words, so it is unknown.
//! * **Estimate**: only complete runs that passed (`all_passed`), the sum of their steps'
//!   seconds (lease waiting is not in it). 5 or more: p90 (nearest rank); fewer: the max.
//! * **Class**: short ≤ 10 min, medium ≤ 20 min, long beyond. No history ⇒ unknown, which is
//!   scheduled as long; a content table still gives a first estimate.
//! * **Always long**: a whole suite, Chrome / CDP / e2e / browser / puppeteer, a real AI CLI,
//!   ignored tests. History cannot make these short.
//! * **Light** (measured only): every passing run's peak CPU ≤ 4 cores and max RSS ≤ 1 GiB; a
//!   number missing in any of them ⇒ not light. Max RSS is `/usr/bin/time -l`'s: the largest
//!   **single process** of a step, not the sum of its tree (2240e #609 N1).
//! * **Going ahead** also needs 3 passing runs and no Chrome process seen during any run of
//!   these steps (`chrome_seen`, any process on the machine; 2240e #609 L1 / N2).
//! * **Overrun**: a run of this fingerprint in the last 7 days that was stopped as
//!   `short_overrun`, or took more than 15 min, ⇒ it may not go ahead.
//! * **Config** can only move a fingerprint to long, never to short.

use serde_json::{json, Value};

use crate::run::mask_secrets;

/// v1.1: short is p90 ≤ 10 min.
pub const SHORT_SECS: i64 = 600;
/// v1: medium up to 20 min.
pub const MEDIUM_SECS: i64 = 1200;
/// v1.1: light is a peak of at most 4 cores …
pub const LIGHT_CPU_PCT: i64 = 400;
/// … and at most 1 GiB of memory (v1): the largest single process's max RSS, not the tree's sum.
pub const LIGHT_RSS_BYTES: i64 = 1 << 30;
/// v1: a short run past 15 min is not short any more.
pub const OVERRUN_SECS: i64 = 900;
/// v1.1: an overrun keeps the fingerprint from going ahead for 7 days.
pub const BAN_SECS: i64 = 7 * 24 * 3600;
/// v1: 5 or more samples use p90, fewer use the max.
pub const P90_MIN_SAMPLES: usize = 5;
/// 2240e #609 N2: going ahead needs at least this many passing runs.
pub const GO_AHEAD_MIN_SAMPLES: usize = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    Short,
    Medium,
    Long,
    /// No usable history (or no fingerprint): scheduled as long.
    Unknown,
}

impl Class {
    pub fn as_str(self) -> &'static str {
        match self {
            Class::Short => "short",
            Class::Medium => "medium",
            Class::Long => "long",
            Class::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Classification {
    pub class: Class,
    /// Measured light (see the module notes); `false` when not measured.
    pub light: bool,
    /// Short, light, not always-long and not banned for an overrun: may go ahead of a long run.
    pub may_go_ahead: bool,
    /// Seconds expected (history, else the content table).
    pub est_secs: i64,
    /// Passing runs the estimate came from.
    pub samples: usize,
    pub fingerprint: Option<String>,
    /// Why, in a few words (one per rule that decided).
    pub reasons: Vec<String>,
}

impl Classification {
    pub fn to_json(&self) -> Value {
        json!({
            "class": self.class.as_str(),
            "light": self.light,
            "may_go_ahead": self.may_go_ahead,
            "est_secs": self.est_secs,
            "samples": self.samples,
            "fingerprint": self.fingerprint,
            "reasons": self.reasons,
        })
    }
}

/// Split one step into words like a POSIX shell would for a plain command. `None` when the
/// step uses anything beyond words and quotes (then the words do not say what runs).
pub fn words(step: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut have = false;
    let mut it = step.chars().peekable();
    while let Some(c) = it.next() {
        match c {
            ' ' | '\t' => {
                if have {
                    out.push(std::mem::take(&mut cur));
                    have = false;
                }
            }
            '\'' => {
                have = true;
                loop {
                    match it.next() {
                        Some('\'') => break,
                        Some(x) => cur.push(x),
                        None => return None,
                    }
                }
            }
            '"' => {
                have = true;
                loop {
                    match it.next() {
                        Some('"') => break,
                        Some('$' | '`') | None => return None,
                        Some('\\') => match it.next() {
                            Some(x @ ('"' | '\\')) => cur.push(x),
                            Some(x) => {
                                cur.push('\\');
                                cur.push(x);
                            }
                            None => return None,
                        },
                        Some(x) => cur.push(x),
                    }
                }
            }
            '\\' => {
                have = true;
                cur.push(it.next()?);
            }
            ';' | '|' | '&' | '<' | '>' | '$' | '`' | '(' | ')' | '*' | '?' | '[' | '\n' | '#'
            | '{' | '}' | '~' => return None,
            x => {
                have = true;
                cur.push(x);
            }
        }
    }
    if have {
        out.push(cur);
    }
    (!out.is_empty()).then_some(out)
}

fn is_commit_id(w: &str) -> bool {
    (7..=64).contains(&w.len()) && w.bytes().all(|b| b.is_ascii_hexdigit())
}

fn is_temp_path(w: &str) -> bool {
    [
        "/tmp/",
        "/private/tmp/",
        "/var/folders/",
        "/private/var/folders/",
    ]
    .iter()
    .any(|p| w.starts_with(p))
}

/// One step, normalised: masked like the history, commit ids, `--rev` values and temp paths
/// replaced. `None` = shell syntax.
pub fn normalise(step: &str) -> Option<String> {
    let w = words(&mask_secrets(step))?;
    let mut out = Vec::with_capacity(w.len());
    let mut after_rev = false;
    for x in w {
        if after_rev {
            out.push("<rev>".to_string());
            after_rev = false;
        } else if x == "--rev" {
            out.push(x);
            after_rev = true;
        } else if x.starts_with("--rev=") {
            out.push("--rev=<rev>".to_string());
        } else if is_commit_id(&x) {
            out.push("<sha>".to_string());
        } else if is_temp_path(&x) {
            out.push("<tmp>".to_string());
        } else {
            out.push(x);
        }
    }
    Some(out.join(" "))
}

/// Host + the normalised steps; `None` when a step has no fingerprint.
pub fn fingerprint(host: &str, steps: &[String]) -> Option<String> {
    if steps.is_empty() {
        return None;
    }
    let mut parts = Vec::with_capacity(steps.len());
    for s in steps {
        parts.push(normalise(s)?);
    }
    Some(format!("{host} :: {}", parts.join(" ;; ")))
}

/// Why the content alone makes a step long (and heavy), if it does.
fn always_long(w: &[String]) -> Option<&'static str> {
    let lw: Vec<String> = w.iter().map(|x| x.to_ascii_lowercase()).collect();
    let prog = lw
        .iter()
        .position(|x| !x.contains('='))
        .map(|i| &lw[i..])
        .unwrap_or(&[]);
    let base = |x: &str| x.rsplit('/').next().unwrap_or(x).to_string();
    let p0 = prog.first().map(|x| base(x)).unwrap_or_default();
    let p1 = prog.get(1).map(String::as_str).unwrap_or("");
    if p0 == "aib" && p1 == "test" && prog.iter().any(|x| x == "--all") {
        return Some("whole suite (aib test --all)");
    }
    if p0 == "aib" && p1 == "verify" {
        return Some("real CLI (aib verify)");
    }
    if matches!(p0.as_str(), "claude" | "codex" | "agy" | "gemini") {
        return Some("real AI CLI");
    }
    if lw.iter().any(|x| x == "--workspace") {
        return Some("whole workspace");
    }
    if lw
        .iter()
        .any(|x| x == "--ignored" || x == "--include-ignored")
    {
        return Some("ignored tests");
    }
    if lw.iter().any(|x| x.ends_with("tools/test.js")) {
        return Some("whole suite (tools/test.js)");
    }
    if lw.iter().any(|x| {
        // 2240e #609 L1: `*-browser.js`, `require("puppeteer")` style tests open Chrome too
        [
            "chrome",
            "chromium",
            "cdp",
            "e2e",
            "playwright",
            "puppeteer",
            "browser",
        ]
        .iter()
        .any(|k| x.contains(k))
    }) {
        return Some("opens a browser");
    }
    None
}

/// First estimate from the words (4542e's table in the DECISION): seconds.
fn content_estimate(w: &[String]) -> i64 {
    let prog: Vec<&str> = w
        .iter()
        .skip_while(|x| x.contains('='))
        .map(String::as_str)
        .collect();
    let p0 = prog
        .first()
        .map(|x| x.rsplit('/').next().unwrap_or(x))
        .unwrap_or("");
    let sub = prog
        .iter()
        .skip(1)
        .find(|x| !x.starts_with('+') && !x.starts_with('-'))
        .copied()
        .unwrap_or("");
    match (p0, sub) {
        ("cargo", "fmt" | "metadata") => 30,
        ("cargo", "check" | "clippy") => 180,
        ("cargo", "test" | "build" | "nextest") => 720,
        ("python3" | "python", _) => 120,
        _ => crate::run::DEFAULT_TTL as i64,
    }
}

struct Sample {
    secs: i64,
    cpu: Option<i64>,
    rss: Option<i64>,
}

fn steps_of(v: &Value) -> Option<Vec<String>> {
    v["steps"]
        .as_array()?
        .iter()
        .map(|s| s["cmd"].as_str().map(str::to_owned))
        .collect()
}

/// Classify `steps` on `host` from the D5 history (`aib-lease-history/v1` lines; unreadable
/// lines are skipped). `long` holds config entries: a fingerprint containing one is long.
/// `now` is the epoch second the 7-day overrun window ends at.
pub fn classify(
    host: &str,
    steps: &[String],
    history: &str,
    long: &[String],
    now: i64,
) -> Classification {
    let mut reasons = Vec::new();
    let fp = fingerprint(host, steps);
    let parsed: Vec<Vec<String>> = steps.iter().filter_map(|s| words(s)).collect();
    let content_est: i64 = parsed.iter().map(|w| content_estimate(w)).sum();
    let heavy: Vec<&'static str> = parsed.iter().filter_map(|w| always_long(w)).collect();

    let Some(fp_s) = fp.clone() else {
        reasons.push("shell syntax in a step: what runs cannot be told".into());
        return Classification {
            class: Class::Unknown,
            light: false,
            may_go_ahead: false,
            est_secs: crate::run::DEFAULT_TTL as i64,
            samples: 0,
            fingerprint: None,
            reasons,
        };
    };

    let mut samples = Vec::new();
    let mut banned = false;
    let mut chrome_seen = false;
    for v in history
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
    {
        if v["ssh_host"] != host {
            continue;
        }
        let Some(run_steps) = steps_of(&v) else {
            continue;
        };
        if fingerprint(host, &run_steps).as_deref() != Some(fp_s.as_str()) {
            continue;
        }
        let secs: Option<i64> = v["steps"].as_array().and_then(|a| {
            a.iter()
                .map(|s| s["secs"].as_i64().filter(|x| *x >= 0))
                .sum::<Option<i64>>()
        });
        let ended = v["end_epoch"].as_i64().unwrap_or(0);
        let recent = now - ended <= BAN_SECS;
        if recent && (v["outcome"] == "short_overrun" || secs.is_some_and(|s| s > OVERRUN_SECS)) {
            banned = true;
        }
        // 2240e #609 L1: a Chrome process was on the machine during a run of this fingerprint
        // (any outcome, any time): never ahead of anyone
        if v["steps"]
            .as_array()
            .is_some_and(|a| a.iter().any(|s| s["chrome_seen"] == true))
        {
            chrome_seen = true;
        }
        if v["outcome"] != "all_passed" {
            continue;
        }
        let Some(secs) = secs else { continue };
        let max_of = |k: &str| -> Option<i64> {
            v["steps"].as_array().and_then(|a| {
                a.iter()
                    .map(|s| s[k].as_i64().filter(|x| *x >= 0))
                    .try_fold(0i64, |m, x| x.map(|x| m.max(x)))
            })
        };
        samples.push(Sample {
            secs,
            cpu: max_of("peak_cpu_pct"),
            rss: max_of("maxrss_bytes"),
        });
    }

    let n = samples.len();
    let (mut class, est) = if n == 0 {
        reasons.push(format!(
            "no passing run on {host} yet: unknown, first estimate from the commands"
        ));
        (Class::Unknown, content_est)
    } else {
        let mut secs: Vec<i64> = samples.iter().map(|s| s.secs).collect();
        secs.sort_unstable();
        let est = if n >= P90_MIN_SAMPLES {
            let rank = (n * 9).div_ceil(10).max(1);
            reasons.push(format!("p90 of {n} passing runs"));
            secs[rank - 1]
        } else {
            reasons.push(format!(
                "max of {n} passing runs (fewer than {P90_MIN_SAMPLES})"
            ));
            secs[n - 1]
        };
        let c = if est <= SHORT_SECS {
            Class::Short
        } else if est <= MEDIUM_SECS {
            Class::Medium
        } else {
            Class::Long
        };
        (c, est)
    };
    let light = n > 0
        && samples.iter().all(|s| {
            s.cpu.is_some_and(|c| c <= LIGHT_CPU_PCT) && s.rss.is_some_and(|r| r <= LIGHT_RSS_BYTES)
        });
    if n > 0 && !light {
        reasons.push(
            "not measured light (peak CPU > 4 cores, RSS > 1 GiB, or a number missing)".into(),
        );
    }
    if let Some(h) = heavy.first() {
        class = Class::Long;
        reasons.push(format!("always long: {h}"));
    }
    if long
        .iter()
        .any(|e| !e.trim().is_empty() && fp_s.contains(e.trim()))
    {
        class = Class::Long;
        reasons.push("config: long".into());
    }
    if banned {
        reasons.push("ran over 15 min (or short_overrun) in the last 7 days".into());
    }
    if chrome_seen {
        reasons.push("Chrome was running during a run of these steps".into());
    }
    if n < GO_AHEAD_MIN_SAMPLES {
        reasons.push(format!(
            "fewer than {GO_AHEAD_MIN_SAMPLES} passing runs to go ahead on"
        ));
    }
    let may_go_ahead = class == Class::Short
        && light
        && heavy.is_empty()
        && !banned
        && !chrome_seen
        && n >= GO_AHEAD_MIN_SAMPLES;
    Classification {
        class,
        light: light && heavy.is_empty(),
        may_go_ahead,
        est_secs: est,
        samples: n,
        fingerprint: fp,
        reasons,
    }
}

/// `AIB_LEASE_CLASSIFY_LONG`, else `~/.ez2-aib/state/lease/classify-long.txt`: one entry per
/// line, `#` starts a comment. There is no file that makes anything short.
pub fn long_config_path() -> std::path::PathBuf {
    if let Ok(p) = std::env::var("AIB_LEASE_CLASSIFY_LONG") {
        if !p.trim().is_empty() {
            return p.into();
        }
    }
    crate::run::history_path().with_file_name("classify-long.txt")
}

pub fn read_long_config(text: &str) -> Vec<String> {
    text.lines()
        .map(|l| l.split('#').next().unwrap_or("").trim().to_string())
        .filter(|l| !l.is_empty())
        .collect()
}

const USAGE: &str = "usage: aib-lease classify --host HOST [--step CMD]... [-- CMD ARGS...]\n\
prints one JSON line: class (short|medium|long|unknown), light, may_go_ahead, est_secs, samples, \
fingerprint, reasons. exit 0, 2 usage";

/// `aib-lease classify …`; returns the exit code.
pub fn cli(argv: &[String]) -> u8 {
    let mut host = None;
    let mut steps = Vec::new();
    let mut it = argv.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => {
                println!("{USAGE}");
                return 0;
            }
            "--" => {
                let rest: Vec<String> = it.by_ref().cloned().collect();
                if !rest.is_empty() {
                    steps.push(crate::run::join_command(&rest));
                }
                break;
            }
            "--host" | "--step" => {
                let Some(v) = it.next().cloned() else {
                    eprintln!("aib-lease classify: {a} needs a value\n{USAGE}");
                    return 2;
                };
                if a == "--host" {
                    host = Some(v);
                } else {
                    steps.push(v);
                }
            }
            other => {
                eprintln!("aib-lease classify: unknown argument {other:?}\n{USAGE}");
                return 2;
            }
        }
    }
    let (Some(host), false) = (host, steps.is_empty()) else {
        eprintln!("aib-lease classify: --host and at least one step are required\n{USAGE}");
        return 2;
    };
    let history = std::fs::read_to_string(crate::run::history_path()).unwrap_or_default();
    let long = read_long_config(&std::fs::read_to_string(long_config_path()).unwrap_or_default());
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let c = classify(&host, &steps, &history, &long, now);
    println!("{}", c.to_json());
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_240_000;

    fn run_line(
        host: &str,
        cmds: &[&str],
        secs: &[i64],
        outcome: &str,
        cpu: i64,
        rss: i64,
        end: i64,
    ) -> String {
        let steps: Vec<Value> = cmds
            .iter()
            .zip(secs)
            .map(|(c, s)| json!({"cmd": c, "secs": s, "rc": 0, "peak_cpu_pct": cpu, "maxrss_bytes": rss}))
            .collect();
        json!({"schema": "aib-lease-history/v1", "ssh_host": host, "outcome": outcome,
               "end_epoch": end, "steps": steps})
        .to_string()
    }

    fn hist(lines: &[String]) -> String {
        lines.join("\n") + "\n"
    }

    fn s(x: &[&str]) -> Vec<String> {
        x.iter().map(|y| y.to_string()).collect()
    }

    /// Tonight (043dd 23:38 / 00:03 / 00:24): single-crate checks of a minute or less sat
    /// behind 10–60 min leases. With their history they are short and light.
    #[test]
    fn tonights_single_crate_checks_are_short_and_may_go_ahead() {
        let cmd = "cargo test -p aibd --locked --test gate_replay";
        let h = hist(&[
            run_line(
                "mac-b",
                &[cmd],
                &[60],
                "all_passed",
                380,
                500 << 20,
                NOW - 3600,
            ),
            run_line(
                "mac-b",
                &[cmd],
                &[55],
                "all_passed",
                300,
                480 << 20,
                NOW - 1800,
            ),
            run_line(
                "mac-b",
                &[cmd],
                &[70],
                "all_passed",
                390,
                510 << 20,
                NOW - 600,
            ),
        ]);
        let c = classify("mac-b", &s(&[cmd]), &h, &[], NOW);
        assert_eq!(
            (c.class, c.light, c.may_go_ahead, c.est_secs, c.samples),
            (Class::Short, true, true, 70, 3),
            "{c:?}"
        );
    }

    /// ebb36: the footprint regression, 13 runs of 739–866 s ⇒ medium, never ahead.
    #[test]
    fn a_thirteen_minute_regression_is_medium() {
        let cmd = "python3 tests/footprint/test_regress.py";
        let lines: Vec<String> = (0..13)
            .map(|i| {
                run_line(
                    "mac-c",
                    &[cmd],
                    &[739 + i * 10],
                    "all_passed",
                    100,
                    1 << 20,
                    NOW - 100,
                )
            })
            .collect();
        let c = classify("mac-c", &s(&[cmd]), &hist(&lines), &[], NOW);
        assert_eq!(c.class, Class::Medium, "{c:?}");
        assert!(!c.may_go_ahead);
        assert!(c.reasons[0].starts_with("p90 of 13"), "{c:?}");
    }

    #[test]
    fn no_history_is_unknown_with_a_first_estimate_and_never_goes_ahead() {
        let c = classify(
            "mac-b",
            &s(&["cargo fmt --check", "cargo test -p aib-lease"]),
            "",
            &[],
            NOW,
        );
        assert_eq!(
            (c.class, c.may_go_ahead, c.est_secs),
            (Class::Unknown, false, 30 + 720),
            "{c:?}"
        );
    }

    #[test]
    fn a_whole_suite_is_long_whatever_its_history_says() {
        let cmd = "aib test --all";
        let h = hist(&[run_line("mac-b", &[cmd], &[5], "all_passed", 10, 1, NOW)]);
        let c = classify("mac-b", &s(&[cmd]), &h, &[], NOW);
        assert_eq!(
            (c.class, c.may_go_ahead, c.light),
            (Class::Long, false, false),
            "{c:?}"
        );
        for cmd in [
            "node tools/test.js",
            "cargo test -p x -- --include-ignored",
            "python3 test-cdp-shot.py",
            "claude -p hi",
            "cargo test --workspace",
        ] {
            let h = hist(&[run_line("mac-b", &[cmd], &[5], "all_passed", 10, 1, NOW)]);
            assert_eq!(
                classify("mac-b", &s(&[cmd]), &h, &[], NOW).class,
                Class::Long,
                "{cmd}"
            );
        }
    }

    #[test]
    fn shell_syntax_has_no_fingerprint_and_is_unknown() {
        for cmd in [
            "cd x && cargo test",
            "cargo test | tail",
            "echo $HOME",
            "cargo test > o",
            "rm *",
            "a; b",
            "echo \"$(id)\"",
        ] {
            let c = classify("mac-b", &s(&[cmd]), "", &[], NOW);
            assert_eq!(
                (c.class, c.fingerprint.as_deref()),
                (Class::Unknown, None),
                "{cmd}"
            );
        }
    }

    #[test]
    fn commit_ids_rev_values_and_temp_paths_do_not_split_the_history() {
        let then = "aib-verify --rev 2640110c --dir /tmp/x1.noindex 3429a17a0f5570265ff2b236300c8a2f0e4c78b8";
        let now =
            "aib-verify --rev HEAD --dir /tmp/x2.noindex 5f583dd43d6f3f16fba2d398c4549ee057a6e954";
        assert_eq!(fingerprint("h", &s(&[then])), fingerprint("h", &s(&[now])));
        let h = hist(&[run_line("h", &[then], &[20], "all_passed", 100, 1, NOW)]);
        assert_eq!(classify("h", &s(&[now]), &h, &[], NOW).samples, 1);
        // the quoted form `aib-lease run -- …` records is the same command
        assert_eq!(
            fingerprint("h", &s(&["'cargo' 'test' '-p' 'aibd'"])),
            fingerprint("h", &s(&["cargo test -p aibd"]))
        );
    }

    #[test]
    fn config_moves_to_long_and_nothing_moves_to_short() {
        let cmd = "cargo clippy -p aib-lease";
        let h = hist(&[run_line("mac-b", &[cmd], &[30], "all_passed", 100, 1, NOW)]);
        assert_eq!(
            classify("mac-b", &s(&[cmd]), &h, &[], NOW).class,
            Class::Short
        );
        let c = classify(
            "mac-b",
            &s(&[cmd]),
            &h,
            &["clippy -p aib-lease".into()],
            NOW,
        );
        assert_eq!((c.class, c.may_go_ahead), (Class::Long, false), "{c:?}");
        // a config line cannot make a whole suite short
        let c = classify(
            "mac-b",
            &s(&["aib test --all"]),
            "",
            &read_long_config("short: aib test --all\n"),
            NOW,
        );
        assert_eq!(c.class, Class::Long);
        assert_eq!(
            read_long_config("# comment\n  a  # tail\n\n"),
            vec!["a".to_string()]
        );
        assert_eq!(c.to_json()["class"], "long");
    }

    #[test]
    fn an_overrun_in_the_last_seven_days_keeps_it_from_going_ahead() {
        let cmd = "cargo test -p aib-lease";
        let ok = |end| run_line("mac-b", &[cmd], &[40], "all_passed", 100, 1, end);
        let over = |end| run_line("mac-b", &[cmd], &[901], "short_overrun", 100, 1, end);
        let three = || vec![ok(NOW), ok(NOW - 60), ok(NOW - 120)];
        let with = |x: String| {
            let mut v = three();
            v.push(x);
            hist(&v)
        };
        let c = classify("mac-b", &s(&[cmd]), &with(over(NOW - 3 * 86400)), &[], NOW);
        assert_eq!((c.class, c.may_go_ahead), (Class::Short, false), "{c:?}");
        let c = classify("mac-b", &s(&[cmd]), &with(over(NOW - 8 * 86400)), &[], NOW);
        assert!(c.may_go_ahead, "{c:?}");
        // a passing run that took over 15 min bans as well
        let slow = run_line("mac-b", &[cmd], &[1000], "step_failed", 100, 1, NOW);
        assert!(!classify("mac-b", &s(&[cmd]), &with(slow), &[], NOW).may_go_ahead);
        // two passing runs are not enough to go ahead on (2240e #609 N2)
        let c = classify("mac-b", &s(&[cmd]), &hist(&[ok(NOW), ok(NOW)]), &[], NOW);
        assert_eq!(
            (c.class, c.light, c.may_go_ahead),
            (Class::Short, true, false),
            "{c:?}"
        );
    }

    #[test]
    fn fewer_than_five_runs_use_the_max_and_only_passing_runs_on_this_host_count() {
        let cmd = "cargo test -p aibd --lib";
        let p =
            |secs, outcome: &str, host: &str| run_line(host, &[cmd], &[secs], outcome, 100, 1, NOW);
        let h = hist(&[
            p(10, "all_passed", "mac-b"),
            p(10, "all_passed", "mac-b"),
            p(700, "all_passed", "mac-b"),
            p(2, "step_failed", "mac-b"),
            p(5000, "all_passed", "mac-c"),
        ]);
        let c = classify("mac-b", &s(&[cmd]), &h, &[], NOW);
        assert_eq!(
            (c.class, c.est_secs, c.samples),
            (Class::Medium, 700, 3),
            "{c:?}"
        );
        let five: Vec<String> = [10, 20, 30, 40, 2000]
            .iter()
            .map(|x| p(*x, "all_passed", "mac-b"))
            .collect();
        let c = classify("mac-b", &s(&[cmd]), &hist(&five), &[], NOW);
        assert_eq!(c.est_secs, 2000, "p90 nearest rank of 5 is the 5th: {c:?}");
    }

    #[test]
    fn a_missing_or_high_number_is_not_light() {
        let cmd = "cargo check -p aibd";
        let one = |cpu, rss| {
            let ok = run_line("mac-b", &[cmd], &[30], "all_passed", 100, 1, NOW);
            let x = run_line("mac-b", &[cmd], &[30], "all_passed", cpu, rss, NOW);
            hist(&[ok.clone(), ok, x])
        };
        assert!(classify("mac-b", &s(&[cmd]), &one(400, 1 << 30), &[], NOW).may_go_ahead);
        for (cpu, rss) in [(-1, 1), (401, 1), (100, -1), (100, (1 << 30) + 1)] {
            let c = classify("mac-b", &s(&[cmd]), &one(cpu, rss), &[], NOW);
            assert_eq!(
                (c.class, c.light, c.may_go_ahead),
                (Class::Short, false, false),
                "{cpu} {rss}"
            );
        }
    }

    /// 2240e #609 L1: tonight's queues were Chrome tests; a browser test is never ahead.
    #[test]
    fn a_browser_test_or_a_run_that_saw_chrome_never_goes_ahead() {
        for cmd in ["node tests/shot-browser.js", "node tests/puppeteer-shot.js"] {
            let h = hist(&[run_line("mac-b", &[cmd], &[5], "all_passed", 10, 1, NOW)]);
            assert_eq!(
                classify("mac-b", &s(&[cmd]), &h, &[], NOW).class,
                Class::Long,
                "{cmd}"
            );
        }
        let cmd = "python3 tests/test-shot.py";
        let ok = run_line("mac-b", &[cmd], &[30], "all_passed", 100, 1, NOW);
        let mut saw: Value = serde_json::from_str(&ok).unwrap();
        saw["steps"][0]["chrome_seen"] = Value::Bool(true);
        saw["outcome"] = "step_failed".into();
        let clean = hist(&[ok.clone(), ok.clone(), ok.clone()]);
        assert!(classify("mac-b", &s(&[cmd]), &clean, &[], NOW).may_go_ahead);
        let dirty = hist(&[ok.clone(), ok.clone(), ok, saw.to_string()]);
        let c = classify("mac-b", &s(&[cmd]), &dirty, &[], NOW);
        assert_eq!((c.class, c.may_go_ahead), (Class::Short, false), "{c:?}");
    }

    #[test]
    fn words_follow_shell_quoting() {
        assert_eq!(
            words("a 'b c' \"d\\\"e\" f\\ g"),
            Some(s(&["a", "b c", "d\"e", "f g"]))
        );
        assert_eq!(words("  "), None);
        assert_eq!(words("'open"), None);
        assert_eq!(words("A=1 cargo test"), Some(s(&["A=1", "cargo", "test"])));
    }
}
