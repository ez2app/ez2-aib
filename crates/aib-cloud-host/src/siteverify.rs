// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site verify [<code>] [--records dir] [--thin vg/pool] [--json]` — the
//! passive half (4.4.1 §12; check list: c477a). Reads host files and runs read-only
//! commands with a timeout each (host H01–H10, H12–H15; per site S00–S06, S10–S16), hands what it saw to `aib_cloud::verify`, prints the
//! verdicts. Nothing here writes, save one line per Cloudflare call in the Cloudflare
//! ledger; `--active` checks are a later slice. One check asks Cloudflare: S15 lists the
//! customer's zone, read only (`crate::cfdns::proxied_names`), to see that every record
//! under the site's names goes through the proxy; a customer's status (`customer`) never
//! runs it. A site whose names span two zones is unchecked (`multi_zone`). Two checks enter a site's container: S14 asks its scheduler timer's state and
//! lists the site software's scheduled events as the site's own user, without the
//! customer's plugins and theme, each with a time limit and a cap on what is read; S16
//! tests whether a platform template's own files are there (names only); S17 reads
//! nginx's whole configuration and each PHP-FPM's `expose_php` (E6 S7), for `site verify`
//! only, not a customer's status; S18 (`crate::siteusercron`) asks whether the site's user
//! may keep a schedule with cron, under the same rules as S17. Nothing is run inside a
//! quarantined container: S14, S16, S17 and S18 are left out for one.
//!
//! "Could not observe" is passed on as `None`, and `aib_cloud::verify` turns that into
//! unknown, never green. Exit status: 0 all green, 1 any red, 3 any unknown, 2 warnings
//! only. Output carries check ids and short codes, not what was read.

use std::fs;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aib_cloud::quarantine;
use aib_cloud::roster::{ENTRY_ACCOUNT, SUDOERS_PATH};
use aib_cloud::site::spec_from_record_strict;
use aib_cloud::verify::{self, CheckResult, DiskUse, EntrySudoers, Outcome, SiteState};
use serde_json::{json, Value};

use crate::probe::run_capture;
use crate::sitecreatecmd::{Runner, CT_OUT_MAX};
use crate::siteidentity::{cron_unit, in_ct, wp};

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const THIN_POOL: &str = "vg0/data";
const LAST_PLATFORM_OK: &str = "/var/lib/ez2-cloud/last-platform-ok";
const LAST_EXPORT_OK: &str = "/var/lib/ez2-cloud/last-export-ok";
const DUMP_DIR: &str = "/var/lib/vz";
const CLUSTER_FW: &str = "/etc/pve/firewall/cluster.fw";
const HOST_HEALTH: &str = "/run/ez2-host-health.json";
const REBOOT_REQUIRED: &str = "/var/run/reboot-required";
/// How long one read inside a site's container may take (S14).
const CT_SECONDS: u64 = 20;
/// The site software's scheduled events, when each is due. The events are stored with the
/// site: none of the customer's plugins or theme has to run to list them.
pub(crate) const CRON_LIST: [&str; 7] = [
    "cron",
    "event",
    "list",
    "--fields=time",
    "--format=csv",
    "--skip-plugins",
    "--skip-themes",
];

/// Reads inside a site's container: a time limit, a cap on what is read, nothing fed in.
struct CtReader;

impl Runner for CtReader {
    fn run(&mut self, argv: &[String]) -> Result<String, String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args).env("LC_ALL", "C");
        crate::probe::run_bounded_input(&mut cmd, Duration::from_secs(CT_SECONDS), CT_OUT_MAX, b"")
    }
}

/// Whether S14 is looked at for a site in this state: nothing is run inside a quarantined
/// container (Andy 11-10).
fn s14_runs_in(state: SiteState) -> bool {
    state != SiteState::Quarantined
}

/// S14 for one site: its scheduler timer's state and its scheduled events, read inside its
/// container, judged by `aib_cloud::verify::s14_wp_cron` against `red_after` seconds.
/// Whatever could not be read is passed on as `None`.
pub(crate) fn s14_check(
    runner: &mut impl Runner,
    spec: &aib_cloud::site::SiteSpec,
    now: u64,
    red_after: u64,
) -> CheckResult {
    let timer = format!("{}.timer", cron_unit(&spec.code));
    let shown = runner
        .run(&in_ct(
            spec.ct,
            &[
                "systemctl",
                "show",
                &timer,
                "-p",
                "LoadState",
                "-p",
                "UnitFileState",
                "-p",
                "ActiveState",
            ],
        ))
        .ok();
    let events = runner.run(&wp(spec, &CRON_LIST)).ok();
    verify::s14_wp_cron(now, red_after, shown.as_deref(), events.as_deref())
}

/// S16 for one site: whether a file a platform template leaves (`mask::PLATFORM_FILES`)
/// is inside its container, judged by `aib_cloud::verify::s16_no_platform_file`. Only
/// names are tested; nothing is read.
pub(crate) fn s16_check(runner: &mut impl Runner, ct: u32) -> CheckResult {
    let mut args: Vec<&str> = vec!["sh", "-c", verify::S16_SCRIPT, "sh"];
    args.extend(aib_cloud::mask::PLATFORM_FILES);
    let listing = runner.run(&in_ct(ct, &args)).ok();
    verify::s16_no_platform_file(listing.as_deref())
}

/// S17 for one site (E6 S7): whether nginx and PHP-FPM name their versions in the site's
/// answers, read inside its container and judged by
/// `aib_cloud::verify::s17_no_version_banner`. For `site verify` only, as S15 is (c477a
/// S7 L1): most sites were made from a template that does not turn these off yet, so a
/// customer would see a red check they cannot fix themselves. Whether to show it to
/// customers is f1fae's / Andy's call once the template is rebuilt and the older sites
/// are brought up to it.
pub(crate) fn s17_check(runner: &mut impl Runner, ct: u32) -> CheckResult {
    let listing = runner
        .run(&in_ct(ct, &["sh", "-c", verify::S17_SCRIPT]))
        .ok();
    verify::s17_no_version_banner(listing.as_deref())
}

/// S15 (E6 D1): every address record under the site's names is proxied at Cloudflare, so a
/// lookup of the name never gives the host's own address. A grey record is red; anything
/// that kept the zone from being read whole is unknown, never green. Also the grey names
/// (names only, never what the records hold), for `site verify` to print; a customer's
/// status does not show them.
pub(crate) fn s15_dns_proxied(
    found: Result<aib_cloud::sitedns::Proxied, String>,
) -> (CheckResult, Vec<String>) {
    use aib_cloud::sitedns::{self, Proxied};
    let mut grey = Vec::new();
    let outcome = match found {
        Ok(Proxied::All) => Outcome::Green,
        Ok(Proxied::Grey(names)) => {
            grey = names;
            Outcome::Red("dns_not_proxied")
        }
        Ok(Proxied::Unchecked(word)) => Outcome::Unknown(word),
        Err(why) => Outcome::Unknown(
            [
                crate::cfdns::KEY_UNSET,
                sitedns::KEY_REFUSED,
                sitedns::ACCESS_REVOKED,
                sitedns::OWNER_UNKNOWN,
                sitedns::ZONE_NOT_FOUND,
                sitedns::ZONE_AMBIGUOUS,
                sitedns::NAME_OUTSIDE_ZONE,
                sitedns::CALL_NOT_ALLOWED,
                sitedns::UNREADABLE,
            ]
            .into_iter()
            .find(|w| why.starts_with(w))
            .unwrap_or("dns_unread"),
        ),
    };
    (CheckResult { id: "S15", outcome }, grey)
}

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

const VISUDO: &str = "/usr/sbin/visudo";
const SUDOERS_MAIN: &str = "/etc/sudoers";

/// Every sudoers file sudo reads besides `ours`: the main file and the files of its
/// directory, less those sudo skips (a name with a `.` or ending in `~`). `None` when any
/// of them cannot be read: a file left out could hold the rule H14 looks for.
fn other_sudoers(main: &str, ours: &str) -> Option<Vec<String>> {
    let ours = std::path::Path::new(ours);
    let dir = ours.parent()?;
    let mut out = vec![fs::read_to_string(main).ok()?];
    let mut names: Vec<std::path::PathBuf> = Vec::new();
    for entry in fs::read_dir(dir).ok()? {
        let path = entry.ok()?.path();
        let name = path.file_name()?.to_str()?.to_owned();
        if path == ours || name.contains('.') || name.ends_with('~') {
            continue;
        }
        names.push(path);
    }
    names.sort();
    for p in names {
        out.push(fs::read_to_string(p).ok()?);
    }
    Some(out)
}

/// The entry account's uid and its groups, each by name and as `#<gid>` (sudoers takes
/// `%name` and `%#gid`), from `id`.
fn account_of(name: &str) -> Option<(u32, Vec<String>)> {
    let uid = observe("id", &["-u", name], 3)?.trim().parse().ok()?;
    let mut groups: Vec<String> = observe("id", &["-nG", name], 3)?
        .split_whitespace()
        .map(str::to_owned)
        .collect();
    for gid in observe("id", &["-G", name], 3)?.split_whitespace() {
        gid.parse::<u32>().ok()?;
        groups.push(format!("#{gid}"));
    }
    Some((uid, groups))
}

/// H14's observation of the entry account's sudoers file. `visudo -c -f` that cannot be
/// found is "could not look"; one that ran and did not say yes (or timed out) is "no".
fn entry_sudoers(path: &str, main: &str) -> verify::CheckResult {
    use std::os::unix::fs::MetadataExt;
    let meta = fs::symlink_metadata(path);
    let present = match &meta {
        Ok(_) => Some(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(false),
        Err(_) => None,
    };
    // a symlink is not "the file": its own mode says nothing, so it reads as a wrong mode
    let owner_mode = meta.ok().map(|m| {
        (
            m.uid(),
            m.gid(),
            if m.file_type().is_file() {
                m.mode() & 0o7777
            } else {
                0
            },
        )
    });
    let text = fs::read_to_string(path).ok();
    let visudo_ok = fs::exists(VISUDO)
        .ok()
        .filter(|there| *there)
        .map(|_| observe(VISUDO, &["-c", "-f", path], 5).is_some());
    let others = other_sudoers(main, path);
    let nsswitch = match fs::read_to_string("/etc/nsswitch.conf") {
        Ok(t) => Some(t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(String::new()),
        Err(_) => None,
    };
    let account = account_of(ENTRY_ACCOUNT);
    verify::h14_entry_sudoers(EntrySudoers {
        present,
        owner_mode,
        text: text.as_deref(),
        visudo_ok,
        others: others.as_deref(),
        account: account.as_ref().map(|(u, g)| (*u, g.as_slice())),
        nss_files_only: verify::nss_sudoers_files_only(nsswitch.as_deref()),
    })
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
        verify::h01_health_fresh(now, mtime(HOST_HEALTH)),
        verify::h06_load(
            fs::read_to_string("/proc/loadavg").ok().as_deref(),
            std::thread::available_parallelism()
                .ok()
                .and_then(|n| u32::try_from(n.get()).ok())
                .unwrap_or(0),
        ),
        verify::h07_reboot_required(fs::exists(REBOOT_REQUIRED).ok()),
        verify::h15_dmesg_restrict(
            fs::read_to_string("/proc/sys/kernel/dmesg_restrict")
                .ok()
                .as_deref(),
        ),
        verify::h10_fail2ban(observe("fail2ban-client", &["status", "sshd"], 5).as_deref()),
        verify::h12_time_sync(
            observe(
                "timedatectl",
                &["show", "-p", "NTPSynchronized", "--value"],
                3,
            )
            .as_deref(),
        ),
        entry_sudoers(SUDOERS_PATH, SUDOERS_MAIN),
    ]
}

/// The checks of one site, from its record file. A record that is not complete is one red
/// finding (`S00`): nothing else can be judged against values nobody recorded.
/// These are the checks a customer's status is made of (`customer` reads them): S15 is not
/// among them, see [`Audience`].
pub(crate) fn site_checks(
    record_path: &std::path::Path,
) -> (String, Option<u32>, Vec<CheckResult>) {
    let (name, ct, results, _) = site_checks_for(
        record_path,
        Audience::Customer,
        &mut crate::sitecreatecmd::HostRunner,
        &cf_paths(record_path),
    );
    (name, ct, results)
}

/// Who a site's checks are for. S15 asks Cloudflare with our one key: a customer (or their
/// AI) asking `status` in a loop would spend that key's rate limit for every tenant, grow
/// the Cloudflare ledger and wait on Cloudflare. So only `site verify` (ours) looks at
/// S15; a customer's status asks Cloudflare nothing (c477a, S11 M1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Audience {
    Customer,
    Operator,
}

/// The host's Cloudflare paths, with the site records read from where this record is.
fn cf_paths(record_path: &std::path::Path) -> crate::sitecreatecmd::HostPaths {
    let mut paths = crate::sitecreatecmd::HostPaths::host();
    if let Some(dir) = record_path.parent() {
        paths.records = dir.to_path_buf();
    }
    paths
}

/// One site's checks: its code, its CT, the results, and the names S15 found not proxied.
type SiteChecks = (String, Option<u32>, Vec<CheckResult>, Vec<String>);

/// [`site_checks`] for `audience`, with the names S15 found not proxied. `cf` and
/// `cf_paths` are what S15 asks Cloudflare through.
fn site_checks_for(
    record_path: &std::path::Path,
    audience: Audience,
    cf: &mut impl Runner,
    cf_paths: &crate::sitecreatecmd::HostPaths,
) -> SiteChecks {
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
        return (name, None, s00("record_unreadable"), Vec::new());
    };
    let Ok(spec) = spec_from_record_strict(&rec) else {
        return (name, None, s00("record_incomplete"), Vec::new());
    };
    if spec.code != name {
        return (name, None, s00("record_name_mismatch"), Vec::new());
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
    // S06: everything `site drift` compares. For a quarantined site the firewall file is
    // meant to differ from the record (S04 checks it is the quarantine one), so that one
    // key is left out of the count.
    let drifted = config.as_deref().and_then(|cfg| {
        let all = aib_cloud::site::drift(&spec, cfg, ct_fw.as_deref()).ok()?;
        Some(
            all.iter()
                .filter(|d| !(state == SiteState::Quarantined && d.key == "firewall"))
                .count(),
        )
    });
    let mut results = vec![
        verify::s01_ct_status(state, status.as_deref()),
        verify::s02_unprivileged(config.as_deref()),
        verify::s03_firewall_attached(config.as_deref(), ct_fw.as_deref()),
        verify::s04_firewall_group(&spec, state, quarantine::GROUP, ct_fw.as_deref()),
        verify::s05_limits(&spec, config.as_deref()),
        verify::s06_drift(drifted),
        // the mask (4.5 section 7): the hook line, what the hook said when the CT started,
        // and what else of the CT's configuration would name the platform
        verify::s10_masked(
            config.as_deref(),
            match status.as_deref().map(str::trim) {
                Some("status: running") => Some(true),
                Some("status: stopped") => Some(false),
                _ => None,
            },
            fs::read_to_string(format!("{}/{ct}/result", crate::maskcmd::STATE))
                .ok()
                .as_deref(),
        ),
        verify::s11_resolver(config.as_deref()),
        verify::s12_hwaddr(config.as_deref()),
        verify::s13_no_ipv6(config.as_deref()),
    ];
    if s14_runs_in(state) {
        results.push(s14_check(
            &mut CtReader,
            &spec,
            crate::probe::unix_now(),
            verify::WP_CRON_OVERDUE_RED,
        ));
        // S16 enters the container too: not a quarantined one
        results.push(s16_check(&mut CtReader, spec.ct));
    }
    // S17 enters the container too: not a quarantined one. `site verify` only for now (see
    // s17_check): never a customer's status
    if audience == Audience::Operator && s14_runs_in(state) {
        results.push(s17_check(&mut CtReader, spec.ct));
        // S18 (E10 6.1): the site's user can keep a schedule with cron; same rules as S17
        results.push(crate::siteusercron::s18_check(&mut CtReader, &spec));
    }
    // S15 reads Cloudflare only, so it is looked at for a quarantined site too; never for
    // a customer's status
    let mut grey = Vec::new();
    if audience == Audience::Operator {
        let (s15, names) = s15_dns_proxied(crate::cfdns::proxied_names(cf, cf_paths, &spec, &rec));
        results.push(s15);
        grey = names;
    }
    (name, Some(spec.ct), results, grey)
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

/// `site verify`. Anything that keeps the checks from being made — the records directory
/// cannot be read, there are no records, an argument is wrong — exits **3** like any other
/// "could not observe": this command's 2 means "warnings only", and "no site was looked
/// at" must never read as that (c477a, #625 seg. 3 M1).
pub fn cmd(args: &[String]) -> Result<u8, String> {
    match run(args) {
        Ok(code) => Ok(code),
        Err(why) => {
            eprintln!("aib-cloud-host site verify: {why}");
            Ok(3)
        }
    }
}

fn run(args: &[String]) -> Result<u8, String> {
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

    let mut host = host_checks(now, &thin_pool);
    let named: Vec<SiteChecks> = paths
        .iter()
        .map(|p| {
            site_checks_for(
                p,
                Audience::Operator,
                &mut crate::sitecreatecmd::HostRunner,
                &cf_paths(p),
            )
        })
        .collect();
    // S15's grey names, per site: shown by `site verify` only
    let grey: Vec<(String, Vec<String>)> = named
        .iter()
        .filter(|(_, _, _, g)| !g.is_empty())
        .map(|(n, _, _, g)| (n.clone(), g.clone()))
        .collect();
    let checked: Vec<(String, Option<u32>, Vec<CheckResult>)> =
        named.into_iter().map(|(n, ct, r, _)| (n, ct, r)).collect();
    // the inventory only makes sense over every record, not when one site was asked for
    if only.is_none() {
        let record_cts: Vec<u32> = checked.iter().filter_map(|(_, ct, _)| *ct).collect();
        let cts = observe("pct", &["list"], 5).and_then(|o| verify::parse_pct_list(&o));
        host.push(verify::h13_inventory(
            cts.as_deref(),
            &record_cts,
            &[aib_cloud::site::EDGE_CT, aib_cloud::site::TEMPLATE_CT],
        ));
    }
    let sites: Vec<(String, Vec<CheckResult>)> =
        checked.into_iter().map(|(n, _, r)| (n, r)).collect();
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
            "not_proxied": grey.iter().map(|(code, g)| json!({"site": code, "names": g})).collect::<Vec<_>>(),
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
        for (code, names) in &grey {
            eprintln!("S15 {code} not proxied: {}", names.join(" "));
        }
        if s17_red_anywhere(&sites) {
            eprintln!("{}", s17_template_hint());
        }
        eprintln!(
            "{} checks on the host and {} site(s): exit {exit}",
            all.len(),
            sites.len()
        );
    }
    Ok(exit)
}

/// Whether any site's S17 is red: `site verify` then prints [`s17_template_hint`].
fn s17_red_anywhere(sites: &[(String, Vec<CheckResult>)]) -> bool {
    sites
        .iter()
        .flat_map(|(_, r)| r)
        .any(|r| r.id == "S17" && matches!(r.outcome, Outcome::Red(_)))
}

/// What the site template is to carry so new sites start with S17 green (E6 S7): a plan
/// for when the template is next rebuilt. Nothing here writes it; an existing site is
/// changed by its own reviewed approval, not by this.
fn s17_template_hint() -> String {
    format!(
        "S17 plan for site template CT {} (next rebuild, not written here):\n  {}",
        aib_cloud::site::TEMPLATE_CT,
        verify::S17_TEMPLATE_LINES.join("\n  ")
    )
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
    fn nothing_checked_is_exit_3_never_2() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert_eq!(cmd(&args(&["--records", "/nonexistent/aib-sv"])), Ok(3));
        assert_eq!(cmd(&args(&["--no-such-flag"])), Ok(3));
        assert_eq!(cmd(&args(&["Bad Code"])), Ok(3));
        let empty = std::env::temp_dir().join(format!("aib-sv-empty-{}", std::process::id()));
        fs::create_dir_all(&empty).unwrap();
        assert_eq!(cmd(&args(&["--records", &empty.to_string_lossy()])), Ok(3));
        let _ = fs::remove_dir_all(&empty);
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
        let (name, _, r) = site_checks(&missing);
        assert_eq!(name, "nosuch");
        assert_eq!(r.len(), 1);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_unreadable"))
        );
        // a record the lenient reader accepts but that leaves the limits to a default
        let poc = dir.join("site2.json");
        fs::write(&poc, r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112}"#).unwrap();
        let (_, _, r) = site_checks(&poc);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_incomplete"))
        );
        // a complete record under another site's file name
        let full = r#"{"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"s2.example.test","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112,"limits":{"cores":2,"cpulimit_x100":200,"memory_mb":2048,"swap_mb":512,"pids_max":2048}}"#;
        let wrong = dir.join("site9.json");
        fs::write(&wrong, full).unwrap();
        let (_, _, r) = site_checks(&wrong);
        assert_eq!(
            (r[0].id, r[0].outcome),
            ("S00", Outcome::Red("record_name_mismatch"))
        );
        // a complete record on a machine without pct: every check is unknown or red, none
        // is green
        let ok = dir.join("site2.json");
        fs::write(&ok, full).unwrap();
        let (_, _, r) = site_checks(&ok);
        // S00–S06, S10–S14 and S16; S15 asks Cloudflare and S17 is not yet shown to
        // customers, both for `site verify` only (c477a S7 L1)
        assert_eq!(r.len(), 12, "S00–S06, S10–S14 and S16");
        assert!(r.iter().all(|x| x.id != "S17"), "{r:?}");
        // a site's checks are what a customer's status is made of (`customer` reads
        // site_checks): the host's H14 is never among them (f1fae, #642)
        assert!(r.iter().all(|x| x.id.starts_with('S')), "{r:?}");
        if Command::new("pct").arg("--version").output().is_err() {
            assert!(r.iter().all(|x| x.outcome != Outcome::Green), "{r:?}");
            assert_ne!(verify::exit_code(&r), 0);
        }
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn h14_reads_the_file_it_is_given() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("aib-sv-h14-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let d = dir.join("sudoers.d");
        fs::create_dir_all(&d).unwrap();
        let main = dir.join("sudoers");
        let main = main.to_str().unwrap();
        let file = d.join("ez2-entry");
        let path = file.to_str().unwrap();
        // not set up yet
        assert_eq!(
            entry_sudoers(path, main).outcome,
            Outcome::Warn("entry_not_set_up")
        );
        // there, but not root:root 0440 (a test runs as a user; 0644 is wrong either way)
        fs::write(&file, aib_cloud::roster::sudoers_file(&[]).unwrap()).unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            entry_sudoers(path, main).outcome,
            Outcome::Red("sudoers_mode")
        );
        // a symlink to it is not the file
        let link = dir.join("link");
        std::os::unix::fs::symlink(&file, &link).unwrap();
        assert_eq!(
            entry_sudoers(link.to_str().unwrap(), main).outcome,
            Outcome::Red("sudoers_mode")
        );
        // the other files: the main one missing is "could not read"; a file sudo skips
        // (a dot in its name, a trailing ~) is not read; ours is not among them
        assert_eq!(other_sudoers(main, path), None);
        fs::write(main, "root ALL=(ALL) ALL\n").unwrap();
        fs::write(d.join("zz"), "b\n").unwrap();
        fs::write(d.join("aa"), "a\n").unwrap();
        fs::write(d.join("aa.bak"), "skipped\n").unwrap();
        fs::write(d.join("aa~"), "skipped\n").unwrap();
        assert_eq!(
            other_sudoers(main, path).unwrap(),
            ["root ALL=(ALL) ALL\n", "a\n", "b\n"]
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// A container that answers each command from a table, and keeps what was asked.
    struct Ct {
        asked: Vec<String>,
        show: Result<&'static str, ()>,
        events: Result<&'static str, ()>,
    }

    impl Runner for Ct {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.asked.push(line.clone());
            let answer = if line.contains(" systemctl show ") {
                self.show
            } else if line.contains(" cron event list ") {
                self.events
            } else {
                Err(())
            };
            answer.map(str::to_owned).map_err(|()| "failed".to_owned())
        }
    }

    #[test]
    fn s16_asks_only_whether_the_platform_files_are_there() {
        struct Ls(Result<&'static str, ()>, Vec<Vec<String>>);
        impl Runner for Ls {
            fn run(&mut self, argv: &[String]) -> Result<String, String> {
                self.1.push(argv.to_vec());
                self.0.map(str::to_owned).map_err(|()| "failed".to_owned())
            }
        }
        let mut ct = Ls(Ok("ez2-s16-end\n"), Vec::new());
        assert_eq!(s16_check(&mut ct, 131).outcome, Outcome::Green);
        let argv = &ct.1[0];
        assert_eq!(
            &argv[..8],
            [
                "pct",
                "exec",
                "131",
                "--",
                "sh",
                "-c",
                verify::S16_SCRIPT,
                "sh"
            ]
        );
        assert_eq!(&argv[8..], aib_cloud::mask::PLATFORM_FILES);
        ct.0 = Ok("/etc/appliance.info\nez2-s16-end\n");
        assert_eq!(
            s16_check(&mut ct, 131).outcome,
            Outcome::Red("platform_file")
        );
        ct.0 = Err(());
        assert_eq!(s16_check(&mut ct, 131).outcome, Outcome::Unknown("unread"));
    }

    #[test]
    fn s17_reads_nginx_and_php_inside_the_container() {
        struct Sh(Result<String, ()>, Vec<Vec<String>>);
        impl Runner for Sh {
            fn run(&mut self, argv: &[String]) -> Result<String, String> {
                self.1.push(argv.to_vec());
                self.0.clone().map_err(|()| "failed".to_owned())
            }
        }
        let good = format!(
            "{}\nexpose_php => Off => Off\n{}\n{}\n# configuration file /etc/nginx/nginx.conf:\nhttp {{\n    server_tokens off;\n}}\n{}\n",
            verify::S17_PHP,
            verify::S17_POOL,
            verify::S17_NGINX,
            verify::S17_END
        );
        let mut ct = Sh(Ok(good.clone()), Vec::new());
        assert_eq!(s17_check(&mut ct, 131).outcome, Outcome::Green);
        assert_eq!(
            ct.1[0],
            ["pct", "exec", "131", "--", "sh", "-c", verify::S17_SCRIPT]
        );
        ct.0 = Ok(good.replace("expose_php => Off => Off", "expose_php => On => On"));
        assert_eq!(
            s17_check(&mut ct, 131).outcome,
            Outcome::Red("expose_php_on")
        );
        ct.0 = Err(());
        assert_eq!(s17_check(&mut ct, 131).outcome, Outcome::Unknown("unread"));
        // only a red S17 brings the template plan, which names the template CT
        let red = [(
            "site2".to_owned(),
            vec![s17_check(
                &mut Sh(
                    Ok(good.replace("server_tokens off", "server_tokens on")),
                    Vec::new(),
                ),
                131,
            )],
        )];
        assert!(s17_red_anywhere(&red));
        assert!(!s17_red_anywhere(&[(
            "site2".to_owned(),
            vec![s17_check(&mut Sh(Ok(good), Vec::new()), 131)]
        )]));
        let hint = s17_template_hint();
        assert!(hint.contains("server_tokens off;") && hint.contains("expose_php = Off"));
        assert!(hint.contains(&aib_cloud::site::TEMPLATE_CT.to_string()));
    }

    fn spec() -> aib_cloud::site::SiteSpec {
        aib_cloud::site::SiteSpec::new(
            "s-wpcron",
            131,
            31,
            &["new.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    #[test]
    fn s14_is_not_run_in_a_quarantined_container() {
        assert!(!s14_runs_in(SiteState::Quarantined));
        assert!(s14_runs_in(SiteState::Active));
        assert!(s14_runs_in(SiteState::Suspended));
    }

    const ON: &str = "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n";

    #[test]
    fn s14_reads_the_timer_and_the_events_inside_the_container() {
        let now = 1_000_000;
        let mut ct = Ct {
            asked: Vec::new(),
            show: Ok(ON),
            events: Ok("time\n999000\n1000100\n"),
        };
        let r = s14_check(&mut ct, &spec(), now, 3600);
        assert_eq!((r.id, r.outcome), ("S14", Outcome::Green));
        assert_eq!(
            ct.asked[0],
            "pct exec 131 -- systemctl show wpcron-s-wpcron.timer -p LoadState -p UnitFileState -p ActiveState"
        );
        // as the site's own user, without the customer's plugins and theme
        assert_eq!(
            ct.asked[1],
            "pct exec 131 -- sudo -u s-wpcron -H /usr/local/bin/wp --path=/var/www/s-wpcron cron event list --fields=time --format=csv --skip-plugins --skip-themes"
        );
        // an event an hour and more late is red, with the limit the caller gives
        ct.events = Ok("time\n990000\n");
        assert_eq!(
            s14_check(&mut ct, &spec(), now, 3600).outcome,
            Outcome::Red("overdue")
        );
        assert_eq!(
            s14_check(&mut ct, &spec(), now, 20_000).outcome,
            Outcome::Green
        );
        // what could not be read is unknown, not green
        ct.events = Err(());
        assert_eq!(
            s14_check(&mut ct, &spec(), now, 3600).outcome,
            Outcome::Unknown("events_unread")
        );
        ct.events = Ok("time\n");
        ct.show = Err(());
        assert_eq!(
            s14_check(&mut ct, &spec(), now, 3600).outcome,
            Outcome::Unknown("timer_unread")
        );
    }

    #[test]
    fn s15_is_red_for_a_grey_name_and_unknown_when_unread() {
        use aib_cloud::sitedns::{self, Proxied};
        let outcome = |found: Result<Proxied, String>| s15_dns_proxied(found).0.outcome;
        let (r, grey) = s15_dns_proxied(Ok(Proxied::All));
        assert_eq!((r.id, r.outcome, grey.len()), ("S15", Outcome::Green, 0));
        let www = vec!["www.example.test".to_owned()];
        let (r, grey) = s15_dns_proxied(Ok(Proxied::Grey(www.clone())));
        assert_eq!((r.outcome, grey), (Outcome::Red("dns_not_proxied"), www));
        assert_eq!(
            outcome(Ok(Proxied::Unchecked(sitedns::NO_ADDRESS))),
            Outcome::Unknown(sitedns::NO_ADDRESS)
        );
        // what kept the zone from being read keeps its word, and is never green
        assert_eq!(
            outcome(Err(format!("{}: zones", sitedns::ACCESS_REVOKED))),
            Outcome::Unknown(sitedns::ACCESS_REVOKED)
        );
        assert_eq!(
            outcome(Err(format!("{}: no file", crate::cfdns::KEY_UNSET))),
            Outcome::Unknown(crate::cfdns::KEY_UNSET)
        );
        assert_eq!(
            outcome(Err("the site has no name".to_owned())),
            Outcome::Unknown("dns_unread")
        );
    }

    /// Counts what would have gone to curl, and lets nothing through.
    struct NoCurl {
        fed: usize,
    }

    impl Runner for NoCurl {
        fn run(&mut self, _argv: &[String]) -> Result<String, String> {
            Err("not here".to_owned())
        }
        fn run_input(&mut self, _argv: &[String], _input: &[u8]) -> Result<String, String> {
            self.fed += 1;
            Err("no curl in a test".to_owned())
        }
    }

    #[test]
    fn a_customer_status_asks_cloudflare_nothing() {
        let dir = std::env::temp_dir().join(format!("aib-sv-s15-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let paths = crate::sitecreatecmd::HostPaths {
            creating: dir.join("creating"),
            lxc_conf: dir.join("lxc"),
            firewall: dir.join("firewall"),
            secrets: dir.join("secrets"),
            site_admins: dir.join("site-admins"),
            setup: dir.join("ez2-site-setup"),
            package: dir.join("wp-package"),
            package_conf: dir.join("wp-package.conf"),
            records: dir.join("records"),
            sites_list: dir.join("sites.list"),
            keys: dir.join("keys.env"),
            opened: dir.join("opened"),
            cloudflare: dir.join("cloudflare.env"),
            rosters: dir.join("rosters"),
            cf_ledger: dir.join("log").join("cloudflare.jsonl"),
        };
        fs::create_dir_all(&paths.records).unwrap();
        fs::create_dir_all(&paths.rosters).unwrap();
        // a key, a roster with an account and a complete record naming its customer: all
        // S15 needs to ask
        fs::write(
            &paths.cloudflare,
            "CF_AUTH_EMAIL=ops@example.test\nCF_GLOBAL_KEY=0123456789abcdef0123456789abcdef01234\n",
        )
        .unwrap();
        fs::write(
            paths.rosters.join("t-aaa.json"),
            r#"{"tenant_id":"t-aaa","cloudflare_accounts":["0123456789abcdef0123456789abcdef"],"members":[]}"#,
        )
        .unwrap();
        let site = spec();
        let record = paths.records.join(format!("{}.json", site.code));
        let mut rec = aib_cloud::site::record(&site, "2026-11-12T00:00:00Z");
        rec["tenant_id"] = json!("t-aaa");
        fs::write(&record, rec.to_string()).unwrap();
        let mut cf = NoCurl { fed: 0 };
        for _ in 0..2 {
            let (_, _, r, grey) = site_checks_for(&record, Audience::Customer, &mut cf, &paths);
            assert!(r.iter().all(|x| x.id != "S15"), "{r:?}");
            assert!(grey.is_empty());
        }
        assert_eq!(cf.fed, 0, "a customer's status asked Cloudflare");
        assert!(
            !paths.cf_ledger.exists(),
            "a customer's status wrote the ledger"
        );
        // the same record for `site verify` does ask (so the above is not luck)
        let (_, _, r, _) = site_checks_for(&record, Audience::Operator, &mut cf, &paths);
        assert!(cf.fed > 0);
        assert!(r
            .iter()
            .any(|x| x.id == "S15" && matches!(x.outcome, Outcome::Unknown(_))));
        let _ = fs::remove_dir_all(&dir);
    }
}
