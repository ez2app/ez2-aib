// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host backup check [--remote <rclone remote>] [--records dir] [--json]`
//! (4.4.1 §10 / §12). Lists the off-site backup with `rclone lsjson` — listing only, no
//! object is read, written or deleted — and hands the listing to `aib_cloud::backup`.
//!
//! The CTs that must be in a backup are the ones the site records name plus the edge. The
//! remote comes from `--remote` or from `REMOTE=` in `/etc/ez2-cloud/backup.env` (the file
//! the backup job itself reads). Exit status as `site verify`: 0 green, 1 red, 3 unknown
//! (including "nothing could be checked"), 2 warnings only.

use std::fs;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aib_cloud::backup::{self, Object};
use aib_cloud::site::{spec_from_record, EDGE_CT};
use aib_cloud::verify;
use serde_json::{json, Value};

use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const BACKUP_ENV: &str = "/etc/ez2-cloud/backup.env";

/// `REMOTE=` out of an env file: the last assignment wins, quotes are dropped, `export`
/// is allowed. Nothing is expanded.
pub fn remote_from_env(text: &str) -> Option<String> {
    text.lines()
        .filter_map(|l| {
            let l = l.trim();
            let l = l.strip_prefix("export ").unwrap_or(l).trim_start();
            let v = l.strip_prefix("REMOTE=")?.trim();
            let v = v
                .strip_prefix('"')
                .and_then(|x| x.strip_suffix('"'))
                .or_else(|| v.strip_prefix('\'').and_then(|x| x.strip_suffix('\'')))
                .unwrap_or(v);
            Some(v.to_owned())
        })
        .next_back()
        .filter(|v| valid_remote(v))
}

/// An rclone remote path as we write them: `name:bucket/path`, plain characters only. It
/// becomes one argument of `rclone`; this keeps it from being read as an option or from
/// pointing at a local path.
pub fn valid_remote(remote: &str) -> bool {
    let Some((name, path)) = remote.split_once(':') else {
        return false;
    };
    let plain = |s: &str, extra: &[u8]| {
        s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || extra.contains(&c))
    };
    !name.is_empty()
        && !name.starts_with('-')
        && plain(name, b"_-")
        && !path.is_empty()
        && plain(path, b"_-./")
        && !path.split('/').any(|seg| seg == "..")
}

/// `rclone lsjson` output as (name, size, is a directory). `None` when it is not the JSON
/// rclone writes.
pub fn parse_lsjson(out: &str) -> Option<Vec<(String, u64, bool)>> {
    let v: Value = serde_json::from_str(out).ok()?;
    v.as_array()?
        .iter()
        .map(|e| {
            let name = e.get("Name")?.as_str()?.to_owned();
            let is_dir = e.get("IsDir")?.as_bool()?;
            // directories report -1
            let size = e.get("Size").and_then(Value::as_u64).unwrap_or(0);
            Some((name, size, is_dir))
        })
        .collect()
}

fn lsjson(remote: &str) -> Option<Vec<(String, u64, bool)>> {
    let mut cmd = Command::new("rclone");
    cmd.args(["lsjson", "--no-modtime", "--no-mimetype", remote])
        .env("LC_ALL", "C");
    parse_lsjson(&run_capture(&mut cmd, Duration::from_secs(60)).ok()?)
}

fn objects(listing: Vec<(String, u64, bool)>) -> Vec<Object> {
    listing
        .into_iter()
        .filter(|(_, _, is_dir)| !is_dir)
        .map(|(name, size, _)| Object { name, size })
        .collect()
}

/// CT ids the site records name, plus the edge. An unreadable directory, an unreadable
/// record or no record at all is an error: a list that silently leaves a site out is how
/// a site goes without backup unnoticed.
fn must_have(records: &str) -> Result<Vec<u32>, String> {
    let mut cts = vec![EDGE_CT];
    for entry in fs::read_dir(records).map_err(|e| format!("{records}: {e}"))? {
        let path = entry.map_err(|e| format!("{records}: {e}"))?.path();
        if path.extension().is_none_or(|x| x != "json") {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rec: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        // lenient on purpose: only the CT id is needed here, and a record with gaps still
        // names a CT that must be backed up
        let spec = spec_from_record(&rec).map_err(|e| format!("{}: {e}", path.display()))?;
        cts.push(spec.ct);
    }
    if cts.len() == 1 {
        return Err(format!("{records}: no site records"));
    }
    Ok(cts)
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    match run(args) {
        Ok(code) => Ok(code),
        Err(why) => {
            eprintln!("aib-cloud-host backup check: {why}");
            Ok(3)
        }
    }
}

fn run(args: &[String]) -> Result<u8, String> {
    let mut remote: Option<String> = None;
    let mut records = RECORDS.to_owned();
    let mut as_json = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--remote" => remote = Some(it.next().ok_or("--remote needs <name>:<path>")?.clone()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--json" => as_json = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let remote = match remote {
        Some(r) if valid_remote(&r) => r,
        Some(r) => return Err(format!("--remote {r:?} is not <name>:<path>")),
        None => fs::read_to_string(BACKUP_ENV)
            .ok()
            .and_then(|t| remote_from_env(&t))
            .ok_or_else(|| format!("no usable REMOTE= in {BACKUP_ENV}; pass --remote"))?,
    };
    let cts = must_have(&records)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();

    let top = lsjson(&remote);
    let days: Option<Vec<String>> = top.map(|l| {
        l.into_iter()
            .filter(|(_, _, is_dir)| *is_dir)
            .map(|(name, _, _)| name)
            .collect()
    });
    // newest and the one before it, among the folders that are dates
    let mut dated: Vec<&String> = days
        .iter()
        .flatten()
        .filter(|d| backup::day_number(d).is_some())
        .collect();
    dated.sort();
    let newest = dated
        .last()
        .and_then(|d| lsjson(&format!("{remote}/{d}")))
        .map(objects);
    let previous = match dated.len() {
        0 | 1 => Some(Vec::new()),
        n => lsjson(&format!("{remote}/{}", dated[n - 2])).map(objects),
    };
    let results = backup::check(
        now,
        days.as_deref(),
        newest.as_deref(),
        previous.as_deref(),
        &cts,
    );
    let exit = verify::exit_code(&results);
    if as_json {
        let doc = json!({ "exit": exit, "cts": cts, "backup": verify::report(None, &results) });
        println!(
            "{}",
            serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
        );
    } else {
        for r in &results {
            let (word, code) = match r.outcome {
                verify::Outcome::Green => ("ok     ", "ok"),
                verify::Outcome::Warn(c) => ("warn   ", c),
                verify::Outcome::Red(c) => ("RED    ", c),
                verify::Outcome::Unknown(c) => ("unknown", c),
            };
            eprintln!("{word} {} {code}", r.id);
        }
        eprintln!("{} CT(s) must be in the backup: exit {exit}", cts.len());
    }
    Ok(exit)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn remotes() {
        for ok in ["r2:ez2-cloud-eu02/platform", "b2:bucket/a/b", "my_remote:x"] {
            assert!(valid_remote(ok), "{ok}");
        }
        for bad in [
            "",
            "/var/lib/vz",
            "r2:",
            ":bucket",
            "-o:x",
            "r 2:bucket",
            "r2;x:bucket",
            "r2:bucket/../other",
            "r2:bucket path",
            "r2:bucket;rm",
            "r2:bucket --delete-excluded",
        ] {
            assert!(!valid_remote(bad), "{bad:?}");
        }
    }

    #[test]
    fn remote_out_of_the_backup_env() {
        let env = "# backup\nAGE_RECIPIENT=age1xyz\nREMOTE=r2:ez2-cloud-eu02/platform\nKEEP_LOCAL_DAYS=3\n";
        assert_eq!(
            remote_from_env(env).as_deref(),
            Some("r2:ez2-cloud-eu02/platform")
        );
        assert_eq!(
            remote_from_env("export REMOTE=\"b2:bk/p\"\n").as_deref(),
            Some("b2:bk/p")
        );
        assert_eq!(
            remote_from_env("REMOTE='b2:bk/p'\n").as_deref(),
            Some("b2:bk/p")
        );
        // the last assignment wins; one that is not a remote is not used
        assert_eq!(
            remote_from_env("REMOTE=a:b\nREMOTE=c:d\n").as_deref(),
            Some("c:d")
        );
        assert_eq!(remote_from_env("REMOTE=$OTHER/x\n"), None);
        assert_eq!(remote_from_env("#REMOTE=a:b\nXREMOTE=a:b\n"), None);
        assert_eq!(remote_from_env(""), None);
    }

    #[test]
    fn lsjson_listings() {
        let out = r#"[{"Path":"2026-11-10","Name":"2026-11-10","Size":-1,"IsDir":true},
                      {"Path":"x.age","Name":"x.age","Size":12345,"IsDir":false}]"#;
        assert_eq!(
            parse_lsjson(out),
            Some(vec![
                ("2026-11-10".to_owned(), 0, true),
                ("x.age".to_owned(), 12345, false)
            ])
        );
        assert_eq!(parse_lsjson("[]"), Some(Vec::new()));
        assert_eq!(parse_lsjson("Failed to lsjson: directory not found"), None);
        assert_eq!(parse_lsjson(r#"[{"Name":"x"}]"#), None);
        let files = objects(parse_lsjson(out).unwrap());
        assert_eq!(files.len(), 1);
        assert_eq!((files[0].name.as_str(), files[0].size), ("x.age", 12345));
    }

    #[test]
    fn nothing_listed_is_exit_3() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert_eq!(cmd(&args(&["--remote", "/etc"])), Ok(3));
        assert_eq!(cmd(&args(&["--bogus"])), Ok(3));
        assert_eq!(
            cmd(&args(&[
                "--remote",
                "r2:b/p",
                "--records",
                "/nonexistent/aib-bk"
            ])),
            Ok(3)
        );
        // records that name CTs, a remote nobody can list: every rule unknown, exit 3
        let dir = std::env::temp_dir().join(format!("aib-bk-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("site2.json"),
            r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test"}"#,
        )
        .unwrap();
        assert_eq!(must_have(&dir.to_string_lossy()).unwrap(), [EDGE_CT, 112]);
        let listed = cmd(&args(&[
            "--remote",
            "nosuchremote-aib-test:b/p",
            "--records",
            &dir.to_string_lossy(),
        ]));
        assert_eq!(listed, Ok(3));
        let _ = fs::remove_dir_all(&dir);
    }
}
