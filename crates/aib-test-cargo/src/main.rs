//! `aib-test-rs`: the first runnable `aib test` 2.0, observation mode (519d0 11-17 03:35).
//! Every unit runs for real; the ledger also records, per unit, the earlier run it would
//! have reused and whether the result came out the same. Nothing here is evidence for a
//! push, a review or a merge (519d0 rule 5). It does not replace 1.x `aib test`.
//!
//! usage: aib-test-rs [-p <package>]... [--json] [--why] [--state <dir>]
//! exit: the codes of aib_test_core::exit (0 ok, 1 failed, 3 zero units, 4 nothing ran,
//!       7 engine error); 64 bad arguments.

use aib_test_audit::{engine, Compare, Outcome as AuditOutcome, Verdict};
use aib_test_cargo::units::{self, Workspace};
use aib_test_cargo::{junit_path, list_args, parse_junit, parse_list, run_args};
use aib_test_core::context::{self, Context, CARGO_VARS};
use aib_test_core::exit::{exit_for, Exit, Outcome as RunOutcome};
use aib_test_core::key::{self, FileCache};
use aib_test_core::ledger::{
    self, Audit, AuditCompare, How, Origin, Outcome, Plan, Reuse, Shadow, Unit,
};
use aib_test_core::skip::Use;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "usage: aib-test-rs [-p <package>]... [--json] [--why] [--state <dir>]";

struct Args {
    packages: Vec<String>,
    json: bool,
    why: bool,
    state: Option<PathBuf>,
    help: bool,
}

fn args() -> Result<Args, String> {
    let mut a = Args {
        packages: Vec::new(),
        json: false,
        why: false,
        state: None,
        help: false,
    };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        match x.as_str() {
            "-p" | "--package" => a.packages.push(it.next().ok_or("-p needs a package")?),
            "--json" => a.json = true,
            "--why" => a.why = true,
            "--state" => a.state = Some(PathBuf::from(it.next().ok_or("--state needs a dir")?)),
            "-h" | "--help" => a.help = true,
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    Ok(a)
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn git(root: &Path, a: &[&str]) -> Option<String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(a)
        .output()
        .ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).trim().to_string())
}

fn cargo(root: &Path, a: &[String]) -> Result<std::process::Output, String> {
    Command::new("cargo")
        .current_dir(root)
        .args(a)
        .output()
        .map_err(|e| format!("cargo: {e}"))
}

fn state_root(a: &Args) -> PathBuf {
    if let Some(s) = &a.state {
        return s.clone();
    }
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".ez2-aib").join("state").join("aib-test-rs")
}

/// A plain id for the ledger from the repo folder name.
fn repo_id(root: &Path) -> String {
    let n = root
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let s: String = n
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    if s.is_empty() {
        "repo".into()
    } else {
        s
    }
}

struct Row {
    unit: String,
    key: Option<String>,
    unmeasurable: Option<String>,
    would: Option<Reuse>,
    /// the source's result, to say whether the real run agreed
    source_result: Option<Outcome>,
    /// the source line as a verdict, to check this real run against (1ced7 engine link)
    source: Option<Verdict>,
    failed: Vec<String>,
    audit: Option<Audit>,
    result: Outcome,
    ran: u32,
    ms: u64,
}

fn main() -> ExitCode {
    let a = match args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-test-rs: {e}");
            return ExitCode::from(64);
        }
    };
    if a.help {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    match run(&a) {
        Ok(e) => ExitCode::from(e.code() as u8),
        Err(e) => {
            eprintln!("aib-test-rs: engine error: {e}");
            ExitCode::from(Exit::EngineError.code() as u8)
        }
    }
}

fn run(a: &Args) -> Result<Exit, String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    let root = key::repo_root(&cwd).map_err(|e| format!("{e:?}"))?;
    let meta = cargo(
        &root,
        &["metadata", "--no-deps", "--format-version", "1", "--locked"].map(String::from),
    )?;
    if !meta.status.success() {
        return Err(format!(
            "cargo metadata: {}",
            String::from_utf8_lossy(&meta.stderr).trim()
        ));
    }
    let ws: Workspace = units::parse_metadata(&String::from_utf8_lossy(&meta.stdout))?;
    let chosen: Vec<String> = if a.packages.is_empty() {
        ws.packages.iter().map(|p| p.name.clone()).collect()
    } else {
        a.packages.clone()
    };
    if chosen.is_empty() {
        return Ok(Exit::ZeroUnits);
    }

    // the key of each unit, from the files on disk
    let env: Vec<(String, String)> = std::env::vars().collect();
    let ctx = Context {
        machine_kind: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        toolchain: context::cargo_toolchain(),
        vars: CARGO_VARS.iter().map(|s| s.to_string()).collect(),
        target_dir: ws.target_dir.clone(),
        target_shared: false,
    };
    let all = key::wide_paths(&root);
    let chash = ctx.hash(&env);
    let mut cache = FileCache::default();
    let present = |f: &str| root.join(f).exists();
    let state = state_root(a);
    let rid = repo_id(&root);
    let (streams, problems) = ledger::streams(&state.join("ledger"), &rid);
    let mut rows: Vec<Row> = Vec::new();
    for name in &chosen {
        let k = (|| -> Result<String, String> {
            let all = all.as_ref().map_err(|e| format!("{e:?}"))?;
            let ch = chash.as_ref().map_err(|e| format!("{e:?}"))?;
            let pre = ws.inputs(name, &present)?;
            let paths = key::declared(all, &pre).map_err(|e| format!("{e:?}"))?;
            let inputs =
                key::input_list(&root, &paths, &mut cache).map_err(|e| format!("{e:?}"))?;
            Ok(key::key(
                &format!("cargo nextest run -p {name}"),
                ch,
                &inputs,
            ))
        })();
        let (key, unmeasurable) = match k {
            Ok(k) => (Some(k), None),
            Err(e) => (None, Some(e)),
        };
        let src = key
            .as_deref()
            .and_then(|k| ledger::local_source(&streams, name, k));
        let would = src.map(|(st, _, at)| Reuse {
            origin: Origin::Local,
            from: format!("{rid}/{}", st.invocation_id),
            key: key.clone().unwrap_or_default(),
            tree: st.plan.tree.clone(),
            at: aib_ledger::envelope::rfc3339_ms(at),
        });
        let source_result = src.map(|(_, u, _)| u.result);
        let source = src.map(|(st, u, _)| Verdict {
            outcome: audit_outcome(u.result),
            tests_ran: u.tests_ran,
            failed: u.failed.clone(),
            output_digest: u.output_digest.clone().unwrap_or_default(),
            censored: !st.complete(),
        });
        rows.push(Row {
            unit: name.clone(),
            key,
            unmeasurable,
            would,
            source_result,
            source,
            failed: Vec::new(),
            audit: None,
            result: Outcome::None,
            ran: 0,
            ms: 0,
        });
    }

    // observation mode: every unit runs, one nextest for all of them
    let cfg = state.join("nextest-aib.toml");
    std::fs::create_dir_all(&state).map_err(|e| e.to_string())?;
    std::fs::write(&cfg, aib_test_cargo::TOOL_CONFIG).map_err(|e| e.to_string())?;
    let mut select = Vec::new();
    for n in &chosen {
        select.push("-p".to_string());
        select.push(n.clone());
    }
    // no silent fallback to `cargo test` (519d0 11-17 ruling 2): say what is missing
    let ver = cargo(&root, &["nextest".to_string(), "--version".to_string()]);
    if !ver.as_ref().is_ok_and(|o| o.status.success()) {
        return Err(
            "cargo-nextest is not installed (cargo nextest --version failed); install it: \
             cargo install cargo-nextest --locked, or the prebuilt binary from get.nexte.st"
                .into(),
        );
    }
    let list = cargo(&root, &list_args(&cfg, &select))?;
    if !list.status.success() {
        return Err(format!(
            "cargo nextest list failed: {}",
            String::from_utf8_lossy(&list.stderr).trim()
        ));
    }
    let listed = parse_list(&String::from_utf8_lossy(&list.stdout))?;
    let started = now_ms();
    // a junit.xml left by an earlier run must never be read as this one's (5d33a L1)
    let jp = junit_path(Path::new(&ws.target_dir));
    match std::fs::remove_file(&jp) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => {
            return Err(format!(
                "{}: cannot clear the last run's result: {e}",
                jp.display()
            ))
        }
    }
    let runout = cargo(&root, &run_args(&cfg, &select))?;
    let xml = std::fs::read_to_string(&jp).map_err(|e| format!("{}: {e}", jp.display()))?;
    let cases = parse_junit(&xml)?;

    // read the result back per package
    let mut per: BTreeMap<String, (Vec<_>, Vec<_>)> = BTreeMap::new();
    for l in &listed.tests {
        per.entry(units::package_of_binary(&l.binary).to_string())
            .or_default()
            .0
            .push(l.clone());
    }
    for c in &cases {
        per.entry(units::package_of_binary(&c.binary).to_string())
            .or_default()
            .1
            .push(c.clone());
    }
    let mut outcomes = Vec::new();
    for r in &mut rows {
        let (l, c) = per.remove(&r.unit).unwrap_or_default();
        // the unit's own time: the sum of its cases' times (5d33a 0cddcc0c N1; not the whole run's)
        r.ms = c.iter().filter_map(|x| x.ms).sum();
        let reading = aib_test_cargo::read(&l, &c);
        let RunOutcome::Ran { met, tests_ran } = reading.outcome.clone() else {
            unreachable!("read always gives Ran")
        };
        r.ran = tests_ran.unwrap_or(0);
        r.result = if r.ran == 0 {
            Outcome::Invalid
        } else if met {
            Outcome::Pass
        } else {
            Outcome::Fail
        };
        r.failed = c
            .iter()
            .filter(|x| x.status == aib_test_cargo::CaseStatus::Failed)
            .map(|x| format!("{} {}", x.binary, x.name))
            .collect();
        r.failed.sort();
        if let Some(src) = &r.source {
            // this real run is an audit of the source it would have reused; no output digest
            // is taken yet, so the two sides compare on outcome, count and failed names only
            let audit = Verdict {
                outcome: audit_outcome(r.result),
                tests_ran: Some(r.ran),
                failed: r.failed.clone(),
                output_digest: String::new(),
                censored: false,
            };
            let l = engine::link(&audit, src, met);
            r.audit = Some(Audit {
                compare: match l.compare {
                    Compare::Same => AuditCompare::Same,
                    Compare::BehaviorChanged => AuditCompare::BehaviorChanged,
                    Compare::Mismatch => AuditCompare::Mismatch,
                    Compare::Incomparable => AuditCompare::Incomparable,
                },
                revoke: l.revoke,
            });
        }
        outcomes.push(reading.outcome);
    }
    let code = exit_for(
        &outcomes,
        Use::Local,
        Some(std::env::consts::OS),
        false,
        !runout.status.success() && cases.is_empty(),
        &|_| false,
    );

    // the ledger: one stream for this run
    let head = git(&root, &["rev-parse", "HEAD"]).unwrap_or_default();
    let tree = git(&root, &["rev-parse", "HEAD^{tree}"]).unwrap_or_default();
    let dirty = git(&root, &["status", "--porcelain"]).is_none_or(|s| !s.is_empty());
    let inv = format!("tr-{}-{}", started, std::process::id());
    let seat = std::env::var("AIB_SEAT").unwrap_or_else(|_| "unknown".into());
    let plan = Plan {
        head: head.clone(),
        tree: if dirty {
            format!("{tree}+dirty")
        } else {
            tree.clone()
        },
        host: ctx.machine_kind.clone(),
        run_os: std::env::consts::OS.to_string(),
        cells: vec!["aib-test-rs-observe".into()],
        units: chosen.clone(),
    };
    let mut w = ledger::Writer::start(&state.join("ledger"), &rid, &inv, &seat, started, &plan)?;
    for r in &rows {
        let u = Unit {
            unit: r.unit.clone(),
            krate: Some(r.unit.clone()),
            how: How::Ran,
            result: r.result,
            reuse: None,
            why_not: None,
            why_detail: r.unmeasurable.clone(),
            skip: None,
            dur_ms: Some(r.ms),
            seat: None,
            key: r.key.clone(),
            tests_ran: Some(r.ran),
            failed: r.failed.clone(),
            output_digest: None,
            audit: r.audit.clone(),
            shadow: Some(Shadow {
                would_reuse: r.would.clone(),
                same: r.source_result.map(|s| s == r.result),
            }),
        };
        w.unit(now_ms(), &u)?;
    }
    w.end(now_ms(), true)?;

    // what it says
    let would = rows.iter().filter(|r| r.would.is_some()).count();
    let same = rows
        .iter()
        .filter(|r| r.source_result == Some(r.result))
        .count();
    if a.json {
        let v: Vec<serde_json::Value> = rows
            .iter()
            .map(|r| {
                serde_json::json!({
                    "unit": r.unit, "result": r.result.word(), "tests": r.ran,
                    "key": r.key, "unmeasurable": r.unmeasurable,
                    "would_reuse": r.would.as_ref().map(|w| serde_json::json!({
                        "from": w.from, "key": w.key, "tree": w.tree, "at": w.at })),
                    "same": r.source_result.map(|s| s == r.result),
                    "audit": r.audit.as_ref().map(|a| serde_json::json!({
                        "compare": a.compare.word(), "revoke": a.revoke })),
                })
            })
            .collect();
        println!(
            "{}",
            serde_json::json!({"invocation_id": inv, "head": head, "dirty": dirty,
                "exit": code.code(), "units": v, "ledger_problems": problems})
        );
    } else {
        if a.why {
            for r in &rows {
                let src = r
                    .would
                    .as_ref()
                    .map(|w| {
                        format!(
                            "would reuse {} (key {} tree {} at {})",
                            w.from,
                            w.key.get(..12).unwrap_or(&w.key),
                            w.tree.get(..12).unwrap_or(&w.tree),
                            w.at
                        )
                    })
                    .unwrap_or_else(|| {
                        r.unmeasurable
                            .clone()
                            .map(|e| format!("not measured: {e}"))
                            .unwrap_or_else(|| "no earlier passing run with this key".into())
                    });
                let src = match &r.audit {
                    Some(a) if a.revoke => {
                        format!("{src}; audit {} ⇒ source revoked", a.compare.word())
                    }
                    Some(a) => format!("{src}; audit {}", a.compare.word()),
                    None => src,
                };
                println!(
                    "  {:<24} {:<7} {:>4} tests  {src}",
                    r.unit,
                    r.result.word(),
                    r.ran
                );
            }
            for p in &problems {
                println!("  ledger: {p}");
            }
        }
        println!(
            "aib-test-rs (observe): {} units ran, {} would have been reused ({} same result), exit {}, ledger {}",
            rows.len(),
            would,
            same,
            code.code(),
            ledger::stream_path(&state.join("ledger"), &rid, &inv).display()
        );
    }
    Ok(code)
}

/// The ledger's result in the audit's words (a line with no result never finished).
fn audit_outcome(o: Outcome) -> AuditOutcome {
    match o {
        Outcome::Pass => AuditOutcome::Pass,
        Outcome::Fail => AuditOutcome::Fail,
        Outcome::Invalid => AuditOutcome::Invalid,
        Outcome::None => AuditOutcome::Incomplete,
    }
}
