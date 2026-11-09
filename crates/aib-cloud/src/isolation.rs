// SPDX-License-Identifier: Apache-2.0
//! Isolation acceptance matrix (4.4.1 §5, M0 item 4): pure planning and judging.
//!
//! From a host's topology this module derives the **matrix** — every cell says who
//! probes which `(address, port, protocol)` and whether it must be **open**,
//! **blocked** or **closed** — and a **control** for every blocked cell: an *open*
//! cell against **the same address, port and protocol from an allowed source**
//! (#568 r1 H1: a control against `127.0.0.1` proves nothing about a service that
//! only listens on loopback). "Closed" cells assert a service does not listen on an
//! address at all (MariaDB on the LAN address, rpcbind on the host); they need no
//! control because the expectation is the absence of the service, not a firewall.
//!
//! The probes are I/O and run on the host (`ez2-isolation-probe`); the runner reports
//! `{plan_fnv, host, results[]}` and [`judge`] checks the report against **this**
//! plan (fingerprint + host) and **every result against its cell** (source, kind,
//! address, port — #568 r1 M1) before deciding pass / fail / missing. Cells the
//! runner cannot execute (external vantage point) are listed as missing; a receipt
//! with missing internal cells is never `ok`. No I/O in this crate.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;

use serde_json::{json, Value};

/// Why a topology was refused, the matrix is inconsistent, or a report cannot be judged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Not a dotted IPv4 literal (or, for `Ip6`, not an IPv6 literal).
    Ip(String),
    /// A CT id is outside Proxmox's range (100..=999_999_999).
    CtId(u32),
    /// Two nodes share an address or a CT id, or there is no site.
    Duplicate(String),
    /// The derived matrix broke its own rules (duplicate id, bad control).
    Matrix(String),
    /// The report has the wrong shape, belongs to another plan / host, or a result
    /// does not match its cell.
    Report(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Ip(s) => write!(f, "address refused: {s}"),
            Error::CtId(n) => write!(f, "ct id refused: {n}"),
            Error::Duplicate(s) => write!(f, "duplicate in topology: {s}"),
            Error::Matrix(s) => write!(f, "matrix inconsistent: {s}"),
            Error::Report(s) => write!(f, "report unusable: {s}"),
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
            let digits = !p.is_empty() && p.len() <= 3 && p.chars().all(|c| c.is_ascii_digit());
            let no_lead_zero = p.len() == 1 || !p.starts_with('0');
            if !digits || !no_lead_zero || p.parse::<u16>().map_or(true, |v| v > 255) {
                return Err(Error::Ip(s.to_owned()));
            }
        }
        Ok(Ip(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// An IPv6 literal (hex groups and colons, one `::` at most). Used only as a probe
/// target to prove containers have no IPv6 egress.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ip6(String);

impl Ip6 {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let ok_chars = !s.is_empty() && s.chars().all(|c| c.is_ascii_hexdigit() || c == ':');
        let groups = s.split(':').filter(|g| !g.is_empty()).count();
        let double = s.matches("::").count();
        let bad = !ok_chars
            || !s.contains(':')
            || s.len() > 39
            || double > 1
            || (double == 0 && groups != 8)
            || groups > 8
            || s.split(':').any(|g| g.len() > 4);
        if bad {
            return Err(Error::Ip(s.to_owned()));
        }
        Ok(Ip6(s.to_owned()))
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
        Ok(Ct { id, ip: Ip::parse(ip)? })
    }
}

/// Public addresses used to prove egress works / is filtered (TCP handshake, one DNS
/// query or one ICMP echo; nothing else is sent).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InternetTargets {
    pub dns: Ip,
    pub http: Ip,
    pub https: Ip,
    /// A public host with sshd (whitelist proof: host can, containers cannot).
    pub ssh: Ip,
    /// An IPv6 HTTPS endpoint; present when the host has IPv6 (containers must not).
    pub https6: Option<Ip6>,
}

/// Everything the matrix needs to know about one host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Topology {
    pub host_internal: Ip,
    pub host_public: Ip,
    pub tailnet: Option<Ip>,
    /// Another Ez2 host (EU01): containers must not reach its ssh, the host may.
    pub peer_host: Option<Ip>,
    pub edge: Ct,
    pub sites: Vec<Ct>,
    pub internet: InternetTargets,
}

impl Topology {
    /// Distinct CT ids, distinct addresses, at least one site.
    pub fn validate(&self) -> Result<(), Error> {
        if self.sites.is_empty() {
            return Err(Error::Duplicate("no site CT".to_owned()));
        }
        let mut ids: Vec<u32> = Vec::new();
        for id in core::iter::once(self.edge.id).chain(self.sites.iter().map(|s| s.id)) {
            if ids.contains(&id) {
                return Err(Error::Duplicate(alloc::format!("ct {id}")));
            }
            ids.push(id);
        }
        let mut addrs: Vec<&str> = alloc::vec![self.host_internal.as_str(), self.host_public.as_str(), self.edge.ip.as_str()];
        addrs.extend(self.sites.iter().map(|s| s.ip.as_str()));
        addrs.extend(self.tailnet.iter().map(Ip::as_str));
        addrs.extend(self.peer_host.iter().map(Ip::as_str));
        let mut seen: Vec<&str> = Vec::new();
        for a in addrs {
            if seen.contains(&a) {
                return Err(Error::Duplicate(a.to_owned()));
            }
            seen.push(a);
        }
        Ok(())
    }
}

/// Who runs the probe.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    Host,
    /// Inside a CT's network namespace.
    Ct(u32),
    /// From outside the host (EU01 or any public vantage point); the host-side runner
    /// cannot execute these, so they come back missing unless merged in.
    External,
}

impl Source {
    pub fn tag(&self) -> String {
        match self {
            Source::Host => "host".to_owned(),
            Source::Ct(id) => alloc::format!("ct:{id}"),
            Source::External => "external".to_owned(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Proto {
    Tcp,
    /// A DNS query over UDP/53.
    Dns,
    Icmp,
}

impl Proto {
    pub fn tag(self) -> &'static str {
        match self {
            Proto::Tcp => "tcp",
            Proto::Dns => "dns",
            Proto::Icmp => "icmp",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Expect {
    /// The connection must succeed.
    Open,
    /// The connection must fail **and** the control (same target, allowed source) must be open.
    Blocked,
    /// The connection must fail because nothing listens there (configuration assertion).
    Closed,
}

impl Expect {
    pub fn tag(self) -> &'static str {
        match self {
            Expect::Open => "open",
            Expect::Blocked => "blocked",
            Expect::Closed => "closed",
        }
    }
}

/// One cell of the matrix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    pub id: String,
    /// 4.4.1 section the cell proves (e.g. "5.2"), "ctl" for pure controls.
    pub section: &'static str,
    pub from: Source,
    /// IPv4 or IPv6 literal.
    pub to: String,
    /// 0 for ICMP.
    pub port: u16,
    pub proto: Proto,
    pub expect: Expect,
    /// Blocked cells: id of the Open cell with the same `(to, port, proto)`.
    pub control: Option<String>,
    pub why: &'static str,
}

struct Builder {
    cells: Vec<Cell>,
}

impl Builder {
    #[allow(clippy::too_many_arguments)]
    fn add(&mut self, id: String, section: &'static str, from: Source, to: &str, port: u16, proto: Proto, expect: Expect, control: Option<String>, why: &'static str) {
        self.cells.push(Cell { id, section, from, to: to.to_owned(), port, proto, expect, control, why });
    }
    /// An Open cell; returns its id so it can serve as a control.
    #[allow(clippy::too_many_arguments)]
    fn open(&mut self, id: String, section: &'static str, from: Source, to: &str, port: u16, proto: Proto, why: &'static str) -> String {
        self.add(id.clone(), section, from, to, port, proto, Expect::Open, None, why);
        id
    }
    #[allow(clippy::too_many_arguments)]
    fn blocked(&mut self, id: String, section: &'static str, from: Source, to: &str, port: u16, proto: Proto, control: &str, why: &'static str) {
        self.add(id, section, from, to, port, proto, Expect::Blocked, Some(control.to_owned()), why);
    }
    fn closed(&mut self, id: String, section: &'static str, from: Source, to: &str, port: u16, why: &'static str) {
        self.add(id, section, from, to, port, Proto::Tcp, Expect::Closed, None, why);
    }
}

/// Matrix rules every derived matrix must satisfy (exported so a test can knife them):
/// unique ids; every Blocked cell names an existing Open cell with the same
/// `(to, port, proto)` and a different source; Open / Closed cells carry no control.
pub fn check_matrix(cells: &[Cell]) -> Result<(), Error> {
    for (i, c) in cells.iter().enumerate() {
        if cells[..i].iter().any(|x| x.id == c.id) {
            return Err(Error::Matrix(alloc::format!("duplicate id {}", c.id)));
        }
        match (c.expect, &c.control) {
            (Expect::Blocked, Some(ctl)) => {
                let Some(k) = cells.iter().find(|x| &x.id == ctl) else {
                    return Err(Error::Matrix(alloc::format!("{}: control {ctl} missing", c.id)));
                };
                if k.expect != Expect::Open {
                    return Err(Error::Matrix(alloc::format!("{}: control {ctl} is not an Open cell", c.id)));
                }
                if (k.to.as_str(), k.port, k.proto) != (c.to.as_str(), c.port, c.proto) {
                    return Err(Error::Matrix(alloc::format!(
                        "{}: control {ctl} probes {}:{} {:?}, not the same target",
                        c.id, k.to, k.port, k.proto
                    )));
                }
                if k.from == c.from {
                    return Err(Error::Matrix(alloc::format!("{}: control {ctl} has the same source", c.id)));
                }
            }
            (Expect::Blocked, None) => return Err(Error::Matrix(alloc::format!("{}: blocked without control", c.id))),
            (_, Some(_)) => return Err(Error::Matrix(alloc::format!("{}: only blocked cells carry a control", c.id))),
            _ => {}
        }
    }
    Ok(())
}

/// Build the full matrix for a topology.
#[allow(non_snake_case)]
pub fn matrix(t: &Topology) -> Result<Vec<Cell>, Error> {
    t.validate()?;
    let mut b = Builder { cells: Vec::new() };
    let e = &t.edge;
    let hi = t.host_internal.as_str();
    let hp = t.host_public.as_str();
    let (H, E) = (Source::Host, Source::Ct(e.id));

    // --- controls from the host: never filtered towards its own addresses or the internet ---
    let c_hi22 = b.open("host-to-hostint-22".to_owned(), "ctl", H.clone(), hi, 22, Proto::Tcp, "宿主 sshd 在內網位址上聽");
    let c_hi8006 = b.open("host-to-hostint-8006".to_owned(), "ctl", H.clone(), hi, 8006, Proto::Tcp, "pveproxy 在內網位址上聽");
    let c_hi3128 = b.open("host-to-hostint-3128".to_owned(), "ctl", H.clone(), hi, 3128, Proto::Tcp, "spiceproxy 在內網位址上聽");
    let c_hp22 = b.open("host-to-hostpub-22".to_owned(), "ctl", H.clone(), hp, 22, Proto::Tcp, "宿主 sshd 在公網位址上聽");
    let c_hp8006 = b.open("host-to-hostpub-8006".to_owned(), "ctl", H.clone(), hp, 8006, Proto::Tcp, "pveproxy 在公網位址上聽");
    let c_e80 = b.open(alloc::format!("host-to-edge-{}-80", e.id), "ctl", H.clone(), e.ip.as_str(), 80, Proto::Tcp, "Edge 80 從宿主可達");
    b.open("host-egress-443".to_owned(), "ctl", H.clone(), t.internet.https.as_str(), 443, Proto::Tcp, "宿主出站 443");
    b.open("host-egress-80".to_owned(), "ctl", H.clone(), t.internet.http.as_str(), 80, Proto::Tcp, "宿主出站 80");
    b.open("host-egress-dns".to_owned(), "ctl", H.clone(), t.internet.dns.as_str(), 53, Proto::Dns, "宿主 DNS");
    let c_ssh = b.open("host-egress-22".to_owned(), "ctl", H.clone(), t.internet.ssh.as_str(), 22, Proto::Tcp, "宿主出站 22 可達(白名單對照)");
    let c_tn = t.tailnet.as_ref().map(|tn| b.open("host-tailnet-icmp".to_owned(), "ctl", H.clone(), tn.as_str(), 0, Proto::Icmp, "tailnet 位址活著"));
    let c_peer = t.peer_host.as_ref().map(|p| b.open("host-to-peerhost-22".to_owned(), "ctl", H.clone(), p.as_str(), 22, Proto::Tcp, "宿主可達另一台主機的 ssh"));
    let c_v6 = t.internet.https6.as_ref().map(|v| b.open("host-egress6-443".to_owned(), "ctl", H.clone(), v.as_str(), 443, Proto::Tcp, "宿主有 IPv6 出站"));
    let c_e_self22 = b.open(alloc::format!("edge-{}-self-22", e.id), "ctl", E.clone(), e.ip.as_str(), 22, Proto::Tcp, "Edge sshd 在自己的內網位址上聽");

    // --- host configuration assertion (no firewall involved) ---
    b.closed("host-hostint-111-closed".to_owned(), "7", H.clone(), hi, 111, "rpcbind 關(5.2)");

    for s in &t.sites {
        let S = Source::Ct(s.id);
        let sip = s.ip.as_str();
        let c_s80 = b.open(alloc::format!("edge-to-site-{}-80", s.id), "5.2", E.clone(), sip, 80, Proto::Tcp, "Edge → 站 80 是唯一准許的入站");
        let c_s_self22 = b.open(alloc::format!("site-{}-self-22", s.id), "ctl", S.clone(), sip, 22, Proto::Tcp, "站 sshd 在自己的內網位址上聽");
        b.open(alloc::format!("site-{}-egress-443", s.id), "5.6", S.clone(), t.internet.https.as_str(), 443, Proto::Tcp, "站出站 443 通(更新 / API)");
        b.open(alloc::format!("site-{}-egress-80", s.id), "5.6", S.clone(), t.internet.http.as_str(), 80, Proto::Tcp, "站出站 80 通");
        b.open(alloc::format!("site-{}-egress-dns", s.id), "5.6", S.clone(), t.internet.dns.as_str(), 53, Proto::Dns, "站 DNS 通");
        b.closed(alloc::format!("site-{}-lan-3306-closed", s.id), "5.1", S.clone(), sip, 3306, "MariaDB 只綁 127.0.0.1,內網位址上沒有 3306");
        b.blocked(alloc::format!("site-{}-to-hostint-22", s.id), "5.2", S.clone(), hi, 22, Proto::Tcp, &c_hi22, "站 → 宿主 ssh");
        b.blocked(alloc::format!("site-{}-to-hostint-8006", s.id), "5.2", S.clone(), hi, 8006, Proto::Tcp, &c_hi8006, "站 → 宿主 8006");
        b.blocked(alloc::format!("site-{}-to-hostint-3128", s.id), "5.2", S.clone(), hi, 3128, Proto::Tcp, &c_hi3128, "站 → 宿主 3128");
        b.blocked(alloc::format!("site-{}-to-hostpub-22", s.id), "5.6", S.clone(), hp, 22, Proto::Tcp, &c_hp22, "站 → 宿主公網位址 ssh(防 A→公網→DNAT→B)");
        b.blocked(alloc::format!("site-{}-to-hostpub-8006", s.id), "5.6", S.clone(), hp, 8006, Proto::Tcp, &c_hp8006, "站 → 宿主公網位址 8006");
        b.blocked(alloc::format!("site-{}-to-edge-22", s.id), "5.8", S.clone(), e.ip.as_str(), 22, Proto::Tcp, &c_e_self22, "站 → Edge ssh");
        b.blocked(alloc::format!("site-{}-to-edge-80", s.id), "5.6", S.clone(), e.ip.as_str(), 80, Proto::Tcp, &c_e80, "站 → Edge 80(內網黑名單;公開站走公網)");
        if let (Some(tn), Some(c)) = (&t.tailnet, &c_tn) {
            b.blocked(alloc::format!("site-{}-to-tailnet", s.id), "5.6", S.clone(), tn.as_str(), 0, Proto::Icmp, c, "站 → tailnet");
        }
        b.blocked(alloc::format!("site-{}-egress-22", s.id), "5.6", S.clone(), t.internet.ssh.as_str(), 22, Proto::Tcp, &c_ssh, "站出站 22 不在白名單");
        if let (Some(p), Some(c)) = (&t.peer_host, &c_peer) {
            b.blocked(alloc::format!("site-{}-to-peerhost-22", s.id), "5.6", S.clone(), p.as_str(), 22, Proto::Tcp, c, "站 → 另一台主機 ssh");
        }
        if let (Some(v), Some(c)) = (&t.internet.https6, &c_v6) {
            b.blocked(alloc::format!("site-{}-egress6-443", s.id), "5.6", S.clone(), v.as_str(), 443, Proto::Tcp, c, "站沒有 IPv6 出站");
        }
        b.blocked(alloc::format!("edge-to-site-{}-22", s.id), "5.8", E.clone(), sip, 22, Proto::Tcp, &c_s_self22, "Edge → 站 ssh(Edge 被打穿也進不了站)");
        for p in t.sites.iter().filter(|p| p.id != s.id) {
            let P = Source::Ct(p.id);
            b.blocked(alloc::format!("site-{}-to-site-{}-80", p.id, s.id), "5.1", P.clone(), sip, 80, Proto::Tcp, &c_s80, "站 A → 站 B 網站");
            b.blocked(alloc::format!("site-{}-to-site-{}-22", p.id, s.id), "5.1", P, sip, 22, Proto::Tcp, &c_s_self22, "站 A → 站 B ssh");
        }
    }
    // --- edge egress / lateral ---
    b.open(alloc::format!("edge-{}-egress-443", e.id), "5.8", E.clone(), t.internet.https.as_str(), 443, Proto::Tcp, "Edge 出站 443 通(ACME)");
    b.blocked(alloc::format!("edge-{}-to-hostint-22", e.id), "5.8", E.clone(), hi, 22, Proto::Tcp, &c_hi22, "Edge → 宿主 ssh");
    b.blocked(alloc::format!("edge-{}-to-hostint-8006", e.id), "5.8", E.clone(), hi, 8006, Proto::Tcp, &c_hi8006, "Edge → 宿主 8006");
    b.blocked(alloc::format!("edge-{}-egress-22", e.id), "5.8", E.clone(), t.internet.ssh.as_str(), 22, Proto::Tcp, &c_ssh, "Edge 出站 22 不在白名單");
    if let (Some(tn), Some(c)) = (&t.tailnet, &c_tn) {
        b.blocked(alloc::format!("edge-{}-to-tailnet", e.id), "5.8", E.clone(), tn.as_str(), 0, Proto::Icmp, c, "Edge → tailnet");
    }
    if let (Some(v), Some(c)) = (&t.internet.https6, &c_v6) {
        b.blocked(alloc::format!("edge-{}-egress6-443", e.id), "5.8", E.clone(), v.as_str(), 443, Proto::Tcp, c, "Edge 沒有 IPv6 出站");
    }
    // --- from outside ---
    b.open("public-host-22".to_owned(), "7.3", Source::External, hp, 22, Proto::Tcp, "公網 ssh 開(金鑰 + fail2ban)");
    b.open("public-host-443".to_owned(), "6", Source::External, hp, 443, Proto::Tcp, "公網 443 到 Edge");
    b.blocked("public-host-8006".to_owned(), "7.3", Source::External, hp, 8006, Proto::Tcp, &c_hp8006, "8006 不對公網");

    check_matrix(&b.cells)?;
    Ok(b.cells)
}

/// The job list for the host-side runner: one object per cell, sorted by id.
pub fn probe_plan(cells: &[Cell]) -> Value {
    let mut sorted: Vec<&Cell> = cells.iter().collect();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    Value::Array(
        sorted
            .iter()
            .map(|c| json!({"id": c.id, "from": c.from.tag(), "kind": c.proto.tag(), "ip": c.to, "port": c.port, "expect": c.expect.tag()}))
            .collect(),
    )
}

/// FNV-1a 64 of the compact JSON of a plan. The runner computes the same over the plan it
/// executed (`json.dumps(plan, sort_keys=True, separators=(",", ":"))`), so a report is
/// matched to exactly this plan. It binds, it does not sign.
pub fn plan_fingerprint(plan: &Value) -> String {
    let mut text = String::new();
    canonical(plan, &mut text);
    fnv1a64(text.as_bytes())
}

/// Compact JSON with object keys sorted, independent of serde_json's map type / features
/// (`preserve_order` anywhere in the workspace would otherwise change the fingerprint).
fn canonical(v: &Value, out: &mut String) {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(k).unwrap_or_default());
                out.push(':');
                canonical(&m[*k], out);
            }
            out.push('}');
        }
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                canonical(x, out);
            }
            out.push(']');
        }
        other => out.push_str(&serde_json::to_string(other).unwrap_or_default()),
    }
}

fn fnv1a64(bytes: &[u8]) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in bytes {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    alloc::format!("{h:016x}")
}

/// Outcome of one matrix run.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Receipt {
    pub plan_fnv: String,
    pub host: String,
    pub pass: Vec<String>,
    /// (cell id, reason)
    pub fail: Vec<(String, String)>,
    /// Cells with no result; `ok()` tolerates only External ones.
    pub missing: Vec<String>,
}

impl Receipt {
    pub fn ok(&self) -> bool {
        self.fail.is_empty() && self.missing.iter().all(|id| id.starts_with("public-"))
    }

    pub fn to_json(&self) -> Value {
        json!({
            "ok": self.ok(),
            "plan_fnv": self.plan_fnv,
            "host": self.host,
            "pass": self.pass,
            "fail": self.fail.iter().map(|(id, why)| json!({"id": id, "why": why})).collect::<Vec<_>>(),
            "missing": self.missing,
            "counts": {"pass": self.pass.len(), "fail": self.fail.len(), "missing": self.missing.len()},
        })
    }
}

/// Judge a runner report against the cells it was derived from.
/// `report = {"plan_fnv": "<16 hex>", "host": "<host_public>", "results": [{id, from, kind, ip, port, open}, …]}`.
/// The fingerprint must equal `plan_fingerprint(&probe_plan(cells))`, the host must be the
/// topology's public address, and every result must match its cell on from / kind / ip /
/// port (#568 r1 M1); unknown or duplicate ids and a non-bool `open` are errors. Then:
/// Open must be open; Closed must be closed; Blocked must be closed **and** its control
/// must have passed; cells without a result are missing. With `external_required`,
/// missing External cells become failures.
pub fn judge(cells: &[Cell], host_public: &str, report: &Value, external_required: bool) -> Result<Receipt, Error> {
    let obj = report.as_object().ok_or_else(|| Error::Report("not an object".to_owned()))?;
    let fnv = obj.get("plan_fnv").and_then(Value::as_str).ok_or_else(|| Error::Report("missing plan_fnv".to_owned()))?;
    let want = plan_fingerprint(&probe_plan(cells));
    if fnv != want {
        return Err(Error::Report(alloc::format!("plan_fnv {fnv} is not this plan ({want})")));
    }
    let host = obj.get("host").and_then(Value::as_str).ok_or_else(|| Error::Report("missing host".to_owned()))?;
    if host != host_public {
        return Err(Error::Report(alloc::format!("report is for host {host}, not {host_public}")));
    }
    let arr = obj.get("results").and_then(Value::as_array).ok_or_else(|| Error::Report("results is not an array".to_owned()))?;
    let mut observed: Vec<(String, bool)> = Vec::new();
    for r in arr {
        let id = r.get("id").and_then(Value::as_str).ok_or_else(|| Error::Report("result without id".to_owned()))?;
        let Some(c) = cells.iter().find(|c| c.id == id) else {
            return Err(Error::Report(alloc::format!("unknown cell id {id}")));
        };
        let s = |k: &str| r.get(k).and_then(Value::as_str).unwrap_or("");
        let port = r.get("port").and_then(Value::as_u64).unwrap_or(u64::MAX);
        if s("from") != c.from.tag() || s("kind") != c.proto.tag() || s("ip") != c.to || port != u64::from(c.port) {
            return Err(Error::Report(alloc::format!(
                "{id}: result probed {} {}://{}:{} but the cell is {} {}://{}:{}",
                s("from"),
                s("kind"),
                s("ip"),
                port,
                c.from.tag(),
                c.proto.tag(),
                c.to,
                c.port
            )));
        }
        let open = r.get("open").and_then(Value::as_bool).ok_or_else(|| Error::Report(alloc::format!("{id}: open is not a bool")))?;
        if observed.iter().any(|(i, _)| i == id) {
            return Err(Error::Report(alloc::format!("duplicate result for {id}")));
        }
        observed.push((id.to_owned(), open));
    }
    let lookup = |id: &str| observed.iter().find(|(i, _)| i == id).map(|(_, o)| *o);
    let mut rc = Receipt { plan_fnv: want, host: host_public.to_owned(), ..Receipt::default() };
    let mut passed: Vec<String> = Vec::new();
    for c in cells.iter().filter(|c| c.expect != Expect::Blocked) {
        match (lookup(&c.id), c.expect) {
            (Some(true), Expect::Open) | (Some(false), Expect::Closed) => {
                passed.push(c.id.clone());
                rc.pass.push(c.id.clone());
            }
            (Some(false), Expect::Open) => rc.fail.push((c.id.clone(), "expected open, observed closed".to_owned())),
            (Some(true), Expect::Closed) => rc.fail.push((c.id.clone(), "expected nothing listening, observed OPEN".to_owned())),
            (None, _) => rc.missing.push(c.id.clone()),
            _ => {}
        }
    }
    for c in cells.iter().filter(|c| c.expect == Expect::Blocked) {
        match lookup(&c.id) {
            Some(true) => rc.fail.push((c.id.clone(), "expected blocked, observed OPEN".to_owned())),
            Some(false) => {
                let ctl = c.control.as_deref().unwrap_or("");
                if passed.iter().any(|p| p == ctl) {
                    rc.pass.push(c.id.clone());
                } else {
                    rc.fail.push((c.id.clone(), alloc::format!("control_not_proved: {ctl} did not pass, a timeout alone is not a pass")));
                }
            }
            None => rc.missing.push(c.id.clone()),
        }
    }
    if external_required {
        let ext: Vec<String> = rc.missing.iter().filter(|id| id.starts_with("public-")).cloned().collect();
        for id in ext {
            rc.missing.retain(|m| m != &id);
            rc.fail.push((id, "external probe required but missing".to_owned()));
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
            peer_host: Some(Ip::parse("136.243.105.110").unwrap()),
            edge: Ct::new(101, "10.77.0.2").unwrap(),
            sites: sites.iter().map(|(id, ip)| Ct::new(*id, ip).unwrap()).collect(),
            internet: InternetTargets {
                dns: Ip::parse("1.1.1.1").unwrap(),
                http: Ip::parse("151.101.2.132").unwrap(),
                https: Ip::parse("140.82.121.4").unwrap(),
                ssh: Ip::parse("140.82.121.4").unwrap(),
                https6: Some(Ip6::parse("2606:4700:4700::1111").unwrap()),
            },
        }
    }

    fn report(cells: &[Cell], host: &str, edit: impl Fn(&Cell, &mut Value)) -> Value {
        let results: Vec<Value> = cells
            .iter()
            .map(|c| {
                let mut v = json!({"id": c.id, "from": c.from.tag(), "kind": c.proto.tag(), "ip": c.to, "port": c.port, "open": c.expect == Expect::Open});
                edit(c, &mut v);
                v
            })
            .collect();
        json!({"plan_fnv": plan_fingerprint(&probe_plan(cells)), "host": host, "results": results})
    }

    #[test]
    fn i1_controls_probe_the_same_target_from_another_source() {
        let cells = matrix(&topo(&[(110, "10.77.0.10"), (112, "10.77.0.12")])).unwrap();
        check_matrix(&cells).unwrap();
        for c in cells.iter().filter(|c| c.expect == Expect::Blocked) {
            let k = cells.iter().find(|x| Some(&x.id) == c.control.as_ref()).unwrap();
            assert_eq!((k.to.as_str(), k.port, k.proto, k.expect), (c.to.as_str(), c.port, c.proto, Expect::Open), "{}", c.id);
            assert_ne!(k.from, c.from, "{}", c.id);
        }
        assert!(!cells.iter().any(|c| c.to == "127.0.0.1"), "loopback controls prove nothing");
        assert!(cells.iter().filter(|c| c.expect == Expect::Blocked).count() >= 30);
    }

    #[test]
    fn i1b_check_matrix_knives() {
        let mut cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let i = cells.iter().position(|c| c.id == "site-110-to-hostint-22").unwrap();
        cells[i].control = Some("host-to-hostint-8006".to_owned());
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("not the same target")));
        cells[i].control = Some("nope".to_owned());
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("missing")));
        cells[i].control = None;
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("without control")));
        cells[i].control = Some("site-110-to-hostint-22".to_owned());
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("not an Open cell")));
        let mut cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let j = cells.iter().position(|c| c.id == "host-to-hostint-22").unwrap();
        cells[j].from = Source::Ct(110);
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("same source")));
        let mut cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        cells.push(cells[0].clone());
        assert!(matches!(check_matrix(&cells), Err(Error::Matrix(m)) if m.contains("duplicate id")));
    }

    #[test]
    fn i2_two_sites_get_peer_cells_one_site_does_not() {
        let one = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let two = matrix(&topo(&[(110, "10.77.0.10"), (112, "10.77.0.12")])).unwrap();
        assert!(!one.iter().any(|c| c.id.starts_with("site-") && c.id.contains("-to-site-")));
        assert!(two.iter().any(|c| c.id == "site-110-to-site-112-80"));
        assert!(two.iter().any(|c| c.id == "site-112-to-site-110-22"));
        assert!(two.iter().any(|c| c.id == "site-112-lan-3306-closed" && c.expect == Expect::Closed && c.control.is_none()));
        assert!(two.len() > one.len());
        let mut t = topo(&[(110, "10.77.0.10")]);
        t.tailnet = None;
        t.peer_host = None;
        t.internet.https6 = None;
        let bare = matrix(&t).unwrap();
        assert!(!bare.iter().any(|c| c.id.contains("tailnet") || c.id.contains("peerhost") || c.id.contains("egress6")));
    }

    #[test]
    fn i3_topology_and_address_validation() {
        let mut t = topo(&[(110, "10.77.0.10")]);
        t.sites.push(Ct::new(110, "10.77.0.12").unwrap());
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        let mut t = topo(&[(110, "10.77.0.2")]);
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        t.sites.clear();
        assert!(matches!(matrix(&t), Err(Error::Duplicate(_))));
        assert!(matches!(Ct::new(5, "10.0.0.1"), Err(Error::CtId(5))));
        for bad in ["10.0.0", "010.0.0.1", "１.2.3.4", "1.2.3.256", "a.b.c.d", "::1"] {
            assert!(matches!(Ip::parse(bad), Err(Error::Ip(_))), "{bad}");
        }
        for bad in ["1.2.3.4", "2606:4700:4700::1111::", "12345::1", "gggg::1", "1:2:3:4:5:6:7", ""] {
            assert!(matches!(Ip6::parse(bad), Err(Error::Ip(_))), "{bad}");
        }
        assert!(Ip6::parse("2606:4700:4700::1111").is_ok());
        assert!(Ip6::parse("2a01:4f9:4b:134f::2").is_ok());
        assert!(Ip6::parse("1:2:3:4:5:6:7:8").is_ok());
    }

    #[test]
    fn i4_plan_and_fingerprint_are_stable() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let plan = probe_plan(&cells);
        let arr = plan.as_array().unwrap();
        assert_eq!(arr.len(), cells.len());
        let ids: Vec<&str> = arr.iter().map(|v| v["id"].as_str().unwrap()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(ids, sorted);
        let c = arr.iter().find(|v| v["id"] == "site-110-to-hostint-22").unwrap();
        assert_eq!(
            (c["from"].as_str().unwrap(), c["kind"].as_str().unwrap(), c["ip"].as_str().unwrap(), c["port"].as_u64().unwrap(), c["expect"].as_str().unwrap()),
            ("ct:110", "tcp", "10.77.0.1", 22, "blocked")
        );
        assert!(arr.iter().all(|v| v.as_object().unwrap().len() == 6));
        let f1 = plan_fingerprint(&plan);
        assert_eq!(f1, plan_fingerprint(&probe_plan(&matrix(&topo(&[(110, "10.77.0.10")])).unwrap())));
        assert_eq!(f1.len(), 16);
        assert_ne!(f1, plan_fingerprint(&probe_plan(&matrix(&topo(&[(110, "10.77.0.11")])).unwrap())));
        // FNV-1a 64 known answer for the single byte "a"
        assert_eq!(fnv1a64(b"a"), "af63dc4c8601ec8c");
    }

    #[test]
    fn i5_judge_all_pass() {
        let cells = matrix(&topo(&[(110, "10.77.0.10"), (112, "10.77.0.12")])).unwrap();
        let rc = judge(&cells, "135.181.75.90", &report(&cells, "135.181.75.90", |_, _| {}), false).unwrap();
        assert!(rc.ok(), "{:?}", rc.fail);
        assert_eq!(rc.pass.len(), cells.len());
        assert_eq!(rc.to_json()["counts"]["fail"], 0);
        assert_eq!(rc.plan_fnv, plan_fingerprint(&probe_plan(&cells)));
    }

    #[test]
    fn i6_judge_failures() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let rc = judge(&cells, "135.181.75.90", &report(&cells, "135.181.75.90", |c, v| if c.id == "site-110-to-hostint-22" { v["open"] = json!(true) }), false).unwrap();
        assert_eq!(rc.fail, vec![("site-110-to-hostint-22".to_string(), "expected blocked, observed OPEN".to_string())]);
        let rc = judge(&cells, "135.181.75.90", &report(&cells, "135.181.75.90", |c, v| if c.id == "host-to-hostint-8006" { v["open"] = json!(false) }), false).unwrap();
        let ids: Vec<&str> = rc.fail.iter().map(|(i, _)| i.as_str()).collect();
        assert!(ids.contains(&"host-to-hostint-8006") && ids.contains(&"site-110-to-hostint-8006") && ids.contains(&"edge-101-to-hostint-8006"));
        assert!(rc.fail.iter().any(|(_, w)| w.contains("control_not_proved")));
        assert!(!ids.contains(&"site-110-to-hostint-22"));
        let rc = judge(&cells, "135.181.75.90", &report(&cells, "135.181.75.90", |c, v| if c.id == "site-110-lan-3306-closed" { v["open"] = json!(true) }), false).unwrap();
        assert!(rc.fail.iter().any(|(i, w)| i == "site-110-lan-3306-closed" && w.contains("observed OPEN")));
    }

    #[test]
    fn i7_report_must_match_plan_host_and_cells() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let good = report(&cells, "135.181.75.90", |_, _| {});
        assert!(judge(&cells, "135.181.75.90", &good, false).unwrap().ok());
        let other = matrix(&topo(&[(110, "10.77.0.11")])).unwrap();
        assert!(matches!(judge(&other, "135.181.75.90", &good, false), Err(Error::Report(m)) if m.contains("not this plan")));
        assert!(matches!(judge(&cells, "1.2.3.4", &good, false), Err(Error::Report(m)) if m.contains("not 1.2.3.4")));
        let moved = report(&cells, "135.181.75.90", |_, v| v["ip"] = json!("9.9.9.9"));
        assert!(matches!(judge(&cells, "135.181.75.90", &moved, false), Err(Error::Report(m)) if m.contains("but the cell is")));
        let wrong_from = report(&cells, "135.181.75.90", |c, v| if c.id == "site-110-to-hostint-22" { v["from"] = json!("host") });
        assert!(matches!(judge(&cells, "135.181.75.90", &wrong_from, false), Err(Error::Report(_))));
        let wrong_port = report(&cells, "135.181.75.90", |c, v| if c.id == "site-110-to-hostint-22" { v["port"] = json!(2222) });
        assert!(matches!(judge(&cells, "135.181.75.90", &wrong_port, false), Err(Error::Report(_))));
        assert!(matches!(judge(&cells, "135.181.75.90", &json!([]), false), Err(Error::Report(_))));
        assert!(matches!(judge(&cells, "135.181.75.90", &json!({"host": "135.181.75.90", "results": []}), false), Err(Error::Report(m)) if m.contains("plan_fnv")));
        let mut dup = good.clone();
        let first = dup["results"][0].clone();
        dup["results"].as_array_mut().unwrap().push(first);
        assert!(matches!(judge(&cells, "135.181.75.90", &dup, false), Err(Error::Report(m)) if m.contains("duplicate")));
        let mut unknown = good.clone();
        unknown["results"].as_array_mut().unwrap().push(json!({"id": "nope", "from": "host", "kind": "tcp", "ip": "1.1.1.1", "port": 1, "open": true}));
        assert!(matches!(judge(&cells, "135.181.75.90", &unknown, false), Err(Error::Report(m)) if m.contains("unknown")));
    }

    #[test]
    fn i8_missing_cells() {
        let cells = matrix(&topo(&[(110, "10.77.0.10")])).unwrap();
        let mut r = report(&cells, "135.181.75.90", |_, _| {});
        r["results"].as_array_mut().unwrap().retain(|v| !v["id"].as_str().unwrap().starts_with("public-"));
        let rc = judge(&cells, "135.181.75.90", &r, false).unwrap();
        assert!(rc.ok(), "external cells may be missing: {:?}", rc.fail);
        assert_eq!(rc.missing.len(), 3);
        let rc3 = judge(&cells, "135.181.75.90", &r, true).unwrap();
        assert!(!rc3.ok() && rc3.fail.iter().any(|(id, w)| id == "public-host-8006" && w.contains("external probe required")));
        r["results"].as_array_mut().unwrap().retain(|v| v["id"] != "site-110-egress6-443");
        let rc = judge(&cells, "135.181.75.90", &r, false).unwrap();
        assert!(!rc.ok());
        assert!(rc.missing.contains(&"site-110-egress6-443".to_string()));
    }

    #[test]
    fn i9_fingerprint_matches_the_python_runner() {
        // ez2-isolation-probe computes FNV-1a 64 over json.dumps(plan, sort_keys=True,
        // separators=(",", ":")); this value was produced by that code for this plan, with the
        // keys deliberately given out of order here.
        let plan = json!([
            {"port": 22, "id": "public-host-22", "expect": "open", "kind": "tcp", "ip": "135.181.75.90", "from": "external"},
            {"id": "public-host-443", "from": "external", "kind": "tcp", "ip": "135.181.75.90", "port": 443, "expect": "open"},
            {"id": "public-host-8006", "from": "external", "kind": "tcp", "ip": "135.181.75.90", "port": 8006, "expect": "blocked"},
            {"id": "host-egress-dns", "from": "host", "kind": "dns", "ip": "1.1.1.1", "port": 53, "expect": "open"}
        ]);
        assert_eq!(plan_fingerprint(&plan), "52240dcf9134f54d");
        let mut other = plan.clone();
        other[3]["port"] = json!(5353);
        assert_ne!(plan_fingerprint(&other), "52240dcf9134f54d");
    }
}
