// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site verify [<code>] [--records dir] [--thin vg/pool] [--json]` — the
//! passive half (4.4.1 §12; check list: c477a). Reads host files and runs read-only
//! commands with a timeout each, hands what it saw to `aib_cloud::verify`, prints the
//! verdicts. Nothing here writes, enters a CT or opens a connection; `--active` checks are
//! a later slice.
//!
//! "Could not observe" is passed on as `None`, and `aib_cloud::verify` turns that into
//! unknown, never green. Exit status: 0 all green, 1 any red, 3 any unknown, 2 warnings
//! only. Output carries check ids and short codes, not what was read.

use std::fs;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aib_cloud::quarantine;
use aib_cloud::site::spec_from_record_strict;
use aib_cloud::verify::{self, CheckResult, DiskUse, Outcome, SiteState};
use serde_json::{json, Value};

use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const THIN_POOL: &str = "vg0/data";
const LAST_PLATFORM_OK: &str = "/var/lib/ez2-cloud/last-platform-ok";
const LAST_EXPORT_OK: &str = "/var/lib/ez2-cloud/last-export-ok";
const DUMP_DIR: &str = "/var/lib/vz";
const CLUSTER_FW: &str = "/etc/pve/firewall/cluster.fw";

/// Run a read-only command; `None` when it could not be run, timed out or failed.
fn observe(program: &str, args: &[&str], seconds: u64) -> Option<String> {
    let mut cmd = Command::new(program);
    // fixed locale: `df`, `lvs` and `pct` change their output with it
    cmd.args(args).env("LC_ALL", "C");
    run_capture(&mut cmd, Duration::from_secs(seconds)).ok()
}

/// Modification time of a marker file in seconds since the epoch; `None` when it is not
/// there (or cannot be read: for a "last success" marker both mean it cannot be shown).
fn mtime(path: &str) -> Option<u64> {
    let modified = fs::metadata(path).ok()?.modified().ok()?;
    Some(modified.duration_since(UNIX_EPOCH).ok()?.as_secs())
}

/// The percentages in `df --output=pcent <a> <b>` output, in order: a header line, then
/// one `NN%` per file system.
pub fn parse_df_pcent(out: &str) -> Vec<Option<u8>> {
    out.lines()
        .skip(1)
        .map(|l| {
            l.trim()
                .strip_suffix('%')
                .and_then(|n| n.parse::<u8>().ok())
        })
        .collect()
}

/// `lvs --noheadings -o data_percent,metadata_percent <pool>`: two decimal numbers.
/// Rounded **up**: 79.01 % is reported as 80 so a threshold is never missed by rounding.
pub fn parse_lvs_percent(out: &str) -> (Option<u8>, Option<u8>) {
    let up = |t: &str| -> Option<u8> {
        let (whole, frac) = t.split_once('.').unwrap_or((t, ""));
        let whole: u8 = whole.parse().ok()?;
        if !frac.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        let more = frac.bytes().any(|c| c != b'0');
        Some(whole.saturating_add(u8::from(more)).min(100))
    };
    let mut fields = out.split_whitespace();
    let data = fields.next().and_then(up);
    let meta = fields.next().and_then(up);
    if fields.next().is_some() {
        return (None, None);
    }
    (data, meta)
}

fn host_checks(now: u64, thin_pool: &str) -> Vec<CheckResult> {
    let df = observe("df", &["--output=pcent", "/", DUMP_DIR], 5)
        .map(|o| parse_df_pcent(&o))
        .unwrap_or_default();
    let (thin_data, thin_meta) = observe(
        "lvs",
        &[
            "--noheadings",
            "-o",
            "data_percent,metadata_percent",
            thin_pool,
        ],
        5,
    )
    .map_or((None, None), |o| parse_lvs_percent(&o));
    let disk = DiskUse {
        root: df.first().copied().flatten(),
        dump: df.get(1).copied().flatten(),
        thin_data,
        thin_meta,
    };
    vec![
        verify::h02_platform_backup(now, mtime(LAST_PLATFORM_OK)),
        verify::h03_customer_export(now, mtime(LAST_EXPORT_OK)),
        verify::h04_disk(disk),
        verify::h05_raid(fs::read_to_string("/proc/mdstat").ok().as_deref()),
        verify::h08_firewall_running(observe("pve-firewall", &["status"], 5).as_deref()),
        verify::h09_cluster_firewall(fs::read_to_string(CLUSTER_FW).ok().as_deref()),
    ]
}

/// The checks of one site, from its record file. A record that is not complete is one red
/// finding (`S00`): nothing else can be judged against values nobody recorded.
fn site_checks(record_path: &std::path::Path) -> (String, Vec<CheckResult>) {
    let name = record_path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let s00 = |code: &'static str| {
        vec![CheckResult {
            id: "S00",
            outcome: Outcome::Red(code),
        }]
    };
    let Some(rec) = fs::read_to_string(record_path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
    else {
        return (name, s00("record_unreadable"));
    };
    let Ok(spec) = spec_from_record_strict(&rec) else {
        return (name, s00("record_incomplete"));
    };
    if spec.code != name {
        return (name, s00("record_name_mismatch"));
    }
    let ct = spec.ct.to_string();
    let status = observe("pct", &["status", &ct], 5);
    let config = observe("pct", &["config", &ct], 5);
    let ct_fw = fs::read_to_string(format!("/etc/pve/firewall/{ct}.fw")).ok();
    // Quarantine is what the firewall file says. Suspension lives at the edge and is not
    // looked at here (that needs to enter the edge CT: an `--active` matter); no passive
    // check below depends on it.
    let state = if ct_fw.as_deref().is_some_and(quarantine::is_quarantined) {
        SiteState::Quarantined
    } else {
        SiteState::Active
    };
    let results = vec![
        verify::s01_ct_status(state, status.as_deref()),
        verify::s02_unprivileged(config.as_deref()),
        verify::s03_firewall_attached(config.as_deref(), ct_fw.as_deref()),
        verify::s04_firewall_group(&spec, state, quarantine::GROUP, ct_fw.as_deref()),
        verify::s05_limits(&spec, config.as_deref()),
    ];
    (name, results)
}

fn print_text(scope: &str, results: &[CheckResult]) {
    for r in results {
        let (word, code) = match r.outcome {
            Outcome::Green => ("ok     ", "ok"),
            Outcome::Warn(c) => ("warn   ", c),
            Outcome::Red(c) => ("RED    ", c),
            Outcome::Unknown(c) => ("unknown", c),
        };
        eprintln!("{word} {} {scope} {code}", r.id);
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let mut records = RECORDS.to_owned();
    let mut thin_pool = THIN_POOL.to_owned();
    let mut as_json = false;
    let mut only: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--thin" => thin_pool = it.next().ok_or("--thin needs <vg>/<pool>")?.clone(),
            "--json" => as_json = true,
            code if !code.starts_with('-') && only.is_none() => {
                if !aib_cloud::site::valid_code(code) {
                    return Err(format!("site code {code:?} is not valid"));
                }
                only = Some(code.to_owned());
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    // the pool name goes to `lvs` as one argument; still, keep it to what a name looks like
    if !thin_pool
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || b"/._-".contains(&c))
        || thin_pool.starts_with('-')
    {
        return Err(format!("--thin {thin_pool:?} is not a pool name"));
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs();

    let mut paths: Vec<std::path::PathBuf> = Vec::new();
    match &only {
        Some(code) => paths.push(format!("{records}/{code}.json").into()),
        None => {
            // an entry that cannot be read is an error: a report that silently leaves a
            // site out would read as "that site is fine"
            for entry in fs::read_dir(&records).map_err(|e| format!("{records}: {e}"))? {
                let path = entry.map_err(|e| format!("{records}: {e}"))?.path();
                if path.extension().is_some_and(|x| x == "json") {
                    paths.push(path);
                }
            }
            paths.sort();
            if paths.is_empty() {
                return Err(format!("{records}: no site records"));
            }
        }
    }

    let host = host_checks(now, &thin_pool);
    let sites: Vec<(String, Vec<CheckResult>)> = paths.iter().map(|p| site_checks(p)).collect();
    let mut all: Vec<CheckResult> = host.clone();
    for (_, results) in &sites {
        all.extend(results.iter().cloned());
    }
    let exit = verify::exit_code(&all);
    if as_json {
        let doc = json!({
            "exit": exit,
            "host": verify::report(None, &host),
            "sites": sites.iter().map(|(code, r)| verify::report(Some(code), r)).collect::<Vec<_>>(),
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
        );
    } else {
        print_text("host", &host);
        for (code, results) in &sites {
            print_text(code, results);
        }
        eprintln!(
            "{} checks on the host and {} site(s): exit {exit}",
            all.len(),
            sites.len()
        );
    }
    Ok(exit)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn df_percentages() {
        assert_eq!(parse_df_pcent("Use%\n 41%\n  7%\n"), [Some(41), Some(7)]);
        assert_eq!(parse_df_pcent("Use%\n100%\n"), [Some(100)]);
        // a line that is not a percentage is "not read", not zero
        assert_eq!(parse_df_pcent("Use%\n-\n 12%\n"), [None, Some(12)]);
        assert!(parse_df_pcent("").is_empty());
    }

    #[test]
    fn lvs_percentages_round_up() {
        assert_eq!(parse_lvs_percent("  12.34  3.00\n"), (Some(13), Some(3)));
        assert_eq!(parse_lvs_percent("79.01 79.00"), (Some(80), Some(79)));
        assert_eq!(parse_lvs_percent("100.00 99.99"), (Some(100), Some(100)));
        assert_eq!(parse_lvs_percent("5 6"), (Some(5), Some(6)));
        assert_eq!(parse_lvs_percent(""), (None, None));
        assert_eq!(parse_lvs_percent("12.34"), (Some(13), None));
        assert_eq!(parse_lvs_percent("x y"), (None, None));
        // more fields than asked for: not the output we know
        assert_eq!(parse_lvs_percent("1.0 2.0 3.0"), (None, None));
    }

    #[test]
    fn a_site_with_no_readable_record_is_red_not_skipped() {
        let dir = std::env::temp_dir().join(format!("aib-sv-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let missing = dir.join("nosuch.json");
        let (name, r) = site_checks(&missing);
        assert_eq!(name, "nosuch");
        assert_eq!(r.len(), 1);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_unreadable"))
        );
        // a record the lenient reader accepts but that leaves the limits to a default
        let poc = dir.join("site2.json");
        fs::write(&poc, r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112}"#).unwrap();
        let (_, r) = site_checks(&poc);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_incomplete"))
        );
        // a complete record under another site's file name
        let full = r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112,"limits":{"cores":2,"cpulimit_x100":200,"memory_mb":2048,"swap_mb":512,"pids_max":2048}}"#;
        let wrong = dir.join("site9.json");
        fs::write(&wrong, full).unwrap();
        let (_, r) = site_checks(&wrong);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_name_mismatch"))
        );
        // a complete record on a machine without pct: every check is unknown or red, none
        // is green
        let ok = dir.join("site2.json");
        fs::write(&ok, full).unwrap();
        let (_, r) = site_checks(&ok);
        assert_eq!(r.len(), 5);
        if Command::new("pct").arg("--version").output().is_err() {
            assert!(r.iter().all(|x| x.outcome != Outcome::Green), "{r:?}");
            assert_ne!(verify::exit_code(&r), 0);
        }
        let _ = fs::remove_dir_all(&dir);
    }
}
