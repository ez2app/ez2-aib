// SPDX-License-Identifier: Apache-2.0
//! E7 — how many sites a customer may open (DEV 4.6 §4 / §15.3). The quota is **the plan**,
//! not a number of sites: "額度 = 一台虛擬機的大小、站數不設固定上限", but opening one more
//! is still admitted against what the plan holds ("開站仍要進場控制"). So the number of
//! sites a customer may have is worked out from the plan: the sites already held plus as
//! many sites on the starting tier ([`Limits::START`]) as still fit in what is left.
//!
//! The plan table is [`arc::PLAN`] (Andy 11-10: one plan on the first host); there is no
//! table of customers to plans yet, so every customer named has that plan. A customer who
//! cannot be told has no plan: the answer is "could not tell", never "fits".
//!
//! What counts against the plan: every **live** record of the customer and every
//! **opening** record (an opening that began and has not ended holds what it will open
//! with). Removed sites are not read at all. Counted per resource the plan enforces and
//! the starting tier names — CPU (the sum of the sites' `cpulimit`, as [`arc::held`]) and
//! memory. Disk is added up and shown, not judged: a record that names no size counts at
//! the starting size [`site::START_DISK_GB`] (Andy 11-12: "10 GB"; disk sizes here are
//! Proxmox's `G`, which is GiB). A record that names a size that is not a whole number of
//! GiB above 0 cannot be read (c477a E7 N3), never counted at the starting size.
//!
//! Also here, the other two numbers of the plan Andy set on 11-12, as decisions only (who
//! is told, and how, is the caller's): **traffic** — 5 TB a month, a notice at 80, 95 and
//! 100 %, each once, and the site is never cut off ([`traffic_notice`]); **mail** — 1,000
//! a site a month free, one notice past it, and mail is never held back ([`mail_notice`],
//! [`mail_held_back`]). The notices are codes ([`traffic_code`], [`MAIL_OVER_FREE`]); the
//! customer's words for them are not written here. Traffic is the plan's, so it is told
//! per customer; mail is per site. A month's notices are remembered in a [`MonthState`]
//! between runs, so each one is told once a month ([`month_run`]); traffic is in decimal
//! GB (5 TB = 5,000 GB, as the plan's bytes), unlike disk.
//!
//! No I/O: the host reads the records and hands them in.
//!
//! Known edges (c477a 11-11 review of 194bec12):
//! * A customer code that is valid but has never been seen gets the whole plan: there is no
//!   table of customers yet, so a typo in `--tenant` is not caught here. The plan marks such
//!   a customer `new_tenant` ([`seen_before`]) for the person reading it.
//! * `site create --apply` (B1) re-plans under the lock and needs the plan to be the same:
//!   it has to re-plan with the **same** `--tenant`. B1 takes the tenant as an argument too
//!   (`--tenant <id>`, or read from the plan file it checks), never a default.
//! * An opening record that names `limits` is rebuilt with [`site::spec_from_record`]: one
//!   missing `ct`, `ip` or `hosts` is unreadable and, since openings without a tenant count
//!   against everyone, makes every customer `quota_unknown` (the safe side). To be tested
//!   with the records B2 really writes when the two meet.
//! * Disk is read from the record's top-level `disk_gb` only; a record without one counts
//!   at [`site::START_DISK_GB`], whatever the container really has. It is shown, not
//!   judged.

use alloc::collections::{BTreeMap, BTreeSet};
use alloc::string::String;
use alloc::vec::Vec;

use serde_json::Value;

use crate::arc::{self, Held, Plan, Resource};
use crate::site::{self, Limits};

/// Why the quota could not be told. Every one is `quota_unknown` to the caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Unknown {
    /// No customer named, or a name that is not a customer code.
    NoTenant,
    /// The records (live or opening) could not be read.
    RecordsUnreadable,
    /// A record of the customer whose site or limits could not be read.
    RecordUnreadable,
}

impl Unknown {
    pub fn as_str(self) -> &'static str {
        match self {
            Unknown::NoTenant => "no_tenant",
            Unknown::RecordsUnreadable => "records_unreadable",
            Unknown::RecordUnreadable => "record_unreadable",
        }
    }
}

/// What the customer holds now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Count {
    /// Live sites of the customer.
    pub live: u32,
    /// Openings under way that count against the customer.
    pub opening: u32,
    /// What all of them hold together.
    pub held: Held,
}

impl Count {
    pub fn sites(&self) -> u32 {
        self.live.saturating_add(self.opening)
    }
}

/// The answer for one more site.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// One more site on the starting tier fits. `allowed` is the number of sites the plan
    /// can hold now (the ones held plus the ones that still fit).
    Fits { count: Count, allowed: u32 },
    /// One more does not fit: `short` is the first resource that runs out.
    Exceeded {
        count: Count,
        allowed: u32,
        short: Resource,
    },
    /// The plan or the count could not be told.
    Unknown(Unknown),
}

impl Verdict {
    /// The word a plan stops at, `None` when it fits.
    pub fn stop(&self) -> Option<&'static str> {
        match self {
            Verdict::Fits { .. } => None,
            Verdict::Exceeded { .. } => Some("quota_exceeded"),
            Verdict::Unknown(_) => Some("quota_unknown"),
        }
    }
}

/// The plan of a customer. One plan on the first host: a valid customer code has
/// [`arc::PLAN`]; anything else has none.
pub fn plan_of(tenant: Option<&str>) -> Result<Plan, Unknown> {
    match tenant {
        Some(t) if site::valid_code(t) => Ok(arc::PLAN),
        _ => Err(Unknown::NoTenant),
    }
}

/// The disk a record holds, in GiB: [`site::START_DISK_GB`] when it names none; a size
/// that is not a whole number above 0 that fits cannot be read (c477a E7 N3).
fn disk_of(rec: &Value) -> Result<u32, Unknown> {
    match rec.get("disk_gb") {
        None => Ok(site::START_DISK_GB),
        Some(v) => v
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .filter(|n| *n > 0)
            .ok_or(Unknown::RecordUnreadable),
    }
}

/// Count what `tenant` holds. `live`: the live records (a record that names no tenant is
/// nobody's, as [`site::record_tenant`]). `opening`: the opening records; one that names
/// this tenant is the customer's, and one that names **no** tenant cannot be told apart
/// from the customer's and is counted against them too — holding room for an opening that
/// may be theirs is the side that cannot over-open. An opening whose site already has a
/// live record is counted once, as the live one. An opening holds [`Limits::START`] unless
/// its record names limits. Removed sites are not handed in.
pub fn count(tenant: &str, live: &[Value], opening: &[Value]) -> Result<Count, Unknown> {
    let mut sites: Vec<(Limits, u32)> = Vec::new();
    let mut codes: BTreeSet<&str> = BTreeSet::new();
    let mut n_live = 0u32;
    for rec in live {
        // no tenant_id: nobody's; a tenant_id that cannot be read: could not tell whose
        // (c477a L1), never nobody's — that would let the customer open past the plan
        if rec.get("tenant_id").is_none() {
            continue;
        }
        let owner = site::record_tenant(rec).ok_or(Unknown::RecordUnreadable)?;
        if owner != tenant {
            continue;
        }
        let spec = site::spec_from_record(rec).map_err(|_| Unknown::RecordUnreadable)?;
        if let Some(code) = rec.get("site").and_then(Value::as_str) {
            codes.insert(code);
        }
        sites.push((spec.limits, disk_of(rec)?));
        n_live = n_live.saturating_add(1);
    }
    let mut n_opening = 0u32;
    for rec in opening {
        let theirs = match rec.get("tenant_id") {
            None => true,
            Some(_) => site::record_tenant(rec) == Some(tenant),
        };
        if !theirs {
            continue;
        }
        let code = rec
            .get("site")
            .and_then(Value::as_str)
            .ok_or(Unknown::RecordUnreadable)?;
        if codes.contains(code) {
            continue;
        }
        let limits = if rec.get("limits").is_some() {
            site::spec_from_record(rec)
                .map_err(|_| Unknown::RecordUnreadable)?
                .limits
        } else {
            Limits::START
        };
        sites.push((limits, disk_of(rec)?));
        n_opening = n_opening.saturating_add(1);
    }
    Ok(Count {
        live: n_live,
        opening: n_opening,
        held: arc::held(&sites),
    })
}

/// Whether any record handed in (live, opening or removed) names `tenant`. A customer
/// never seen before is not refused — there is no table of customers yet — but the plan
/// says so, so a mistyped code is noticed by the person reading it (c477a N1).
pub fn seen_before(tenant: &str, lists: &[&[Value]]) -> bool {
    lists
        .iter()
        .flat_map(|l| l.iter())
        .any(|rec| site::record_tenant(rec) == Some(tenant))
}

/// How many more sites on the starting tier fit in what the plan has left, and the first
/// resource that runs out.
fn room(plan: &Plan, held: &Held) -> (u32, Resource) {
    let start = Limits::START;
    let cpu_left = plan.cores.saturating_mul(100).saturating_sub(held.cpu_x100);
    let mem_left = plan.memory_mb.saturating_sub(held.memory_mb);
    let by_cpu = cpu_left / start.cpulimit_x100.max(1);
    let by_mem = mem_left / start.memory_mb.max(1);
    if by_cpu <= by_mem {
        (by_cpu, Resource::Cpu)
    } else {
        (by_mem, Resource::Memory)
    }
}

/// Whether `tenant` may open one more site. `live` / `opening` are `None` when they could
/// not be read: a list that could not be read is not an empty list.
pub fn judge(tenant: Option<&str>, live: Option<&[Value]>, opening: Option<&[Value]>) -> Verdict {
    let plan = match plan_of(tenant) {
        Ok(p) => p,
        Err(u) => return Verdict::Unknown(u),
    };
    let (Some(tenant), Some(live), Some(opening)) = (tenant, live, opening) else {
        return Verdict::Unknown(Unknown::RecordsUnreadable);
    };
    let count = match count(tenant, live, opening) {
        Ok(c) => c,
        Err(u) => return Verdict::Unknown(u),
    };
    let (more, short) = room(&plan, &count.held);
    let allowed = count.sites().saturating_add(more);
    if more >= 1 {
        Verdict::Fits { count, allowed }
    } else {
        Verdict::Exceeded {
            count,
            allowed,
            short,
        }
    }
}

// ---------------------------------------------------------------- E7 traffic and mail (Andy 11-12)

/// Traffic a month the plan comes with, in GB (5 TB, as [`arc::PLAN`]'s bytes).
pub const TRAFFIC_PLAN_GB: u64 = 5000;
/// The shares of [`TRAFFIC_PLAN_GB`], in percent, at which the customer is told, once each
/// a month. Rising order.
pub const TRAFFIC_NOTICE_PCT: [u8; 3] = [80, 95, 100];
/// Mail a site may send a calendar month before the customer is told.
pub const MAIL_FREE_PER_SITE_MONTH: u64 = 1000;
/// The code of the notice for mail past [`MAIL_FREE_PER_SITE_MONTH`].
pub const MAIL_OVER_FREE: &str = "mail_over_free";

/// The code of the traffic notice at `pct` (`traffic_80`, `traffic_95`, `traffic_100`);
/// `None` for a share that is not one of [`TRAFFIC_NOTICE_PCT`].
pub fn traffic_code(pct: u8) -> Option<&'static str> {
    match pct {
        80 => Some("traffic_80"),
        95 => Some("traffic_95"),
        100 => Some("traffic_100"),
        _ => None,
    }
}

/// The traffic notice to send now, if any: the highest share of the plan `used_gb` has
/// reached, when no notice at it or above it was sent this month (`already_sent`, the
/// shares sent since the month began). A month that jumps past two shares at once gets
/// the higher one only; nothing lower is sent after a higher one. Past 100 % there is
/// nothing more to send, and nothing is cut off ([`traffic_cut_off`]).
pub fn traffic_notice(used_gb: u64, already_sent: &[u8]) -> Option<u8> {
    let pct = used_gb.saturating_mul(100) / TRAFFIC_PLAN_GB;
    let top = TRAFFIC_NOTICE_PCT
        .iter()
        .copied()
        .filter(|t| pct >= u64::from(*t))
        .max()?;
    (!already_sent.iter().any(|s| *s >= top)).then_some(top)
}

/// Whether a site is cut off for its traffic: never, for now (Andy 11-12: "到 100% 不斷線、
/// 只通知(之後再定超量)").
pub fn traffic_cut_off(used_gb: u64) -> bool {
    let _ = used_gb;
    false
}

/// Whether to tell the customer about a site's mail now: once a month, when more than
/// [`MAIL_FREE_PER_SITE_MONTH`] went out and no notice was sent yet.
pub fn mail_notice(sent_this_month: u64, already_noticed: bool) -> bool {
    sent_this_month > MAIL_FREE_PER_SITE_MONTH && !already_noticed
}

/// Whether a site's mail is held back for its count: never (Andy 11-12: "超過先通知、
/// 不擋信").
pub fn mail_held_back(sent_this_month: u64) -> bool {
    let _ = sent_this_month;
    false
}

// ---------------------------------------------------------------- E7 a month's notices

/// The notice state file could not be read: nothing is told and nothing written, so no
/// notice is told twice.
pub const NOTICE_STATE_UNREADABLE: &str = "notice_state_unreadable";
/// The usage handed in could not be read.
pub const NOTICE_USAGE_UNREADABLE: &str = "notice_usage_unreadable";
/// The usage handed in is of a month before the kept state's: a late rerun of an old
/// month would wipe this month's state and tell everything again, so nothing is done.
pub const NOTICE_USAGE_OLDER: &str = "notice_usage_older_than_state";

/// What was told in one calendar month, kept between runs: the traffic shares told per
/// customer, and the sites told about their mail.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MonthState {
    /// `YYYY-MM`; empty before the first run.
    pub month: String,
    pub traffic_sent: BTreeMap<String, Vec<u8>>,
    pub mail_noticed: BTreeSet<String>,
}

/// One notice to tell: to a customer (`tenant`) about traffic, or about one site's mail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Notice {
    /// `tenant` or `site`.
    pub about: &'static str,
    pub who: String,
    pub code: &'static str,
}

/// What a month's run reads: the month, each customer's traffic in GB and each site's
/// mail sent so far this month.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MonthUsage {
    pub month: String,
    pub traffic_gb: Vec<(String, u64)>,
    pub mail_sent: Vec<(String, u64)>,
}

/// `YYYY-MM` with a month 01–12.
pub fn valid_month(m: &str) -> bool {
    let b = m.as_bytes();
    b.len() == 7
        && b[4] == b'-'
        && b[..4].iter().chain(&b[5..]).all(u8::is_ascii_digit)
        && matches!(m[5..].parse::<u8>(), Ok(1..=12))
}

fn counts(v: Option<&Value>) -> Option<Vec<(String, u64)>> {
    let Some(v) = v else {
        return Some(Vec::new());
    };
    v.as_object()?
        .iter()
        .map(|(k, n)| {
            site::valid_code(k)
                .then_some(())
                .and(n.as_u64())
                .map(|n| (k.clone(), n))
        })
        .collect()
}

/// Read the usage of a month: `{"month":"YYYY-MM","traffic_gb":{<customer>:GB},
/// "mail_sent":{<site>:N}}`. Either map may be missing (nothing measured); a code that
/// is not one, or a count that is not a whole number, makes the whole usage unreadable.
pub fn parse_usage(v: &Value) -> Result<MonthUsage, &'static str> {
    let month = v
        .get("month")
        .and_then(Value::as_str)
        .filter(|m| valid_month(m))
        .ok_or(NOTICE_USAGE_UNREADABLE)?;
    Ok(MonthUsage {
        month: String::from(month),
        traffic_gb: counts(v.get("traffic_gb")).ok_or(NOTICE_USAGE_UNREADABLE)?,
        mail_sent: counts(v.get("mail_sent")).ok_or(NOTICE_USAGE_UNREADABLE)?,
    })
}

/// Read the state a run left. `None`: there is no state yet (the first run). Anything
/// that is there but cannot be read wholly is [`NOTICE_STATE_UNREADABLE`], never "nothing
/// told yet" — that would tell every notice again.
pub fn parse_state(v: Option<&Value>) -> Result<MonthState, &'static str> {
    let Some(v) = v else {
        return Ok(MonthState::default());
    };
    let bad = NOTICE_STATE_UNREADABLE;
    let month = v
        .get("month")
        .and_then(Value::as_str)
        .filter(|m| valid_month(m))
        .ok_or(bad)?;
    let mut traffic_sent = BTreeMap::new();
    for (who, sent) in v
        .get("traffic_sent")
        .and_then(Value::as_object)
        .ok_or(bad)?
    {
        let sent = sent
            .as_array()
            .ok_or(bad)?
            .iter()
            .map(|p| {
                p.as_u64()
                    .and_then(|p| u8::try_from(p).ok())
                    .filter(|p| TRAFFIC_NOTICE_PCT.contains(p))
            })
            .collect::<Option<Vec<u8>>>()
            .ok_or(bad)?;
        if !site::valid_code(who) {
            return Err(bad);
        }
        traffic_sent.insert(who.clone(), sent);
    }
    let mut mail_noticed = BTreeSet::new();
    for s in v.get("mail_noticed").and_then(Value::as_array).ok_or(bad)? {
        let s = s.as_str().filter(|s| site::valid_code(s)).ok_or(bad)?;
        mail_noticed.insert(String::from(s));
    }
    Ok(MonthState {
        month: String::from(month),
        traffic_sent,
        mail_noticed,
    })
}

/// The state as the file keeps it ([`parse_state`] reads it back).
pub fn state_json(s: &MonthState) -> Value {
    serde_json::json!({
        "month": s.month,
        "traffic_sent": s.traffic_sent,
        "mail_noticed": s.mail_noticed,
    })
}

/// One run of a month: what to tell now and the state to keep. A state of another month
/// is a month gone: everything starts over. Each notice told is put in the state, so the
/// next run does not tell it again.
/// Usage of a month before the state's is refused whole ([`NOTICE_USAGE_OLDER`]).
pub fn month_run(
    prev: MonthState,
    usage: &MonthUsage,
) -> Result<(MonthState, Vec<Notice>), &'static str> {
    if usage.month < prev.month {
        return Err(NOTICE_USAGE_OLDER);
    }
    let mut st = prev;
    if st.month != usage.month {
        st = MonthState {
            month: usage.month.clone(),
            ..MonthState::default()
        };
    }
    let mut told = Vec::new();
    for (tenant, gb) in &usage.traffic_gb {
        let sent = st.traffic_sent.entry(tenant.clone()).or_default();
        if let Some(pct) = traffic_notice(*gb, sent) {
            sent.push(pct);
            if let Some(code) = traffic_code(pct) {
                told.push(Notice {
                    about: "tenant",
                    who: tenant.clone(),
                    code,
                });
            }
        }
    }
    for (code, n) in &usage.mail_sent {
        if mail_notice(*n, st.mail_noticed.contains(code)) {
            st.mail_noticed.insert(code.clone());
            told.push(Notice {
                about: "site",
                who: code.clone(),
                code: MAIL_OVER_FREE,
            });
        }
    }
    Ok((st, told))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn live(code: &str, tenant: &str, mem: u32, cpu: u32) -> Value {
        json!({ "site": code, "ct": 120, "ip": "10.77.0.20", "hosts": "a.example.test",
                "tenant_id": tenant,
                "limits": { "cores": 8, "cpulimit_x100": cpu, "memory_mb": mem, "swap_mb": 512, "pids_max": 2048 } })
    }

    fn opening(code: &str) -> Value {
        json!({ "site": code, "ct": 130, "ip": "10.77.0.30", "hosts": "b.example.test", "op": "x", "step": "clone" })
    }

    #[test]
    fn an_empty_customer_may_open_as_many_starting_sites_as_the_plan_holds() {
        let v = judge(Some("acme"), Some(&[]), Some(&[]));
        // 8 vCPU = 800 / 200 = 4; 16384 / 1024 = 16: CPU runs out first
        let Verdict::Fits { count, allowed } = v else {
            panic!("{v:?}")
        };
        assert_eq!(count.sites(), 0);
        assert_eq!(allowed, 4);
        assert_eq!(judge(Some("acme"), Some(&[]), Some(&[])).stop(), None);
    }

    #[test]
    fn live_and_opening_count_and_other_customers_do_not() {
        let l = [
            live("s1", "acme", 1024, 200),
            live("s2", "acme", 1024, 200),
            live("s9", "other", 8192, 800),
            // a record that names no tenant is nobody's
            json!({ "site": "s8", "ct": 128, "ip": "10.77.0.28", "hosts": "c.example.test" }),
        ];
        let o = [opening("s3")];
        let Verdict::Fits { count, allowed } = judge(Some("acme"), Some(&l), Some(&o)) else {
            panic!()
        };
        assert_eq!((count.live, count.opening), (2, 1));
        assert_eq!(count.held.cpu_x100, 600);
        assert_eq!(count.held.memory_mb, 3072);
        assert_eq!(allowed, 4);
    }

    #[test]
    fn the_last_room_taken_is_quota_exceeded() {
        let l = [
            live("s1", "acme", 1024, 200),
            live("s2", "acme", 1024, 200),
            live("s3", "acme", 1024, 200),
            live("s4", "acme", 1024, 200),
        ];
        let v = judge(Some("acme"), Some(&l), Some(&[]));
        assert_eq!(v.stop(), Some("quota_exceeded"));
        let Verdict::Exceeded { short, allowed, .. } = v else {
            panic!()
        };
        assert_eq!(short, Resource::Cpu);
        assert_eq!(allowed, 4);
        // memory can run out first too
        let big = [live("s1", "acme", 16_000, 100)];
        let Verdict::Exceeded { short, .. } = judge(Some("acme"), Some(&big), Some(&[])) else {
            panic!()
        };
        assert_eq!(short, Resource::Memory);
    }

    #[test]
    fn an_opening_of_nobody_named_holds_room_and_one_of_another_does_not() {
        let mut theirs = opening("s5");
        theirs["tenant_id"] = json!("other");
        let mut mine = opening("s6");
        mine["tenant_id"] = json!("acme");
        let o = [opening("s4"), theirs, mine];
        let Ok(c) = count("acme", &[], &o) else {
            panic!()
        };
        assert_eq!(c.opening, 2);
        assert_eq!(c.held.memory_mb, 2 * Limits::START.memory_mb);
    }

    #[test]
    fn an_opening_that_already_has_its_live_record_counts_once() {
        let l = [live("s1", "acme", 1024, 200)];
        let o = [opening("s1")];
        let Ok(c) = count("acme", &l, &o) else {
            panic!()
        };
        assert_eq!((c.live, c.opening, c.held.cpu_x100), (1, 0, 200));
    }

    #[test]
    fn what_cannot_be_told_is_quota_unknown() {
        assert_eq!(
            judge(None, Some(&[]), Some(&[])),
            Verdict::Unknown(Unknown::NoTenant)
        );
        assert_eq!(
            judge(Some("not a code!"), Some(&[]), Some(&[])),
            Verdict::Unknown(Unknown::NoTenant)
        );
        assert_eq!(
            judge(Some("acme"), None, Some(&[])),
            Verdict::Unknown(Unknown::RecordsUnreadable)
        );
        assert_eq!(
            judge(Some("acme"), Some(&[]), None),
            Verdict::Unknown(Unknown::RecordsUnreadable)
        );
        // a record of the customer whose limits cannot be rebuilt
        let bad = [json!({ "site": "s1", "tenant_id": "acme" })];
        let v = judge(Some("acme"), Some(&bad), Some(&[]));
        assert_eq!(v, Verdict::Unknown(Unknown::RecordUnreadable));
        assert_eq!(v.stop(), Some("quota_unknown"));
        // an opening without a site code
        let o = [json!({ "ct": 130 })];
        assert_eq!(
            judge(Some("acme"), Some(&[]), Some(&o)),
            Verdict::Unknown(Unknown::RecordUnreadable)
        );
    }

    #[test]
    fn a_live_tenant_id_that_cannot_be_read_is_quota_unknown() {
        for bad in [json!(7), json!("A B"), json!(null)] {
            let mut r = live("s1", "acme", 1024, 200);
            r["tenant_id"] = bad;
            let v = judge(Some("acme"), Some(&[r]), Some(&[]));
            assert_eq!(v, Verdict::Unknown(Unknown::RecordUnreadable));
        }
        // another customer's readable record is still passed over
        let l = [live("s1", "other", 1024, 200)];
        assert_eq!(judge(Some("acme"), Some(&l), Some(&[])).stop(), None);
    }

    #[test]
    fn a_customer_is_seen_before_only_by_a_record_naming_it() {
        let l = [live("s1", "acme", 1024, 200)];
        assert!(seen_before("acme", &[&l]));
        assert!(!seen_before("acme2", &[&l]));
        assert!(!seen_before("acme", &[&[], &[]]));
    }

    #[test]
    fn sums_saturate_into_exceeded() {
        let l: Vec<Value> = (0..3)
            .map(|i| live(&alloc::format!("s{i}"), "acme", 16_384, 800))
            .collect();
        assert_eq!(
            judge(Some("acme"), Some(&l), Some(&[])).stop(),
            Some("quota_exceeded")
        );
    }

    #[test]
    fn crossing_80_then_95_sends_each_once() {
        assert_eq!(TRAFFIC_PLAN_GB * 1_000_000_000, arc::PLAN.traffic_bytes);
        assert_eq!(traffic_notice(3999, &[]), None);
        assert_eq!(traffic_notice(4000, &[]), Some(80));
        assert_eq!(traffic_notice(4500, &[80]), None);
        assert_eq!(traffic_notice(4750, &[80]), Some(95));
        assert_eq!(traffic_notice(4800, &[80, 95]), None);
        // a jump past both: the higher one only, and the lower one never after it
        assert_eq!(traffic_notice(4800, &[]), Some(95));
        assert_eq!(traffic_notice(4900, &[95]), None);
        assert_eq!(traffic_code(80), Some("traffic_80"));
        assert_eq!(traffic_code(95), Some("traffic_95"));
        assert_eq!(traffic_code(50), None);
    }

    #[test]
    fn over_100_still_only_notices() {
        assert_eq!(traffic_notice(5000, &[80, 95]), Some(100));
        assert_eq!(traffic_code(100), Some("traffic_100"));
        for used in [5000, 7500, 50_000, u64::MAX] {
            assert_eq!(traffic_notice(used, &[80, 95, 100]), None);
            assert!(!traffic_cut_off(used));
        }
        assert_eq!(traffic_notice(u64::MAX, &[]), Some(100));
    }

    #[test]
    fn mail_over_1000_notices_once_never_blocks() {
        assert!(!mail_notice(1000, false));
        assert!(mail_notice(1001, false));
        assert!(!mail_notice(1001, true));
        assert!(!mail_notice(250_000, true));
        for sent in [0, 1000, 1001, 250_000, u64::MAX] {
            assert!(!mail_held_back(sent));
        }
        assert_eq!(MAIL_OVER_FREE, "mail_over_free");
    }

    #[test]
    fn a_new_site_starts_at_10_gb() {
        assert_eq!(site::START_DISK_GB, 10);
        // an opening and a live site whose records name no disk count at the start size;
        // a size the record names is used as it is
        let mut sized = live("s1", "acme", 1024, 200);
        sized["disk_gb"] = json!(40);
        let Ok(c) = count(
            "acme",
            &[live("s2", "acme", 1024, 200), sized],
            &[opening("s3")],
        ) else {
            panic!("count")
        };
        assert_eq!(c.held.disk_gb, 40 + 2 * site::START_DISK_GB);
    }

    fn usage(month: &str, traffic: &[(&str, u64)], mail: &[(&str, u64)]) -> MonthUsage {
        let own = |l: &[(&str, u64)]| l.iter().map(|(k, n)| (String::from(*k), *n)).collect();
        MonthUsage {
            month: String::from(month),
            traffic_gb: own(traffic),
            mail_sent: own(mail),
        }
    }

    #[test]
    fn a_notice_told_is_kept_and_not_told_again_this_month() {
        let u = usage("2026-11", &[("acme", 4100)], &[("s1", 1200)]);
        let (st, told) = month_run(MonthState::default(), &u).unwrap();
        assert_eq!(
            told,
            vec![
                Notice {
                    about: "tenant",
                    who: String::from("acme"),
                    code: "traffic_80"
                },
                Notice {
                    about: "site",
                    who: String::from("s1"),
                    code: MAIL_OVER_FREE
                },
            ]
        );
        // the state is kept as a file between runs
        let st = parse_state(Some(&state_json(&st))).unwrap();
        let (st, told) = month_run(st, &u).unwrap();
        assert_eq!(told, vec![]);
        let (_, told) =
            month_run(st, &usage("2026-11", &[("acme", 4800)], &[("s1", 9000)])).unwrap();
        assert_eq!(told.len(), 1);
        assert_eq!(told[0].code, "traffic_95");
    }

    #[test]
    fn a_new_month_starts_over() {
        let u = usage("2026-11", &[("acme", 4100)], &[("s1", 1200)]);
        let (st, _) = month_run(MonthState::default(), &u).unwrap();
        let (st, told) =
            month_run(st, &usage("2026-12", &[("acme", 4100)], &[("s1", 1200)])).unwrap();
        assert_eq!(told.len(), 2);
        assert_eq!(st.month, "2026-12");
    }

    #[test]
    fn a_state_that_cannot_be_read_is_not_an_empty_one() {
        assert_eq!(parse_state(None), Ok(MonthState::default()));
        for bad in [
            json!({}),
            json!({ "month": "2026-13", "traffic_sent": {}, "mail_noticed": [] }),
            json!({ "month": "2026-11", "traffic_sent": { "acme": [70] }, "mail_noticed": [] }),
            json!({ "month": "2026-11", "traffic_sent": {}, "mail_noticed": ["Not A Code"] }),
            json!({ "month": "2026-11", "traffic_sent": {} }),
            json!({ "month": "2026-11", "traffic_sent": { "Not A Code": [80] }, "mail_noticed": [] }),
            json!([]),
        ] {
            assert_eq!(
                parse_state(Some(&bad)),
                Err(NOTICE_STATE_UNREADABLE),
                "{bad}"
            );
        }
        let ok =
            json!({ "month": "2026-11", "traffic_sent": { "acme": [80] }, "mail_noticed": ["s1"] });
        assert_eq!(state_json(&parse_state(Some(&ok)).unwrap()), ok);
    }

    #[test]
    fn usage_that_cannot_be_read_is_refused_whole() {
        let ok = json!({ "month": "2026-11", "traffic_gb": { "acme": 4100 } });
        assert_eq!(
            parse_usage(&ok).unwrap(),
            usage("2026-11", &[("acme", 4100)], &[])
        );
        for bad in [
            json!({ "traffic_gb": {} }),
            json!({ "month": "2026-11", "traffic_gb": { "acme": "4100" } }),
            json!({ "month": "2026-11", "mail_sent": { "Not A Code": 3 } }),
            json!({ "month": "2026-11", "mail_sent": [] }),
        ] {
            assert_eq!(parse_usage(&bad), Err(NOTICE_USAGE_UNREADABLE), "{bad}");
        }
        assert!(valid_month("2026-01") && !valid_month("2026-00") && !valid_month("26-11"));
    }

    #[test]
    fn a_disk_gb_of_the_wrong_type_is_unreadable_not_the_starting_size() {
        let mut r = live("s1", "acme", 1024, 200);
        assert_eq!(disk_of(&r), Ok(site::START_DISK_GB));
        r["disk_gb"] = json!(16);
        assert_eq!(disk_of(&r), Ok(16));
        for bad in [json!("16"), json!(0), json!(u64::MAX), json!(1.5)] {
            r["disk_gb"] = bad;
            assert_eq!(
                count("acme", core::slice::from_ref(&r), &[]),
                Err(Unknown::RecordUnreadable)
            );
        }
    }

    #[test]
    fn usage_of_an_older_month_than_the_state_is_refused_and_keeps_the_state() {
        let u = usage("2026-12", &[("acme", 4100)], &[("s1", 1200)]);
        let (st, told) = month_run(MonthState::default(), &u).unwrap();
        assert!(!told.is_empty());
        let late = usage("2026-11", &[("acme", 4100)], &[("s1", 1200)]);
        assert_eq!(month_run(st.clone(), &late), Err(NOTICE_USAGE_OLDER));
        // the same month again tells nothing, a later one starts over
        let (_, again) = month_run(st.clone(), &u).unwrap();
        assert!(again.is_empty());
        let (_, next) = month_run(st, &usage("2027-01", &[("acme", 4100)], &[])).unwrap();
        assert!(!next.is_empty());
    }
}
