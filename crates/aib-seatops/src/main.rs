//! aib-seatops — seat operations that used to be shell scripts.
//!
//!   aib-seatops dm close --seat S --id q_<16 hex> --rev N --expect TEXT (--evidence E | --why W) [--json]
//!   aib-seatops rules expiry --file RULES.md [--now EPOCH] [--json] [--notify --cabin C [--captain S]]
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

aib-seatops rules expiry --file RULES.md [--now EPOCH] [--json] [--notify --cabin C [--captain S]]

Every heading with 【暫時】 must carry 【到期:YYYY-MM-DD HH:MM 台北 / 解除條件:… / 負責席:…】;
one marked 【失效】 or 【已解除】 is skipped. --notify sends each expired entry to its owner and
the captain (default a63ab), and entries missing a field to the captain, with `aib dm C/<seat>`.
exit: 0 nothing due; 1 an entry is past its due time; 2 an entry is missing a field (or usage);
3 the file could not be read or a dm was not delivered.
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

/// B12 `rules expiry --file F [--now EPOCH] [--json] [--notify --cabin C [--captain S]]`.
fn rules_expiry(args: &[String]) -> u8 {
    use aib_seatops::rules::{exit_code, line, messages, scan, Verdict};
    let mut opt = std::collections::BTreeMap::<&str, &str>::new();
    let (mut json_out, mut notify) = (false, false);
    let mut it = args.iter();
    while let Some(k) = it.next() {
        match k.as_str() {
            "--json" => json_out = true,
            "--notify" => notify = true,
            "--file" | "--now" | "--cabin" | "--captain" => match it.next() {
                Some(v) if opt.insert(k.as_str(), v.as_str()).is_none() => {}
                Some(_) => return usage(&format!("{k} given twice")),
                None => return usage(&format!("{k} needs a value")),
            },
            other => return usage(&format!("unknown argument {other}")),
        }
    }
    let Some(file) = opt.get("--file") else {
        return usage("--file is required");
    };
    let now = match opt.get("--now") {
        Some(n) => match n.parse::<i64>() {
            Ok(n) => n,
            Err(_) => return usage("--now is epoch seconds"),
        },
        None => std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    };
    let cabin = opt.get("--cabin").copied();
    if notify && cabin.is_none_or(|c| c.is_empty() || !c.bytes().all(|b| b.is_ascii_alphanumeric()))
    {
        return usage("--notify needs --cabin (the cabin code, e.g. 9320)");
    }
    let text = match std::fs::read_to_string(file) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("aib-seatops: cannot read {file}: {e}");
            return 3;
        }
    };
    let entries = scan(&text, now);
    if json_out {
        let rows: Vec<_> = entries
            .iter()
            .map(|e| {
                let (state, owner, why) = match &e.verdict {
                    Verdict::Open { owner, .. } => ("open", Some(owner.as_str()), None),
                    Verdict::Expired { owner, .. } => ("expired", Some(owner.as_str()), None),
                    Verdict::Retired => ("retired", None, None),
                    Verdict::Missing(w) => ("missing", None, Some(*w)),
                };
                serde_json::json!({"line": e.line, "state": state, "due": e.due_text, "owner": owner,
                                   "why": why, "title": e.title})
            })
            .collect();
        println!("{}", serde_json::Value::Array(rows));
    } else {
        entries.iter().for_each(|e| println!("{}", line(e)));
        if entries.is_empty() {
            println!("no 【暫時】 headings in {file}");
        }
    }
    let rc = exit_code(&entries);
    if notify {
        let captain = opt.get("--captain").copied().unwrap_or("a63ab");
        for (seat, msg) in messages(&entries, captain) {
            let to = format!("{}/{seat}", cabin.unwrap_or_default());
            let out = std::process::Command::new("aib")
                .args(["dm", to.as_str(), msg.as_str()])
                .stdin(std::process::Stdio::null())
                .output();
            let sent = out.as_ref().is_ok_and(|o| {
                let s = String::from_utf8_lossy(&o.stdout);
                s.contains("stage=delivered") || s.contains("stage=sort-queued")
            });
            if !sent {
                eprintln!("aib-seatops: dm to {to} not delivered");
                return 3;
            }
            println!("told {to}");
        }
    }
    rc
}

fn usage(m: &str) -> u8 {
    eprintln!("aib-seatops: {m}");
    2
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
    if argv.first().map(String::as_str) == Some("rules") {
        if argv.get(1).map(String::as_str) != Some("expiry") {
            return ExitCode::from(usage("unknown command (rules expiry)"));
        }
        return ExitCode::from(rules_expiry(&argv[2..]));
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
