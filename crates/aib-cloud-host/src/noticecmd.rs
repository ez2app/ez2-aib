// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site notices --usage <file> [--state <file>] [--write]` — E7: what to
//! tell this month about traffic (per customer: 80, 95 and 100 % of the plan's 5 TB, each
//! once) and mail (per site: past 1,000, once), from a month's usage handed in as a file
//! (see [`aib_cloud::quota::parse_usage`]). Measuring traffic and counting mail is not done
//! here; whatever measures them writes that file.
//!
//! Prints one line a notice, `notice <tenant|site> <code> <notice code>`; the customer's
//! words for each code are not written here. Without `--write` nothing is kept, so the
//! same notices print again (a dry run). With `--write` the month's state is written (whole
//! file aside, then renamed over) after the lines are printed: a run that dies in between
//! tells them again next time rather than never. Nothing is cut off and no mail is held
//! back, whatever the numbers (Andy 11-12).
//!
//! Exit: 0 printed (maybe nothing to tell); 2 the usage or the state could not be read
//! (nothing printed, nothing written); 3 a file could not be read or written.

use std::fs;
use std::path::{Path, PathBuf};

use aib_cloud::quota::{self, MonthState, Notice};
use serde_json::Value;

const STATE: &str = "/var/lib/ez2-cloud/notices.json";

pub fn cmd(args: &[String]) -> Result<u8, String> {
    match run(args) {
        Ok(code) => Ok(code),
        Err(why) => {
            eprintln!("aib-cloud-host site notices: {why}");
            Ok(3)
        }
    }
}

/// The line printed for one notice.
pub fn line(n: &Notice) -> String {
    format!("notice {} {} {}", n.about, n.who, n.code)
}

/// Decide from the two files' text (`state`: `None` when there is no state file yet).
/// `Err` is the code of what could not be read.
pub fn decide(usage: &str, state: Option<&str>) -> Result<(MonthState, Vec<Notice>), &'static str> {
    let usage: Value = serde_json::from_str(usage).map_err(|_| quota::NOTICE_USAGE_UNREADABLE)?;
    let usage = quota::parse_usage(&usage)?;
    let state: Option<Value> = match state {
        None => None,
        Some(t) => Some(serde_json::from_str(t).map_err(|_| quota::NOTICE_STATE_UNREADABLE)?),
    };
    let prev = quota::parse_state(state.as_ref())?;
    quota::month_run(prev, &usage)
}

fn put(path: &Path, v: &Value) -> Result<(), String> {
    let mut tmp = path.to_path_buf().into_os_string();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    let err = |p: &Path, e: std::io::Error| format!("{}: {e}", p.display());
    let mut f = fs::File::create(&tmp).map_err(|e| err(&tmp, e))?;
    std::io::Write::write_all(&mut f, format!("{v}\n").as_bytes()).map_err(|e| err(&tmp, e))?;
    // on disk before the rename, so a crash leaves the old state or the new, never a torn one
    f.sync_all().map_err(|e| err(&tmp, e))?;
    fs::rename(&tmp, path).map_err(|e| err(path, e))
}

fn run(args: &[String]) -> Result<u8, String> {
    let mut usage = None;
    let mut state = PathBuf::from(STATE);
    let mut write = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--usage" => usage = Some(PathBuf::from(it.next().ok_or("--usage needs a file")?)),
            "--state" => state = PathBuf::from(it.next().ok_or("--state needs a file")?),
            "--write" => write = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let usage = usage.ok_or("--usage <file> is needed")?;
    let usage_text = fs::read_to_string(&usage).map_err(|e| format!("{}: {e}", usage.display()))?;
    let state_text = match fs::read_to_string(&state) {
        Ok(t) => Some(t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(format!("{}: {e}", state.display())),
    };
    let (next, told) = match decide(&usage_text, state_text.as_deref()) {
        Ok(d) => d,
        Err(code) => {
            println!("stop {code}");
            return Ok(2);
        }
    };
    for n in &told {
        println!("{}", line(n));
    }
    if write {
        put(&state, &quota::state_json(&next))?;
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    const U: &str = r#"{"month":"2026-11","traffic_gb":{"acme":4100},"mail_sent":{"s1":1200}}"#;

    #[test]
    fn notices_print_as_lines_and_the_state_keeps_them() {
        let (st, told) = decide(U, None).unwrap();
        let lines: Vec<String> = told.iter().map(line).collect();
        assert_eq!(
            lines,
            [
                "notice tenant acme traffic_80",
                "notice site s1 mail_over_free"
            ]
        );
        let kept = quota::state_json(&st).to_string();
        assert_eq!(decide(U, Some(&kept)).unwrap().1, vec![]);
    }

    #[test]
    fn a_state_file_that_cannot_be_read_stops_the_run() {
        assert_eq!(
            decide(U, Some("not json")).unwrap_err(),
            quota::NOTICE_STATE_UNREADABLE
        );
        assert_eq!(
            decide("{}", None).unwrap_err(),
            quota::NOTICE_USAGE_UNREADABLE
        );
    }

    #[test]
    fn write_keeps_the_state_and_a_second_run_tells_nothing() {
        let dir = std::env::temp_dir().join(format!("e7notice-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let usage = dir.join("usage.json");
        let state = dir.join("notices.json");
        fs::write(&usage, U).unwrap();
        let a = |w: bool| {
            let mut v = vec![
                String::from("--usage"),
                usage.display().to_string(),
                String::from("--state"),
                state.display().to_string(),
            ];
            if w {
                v.push(String::from("--write"));
            }
            run(&v)
        };
        assert_eq!(a(false), Ok(0));
        assert!(!state.exists());
        assert_eq!(a(true), Ok(0));
        let kept = fs::read_to_string(&state).unwrap();
        assert_eq!(decide(U, Some(&kept)).unwrap().1, vec![]);
        fs::write(&state, "{").unwrap();
        assert_eq!(a(true), Ok(2));
        assert_eq!(fs::read_to_string(&state).unwrap(), "{");
        fs::remove_dir_all(&dir).unwrap();
    }
}
