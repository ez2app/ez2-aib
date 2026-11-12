// SPDX-License-Identifier: Apache-2.0
//! Site containers as data (4.4.1 §8 / §12 `site create`, P1 "漂移偵測 + 每 CT 資源限額",
//! outline #18): what one customer site's CT **must** look like, the argument lists that
//! make it so, and the diff between that and what the host reports. No I/O here: the host
//! shell (`aib-cloud-host site …`) runs `pct` and reads `/etc/pve`, this module only decides.
//!
//! Three things live here:
//! * [`SiteSpec`] — validated input (site code, CT id, internal IP, hostnames, limits).
//!   Anything the bash PoC `ez2-site-create` checked with regexes is checked here, plus the
//!   limits, so an operator typo is refused before any `pct` runs.
//! * [`desired`] — the exact `pct config` keys a CT built from a spec must show, the firewall
//!   file it must have, and the per-site record; [`plan`] — the `pct` argument lists that
//!   produce them (idempotent: every step may be re-run).
//! * [`drift`] — compare the desired state with observed `pct config` text, the CT's
//!   firewall file and the host's record: every difference is one [`Drift`] with the key,
//!   the wanted and the observed value, so a daily timer can report "CT 112 memory 4096 ≠
//!   2048" instead of a red light.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;

use serde_json::{json, Value};

/// Why a spec, a record or an observation was refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Site code outside `^[a-z0-9][a-z0-9.-]{1,40}$`.
    Code(String),
    /// CT id outside 100..=999_999_999, or the template / edge id.
    CtId(u32),
    /// Internal IP outside 10.77.0.0/24 hosts 3..=249, or taken by host / edge.
    Ip(String),
    /// A hostname that is not a lower-case DNS name.
    Host(String),
    /// A resource limit outside the allowed band.
    Limit(&'static str, u32),
    /// Observed text could not be parsed.
    Observed(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Code(s) => write!(
                f,
                "site code refused: {s:?} (a-z 0-9 . - , 2..=41 chars, starts with a-z0-9)"
            ),
            Error::CtId(n) => write!(f, "ct id refused: {n}"),
            Error::Ip(s) => write!(f, "internal ip refused: {s} (10.77.0.3 … 10.77.0.249)"),
            Error::Host(s) => write!(f, "hostname refused: {s:?}"),
            Error::Limit(k, v) => write!(f, "limit {k}={v} outside the allowed band"),
            Error::Observed(s) => write!(f, "observed state unreadable: {s}"),
        }
    }
}

impl core::error::Error for Error {}

/// The disk a new site starts with, in Proxmox's `G`, which is GiB (Andy 11-12 13:07:
/// "10 GB", was 20). Shown, not judged: [`crate::quota`] counts a record that names no
/// `disk_gb` at this size. The size
/// a new container really gets is the template's (CT 900), a host setting this code does
/// not write.
pub const START_DISK_GB: u32 = 10;

/// Resource limits of one site CT. The numbers are **guarantees for the test tier**
/// (§23: the customer-facing plan ceiling is shown, not reserved); every field is bounded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Limits {
    /// vCPUs visible to the CT (1..=8).
    pub cores: u32,
    /// `cpulimit` in hundredths of a core: 200 = at most two full cores of host CPU time
    /// (100..=800). This is what stops one busy site from eating the host (§2 goal 2).
    pub cpulimit_x100: u32,
    /// Memory in MiB (512..=16384).
    pub memory_mb: u32,
    /// Swap in MiB (0..=4096).
    pub swap_mb: u32,
    /// `pids.max` via `lxc.cgroup2.pids.max` (fork-bomb ceiling; 256..=8192).
    pub pids_max: u32,
}

impl Limits {
    /// The first-day test tier (2 vCPU / 2 GiB / 512 MiB swap, two cores of CPU time,
    /// 2048 processes) — what CT 110 / 112 run with.
    pub const TEST_TIER: Limits = Limits {
        cores: 2,
        cpulimit_x100: 200,
        memory_mb: 2048,
        swap_mb: 512,
        pids_max: 2048,
    };

    /// What a new site starts with (Andy 11-10: "起步降到 1G、預期以 512、或更少記憶體運作"):
    /// 1 GiB of memory, the rest as the test tier. Measured idle sites hold 150–200 MiB;
    /// ARC raises the limit when a site really needs more.
    pub const START: Limits = Limits {
        cores: 2,
        cpulimit_x100: 200,
        memory_mb: 1024,
        swap_mb: 512,
        pids_max: 2048,
    };

    pub fn validate(&self) -> Result<(), Error> {
        let band = |k: &'static str, v: u32, lo: u32, hi: u32| {
            if (lo..=hi).contains(&v) {
                Ok(())
            } else {
                Err(Error::Limit(k, v))
            }
        };
        band("cores", self.cores, 1, 8)?;
        band("cpulimit_x100", self.cpulimit_x100, 100, 800)?;
        band("memory_mb", self.memory_mb, 512, 16_384)?;
        band("swap_mb", self.swap_mb, 0, 4096)?;
        band("pids_max", self.pids_max, 256, 8192)?;
        if self.cpulimit_x100 > self.cores * 100 {
            return Err(Error::Limit("cpulimit_x100", self.cpulimit_x100));
        }
        Ok(())
    }

    /// `cpulimit` as Proxmox writes it: `2`, `0.5`, `1.25`.
    pub fn cpulimit_str(&self) -> String {
        let whole = self.cpulimit_x100 / 100;
        let frac = self.cpulimit_x100 % 100;
        if frac == 0 {
            alloc::format!("{whole}")
        } else if frac.is_multiple_of(10) {
            alloc::format!("{whole}.{}", frac / 10)
        } else {
            alloc::format!("{whole}.{frac:02}")
        }
    }
}

/// One customer site, validated.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SiteSpec {
    pub code: String,
    pub ct: u32,
    /// Last octet of the 10.77.0.0/24 address.
    pub ip_last: u8,
    /// Hostnames, primary first.
    pub hosts: Vec<String>,
    pub wp: bool,
    pub locale: String,
    pub limits: Limits,
    /// Versioned firewall group the CT's `.fw` file points at (`ez2-site-v1`, `-v2`, …;
    /// NET-1 11-10 moved every site to v2). Groups are never edited in place, so a record
    /// says which one its CT was switched to.
    pub fw_group: String,
    /// Customer ssh (SSH-1, 4.4.1 §25): public port `2000 + ct` DNATed to the CT's 22. When
    /// set, the `.fw` file carries two extra IN rules before the group line.
    pub ssh_port: Option<u16>,
}

pub const TEMPLATE_CT: u32 = 900;
pub const EDGE_CT: u32 = 101;
/// Default group for a record that names none (the 11-09 PoC records).
pub const FW_GROUP: &str = "ez2-site-v1";

/// `ez2-site-v<N>` only: the group name goes verbatim into a firewall file.
pub fn valid_fw_group(g: &str) -> bool {
    g.strip_prefix("ez2-site-v")
        .is_some_and(|n| !n.is_empty() && n.len() <= 3 && n.bytes().all(|b| b.is_ascii_digit()))
}

/// The public port a customer uses to reach their CT's ssh (SSH-1): `2000 + ct`.
pub fn customer_ssh_port(ct: u32) -> Option<u16> {
    u16::try_from(2000 + ct).ok()
}

impl SiteSpec {
    /// Point the CT at another versioned group (rejects anything but `ez2-site-v<N>`).
    pub fn with_fw_group(mut self, group: &str) -> Result<Self, Error> {
        if !valid_fw_group(group) {
            return Err(Error::Code(alloc::format!("fw_group {group}")));
        }
        self.fw_group = group.to_owned();
        Ok(self)
    }
    /// Declare customer ssh; the port must be exactly `2000 + ct` (that is what the host DNATs).
    pub fn with_ssh_port(mut self, port: u16) -> Result<Self, Error> {
        if customer_ssh_port(self.ct) != Some(port) {
            return Err(Error::Code(alloc::format!(
                "ssh_port {port} is not 2000+{}",
                self.ct
            )));
        }
        self.ssh_port = Some(port);
        Ok(self)
    }
}
const NET: &str = "10.77.0.";

/// Words of the customer's request line (`site <code> …`, `site create`,
/// `aib_cloud::customer`) and words kept for requests to come. None of them is a site's
/// code or a customer's id, anywhere: in a request, in a record, in a roster, or out of
/// the generator that names a new site. One table, read by all of them (f1fae, c477a,
/// ebb36 11-11).
///
/// **Before a word is added**: read the host's site records and rosters for it. A record
/// or a roster holding a code that is in this table is refused whole (33e28, #652 L1).
pub const RESERVED_CODES: &[&str] = &[
    "admin-link",
    "backup",
    "create",
    "delete",
    "domain",
    "help",
    "list",
    "logs",
    "php-reload",
    "restart",
    "rm",
    "site",
    "sites",
    "status",
    "usage",
];

/// Site code: 2–41 chars of `[a-z0-9.-]`, starting with a letter or digit (it becomes a hostname,
/// a record file name and a Caddy marker, so nothing else is allowed).
pub fn valid_code(code: &str) -> bool {
    let b = code.as_bytes();
    !RESERVED_CODES.contains(&code)
        && (2..=41).contains(&b.len())
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'.' || *c == b'-')
}

fn valid_host(h: &str) -> bool {
    let b = h.as_bytes();
    if b.is_empty()
        || b.len() > 253
        || h.starts_with('.')
        || h.ends_with('.')
        || h.starts_with('-')
        || !h.contains('.')
    {
        return false;
    }
    h.split('.').all(|label| {
        let lb = label.as_bytes();
        !lb.is_empty()
            && lb.len() <= 63
            && !label.starts_with('-')
            && !label.ends_with('-')
            && lb
                .iter()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
    })
}

impl SiteSpec {
    /// Validate everything an operator could type wrong.
    pub fn new(
        code: &str,
        ct: u32,
        ip_last: u8,
        hosts: &[&str],
        wp: bool,
        locale: &str,
        limits: Limits,
    ) -> Result<Self, Error> {
        if !valid_code(code) {
            return Err(Error::Code(code.to_owned()));
        }
        if !(100..=999_999_999).contains(&ct) || ct == TEMPLATE_CT || ct == EDGE_CT {
            return Err(Error::CtId(ct));
        }
        if !(3..=249).contains(&ip_last) {
            return Err(Error::Ip(alloc::format!("{NET}{ip_last}")));
        }
        if hosts.is_empty() {
            return Err(Error::Host(String::new()));
        }
        let mut hs: Vec<String> = Vec::new();
        for h in hosts {
            if !valid_host(h) || hs.iter().any(|x| x == h) {
                return Err(Error::Host((*h).to_owned()));
            }
            hs.push((*h).to_owned());
        }
        if !locale
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_')
            || locale.is_empty()
            || locale.len() > 16
        {
            return Err(Error::Code(alloc::format!("locale {locale}")));
        }
        limits.validate()?;
        Ok(SiteSpec {
            fw_group: FW_GROUP.to_owned(),
            ssh_port: None,
            code: code.to_owned(),
            ct,
            ip_last,
            hosts: hs,
            wp,
            locale: locale.to_owned(),
            limits,
        })
    }

    pub fn ip(&self) -> String {
        alloc::format!("{NET}{}", self.ip_last)
    }

    pub fn primary(&self) -> &str {
        &self.hosts[0]
    }

    /// `net0` exactly as `pct set` will write it (hwaddr is assigned by Proxmox and ignored in drift).
    pub fn net0(&self) -> String {
        alloc::format!(
            "name=eth0,bridge=vmbr1,ip={}/24,gw=10.77.0.1,firewall=1",
            self.ip()
        )
    }
}

/// The `pct config` keys a CT built from `spec` must show. Order is stable (sorted by key).
pub fn desired(spec: &SiteSpec) -> Vec<(&'static str, String)> {
    let l = &spec.limits;
    let mut v: Vec<(&'static str, String)> = alloc::vec![
        ("cores", alloc::format!("{}", l.cores)),
        ("cpulimit", l.cpulimit_str()),
        ("features", "nesting=1".to_owned()),
        ("hostname", spec.code.clone()),
        ("lxc.cgroup2.pids.max", alloc::format!("{}", l.pids_max)),
        ("memory", alloc::format!("{}", l.memory_mb)),
        ("net0", spec.net0()),
        ("onboot", "1".to_owned()),
        ("swap", alloc::format!("{}", l.swap_mb)),
        ("unprivileged", "1".to_owned()),
    ];
    v.sort_by(|a, b| a.0.cmp(b.0));
    v
}

/// The CT's own root volume: LVM-thin / dir storage names it `vm-<ct>-disk-N`, ZFS names it
/// `subvol-<ct>-disk-N` (c477a #585 r3 N). Anything else is another CT's disk.
pub fn rootfs_is_own(value: &str, ct: u32) -> bool {
    // `<storage>:<volume>[,option…]`. The volume is matched whole: a substring test would
    // accept `x:vm-900-disk-0,mp=/vm-112-disk-0` or a volume merely *named after* this CT
    // inside another one's path.
    let volume = value.split(',').next().unwrap_or("");
    let Some((storage, name)) = volume.split_once(':') else {
        return false;
    };
    if storage.is_empty() || name.contains(['/', ':']) {
        return false;
    }
    ["vm", "subvol"].iter().any(|kind| {
        name.strip_prefix(&alloc::format!("{kind}-{ct}-disk-"))
            .is_some_and(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_digit()))
    })
}

/// Whether `value` is the path [`crate::mask::HOOK_LINE`] names — the only hook a site CT
/// may carry. The line is one constant for installing, checking and restoring.
pub fn mask_hook_ok(value: &str) -> bool {
    crate::mask::HOOK_LINE.strip_prefix("lxc.hook.mount: ") == Some(value)
}

/// Keys a site CT's `pct config` may carry without being drift: what `desired()` declares,
/// the identity keys PVE writes itself, and `lxc.idmap` (per-CT uid range, DECISION
/// ct-idmap 11-10). Everything else — `lxc.hook.*`, `hookscript`, `lxc.cgroup2.devices.allow`,
/// `lxc.include`, `lxc.cap.*`, `lxc.mount.*`, `lxc.apparmor.*`, `lxc.prlimit.*`, `mp*`, `dev*`,
/// `template`, `protection`… — is drift.
pub fn config_key_allowed(key: &str) -> bool {
    const DESIRED: [&str; 10] = [
        "cores",
        "cpulimit",
        "features",
        "hostname",
        "lxc.cgroup2.pids.max",
        "memory",
        "net0",
        "onboot",
        "swap",
        "unprivileged",
    ];
    const HARMLESS: [&str; 9] = [
        "arch",
        "ostype",
        "rootfs",
        "nameserver",
        "searchdomain",
        "description",
        "tags",
        "cmode",
        "tty",
    ];
    DESIRED.contains(&key) || HARMLESS.contains(&key) || key == "lxc.idmap"
}

/// One `lxc.idmap` value that the per-CT range plan allows (DECISION ct-idmap, c477a #585 M1'):
/// `u 0 <base> 65536` or `g 0 <base> 65536` with `base = 100000 + 65536 × k`, k ≥ 1 — never
/// base 0 (that would make container root the host's root while `unprivileged: 1` still
/// reads fine). Returns the kind and base.
pub fn idmap_value(value: &str) -> Option<(char, u64)> {
    let mut it = value.split_whitespace();
    let kind = match it.next()? {
        "u" => 'u',
        "g" => 'g',
        _ => return None,
    };
    if it.next()? != "0" {
        return None;
    }
    let base: u64 = it.next()?.parse().ok()?;
    if it.next()? != "65536" || it.next().is_some() {
        return None;
    }
    if base < 100_000 + 65_536 || !(base - 100_000).is_multiple_of(65_536) || base > 4_000_000_000 {
        return None;
    }
    Some((kind, base))
}

/// The whole `lxc.idmap` set of a CT: absent is fine (default shared range, the 11-09 sites),
/// otherwise exactly one `u` and one `g` line with the same allowed base.
pub fn idmap_set_ok(values: &[&str]) -> bool {
    if values.is_empty() {
        return true;
    }
    if values.len() != 2 {
        return false;
    }
    match (idmap_value(values[0]), idmap_value(values[1])) {
        (Some((k1, b1)), Some((k2, b2))) => k1 != k2 && b1 == b2,
        _ => false,
    }
}

/// The per-CT firewall file: options, the two SSH-1 rules when the site has customer ssh
/// (private / tailnet sources still dropped on 22, public DNATed ssh accepted), then the
/// versioned group, nothing else. The shell writes exactly this; `drift` compares against it
/// ignoring comments and blank lines.
pub fn desired_firewall(group: &str, ssh_port: Option<u16>) -> String {
    let ssh = if ssh_port.is_some() {
        "IN DROP -source +private -p tcp -dport 22 -log nolog\nIN ACCEPT -p tcp -dport 22 -log nolog\n"
    } else {
        ""
    };
    alloc::format!("[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: DROP\nipfilter: 1\n\n[RULES]\n{ssh}GROUP {group}\n")
}

/// The host-side record (`/var/lib/ez2-cloud/sites/<code>.json`) the shell writes after a
/// successful create; `created` is filled in by the shell.
pub fn record(spec: &SiteSpec, created: &str) -> Value {
    json!({
        "site": spec.code, "ct": spec.ct, "ip": spec.ip(), "hosts": spec.hosts.join(","),
        "wp": u8::from(spec.wp), "locale": spec.locale, "created": created, "template": TEMPLATE_CT, "fw_group": spec.fw_group,
        "ssh_port": spec.ssh_port,
        "limits": {"cores": spec.limits.cores, "cpulimit_x100": spec.limits.cpulimit_x100, "memory_mb": spec.limits.memory_mb,
                   "swap_mb": spec.limits.swap_mb, "pids_max": spec.limits.pids_max}
    })
}

/// Rebuild a spec from a record (what `site drift` starts from). Records written by the
/// bash PoC have no `limits`: they get [`Limits::TEST_TIER`], which is what those CTs run.
pub fn spec_from_record(rec: &Value) -> Result<SiteSpec, Error> {
    let s = |k: &str| {
        rec.get(k)
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Observed(alloc::format!("record without {k}")))
    };
    let code = s("site")?;
    let ct = rec
        .get("ct")
        .and_then(Value::as_u64)
        .ok_or_else(|| Error::Observed("record without ct".to_owned()))?;
    let ct = u32::try_from(ct).map_err(|_| Error::CtId(u32::MAX))?;
    let ip = s("ip")?;
    let last = ip
        .strip_prefix(NET)
        .and_then(|t| t.parse::<u8>().ok())
        .ok_or_else(|| Error::Ip(ip.to_owned()))?;
    let hosts_s = s("hosts")?;
    let hosts: Vec<&str> = hosts_s.split(',').collect();
    let wp = rec.get("wp").and_then(Value::as_u64).unwrap_or(1) == 1;
    let locale = rec.get("locale").and_then(Value::as_str).unwrap_or("zh_TW");
    let limits = match rec.get("limits") {
        Some(l) => {
            let g = |k: &str, d: u32| {
                l.get(k)
                    .and_then(Value::as_u64)
                    .and_then(|x| u32::try_from(x).ok())
                    .unwrap_or(d)
            };
            let t = Limits::TEST_TIER;
            Limits {
                cores: g("cores", t.cores),
                cpulimit_x100: g("cpulimit_x100", t.cpulimit_x100),
                memory_mb: g("memory_mb", t.memory_mb),
                swap_mb: g("swap_mb", t.swap_mb),
                pids_max: g("pids_max", t.pids_max),
            }
        }
        None => Limits::TEST_TIER,
    };
    let mut spec = SiteSpec::new(code, ct, last, &hosts, wp, locale, limits)?;
    if let Some(g) = rec.get("fw_group").and_then(Value::as_str) {
        spec = spec.with_fw_group(g)?;
    }
    match rec.get("ssh_port") {
        None | Some(Value::Null) => {}
        Some(v) => {
            let port = v
                .as_u64()
                .and_then(|p| u16::try_from(p).ok())
                .ok_or_else(|| Error::Observed("record ssh_port is not a port".to_owned()))?;
            spec = spec.with_ssh_port(port)?;
        }
    }
    Ok(spec)
}

/// One way a host record falls short of being a complete statement of what its CT must
/// look like. [`spec_from_record`] is lenient on purpose (the 11-09 PoC records predate
/// some fields and `site drift` must still read them); a check that is going to report
/// "this site is as recorded" must not pass on values nobody recorded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordGap {
    /// Stable code: `R01` required field missing or of the wrong type, `R02` a field
    /// [`spec_from_record`] would fill with a default, `R03` a limit missing, `R04` a key
    /// this version does not know (a different spelling of a known one included).
    pub code: &'static str,
    /// The key, `limits.<name>` for a limit.
    pub key: String,
}

const RECORD_KEYS: &[&str] = &[
    "tenant_id",
    "disk_gb",
    "site",
    "ct",
    "ip",
    "hosts",
    "wp",
    "locale",
    "created",
    "ct_since",
    "template",
    "fw_group",
    "ssh_port",
    "limits",
];
const LIMIT_KEYS: &[&str] = &["cores", "cpulimit_x100", "memory_mb", "swap_mb", "pids_max"];

/// Everything that keeps `rec` from being a complete record: fields that are missing,
/// fields a lenient read would default, and keys nobody reads. Empty means
/// [`spec_from_record`] uses no default for this record. `ssh_port` may be absent or
/// `null` (a site without customer ssh).
pub fn record_gaps(rec: &Value) -> Vec<RecordGap> {
    let gap = |code: &'static str, key: &str| RecordGap {
        code,
        key: key.to_owned(),
    };
    let Some(obj) = rec.as_object() else {
        return alloc::vec![gap("R01", "")];
    };
    let mut out = Vec::new();
    for key in ["site", "ip", "hosts", "created"] {
        if !obj.get(key).is_some_and(Value::is_string) {
            out.push(gap("R01", key));
        }
    }
    // "is the lenient read going to use this value as it stands" — so the same
    // conversions it makes: a number that does not fit is defaulted there, a gap here
    let fits_u32 = |v: &Value| v.as_u64().is_some_and(|n| u32::try_from(n).is_ok());
    if !obj.get("ct").is_some_and(fits_u32) {
        out.push(gap("R01", "ct"));
    }
    if !obj
        .get("wp")
        .is_some_and(|v| matches!(v.as_u64(), Some(0 | 1)))
    {
        out.push(gap("R02", "wp"));
    }
    if obj
        .get("ssh_port")
        .is_some_and(|v| !v.is_null() && !v.as_u64().is_some_and(|n| u16::try_from(n).is_ok()))
    {
        out.push(gap("R01", "ssh_port"));
    }
    for key in ["locale", "fw_group"] {
        if !obj.get(key).is_some_and(Value::is_string) {
            out.push(gap("R02", key));
        }
    }
    match obj.get("limits").and_then(Value::as_object) {
        None => out.push(gap("R02", "limits")),
        Some(limits) => {
            for key in LIMIT_KEYS {
                if !limits.get(*key).is_some_and(fits_u32) {
                    out.push(gap("R03", &alloc::format!("limits.{key}")));
                }
            }
            for key in limits.keys() {
                if !LIMIT_KEYS.contains(&key.as_str()) {
                    out.push(gap("R04", &alloc::format!("limits.{key}")));
                }
            }
        }
    }
    // optional, but of the right kind when present
    if obj
        .get("tenant_id")
        .is_some_and(|v| !v.as_str().is_some_and(valid_code))
    {
        out.push(gap("R01", "tenant_id"));
    }
    if obj
        .get("disk_gb")
        .is_some_and(|v| !v.as_u64().is_some_and(|n| (1..=1024).contains(&n)))
    {
        out.push(gap("R01", "disk_gb"));
    }
    for key in obj.keys() {
        if !RECORD_KEYS.contains(&key.as_str()) {
            out.push(gap("R04", key));
        }
    }
    out
}

/// The customer a site belongs to: the record's `tenant_id` (Ez2.PAGE's name for it; the
/// two products are to share one account). A record that names none belongs to **no**
/// tenant: there is no falling back to the site's own code, or a tenant id that happened to
/// equal a site code would own that site. The id is an opaque code, never the customer's name.
pub fn record_tenant(rec: &Value) -> Option<&str> {
    rec.get("tenant_id")?.as_str().filter(|o| valid_code(o))
}

/// [`spec_from_record`] for callers that must not accept a defaulted value: any
/// [`RecordGap`] is an error.
pub fn spec_from_record_strict(rec: &Value) -> Result<SiteSpec, Error> {
    let gaps = record_gaps(rec);
    if let Some(first) = gaps.first() {
        return Err(Error::Observed(alloc::format!(
            "record incomplete: {} {} ({} gap(s) in all)",
            first.code,
            first.key,
            gaps.len()
        )));
    }
    spec_from_record(rec)
}

/// One step of `site create`, as an argument list (never a shell string).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub id: &'static str,
    pub argv: Vec<String>,
    /// What a re-run of the same step should do: skip (already there) or re-apply (same result).
    pub idempotent: &'static str,
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| (*s).to_owned()).collect()
}

/// The host-side `pct` steps up to (not including) the in-container setup. The shell also
/// writes [`desired_firewall`] to `/etc/pve/firewall/<ct>.fw` between `set` and `start`.
pub fn plan(spec: &SiteSpec) -> Vec<Step> {
    let ct = alloc::format!("{}", spec.ct);
    let l = &spec.limits;
    alloc::vec![
        Step { id: "clone", argv: argv(&["pct", "clone", "900", &ct, "--hostname", &spec.code, "--full", "1"]), idempotent: "skip if `pct status <ct>` succeeds" },
        Step {
            id: "set",
            argv: argv(&[
                "pct", "set", &ct, "--net0", &spec.net0(), "--onboot", "1", "--cores", &alloc::format!("{}", l.cores), "--memory", &alloc::format!("{}", l.memory_mb),
                "--swap", &alloc::format!("{}", l.swap_mb), "--cpulimit", &l.cpulimit_str(),
            ]),
            idempotent: "re-apply (same values)",
        },
        Step { id: "pids", argv: argv(&["pct", "set", &ct, "--lxc.cgroup2.pids.max", &alloc::format!("{}", l.pids_max)]), idempotent: "re-apply; pct set accepts raw lxc keys only via config edit on older PVE — the shell falls back to appending `lxc.cgroup2.pids.max: N` to /etc/pve/lxc/<ct>.conf" },
        Step { id: "start", argv: argv(&["pct", "start", &ct]), idempotent: "skip if running" },
    ]
}

/// One difference between desired and observed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Drift {
    pub ct: u32,
    pub key: String,
    pub want: String,
    pub got: String,
}

impl Drift {
    pub fn to_json(&self) -> Value {
        json!({"ct": self.ct, "key": self.key, "want": self.want, "got": self.got})
    }
}

/// Parse `pct config` output (`key: value` lines) into pairs; `description` is dropped.
pub fn parse_pct_config(text: &str) -> Result<Vec<(String, String)>, Error> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim_end();
        if line.is_empty() {
            continue;
        }
        let Some((k, v)) = line.split_once(':') else {
            return Err(Error::Observed(alloc::format!(
                "line without ':': {line:?}"
            )));
        };
        let k = k.trim();
        if k == "description" || k.is_empty() {
            continue;
        }
        out.push((k.to_owned(), v.trim().to_owned()));
    }
    if out.is_empty() {
        return Err(Error::Observed("empty pct config".to_owned()));
    }
    Ok(out)
}

/// `net0` as a set of `k=v` pairs minus `hwaddr` / `type` (assigned by Proxmox), sorted,
/// so field order and the MAC never count as drift.
fn net0_canonical(v: &str) -> String {
    let mut parts: Vec<&str> = v
        .split(',')
        .filter(|p| !p.starts_with("hwaddr=") && !p.starts_with("type="))
        .collect();
    parts.sort_unstable();
    parts.join(",")
}

/// Compare desired state with what the host reports. `observed_fw` is the content of
/// `/etc/pve/firewall/<ct>.fw` (None = file missing). Every difference is one entry; an
/// empty vector means the CT is exactly as declared.
pub fn drift(
    spec: &SiteSpec,
    observed_pct_config: &str,
    observed_fw: Option<&str>,
) -> Result<Vec<Drift>, Error> {
    let obs = parse_pct_config(observed_pct_config)?;
    let get = |k: &str| obs.iter().find(|(ok, _)| ok == k).map(|(_, v)| v.as_str());
    let mut out = Vec::new();
    for (k, want) in desired(spec) {
        let got = get(k).unwrap_or("<missing>");
        let same = if k == "net0" {
            net0_canonical(got) == net0_canonical(&want)
        } else {
            got == want
        };
        if !same {
            out.push(Drift {
                ct: spec.ct,
                key: k.to_owned(),
                want,
                got: got.to_owned(),
            });
        }
    }
    // Everything else in the config must be on the allow-list (c477a #585 M1: a deny-list
    // missed `lxc.hook.*`, which runs as host root at CT start). Anything not declared by
    // `desired()` and not a known harmless PVE key is drift, named by its key.
    // lxc.idmap: the key is allowed, the value must be the per-CT range shape (M1')
    let idmaps: Vec<&str> = obs
        .iter()
        .filter(|(k, _)| k == "lxc.idmap")
        .map(|(_, v)| v.as_str())
        .collect();
    if !idmap_set_ok(&idmaps) {
        out.push(Drift {
            ct: spec.ct,
            key: "lxc.idmap".to_owned(),
            want: "<absent> or exactly `u 0 <base> 65536` + `g 0 <base> 65536` with base = 100000 + 65536k, k ≥ 1 (never base 0)".to_owned(),
            got: idmaps.join(" | "),
        });
    }
    let mask_hooks = obs.iter().filter(|(k, _)| k == "lxc.hook.mount").count();
    for (k, v) in &obs {
        // rootfs must be this CT's own volume (c477a #585 L3: another CT's volume = two sites on one disk)
        if k == "rootfs" && !rootfs_is_own(v, spec.ct) {
            out.push(Drift {
                ct: spec.ct,
                key: "rootfs".to_owned(),
                want: alloc::format!(
                    "<a volume named vm-{ct}-disk-* or subvol-{ct}-disk-*>",
                    ct = spec.ct
                ),
                got: v.clone(),
            });
            continue;
        }
        // the mask's own hook and no other: this key with exactly the wrapper's path, once
        // (33e28). Any other hook, another path, or a second line stays drift below.
        if k == "lxc.hook.mount" && mask_hook_ok(v) && mask_hooks == 1 {
            continue;
        }
        if !config_key_allowed(k) {
            let why = if k.starts_with("mp") || k.starts_with("dev") {
                "<absent> (no bind mounts / devices on a site CT)"
            } else if k.starts_with("lxc.") || k == "hookscript" {
                "<absent> (raw lxc.* / hookscript run as host root; only lxc.cgroup2.pids.max and lxc.idmap are declared)"
            } else {
                "<absent> (not in the site CT allow-list)"
            };
            out.push(Drift {
                ct: spec.ct,
                key: k.clone(),
                want: why.to_owned(),
                got: v.clone(),
            });
        }
    }
    // firewall file
    let want_fw = desired_firewall(&spec.fw_group, spec.ssh_port);
    match observed_fw {
        None => out.push(Drift {
            ct: spec.ct,
            key: "firewall".to_owned(),
            want: want_fw,
            got: "<missing file>".to_owned(),
        }),
        Some(fw) => {
            let norm = |t: &str| {
                t.lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty() && !l.starts_with('#'))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            if norm(fw) != norm(&want_fw) {
                out.push(Drift {
                    ct: spec.ct,
                    key: "firewall".to_owned(),
                    want: want_fw,
                    got: fw.to_owned(),
                });
            }
        }
    }
    Ok(out)
}

/// The keys the limits step of `site create` sets (`pct set` and the pids line).
pub const LIMITS_STEP_KEYS: [&str; 7] = [
    "cores",
    "cpulimit",
    "lxc.cgroup2.pids.max",
    "memory",
    "net0",
    "onboot",
    "swap",
];

/// What the limits step must have left: every key of [`LIMITS_STEP_KEYS`] as [`desired`] has
/// it (`net0` without the MAC and type, like [`drift`]). Only those keys: the rest of a
/// fresh clone is the template's and is checked by [`drift`] once the site is open.
pub fn limits_differ(spec: &SiteSpec, observed_pct_config: &str) -> Result<Vec<Drift>, Error> {
    let obs = parse_pct_config(observed_pct_config)?;
    let mut out = Vec::new();
    for (k, want) in desired(spec) {
        if !LIMITS_STEP_KEYS.contains(&k) {
            continue;
        }
        let got = obs
            .iter()
            .find(|(ok, _)| ok == k)
            .map_or("<missing>", |(_, v)| v.as_str());
        let same = if k == "net0" {
            net0_canonical(got) == net0_canonical(&want)
        } else {
            got == want
        };
        if !same {
            out.push(Drift {
                ct: spec.ct,
                key: k.to_owned(),
                want,
                got: got.to_owned(),
            });
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------- Edge block, suspend / resume, blue-green restore (P2)
// The Edge Caddyfile keeps one block per site, tagged `# site:<code>` on the line before the
// host line and closed by a `}` at column 0. These functions render the three shapes a block
// can take and swap one for another in the file text; the host shell pushes the result,
// validates it with caddy and reloads. Pure text here, so the exact bytes are tested.

/// Marker line that precedes a site's block in the Caddyfile.
pub fn caddy_marker(code: &str) -> String {
    alloc::format!("# site:{code}")
}

/// The normal block: proxy to the site CT at `upstream_ip` (the spec's own IP, or the new CT's
/// during a blue-green restore). Carries the white-label import (WL-1) and, on its own,
/// strips what names the server (E6 C1): `Server` / `Via` / `X-Powered-By` from every
/// response, and the CT's `Server` / `X-Powered-By` before they are copied out, so a block
/// does not depend on the hand-kept snippet on the Edge to stay quiet.
pub fn caddy_block_upstream(spec: &SiteSpec, upstream_ip: &str) -> String {
    alloc::format!(
        "{marker}\n{hosts} {{\n\timport ez2_whitelabel\n\theader {{\n\t\t-Server\n\t\t-Via\n\t\t-X-Powered-By\n\t}}\n\tencode zstd gzip\n\treverse_proxy {ip}:80 {{\n\t\theader_up X-Real-IP {{remote_host}}\n\t\theader_down -Server\n\t\theader_down -X-Powered-By\n\t}}\n}}\n",
        marker = caddy_marker(&spec.code),
        hosts = spec.hosts.join(" "),
        ip = upstream_ip
    )
}

/// The suspended block: every request gets the suspension page (HTTP 503, `Retry-After`,
/// no caching), nothing reaches the CT. The page file lives on the Edge; its wording is the
/// customer-facing text Andy decides (§14 P2), this only wires it.
pub fn caddy_block_suspended(spec: &SiteSpec, page_path: &str) -> String {
    alloc::format!(
        "{marker}\n{hosts} {{\n\timport ez2_whitelabel\n\t# ez2:suspended\n\theader {{\n\t\t-Server\n\t\t-Via\n\t\t-X-Powered-By\n\t}}\n\theader Cache-Control \"no-store\"\n\theader Retry-After \"3600\"\n\troot * {dir}\n\trewrite * /{file}\n\tfile_server {{\n\t\tstatus 503\n\t}}\n}}\n",
        marker = caddy_marker(&spec.code),
        hosts = spec.hosts.join(" "),
        dir = page_path.rsplit_once('/').map_or("/srv/errors", |(d, _)| d),
        file = page_path.rsplit_once('/').map_or(page_path, |(_, f)| f)
    )
}

/// Replace the site's block in a Caddyfile with `new_block`. The block runs from the marker
/// line to the first line that is exactly `}` after it. Errors if the marker is missing or
/// appears twice, or the block never closes.
pub fn caddy_swap_block(caddyfile: &str, code: &str, new_block: &str) -> Result<String, Error> {
    let marker = caddy_marker(code);
    let lines: Vec<&str> = caddyfile.lines().collect();
    let starts: Vec<usize> = lines
        .iter()
        .enumerate()
        .filter(|(_, l)| l.trim_end() == marker)
        .map(|(i, _)| i)
        .collect();
    let start = match starts.as_slice() {
        [i] => *i,
        [] => {
            return Err(Error::Observed(alloc::format!(
                "Caddyfile has no block for {code}"
            )))
        }
        _ => {
            return Err(Error::Observed(alloc::format!(
                "Caddyfile has {} blocks for {code}",
                starts.len()
            )))
        }
    };
    let end = lines[start..]
        .iter()
        .position(|l| *l == "}")
        .map(|o| start + o)
        .ok_or_else(|| Error::Observed(alloc::format!("block for {code} never closes")))?;
    let mut out = String::new();
    for l in &lines[..start] {
        out.push_str(l);
        out.push('\n');
    }
    out.push_str(new_block.trim_end_matches('\n'));
    out.push('\n');
    for l in &lines[end + 1..] {
        out.push_str(l);
        out.push('\n');
    }
    Ok(out)
}

/// Is the site's block currently the suspended shape?
pub fn caddy_block_is_suspended(caddyfile: &str, code: &str) -> Result<bool, Error> {
    let marker = caddy_marker(code);
    let lines: Vec<&str> = caddyfile.lines().collect();
    let start = lines
        .iter()
        .position(|l| l.trim_end() == marker)
        .ok_or_else(|| Error::Observed(alloc::format!("Caddyfile has no block for {code}")))?;
    let end = lines[start..]
        .iter()
        .position(|l| *l == "}")
        .map_or(lines.len(), |o| start + o);
    let body: Vec<&str> = lines[start..end].iter().map(|l| l.trim()).collect();
    // c477a #585 L5: a stray "# suspended" comment in a live block must not count; the
    // suspended shape is our own marker *and* the 503 file_server pair.
    let marked = body.contains(&"# ez2:suspended");
    let serves_503 =
        body.iter().any(|l| l.starts_with("file_server")) && body.contains(&"status 503");
    Ok(marked && serves_503)
}

/// Blue-green restore: bring the backup up as a **new** CT with a **new** internal IP next to
/// the running one, then point the Edge at it; the old CT stays until someone deletes it
/// (that delete is L2). Validated: new id / ip differ from the live ones and from the
/// template / edge; backup path is a vzdump archive name.
/// `taken_ip_last`: every last octet already used on vmbr1 — the other sites' records and
/// what `pct config` shows for every CT (c477a #585 M2: a mistyped `--ip` would put two CTs
/// on one address and take another customer's site down by ARP; pct does not refuse it).
pub fn restore_plan(
    spec: &SiteSpec,
    backup_path: &str,
    new_ct: u32,
    new_ip_last: u8,
    taken_ip_last: &[u8],
) -> Result<Vec<Step>, Error> {
    if new_ct == spec.ct
        || !(100..=999_999_999).contains(&new_ct)
        || new_ct == TEMPLATE_CT
        || new_ct == EDGE_CT
    {
        return Err(Error::CtId(new_ct));
    }
    if new_ip_last == spec.ip_last
        || !(3..=249).contains(&new_ip_last)
        || taken_ip_last.contains(&new_ip_last)
    {
        return Err(Error::Ip(alloc::format!("{NET}{new_ip_last}")));
    }
    let name = backup_path.rsplit('/').next().unwrap_or("");
    if !backup_path.starts_with('/')
        || !name.starts_with("vzdump-lxc-")
        || !(name.ends_with(".tar.zst") || name.ends_with(".tar.gz") || name.ends_with(".tar"))
        || backup_path.contains("..")
    {
        return Err(Error::Observed(alloc::format!(
            "backup path refused: {backup_path}"
        )));
    }
    let l = &spec.limits;
    let ct = alloc::format!("{new_ct}");
    let net0 =
        alloc::format!("name=eth0,bridge=vmbr1,ip={NET}{new_ip_last}/24,gw=10.77.0.1,firewall=1");
    Ok(alloc::vec![
        Step {
            id: "restore",
            argv: argv(&[
                "pct",
                "restore",
                &ct,
                backup_path,
                "--storage",
                "local-lvm",
                "--unprivileged",
                "1",
                "--hostname",
                &spec.code
            ]),
            idempotent: "refuse if <ct> exists"
        },
        Step {
            id: "set",
            argv: argv(&[
                "pct",
                "set",
                &ct,
                "--net0",
                &net0,
                "--onboot",
                "1",
                "--cores",
                &alloc::format!("{}", l.cores),
                "--memory",
                &alloc::format!("{}", l.memory_mb),
                "--swap",
                &alloc::format!("{}", l.swap_mb),
                "--cpulimit",
                &l.cpulimit_str(),
            ]),
            idempotent: "re-apply",
        },
        Step {
            id: "start",
            argv: argv(&["pct", "start", &ct]),
            idempotent: "skip if running"
        },
        Step {
            id: "verify",
            argv: argv(&[
                "pct",
                "exec",
                &ct,
                "--",
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-m",
                "8",
                "-w",
                "%{http_code}",
                "-H",
                &alloc::format!("Host: {}", spec.primary()),
                "http://127.0.0.1/"
            ]),
            idempotent: "read-only; want 200"
        },
    ])
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::string::ToString;

    fn site2() -> SiteSpec {
        SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.net"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
    }

    #[test]
    fn the_limits_step_is_checked_on_its_own_keys() {
        let spec = site2();
        let mut wanted = String::new();
        for (k, v) in desired(&spec) {
            wanted.push_str(&alloc::format!("{k}: {v}\n"));
        }
        assert_eq!(limits_differ(&spec, &wanted).unwrap(), Vec::new());
        // the template's own keys are not this step's
        let other = wanted.replace("hostname: site2", "hostname: template");
        assert_eq!(limits_differ(&spec, &other).unwrap(), Vec::new());
        let low = wanted.replace("memory: ", "memory: 1");
        let d = limits_differ(&spec, &low).unwrap();
        assert_eq!(d.len(), 1);
        assert_eq!(d[0].key, "memory");
        let no_pids: String = wanted
            .lines()
            .filter(|l| !l.starts_with("lxc.cgroup2.pids.max"))
            .map(|l| alloc::format!("{l}\n"))
            .collect();
        let d = limits_differ(&spec, &no_pids).unwrap();
        assert_eq!(d[0].key, "lxc.cgroup2.pids.max");
        assert_eq!(d[0].got, "<missing>");
        assert!(limits_differ(&spec, "").is_err());
    }

    const CT112: &str = "arch: amd64\ncores: 2\ncpulimit: 2\nfeatures: nesting=1\nhostname: site2\nlxc.cgroup2.pids.max: 2048\nmemory: 2048\nnameserver: 192.0.2.53\nnet0: name=eth0,bridge=vmbr1,firewall=1,gw=10.77.0.1,hwaddr=BC:24:11:AA:BB:CC,ip=10.77.0.12/24,type=veth\nonboot: 1\nostype: debian\nrootfs: local-lvm:vm-112-disk-0,size=16G\nswap: 512\nunprivileged: 1\n";

    #[test]
    fn s1_spec_refuses_each_bad_input() {
        let ok = Limits::TEST_TIER;
        assert!(
            SiteSpec::new("Site2", 112, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "uppercase code"
        );
        assert!(
            SiteSpec::new("s", 112, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "1-char code"
        );
        assert!(
            SiteSpec::new("site2;rm", 112, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "shell in code"
        );
        assert!(
            SiteSpec::new("site2", 99, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "ct < 100"
        );
        assert!(
            SiteSpec::new("site2", 900, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "template id"
        );
        assert!(
            SiteSpec::new("site2", 101, 12, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "edge id"
        );
        assert!(
            SiteSpec::new("site2", 112, 1, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "host ip"
        );
        assert!(
            SiteSpec::new("site2", 112, 2, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "edge ip"
        );
        assert!(
            SiteSpec::new("site2", 112, 250, &["a.b"], true, "zh_TW", ok.clone()).is_err(),
            "template ip"
        );
        assert!(
            SiteSpec::new("site2", 112, 12, &[], true, "zh_TW", ok.clone()).is_err(),
            "no host"
        );
        assert!(
            SiteSpec::new(
                "site2",
                112,
                12,
                &["Example.COM"],
                true,
                "zh_TW",
                ok.clone()
            )
            .is_err(),
            "uppercase host"
        );
        assert!(
            SiteSpec::new("site2", 112, 12, &["localhost"], true, "zh_TW", ok.clone()).is_err(),
            "no dot"
        );
        assert!(
            SiteSpec::new("site2", 112, 12, &["a.b", "a.b"], true, "zh_TW", ok.clone()).is_err(),
            "dup host"
        );
        assert!(
            SiteSpec::new("site2", 112, 12, &["a.b"], true, "zh TW", ok).is_err(),
            "locale"
        );
        assert!(site2().hosts == ["site2.example.net"]);
        assert_eq!(site2().ip(), "10.77.0.12");
    }

    #[test]
    fn s2_limits_bands() {
        let t = Limits::TEST_TIER;
        assert!(t.validate().is_ok());
        assert!(Limits {
            cores: 0,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            cores: 9,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            cpulimit_x100: 50,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(
            Limits {
                cpulimit_x100: 300,
                ..t.clone()
            }
            .validate()
            .is_err(),
            "cpulimit above cores"
        );
        assert!(Limits {
            memory_mb: 256,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            memory_mb: 32_768,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            swap_mb: 8192,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            pids_max: 100,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert!(Limits {
            pids_max: 65_536,
            ..t.clone()
        }
        .validate()
        .is_err());
        assert_eq!(
            Limits {
                cpulimit_x100: 200,
                ..t.clone()
            }
            .cpulimit_str(),
            "2"
        );
        assert_eq!(
            Limits {
                cpulimit_x100: 150,
                ..t.clone()
            }
            .cpulimit_str(),
            "1.5"
        );
        assert_eq!(
            Limits {
                cores: 2,
                cpulimit_x100: 125,
                ..t.clone()
            }
            .cpulimit_str(),
            "1.25"
        );
        assert_eq!(
            Limits {
                cores: 1,
                cpulimit_x100: 100,
                ..t
            }
            .cpulimit_str(),
            "1"
        );
    }

    #[test]
    fn s3_plan_is_argv_and_desired_matches_plan() {
        let p = plan(&site2());
        assert_eq!(p[0].argv[..4], ["pct", "clone", "900", "112"]);
        let set = &p[1].argv;
        assert!(set.contains(&"--cpulimit".to_string()) && set.contains(&"2".to_string()));
        assert!(set
            .iter()
            .any(|a| a == "name=eth0,bridge=vmbr1,ip=10.77.0.12/24,gw=10.77.0.1,firewall=1"));
        for s in &p {
            for a in &s.argv {
                assert!(
                    !a.contains(';') && !a.contains('|') && !a.contains('&'),
                    "{a}"
                );
            }
        }
        let d = desired(&site2());
        assert!(d.iter().any(|(k, v)| *k == "memory" && v == "2048"));
        assert!(d
            .iter()
            .any(|(k, v)| *k == "lxc.cgroup2.pids.max" && v == "2048"));
        assert!(d.windows(2).all(|w| w[0].0 < w[1].0), "sorted");
        assert!(desired_firewall(FW_GROUP, None).contains("GROUP ez2-site-v1"));
    }

    #[test]
    fn s4_record_round_trip_and_poc_record_gets_test_tier() {
        let rec = record(&site2(), "2026-11-09T03:33:11Z");
        assert_eq!(spec_from_record(&rec).unwrap(), site2());
        let poc = json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"site2.example.net","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v1"});
        assert_eq!(spec_from_record(&poc).unwrap(), site2());
        assert!(spec_from_record(&json!({"site":"site2"})).is_err());
        assert!(spec_from_record(
            &json!({"site":"site2","ct":112,"ip":"192.168.1.5","hosts":"a.b"})
        )
        .is_err());
    }

    #[test]
    fn s5_no_drift_when_identical_modulo_mac_and_order() {
        let fw = desired_firewall(FW_GROUP, None);
        assert_eq!(
            drift(&site2(), CT112, Some(&fw)).unwrap(),
            Vec::<Drift>::new()
        );
        // net0 field order and MAC differ: still no drift
        let reordered = CT112.replace("net0: name=eth0,bridge=vmbr1,firewall=1,gw=10.77.0.1,hwaddr=BC:24:11:AA:BB:CC,ip=10.77.0.12/24,type=veth", "net0: ip=10.77.0.12/24,name=eth0,hwaddr=00:11:22:33:44:55,gw=10.77.0.1,bridge=vmbr1,type=veth,firewall=1");
        assert_eq!(
            drift(&site2(), &reordered, Some(&fw)).unwrap(),
            Vec::<Drift>::new()
        );
        // comments / blank lines in the firewall file do not count
        let fw_c = alloc::format!("# managed\n{fw}\n\n");
        assert_eq!(
            drift(&site2(), CT112, Some(&fw_c)).unwrap(),
            Vec::<Drift>::new()
        );
    }

    #[test]
    fn s6_each_drift_is_named() {
        let fw = desired_firewall(FW_GROUP, None);
        let keys = |d: Vec<Drift>| d.into_iter().map(|x| x.key).collect::<Vec<_>>();
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("memory: 2048", "memory: 4096"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["memory"]
        );
        assert_eq!(
            keys(drift(&site2(), &CT112.replace("cpulimit: 2\n", ""), Some(&fw)).unwrap()),
            ["cpulimit"]
        );
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("lxc.cgroup2.pids.max: 2048\n", ""),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["lxc.cgroup2.pids.max"]
        );
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("firewall=1", "firewall=0"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["net0"]
        );
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("ip=10.77.0.12/24", "ip=10.77.0.13/24"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["net0"]
        );
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("unprivileged: 1", "unprivileged: 0"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["unprivileged"]
        );
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &alloc::format!("{CT112}mp0: /srv,mp=/mnt\n"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["mp0"]
        );
        // c477a #585 M1: allow-list, not deny-list — the dangerous raw keys are drift
        for extra in [
            "lxc.hook.pre-start: /x\n",
            "hookscript: local:snippets/x.sh\n",
            "lxc.cgroup2.devices.allow: a\n",
            "lxc.include: /etc/x.conf\n",
            "lxc.cap.keep: sys_admin\n",
            "lxc.prlimit.nofile: 65536\n",
            "protection: 1\n",
            "dev0: /dev/sda\n",
        ] {
            let d = keys(drift(&site2(), &alloc::format!("{CT112}{extra}"), Some(&fw)).unwrap());
            assert_eq!(d, [extra.split(':').next().unwrap()], "{extra}");
        }
        // the mask's hook is the one hook that is not drift: that key, that path, once (33e28)
        let hook = crate::mask::HOOK_LINE;
        assert_eq!(
            drift(&site2(), &alloc::format!("{CT112}{hook}\n"), Some(&fw)).unwrap(),
            Vec::<Drift>::new()
        );
        for other in [
            alloc::format!("{hook}x\n"),
            alloc::format!("{hook} --more\n"),
            alloc::format!("{hook}\n{hook}\n"),
            alloc::format!("{hook}\nlxc.hook.mount: /x\n"),
            hook.replace("lxc.hook.mount", "lxc.hook.pre-start") + "\n",
            hook.replace("lxc.hook.mount", "lxc.hook.start-host") + "\n",
        ] {
            let d = keys(drift(&site2(), &alloc::format!("{CT112}{other}"), Some(&fw)).unwrap());
            assert!(
                !d.is_empty() && d.iter().all(|k| k.starts_with("lxc.hook.")),
                "{other}"
            );
        }
        // …while PVE's own identity keys and the declared idmap are not
        let benign = alloc::format!(
            "{CT112}description: customer site\nsearchdomain: ez2.site\ntags: ez2\nlxc.idmap: u 0 165536 65536\nlxc.idmap: g 0 165536 65536\n"
        );
        assert_eq!(
            drift(&site2(), &benign, Some(&fw)).unwrap(),
            Vec::<Drift>::new()
        );
        // c477a #585 M1': lxc.idmap value is checked — base 0 is root-as-root, one line alone or
        // mismatched bases are not a range plan either
        for bad in [
            "lxc.idmap: u 0 0 65536\nlxc.idmap: g 0 0 65536\n",
            "lxc.idmap: u 0 165536 65536\n",
            "lxc.idmap: u 0 165536 65536\nlxc.idmap: g 0 231072 65536\n",
            "lxc.idmap: u 0 100000 65536\nlxc.idmap: g 0 100000 65536\n",
            "lxc.idmap: u 0 165537 65536\nlxc.idmap: g 0 165537 65536\n",
            "lxc.idmap: u 0 165536 65535\nlxc.idmap: g 0 165536 65535\n",
            "lxc.idmap: u 1000 165536 1\nlxc.idmap: g 0 165536 65536\n",
        ] {
            assert_eq!(
                keys(drift(&site2(), &alloc::format!("{CT112}{bad}"), Some(&fw)).unwrap()),
                ["lxc.idmap"],
                "{bad}"
            );
        }
        assert_eq!(idmap_value("u 0 165536 65536"), Some(('u', 165_536)));
        assert_eq!(idmap_value("g 0 231072 65536"), Some(('g', 231_072)));
        assert!(idmap_value("u 0 0 65536").is_none() && idmap_value("x 0 165536 65536").is_none());
        assert!(idmap_set_ok(&[]) && !idmap_set_ok(&["u 0 165536 65536"]));
        // L3: rootfs pointing at another CT's volume is drift
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    &CT112.replace("vm-112-disk-0", "vm-110-disk-0"),
                    Some(&fw)
                )
                .unwrap()
            ),
            ["rootfs"]
        );
        // ZFS names the same volume subvol-<ct>-disk-N (c477a #585 r3 N)
        assert_eq!(
            drift(
                &site2(),
                &CT112.replace("local-lvm:vm-112-disk-0", "tank:subvol-112-disk-0"),
                Some(&fw)
            )
            .unwrap(),
            Vec::<Drift>::new()
        );
        assert!(
            rootfs_is_own("tank:subvol-112-disk-0,size=16G", 112)
                && !rootfs_is_own("tank:subvol-110-disk-0", 112)
                && rootfs_is_own("local-lvm:vm-112-disk-1", 112)
                && !rootfs_is_own("local-lvm:vm-900-disk-0,mp=/vm-112-disk-0", 112)
                && !rootfs_is_own("local-lvm:vm-112-disk-0x", 112)
                && !rootfs_is_own("local-lvm:vm-112-disk-", 112)
                && !rootfs_is_own("dir:900/vm-112-disk-0", 112)
                && !rootfs_is_own("vm-112-disk-0", 112)
                && !rootfs_is_own("local-lvm:vm-1120-disk-0", 112)
        );
        assert!(config_key_allowed("lxc.cgroup2.pids.max") && config_key_allowed("lxc.idmap"));
        assert!(
            !config_key_allowed("lxc.hook.pre-start")
                && !config_key_allowed("hookscript")
                && !config_key_allowed("template")
        );
        assert_eq!(keys(drift(&site2(), CT112, None).unwrap()), ["firewall"]);
        assert_eq!(
            keys(
                drift(
                    &site2(),
                    CT112,
                    Some(&fw.replace("GROUP ez2-site-v1", "GROUP ez2-site-v2"))
                )
                .unwrap()
            ),
            ["firewall"]
        );
        assert_eq!(
            keys(drift(&site2(), CT112, Some(&fw.replace("policy_out: DROP\n", ""))).unwrap()),
            ["firewall"]
        );
        let d = drift(
            &site2(),
            &CT112.replace("memory: 2048", "memory: 4096"),
            Some(&fw),
        )
        .unwrap();
        assert_eq!(
            (d[0].ct, d[0].want.as_str(), d[0].got.as_str()),
            (112, "2048", "4096")
        );
        assert!(
            drift(&site2(), "", Some(&fw)).is_err(),
            "empty config is unreadable, not clean"
        );
        assert!(drift(&site2(), "garbage", Some(&fw)).is_err());
    }

    /// E6 C1: what a visitor gets back must not name the server. Each shape strips
    /// `Server` / `Via` / `X-Powered-By` itself (the whitelabel snippet is hand-kept on the
    /// Edge), and the live block also drops the CT's own before Caddy copies them out.
    #[test]
    fn a_site_block_strips_server_headers() {
        const STRIP: &str = "\theader {\n\t\t-Server\n\t\t-Via\n\t\t-X-Powered-By\n\t}\n";
        let sp = site2();
        let up = caddy_block_upstream(&sp, &sp.ip());
        assert_eq!(up.matches(STRIP).count(), 1, "{up}");
        let proxy = up
            .split_once("\treverse_proxy ")
            .map(|(_, rest)| rest)
            .unwrap_or_default();
        assert!(
            proxy.contains("\t\theader_down -Server\n")
                && proxy.contains("\t\theader_down -X-Powered-By\n"),
            "{up}"
        );
        let sus = caddy_block_suspended(&sp, "/srv/errors/suspended.html");
        assert_eq!(sus.matches(STRIP).count(), 1, "{sus}");
        // still one block each: the nested braces do not close it early
        for block in [&up, &sus] {
            let file = alloc::format!("{block}\n# site:other\nother.example {{\n}}\n");
            assert_eq!(
                crate::quarantine::caddy_block_text(&file, "site2").unwrap(),
                block.as_str()
            );
        }
        assert!(
            caddy_block_is_suspended(&caddy_swap_block(&up, "site2", &sus).unwrap(), "site2")
                .unwrap()
        );
    }

    #[test]
    fn s7_caddy_blocks_and_swap() {
        let sp = site2();
        let up = caddy_block_upstream(&sp, &sp.ip());
        assert!(
            up.starts_with("# site:site2\nsite2.example.net {\n\timport ez2_whitelabel\n"),
            "{up}"
        );
        assert!(up.contains("reverse_proxy 10.77.0.12:80 {") && up.ends_with("}\n"));
        let sus = caddy_block_suspended(&sp, "/srv/errors/suspended.html");
        assert!(
            sus.contains("# ez2:suspended")
                && sus.contains("root * /srv/errors")
                && sus.contains("rewrite * /suspended.html")
                && sus.contains("status 503")
        );
        assert!(
            !sus.contains("reverse_proxy"),
            "a suspended site must not reach the CT"
        );
        let file = alloc::format!("# global\n{{\n\temail x@y\n}}\n\n{}\n# site:other\nother.example {{\n\treverse_proxy 10.77.0.20:80\n}}\n", up);
        // swap to suspended and back
        let swapped = caddy_swap_block(&file, "site2", &sus).unwrap();
        assert!(caddy_block_is_suspended(&swapped, "site2").unwrap());
        assert!(!caddy_block_is_suspended(&swapped, "other").unwrap());
        // c477a #585 L5: a live block with a stray "# suspended" comment is still live
        let stray = caddy_swap_block(
            &file,
            "site2",
            &up.replace(
                "\timport ez2_whitelabel\n",
                "\timport ez2_whitelabel\n\t# suspended\n\t# ez2:suspended\n",
            ),
        )
        .unwrap();
        assert!(
            !caddy_block_is_suspended(&stray, "site2").unwrap(),
            "marker without the 503 file_server is not suspended"
        );
        assert!(
            swapped.contains("# site:other\nother.example {"),
            "other block untouched"
        );
        assert_eq!(swapped.matches("# site:site2").count(), 1);
        let back = caddy_swap_block(&swapped, "site2", &up).unwrap();
        assert_eq!(back, file);
        // blue-green: same hosts, new upstream
        let green =
            caddy_swap_block(&file, "site2", &caddy_block_upstream(&sp, "10.77.0.22")).unwrap();
        assert!(green.contains("reverse_proxy 10.77.0.22:80") && !green.contains("10.77.0.12"));
        // errors
        assert!(caddy_swap_block(&file, "nope", &up).is_err());
        assert!(
            caddy_swap_block(&alloc::format!("{file}{up}"), "site2", &up).is_err(),
            "two markers"
        );
        assert!(caddy_swap_block("# site:site2\nx {\n\tnever closes\n", "site2", &up).is_err());
    }

    #[test]
    fn s8_restore_plan_is_blue_green_and_validated() {
        let sp = site2();
        let p = restore_plan(
            &sp,
            "/var/lib/vz/dump/vzdump-lxc-112-2026_11_09-03_00_01.tar.zst",
            212,
            22,
            &[10, 13],
        )
        .unwrap();
        assert_eq!(p[0].argv[..3], ["pct", "restore", "212"]);
        assert!(
            p[0].argv.contains(&"--unprivileged".to_string())
                && p[0].argv.contains(&"site2".to_string())
        );
        assert!(p[1]
            .argv
            .iter()
            .any(|a| a == "name=eth0,bridge=vmbr1,ip=10.77.0.22/24,gw=10.77.0.1,firewall=1"));
        assert!(p[1].argv.contains(&"--cpulimit".to_string()));
        assert_eq!(p[2].argv, ["pct", "start", "212"]);
        assert!(p[3].argv.iter().any(|a| a == "Host: site2.example.net"));
        for st in &p {
            for a in &st.argv {
                assert!(!a.contains(';') && !a.contains('|'), "{a}");
            }
        }
        let b = "/var/lib/vz/dump/vzdump-lxc-112-x.tar.zst";
        assert!(restore_plan(&sp, b, 112, 22, &[]).is_err(), "same ct");
        assert!(restore_plan(&sp, b, 212, 12, &[]).is_err(), "same ip");
        assert!(restore_plan(&sp, b, 900, 22, &[]).is_err(), "template id");
        assert!(restore_plan(&sp, b, 212, 2, &[]).is_err(), "edge ip");
        // c477a #585 M2: an address another CT already has on vmbr1 is refused
        assert!(
            restore_plan(&sp, b, 212, 10, &[10, 13]).is_err(),
            "site1's ip"
        );
        assert!(
            restore_plan(&sp, b, 212, 13, &[10, 13]).is_err(),
            "site3's ip"
        );
        assert!(restore_plan(&sp, b, 212, 22, &[10, 13]).is_ok());
        assert!(
            restore_plan(&sp, "vzdump-lxc-112.tar.zst", 212, 22, &[]).is_err(),
            "relative path"
        );
        assert!(
            restore_plan(&sp, "/var/lib/vz/dump/../../etc/passwd", 212, 22, &[]).is_err(),
            "traversal"
        );
        assert!(
            restore_plan(&sp, "/var/lib/vz/dump/other.tar.zst", 212, 22, &[]).is_err(),
            "not a vzdump name"
        );
    }

    #[test]
    fn s9_fw_group_and_customer_ssh_are_part_of_the_desired_firewall() {
        // NET-1 (11-10): a site switched to ez2-site-v2 is not drift when its record says so
        let v2 = site2().with_fw_group("ez2-site-v2").unwrap();
        let fw_v2 = desired_firewall("ez2-site-v2", None);
        assert_eq!(
            drift(&v2, CT112, Some(&fw_v2)).unwrap(),
            Vec::<Drift>::new()
        );
        // …but the v1 file against a v2 record is
        let keys = |d: Vec<Drift>| d.into_iter().map(|x| x.key).collect::<Vec<_>>();
        assert_eq!(
            keys(drift(&v2, CT112, Some(&desired_firewall(FW_GROUP, None))).unwrap()),
            ["firewall"]
        );
        assert!(site2().with_fw_group("ez2-site-v").is_err());
        assert!(site2().with_fw_group("ez2-edge-v1").is_err());
        assert!(site2().with_fw_group("ez2-site-v2; rm").is_err());
        // SSH-1: the record's ssh_port adds exactly the two IN rules the shell writes
        let ssh = site2().with_ssh_port(2112).unwrap();
        let fw_ssh = desired_firewall(FW_GROUP, Some(2112));
        assert!(fw_ssh.contains("IN DROP -source +private -p tcp -dport 22 -log nolog\nIN ACCEPT -p tcp -dport 22 -log nolog\nGROUP ez2-site-v1"));
        assert_eq!(
            drift(&ssh, CT112, Some(&fw_ssh)).unwrap(),
            Vec::<Drift>::new()
        );
        // the same file against a record without ssh is drift (and vice versa)
        assert_eq!(
            keys(drift(&site2(), CT112, Some(&fw_ssh)).unwrap()),
            ["firewall"]
        );
        assert_eq!(
            keys(drift(&ssh, CT112, Some(&desired_firewall(FW_GROUP, None))).unwrap()),
            ["firewall"]
        );
        // the port must be 2000 + ct
        assert!(site2().with_ssh_port(2113).is_err());
        assert_eq!(customer_ssh_port(112), Some(2112));
        // record round trip keeps both
        let full = site2()
            .with_fw_group("ez2-site-v2")
            .unwrap()
            .with_ssh_port(2112)
            .unwrap();
        let rec = record(&full, "2026-11-10T01:00:00Z");
        assert_eq!(rec["fw_group"], "ez2-site-v2");
        assert_eq!(rec["ssh_port"], 2112);
        assert_eq!(spec_from_record(&rec).unwrap(), full);
        // the real host A records after NET-1 / SSH-1
        let host_a = json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"site2.example.net","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112});
        assert_eq!(spec_from_record(&host_a).unwrap(), full);
        assert!(spec_from_record(
            &json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"a.b","ssh_port":22})
        )
        .is_err());
        assert!(spec_from_record(
            &json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"a.b","fw_group":"other"})
        )
        .is_err());
    }

    fn gaps(rec: &Value) -> Vec<(&'static str, String)> {
        record_gaps(rec)
            .into_iter()
            .map(|g| (g.code, g.key))
            .collect()
    }

    #[test]
    fn a_record_written_by_this_crate_has_no_gaps() {
        let rec = record(&site2(), "2026-11-10T01:00:00Z");
        assert_eq!(gaps(&rec), []);
        assert_eq!(spec_from_record_strict(&rec).unwrap(), site2());
    }

    #[test]
    fn the_three_shapes_found_on_the_first_host() {
        // site2: written by the bash PoC, no limits at all. The lenient read fills in the
        // test tier; the strict one says so.
        let site2 = json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"site2.example.net","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112});
        assert_eq!(gaps(&site2), [("R02", "limits".to_owned())]);
        assert!(spec_from_record(&site2).is_ok());
        assert!(spec_from_record_strict(&site2).is_err());
        // site3: another spelling (`cpulimit`, in cores) and two limits missing. A lenient
        // read ignores the unknown key and defaults the rest.
        let mut site3 = site2.clone();
        site3["limits"] = json!({"cpulimit": 2, "memory_mb": 2048, "pids_max": 2048});
        assert_eq!(
            gaps(&site3),
            [
                ("R03", "limits.cores".to_owned()),
                ("R03", "limits.cpulimit_x100".to_owned()),
                ("R03", "limits.swap_mb".to_owned()),
                ("R04", "limits.cpulimit".to_owned()),
            ]
        );
        // site1: the full form.
        let mut site1 = site2.clone();
        site1["limits"] =
            json!({"cores":2,"cpulimit_x100":200,"memory_mb":2048,"swap_mb":512,"pids_max":2048});
        assert_eq!(gaps(&site1), []);
    }

    #[test]
    fn tenant_and_disk_are_optional_but_typed() {
        let mut r = record(&site2(), "2026-11-10T01:00:00Z");
        // no tenant named: no tenant, and never the site's own code
        assert_eq!(record_tenant(&r), None);
        r["tenant_id"] = json!("site2");
        assert_eq!(record_tenant(&r), Some("site2"));
        r["tenant_id"] = json!("t-3k9f2a");
        r["disk_gb"] = json!(16);
        assert_eq!(gaps(&r), []);
        assert_eq!(record_tenant(&r), Some("t-3k9f2a"));
        r["tenant_id"] = json!("Not A Code");
        r["disk_gb"] = json!(0);
        assert_eq!(
            gaps(&r),
            [
                ("R01", "tenant_id".to_owned()),
                ("R01", "disk_gb".to_owned())
            ]
        );
        assert_eq!(record_tenant(&r), None);
        Limits::START.validate().unwrap();
        assert_eq!(Limits::START.memory_mb, 1024);
    }

    #[test]
    fn record_gaps_by_kind() {
        let full = record(&site2(), "2026-11-10T01:00:00Z");
        let without = |key: &str| {
            let mut r = full.clone();
            r.as_object_mut().unwrap().remove(key);
            r
        };
        assert_eq!(gaps(&json!([])), [("R01", String::new())]);
        for key in ["site", "ct", "ip", "hosts", "created"] {
            assert_eq!(gaps(&without(key)), [("R01", key.to_owned())], "{key}");
        }
        for key in ["wp", "locale", "fw_group", "limits"] {
            assert_eq!(gaps(&without(key)), [("R02", key.to_owned())], "{key}");
        }
        // no customer ssh is a complete statement, absent or null
        assert_eq!(gaps(&without("ssh_port")), []);
        let mut r = full.clone();
        r["ssh_port"] = Value::Null;
        assert_eq!(gaps(&r), []);
        // wrong types are gaps too, and unknown keys are reported
        let mut r = full.clone();
        r["ct"] = json!("112");
        r["limits"]["cores"] = json!("2");
        r["state"] = json!("active");
        assert_eq!(
            gaps(&r),
            [
                ("R01", "ct".to_owned()),
                ("R03", "limits.cores".to_owned()),
                ("R04", "state".to_owned()),
            ]
        );
    }

    /// A value the lenient read would replace with a default is a gap, whatever its JSON
    /// type says (review of #623 segment 2, f1fae M1 / L1 / L2).
    #[test]
    fn values_the_lenient_read_would_not_use_are_gaps() {
        let full = record(&site2(), "2026-11-10T01:00:00Z");
        // 2^32 does not fit a limit: spec_from_record would fall back to the test tier
        let mut r = full.clone();
        r["limits"]["memory_mb"] = json!(4_294_967_296_u64);
        assert_eq!(gaps(&r), [("R03", "limits.memory_mb".to_owned())]);
        assert!(spec_from_record_strict(&r).is_err());
        assert_eq!(spec_from_record(&r).unwrap().limits.memory_mb, 2048);
        let mut r = full.clone();
        r["limits"]["memory_mb"] = json!(4_294_967_295_u64);
        assert_eq!(gaps(&r), []);
        // wp is 0 or 1; anything else reads as "not WordPress" without saying so
        for (wp, want) in [
            (json!(0), 0),
            (json!(1), 0),
            (json!(2), 1),
            (json!(true), 1),
        ] {
            let mut r = full.clone();
            r["wp"] = wp;
            assert_eq!(gaps(&r).len(), want, "{}", r["wp"]);
        }
        // a port that is not a port
        for port in [json!("2112"), json!(70_000), json!(-1)] {
            let mut r = full.clone();
            r["ssh_port"] = port;
            assert_eq!(gaps(&r), [("R01", "ssh_port".to_owned())]);
        }
        let mut r = full.clone();
        r["ct"] = json!(4_294_967_296_u64);
        assert_eq!(gaps(&r), [("R01", "ct".to_owned())]);
    }
}
