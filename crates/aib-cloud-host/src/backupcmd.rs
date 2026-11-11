// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host backup check [--remote <rclone remote>] [--records dir] [--json]`
//! (4.4.1 §10 / §12). Lists the off-site backup with `rclone lsjson` — listing only, no
//! object is read, written or deleted — and hands the listing to `aib_cloud::backup`.
//!
//! The CTs that must be in a backup are the ones the site records name plus the edge. The
//! remote comes from `--remote` or from `REMOTE=` in `/etc/ez2-cloud/backup.env` (the file
//! the backup job itself reads). Exit status as `site verify`: 0 green, 1 red, 3 unknown
//! (including "nothing could be checked"), 2 warnings only.
//!
//! `aib-cloud-host backup fresh <code> [--max-minutes N] [--records dir] [--dumps dir]`
//! (E10, backup before an update): does the site have a finished local dump from the last
//! N minutes (default 30)? Reads the site record and the names, times and sizes in the
//! local dump folder — no dump is opened, nothing is written. The rule is
//! `aib_cloud::fresh`. Prints one JSON object; exit 0 fresh (an update may go ahead),
//! 1 stale or no backup, 3 could not tell, 2 usage. Starting a backup is not part of it.

use std::fs;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aib_cloud::backup::{self, Object};
use aib_cloud::fresh::{self, DumpFile, Fresh};
use aib_cloud::site::{spec_from_record, valid_code, EDGE_CT};
use aib_cloud::verify;
use serde_json::{json, Value};

use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const BACKUP_ENV: &str = "/etc/ez2-cloud/backup.env";
/// Where vzdump leaves the local dumps (the `local` storage).
const DUMPS: &str = "/var/lib/vz/dump";

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

/// `RCLONE_*` settings out of the same env file, for `rclone` itself. The backup job
/// sources the file, so its `rclone` runs with them; on the first host that is
/// `RCLONE_BIND`, the address the storage token is locked to — without it the listing
/// leaves over IPv6 and is refused (ebb36, #631 seg. 4 M1). Read like
/// [`remote_from_env`]: the last assignment wins, quotes are dropped, nothing is
/// expanded; a value that would need expanding is left out.
pub fn rclone_env(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for l in text.lines() {
        let l = l.trim();
        let l = l.strip_prefix("export ").unwrap_or(l).trim_start();
        let Some((k, v)) = l.split_once('=') else {
            continue;
        };
        if !k.starts_with("RCLONE_")
            || !k
                .bytes()
                .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
        {
            continue;
        }
        let v = v.trim();
        let v = v
            .strip_prefix('"')
            .and_then(|x| x.strip_suffix('"'))
            .or_else(|| v.strip_prefix('\'').and_then(|x| x.strip_suffix('\'')))
            .unwrap_or(v);
        out.retain(|(name, _)| name != k);
        if v.is_empty() || v.contains(['$', '`', '\\']) {
            continue;
        }
        out.push((k.to_owned(), v.to_owned()));
    }
    out
}

/// The `rclone lsjson` that lists `remote`, run with the backup job's rclone settings.
fn rclone_lsjson(remote: &str, env: &[(String, String)]) -> Command {
    let mut cmd = Command::new("rclone");
    cmd.args(["lsjson", "--no-modtime", "--no-mimetype", remote])
        .env("LC_ALL", "C");
    cmd.envs(env.iter().map(|(k, v)| (k, v)));
    cmd
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

fn lsjson(remote: &str, env: &[(String, String)]) -> Option<Vec<(String, u64, bool)>> {
    let mut cmd = rclone_lsjson(remote, env);
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
    // read once: the remote (unless named on the command line) and rclone's settings
    let env_text = fs::read_to_string(BACKUP_ENV).ok();
    let env = env_text.as_deref().map(rclone_env).unwrap_or_default();
    let remote = match remote {
        Some(r) if valid_remote(&r) => r,
        Some(r) => return Err(format!("--remote {r:?} is not <name>:<path>")),
        None => env_text
            .as_deref()
            .and_then(remote_from_env)
            .ok_or_else(|| format!("no usable REMOTE= in {BACKUP_ENV}; pass --remote"))?,
    };
    let cts = must_have(&records)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();

    let top = lsjson(&remote, &env);
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
        .and_then(|d| lsjson(&format!("{remote}/{d}"), &env))
        .map(objects);
    let previous = match dated.len() {
        0 | 1 => Some(Vec::new()),
        n => lsjson(&format!("{remote}/{}", dated[n - 2]), &env).map(objects),
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

/// The local dump folder as (name, plain file, modification time, size); `None` when the
/// folder cannot be listed. Links are not followed: a link is not a plain file.
fn list_dumps(dir: &str) -> Option<Vec<(String, bool, Option<u64>, Option<u64>)>> {
    let mut out = Vec::new();
    for entry in fs::read_dir(dir).ok()? {
        let entry = entry.ok()?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let meta = fs::symlink_metadata(entry.path()).ok();
        let is_file = meta.as_ref().is_some_and(fs::Metadata::is_file);
        let mtime = meta
            .as_ref()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs());
        let size = meta.as_ref().map(fs::Metadata::len);
        out.push((name, is_file, mtime, size));
    }
    Some(out)
}

/// The answer to `backup fresh`: (exit status, JSON). Every input is a parameter, so the
/// whole path from the record to the verdict is tested without a host.
fn fresh_answer(code: &str, max_minutes: u32, records: &str, dumps: &str, now: u64) -> (u8, Value) {
    let unknown = |why: &str| {
        (
            3,
            json!({ "site": code, "verdict": why, "update_allowed": false, "max_minutes": max_minutes }),
        )
    };
    let Ok(text) = fs::read_to_string(format!("{records}/{code}.json")) else {
        return unknown("record_unreadable");
    };
    let Ok(rec) = serde_json::from_str::<Value>(&text) else {
        return unknown("record_unreadable");
    };
    let Ok(spec) = spec_from_record(&rec) else {
        return unknown("record_unreadable");
    };
    let listed = list_dumps(dumps);
    let files: Option<Vec<DumpFile<'_>>> = listed.as_ref().map(|l| {
        l.iter()
            .map(|(name, is_file, mtime, size)| DumpFile {
                name,
                is_file: *is_file,
                mtime: *mtime,
                size: *size,
            })
            .collect()
    });
    let got = fresh::judge(
        spec.ct,
        files.as_deref(),
        now,
        u64::from(max_minutes) * 60,
    );
    let age = match got {
        Fresh::Fresh { age } | Fresh::Stale { age } => Some(age),
        Fresh::NoBackup | Fresh::Unknown(_) => None,
    };
    let exit = match got {
        Fresh::Fresh { .. } => 0,
        Fresh::Stale { .. } | Fresh::NoBackup => 1,
        Fresh::Unknown(_) => 3,
    };
    (
        exit,
        json!({
            "site": code,
            "ct": spec.ct,
            "verdict": got.as_str(),
            "update_allowed": fresh::update_allowed(got),
            "age_seconds": age,
            "max_minutes": max_minutes,
        }),
    )
}

/// `backup fresh <code> [--max-minutes N] [--records dir] [--dumps dir]`.
pub fn fresh_cmd(args: &[String]) -> Result<u8, String> {
    let mut code: Option<&str> = None;
    let mut max_minutes = fresh::DEFAULT_MAX_MINUTES;
    let mut records = RECORDS.to_owned();
    let mut dumps = DUMPS.to_owned();
    let mut it = args.iter();
    let usage = |why: &str| {
        eprintln!("aib-cloud-host backup fresh: {why}");
        Ok(2)
    };
    while let Some(a) = it.next() {
        match a.as_str() {
            "--max-minutes" => {
                match it.next().and_then(|n| n.parse::<u32>().ok()) {
                    Some(n) if fresh::max_minutes_ok(n) => max_minutes = n,
                    _ => return usage("--max-minutes needs 1 to 1440"),
                }
            }
            "--records" => match it.next() {
                Some(d) => records = d.clone(),
                None => return usage("--records needs a dir"),
            },
            "--dumps" => match it.next() {
                Some(d) => dumps = d.clone(),
                None => return usage("--dumps needs a dir"),
            },
            other if code.is_none() && valid_code(other) => code = Some(other),
            _ => return usage("needs <code> [--max-minutes N] [--records dir] [--dumps dir]"),
        }
    }
    let Some(code) = code else {
        return usage("needs <code>");
    };
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();
    let (exit, doc) = fresh_answer(code, max_minutes, &records, &dumps, now);
    println!("{doc}");
    Ok(exit)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn remotes() {
        for ok in [
            "r2:ez2-cloud-host-a/platform",
            "b2:bucket/a/b",
            "my_remote:x",
        ] {
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

    /// ebb36, #631 seg. 4 M1: on the first host the listing is refused without RCLONE_BIND.
    #[test]
    fn rclone_runs_with_the_backup_jobs_settings() {
        let text = "# backup\nAGE_RECIPIENT=age1xyz\nREMOTE=r2:ez2-cloud-host-a/platform\n\
                    RCLONE_BIND=203.0.113.90\nexport RCLONE_RETRIES=\"5\"\nRCLONE_BIND=10.0.0.1\n\
                    RCLONE_BIND=203.0.113.90\nRCLONE_S3_ENDPOINT=$ENDPOINT\nRCLONE_lower=x\n\
                    XRCLONE_BIND=1.2.3.4\nRCLONE_EMPTY=\n";
        let env = rclone_env(text);
        assert_eq!(
            env,
            [
                ("RCLONE_RETRIES".to_owned(), "5".to_owned()),
                ("RCLONE_BIND".to_owned(), "203.0.113.90".to_owned()),
            ]
        );
        // a later assignment that cannot be used takes the earlier one with it
        assert_eq!(rclone_env("RCLONE_BIND=1.2.3.4\nRCLONE_BIND=$IP\n"), []);
        // and they reach rclone, next to the fixed arguments
        let cmd = rclone_lsjson("r2:bucket/platform", &env);
        let got: Vec<(String, String)> = cmd
            .get_envs()
            .filter_map(|(k, v)| Some((k.to_str()?.to_owned(), v?.to_str()?.to_owned())))
            .collect();
        assert!(got.contains(&("RCLONE_BIND".to_owned(), "203.0.113.90".to_owned())));
        assert!(got.contains(&("RCLONE_RETRIES".to_owned(), "5".to_owned())));
        assert!(got.contains(&("LC_ALL".to_owned(), "C".to_owned())));
        let args: Vec<_> = cmd.get_args().map(|a| a.to_str().unwrap()).collect();
        assert_eq!(
            args,
            [
                "lsjson",
                "--no-modtime",
                "--no-mimetype",
                "r2:bucket/platform"
            ]
        );
    }

    #[test]
    fn remote_out_of_the_backup_env() {
        let env = "# backup\nAGE_RECIPIENT=age1xyz\nREMOTE=r2:ez2-cloud-host-a/platform\nKEEP_LOCAL_DAYS=3\n";
        assert_eq!(
            remote_from_env(env).as_deref(),
            Some("r2:ez2-cloud-host-a/platform")
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

    #[test]
    fn backup_fresh_answers_from_the_record_and_the_dump_folder() {
        let root = std::env::temp_dir().join(format!("aib-fresh-{}", std::process::id()));
        let records = root.join("sites");
        let dumps = root.join("dump");
        fs::create_dir_all(&records).unwrap();
        fs::create_dir_all(&dumps).unwrap();
        fs::write(
            records.join("site2.json"),
            r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test"}"#,
        )
        .unwrap();
        let (r, d) = (records.to_string_lossy(), dumps.to_string_lossy());
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let put = |name: &str, secs_ago: u64, body: &[u8]| {
            let p = dumps.join(name);
            fs::write(&p, body).unwrap();
            let t = UNIX_EPOCH + Duration::from_secs(now - secs_ago);
            fs::File::options()
                .write(true)
                .open(&p)
                .unwrap()
                .set_modified(t)
                .unwrap();
        };

        // nothing in the folder yet: no backup, no update
        let (exit, doc) = fresh_answer("site2", 30, &r, &d, now);
        assert_eq!((exit, doc["verdict"].as_str()), (1, Some("no_backup")));
        assert_eq!(doc["update_allowed"], false);
        assert_eq!(doc["ct"], 112);

        // last night's backup only: too old
        put("vzdump-lxc-112-2026_11_11-03_00_05.tar.zst", 3 * 3600, b"x");
        let (exit, doc) = fresh_answer("site2", 30, &r, &d, now);
        assert_eq!((exit, doc["verdict"].as_str()), (1, Some("stale")));

        // a link with a dump's name, to a fresh file, is not a backup in the folder
        #[cfg(unix)]
        {
            fs::write(root.join("elsewhere"), b"x").unwrap();
            std::os::unix::fs::symlink(
                root.join("elsewhere"),
                dumps.join("vzdump-lxc-112-2026_11_11-18_25_05.tar.zst"),
            )
            .unwrap();
            assert_eq!(fresh_answer("site2", 30, &r, &d, now).0, 1);
        }

        // one still being written and another site's fresh one do not count
        put("vzdump-lxc-112-2026_11_11-18_30_05.tar.zst.dat", 60, b"x");
        put("vzdump-lxc-113-2026_11_11-18_30_05.tar.zst", 60, b"x");
        let (exit, _) = fresh_answer("site2", 30, &r, &d, now);
        assert_eq!(exit, 1);

        // a finished one from ten minutes ago: the update may go ahead
        put("vzdump-lxc-112-2026_11_11-18_20_05.tar.zst", 600, b"x");
        let (exit, doc) = fresh_answer("site2", 30, &r, &d, now);
        assert_eq!((exit, doc["verdict"].as_str()), (0, Some("fresh")));
        assert_eq!(doc["update_allowed"], true);
        assert_eq!(doc["age_seconds"], 600);
        // with a five-minute window it is not fresh enough
        assert_eq!(fresh_answer("site2", 5, &r, &d, now).0, 1);

        // what cannot be read is never "allowed"
        let (exit, doc) = fresh_answer("site9", 30, &r, &d, now);
        assert_eq!((exit, doc["verdict"].as_str()), (3, Some("record_unreadable")));
        assert_eq!(doc["update_allowed"], false);
        let gone = root.join("nosuchdir");
        let (exit, doc) = fresh_answer("site2", 30, &r, &gone.to_string_lossy(), now);
        assert_eq!(
            (exit, doc["verdict"].as_str()),
            (3, Some("dump_folder_unreadable"))
        );
        fs::write(records.join("site3.json"), "{not json").unwrap();
        assert_eq!(fresh_answer("site3", 30, &r, &d, now).0, 3);

        // usage
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert_eq!(fresh_cmd(&args(&[])), Ok(2));
        assert_eq!(fresh_cmd(&args(&["site2", "--max-minutes", "0"])), Ok(2));
        assert_eq!(fresh_cmd(&args(&["site2", "--max-minutes", "1441"])), Ok(2));
        assert_eq!(fresh_cmd(&args(&["site2", "site3"])), Ok(2));
        assert_eq!(fresh_cmd(&args(&["../etc"])), Ok(2));
        assert_eq!(
            fresh_cmd(&args(&["site2", "--records", &r, "--dumps", &d])),
            Ok(0)
        );
        let _ = fs::remove_dir_all(&root);
    }
}
