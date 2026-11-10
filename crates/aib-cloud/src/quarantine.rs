// SPDX-License-Identifier: Apache-2.0
//! `site quarantine` / `unquarantine` as decisions (4.4.1 §1.3 "預先授權的緊急隔離",
//! §12; design notes: 33e28 and the dev-order review, 11-10). A site found attacking the
//! outside world is cut off — no egress, no route, no customer ssh — without deleting
//! anything. The provider locks the **whole host** on an abuse report, so this is the one
//! thing that protects every other site from one bad one. No I/O here.
//!
//! The rules this module holds:
//! 1. **One site only.** A plan is built from that site's spec and touches that site's
//!    firewall file and that site's block in the edge configuration; every other byte of
//!    the edge configuration comes out unchanged.
//! 2. **Egress first.** The firewall file is replaced and the CT's existing connections
//!    are dropped before the route is touched: changing rules does not end a connection
//!    that is already sending.
//! 3. **What was there is kept verbatim** in a record written *before* the first change,
//!    and `unquarantine` puts exactly that back. Nothing is regenerated.
//! 4. **A second quarantine never overwrites the record**: the originals would be lost.
//! 5. **Quarantined is what the firewall file says**, not what a record or a running rule
//!    says ([`is_quarantined`]): the file survives reloads and reboots.
//! 6. **Nothing else lifts it.** Resume, start, restore and adding a domain are refused
//!    for a quarantined site ([`guard`]); only `unquarantine` (L2) ends it.
//! 7. **A failure leaves the site cut off.** A quarantine plan has no way back and arms
//!    no timed revert: reverting on error would put the attack back on the wire.
//! 8. While a site is quarantined, the firewall file drift should expect is the
//!    quarantine one ([`expected_firewall`]).
//!
//! What a quarantine does **not** stop, for the runbook: mail sent with the site's mail
//! key from somewhere else (a leaked key is revoked or rotated separately); and what it
//! is expected to break: the site's uptime monitor (503), backups the site pushes out
//! itself (host-side backups do not use the CT's network and carry on).

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::{json, Value};

use crate::site::{caddy_marker, caddy_swap_block, desired_firewall, Error, SiteSpec};

/// The firewall group a quarantined CT is put in. It is defined on the host with no
/// rules: with `policy_in` / `policy_out` DROP in the CT's file that means nothing in and
/// nothing out. Management reaches the CT through the host (`pct`), not over the network.
pub const GROUP: &str = "ez2-quarantine-v1";

/// The firewall file of a quarantined CT. No customer-ssh lines: that door closes too.
pub fn firewall() -> String {
    alloc::format!(
        "[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: DROP\nipfilter: 1\n\n[RULES]\nGROUP {GROUP}\n"
    )
}

/// Whether a CT firewall file is the quarantine one: firewall on, both policies DROP and
/// the quarantine group the only rule. This is the test of "is this site quarantined";
/// it reads what survives a reboot.
pub fn is_quarantined(ct_fw: &str) -> bool {
    let mut in_rules = false;
    let mut rules: Vec<&str> = Vec::new();
    let mut options: Vec<&str> = Vec::new();
    // comment lines are not rules: a hand-added note must not turn "quarantined" into "not"
    for line in ct_fw
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
    {
        if line.starts_with('[') {
            in_rules = line.eq_ignore_ascii_case("[RULES]");
        } else if in_rules {
            rules.push(line);
        } else {
            options.push(line);
        }
    }
    let has = |o: &str| options.contains(&o);
    has("enable: 1")
        && has("policy_in: DROP")
        && has("policy_out: DROP")
        && rules.len() == 1
        && rules[0].strip_prefix("GROUP ").map(str::trim) == Some(GROUP)
}

/// The firewall file drift should expect for a site.
pub fn expected_firewall(spec: &SiteSpec, quarantined: bool) -> String {
    if quarantined {
        firewall()
    } else {
        desired_firewall(&spec.fw_group, spec.ssh_port)
    }
}

/// The edge block of a quarantined site: the quarantine page with 503, nothing proxied.
pub fn caddy_block(spec: &SiteSpec, page_path: &str) -> String {
    let (dir, file) = page_path
        .rsplit_once('/')
        .unwrap_or(("/srv/errors", page_path));
    alloc::format!(
        "{marker}\n{hosts} {{\n\timport ez2_whitelabel\n\t# ez2:quarantined\n\theader Cache-Control \"no-store\"\n\troot * {dir}\n\trewrite * /{file}\n\tfile_server {{\n\t\tstatus 503\n\t}}\n}}\n",
        marker = caddy_marker(&spec.code),
        hosts = spec.hosts.join(" "),
    )
}

/// This site's block in the edge configuration, verbatim, marker line to closing brace.
/// Refused when the site has no block, more than one, or one that never closes.
pub fn caddy_block_text(caddyfile: &str, code: &str) -> Result<String, Error> {
    let marker = caddy_marker(code);
    let lines: Vec<&str> = caddyfile.lines().collect();
    let starts: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.trim_end() == marker)
        .map(|(i, _)| i)
        .collect();
    let [start] = starts.as_slice() else {
        return Err(Error::Observed(alloc::format!(
            "Caddyfile has {} blocks for {code}",
            starts.len()
        )));
    };
    let len = lines[*start..]
        .iter()
        .position(|l| *l == "}")
        .ok_or_else(|| Error::Observed(alloc::format!("block for {code} never closes")))?;
    // the closing line must be this block's own: if another site's marker comes first,
    // this block's brace is malformed (trailing blank, say) and the text up to the next
    // "}" would be two sites' worth
    if lines[*start + 1..*start + len]
        .iter()
        .any(|l| l.trim_start().starts_with("# site:"))
    {
        return Err(Error::Observed(alloc::format!(
            "block for {code} runs into the next site's"
        )));
    }
    let mut out = String::new();
    for l in &lines[*start..=*start + len] {
        out.push_str(l);
        out.push('\n');
    }
    Ok(out)
}

/// What was there before, kept so that `unquarantine` can put exactly it back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    /// Site code.
    pub site: String,
    /// CT id.
    pub ct: u32,
    /// The operation that quarantined it.
    pub op: String,
    /// Why, as a short code (`abuse_report`, `outbound_spam`, `operator`, …).
    pub reason: String,
    /// The CT's firewall file before, verbatim. Empty when the CT had **no** firewall
    /// file — no firewall at all, egress wide open, the site that most needs this.
    pub prev_firewall: String,
    /// The site's edge block before, verbatim.
    pub prev_caddy_block: String,
}

const RECORD_KEYS: &[&str] = &[
    "site",
    "ct",
    "op",
    "reason",
    "prev_firewall",
    "prev_caddy_block",
];

fn valid_reason(reason: &str) -> bool {
    !reason.is_empty()
        && reason.len() <= 40
        && reason
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
}

impl Record {
    /// The record as JSON.
    pub fn to_json(&self) -> Value {
        json!({
            "site": self.site, "ct": self.ct, "op": self.op, "reason": self.reason,
            "prev_firewall": self.prev_firewall, "prev_caddy_block": self.prev_caddy_block,
        })
    }

    /// Read a record back, strictly: every key, no other key, nothing empty.
    pub fn from_json(v: &Value) -> Result<Self, Error> {
        let bad = |what: &str| Error::Observed(alloc::format!("quarantine record: {what}"));
        let obj = v.as_object().ok_or_else(|| bad("not an object"))?;
        if obj.len() != RECORD_KEYS.len() || !obj.keys().all(|k| RECORD_KEYS.contains(&k.as_str()))
        {
            return Err(bad("keys"));
        }
        let text = |k: &str| {
            obj.get(k)
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(ToOwned::to_owned)
                .ok_or_else(|| bad(k))
        };
        let ct = obj
            .get("ct")
            .and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| bad("ct"))?;
        let record = Record {
            site: text("site")?,
            ct,
            op: text("op")?,
            reason: text("reason")?,
            // empty = the CT had no firewall file
            prev_firewall: obj
                .get("prev_firewall")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned)
                .ok_or_else(|| bad("prev_firewall"))?,
            prev_caddy_block: text("prev_caddy_block")?,
        };
        if !valid_reason(&record.reason) || !crate::site::valid_code(&record.site) {
            return Err(bad("site or reason"));
        }
        Ok(record)
    }
}

/// One thing the host shell does, in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// Write the record to durable storage and sync it. If this fails, stop: nothing has
    /// been changed and the site is **not** to be reported as quarantined.
    SaveRecord(Record),
    /// Replace the CT's firewall file with this text (write beside, sync, rename).
    WriteFirewall {
        /// CT id.
        ct: u32,
        /// The whole file.
        text: String,
    },
    /// Have the firewall compile and apply its configuration; a compile error is a failure.
    /// The shell returns from this only when the rules are **in effect in the kernel** for
    /// this CT (it waits until it can see them): the firewall daemon applies changes in
    /// the background, and connections dropped before the new rules are loaded can simply
    /// be opened again and then stay allowed as established.
    ApplyFirewall,
    /// Drop every tracked connection from and to this address.
    DropConnections {
        /// The CT's internal address.
        ip: String,
    },
    /// Replace the edge configuration with this text, validate it, reload. The text
    /// differs from the one read only in this site's block.
    WriteCaddyfile {
        /// The whole file.
        text: String,
    },
    /// Check from outside the CT that the state is the one wanted.
    Verify {
        /// `true`: egress blocked and the quarantine page served; `false`: the site back.
        quarantined: bool,
    },
    /// Move the record aside (kept as evidence), after a verified unquarantine.
    ArchiveRecord,
}

/// What a quarantine request amounts to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Quarantine {
    /// Do these, in this order. On a failure stop where it failed and leave everything
    /// done so far in place.
    Do(Vec<Action>),
    /// The site is already quarantined and its record is intact: only verify. The record
    /// is not rewritten.
    Already(Vec<Action>),
}

/// Plan a quarantine. `ct_fw` and `caddyfile` are what the host has now; `existing` is the
/// site's quarantine record if one is on disk.
pub fn quarantine_plan(
    spec: &SiteSpec,
    op: &str,
    reason: &str,
    page_path: &str,
    ct_fw: &str,
    caddyfile: &str,
    existing: Option<&Record>,
) -> Result<Quarantine, Error> {
    let refuse = |what: &str| {
        Err(Error::Observed(alloc::format!(
            "quarantine {}: {what}",
            spec.code
        )))
    };
    if !crate::op::valid_op_id(op) {
        return refuse("operation id");
    }
    if !valid_reason(reason) {
        return refuse("reason must be a short code (a-z 0-9 _)");
    }
    // goes verbatim into the edge configuration, on the emergency path: plain paths only
    if !page_path.starts_with('/')
        || !page_path
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._/-".contains(&c))
    {
        return refuse("page path");
    }
    if existing.is_some_and(|r| r.site != spec.code || r.ct != spec.ct) {
        return refuse("the record on disk belongs to another site");
    }
    let cut_off = is_quarantined(ct_fw);
    let block = caddy_block(spec, page_path);
    let record = match (cut_off, existing) {
        // already cut off, originals safe: finish whatever is missing, keep the record
        (true, Some(r)) => r.clone(),
        // cut off but nobody kept what was there before: a person has to look
        (true, None) => return refuse(
            "already on the quarantine firewall but there is no record of what was there before",
        ),
        // a record without the firewall: an earlier attempt stopped right after saving
        // it. Its originals are the true ones (nothing was changed after it was written).
        // … but only if the host still looks exactly as that record says. A record left
        // behind by an unquarantine that restored everything and stopped before archiving
        // describes an older incident: reusing it would hide this one's reason and, on the
        // way out, put back a firewall and a block from before whatever changed since.
        (false, Some(r)) => {
            if r.prev_firewall != ct_fw
                || r.prev_caddy_block != caddy_block_text(caddyfile, &spec.code)?
            {
                return refuse(
                    "a quarantine record is on disk but the host no longer matches it (an older incident?): archive it first",
                );
            }
            r.clone()
        }
        (false, None) => Record {
            site: spec.code.clone(),
            ct: spec.ct,
            op: op.to_owned(),
            reason: reason.to_owned(),
            prev_firewall: ct_fw.to_owned(),
            prev_caddy_block: caddy_block_text(caddyfile, &spec.code)?,
        },
    };
    let new_caddyfile = caddy_swap_block(caddyfile, &spec.code, &block)?;
    let route_done = new_caddyfile == caddyfile;
    if cut_off && route_done {
        return Ok(Quarantine::Already(alloc::vec![Action::Verify {
            quarantined: true
        }]));
    }
    let mut actions = Vec::new();
    if existing.is_none() {
        actions.push(Action::SaveRecord(record));
    }
    if !cut_off {
        actions.push(Action::WriteFirewall {
            ct: spec.ct,
            text: firewall(),
        });
        actions.push(Action::ApplyFirewall);
    }
    // always: a connection that was open before the rules changed keeps flowing
    let cut = Action::DropConnections { ip: spec.ip() };
    actions.push(cut.clone());
    if !route_done {
        actions.push(Action::WriteCaddyfile {
            text: new_caddyfile,
        });
    }
    // and once more at the end: whatever was opened between the first drop and now
    actions.push(cut);
    actions.push(Action::Verify { quarantined: true });
    Ok(Quarantine::Do(actions))
}

/// Plan the way out (L2). Puts back exactly what the record holds. Refused when the site
/// is not on the quarantine firewall (someone changed it by hand: look first) or when the
/// record is another site's.
pub fn unquarantine_plan(
    spec: &SiteSpec,
    record: &Record,
    ct_fw: &str,
    caddyfile: &str,
) -> Result<Vec<Action>, Error> {
    let refuse = |what: &str| {
        Err(Error::Observed(alloc::format!(
            "unquarantine {}: {what}",
            spec.code
        )))
    };
    if record.site != spec.code || record.ct != spec.ct {
        return refuse("the record belongs to another site");
    }
    if !is_quarantined(ct_fw) {
        return refuse("the CT is not on the quarantine firewall");
    }
    if is_quarantined(&record.prev_firewall) {
        return refuse("the record's previous firewall is itself the quarantine one");
    }
    // the block must be exactly one block, this site's: anything after its closing brace
    // (another site's block, say) would be written into the edge configuration with it
    if caddy_block_text(&record.prev_caddy_block, &spec.code)
        .ok()
        .as_deref()
        != Some(record.prev_caddy_block.as_str())
    {
        return refuse("the record's edge block is not exactly this site's block");
    }
    // the one place where what comes back is not what was there: a CT that had no
    // firewall file gets the file its record says it should have, not "no firewall" again
    let firewall_back = if record.prev_firewall.is_empty() {
        desired_firewall(&spec.fw_group, spec.ssh_port)
    } else {
        record.prev_firewall.clone()
    };
    let restored = caddy_swap_block(caddyfile, &spec.code, &record.prev_caddy_block)?;
    Ok(alloc::vec![
        Action::WriteCaddyfile { text: restored },
        Action::WriteFirewall {
            ct: spec.ct,
            text: firewall_back,
        },
        Action::ApplyFirewall,
        Action::Verify { quarantined: false },
        Action::ArchiveRecord,
    ])
}

/// Plan the clean-up after an `unquarantine` that put everything back and stopped before
/// archiving its record. In that state [`guard`] refuses resume / start (a record is on
/// disk), [`unquarantine_plan`] refuses (the CT is not on the quarantine firewall) and
/// [`quarantine_plan`] refuses (the record is stale) — this is the way out. It is allowed
/// only when the host is exactly what that unquarantine would have left: not quarantined,
/// the firewall file the record would restore, and the edge block the record holds.
/// Anything else is a half-finished state a person has to look at.
pub fn archive_stale_plan(
    spec: &SiteSpec,
    record: &Record,
    ct_fw: &str,
    caddyfile: &str,
) -> Result<Vec<Action>, Error> {
    let refuse = |what: &str| {
        Err(Error::Observed(alloc::format!(
            "archive record {}: {what}",
            spec.code
        )))
    };
    if record.site != spec.code || record.ct != spec.ct {
        return refuse("the record belongs to another site");
    }
    if is_quarantined(ct_fw) {
        return refuse("the CT is still on the quarantine firewall: unquarantine instead");
    }
    let restored_firewall = if record.prev_firewall.is_empty() {
        desired_firewall(&spec.fw_group, spec.ssh_port)
    } else {
        record.prev_firewall.clone()
    };
    if ct_fw != restored_firewall {
        return refuse("the firewall file is not the one this record restores");
    }
    if caddy_block_text(caddyfile, &spec.code)? != record.prev_caddy_block {
        return refuse("the edge block is not the one this record restores");
    }
    Ok(alloc::vec![
        Action::Verify { quarantined: false },
        Action::ArchiveRecord,
    ])
}

/// Operations that would put a site back on the network.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reopens {
    /// `site resume`.
    Resume,
    /// `site start` / `restart`.
    Start,
    /// `site restore` (switching the route to a restored CT).
    Restore,
    /// `edge domain add`.
    DomainAdd,
}

/// Refuse anything that would reopen a quarantined site. Only `unquarantine` ends a
/// quarantine. `ct_fw` is the CT's firewall file as it is now; `record` is the site's
/// quarantine record if one is on disk. Either is enough: a record without the firewall
/// is a quarantine that was interrupted right after it began, and that is not the moment
/// to restore or resume the site.
pub fn guard(code: &str, ct_fw: &str, record: Option<&Record>, what: Reopens) -> Result<(), Error> {
    if record.is_some() || is_quarantined(ct_fw) {
        return Err(Error::Observed(alloc::format!(
            "{code} is quarantined: {what:?} refused (unquarantine first)"
        )));
    }
    Ok(())
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use crate::site::{caddy_block_upstream, Limits};

    const OP: &str = "20261110T014500Z-0a1b2c3d";
    const PAGE: &str = "/srv/errors/quarantined.html";

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
        .with_ssh_port(2112)
        .unwrap()
    }

    fn normal_fw() -> String {
        desired_firewall("ez2-site-v2", Some(2112))
    }

    /// An edge configuration with a global block, this site, and another site whose block
    /// has a route the generic template would not produce.
    fn caddyfile() -> String {
        alloc::format!(
            "{{\n\temail ops@example.test\n}}\n\n{}\n# site:other\nother.example.test {{\n\thandle /e2/* {{\n\t\treverse_proxy 10.77.0.20:8081\n\t}}\n\treverse_proxy 10.77.0.20:80\n}}\n",
            caddy_block_upstream(&spec(), "10.77.0.12")
        )
    }

    fn other_block(file: &str) -> String {
        caddy_block_text(file, "other").unwrap()
    }

    fn do_plan(ct_fw: &str, file: &str, existing: Option<&Record>) -> Quarantine {
        quarantine_plan(&spec(), OP, "abuse_report", PAGE, ct_fw, file, existing).unwrap()
    }

    fn actions(q: Quarantine) -> Vec<Action> {
        match q {
            Quarantine::Do(a) | Quarantine::Already(a) => a,
        }
    }

    fn written_caddyfile(a: &[Action]) -> &str {
        a.iter()
            .find_map(|x| match x {
                Action::WriteCaddyfile { text } => Some(text.as_str()),
                _ => None,
            })
            .unwrap()
    }

    #[test]
    fn the_quarantine_firewall_is_recognised_and_nothing_else_is() {
        assert!(is_quarantined(&firewall()));
        assert!(!is_quarantined(&normal_fw()));
        assert!(!is_quarantined(""));
        // the group plus anything else is not a quarantine
        let extra = firewall().replace("GROUP", "IN ACCEPT -p tcp -dport 22\nGROUP");
        assert!(!is_quarantined(&extra));
        let open = firewall().replace("policy_out: DROP", "policy_out: ACCEPT");
        assert!(!is_quarantined(&open));
        let off = firewall().replace("enable: 1", "enable: 0");
        assert!(!is_quarantined(&off));
        let other = firewall().replace(GROUP, "ez2-site-v2");
        assert!(!is_quarantined(&other));
    }

    #[test]
    fn plan_order_is_record_egress_connections_route_verify() {
        let a = actions(do_plan(&normal_fw(), &caddyfile(), None));
        let kinds: Vec<&str> = a
            .iter()
            .map(|x| match x {
                Action::SaveRecord(_) => "record",
                Action::WriteFirewall { .. } => "firewall",
                Action::ApplyFirewall => "apply",
                Action::DropConnections { .. } => "drop",
                Action::WriteCaddyfile { .. } => "route",
                Action::Verify { .. } => "verify",
                Action::ArchiveRecord => "archive",
            })
            .collect();
        assert_eq!(
            kinds,
            ["record", "firewall", "apply", "drop", "route", "drop", "verify"]
        );
        assert_eq!(
            a[1],
            Action::WriteFirewall {
                ct: 112,
                text: firewall()
            }
        );
        assert_eq!(
            a[3],
            Action::DropConnections {
                ip: "10.77.0.12".to_owned()
            }
        );
        assert_eq!(a[6], Action::Verify { quarantined: true });
    }

    #[test]
    fn the_record_keeps_what_was_there_verbatim() {
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(r) = &a[0] else {
            panic!("record first");
        };
        assert_eq!(r.prev_firewall, normal_fw());
        assert_eq!(
            r.prev_caddy_block,
            caddy_block_upstream(&spec(), "10.77.0.12")
        );
        assert_eq!(
            (r.site.as_str(), r.ct, r.op.as_str(), r.reason.as_str()),
            ("site2", 112, OP, "abuse_report")
        );
        assert_eq!(Record::from_json(&r.to_json()).unwrap(), *r);
    }

    #[test]
    fn only_this_sites_block_changes() {
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        let new = written_caddyfile(&a);
        assert_eq!(other_block(new), other_block(&file));
        assert!(new.starts_with("{\n\temail ops@example.test\n}\n"));
        let mine = caddy_block_text(new, "site2").unwrap();
        assert!(mine.contains("# ez2:quarantined") && mine.contains("status 503"));
        assert!(
            !mine.contains("reverse_proxy"),
            "a quarantined site must not reach the CT"
        );
        // everything outside this site's block is byte for byte what it was
        let before = file.replace(&caddy_block_text(&file, "site2").unwrap(), "");
        let after = new.replace(&mine, "");
        assert_eq!(before, after);
    }

    #[test]
    fn a_second_quarantine_keeps_the_first_record() {
        let file = caddyfile();
        let first = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(record) = &first[0] else {
            panic!("record first");
        };
        let quarantined_file = written_caddyfile(&first).to_owned();
        // everything done: only a verify, and no record is written
        let again = do_plan(&firewall(), &quarantined_file, Some(record));
        assert_eq!(
            again,
            Quarantine::Already(alloc::vec![Action::Verify { quarantined: true }])
        );
        // firewall done, route not (an earlier run failed at the edge): finish, and still
        // write no record — a new one would hold the quarantine firewall as "previous"
        let finish = actions(do_plan(&firewall(), &file, Some(record)));
        assert!(!finish
            .iter()
            .any(|x| matches!(x, Action::SaveRecord(_) | Action::WriteFirewall { .. })));
        assert!(finish.contains(&Action::DropConnections {
            ip: "10.77.0.12".to_owned()
        }));
        assert!(finish
            .iter()
            .any(|x| matches!(x, Action::WriteCaddyfile { .. })));
        // record saved, nothing else done: its originals are used, it is not rewritten
        let resume = actions(do_plan(&normal_fw(), &file, Some(record)));
        assert!(!resume.iter().any(|x| matches!(x, Action::SaveRecord(_))));
        assert!(resume
            .iter()
            .any(|x| matches!(x, Action::WriteFirewall { .. })));
    }

    /// Review of #623 segment 5 (1ced7 M1): a record left by an unquarantine that stopped
    /// before archiving must not be reused once the host has moved on.
    #[test]
    fn a_stale_record_is_not_reused() {
        let file = caddyfile();
        let first = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(old) = &first[0] else {
            panic!("record first");
        };
        // host exactly as the record says: the interrupted attempt is finished
        assert!(quarantine_plan(
            &spec(),
            OP,
            "operator",
            PAGE,
            &normal_fw(),
            &file,
            Some(old)
        )
        .is_ok());
        // since then: another firewall group, or another domain on the block
        let new_fw = normal_fw().replace("ez2-site-v2", "ez2-site-v3");
        assert!(quarantine_plan(&spec(), OP, "operator", PAGE, &new_fw, &file, Some(old)).is_err());
        let new_file = file.replace(
            "site2.example.test {",
            "site2.example.test shop.example.test {",
        );
        assert_ne!(new_file, file);
        assert!(quarantine_plan(
            &spec(),
            OP,
            "operator",
            PAGE,
            &normal_fw(),
            &new_file,
            Some(old)
        )
        .is_err());
    }

    /// Review of #623 segment 5 (1ced7 L1 / L2).
    #[test]
    fn block_ends_and_page_paths() {
        // this site's closing brace has a trailing blank: the next "}" is another site's
        let file = caddyfile();
        let mine = caddy_block_text(&file, "site2").unwrap();
        let broken = file.replace(&mine, &alloc::format!("{} \n", mine.trim_end()));
        assert_ne!(broken, file);
        assert!(caddy_block_text(&broken, "site2").is_err());
        assert!(caddy_block_text(&broken, "other").is_ok());
        for page in [
            "/srv/e/q.html\"",
            "/srv/e/q.html#x",
            "/srv/e/q\t.html",
            "/srv/e/q html",
        ] {
            let q = quarantine_plan(&spec(), OP, "operator", page, &normal_fw(), &file, None);
            assert!(q.is_err(), "{page:?}");
        }
    }

    #[test]
    fn cut_off_without_a_record_needs_a_person() {
        let q = quarantine_plan(
            &spec(),
            OP,
            "operator",
            PAGE,
            &firewall(),
            &caddyfile(),
            None,
        );
        assert!(q.is_err());
    }

    #[test]
    fn a_quarantine_plan_has_no_way_back() {
        // rule 7: nothing in a plan restores the previous firewall or the previous route
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        for x in &a[1..] {
            match x {
                Action::WriteFirewall { text, .. } => assert!(is_quarantined(text)),
                Action::WriteCaddyfile { text } => {
                    assert!(!text.contains("reverse_proxy 10.77.0.12"))
                }
                Action::ArchiveRecord | Action::Verify { quarantined: false } => panic!("{x:?}"),
                _ => {}
            }
        }
    }

    #[test]
    fn refused_inputs() {
        let fw = normal_fw();
        let file = caddyfile();
        let plan = |op: &str, reason: &str, page: &str| {
            quarantine_plan(&spec(), op, reason, page, &fw, &file, None)
        };
        assert!(plan(OP, "abuse_report", PAGE).is_ok());
        assert!(plan("op1", "abuse_report", PAGE).is_err());
        assert!(plan(OP, "Abuse report!", PAGE).is_err());
        assert!(plan(OP, "", PAGE).is_err());
        assert!(plan(OP, "operator", "errors/q.html").is_err());
        assert!(plan(OP, "operator", "/srv/x }\nimport evil").is_err());
        // no block for this site, or two
        assert!(quarantine_plan(
            &spec(),
            OP,
            "operator",
            PAGE,
            &fw,
            "# site:other\nx {\n}\n",
            None
        )
        .is_err());
        let twice = alloc::format!("{file}{}", caddy_block_upstream(&spec(), "10.77.0.12"));
        assert!(quarantine_plan(&spec(), OP, "operator", PAGE, &fw, &twice, None).is_err());
        // somebody else's record
        let mut r = Record {
            site: "site3".to_owned(),
            ct: 113,
            op: OP.to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: fw.clone(),
            prev_caddy_block: "x".to_owned(),
        };
        assert!(quarantine_plan(&spec(), OP, "operator", PAGE, &fw, &file, Some(&r)).is_err());
        r.site = "site2".to_owned();
        assert!(quarantine_plan(&spec(), OP, "operator", PAGE, &fw, &file, Some(&r)).is_err());
    }

    #[test]
    fn unquarantine_puts_back_exactly_what_was_there() {
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(record) = &a[0] else {
            panic!("record first");
        };
        let quarantined_file = written_caddyfile(&a).to_owned();
        let back = unquarantine_plan(&spec(), record, &firewall(), &quarantined_file).unwrap();
        assert_eq!(
            back,
            [
                Action::WriteCaddyfile { text: file.clone() },
                Action::WriteFirewall {
                    ct: 112,
                    text: normal_fw()
                },
                Action::ApplyFirewall,
                Action::Verify { quarantined: false },
                Action::ArchiveRecord,
            ]
        );
        // a block edited by hand before the quarantine comes back as it was, not as the
        // template would write it
        let custom = file.replace(
            "\tencode zstd gzip\n",
            "\tencode zstd gzip\n\thandle /e2/* {\n\t\trespond 204\n\t}\n",
        );
        let a = actions(do_plan(&normal_fw(), &custom, None));
        let Action::SaveRecord(record) = &a[0] else {
            panic!("record first");
        };
        let back = unquarantine_plan(&spec(), record, &firewall(), written_caddyfile(&a)).unwrap();
        assert_eq!(back[0], Action::WriteCaddyfile { text: custom });
    }

    #[test]
    fn unquarantine_refusals() {
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(record) = &a[0] else {
            panic!("record first");
        };
        let q_file = written_caddyfile(&a);
        // not quarantined now
        assert!(unquarantine_plan(&spec(), record, &normal_fw(), q_file).is_err());
        // a record that would "restore" the quarantine itself
        let mut r = record.clone();
        r.prev_firewall = firewall();
        assert!(unquarantine_plan(&spec(), &r, &firewall(), q_file).is_err());
        // a record whose block is another site's
        let mut r = record.clone();
        r.prev_caddy_block = other_block(&file);
        assert!(unquarantine_plan(&spec(), &r, &firewall(), q_file).is_err());
        let mut r = record.clone();
        r.ct = 113;
        assert!(unquarantine_plan(&spec(), &r, &firewall(), q_file).is_err());
    }

    #[test]
    fn nothing_else_reopens_a_quarantined_site() {
        for what in [
            Reopens::Resume,
            Reopens::Start,
            Reopens::Restore,
            Reopens::DomainAdd,
        ] {
            assert!(guard("site2", &firewall(), None, what).is_err(), "{what:?}");
            assert!(guard("site2", &normal_fw(), None, what).is_ok(), "{what:?}");
        }
    }

    #[test]
    fn drift_expects_the_quarantine_firewall_while_quarantined() {
        assert_eq!(expected_firewall(&spec(), true), firewall());
        assert_eq!(expected_firewall(&spec(), false), normal_fw());
        assert!(is_quarantined(&expected_firewall(&spec(), true)));
    }

    #[test]
    fn records_are_read_strictly() {
        let r = Record {
            site: "site2".to_owned(),
            ct: 112,
            op: OP.to_owned(),
            reason: "operator".to_owned(),
            prev_firewall: normal_fw(),
            prev_caddy_block: "# site:site2\nx {\n}\n".to_owned(),
        };
        let good = r.to_json();
        assert_eq!(Record::from_json(&good).unwrap(), r);
        for key in RECORD_KEYS {
            let mut v = good.clone();
            v.as_object_mut().unwrap().remove(*key);
            assert!(Record::from_json(&v).is_err(), "{key}");
        }
        let mut v = good.clone();
        v["extra"] = json!(1);
        assert!(Record::from_json(&v).is_err());
        let mut v = good.clone();
        v["prev_caddy_block"] = json!("");
        assert!(Record::from_json(&v).is_err());
        let mut v = good;
        v["reason"] = json!("see ticket #4");
        assert!(Record::from_json(&v).is_err());
    }

    /// Review of #623 segment 5 (33e28): M1 connections are dropped again after the route,
    /// M2 a CT without a firewall file can be quarantined and comes back with the file it
    /// should have, L1 comment lines and a record without the firewall, L2 the record's
    /// block is exactly one block.
    #[test]
    fn review_segment_5() {
        let file = caddyfile();
        // M1
        let a = actions(do_plan(&normal_fw(), &file, None));
        let drops: Vec<usize> = a
            .iter()
            .enumerate()
            .filter(|(_, x)| matches!(x, Action::DropConnections { .. }))
            .map(|(i, _)| i)
            .collect();
        let route = a
            .iter()
            .position(|x| matches!(x, Action::WriteCaddyfile { .. }))
            .unwrap();
        assert_eq!(drops.len(), 2);
        assert!(drops[0] < route && route < drops[1]);
        // M2
        let a = actions(do_plan("", &file, None));
        let Action::SaveRecord(record) = &a[0] else {
            panic!("record first");
        };
        assert_eq!(record.prev_firewall, "");
        assert_eq!(Record::from_json(&record.to_json()).unwrap(), *record);
        assert!(a.contains(&Action::WriteFirewall {
            ct: 112,
            text: firewall()
        }));
        let back = unquarantine_plan(&spec(), record, &firewall(), written_caddyfile(&a)).unwrap();
        assert!(back.contains(&Action::WriteFirewall {
            ct: 112,
            text: normal_fw()
        }));
        // L1 (i)
        let noted = firewall().replace("GROUP", "# cut off 11-10, see ticket\nGROUP");
        assert!(is_quarantined(&noted));
        // L1 (ii)
        assert!(guard("site2", &normal_fw(), Some(record), Reopens::Restore).is_err());
        // L2
        let mut two = record.clone();
        two.prev_firewall = normal_fw();
        two.prev_caddy_block = alloc::format!("{}{}", two.prev_caddy_block, other_block(&file));
        assert!(unquarantine_plan(&spec(), &two, &firewall(), written_caddyfile(&a)).is_err());
    }

    /// Review after #623 (1ced7 L): an unquarantine that restored everything and stopped
    /// before archiving leaves a record nothing else can get past; archiving it is allowed
    /// only when the host is exactly what that unquarantine left.
    #[test]
    fn a_stale_record_can_be_archived_when_the_host_is_fully_restored() {
        let file = caddyfile();
        let a = actions(do_plan(&normal_fw(), &file, None));
        let Action::SaveRecord(record) = &a[0] else {
            panic!("record first");
        };
        let quarantined_file = written_caddyfile(&a);
        // the stuck state: everything restored, record still there
        assert!(guard("site2", &normal_fw(), Some(record), Reopens::Resume).is_err());
        assert!(unquarantine_plan(&spec(), record, &normal_fw(), &file).is_err());
        assert_eq!(
            archive_stale_plan(&spec(), record, &normal_fw(), &file).unwrap(),
            [Action::Verify { quarantined: false }, Action::ArchiveRecord]
        );
        // still quarantined, route not restored, another firewall, another site's record
        assert!(archive_stale_plan(&spec(), record, &firewall(), quarantined_file).is_err());
        assert!(archive_stale_plan(&spec(), record, &normal_fw(), quarantined_file).is_err());
        let other_fw = normal_fw().replace("ez2-site-v2", "ez2-site-v3");
        assert!(archive_stale_plan(&spec(), record, &other_fw, &file).is_err());
        let mut theirs = record.clone();
        theirs.ct = 113;
        assert!(archive_stale_plan(&spec(), &theirs, &normal_fw(), &file).is_err());
        // a CT that had no firewall file: what unquarantine wrote is the desired file
        let mut none = record.clone();
        none.prev_firewall = String::new();
        assert!(archive_stale_plan(&spec(), &none, &normal_fw(), &file).is_ok());
        assert!(archive_stale_plan(&spec(), &none, "", &file).is_err());
    }
}
