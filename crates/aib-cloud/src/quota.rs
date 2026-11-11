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
//! memory. Disk is added up and shown, not judged: a new site's disk size is not in
//! [`Limits`] and no number for it is written down (待 Andy 定).
//!
//! No I/O: the host reads the records and hands them in.

use alloc::collections::BTreeSet;
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

fn disk_of(rec: &Value) -> u32 {
    rec.get("disk_gb")
        .and_then(Value::as_u64)
        .and_then(|n| u32::try_from(n).ok())
        .unwrap_or(0)
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
        if site::record_tenant(rec) != Some(tenant) {
            continue;
        }
        let spec = site::spec_from_record(rec).map_err(|_| Unknown::RecordUnreadable)?;
        if let Some(code) = rec.get("site").and_then(Value::as_str) {
            codes.insert(code);
        }
        sites.push((spec.limits, disk_of(rec)));
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
        sites.push((limits, disk_of(rec)));
        n_opening = n_opening.saturating_add(1);
    }
    Ok(Count {
        live: n_live,
        opening: n_opening,
        held: arc::held(&sites),
    })
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
        assert_eq!(judge(None, Some(&[]), Some(&[])), Verdict::Unknown(Unknown::NoTenant));
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
    fn sums_saturate_into_exceeded() {
        let l: Vec<Value> = (0..3)
            .map(|i| live(&alloc::format!("s{i}"), "acme", 16_384, 800))
            .collect();
        assert_eq!(judge(Some("acme"), Some(&l), Some(&[])).stop(), Some("quota_exceeded"));
    }
}
