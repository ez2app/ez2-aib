//! aib-seatops — seat operations that used to be shell scripts.
//!
//!   aib-seatops dm close --seat S --id q_<16 hex> --rev N --expect TEXT (--evidence E | --why W) [--json]
//!
//! After a close, the seat's `aib now` time is refreshed (item, steps and state are kept); if
//! that cannot be done one line says why and the exit code is still the close's.
//!
//! Exit: 0 closed; 2 refused or usage (nothing changed); 3 the queue could not be locked, read
//! or written.
//!
//! Environment: EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME (default ~/.ez2-aib) for the queue and the
//! relay files; EZ2_AIB_REPO (default ~/ez2-aib) for commits given as evidence.

use std::process::ExitCode;

const HELP: &str = "\
aib-seatops dm close --seat S --id q_<16 hex> --rev N --expect TEXT (--evidence E | --why W) [--json]

Closes one dm of your own seat, atomically with the Python queue: the content (and its relay
file) must still be what you read (--rev, --expect), and the close happens inside the seat's
transition lock. Evidence is one whole token: an existing file, a commit of the repository, a
full PR link, or the id of a reply you sent; otherwise use --why.
exit: 0 closed; 2 refused / usage (nothing changed); 3 the queue could not be locked or written.
";

#[cfg(unix)]
fn run(argv: &[String]) -> Result<String, (u8, String)> {
    use aib_seatops::close::{close, Request, Roots};
    use std::path::PathBuf;

    let usage = |m: String| Err((2, m));
    if argv.first().map(String::as_str) != Some("dm")
        || argv.get(1).map(String::as_str) != Some("close")
    {
        return usage("unknown command (dm close)".into());
    }
    let mut opt = std::collections::BTreeMap::<String, String>::new();
    let mut json_out = false;
    let mut it = argv[2..].iter();
    while let Some(k) = it.next() {
        match k.as_str() {
            "--json" => json_out = true,
            "--seat" | "--id" | "--rev" | "--expect" | "--evidence" | "--why" => {
                let Some(v) = it.next() else {
                    return usage(format!("{k} needs a value"));
                };
                if opt.insert(k.clone(), v.clone()).is_some() {
                    return usage(format!("{k} given twice"));
                }
            }
            other => return usage(format!("unknown argument {other}")),
        }
    }
    let need = |k: &str| opt.get(k).cloned().ok_or((2u8, format!("{k} is required")));
    let rev = need("--rev")?
        .parse::<u64>()
        .map_err(|_| (2u8, "--rev is a number".to_string()))?;
    let req = Request {
        seat: need("--seat")?,
        id: need("--id")?,
        rev,
        expect: need("--expect")?,
        evidence: opt.get("--evidence").cloned(),
        why: opt.get("--why").cloned(),
    };
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    let env_path = |k: &str| {
        std::env::var_os(k)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    let state = env_path("EZ2_AIB_STATE_ROOT")
        .or_else(|| env_path("EZ2_AIB_HOME"))
        .unwrap_or_else(|| home.join(".ez2-aib"));
    // relay files: <state>/pending, and <state>/instances/<cabin>/state/relay[/<YYYYMMDD>]
    let mut relay_dirs = vec![state.join("pending")];
    if let Ok(rd) = std::fs::read_dir(state.join("instances")) {
        relay_dirs.extend(
            rd.filter_map(|e| e.ok())
                .map(|e| e.path().join("state").join("relay")),
        );
    }
    let roots = Roots {
        repo: env_path("EZ2_AIB_REPO").unwrap_or_else(|| home.join("ez2-aib")),
        state,
        relay_dirs,
    };
    let res = close(&roots, &req);
    if res.is_ok() {
        // B10: the close counts as activity on the status line; a failure here never changes rc
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        if let Err(e) = aib_seatops::nowline::refresh(&roots.state, &req.seat, now) {
            eprintln!("aib-seatops: status line not refreshed: {e}");
        }
    }
    match res {
        Ok(c) if json_out => Ok(serde_json::json!({
            "ok": true, "reason": "ok", "id": c.id, "rev": c.rev, "from": c.from,
            "done_kind": c.done_kind,
        })
        .to_string()),
        Ok(c) => Ok(format!(
            "CLOSED {} ({})",
            c.id,
            if c.done_kind == "reason" {
                "why"
            } else {
                "evidence"
            }
        )),
        Err(r) => {
            let rc = if r.broken { 3 } else { 2 };
            if json_out {
                Err((
                    rc,
                    serde_json::json!({"ok": false, "reason": r.reason, "message": r.message})
                        .to_string(),
                ))
            } else {
                Err((
                    rc,
                    format!("NOT CLOSED {} :: {}: {}", req.id, r.reason, r.message),
                ))
            }
        }
    }
}

#[cfg(not(unix))]
fn run(_argv: &[String]) -> Result<String, (u8, String)> {
    Err((2, "aib-seatops dm close runs on macOS / Linux only".into()))
}

fn main() -> ExitCode {
    let argv: Vec<String> = match std::env::args_os()
        .skip(1)
        .map(|a| a.into_string())
        .collect()
    {
        Ok(v) => v,
        Err(a) => {
            eprintln!("aib-seatops: argument {a:?} is not UTF-8");
            return ExitCode::from(2);
        }
    };
    if argv.iter().any(|a| a == "-h" || a == "--help") {
        print!("{HELP}");
        return ExitCode::SUCCESS;
    }
    match run(&argv) {
        Ok(line) => {
            println!("{line}");
            ExitCode::SUCCESS
        }
        Err((rc, m)) => {
            eprintln!("{m}");
            ExitCode::from(rc)
        }
    }
}
