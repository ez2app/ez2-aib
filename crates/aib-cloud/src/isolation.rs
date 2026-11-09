// SPDX-License-Identifier: Apache-2.0
//! Isolation acceptance matrix (4.4.1 §5, M0 item 4): pure planning and judging.
//!
//! Given the host's topology (host, Edge CT, site CTs, optional tailnet address,
//! public probe targets) this module derives the **matrix** — every cell says who
//! probes what, over which protocol, and whether the connection must be **open** or
//! **blocked** — plus a **control** for every blocked cell: a cell that proves the
//! target service is alive, so a timeout alone never counts as a pass ("只看逾時不算過").
//!
//! The probes themselves are I/O and run on the host (`ez2-isolation-probe`, which
//! joins each CT's network namespace); their result list comes back as JSON and
//! [`judge`] turns it into a [`Receipt`]: pass / fail / missing per cell, with a
//! blocked cell failing when its control did not pass. Same contract as
//! [`crate::robot`] / [`crate::r2token`]: validate once, derive everything from the
//! same values, no I/O in this crate.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;

use serde_json::{json, Value};

/// Why a topology was refused or a result list could not be judged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// An address is not a dotted IPv4 literal.
    Ip(String),
    /// A CT id is outside Proxmox's range (100..=999_999_999).
    CtId(u32),
    /// Two nodes share an address or a CT id.
    Duplicate(String),
    /// The result list has the wrong shape or references an unknown cell.
    Results(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Ip(s) => write!(f, "address refused: {s}"),
            Error::CtId(n) => write!(f, "ct id refused: {n}"),
            Error::Duplicate(s) => write!(f, "duplicate in topology: {s}"),
            Error::Results(s) => write!(f, "result list unusable: {s}"),
        }
    }
}

impl core::error::Error for Error {}

/// A dotted IPv4 literal (any range: internal addresses are the point here).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ip(String);

impl Ip {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let parts: Vec<&str> = s.split('.').collect();
        if parts.len() != 4 {
            return Err(Error::Ip(s.to_owned()));
        }
        for p in &parts {
            if p.is_empty() || p.len() > 3 || !p.chars().all(|c| c.is_ascii_digit()) {
                return Err(Error::Ip(s.to_owned()));
            }
            if p.len() > 1 && p.starts_with('0') {
                return Err(Error::Ip(s.to_owned()));
            }
            if p.parse::<u16>().map_or(true, |v| v > 255) {
                return Err(Error::Ip(s.to_owned()));
            }
        }
        Ok(Ip(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A container on the host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ct {
    pub id: u32,
    pub ip: Ip,
}

impl Ct {
    pub fn new(id: u32, ip: &str) -> Result<Self, Error> {
        if !(100..=999_999_999).contains(&id) {
            return Err(Error::CtId(id));
        }
        Ok(Ct {
            id,
            ip: Ip::parse(ip)?,
        })
    }
}

/// Public addresses used to prove egress works / is filtered. They are only probe
/// targets; nothing is sent beyond a TCP handshake, a DNS query or one ICMP echo.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InternetTargets {
    pub dns: Ip,
    pub http: Ip,
    pub https: Ip,
    pub smtp: Ip,
    pub ssh: Ip,
}

/// Everything the matrix needs to know about one host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Topology {
    pub host_internal: Ip,
    pub host_public: Ip,
    pub tailnet: Option<Ip>,
    pub edge: Ct,
    pub sites: Vec<Ct>,
    pub internet: InternetTargets,
}

impl Topology {
    /// Validate: distinct CT ids, distinct addresses, at least one site.
    pub fn validate(&self) -> Result<(), Error> {
        if self.sites.is_empty() {
            return Err(Error::Duplicate("no site CT".to_owned()));
        }
        let mut ids: Vec<u32> = Vec::new();
        let mut take_id = |id: u32| -> Result<(), Error> {
            if ids.contains(&id) {
                return Err(Error::Duplicate(alloc::format!("ct {id}")));
            }
            ids.push(id);
            Ok(())
        };
        take_id(self.edge.id)?;
        for s in &self.sites {
            take_id(s.id)?;
        }
        let mut seen: Vec<String> = Vec::new();
        let mut uniq = |v: &str| -> Result<(), Error> {
            if seen.iter().any(|x| x == v) {
                return Err(Error::Duplicate(v.to_owned()));
            }
            seen.push(v.to_owned());
            Ok(())
        };
        uniq(self.host_internal.as_str())?;
        uniq(self.host_public.as_str())?;
        if let Some(t) = &self.tailnet {
            uniq(t.as_str())?;
        }
        uniq(self.edge.ip.as_str())?;
        for s in &self.sites {
            uniq(s.ip.as_str())?;
        }
        Ok(())
    }
}

/// Who runs the probe.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    /// The host itself.
    Host,
    /// Inside a CT's network namespace.
    Ct(u32),
    /// From outside the host (EU01 or any public vantage point); optional.
    External,
}

impl Source {
    fn tag(&self) -> String {
        match self {
            Source::Host => "host".to_owned(),
            Source::Ct(id) => alloc::format!("ct:{id}"),
            Source::External => "external".to_owned(),
        }
    }
}

/// How the probe is made.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Proto {
    Tcp,
    /// A DNS query over UDP/53.
    Dns,
    Icmp,
}

impl Proto {
    fn tag(self) -> &'static str {
        match self {
            Proto::Tcp => "tcp",
            Proto::Dns => "dns",
            Proto::Icmp => "icmp",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Expect {
    Open,
    Blocked,
}

/// One cell of the matrix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    pub id: String,
    /// 4.4.1 section the cell proves (e.g. "5.2").
    pub section: &'static str,
    pub from: Source,
    pub to: Ip,
    /// 0 for ICMP.
    pub port: u16,
    pub proto: Proto,
    pub expect: Expect,
    /// For a Blocked cell: the id of the Open cell proving the target is alive.
    pub control: Option<String>,
    pub why: &'static str,
}

#[allow(clippy::too_many_arguments)]
fn cell(
    id: String,
    section: &'static str,
    from: Source,
    to: &Ip,
    port: u16,
    proto: Proto,
    expect: Expect,
    control: Option<String>,
    why: &'static str,
) -> Cell {
    Cell {
        id,
        section,
        from,
        to: to.clone(),
        port,
        proto,
        expect,
        control,
        why,
    }
}

/// Build the full matrix for a topology. Every Blocked cell has a control.
pub fn matrix(t: &Topology) -> Result<Vec<Cell>, Error> {
    t.validate()?;
    let lo = Ip::parse("127.0.0.1")?;
    let mut cells: Vec<Cell> = Vec::new();
    let e = &t.edge;

    // --- controls: services really listen (self probes) and egress really works ---
    cells.push(cell(
        "host-self-22".to_owned(),
        "ctl",
        Source::Host,
        &lo,
        22,
        Proto::Tcp,
        Expect::Open,
        None,
        "宿主 sshd 活著",
    ));
    cells.push(cell(
        "host-self-8006".to_owned(),
        "ctl",
        Source::Host,
        &lo,
        8006,
        Proto::Tcp,
        Expect::Open,
        None,
        "pveproxy 活著",
    ));
    cells.push(cell(
        alloc::format!("edge-{}-self-22", e.id),
        "ctl",
        Source::Ct(e.id),
        &lo,
        22,
        Proto::Tcp,
        Expect::Open,
        None,
        "Edge sshd 活著",
    ));
    cells.push(cell(
        alloc::format!("edge-{}-self-80", e.id),
        "ctl",
        Source::Ct(e.id),
        &lo,
        80,
        Proto::Tcp,
        Expect::Open,
        None,
        "Edge 80 活著",
    ));
    if let Some(tn) = &t.tailnet {
        cells.push(cell(
            "host-tailnet-icmp".to_owned(),
            "ctl",
            Source::Host,
            tn,
            0,
            Proto::Icmp,
            Expect::Open,
            None,
            "tailnet 位址活著",
        ));
    }
    for s in &t.sites {
        cells.push(cell(
            alloc::format!("site-{}-self-22", s.id),
            "ctl",
            Source::Ct(s.id),
            &lo,
            22,
            Proto::Tcp,
            Expect::Open,
            None,
            "站 sshd 活著",
        ));
        cells.push(cell(
            alloc::format!("site-{}-self-3306", s.id),
            "ctl",
            Source::Ct(s.id),
            &lo,
            3306,
            Proto::Tcp,
            Expect::Open,
            None,
            "站 MariaDB 活著",
        ));
        cells.push(cell(
            alloc::format!("edge-to-site-{}-80", s.id),
            "5.2",
            Source::Ct(e.id),
            &s.ip,
            80,
            Proto::Tcp,
            Expect::Open,
            None,
            "Edge → 站 80 是唯一准許的入站",
        ));
        cells.push(cell(
            alloc::format!("site-{}-egress-443", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.internet.https,
            443,
            Proto::Tcp,
            Expect::Open,
            None,
            "站出站 443 通(更新 / API)",
        ));
        cells.push(cell(
            alloc::format!("site-{}-egress-80", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.internet.http,
            80,
            Proto::Tcp,
            Expect::Open,
            None,
            "站出站 80 通",
        ));
        cells.push(cell(
            alloc::format!("site-{}-egress-dns", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.internet.dns,
            53,
            Proto::Dns,
            Expect::Open,
            None,
            "站 DNS 通",
        ));
    }
    cells.push(cell(
        alloc::format!("edge-{}-egress-443", e.id),
        "5.8",
        Source::Ct(e.id),
        &t.internet.https,
        443,
        Proto::Tcp,
        Expect::Open,
        None,
        "Edge 出站 443 通(ACME)",
    ));

    // --- blocked cells, each with a control ---
    for s in &t.sites {
        let egress_ctl = Some(alloc::format!("site-{}-egress-443", s.id));
        cells.push(cell(
            alloc::format!("site-{}-to-host-22", s.id),
            "5.2",
            Source::Ct(s.id),
            &t.host_internal,
            22,
            Proto::Tcp,
            Expect::Blocked,
            Some("host-self-22".to_owned()),
            "站 → 宿主 ssh",
        ));
        cells.push(cell(
            alloc::format!("site-{}-to-host-8006", s.id),
            "5.2",
            Source::Ct(s.id),
            &t.host_internal,
            8006,
            Proto::Tcp,
            Expect::Blocked,
            Some("host-self-8006".to_owned()),
            "站 → 宿主 8006",
        ));
        cells.push(cell(
            alloc::format!("site-{}-to-hostpub-22", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.host_public,
            22,
            Proto::Tcp,
            Expect::Blocked,
            Some("host-self-22".to_owned()),
            "站 → 宿主公網位址(防 A→公網→DNAT→B)",
        ));
        cells.push(cell(
            alloc::format!("site-{}-to-edge-22", s.id),
            "5.8",
            Source::Ct(s.id),
            &e.ip,
            22,
            Proto::Tcp,
            Expect::Blocked,
            Some(alloc::format!("edge-{}-self-22", e.id)),
            "站 → Edge ssh",
        ));
        cells.push(cell(
            alloc::format!("site-{}-to-edge-80", s.id),
            "5.6",
            Source::Ct(s.id),
            &e.ip,
            80,
            Proto::Tcp,
            Expect::Blocked,
            Some(alloc::format!("edge-{}-self-80", e.id)),
            "站 → Edge 80(內網黑名單;公開站走公網)",
        ));
        if let Some(tn) = &t.tailnet {
            cells.push(cell(
                alloc::format!("site-{}-to-tailnet", s.id),
                "5.6",
                Source::Ct(s.id),
                tn,
                0,
                Proto::Icmp,
                Expect::Blocked,
                Some("host-tailnet-icmp".to_owned()),
                "站 → tailnet",
            ));
        }
        cells.push(cell(
            alloc::format!("site-{}-egress-25", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.internet.smtp,
            25,
            Proto::Tcp,
            Expect::Blocked,
            egress_ctl.clone(),
            "站出站 25 全擋",
        ));
        cells.push(cell(
            alloc::format!("site-{}-egress-22", s.id),
            "5.6",
            Source::Ct(s.id),
            &t.internet.ssh,
            22,
            Proto::Tcp,
            Expect::Blocked,
            egress_ctl.clone(),
            "站出站 22 不在白名單",
        ));
        for p in &t.sites {
            if p.id == s.id {
                continue;
            }
            cells.push(cell(
                alloc::format!("site-{}-to-site-{}-80", s.id, p.id),
                "5.1",
                Source::Ct(s.id),
                &p.ip,
                80,
                Proto::Tcp,
                Expect::Blocked,
                Some(alloc::format!("edge-to-site-{}-80", p.id)),
                "站 A → 站 B 網站",
            ));
            cells.push(cell(
                alloc::format!("site-{}-to-site-{}-3306", s.id, p.id),
                "5.1",
                Source::Ct(s.id),
                &p.ip,
                3306,
                Proto::Tcp,
                Expect::Blocked,
                Some(alloc::format!("site-{}-self-3306", p.id)),
                "站 A → 站 B 資料庫",
            ));
            cells.push(cell(
                alloc::format!("site-{}-to-site-{}-22", s.id, p.id),
                "5.1",
                Source::Ct(s.id),
                &p.ip,
                22,
                Proto::Tcp,
                Expect::Blocked,
                Some(alloc::format!("site-{}-self-22", p.id)),
                "站 A → 站 B ssh",
            ));
        }
        cells.push(cell(
            alloc::format!("edge-to-site-{}-22", s.id),
            "5.8",
            Source::Ct(e.id),
            &s.ip,
            22,
            Proto::Tcp,
            Expect::Blocked,
            Some(alloc::format!("site-{}-self-22", s.id)),
            "Edge → 站 ssh(Edge 被打穿也進不了站)",
        ));
        cells.push(cell(
            alloc::format!("edge-to-site-{}-3306", s.id),
            "5.8",
            Source::Ct(e.id),
            &s.ip,
            3306,
            Proto::Tcp,
            Expect::Blocked,
            Some(alloc::format!("site-{}-self-3306", s.id)),
            "Edge → 站資料庫",
        ));
    }
    cells.push(cell(
        alloc::format!("edge-{}-to-host-22", e.id),
        "5.8",
        Source::Ct(e.id),
        &t.host_internal,
        22,
        Proto::Tcp,
        Expect::Blocked,
        Some("host-self-22".to_owned()),
        "Edge → 宿主 ssh",
    ));
    cells.push(cell(
        alloc::format!("edge-{}-to-host-8006", e.id),
        "5.8",
        Source::Ct(e.id),
        &t.host_internal,
        8006,
        Proto::Tcp,
        Expect::Blocked,
        Some("host-self-8006".to_owned()),
        "Edge → 宿主 8006",
    ));
    cells.push(cell(
        alloc::format!("edge-{}-egress-25", e.id),
        "5.8",
        Source::Ct(e.id),
        &t.internet.smtp,
        25,
        Proto::Tcp,
        Expect::Blocked,
        Some(alloc::format!("edge-{}-egress-443", e.id)),
        "Edge 出站 25",
    ));
    if let Some(tn) = &t.tailnet {
        cells.push(cell(
            alloc::format!("edge-{}-to-tailnet", e.id),
            "5.8",
            Source::Ct(e.id),
            tn,
            0,
            Proto::Icmp,
            Expect::Blocked,
            Some("host-tailnet-icmp".to_owned()),
            "Edge → tailnet",
        ));
    }

    // --- from outside (optional vantage point) ---
    cells.push(cell(
        "public-host-22".to_owned(),
        "7.3",
        Source::External,
        &t.host_public,
        22,
        Proto::Tcp,
        Expect::Open,
        None,
        "公網 ssh 開(金鑰 + fail2ban)",
    ));
    cells.push(cell(
        "public-host-443".to_owned(),
        "6",
        Source::External,
        &t.host_public,
        443,
        Proto::Tcp,
        Expect::Open,
        None,
        "公網 443 到 Edge",
    ));
    cells.push(cell(
        "public-host-8006".to_owned(),
        "7.3",
        Source::External,
        &t.host_public,
        8006,
        Proto::Tcp,
        Expect::Blocked,
        Some("host-self-8006".to_owned()),
        "8006 不對公網",
    ));

    debug_assert!(cells
        .iter()
        .all(|c| c.expect == Expect::Open || c.control.is_some()));
    Ok(cells)
}

/// The job list for the host-side runner: one object per cell, nothing but what the
/// probe needs. Sorted by id so the file is stable.
pub fn probe_plan(cells: &[Cell]) -> Value {
    let mut sorted: Vec<&Cell> = cells.iter().collect();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    Value::Array(
        sorted
            .iter()
            .map(|c| {
                json!({
                    "id": c.id,
                    "from": c.from.tag(),
                    "kind": c.proto.tag(),
                    "ip": c.to.as_str(),
                    "port": c.port,
                    "expect": match c.expect { Expect::Open => "open", Expect::Blocked => "blocked" },
                })
            })
            .collect(),
    )
}

/// Outcome of one matrix run.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Receipt {
    pub pass: Vec<String>,
    /// (cell id, reason)
    pub fail: Vec<(String, String)>,
    /// Cells with no result at all (External ones when no vantage point ran).
    pub missing: Vec<String>,
}

impl Receipt {
    /// True only when every cell passed; external cells may be missing when
    /// `external_required` was false at judge time (they are then listed, not failed).
    pub fn ok(&self) -> bool {
        self.fail.is_empty() && self.missing.iter().all(|id| id.starts_with("public-"))
    }

    pub fn to_json(&self) -> Value {
        json!({
            "ok": self.ok(),
            "pass": self.pass,
            "fail": self.fail.iter().map(|(id, why)| json!({"id": id, "why": why})).collect::<Vec<_>>(),
            "missing": self.missing,
            "counts": {"pass": self.pass.len(), "fail": self.fail.len(), "missing": self.missing.len()},
        })
    }
}

/// Judge a result list: `[{"id": "...", "open": true|false}, ...]`.
/// Rules: the observed state must equal the expectation; a Blocked cell also needs its
/// control to have passed (otherwise "control_not_proved" — a dead target looks blocked);
/// unknown ids are an error; a non-bool `open` is an error; cells without a result are
/// missing. With `external_required`, missing External cells are failures.
pub fn judge(cells: &[Cell], results: &Value, external_required: bool) -> Result<Receipt, Error> {
    let arr = results
        .as_array()
        .ok_or_else(|| Error::Results("not an array".to_owned()))?;
    let mut observed: Vec<(String, bool)> = Vec::new();
    for r in arr {
        let id = r
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Results("result without id".to_owned()))?;
        if !cells.iter().any(|c| c.id == id) {
            return Err(Error::Results(alloc::format!("unknown cell id {id}")));
        }
        let open = r
            .get("open")
            .and_then(Value::as_bool)
            .ok_or_else(|| Error::Results(alloc::format!("{id}: open is not a bool")))?;
        if observed.iter().any(|(i, _)| i == id) {
            return Err(Error::Results(alloc::format!("duplicate result for {id}")));
        }
        observed.push((id.to_owned(), open));
    }
    let lookup = |id: &str| observed.iter().find(|(i, _)| i == id).map(|(_, o)| *o);
    let mut rc = Receipt::default();
    // controls first so blocked cells can consult them
    let mut passed: Vec<String> = Vec::new();
    for c in cells.iter().filter(|c| c.expect == Expect::Open) {
        match lookup(&c.id) {
            Some(true) => {
                passed.push(c.id.clone());
                rc.pass.push(c.id.clone());
            }
            Some(false) => rc
                .fail
                .push((c.id.clone(), "expected open, observed closed".to_owned())),
            None => rc.missing.push(c.id.clone()),
        }
    }
    for c in cells.iter().filter(|c| c.expect == Expect::Blocked) {
        match lookup(&c.id) {
            Some(true) => rc
                .fail
                .push((c.id.clone(), "expected blocked, observed OPEN".to_owned())),
            Some(false) => {
                let ctl = c.control.as_deref().unwrap_or("");
                if passed.iter().any(|p| p == ctl) {
                    rc.pass.push(c.id.clone());
                } else {
                    rc.fail.push((
                        c.id.clone(),
                        alloc::format!(
                            "control_not_proved: {ctl} did not pass, a timeout alone is not a pass"
                        ),
                    ));
                }
            }
            None => rc.missing.push(c.id.clone()),
        }
    }
    if external_required {
        let ext: Vec<String> = rc
            .missing
            .iter()
            .filter(|id| id.starts_with("public-"))
            .cloned()
            .collect();
        for id in ext {
            rc.missing.retain(|m| m != &id);
            rc.fail
                .push((id, "external probe required but missing".to_owned()));
        }
    }
    rc.pass.sort();
    rc.fail.sort();
    rc.missing.sort();
    Ok(rc)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::string::ToString;

    fn topo(sites: &[(u32, &str)]) -> Topology {
        Topology {
            host_internal: Ip::parse("10.77.0.1").unwrap(),
            host_public: Ip::parse("135.181.75.90").unwrap(),
            tailnet: Some(Ip::parse("100.120.222.46").unwrap()),
            edge: Ct::new(101, "10.77.0.2").unwrap(),
            sites: sites
                .iter()
                .map(|(id, ip)| Ct::new(*id, ip).unwrap())
                .collect(),
            internet: InternetTargets {
                dns: Ip::parse("1.1.1.1").unwrap(),
                http: Ip::parse("151.101.2.132").unwrap(),
                https: Ip::parse("140.82.121.4").unwrap(),
                smtp: Ip::parse("64.233.184.26").unwrap(),
                ssh: Ip::parse("140.82.121.4").unwrap(),
            },
        }
    }

    fn all_pass(cells: &[Cell]) -> Value {
        Value::Array(
            cells
                .iter()
                .map(|c| json!({"id": c.id, "open": c.expect == Expect::Open}))
                .collect(),
        )
    }

    #[test]
    fn i1_every_blocked_cell_has_a_control_that_exists_and_is_open() {
        let cells = matrix(&topo(&[(110, "10.77.0.10"), (111, "10.77.0.11")])).unwrap();
        for c in cells.iter().filter(|c| c.expect == Expect::Blocked) {
            let ctl = c
                .control
                .as_ref()
                .unwrap_or_else(|| panic!("{} has no control", c.id));
            let target = cells
                .iter()
                .find(|x| &x.id == ctl)
                .unwrap_or_else(|| panic!("{} control {ctl} missing", c.id));
            assert_eq!(target.expect, Expect::Open, "{ctl} must be an Open cell");
        }
        let ids: Vec<&str> = cells.iter().map(|c| c.id.as_str()).collect();
        let mut dedup = ids.clone();
        dedup.sort();
        dedup.dedup();
        assert_eq!(ids.len(), dedup.len(), "cell ids must be unique");
    }

    #[test]
    fn i2_two_sites_get_peer_cells_one_site_does_not() {
        let one = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let two = matrix(&topo(&[(110, "10.77.0.10"), (111, "10.77.0.11")])).unwrap();
        assert!(!one
            .iter()
            .any(|c| c.id.starts_with("site-") && c.id.contains("-to-site-")));
        assert!(two.iter().any(|c| c.id == "site-110-to-site-111-3306"));
        assert!(two.iter().any(|c| c.id == "site-111-to-site-110-80"));
        assert!(two.len() > one.len());
    }

    #[test]
    fn i3_topology_validation() {
        let mut t = topo(&[(110, "10.77.0.10")]);
        t.sites.push(Ct::new(110, "10.77.0.12").unwrap());
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        let mut t = topo(&[(110, "10.77.0.2")]); // same ip as edge
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        t.sites.clear();
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        assert!(matches!(Ct::new(5, "10.0.0.1"), Err(Error::CtId(5))));
        for bad in ["10.0.0", "010.0.0.1", "１.2.3.4", "1.2.3.256", "a.b.c.d"] {
            assert!(matches!(Ip::parse(bad), Err(Error::Ip(_))), "{bad}");
        }
    }

    #[test]
    fn i4_probe_plan_is_sorted_and_minimal() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let plan = probe_plan(&cells);
        let arr = plan.as_array().unwrap();
        assert_eq!(arr.len(), cells.len());
        let ids: Vec<&str> = arr.iter().map(|v| v["id"].as_str().unwrap()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(ids, sorted);
        let host22 = arr
            .iter()
            .find(|v| v["id"] == "site-110-to-host-22")
            .unwrap();
        assert_eq!(host22["from"], "ct:110");
        assert_eq!(host22["kind"], "tcp");
        assert_eq!(host22["ip"], "10.77.0.1");
        assert_eq!(host22["port"], 22);
        assert_eq!(host22["expect"], "blocked");
        let dns = arr
            .iter()
            .find(|v| v["id"] == "site-110-egress-dns")
            .unwrap();
        assert_eq!(
            (dns["kind"].as_str().unwrap(), dns["port"].as_u64().unwrap()),
            ("dns", 53)
        );
        assert!(
            arr.iter().all(|v| v.as_object().unwrap().len() == 6),
            "no extra fields leak into the plan"
        );
    }

    #[test]
    fn i5_judge_all_pass() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let rc = judge(&cells, &all_pass(&cells), false).unwrap();
        assert!(rc.ok(), "{:?}", rc.fail);
        assert_eq!(rc.pass.len(), cells.len());
        assert_eq!(rc.to_json()["counts"]["fail"], 0);
    }

    #[test]
    fn i6_judge_blocked_cell_that_is_open_fails() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let mut r = all_pass(&cells);
        for v in r.as_array_mut().unwrap() {
            if v["id"] == "site-110-to-host-22" {
                v["open"] = json!(true);
            }
        }
        let rc = judge(&cells, &r, false).unwrap();
        assert!(!rc.ok());
        assert_eq!(
            rc.fail,
            vec![(
                "site-110-to-host-22".to_string(),
                "expected blocked, observed OPEN".to_string()
            )]
        );
    }

    #[test]
    fn i7_timeout_without_control_is_not_a_pass() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let mut r = all_pass(&cells);
        for v in r.as_array_mut().unwrap() {
            if v["id"] == "host-self-8006" {
                v["open"] = json!(false); // pveproxy "dead" ⇒ every *-8006 blocked cell loses its proof
            }
        }
        let rc = judge(&cells, &r, false).unwrap();
        assert!(!rc.ok());
        let ids: Vec<&str> = rc.fail.iter().map(|(i, _)| i.as_str()).collect();
        assert!(ids.contains(&"host-self-8006"));
        assert!(ids.contains(&"site-110-to-host-8006"));
        assert!(ids.contains(&"edge-101-to-host-8006"));
        assert!(rc
            .fail
            .iter()
            .any(|(_, w)| w.contains("control_not_proved")));
        assert!(
            !ids.contains(&"site-110-to-host-22"),
            "unrelated blocked cells keep their own control"
        );
    }

    #[test]
    fn i8_missing_and_external() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let mut r = all_pass(&cells);
        r.as_array_mut().unwrap().retain(|v| {
            !v["id"].as_str().unwrap().starts_with("public-") && v["id"] != "site-110-egress-dns"
        });
        let rc = judge(&cells, &r, false).unwrap();
        assert!(!rc.ok(), "a missing internal cell is not a pass");
        assert!(rc.missing.contains(&"site-110-egress-dns".to_string()));
        let mut r2 = all_pass(&cells);
        r2.as_array_mut()
            .unwrap()
            .retain(|v| !v["id"].as_str().unwrap().starts_with("public-"));
        let rc2 = judge(&cells, &r2, false).unwrap();
        assert!(
            rc2.ok(),
            "external cells may be missing when no vantage point ran: {:?}",
            rc2.fail
        );
        assert_eq!(rc2.missing.len(), 3);
        let rc3 = judge(&cells, &r2, true).unwrap();
        assert!(!rc3.ok());
        assert!(rc3
            .fail
            .iter()
            .any(|(id, w)| id == "public-host-8006" && w.contains("external probe required")));
    }

    #[test]
    fn i9_bad_result_lists_are_errors() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        assert!(matches!(
            judge(&cells, &json!({}), false),
            Err(Error::Results(_))
        ));
        assert!(matches!(
            judge(&cells, &json!([{"id": "nope", "open": true}]), false),
            Err(Error::Results(_))
        ));
        assert!(matches!(
            judge(
                &cells,
                &json!([{"id": "host-self-22", "open": "yes"}]),
                false
            ),
            Err(Error::Results(_))
        ));
        assert!(matches!(
            judge(&cells, &json!([{"open": true}]), false),
            Err(Error::Results(_))
        ));
        assert!(matches!(
            judge(
                &cells,
                &json!([{"id": "host-self-22", "open": true}, {"id": "host-self-22", "open": false}]),
                false
            ),
            Err(Error::Results(_))
        ));
    }
}
