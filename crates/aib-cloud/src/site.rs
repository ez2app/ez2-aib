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

/// Site code: 2–41 chars of `[a-z0-9.-]`, starting with a letter or digit (it becomes a hostname,
/// a record file name and a Caddy marker, so nothing else is allowed).
pub fn valid_code(code: &str) -> bool {
    let b = code.as_bytes();
    (2..=41).contains(&b.len())
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
    for (k, v) in &obs {
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
/// during a blue-green restore). Carries the white-label import (WL-1).
pub fn caddy_block_upstream(spec: &SiteSpec, upstream_ip: &str) -> String {
    alloc::format!(
        "{marker}\n{hosts} {{\n\timport ez2_whitelabel\n\tencode zstd gzip\n\treverse_proxy {ip}:80 {{\n\t\theader_up X-Real-IP {{remote_host}}\n\t}}\n}}\n",
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
        "{marker}\n{hosts} {{\n\timport ez2_whitelabel\n\t# ez2:suspended\n\theader Cache-Control \"no-store\"\n\theader Retry-After \"3600\"\n\troot * {dir}\n\trewrite * /{file}\n\tfile_server {{\n\t\tstatus 503\n\t}}\n}}\n",
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
    let marked = body.iter().any(|l| *l == "# ez2:suspended");
    let serves_503 = body.iter().any(|l| l.starts_with("file_server"))
        && body.iter().any(|l| *l == "status 503");
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
            &["eu02-site2.ez2.site"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
    }

    const CT112: &str = "arch: amd64\ncores: 2\ncpulimit: 2\nfeatures: nesting=1\nhostname: site2\nlxc.cgroup2.pids.max: 2048\nmemory: 2048\nnameserver: 1.1.1.1\nnet0: name=eth0,bridge=vmbr1,firewall=1,gw=10.77.0.1,hwaddr=BC:24:11:AA:BB:CC,ip=10.77.0.12/24,type=veth\nonboot: 1\nostype: debian\nrootfs: local-lvm:vm-112-disk-0,size=16G\nswap: 512\nunprivileged: 1\n";

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
        assert!(site2().hosts == ["eu02-site2.ez2.site"]);
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
        let poc = json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"eu02-site2.ez2.site","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v1"});
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
        // …while PVE's own identity keys and the declared idmap are not
        let benign = alloc::format!(
            "{CT112}description: customer site\nsearchdomain: ez2.site\ntags: ez2\nlxc.idmap: u 0 165536 65536\nlxc.idmap: g 0 165536 65536\n"
        );
        assert_eq!(
            drift(&site2(), &benign, Some(&fw)).unwrap(),
            Vec::<Drift>::new()
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

    #[test]
    fn s7_caddy_blocks_and_swap() {
        let sp = site2();
        let up = caddy_block_upstream(&sp, &sp.ip());
        assert!(
            up.starts_with("# site:site2\neu02-site2.ez2.site {\n\timport ez2_whitelabel\n"),
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
        assert!(p[3].argv.iter().any(|a| a == "Host: eu02-site2.ez2.site"));
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
        // the real EU02 records after NET-1 / SSH-1
        let eu02 = json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"eu02-site2.ez2.site","wp":1,"locale":"zh_TW","created":"2026-11-09T03:33:11Z","template":900,"fw_group":"ez2-site-v2","ssh_port":2112});
        assert_eq!(spec_from_record(&eu02).unwrap(), full);
        assert!(spec_from_record(
            &json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"a.b","ssh_port":22})
        )
        .is_err());
        assert!(spec_from_record(
            &json!({"site":"site2","ct":112,"ip":"10.77.0.12","hosts":"a.b","fw_group":"other"})
        )
        .is_err());
    }
}
