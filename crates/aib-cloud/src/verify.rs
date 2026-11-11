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
//! * **The site's state changes what is expected**: a quarantined site must be on the
//!   quarantine firewall group, not on its recorded one; only an active site's CT has to
//!   be running.
//! * Results carry a short code, never the observed text: reports leave the host.
//!
//! Passive host checks: H01–H10, H12–H14. Passive site checks: S01–S06. Still to come:
//! H11 (listening ports) and S07–S09, which need output formats confirmed on a host, and
//! the `--active` checks.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::{json, Value};

use crate::roster::{ENTRY_ACCOUNT, SUDOERS_DEFAULTS, SUDOERS_HEADER, SUDOERS_RULE_HEAD};
use crate::site::{parse_pct_config, valid_code, SiteSpec};

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

/// H01: the host-health timer wrote its file within the last 15 minutes. `mtime` is `None`
/// when the file is not there.
pub fn h01_health_fresh(now: u64, mtime: Option<u64>) -> CheckResult {
    let outcome = match mtime {
        None => Outcome::Red("never"),
        Some(t) if t > now.saturating_add(300) => Outcome::Unknown("clock"),
        Some(t) if now.saturating_sub(t) > 900 => Outcome::Red("stale"),
        Some(_) => Outcome::Green,
    };
    result("H01", outcome)
}

/// H06: load. `loadavg` is `/proc/loadavg`; a one-minute load of twice the core count or
/// more is a warning (never red: a busy host is not a broken one).
pub fn h06_load(loadavg: Option<&str>, cores: u32) -> CheckResult {
    let load1_x100 = loadavg
        .and_then(|t| t.split_whitespace().next())
        .and_then(|first| {
            let (whole, frac) = first.split_once('.')?;
            if frac.len() != 2 {
                return None;
            }
            Some(whole.parse::<u64>().ok()?.checked_mul(100)? + frac.parse::<u64>().ok()?)
        });
    let outcome = match load1_x100 {
        None => Outcome::Unknown("unread"),
        Some(_) if cores == 0 => Outcome::Unknown("cores"),
        Some(l) if l >= u64::from(cores) * 200 => Outcome::Warn("load_high"),
        Some(_) => Outcome::Green,
    };
    result("H06", outcome)
}

/// H07: a reboot is pending (`/var/run/reboot-required` exists). Reported, never acted
/// on: a reboot needs a person. `None`: could not tell.
pub fn h07_reboot_required(exists: Option<bool>) -> CheckResult {
    let outcome = match exists {
        None => Outcome::Unknown("unread"),
        Some(true) => Outcome::Warn("reboot_required"),
        Some(false) => Outcome::Green,
    };
    result("H07", outcome)
}

/// H10: fail2ban answers and has its sshd jail (`fail2ban-client status sshd`). The ban
/// count is not judged. No answer is red: public ssh without it is the finding.
pub fn h10_fail2ban(status: Option<&str>) -> CheckResult {
    let outcome = match status {
        None => Outcome::Red("f2b_no_answer"),
        Some(t) if t.contains("Status for the jail: sshd") => Outcome::Green,
        Some(_) => Outcome::Red("f2b_no_sshd_jail"),
    };
    result("H10", outcome)
}

/// H12: the clock is synchronised (`timedatectl show -p NTPSynchronized --value`).
/// Backup ages, certificates and one-time codes all lean on it.
pub fn h12_time_sync(value: Option<&str>) -> CheckResult {
    let outcome = match value.map(str::trim) {
        Some("yes") => Outcome::Green,
        Some("no") => Outcome::Warn("clock_unsynced"),
        _ => Outcome::Unknown("unread"),
    };
    result("H12", outcome)
}

/// CT ids out of `pct list` (a header line, then `<id> <status> …` per CT). `None` when a
/// line does not start with an id: a list that is not understood is not an empty list.
pub fn parse_pct_list(out: &str) -> Option<Vec<u32>> {
    out.lines()
        .skip(1)
        .filter(|l| !l.trim().is_empty())
        .map(|l| l.split_whitespace().next()?.parse::<u32>().ok())
        .collect()
}

/// H13: every site record has its CT and every CT is accounted for — it belongs to a
/// record or is one of `platform` (edge, template, …). A CT nobody recorded is a site
/// that is neither backed up by name nor checked.
pub fn h13_inventory(cts: Option<&[u32]>, record_cts: &[u32], platform: &[u32]) -> CheckResult {
    let Some(cts) = cts else {
        return result("H13", Outcome::Unknown("unread"));
    };
    let outcome = if record_cts.iter().any(|r| !cts.contains(r)) {
        Outcome::Red("record_without_ct")
    } else if cts
        .iter()
        .any(|c| !record_cts.contains(c) && !platform.contains(c))
    {
        Outcome::Red("ct_without_record")
    } else if record_cts
        .iter()
        .enumerate()
        .any(|(i, r)| record_cts[..i].contains(r) || platform.contains(r))
    {
        // two records for one CT, or a record claiming the edge / template
        Outcome::Red("ct_claimed_twice")
    } else {
        Outcome::Green
    };
    result("H13", outcome)
}

/// What `site verify` saw of the entry account's sudoers file
/// ([`crate::roster::SUDOERS_PATH`]). `None` in a field: could not be observed.
#[derive(Debug, Clone, Copy, Default)]
pub struct EntrySudoers<'a> {
    /// Whether the file is there.
    pub present: Option<bool>,
    /// Owner uid, group gid and permission bits.
    pub owner_mode: Option<(u32, u32, u32)>,
    /// Its text.
    pub text: Option<&'a str>,
    /// Whether `visudo -c -f` accepted it.
    pub visudo_ok: Option<bool>,
    /// The text of every other sudoers file sudo reads (`/etc/sudoers` and the files of
    /// `/etc/sudoers.d` it does not skip). `None`: one of them could not be read.
    pub others: Option<&'a [String]>,
    /// The entry account's uid and the names of its groups.
    pub account: Option<(u32, &'a [String])>,
    /// Whether sudo takes its rules from files only (`sudoers:` in nsswitch.conf; see
    /// [`nss_sudoers_files_only`]).
    pub nss_files_only: Option<bool>,
}

/// Whether nsswitch.conf (`None`: could not be read; `Some("")`: there is none) has sudo
/// read its rules from files only. No `sudoers:` line means files. Any other source (sss,
/// ldap, …) holds rules no file here shows (33e28, #655 r3 L1).
pub fn nss_sudoers_files_only(nsswitch: Option<&str>) -> Option<bool> {
    let text = nsswitch?;
    for line in text.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        if let Some(rest) = line.strip_prefix("sudoers:") {
            return Some(
                rest.split_whitespace()
                    .filter(|w| !w.starts_with('['))
                    .all(|w| w == "files"),
            );
        }
    }
    Some(true)
}

/// The user list at the start of `spec`: the words up to the first gap that is not after
/// or before a comma (`root, cloudgate host=…` lists two users; the host list follows).
fn user_list(spec: &str) -> Vec<&str> {
    let head = spec.split('=').next().unwrap_or("");
    let mut out = Vec::new();
    let mut words = head.split_whitespace().peekable();
    while let Some(w) = words.next() {
        out.extend(w.split(',').map(str::trim).filter(|u| !u.is_empty()));
        if !(w.ends_with(',') || words.peek().is_some_and(|n| n.starts_with(','))) {
            break;
        }
    }
    out
}

/// One word where sudoers names users, in a shape this check reads word for word: a user
/// name, `%group`, `%#gid`, `#uid`, `ALL`, or a `User_Alias` name, each maybe after `!`.
/// Anything else (`+netgroup`, `%:group`, quoting, escapes, …) is not read: `None`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Who<'a> {
    User(&'a str),
    Group(&'a str),
    Uid(&'a str),
    All,
    Alias(&'a str),
}

fn who(word: &str) -> Option<Who<'_>> {
    let w = word.strip_prefix('!').unwrap_or(word);
    let name = |n: &str| {
        n.starts_with(|c: char| c.is_ascii_lowercase() || c == '_')
            && n.bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
    };
    let digits = |n: &str| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit());
    if w == "ALL" {
        Some(Who::All)
    } else if let Some(g) = w.strip_prefix("%#") {
        digits(g).then_some(Who::Group(&w[1..]))
    } else if let Some(g) = w.strip_prefix('%') {
        name(g).then_some(Who::Group(g))
    } else if let Some(u) = w.strip_prefix('#') {
        digits(u).then_some(Who::Uid(w))
    } else if w.starts_with(|c: char| c.is_ascii_uppercase())
        && w.bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
    {
        Some(Who::Alias(w))
    } else {
        name(w).then_some(Who::User(w))
    }
}

/// Whether the sudoers files in `files` (all but ours) may grant the entry account anything
/// (33e28, #655 M1 and r2–r3; f1fae: only what is read word for word counts as clean).
///
/// Every line must be one of: empty, a comment, `@includedir /etc/sudoers.d` (or the `#`
/// spelling), `Defaults` not bound to users (plain, `@host`, `>runas`, `!cmnd`),
/// `Host_Alias` / `Cmnd_Alias` / `Cmd_Alias` / `Runas_Alias`, `Defaults:<users>`,
/// `User_Alias A = … [: B = …]`, or a rule `<users> <hosts>=…`, where every user word reads
/// as [`who`] and every alias used is defined. `Ok(true)` when a user list or a used alias
/// comes down to the account (by name, `ALL`, its uid, one of its groups — `groups` holds
/// names and `#gid`s); otherwise `Err("others_unread")` when a file pulls in something other
/// than `/etc/sudoers.d`, `Err("others_shape")` when any line is not read, `Ok(false)`.
fn grants_elsewhere(files: &[String], uid: u32, groups: &[String]) -> Result<bool, &'static str> {
    let me = alloc::format!("#{uid}");
    let mut lines: Vec<String> = Vec::new();
    for text in files {
        for line in text.replace("\\\n", " ").lines().map(str::trim) {
            if line.is_empty() {
                continue;
            }
            if line.starts_with("#include") || line.starts_with("@include") {
                let mut w = line.split_whitespace();
                match (w.next(), w.next(), w.next()) {
                    (Some("#includedir" | "@includedir"), Some("/etc/sudoers.d"), None) => continue,
                    _ => return Err("others_unread"),
                }
            }
            // `#` and then a digit is a uid; any other `#` line is a comment
            if line
                .strip_prefix('#')
                .is_some_and(|rest| !rest.starts_with(|c: char| c.is_ascii_digit()))
            {
                continue;
            }
            lines.push(line.to_owned());
        }
    }
    let mut shape_ok = true;
    // User_Alias A = x, y : B = z
    let mut aliases: Vec<(&str, Vec<Who<'_>>)> = Vec::new();
    for l in &lines {
        let Some(rest) = l.strip_prefix("User_Alias") else {
            continue;
        };
        if !rest.starts_with([' ', '\t']) {
            shape_ok = false;
            continue;
        }
        for part in rest.split(':') {
            let Some((name, members)) = part.split_once('=') else {
                shape_ok = false;
                continue;
            };
            let members: Option<Vec<Who<'_>>> = members.split(',').map(|m| who(m.trim())).collect();
            match (who(name.trim()), members) {
                (Some(Who::Alias(n)), Some(m)) if !m.is_empty() => aliases.push((n, m)),
                _ => shape_ok = false,
            }
        }
    }
    let defined = |n: &str| aliases.iter().any(|(a, _)| *a == n);
    let direct = |w: &Who<'_>| match *w {
        Who::User(u) => u == ENTRY_ACCOUNT,
        Who::All => true,
        Who::Uid(u) => u == me,
        Who::Group(g) => groups.iter().any(|x| x == g),
        Who::Alias(_) => false,
    };
    // the aliases that come down to us, to a fixed point; one naming the account is red as it stands
    let mut ours: Vec<&str> = Vec::new();
    loop {
        let before = ours.len();
        for (name, members) in &aliases {
            if !ours.contains(name)
                && members
                    .iter()
                    .any(|m| direct(m) || matches!(m, Who::Alias(a) if ours.contains(a)))
            {
                ours.push(name);
            }
        }
        if ours.len() == before {
            break;
        }
    }
    let named = aliases
        .iter()
        .any(|(_, m)| m.contains(&Who::User(ENTRY_ACCOUNT)));
    let mut granted = named;
    for l in &lines {
        let first = l.split_whitespace().next().unwrap_or("");
        let users: Vec<&str> = if let Some(rest) = l.strip_prefix("Defaults") {
            // only `Defaults:<users>` binds to users
            match rest.chars().next() {
                Some(':') => user_list(&rest[1..]),
                Some(' ' | '\t' | '@' | '>' | '!') => continue,
                _ => {
                    shape_ok = false;
                    continue;
                }
            }
        } else if matches!(
            first,
            // User_Alias was read above; the others name hosts, commands or run-as users
            "User_Alias" | "Host_Alias" | "Cmnd_Alias" | "Cmd_Alias" | "Runas_Alias"
        ) {
            continue;
        } else {
            if !l.contains('=') {
                shape_ok = false;
                continue;
            }
            user_list(l)
        };
        let read: Option<Vec<Who<'_>>> = users.iter().map(|u| who(u)).collect();
        match read {
            Some(ws) if !ws.is_empty() => {
                for w in &ws {
                    match w {
                        Who::Alias(a) if !defined(a) => shape_ok = false,
                        Who::Alias(a) if ours.contains(a) => granted = true,
                        other => granted |= direct(other),
                    }
                }
            }
            _ => shape_ok = false,
        }
    }
    if granted {
        Ok(true)
    } else if shape_ok {
        Ok(false)
    } else {
        Err("others_shape")
    }
}

/// One rule line's arguments, after [`SUDOERS_RULE_HEAD`]: exactly what
/// [`crate::roster::sudoers_file`] writes — `--tenant <code> [--sites a\,b] --roles r\,s` —
/// and nothing else (no `*`, no second command after a bare `,`).
fn entry_rule_args(rest: &str) -> bool {
    let list = |w: &str| w.split("\\,").all(valid_code);
    let words: Vec<&str> = rest.split(' ').collect();
    match words.as_slice() {
        ["--tenant", t, "--roles", r] => valid_code(t) && list(r),
        ["--tenant", t, "--sites", s, "--roles", r] => valid_code(t) && list(s) && list(r),
        _ => false,
    }
}

/// H14: the entry account's sudoers file is the generated one in shape (c477a, #648 N2):
/// root:root 0440, accepted by `visudo`, our `Defaults` line (both variables kept, no tty
/// needed), and every rule names the customer command with its arguments written out.
///
/// A host where the entry account is not set up yet has no file: a warning, not red.
/// **This is a host check, for the people who run the host**: it is not in any site's
/// checks, so it never reaches what a customer is shown (#642).
pub fn h14_entry_sudoers(seen: EntrySudoers<'_>) -> CheckResult {
    let outcome = match seen.present {
        None => Outcome::Unknown("unread"),
        Some(false) => Outcome::Warn("entry_not_set_up"),
        Some(true) => match (seen.owner_mode, seen.text) {
            (None, _) | (_, None) => Outcome::Unknown("unread"),
            (Some(om), _) if om != (0, 0, 0o440) => Outcome::Red("sudoers_mode"),
            (Some(_), Some(text)) => {
                // only the fixed header may start with `#`: `#include` and `#<uid>` are
                // not comments in sudoers (33e28, #655 H1), so they are judged as rules
                let lines: Vec<&str> = text
                    .lines()
                    .map(str::trim_end)
                    .filter(|l| !l.is_empty() && *l != SUDOERS_HEADER)
                    .collect();
                let mut rules = lines.iter().filter(|l| **l != SUDOERS_DEFAULTS);
                let elsewhere = match (seen.others, seen.account) {
                    (Some(files), Some((uid, groups))) => grants_elsewhere(files, uid, groups),
                    _ => Err("others_unread"),
                };
                if text.contains('*') {
                    Outcome::Red("sudoers_wildcard")
                } else if lines.iter().filter(|l| **l == SUDOERS_DEFAULTS).count() != 1 {
                    Outcome::Red("sudoers_defaults")
                } else if !rules.all(|l| {
                    l.strip_prefix(SUDOERS_RULE_HEAD)
                        .is_some_and(entry_rule_args)
                }) {
                    Outcome::Red("sudoers_rule")
                } else if elsewhere == Ok(true) {
                    Outcome::Red("sudoers_elsewhere")
                } else if let Err(why) = elsewhere {
                    Outcome::Unknown(why)
                } else if seen.nss_files_only != Some(true) {
                    Outcome::Unknown(if seen.nss_files_only.is_none() {
                        "nsswitch_unread"
                    } else {
                        "sudoers_source"
                    })
                } else {
                    match seen.visudo_ok {
                        None => Outcome::Unknown("visudo_unread"),
                        Some(false) => Outcome::Red("sudoers_invalid"),
                        Some(true) => Outcome::Green,
                    }
                }
            }
        },
    };
    result("H14", outcome)
}

/// S06: nothing drifted from the record. `drifted` is how many keys differ; `None` when
/// the comparison could not be made.
pub fn s06_drift(drifted: Option<usize>) -> CheckResult {
    let outcome = match drifted {
        None => Outcome::Unknown("unread"),
        Some(0) => Outcome::Green,
        Some(_) => Outcome::Red("drift"),
    };
    result("S06", outcome)
}

/// S01: an active site's CT runs. `status` is the output of `pct status <ct>`
/// (`status: running`). A suspended site keeps its CT running by design (`site suspend`
/// only swaps the edge block) and so does a quarantined one (cut off, kept as it is for
/// whoever investigates), so for those either state is as expected; what keeps them off
/// the network is checked by S03 / S04 and, with `--active`, at the edge.
pub fn s01_ct_status(state: SiteState, status: Option<&str>) -> CheckResult {
    let running = match status.map(str::trim) {
        Some("status: running") => true,
        Some("status: stopped") => false,
        _ => return result("S01", Outcome::Unknown("unread")),
    };
    let outcome = match (state, running) {
        (SiteState::Active, false) => Outcome::Red("ct_down"),
        (SiteState::Active, true) | (SiteState::Suspended | SiteState::Quarantined, _) => {
            Outcome::Green
        }
    };
    result("S01", outcome)
}

fn config(pct_config: Option<&str>) -> Option<Vec<(String, String)>> {
    parse_pct_config(pct_config?).ok()
}

fn get<'a>(cfg: &'a [(String, String)], key: &str) -> Option<&'a str> {
    cfg.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
}

/// S10: the site is masked (4.5 §7; c477a's list). The CT's configuration carries the
/// mask's hook line — that key, the wrapper's path, once — and, for a running CT, the
/// hook's verdict file says every cover is in place. A stopped CT has no verdict to read:
/// the line alone is checked. `mask_result` is `/run/ez2-cloud/mask/<ct>/result`.
pub fn s10_masked(
    pct_config: Option<&str>,
    running: Option<bool>,
    mask_result: Option<&str>,
) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S10", Outcome::Unknown("unread"));
    };
    let hooks: Vec<&str> = cfg
        .iter()
        .filter(|(k, _)| k == "lxc.hook.mount")
        .map(|(_, v)| v.as_str())
        .collect();
    let outcome = match (hooks.as_slice(), running) {
        ([one], _) if !crate::site::mask_hook_ok(one) => Outcome::Red("no_mask_hook"),
        ([_], Some(false)) => Outcome::Green,
        ([_], Some(true)) if crate::mask::result_is_masked(mask_result) => Outcome::Green,
        ([_], Some(true)) => Outcome::Red("not_masked"),
        ([_], None) => Outcome::Unknown("unread"),
        _ => Outcome::Red("no_mask_hook"),
    };
    result("S10", outcome)
}

/// The search domain a site CT is given: a name that resolves nowhere. It has to be set —
/// a CT without one inherits the host's own search domain (seen 11-11: the site's
/// `resolv.conf` and `hosts` carried the platform's private network name).
pub const SEARCH_DOMAIN: &str = "invalid";

/// S11: the CT has its own resolver and the neutral search domain ([`SEARCH_DOMAIN`]).
/// Without `nameserver`, or without `searchdomain`, the CT inherits the host's.
pub fn s11_resolver(pct_config: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S11", Outcome::Unknown("unread"));
    };
    let outcome = if get(&cfg, "nameserver").is_none() {
        Outcome::Red("host_resolver")
    } else if get(&cfg, "searchdomain") != Some(SEARCH_DOMAIN) {
        Outcome::Red("search_domain")
    } else {
        Outcome::Green
    };
    result("S11", outcome)
}

/// The value of one `key=value` field of the CT's `net0` line.
fn net0<'a>(cfg: &'a [(String, String)], key: &str) -> Option<&'a str> {
    get(cfg, "net0")?
        .split(',')
        .find_map(|f| f.strip_prefix(key)?.strip_prefix('='))
}

/// S12: the CT's hardware address is a locally administered one, not from the platform's
/// own vendor prefix (which names the platform to anything that reads the address).
pub fn s12_hwaddr(pct_config: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S12", Outcome::Unknown("unread"));
    };
    let Some(mac) = net0(&cfg, "hwaddr") else {
        return result("S12", Outcome::Unknown("unparsed"));
    };
    // locally administered and not multicast: the second hex digit is 2, 6, A or E
    let outcome = if crate::mask::hwaddr_is_local(mac) {
        Outcome::Green
    } else {
        Outcome::Red("vendor_hwaddr")
    };
    result("S12", outcome)
}

/// S13: the CT has no IPv6 address. Its IPv6 traffic would leave from the host's own
/// prefix, whatever the IPv4 egress is.
pub fn s13_no_ipv6(pct_config: Option<&str>) -> CheckResult {
    let Some(cfg) = config(pct_config) else {
        return result("S13", Outcome::Unknown("unread"));
    };
    let outcome = if get(&cfg, "net0").is_none() {
        Outcome::Unknown("unparsed")
    } else if net0(&cfg, "ip6").is_some() || net0(&cfg, "gw6").is_some() {
        Outcome::Red("has_ipv6")
    } else {
        Outcome::Green
    };
    result("S13", outcome)
}

/// H15: the kernel log is not readable from inside a container
/// (`/proc/sys/kernel/dmesg_restrict` is 1). It names the hardware at boot.
pub fn h15_dmesg_restrict(value: Option<&str>) -> CheckResult {
    let outcome = match value.map(str::trim) {
        Some("1") => Outcome::Green,
        Some("0") => Outcome::Red("dmesg_open"),
        _ => Outcome::Unknown("unread"),
    };
    result("H15", outcome)
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
    fn s01_only_an_active_site_has_to_be_running() {
        let run = Some("status: running\n");
        let stop = Some("status: stopped\n");
        let s01 = |state, status| s01_ct_status(state, status).outcome;
        assert_eq!(s01(SiteState::Active, run), Outcome::Green);
        assert_eq!(s01(SiteState::Active, stop), Outcome::Red("ct_down"));
        // suspend and quarantine both leave the CT as it is
        for state in [SiteState::Suspended, SiteState::Quarantined] {
            assert_eq!(s01(state, run), Outcome::Green);
            assert_eq!(s01(state, stop), Outcome::Green);
            assert_eq!(s01(state, None), Outcome::Unknown("unread"));
        }
        assert_eq!(
            s01(SiteState::Active, Some("status: mounted")),
            Outcome::Unknown("unread")
        );
        assert_eq!(s01(SiteState::Active, None), Outcome::Unknown("unread"));
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

    #[test]
    fn h01_h06_h07_h10_h12() {
        assert_eq!(
            h01_health_fresh(NOW, Some(NOW - 60)).outcome,
            Outcome::Green
        );
        assert_eq!(
            h01_health_fresh(NOW, Some(NOW - 901)).outcome,
            Outcome::Red("stale")
        );
        assert_eq!(h01_health_fresh(NOW, None).outcome, Outcome::Red("never"));
        assert_eq!(
            h01_health_fresh(NOW, Some(NOW + HOUR)).outcome,
            Outcome::Unknown("clock")
        );

        let load = |t: &str| h06_load(Some(t), 12).outcome;
        assert_eq!(load("3.85 3.85 6.35 1/234 5678\n"), Outcome::Green);
        assert_eq!(load("23.99 1.00 1.00 1/1 1"), Outcome::Green);
        assert_eq!(load("24.00 1.00 1.00 1/1 1"), Outcome::Warn("load_high"));
        assert_eq!(load("95.10 1.00 1.00 1/1 1"), Outcome::Warn("load_high"));
        assert_eq!(load(""), Outcome::Unknown("unread"));
        assert_eq!(load("high"), Outcome::Unknown("unread"));
        assert_eq!(h06_load(None, 12).outcome, Outcome::Unknown("unread"));
        assert_eq!(
            h06_load(Some("1.00 1.00 1.00"), 0).outcome,
            Outcome::Unknown("cores")
        );

        assert_eq!(h07_reboot_required(Some(false)).outcome, Outcome::Green);
        assert_eq!(
            h07_reboot_required(Some(true)).outcome,
            Outcome::Warn("reboot_required")
        );
        assert_eq!(
            h07_reboot_required(None).outcome,
            Outcome::Unknown("unread")
        );

        let jail = "Status for the jail: sshd\n|- Filter\n|  |- Currently failed: 3\n";
        assert_eq!(h10_fail2ban(Some(jail)).outcome, Outcome::Green);
        assert_eq!(
            h10_fail2ban(Some("Sorry but the jail 'sshd' does not exist")).outcome,
            Outcome::Red("f2b_no_sshd_jail")
        );
        assert_eq!(h10_fail2ban(None).outcome, Outcome::Red("f2b_no_answer"));

        assert_eq!(h12_time_sync(Some("yes\n")).outcome, Outcome::Green);
        assert_eq!(
            h12_time_sync(Some("no")).outcome,
            Outcome::Warn("clock_unsynced")
        );
        assert_eq!(h12_time_sync(Some("")).outcome, Outcome::Unknown("unread"));
        assert_eq!(h12_time_sync(None).outcome, Outcome::Unknown("unread"));
    }

    #[test]
    fn h13_every_ct_is_accounted_for() {
        let list = "VMID       Status     Lock         Name\n101        running                 edge\n110        running                 site1\n112        stopped                 site2\n900        stopped                 tpl\n";
        assert_eq!(parse_pct_list(list), Some(alloc::vec![101, 110, 112, 900]));
        assert_eq!(parse_pct_list("VMID Status Lock Name\n"), Some(Vec::new()));
        assert_eq!(parse_pct_list("VMID Status\nconnection refused\n"), None);
        let platform = [101, 900];
        let h13 = |cts: &[u32], recs: &[u32]| h13_inventory(Some(cts), recs, &platform).outcome;
        assert_eq!(h13(&[101, 110, 112, 900], &[110, 112]), Outcome::Green);
        assert_eq!(
            h13(&[101, 110, 900], &[110, 112]),
            Outcome::Red("record_without_ct")
        );
        assert_eq!(
            h13(&[101, 110, 112, 113, 900], &[110, 112]),
            Outcome::Red("ct_without_record")
        );
        assert_eq!(
            h13(&[101, 110, 900], &[110, 110]),
            Outcome::Red("ct_claimed_twice")
        );
        assert_eq!(
            h13(&[101, 110, 900], &[110, 101]),
            Outcome::Red("ct_claimed_twice")
        );
        assert_eq!(
            h13_inventory(None, &[110], &platform).outcome,
            Outcome::Unknown("unread")
        );
    }

    /// 4.5 r4 section 14.1 #6 (c477a's list): the passive mask checks.
    #[test]
    fn s10_to_s13_and_h15_the_site_is_masked() {
        const CT: &str = "arch: amd64\ncores: 2\nhostname: site2\nnameserver: 1.1.1.1\nnet0: name=eth0,bridge=vmbr1,firewall=1,gw=10.77.0.1,hwaddr=BC:24:11:AA:BB:CC,ip=10.77.0.12/24,type=veth\nunprivileged: 1\n";
        let hook = crate::mask::HOOK_LINE;
        let masked = alloc::format!("{CT}{hook}\n");
        let ok = crate::mask::result_ok(212);
        let s10 =
            |cfg: &str, running, res: Option<&str>| s10_masked(Some(cfg), running, res).outcome;
        assert_eq!(s10(&masked, Some(true), Some(&ok)), Outcome::Green);
        // the line is there but the hook did not cover everything, or left no verdict
        assert_eq!(
            s10(&masked, Some(true), Some("NOT MASKED proc\n")),
            Outcome::Red("not_masked")
        );
        assert_eq!(s10(&masked, Some(true), None), Outcome::Red("not_masked"));
        // no line, another path, two lines: not the mask
        assert_eq!(s10(CT, Some(true), Some(&ok)), Outcome::Red("no_mask_hook"));
        assert_eq!(
            s10(
                &alloc::format!("{CT}lxc.hook.mount: /x\n"),
                Some(true),
                Some(&ok)
            ),
            Outcome::Red("no_mask_hook")
        );
        assert_eq!(
            s10(&alloc::format!("{masked}{hook}\n"), Some(true), Some(&ok)),
            Outcome::Red("no_mask_hook")
        );
        // a stopped CT has no verdict; whether it runs unknown is unknown
        assert_eq!(s10(&masked, Some(false), None), Outcome::Green);
        assert_eq!(s10(&masked, None, Some(&ok)), Outcome::Unknown("unread"));
        assert_eq!(
            s10_masked(None, Some(true), Some(&ok)).outcome,
            Outcome::Unknown("unread")
        );

        // no search domain is the host's search domain; only the neutral one passes
        assert_eq!(
            s11_resolver(Some(CT)).outcome,
            Outcome::Red("search_domain")
        );
        assert_eq!(
            s11_resolver(Some(&alloc::format!("{CT}searchdomain: example.net\n"))).outcome,
            Outcome::Red("search_domain")
        );
        let neutral = alloc::format!("{CT}searchdomain: {SEARCH_DOMAIN}\n");
        assert_eq!(s11_resolver(Some(&neutral)).outcome, Outcome::Green);
        assert_eq!(
            s11_resolver(Some(&swap(&neutral, "nameserver: 1.1.1.1\n", ""))).outcome,
            Outcome::Red("host_resolver")
        );
        assert_eq!(s11_resolver(None).outcome, Outcome::Unknown("unread"));

        // the fixture's address is the platform's vendor prefix
        assert_eq!(s12_hwaddr(Some(CT)).outcome, Outcome::Red("vendor_hwaddr"));
        for (mac, want) in [
            ("02:00:0A:4D:00:0C", Outcome::Green),
            ("ae:11:22:33:44:55", Outcome::Green),
            ("00:11:22:33:44:55", Outcome::Red("vendor_hwaddr")),
            ("03:11:22:33:44:55", Outcome::Red("vendor_hwaddr")),
            ("02:00", Outcome::Red("vendor_hwaddr")),
        ] {
            let cfg = swap(CT, "BC:24:11:AA:BB:CC", mac);
            assert_eq!(s12_hwaddr(Some(&cfg)).outcome, want, "{mac}");
        }
        assert_eq!(
            s12_hwaddr(Some(&swap(CT, "hwaddr=BC:24:11:AA:BB:CC,", ""))).outcome,
            Outcome::Unknown("unparsed")
        );

        assert_eq!(s13_no_ipv6(Some(CT)).outcome, Outcome::Green);
        for extra in ["ip6=auto", "ip6=2001:db8::12/64", "gw6=2001:db8::1"] {
            let cfg = swap(CT, "type=veth", &alloc::format!("{extra},type=veth"));
            assert_eq!(
                s13_no_ipv6(Some(&cfg)).outcome,
                Outcome::Red("has_ipv6"),
                "{extra}"
            );
        }
        assert_eq!(s13_no_ipv6(None).outcome, Outcome::Unknown("unread"));

        assert_eq!(h15_dmesg_restrict(Some("1\n")).outcome, Outcome::Green);
        assert_eq!(
            h15_dmesg_restrict(Some("0\n")).outcome,
            Outcome::Red("dmesg_open")
        );
        assert_eq!(h15_dmesg_restrict(None).outcome, Outcome::Unknown("unread"));
    }

    #[test]
    fn h14_the_entry_sudoers_file_is_the_generated_one() {
        use crate::roster::{parse, sudoers_file};
        let key = |c: u8| {
            alloc::format!(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{}",
                core::iter::repeat_n(char::from(c), 43).collect::<String>()
            )
        };
        let roster = json!({ "tenant_id": "t-aaa", "members": [
            { "identity_id": "m-01", "roles": ["owner"], "scope": "tenant", "keys": [
                { "public_key": key(b'A'), "use": "entry", "added": "2026-11-11T00:00:00Z" }] },
            { "identity_id": "m-02", "roles": ["editor", "analyst"], "scope": ["site2", "site3"], "keys": [
                { "public_key": key(b'B'), "use": "entry", "added": "2026-11-11T00:00:00Z" }] }] });
        let roster = parse(&roster.to_string(), &["site2", "site3"]).unwrap();
        let good = sudoers_file(&[roster]).unwrap();
        static NONE: [String; 0] = [];
        fn seen(text: &str) -> EntrySudoers<'_> {
            EntrySudoers {
                present: Some(true),
                owner_mode: Some((0, 0, 0o440)),
                text: Some(text),
                visudo_ok: Some(true),
                others: Some(&NONE),
                account: Some((1001, &NONE)),
                nss_files_only: Some(true),
            }
        }
        let h14 = |e: EntrySudoers<'_>| h14_entry_sudoers(e).outcome;
        assert_eq!(h14(seen(&good)), Outcome::Green);
        assert_eq!(h14_entry_sudoers(seen(&good)).id, "H14");
        // the hand-written rule this replaces
        let star = "Defaults:cloudgate !requiretty, env_keep += \"SSH_ORIGINAL_COMMAND SSH_CONNECTION\"\ncloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer *\n";
        assert_eq!(h14(seen(star)), Outcome::Red("sudoers_wildcard"));
        // a second command after a bare comma, more roles than a code, another program
        for bad in [
            "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer --tenant t-aaa --roles owner, /bin/sh",
            "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer --tenant t-aaa --roles owner --records /tmp",
            "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer --site site2 --roles owner",
            "cloudgate ALL=(root) NOPASSWD: /bin/sh",
            "cloudgate ALL=(ALL) ALL",
        ] {
            let text = alloc::format!("{good}{bad}\n");
            assert_eq!(h14(seen(&text)), Outcome::Red("sudoers_rule"), "{bad}");
        }
        // `#` lines other than the header are not comments in sudoers (33e28, #655 H1)
        for bad in [
            "#include /etc/x",
            "#includedir /etc/y",
            "@include /etc/x",
            "#0 ALL=(ALL) NOPASSWD: ALL",
            "# just a note",
        ] {
            let text = alloc::format!("{good}{bad}\n");
            assert_eq!(h14(seen(&text)), Outcome::Red("sudoers_rule"), "{bad}");
        }
        // another sudoers file that grants the entry account anything (33e28, #655 M1)
        let debian = "# comment cloudgate\nDefaults\tenv_reset\nroot\tALL=(ALL:ALL) ALL\n%sudo\tALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n".to_string();
        let groups = [
            "cloudgate".to_string(),
            "users".to_string(),
            "#2000".to_string(),
        ];
        let with = |extra: &str| alloc::vec![debian.clone(), extra.to_string()];
        let judge = |files: &[String], groups: &[String]| {
            h14(EntrySudoers {
                others: Some(files),
                account: Some((1001, groups)),
                ..seen(&good)
            })
        };
        assert_eq!(judge(&with(""), &groups), Outcome::Green);
        for (extra, why) in [
            (
                "cloudgate ALL=(root) NOPASSWD: /usr/local/sbin/aib-cloud-host customer x",
                "by name",
            ),
            ("User_Alias GATE = nobody, cloudgate", "in an alias"),
            ("ALL ALL=(root) NOPASSWD: /bin/true", "for ALL"),
            ("#1001 ALL=(ALL) ALL", "by uid"),
            ("%users ALL=(ALL) ALL", "by group"),
            ("root,cloudgate ALL=(ALL) ALL", "in a user list"),
            (
                "root, #1001 ALL=(ALL) ALL",
                "by uid, second in a spaced list",
            ),
            (
                "%nobody, \\\n    #1001 ALL=(ALL) ALL",
                "by uid on a continued line",
            ),
            (
                "User_Alias OPS = %users\nOPS ALL=(ALL) ALL",
                "through an alias of its group",
            ),
            (
                "User_Alias OPS = #1001\nroot, OPS ALL=(ALL) ALL",
                "through an alias of its uid",
            ),
            (
                "User_Alias A = %users : B = nobody\nUser_Alias C = A\nC ALL=(ALL) ALL",
                "through an alias of an alias",
            ),
            ("%#2000 ALL=(ALL) ALL", "by gid"),
            (
                "Defaults:%users env_keep += \"LD_PRELOAD\"",
                "a Defaults bound to its group",
            ),
            (
                "Defaults:OPS2 !env_reset\nUser_Alias OPS2 = ALL",
                "a Defaults bound to an alias of ALL",
            ),
        ] {
            let files = with(extra);
            assert_eq!(
                judge(&files, &groups),
                Outcome::Red("sudoers_elsewhere"),
                "{why}"
            );
        }
        // an alias that does not come down to us, used in a rule; Defaults not bound to users
        let fine = with("User_Alias OPS = alice, %sudo\nOPS ALL=(ALL) ALL\nDefaults@host1 env_reset\nDefaults>root !lecture\nDefaults\tsecure_path=\"/usr/bin\"");
        assert_eq!(judge(&fine, &groups), Outcome::Green);
        // an alias defined in one file and used in another
        let split = alloc::vec![
            debian.clone(),
            "User_Alias OPS = %users".to_string(),
            "OPS ALL=(ALL) ALL".to_string()
        ];
        assert_eq!(judge(&split, &groups), Outcome::Red("sudoers_elsewhere"));
        // a netgroup in a user position: who is in it no file here says (33e28, #655 r3 M1)
        for extra in [
            "+ops ALL=(ALL) ALL",
            "User_Alias OPS = +ops\nOPS ALL=(ALL) ALL",
            "Defaults:+ops !env_reset",
            "root, !+ops ALL=(ALL) ALL",
        ] {
            assert_eq!(
                judge(&with(extra), &groups),
                Outcome::Unknown("others_shape"),
                "{extra}"
            );
        }
        // shapes this check does not read word for word: unknown, never green (f1fae 06:5x)
        for extra in [
            "%:users ALL=(ALL) ALL",
            "\"root\" ALL=(ALL) ALL",
            "OPS ALL=(ALL) ALL",
            "User_Alias ops = root",
            "User_Alias OPS = +ops",
            "Defaults~x y",
            "something odd",
            "User_Alias OPS = %:users\nOPS ALL=(ALL) ALL",
        ] {
            assert_eq!(
                judge(&with(extra), &groups),
                Outcome::Unknown("others_shape"),
                "{extra}"
            );
        }
        // ... unless something else already grants: red wins
        assert_eq!(
            judge(
                &with("+ops ALL=(ALL) ALL\ncloudgate ALL=(ALL) ALL"),
                &groups
            ),
            Outcome::Red("sudoers_elsewhere")
        );
        // where sudo takes its rules from (33e28, #655 r3 L1)
        assert_eq!(nss_sudoers_files_only(Some("")), Some(true));
        assert_eq!(
            nss_sudoers_files_only(Some("passwd: files systemd\n")),
            Some(true)
        );
        assert_eq!(nss_sudoers_files_only(Some("sudoers: files\n")), Some(true));
        assert_eq!(
            nss_sudoers_files_only(Some("sudoers:\tfiles [NOTFOUND=return]\n")),
            Some(true)
        );
        assert_eq!(
            nss_sudoers_files_only(Some("#sudoers: sss\nsudoers: files sss\n")),
            Some(false)
        );
        assert_eq!(nss_sudoers_files_only(Some("sudoers: ldap\n")), Some(false));
        assert_eq!(nss_sudoers_files_only(None), None);
        assert_eq!(
            h14(EntrySudoers {
                nss_files_only: Some(false),
                ..seen(&good)
            }),
            Outcome::Unknown("sudoers_source")
        );
        assert_eq!(
            h14(EntrySudoers {
                nss_files_only: None,
                ..seen(&good)
            }),
            Outcome::Unknown("nsswitch_unread")
        );
        // the sudo group is not the account's: green; a group the account is in: red
        let sudo_member = ["cloudgate".to_string(), "sudo".to_string()];
        assert_eq!(
            judge(&with(""), &sudo_member),
            Outcome::Red("sudoers_elsewhere")
        );
        // an include the caller did not follow, or files not read: unknown
        assert_eq!(
            judge(&with("@include /etc/other"), &groups),
            Outcome::Unknown("others_unread")
        );
        assert_eq!(
            judge(&with("#includedir /opt/x"), &groups),
            Outcome::Unknown("others_unread")
        );
        assert_eq!(
            h14(EntrySudoers {
                others: None,
                ..seen(&good)
            }),
            Outcome::Unknown("others_unread")
        );
        assert_eq!(
            h14(EntrySudoers {
                account: None,
                ..seen(&good)
            }),
            Outcome::Unknown("others_unread")
        );
        // Defaults missing (variables not kept: every request reads as empty) or twice
        let no_defaults: String = good
            .lines()
            .filter(|l| !l.starts_with("Defaults"))
            .map(|l| alloc::format!("{l}\n"))
            .collect();
        assert_eq!(h14(seen(&no_defaults)), Outcome::Red("sudoers_defaults"));
        let twice = alloc::format!("{good}{SUDOERS_DEFAULTS}\n");
        assert_eq!(h14(seen(&twice)), Outcome::Red("sudoers_defaults"));
        // owner and mode
        for om in [
            (0, 0, 0o640),
            (0, 0, 0o444),
            (1000, 0, 0o440),
            (0, 27, 0o440),
        ] {
            let e = EntrySudoers {
                owner_mode: Some(om),
                ..seen(&good)
            };
            assert_eq!(h14(e), Outcome::Red("sudoers_mode"), "{om:?}");
        }
        // visudo
        assert_eq!(
            h14(EntrySudoers {
                visudo_ok: Some(false),
                ..seen(&good)
            }),
            Outcome::Red("sudoers_invalid")
        );
        assert_eq!(
            h14(EntrySudoers {
                visudo_ok: None,
                ..seen(&good)
            }),
            Outcome::Unknown("visudo_unread")
        );
        // not set up yet: a warning; could not look: unknown, never green
        assert_eq!(
            h14(EntrySudoers {
                present: Some(false),
                ..EntrySudoers::default()
            }),
            Outcome::Warn("entry_not_set_up")
        );
        assert_eq!(h14(EntrySudoers::default()), Outcome::Unknown("unread"));
        assert_eq!(
            h14(EntrySudoers {
                text: None,
                ..seen(&good)
            }),
            Outcome::Unknown("unread")
        );
        assert_eq!(
            h14(EntrySudoers {
                owner_mode: None,
                ..seen(&good)
            }),
            Outcome::Unknown("unread")
        );
    }

    #[test]
    fn s06_drift_counts() {
        assert_eq!(s06_drift(Some(0)).outcome, Outcome::Green);
        assert_eq!(s06_drift(Some(2)).outcome, Outcome::Red("drift"));
        assert_eq!(s06_drift(None).outcome, Outcome::Unknown("unread"));
    }
}
