// SPDX-License-Identifier: Apache-2.0
//! aib-consent-run — called by `aib consent` (consent.py); not meant to be run by hand.
//!
//!   aib-consent-run stage --home <dir> --program <file> --pin <sha256> --params <file>
//!                         --review <file> --dir <0700 dir>
//!       Check the shared program and its parameter file, copy both into <dir>. Prints one
//!       JSON object: {"ok":true,"program":{…},"params":{…},"review":"<first line>"} or
//!       {"ok":false,"why":"<code>","line":<n>}. Exit 0 / 1 refused / 2 usage.
//!
//!   aib-consent-run supervise --id <id> --log <file> --state <file> --stop-request <file>
//!                             --cwd <dir> --ask-json '["prog","arg",…]'
//!                             [--env K=V]… [--params-copy <file> --params-sha <sha256>]
//!                             [--program-copy <file> --program-sha <sha256>]
//!                             [--quiet-s N] [--ask-after-s N] [--hard-s N] -- <argv…>
//!       Run <argv> under watch (see the supervise module). A staged program or parameter
//!       file is checked against its sha256 again first; the parameters become the job's
//!       environment next to the --env pairs. A staged program must be the last word of
//!       <argv>; it is not run from that path: the bytes just checked go through a pipe and
//!       the interpreter reads /dev/fd/<n> (a change to the copy after the check is never
//!       run). Prints the record as one JSON object.
//!       Exit 0 when a record was printed (whatever the job did) / 1 a staged copy changed /
//!       2 usage.

use aib_consent::params::{self, Refused};
use aib_consent::supervise::{self, Job};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;

fn usage() -> u8 {
    eprintln!("usage: aib-consent-run stage … | aib-consent-run supervise … -- <argv…> (see the source header)");
    2
}

fn refused_json(r: &Refused) -> Value {
    json!({"ok": false, "why": r.code, "line": r.line})
}

/// `--name value` pairs before an optional `--`; repeated names keep every value.
type Flags = Vec<(String, String)>;

fn flags(argv: &[String]) -> Option<(Flags, Vec<String>)> {
    let mut out = Vec::new();
    let mut it = argv.iter();
    while let Some(a) = it.next() {
        if a == "--" {
            return Some((out, it.cloned().collect()));
        }
        let name = a.strip_prefix("--")?;
        let v = it.next()?;
        out.push((name.to_owned(), v.clone()));
    }
    Some((out, Vec::new()))
}

fn one<'a>(f: &'a [(String, String)], name: &str) -> Option<&'a str> {
    let mut v = f.iter().filter(|(k, _)| k == name).map(|(_, v)| v.as_str());
    let first = v.next();
    if v.next().is_some() {
        return None;
    }
    first
}

fn known(f: &[(String, String)], names: &[&str]) -> bool {
    f.iter().all(|(k, _)| names.contains(&k.as_str()))
}

fn stage(f: &[(String, String)]) -> (Value, u8) {
    let names = ["home", "program", "pin", "params", "review", "dir"];
    if !known(f, &names) {
        return (json!({"ok": false, "why": "usage"}), 2);
    }
    let (Some(home), Some(program), Some(pin), Some(pfile), Some(review), Some(dir)) = (
        one(f, "home"),
        one(f, "program"),
        one(f, "pin"),
        one(f, "params"),
        one(f, "review"),
        one(f, "dir"),
    ) else {
        return (json!({"ok": false, "why": "usage"}), 2);
    };
    let prog = match params::check_program(Path::new(home), Path::new(program), pin) {
        Ok(p) => p,
        Err(r) => return (refused_json(&r), 1),
    };
    let Some(declared) = params::declared_keys(&prog.bytes) else {
        return (
            json!({"ok": false, "why": "program_declares_no_params", "line": 0}),
            1,
        );
    };
    let (text, pairs) = match params::read_params(Path::new(pfile)) {
        Ok(x) => x,
        Err(r) => return (refused_json(&r), 1),
    };
    if let Err(r) = params::params_declared(&pairs, &declared) {
        return (refused_json(&r), 1);
    }
    // declared but not given: shown in the dialog (a typo such as ARCHVE= is refused above,
    // so a missing key here is one the program will default or refuse by itself)
    let missing: Vec<String> = declared
        .iter()
        .filter(|d| !pairs.iter().any(|(pk, _)| *pk == d.key))
        .map(|d| d.key.clone())
        .collect();
    let review_text = match std::fs::read(review) {
        Ok(b) if b.len() <= 256 * 1024 => String::from_utf8_lossy(&b).into_owned(),
        _ => {
            return (
                json!({"ok": false, "why": "review_unreadable", "line": 0}),
                1,
            )
        }
    };
    let first = match params::review_first_line(&review_text, pin) {
        Ok(l) => l,
        Err(r) => return (refused_json(&r), 1),
    };
    let name = prog
        .real
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("program")
        .to_owned();
    let pcopy = match params::stage_bytes(Path::new(dir), &name, &prog.bytes) {
        Ok(p) => p,
        Err(r) => return (refused_json(&r), 1),
    };
    let params_copy = match params::stage_bytes(Path::new(dir), "params.env", text.as_bytes()) {
        Ok(p) => p,
        Err(r) => return (refused_json(&r), 1),
    };
    let lang = match prog.lang {
        params::Lang::Bash => "bash",
        params::Lang::Python => "python",
    };
    (
        json!({
            "ok": true,
            "program": {"real": prog.real, "name": name, "sha256": prog.sha256, "copy": pcopy,
                        "lang": lang, "argv": prog.lang.argv(&pcopy),
                        "declared": declared.iter().map(|d| d.key.as_str()).collect::<Vec<_>>()},
            "params": {"sha256": params::sha256_hex(text.as_bytes()), "copy": params_copy,
                       "text": text, "pairs": pairs, "missing": missing},
            "review": first,
        }),
        0,
    )
}

fn secs(f: &[(String, String)], name: &str) -> Result<Option<Duration>, ()> {
    match one(f, name) {
        None => Ok(None),
        Some(v) => v
            .parse::<u64>()
            .map(|s| Some(Duration::from_secs(s)))
            .map_err(|_| ()),
    }
}

fn supervise_cmd(f: &[(String, String)], argv: Vec<String>) -> (Value, u8) {
    let names = [
        "id",
        "log",
        "state",
        "stop-request",
        "cwd",
        "ask-json",
        "env",
        "params-copy",
        "params-sha",
        "program-copy",
        "program-sha",
        "quiet-s",
        "ask-after-s",
        "hard-s",
    ];
    if !known(f, &names) || argv.is_empty() {
        return (json!({"result": "failed", "why": "usage"}), 2);
    }
    let (Some(id), Some(log), Some(state), Some(stop), Some(cwd), Some(ask)) = (
        one(f, "id"),
        one(f, "log"),
        one(f, "state"),
        one(f, "stop-request"),
        one(f, "cwd"),
        one(f, "ask-json"),
    ) else {
        return (json!({"result": "failed", "why": "usage"}), 2);
    };
    let Ok(ask) = serde_json::from_str::<Vec<String>>(ask) else {
        return (json!({"result": "failed", "why": "usage"}), 2);
    };
    if ask.is_empty() {
        return (json!({"result": "failed", "why": "usage"}), 2);
    }
    let mut env: Vec<(String, String)> = Vec::new();
    for (k, v) in f.iter().filter(|(k, _)| k == "env") {
        let _ = k;
        let Some((ek, ev)) = v.split_once('=') else {
            return (json!({"result": "failed", "why": "usage"}), 2);
        };
        env.push((ek.to_owned(), ev.to_owned()));
    }
    // a staged program must still be the approved bytes, and those bytes are what runs
    let mut argv = argv;
    let script = match (one(f, "program-copy"), one(f, "program-sha")) {
        (Some(c), Some(s)) => {
            if argv.last().map(String::as_str) != Some(c) || argv.len() < 2 {
                return (
                    json!({"result": "failed", "why": "program_copy_not_run"}),
                    2,
                );
            }
            let bytes = match params::read_staged(Path::new(c), s, params::PROGRAM_MAX_BYTES) {
                Ok(b) => b,
                Err(r) => return (json!({"result": "failed", "why": r.code}), 1),
            };
            if let Some(last) = argv.last_mut() {
                *last = supervise::SCRIPT_ARG.to_owned();
            }
            Some(bytes)
        }
        (None, None) => None,
        _ => return (json!({"result": "failed", "why": "usage"}), 2),
    };
    // the parameters come from the staged copy (the one the dialog showed), checked again
    match (one(f, "params-copy"), one(f, "params-sha")) {
        (Some(c), Some(s)) => {
            let bytes = match params::read_staged(Path::new(c), s, params::PARAMS_MAX_BYTES as u64)
            {
                Ok(b) => b,
                Err(r) => return (json!({"result": "failed", "why": r.code}), 1),
            };
            let text = String::from_utf8_lossy(&bytes);
            match params::parse_params(&text) {
                Ok(pairs) => {
                    if pairs.iter().any(|(k, _)| env.iter().any(|(ek, _)| ek == k)) {
                        return (json!({"result": "failed", "why": "params_key_clash"}), 1);
                    }
                    env.extend(pairs);
                }
                Err(r) => return (json!({"result": "failed", "why": r.code}), 1),
            }
        }
        (None, None) => {}
        _ => return (json!({"result": "failed", "why": "usage"}), 2),
    }
    let (Ok(quiet), Ok(ask_after), Ok(hard)) = (
        secs(f, "quiet-s"),
        secs(f, "ask-after-s"),
        secs(f, "hard-s"),
    ) else {
        return (json!({"result": "failed", "why": "usage"}), 2);
    };
    let job = Job {
        id: id.to_owned(),
        argv,
        env,
        cwd: PathBuf::from(cwd),
        log: PathBuf::from(log),
        state: PathBuf::from(state),
        stop_request: PathBuf::from(stop),
        ask,
        quiet: supervise::quiet_period(quiet),
        ask_after: supervise::shorter(ask_after, supervise::ASK_AFTER_DEFAULT),
        ask_every: supervise::ASK_EVERY_DEFAULT,
        hard: supervise::shorter(hard, supervise::HARD_DEFAULT),
        grace: supervise::GRACE_DEFAULT,
        ask_limit: supervise::ASK_LIMIT_DEFAULT,
        tick: Duration::from_millis(200),
        script,
    };
    (supervise::supervise(&job), 0)
}

fn run(argv: &[String]) -> u8 {
    let Some(cmd) = argv.first() else {
        return usage();
    };
    let Some((f, rest)) = flags(&argv[1..]) else {
        return usage();
    };
    let (out, rc) = match cmd.as_str() {
        "stage" if rest.is_empty() => stage(&f),
        "supervise" => supervise_cmd(&f, rest),
        _ => return usage(),
    };
    println!("{out}");
    rc
}

fn main() -> std::process::ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(run(&argv))
}
