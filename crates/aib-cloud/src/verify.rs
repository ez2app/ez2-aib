// SPDX-License-Identifier: Apache-2.0
//! `site verify` as decisions (4.4.1 §12; check list: SPEC-site-verify-checks, c477a
//! 11-10). The host shell reads files and runs read-only commands; this module is handed
//! what was observed and says green, warn, red or unknown for one check at a time. No I/O.
//!
//! Rules that hold for every check:
//! * **Unknown is not green.** A command that timed out, a file that could not be read or
//!   text that does not parse gives [`Outcome::Unknown`]; the caller passes `None` for
//!   "could not observe".
//! * **What is expected comes from the site record**, read with
//!   [`crate::site::spec_from_record_strict`]: a value nobody recorded is not an
//!   expectation.
//! * **The site's state turns expectations round**: a suspended or quarantined site whose
//!   CT is running is red.
//! * Results carry a short code, never the observed text: reports leave the host.
//!
//! This first slice holds the passive host checks H02–H05, H08, H09 and the passive site
//! checks S01–S05. The remaining passive checks and the `--active` ones follow.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::{json, Value};

use crate::site::{parse_pct_config, SiteSpec};

/// What one check found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// As expected.
    Green,
    /// Worth a look; not a failure.
    Warn(&'static str),
    /// Not as expected.
    Red(&'static str),
    /// Could not be observed or understood. Never treated as green.
    Unknown(&'static str),
}

/// One line of a report.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    /// Check id (`H04`, `S03`, …).
    pub id: &'static str,
    /// What it found.
    pub outcome: Outcome,
}

/// The state a site is recorded to be in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SiteState {
    /// Serving.
    Active,
    /// Stopped for business reasons; the edge shows the suspended page.
    Suspended,
    /// Cut off for safety: no route, no egress.
    Quarantined,
}

/// Exit status of a run: 1 any red, else 3 any unknown, else 2 any warn, else 0.
pub fn exit_code(results: &[CheckResult]) -> u8 {
    let any = |f: fn(&Outcome) -> bool| results.iter().any(|r| f(&r.outcome));
    if any(|o| matches!(o, Outcome::Red(_))) {
        1
    } else if any(|o| matches!(o, Outcome::Unknown(_))) {
        3
    } else if any(|o| matches!(o, Outcome::Warn(_))) {
        2
    } else {
        0
    }
}

/// The report as JSON: `{"site": …, "exit": n, "checks": [{"id", "result", "code"}]}`.
pub fn report(site: Option<&str>, results: &[CheckResult]) -> Value {
    let checks: Vec<Value> = results
        .iter()
        .map(|r| {
            let (result, code) = match r.outcome {
                Outcome::Green => ("green", None),
                Outcome::Warn(c) => ("warn", Some(c)),
                Outcome::Red(c) => ("red", Some(c)),
                Outcome::Unknown(c) => ("unknown", Some(c)),
            };
            json!({ "id": r.id, "result": result, "code": code })
        })
        .collect();
    json!({ "site": site, "exit": exit_code(results), "checks": checks })
}

fn result(id: &'static str, outcome: Outcome) -> CheckResult {
    CheckResult { id, outcome }
}

/// Age of a marker file against two thresholds, all in seconds. `mtime` is `None` when
/// the file is missing: a job that never succeeded is red, not unknown.
fn age(
    id: &'static str,
    now: u64,
    mtime: Option<u64>,
    warn_after: u64,
    red_after: u64,
) -> CheckResult {
    let outcome = match mtime {
        None => Outcome::Red("never"),
        // a marker from the future says the clock is wrong, not that the job is fresh
        Some(t) if t > now.saturating_add(300) => Outcome::Unknown("clock"),
        Some(t) if now.saturating_sub(t) > red_after => Outcome::Red("stale"),
        Some(t) if now.saturating_sub(t) > warn_after => Outcome::Warn("aging"),
        Some(_) => Outcome::Green,
    };
    result(id, outcome)
}

/// H02: the platform backup last succeeded within 26 h (warn after 24 h).
pub fn h02_platform_backup(now: u64, last_ok: Option<u64>) -> CheckResult {
    age("H02", now, last_ok, 24 * 3600, 26 * 3600)
}

/// H03: the customer export last succeeded within 192 h (warn after 168 h).
pub fn h03_customer_export(now: u64, last_ok: Option<u64>) -> CheckResult {
    age("H03", now, last_ok, 168 * 3600, 192 * 3600)
}

/// Percent used, as observed: `None` could not be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DiskUse {
    /// Root file system.
    pub root: Option<u8>,
    /// Backup / dump directory.
    pub dump: Option<u8>,
    /// Thin pool data.
    pub thin_data: Option<u8>,
    /// Thin pool metadata. A full metadata volume turns the whole pool read-only, for
    /// every site at once.
    pub thin_meta: Option<u8>,
}

/// H04: disk and thin pool. Red at 85 % (file systems) / 80 % (pool), warn at 75 %.
pub fn h04_disk(u: DiskUse) -> CheckResult {
    let all = [
        (u.root, 85, "root_full"),
        (u.dump, 85, "dump_full"),
        (u.thin_data, 80, "thin_data_full"),
        (u.thin_meta, 80, "thin_meta_full"),
    ];
    if let Some((_, _, code)) = all.iter().find(|(v, red, _)| v.is_some_and(|v| v >= *red)) {
        return result("H04", Outcome::Red(code));
    }
    if all.iter().any(|(v, _, _)| v.is_none()) {
        return result("H04", Outcome::Unknown("unread"));
    }
    if all.iter().any(|(v, _, _)| v.is_some_and(|v| v >= 75)) {
        return result("H04", Outcome::Warn("filling"));
    }
    result("H04", Outcome::Green)
}

/// H05: software RAID, from `/proc/mdstat`. A missing member (`[U_]`) is red; a resync or
/// recovery in progress is a warning; no array at all is unknown (this host has arrays).
pub fn h05_raid(mdstat: Option<&str>) -> CheckResult {
    let Some(text) = mdstat else {
        return result("H05", Outcome::Unknown("unread"));
    };
    let mut arrays = 0;
    let mut degraded = false;
    for line in text.lines() {
        // status lines end in "[2/2] [UU]"; the last bracket holds one letter per member
        let Some(open) = line.rfind('[') else {
            continue;
        };
        let tail = &line[open + 1..];
        let Some(members) = tail.strip_suffix(']') else {
            continue;
        };
        if members.is_empty() || !members.bytes().all(|c| c == b'U' || c == b'_') {
            continue;
        }
        arrays += 1;
        degraded |= members.contains('_');
    }
    // an array that did not assemble has no member line at all; beside a healthy one it
    // would otherwise go unseen
    let inactive = text.lines().any(|l| l.contains(": inactive"));
    if degraded {
        result("H05", Outcome::Red("degraded"))
    } else if inactive {
        result("H05", Outcome::Red("inactive"))
    } else if arrays == 0 {
        result("H05", Outcome::Unknown("no_array"))
    } else if text.contains("resync") || text.contains("recovery") {
        result("H05", Outcome::Warn("resync"))
    } else {
        result("H05", Outcome::Green)
    }
}

/// H08: `pve-firewall status` says enabled and running.
pub fn h08_firewall_running(status: Option<&str>) -> CheckResult {
    let outcome = match status.map(str::trim) {
        None | Some("") => Outcome::Unknown("unread"),
        Some(s) if s.starts_with("Status: enabled/running") => Outcome::Green,
        Some(_) => Outcome::Red("fw_not_running"),
    };
    result("H08", outcome)
}

/// Value of `key:` inside the `[OPTIONS]` section of a Proxmox firewall file.
fn fw_option<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let mut in_options = false;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            in_options = line.eq_ignore_ascii_case("[OPTIONS]");
            continue;
        }
        if in_options {
            if let Some((k, v)) = line.split_once(':') {
                if k.trim() == key {
                    return Some(v.trim());
                }
            }
        }
    }
    None
}

/// H09: the cluster firewall file turns the firewall on and drops inbound by default.
pub fn h09_cluster_firewall(cluster_fw: Option<&str>) -> CheckResult {
    let Some(text) = cluster_fw else {
        return result("H09", Outcome::Unknown("unread"));
    };
    let outcome = if fw_option(text, "enable") != Some("1") {
        Outcome::Red("cluster_fw_off")
    } else if !matches!(fw_option(text, "policy_in"), None | Some("DROP" | "REJECT")) {
        // not written means the default, which is DROP
        Outcome::Red("policy_in_open")
    } else {
        Outcome::Green
    };
    result("H09", outcome)
}

/// S01: the CT runs exactly when the site is active. `status` is the output of
/// `pct status <ct>` (`status: running`).
pub fn s01_ct_status(state: SiteState, status: Option<&str>) -> CheckResult {
    let running = match status.map(str::trim) {
        Some("status: running") => true,
        Some("status: stopped") => false,
        _ => return result("S01", Outcome::Unknown("unread")),
    };
    let outcome = match (state, running) {
        (SiteState::Active, true) | (SiteState::Suspended | SiteState::Quarantined, false) => {
            Outcome::Green
        }
        (SiteState::Active, false) => Outcome::Red("ct_down"),
        (SiteState::Suspended, true) => Outcome::Red("suspended_but_running"),
        (SiteState::Quarantined, true) => Outcome::Red("quarantined_but_running"),
    };
    result("S01", outcome)
}

fn config(pct_config: Option<&str>) -> Option<Vec<(String, String)>> {
    parse_pct_config(pct_config?).ok()
}

fn get<'a>(cfg: &'a [(String, String)], key: &str) -> Option<&'a str> {
    cfg.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
}

/// S02: the CT is unprivileged and is not a template.
pub fn s02_unprivileged(pct_config: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S02", Outcome::Unknown("unread"));
    };
    let outcome = if get(&cfg, "template") == Some("1") {
        Outcome::Red("is_template")
    } else if get(&cfg, "unprivileged") != Some("1") {
        Outcome::Red("privileged")
    } else {
        Outcome::Green
    };
    result("S02", outcome)
}

/// S03: the firewall is really on for this CT: `firewall=1` on its network card **and**
/// `enable: 1` in its firewall file. Either one missing means the rules do not apply.
pub fn s03_firewall_attached(pct_config: Option<&str>, ct_fw: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S03", Outcome::Unknown("unread"));
    };
    // every card: one `netN` without firewall=1 is a way round all the rules
    let cards: Vec<&str> = cfg
        .iter()
        .filter(|(k, _)| {
            k.strip_prefix("net")
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
        })
        .map(|(_, v)| v.as_str())
        .collect();
    let on_every_card = !cards.is_empty()
        && cards
            .iter()
            .all(|n| n.split(',').any(|p| p == "firewall=1"));
    let outcome = if !on_every_card {
        Outcome::Red("fw_not_on_card")
    } else {
        match ct_fw {
            // no file at all: nothing is enabled
            None => Outcome::Red("fw_file_missing"),
            Some(text) if fw_option(text, "enable") != Some("1") => Outcome::Red("fw_disabled"),
            // with inbound ACCEPT by default a group only adds openings: everything is open.
            // Not written means the default, which is DROP.
            Some(text)
                if !matches!(fw_option(text, "policy_in"), None | Some("DROP" | "REJECT")) =>
            {
                Outcome::Red("ct_policy_open")
            }
            Some(_) => Outcome::Green,
        }
    };
    result("S03", outcome)
}

/// S04: the CT's firewall file points at exactly one group, the one expected for the
/// site's state: the recorded group, or `quarantine_group` for a quarantined site.
pub fn s04_firewall_group(
    spec: &SiteSpec,
    state: SiteState,
    quarantine_group: &str,
    ct_fw: Option<&str>,
) -> CheckResult {
    let Some(text) = ct_fw else {
        return result("S04", Outcome::Unknown("unread"));
    };
    let groups: Vec<&str> = text
        .lines()
        .filter_map(|l| l.trim().strip_prefix("GROUP "))
        .map(str::trim)
        .collect();
    let want = if state == SiteState::Quarantined {
        quarantine_group
    } else {
        spec.fw_group.as_str()
    };
    let outcome = match groups.as_slice() {
        [] => Outcome::Red("no_group"),
        [one] if *one == want => Outcome::Green,
        [_] => Outcome::Red("wrong_group"),
        _ => Outcome::Red("extra_group"),
    };
    result("S04", outcome)
}

/// `cpulimit` as Proxmox writes it (`2`, `0.5`, `1.25`), in hundredths of a core.
fn cpulimit_x100(text: &str) -> Option<u32> {
    let (whole, frac) = text.split_once('.').unwrap_or((text, ""));
    if frac.len() > 2 || !frac.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let whole: u32 = whole.parse().ok()?;
    let frac_x100 = match frac.len() {
        0 => 0,
        1 => frac.parse::<u32>().ok()? * 10,
        _ => frac.parse::<u32>().ok()?,
    };
    whole.checked_mul(100)?.checked_add(frac_x100)
}

/// S05: the CT has the resources the record says. **More** than recorded is red (a site
/// that can take more of the host than agreed); less is a warning. A limit that is absent
/// from the config is unlimited, which is more.
pub fn s05_limits(spec: &SiteSpec, pct_config: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S05", Outcome::Unknown("unread"));
    };
    let l = &spec.limits;
    let number = |key: &str| get(&cfg, key).map(|v| v.parse::<u32>().ok());
    // "no limit" has two more spellings than leaving the key out: `cpulimit: 0` (also the
    // default) and `pids.max: max`. Both are read as absent, which is red below.
    let cpulimit = get(&cfg, "cpulimit")
        .map(cpulimit_x100)
        .filter(|v| *v != Some(0));
    let pids = get(&cfg, "lxc.cgroup2.pids.max")
        .filter(|v| *v != "max")
        .map(|v| v.parse::<u32>().ok());
    let observed = [
        (number("cores"), l.cores),
        (cpulimit, l.cpulimit_x100),
        (number("memory"), l.memory_mb),
        (number("swap"), l.swap_mb),
        (pids, l.pids_max),
    ];
    let mut less = false;
    for (seen, want) in observed {
        match seen {
            None => return result("S05", Outcome::Red("unlimited")),
            Some(None) => return result("S05", Outcome::Unknown("unparsed")),
            Some(Some(v)) if v > want => return result("S05", Outcome::Red("over_record")),
            Some(Some(v)) => less |= v < want,
        }
    }
    let outcome = if less {
        Outcome::Warn("under_record")
    } else {
        Outcome::Green
    };
    result("S05", outcome)
}

/// The state a record or an operator names; anything else is refused.
pub fn parse_state(text: &str) -> Option<SiteState> {
    match text {
        "active" => Some(SiteState::Active),
        "suspended" => Some(SiteState::Suspended),
        "quarantined" => Some(SiteState::Quarantined),
        _ => None,
    }
}

/// The code of an outcome, for callers that log it.
pub fn code(outcome: Outcome) -> String {
    match outcome {
        Outcome::Green => "ok".to_owned(),
        Outcome::Warn(c) | Outcome::Red(c) | Outcome::Unknown(c) => c.to_owned(),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use crate::site::Limits;
    use alloc::string::ToString;

    const HOUR: u64 = 3600;
    const NOW: u64 = 1_800_000_000;

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.test"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
        .with_fw_group("ez2-site-v2")
        .unwrap()
    }

    const CONFIG: &str = "arch: amd64\ncores: 2\ncpulimit: 2\nfeatures: nesting=1\nhostname: site2\nmemory: 2048\nnet0: name=eth0,bridge=vmbr1,firewall=1,ip=10.77.0.12/24\nonboot: 1\nrootfs: local-lvm:vm-112-disk-0,size=24G\nswap: 512\nunprivileged: 1\nlxc.cgroup2.pids.max: 2048\n";
    const CT_FW: &str = "[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: DROP\nipfilter: 1\n\n[RULES]\nGROUP ez2-site-v2\n";

    fn swap(text: &str, from: &str, to: &str) -> String {
        assert!(text.contains(from), "{from:?} not in fixture");
        text.replace(from, to)
    }

    #[test]
    fn exit_code_red_beats_unknown_beats_warn() {
        let r = |o| result("X", o);
        assert_eq!(exit_code(&[]), 0);
        assert_eq!(exit_code(&[r(Outcome::Green)]), 0);
        assert_eq!(exit_code(&[r(Outcome::Green), r(Outcome::Warn("w"))]), 2);
        assert_eq!(
            exit_code(&[r(Outcome::Warn("w")), r(Outcome::Unknown("u"))]),
            3
        );
        assert_eq!(
            exit_code(&[
                r(Outcome::Unknown("u")),
                r(Outcome::Red("r")),
                r(Outcome::Warn("w"))
            ]),
            1
        );
    }

    #[test]
    fn report_carries_codes_only() {
        let results = [
            h08_firewall_running(Some("Status: disabled/running")),
            h05_raid(None),
        ];
        let v = report(Some("site2"), &results);
        assert_eq!(v["site"], "site2");
        assert_eq!(v["exit"], 1);
        assert_eq!(
            v["checks"][0],
            json!({ "id": "H08", "result": "red", "code": "fw_not_running" })
        );
        assert_eq!(
            v["checks"][1],
            json!({ "id": "H05", "result": "unknown", "code": "unread" })
        );
        assert!(!v.to_string().contains("disabled/running"));
    }

    #[test]
    fn h02_h03_ages() {
        assert_eq!(
            h02_platform_backup(NOW, Some(NOW - HOUR)).outcome,
            Outcome::Green
        );
        assert_eq!(
            h02_platform_backup(NOW, Some(NOW - 25 * HOUR)).outcome,
            Outcome::Warn("aging")
        );
        assert_eq!(
            h02_platform_backup(NOW, Some(NOW - 27 * HOUR)).outcome,
            Outcome::Red("stale")
        );
        assert_eq!(
            h02_platform_backup(NOW, None).outcome,
            Outcome::Red("never")
        );
        assert_eq!(
            h02_platform_backup(NOW, Some(NOW + HOUR)).outcome,
            Outcome::Unknown("clock")
        );
        assert_eq!(
            h03_customer_export(NOW, Some(NOW - 27 * HOUR)).outcome,
            Outcome::Green
        );
        assert_eq!(
            h03_customer_export(NOW, Some(NOW - 170 * HOUR)).outcome,
            Outcome::Warn("aging")
        );
        assert_eq!(
            h03_customer_export(NOW, Some(NOW - 193 * HOUR)).outcome,
            Outcome::Red("stale")
        );
    }

    #[test]
    fn h04_disk_thresholds() {
        let ok = DiskUse {
            root: Some(40),
            dump: Some(50),
            thin_data: Some(30),
            thin_meta: Some(10),
        };
        assert_eq!(h04_disk(ok).outcome, Outcome::Green);
        assert_eq!(
            h04_disk(DiskUse {
                root: Some(76),
                ..ok
            })
            .outcome,
            Outcome::Warn("filling")
        );
        assert_eq!(
            h04_disk(DiskUse {
                root: Some(85),
                ..ok
            })
            .outcome,
            Outcome::Red("root_full")
        );
        assert_eq!(
            h04_disk(DiskUse {
                thin_data: Some(80),
                ..ok
            })
            .outcome,
            Outcome::Red("thin_data_full")
        );
        assert_eq!(
            h04_disk(DiskUse {
                thin_meta: Some(81),
                ..ok
            })
            .outcome,
            Outcome::Red("thin_meta_full")
        );
        assert_eq!(
            h04_disk(DiskUse { dump: None, ..ok }).outcome,
            Outcome::Unknown("unread")
        );
        // a red that was read wins over a value that was not
        assert_eq!(
            h04_disk(DiskUse {
                dump: None,
                thin_meta: Some(90),
                ..ok
            })
            .outcome,
            Outcome::Red("thin_meta_full")
        );
    }

    #[test]
    fn h05_raid_states() {
        let healthy = "Personalities : [raid1]\nmd2 : active raid1 nvme1n1p3[1] nvme0n1p3[0]\n      498802048 blocks super 1.2 [2/2] [UU]\n      bitmap: 2/4 pages [8KB], 65536KB chunk\n\nmd1 : active raid1 nvme1n1p2[1] nvme0n1p2[0]\n      2094080 blocks super 1.2 [2/2] [UU]\n\nunused devices: <none>\n";
        assert_eq!(h05_raid(Some(healthy)).outcome, Outcome::Green);
        let degraded = swap(
            healthy,
            "[2/2] [UU]\n      bitmap",
            "[2/1] [U_]\n      bitmap",
        );
        assert_eq!(h05_raid(Some(&degraded)).outcome, Outcome::Red("degraded"));
        let resync = swap(
            healthy,
            "      bitmap",
            "      [=>...]  resync = 8.1% (1/2) finish=1min\n      bitmap",
        );
        assert_eq!(h05_raid(Some(&resync)).outcome, Outcome::Warn("resync"));
        assert_eq!(
            h05_raid(Some("Personalities :\nunused devices: <none>\n")).outcome,
            Outcome::Unknown("no_array")
        );
        assert_eq!(h05_raid(None).outcome, Outcome::Unknown("unread"));
    }

    #[test]
    fn h08_h09_firewall_is_on() {
        assert_eq!(
            h08_firewall_running(Some("Status: enabled/running\n")).outcome,
            Outcome::Green
        );
        assert_eq!(
            h08_firewall_running(Some("Status: disabled/running")).outcome,
            Outcome::Red("fw_not_running")
        );
        assert_eq!(
            h08_firewall_running(Some("Status: enabled/stopped")).outcome,
            Outcome::Red("fw_not_running")
        );
        assert_eq!(
            h08_firewall_running(None).outcome,
            Outcome::Unknown("unread")
        );
        assert_eq!(
            h08_firewall_running(Some("")).outcome,
            Outcome::Unknown("unread")
        );

        let cluster =
            "[OPTIONS]\n\nenable: 1\npolicy_in: DROP\n\n[IPSET management]\n100.64.0.0/10\n";
        assert_eq!(h09_cluster_firewall(Some(cluster)).outcome, Outcome::Green);
        let off = swap(cluster, "enable: 1", "enable: 0");
        assert_eq!(
            h09_cluster_firewall(Some(&off)).outcome,
            Outcome::Red("cluster_fw_off")
        );
        let open = swap(cluster, "policy_in: DROP", "policy_in: ACCEPT");
        assert_eq!(
            h09_cluster_firewall(Some(&open)).outcome,
            Outcome::Red("policy_in_open")
        );
        // `enable: 1` outside [OPTIONS] does not count
        let elsewhere = "[OPTIONS]\npolicy_in: DROP\n\n[RULES]\nenable: 1\n";
        assert_eq!(
            h09_cluster_firewall(Some(elsewhere)).outcome,
            Outcome::Red("cluster_fw_off")
        );
        assert_eq!(
            h09_cluster_firewall(None).outcome,
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s01_state_turns_the_expectation_round() {
        let run = Some("status: running\n");
        let stop = Some("status: stopped\n");
        assert_eq!(
            s01_ct_status(SiteState::Active, run).outcome,
            Outcome::Green
        );
        assert_eq!(
            s01_ct_status(SiteState::Active, stop).outcome,
            Outcome::Red("ct_down")
        );
        assert_eq!(
            s01_ct_status(SiteState::Suspended, stop).outcome,
            Outcome::Green
        );
        assert_eq!(
            s01_ct_status(SiteState::Suspended, run).outcome,
            Outcome::Red("suspended_but_running")
        );
        assert_eq!(
            s01_ct_status(SiteState::Quarantined, run).outcome,
            Outcome::Red("quarantined_but_running")
        );
        assert_eq!(
            s01_ct_status(SiteState::Quarantined, stop).outcome,
            Outcome::Green
        );
        assert_eq!(
            s01_ct_status(SiteState::Active, Some("status: mounted")).outcome,
            Outcome::Unknown("unread")
        );
        assert_eq!(
            s01_ct_status(SiteState::Active, None).outcome,
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s02_unprivileged_and_not_a_template() {
        assert_eq!(s02_unprivileged(Some(CONFIG)).outcome, Outcome::Green);
        let privileged = swap(CONFIG, "unprivileged: 1\n", "");
        assert_eq!(
            s02_unprivileged(Some(&privileged)).outcome,
            Outcome::Red("privileged")
        );
        let zero = swap(CONFIG, "unprivileged: 1", "unprivileged: 0");
        assert_eq!(
            s02_unprivileged(Some(&zero)).outcome,
            Outcome::Red("privileged")
        );
        let template = swap(CONFIG, "onboot: 1", "onboot: 1\ntemplate: 1");
        assert_eq!(
            s02_unprivileged(Some(&template)).outcome,
            Outcome::Red("is_template")
        );
        assert_eq!(s02_unprivileged(None).outcome, Outcome::Unknown("unread"));
        assert_eq!(
            s02_unprivileged(Some("")).outcome,
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s03_firewall_must_be_on_the_card_and_in_the_file() {
        assert_eq!(
            s03_firewall_attached(Some(CONFIG), Some(CT_FW)).outcome,
            Outcome::Green
        );
        let off_card = swap(CONFIG, ",firewall=1", "");
        assert_eq!(
            s03_firewall_attached(Some(&off_card), Some(CT_FW)).outcome,
            Outcome::Red("fw_not_on_card")
        );
        // "firewall=10" or a key that merely ends the same way is not firewall=1
        let ten = swap(CONFIG, ",firewall=1,", ",xfirewall=1,");
        assert_eq!(
            s03_firewall_attached(Some(&ten), Some(CT_FW)).outcome,
            Outcome::Red("fw_not_on_card")
        );
        let disabled = swap(CT_FW, "enable: 1", "enable: 0");
        assert_eq!(
            s03_firewall_attached(Some(CONFIG), Some(&disabled)).outcome,
            Outcome::Red("fw_disabled")
        );
        assert_eq!(
            s03_firewall_attached(Some(CONFIG), None).outcome,
            Outcome::Red("fw_file_missing")
        );
        assert_eq!(
            s03_firewall_attached(None, Some(CT_FW)).outcome,
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s04_exactly_the_expected_group() {
        let q = "ez2-quarantine-v1";
        let s = spec();
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, Some(CT_FW)).outcome,
            Outcome::Green
        );
        let old = swap(CT_FW, "ez2-site-v2", "ez2-site-v1");
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, Some(&old)).outcome,
            Outcome::Red("wrong_group")
        );
        let none = swap(CT_FW, "GROUP ez2-site-v2\n", "");
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, Some(&none)).outcome,
            Outcome::Red("no_group")
        );
        let two = swap(
            CT_FW,
            "GROUP ez2-site-v2\n",
            "GROUP ez2-site-v2\nGROUP ez2-site-v1\n",
        );
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, Some(&two)).outcome,
            Outcome::Red("extra_group")
        );
        // a quarantined site must be on the quarantine group, not on its normal one
        assert_eq!(
            s04_firewall_group(&s, SiteState::Quarantined, q, Some(CT_FW)).outcome,
            Outcome::Red("wrong_group")
        );
        let quarantined = swap(CT_FW, "ez2-site-v2", q);
        assert_eq!(
            s04_firewall_group(&s, SiteState::Quarantined, q, Some(&quarantined)).outcome,
            Outcome::Green
        );
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, Some(&quarantined)).outcome,
            Outcome::Red("wrong_group")
        );
        assert_eq!(
            s04_firewall_group(&s, SiteState::Active, q, None).outcome,
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s05_more_than_recorded_is_red() {
        let s = spec();
        assert_eq!(s05_limits(&s, Some(CONFIG)).outcome, Outcome::Green);
        for (from, to) in [
            ("cores: 2", "cores: 4"),
            ("cpulimit: 2", "cpulimit: 2.5"),
            ("memory: 2048", "memory: 4096"),
            ("swap: 512", "swap: 1024"),
            ("pids.max: 2048", "pids.max: 4096"),
        ] {
            let more = swap(CONFIG, from, to);
            assert_eq!(
                s05_limits(&s, Some(&more)).outcome,
                Outcome::Red("over_record"),
                "{to}"
            );
        }
        let less = swap(CONFIG, "memory: 2048", "memory: 1024");
        assert_eq!(
            s05_limits(&s, Some(&less)).outcome,
            Outcome::Warn("under_record")
        );
        let half = swap(CONFIG, "cpulimit: 2", "cpulimit: 0.5");
        assert_eq!(
            s05_limits(&s, Some(&half)).outcome,
            Outcome::Warn("under_record")
        );
        // a limit that is not there is no limit
        let unlimited = swap(CONFIG, "cpulimit: 2\n", "");
        assert_eq!(
            s05_limits(&s, Some(&unlimited)).outcome,
            Outcome::Red("unlimited")
        );
        let no_pids = swap(CONFIG, "lxc.cgroup2.pids.max: 2048\n", "");
        assert_eq!(
            s05_limits(&s, Some(&no_pids)).outcome,
            Outcome::Red("unlimited")
        );
        let junk = swap(CONFIG, "memory: 2048", "memory: lots");
        assert_eq!(
            s05_limits(&s, Some(&junk)).outcome,
            Outcome::Unknown("unparsed")
        );
        assert_eq!(s05_limits(&s, None).outcome, Outcome::Unknown("unread"));
    }

    #[test]
    fn cpulimit_spellings() {
        assert_eq!(cpulimit_x100("2"), Some(200));
        assert_eq!(cpulimit_x100("0.5"), Some(50));
        assert_eq!(cpulimit_x100("1.25"), Some(125));
        assert_eq!(cpulimit_x100("1.255"), None);
        assert_eq!(cpulimit_x100("two"), None);
        assert_eq!(cpulimit_x100(""), None);
    }

    #[test]
    fn states_and_codes() {
        assert_eq!(parse_state("active"), Some(SiteState::Active));
        assert_eq!(parse_state("quarantined"), Some(SiteState::Quarantined));
        assert_eq!(parse_state("Active"), None);
        assert_eq!(code(Outcome::Green), "ok");
        assert_eq!(code(Outcome::Red("ct_down")), "ct_down");
    }

    /// Review of #623 segment 3 (c477a): M1 the other spellings of "no limit", L1 every
    /// card, L2 the CT's own default policy, L3 an array that did not assemble, N2 the
    /// default and REJECT in the cluster file.
    #[test]
    fn review_segment_3() {
        let s = spec();
        let zero = swap(CONFIG, "cpulimit: 2", "cpulimit: 0");
        assert_eq!(
            s05_limits(&s, Some(&zero)).outcome,
            Outcome::Red("unlimited")
        );
        let max = swap(CONFIG, "pids.max: 2048", "pids.max: max");
        assert_eq!(
            s05_limits(&s, Some(&max)).outcome,
            Outcome::Red("unlimited")
        );

        let s03 = |cfg: &str, fw: &str| s03_firewall_attached(Some(cfg), Some(fw)).outcome;
        let two = swap(
            CONFIG,
            "onboot: 1",
            "net1: name=eth1,bridge=vmbr0,ip=dhcp\nonboot: 1",
        );
        assert_eq!(s03(&two, CT_FW), Outcome::Red("fw_not_on_card"));
        let both = swap(&two, "ip=dhcp", "ip=dhcp,firewall=1");
        assert_eq!(s03(&both, CT_FW), Outcome::Green);
        let card = "net0: name=eth0,bridge=vmbr1,firewall=1,ip=10.77.0.12/24\n";
        assert_eq!(
            s03(&swap(CONFIG, card, ""), CT_FW),
            Outcome::Red("fw_not_on_card")
        );
        let accept = swap(CT_FW, "policy_in: DROP", "policy_in: ACCEPT");
        assert_eq!(s03(CONFIG, &accept), Outcome::Red("ct_policy_open"));
        let reject = swap(CT_FW, "policy_in: DROP", "policy_in: REJECT");
        assert_eq!(s03(CONFIG, &reject), Outcome::Green);
        assert_eq!(
            s03(CONFIG, &swap(CT_FW, "policy_in: DROP\n", "")),
            Outcome::Green
        );

        let md = "md2 : active raid1 a[1] b[0]\n      1 blocks [2/2] [UU]\nmd0 : inactive sda1[0](S)\n      1024 blocks\n";
        assert_eq!(h05_raid(Some(md)).outcome, Outcome::Red("inactive"));

        let cluster = "[OPTIONS]\nenable: 1\npolicy_in: REJECT\n";
        assert_eq!(h09_cluster_firewall(Some(cluster)).outcome, Outcome::Green);
        assert_eq!(
            h09_cluster_firewall(Some("[OPTIONS]\nenable: 1\n")).outcome,
            Outcome::Green
        );
        let open = "[OPTIONS]\nenable: 1\npolicy_in: ACCEPT\n";
        assert_eq!(
            h09_cluster_firewall(Some(open)).outcome,
            Outcome::Red("policy_in_open")
        );
    }
}
