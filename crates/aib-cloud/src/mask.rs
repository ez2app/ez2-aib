// SPDX-License-Identifier: Apache-2.0
//! What a site's container is **not** shown of the machine underneath (4.4.1 §23: the
//! plan does not disclose the physical host; Andy 11-10: "CPU 型號先遮、能遮的預設全遮").
//!
//! Measured in a site CT (11-10, read-only): the CPU's model name, the provider's name
//! and the board's model in DMI, the BIOS vendor, the disks' model, the RAID layout, the
//! host's boot command line and its hardware sensors were all readable. (What exactly
//! was read is in the private spec, not here: this repository is public.)
//!
//! This module says what is put in their place; the host shell does the mounting when
//! the container starts. No I/O.
//!
//! * `/proc/cpuinfo` is rebuilt from an **allowlist** of fields: the model name becomes
//!   [`CPU_MODEL`], family / model / stepping become 0, and a field not named here is
//!   dropped (microcode, cache size, bugs, power management …). The feature flags stay:
//!   programs choose code paths by them.
//! * Under `/sys` only what [`SYS_KEEP`] names stays readable; everything else is covered
//!   by an empty directory or an empty file. Under `/proc` the entries of [`PROC_HIDE`]
//!   are covered.
//!
//! **What this cannot hide**, and so does not claim to:
//! * the kernel's release string (`uname`), which names the virtualisation platform;
//! * whatever a program asks the processor itself (the CPUID instruction answers with
//!   the real brand string);
//! * **who owns the IP address** the container reaches the internet from and is reached
//!   at: that is a public record, and it names the provider. Hiding it is a matter of
//!   where traffic enters and leaves (a relay, a proxy), not of this module;
//! * a container's own root can take the covers off again; they hold against the site's
//!   users, which is what a customer's key and seat are given. The same holds for the
//!   per-site part below (the user-namespace limit is set by the CT's root and can be
//!   undone by it): **the whole mask stands on a customer never getting the CT's root** —
//!   `aib_cloud::customer`'s roles give the site's user only (c477a, ct-mask2 N3).

use alloc::string::String;
use alloc::vec::Vec;

/// The model name a container sees.
pub const CPU_MODEL: &str = "vCPU";

/// Most processors one container is given a `cpuinfo` for.
pub const CORES_MAX: u32 = 256;

/// What stays readable under `/sys`, as paths below it. **Everything else under `/sys`
/// is covered** — an allowlist, because a customer's AI seat works inside the container
/// (Andy 11-10: "要讓 ai 也盡量找不出主機與主機商的任何資訊") and a list of what to hide
/// would always be one entry short. Kept: the container's own cgroup tree, fuse and its
/// network interfaces. `devices/system/cpu` is left by the walk only to be covered whole
/// by the directory [`cpu_dir`] describes: what lxcfs shows there still lists every
/// processor of the host and the processor's model code (c477a, measured 11-10).
pub const SYS_KEEP: &[&str] = &[
    "fs/cgroup",
    "fs/fuse",
    "devices/system/cpu",
    "devices/virtual/net",
    "class/net",
];

/// What to do with the entries of one directory under `/sys`.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct SysStep {
    /// Entries to cover (paths below `/sys`).
    pub cover: Vec<String>,
    /// Directories to open and decide again, because something kept lies below them.
    pub descend: Vec<String>,
}

/// Decide for the directory `dir` below `/sys` (`""` for `/sys` itself) and the names
/// found in it: an entry that is kept is left alone, one that leads to something kept
/// is opened, every other one is covered.
pub fn sys_step(dir: &str, names: &[&str]) -> SysStep {
    let mut step = SysStep::default();
    for name in names {
        let path = if dir.is_empty() {
            String::from(*name)
        } else {
            alloc::format!("{dir}/{name}")
        };
        if SYS_KEEP.contains(&path.as_str()) {
            continue;
        }
        let leads_to_kept = SYS_KEEP.iter().any(|k| {
            k.strip_prefix(path.as_str())
                .is_some_and(|rest| rest.starts_with('/'))
        });
        if leads_to_kept {
            step.descend.push(path);
        } else {
            step.cover.push(path);
        }
    }
    step
}

/// Entries of `/proc` that are covered, files and directories alike: hardware and
/// driver lists, interrupt and scheduler tables (one column per host processor), memory
/// zone tables (the host's real size), the RAID layout, the host's boot command line,
/// host file-system device names. `/proc` cannot be an allowlist: it is where the
/// container's own processes live.
///
/// Left alone on purpose: `/proc/version` and `/proc/sys/kernel/osrelease` — the same
/// string is what `uname` answers, which no mount can change, and two different answers
/// would only draw the eye.
pub const PROC_HIDE: &[&str] = &[
    "acpi",
    "asound",
    "buddyinfo",
    "bus",
    "cmdline",
    "consoles",
    "crypto",
    "devices",
    "diskstats",
    "dma",
    "driver",
    "fb",
    "fs",
    "interrupts",
    "iomem",
    "ioports",
    "irq",
    "kallsyms",
    "mdstat",
    "misc",
    "modules",
    "mtrr",
    "pagetypeinfo",
    "partitions",
    "sched_debug",
    "schedstat",
    "scsi",
    "softirqs",
    "timer_list",
    "vmallocinfo",
    "zoneinfo",
];

/// The line in a container's configuration that runs the mask when it starts. It names
/// a wrapper, not the binary: the runtime treats a hook that is missing, that an older
/// binary does not know, or that hangs, as a failed start (33e28, measured 11-10) — and
/// the mask must never be why a customer's site is down.
pub const HOOK_LINE: &str = "lxc.hook.mount: /usr/local/sbin/ez2-ct-mask-hook";

/// The wrapper [`HOOK_LINE`] names: the last start's verdict removed first (the verdict
/// file is on the host and outlives a restart: a hook that cannot run, or is cut off by
/// the time limit, must not leave an old `ok` standing — c477a, #642 seg. 1 M1), a time
/// limit, and exit 0 whatever happens. The hook
/// leaves its verdict in a file for `site verify` to read; a wrapper that could not even
/// start the binary leaves none, which reads as not masked.
pub const HOOK_WRAPPER: &str = "#!/bin/sh\n# installed by aib-cloud-host; see aib_cloud::mask\n# the verdict of an earlier start must not stand for this one\ncase \"${LXC_NAME:-}\" in ''|*[!0-9]*) ;; *) rm -f \"/run/ez2-cloud/mask/$LXC_NAME/result\" ;; esac\n/usr/bin/timeout 20 /usr/local/sbin/aib-cloud-host ct mask >/dev/null 2>&1\nexit 0\n";

/// Other full views of `/proc` and `/sys` a container has and that must be covered too:
/// with nesting on, the runtime keeps an unmasked copy of each for nested containers
/// (33e28, measured 11-10: only the AppArmor profile stood between a site user and them).
pub const EXTRA_HIDE: &[&str] = &["dev/.lxc/proc", "dev/.lxc/sys"];

/// What the hook writes to its result file when every cover is in place.
pub fn result_ok(covers: usize) -> String {
    alloc::format!("ok {covers}\n")
}

/// Whether a result file says the container is masked. A missing or empty file, or
/// anything but `ok <n>` with n above zero, is "not masked".
pub fn result_is_masked(text: Option<&str>) -> bool {
    text.and_then(|t| t.trim().strip_prefix("ok "))
        .and_then(|n| n.parse::<usize>().ok())
        .is_some_and(|n| n > 0)
}

/// What a field of the host's `cpuinfo` becomes for processor `i` of `cores`. `None`
/// drops the field: only what is named here is passed on.
fn field(key: &str, value: &str, i: u32, cores: u32) -> Option<String> {
    let number = |n: u32| Some(alloc::format!("{n}"));
    match key {
        "processor" | "core id" | "apicid" | "initial apicid" => number(i),
        "model name" => Some(CPU_MODEL.into()),
        "cpu family" | "model" | "stepping" | "physical id" => number(0),
        "siblings" | "cpu cores" => number(cores),
        // no clock speed, CPUID level or address sizes: with the flags they narrow the
        // model down to a few (33e28). The flags stay because programs need them.
        "vendor_id" | "fpu" | "fpu_exception" | "wp" | "flags" | "clflush size"
        | "cache_alignment" => Some(value.into()),
        _ => None,
    }
}

/// `/proc/cpuinfo` for a container with `cores` processors, made from the first
/// processor of the host's. `None` when the host's text has no such block or `cores` is
/// not 1..=[`CORES_MAX`].
pub fn cpuinfo(host: &str, cores: u32) -> Option<String> {
    if !(1..=CORES_MAX).contains(&cores) {
        return None;
    }
    let first: Vec<(&str, &str)> = host
        .lines()
        .take_while(|l| !l.trim().is_empty())
        .filter_map(|l| l.split_once(':'))
        .map(|(k, v)| (k.trim(), v.trim()))
        .collect();
    if first.first().map(|f| f.0) != Some("processor") {
        return None;
    }
    let mut out = String::new();
    for i in 0..cores {
        for (key, value) in &first {
            if let Some(v) = field(key, value, i, cores) {
                out.push_str(key);
                // the kernel's own layout: a tab after the key, then ": "
                out.push_str("\t: ");
                out.push_str(&v);
                out.push('\n');
            }
        }
        out.push('\n');
    }
    Some(out)
}

/// What [`cpu_dir`] gives: the files directly in the directory (name, text) and the
/// names of the per-processor directories.
pub type CpuDir = (Vec<(&'static str, String)>, Vec<String>);

/// What the container sees as `/sys/devices/system/cpu`, for `cores` processors: files
/// (name, content) and the names of the per-processor directories. Programs count
/// processors from `online` and from the `cpuN` entries; nothing else of the host's
/// directory is carried over.
pub fn cpu_dir(cores: u32) -> Option<CpuDir> {
    if !(1..=CORES_MAX).contains(&cores) {
        return None;
    }
    let range = if cores == 1 {
        String::from("0\n")
    } else {
        alloc::format!("0-{}\n", cores - 1)
    };
    let files = ["online", "possible", "present"]
        .into_iter()
        .map(|name| (name, range.clone()))
        .collect();
    let dirs = (0..cores).map(|i| alloc::format!("cpu{i}")).collect();
    Some((files, dirs))
}

/// Whether a `cpuinfo` text is one this module wrote: every processor carries
/// [`CPU_MODEL`] and nothing else. For the check that a running container is masked.
pub fn is_masked_cpuinfo(text: &str) -> bool {
    let mut models = text
        .lines()
        .filter_map(|l| l.split_once(':'))
        .filter(|(k, _)| k.trim() == "model name")
        .map(|(_, v)| v.trim())
        .peekable();
    models.peek().is_some() && models.all(|m| m == CPU_MODEL)
}

/// The container's id out of what the runtime passes the hook: digits only, 100 and up.
pub fn valid_ct_name(name: &str) -> Option<u32> {
    if name.is_empty() || name.len() > 9 || !name.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    name.parse().ok().filter(|n| *n >= 100)
}

/// `cores` out of a container's configuration text (`cores: 2`); the first section only
/// (snapshots follow after a `[name]` line).
pub fn conf_cores(conf: &str) -> Option<u32> {
    conf.lines()
        .take_while(|l| !l.trim_start().starts_with('['))
        .find_map(|l| l.strip_prefix("cores:"))
        .and_then(|v| v.trim().parse().ok())
}

// ---------------------------------------------------------------------------------------
// The per-site part: what a site CT's own configuration and files must carry for the
// covers above to be the whole story (Andy 11-11: "用戶用 aib ssh 透過 ssh 使用 ai 查得出
// 主機資訊、與主機商嗎?"). Measured in a site CT that day: the platform's vendor prefix in
// the hardware address, the platform's name and our private network's name in
// `resolv.conf` / `hosts`, and user namespaces open to the site's users — through which a
// fresh `/proc` or `/sys` could be mounted past every cover (c477a, 11-10).

/// The resolver a site CT is given when its configuration names none (without one, the CT
/// inherits the host's). Neutral: it names nobody's network.
pub const NEUTRAL_RESOLVER: &str = "1.1.1.1"; // ip-literal-ok: well-known public resolver, never a host of ours

/// Whether a CT's `nameserver` value names only public resolvers: every address in it
/// IPv4, and none private, loopback, link-local, shared, unspecified, broadcast, multicast,
/// reserved, documentation or benchmarking (c477a, ct-mask2 N4).
/// Shared is 100.64.0.0/10, where a tailnet's own resolver lives. (ip-literal-ok: range name, never a host of ours)
/// Reserved is 240.0.0.0/4. (ip-literal-ok: range name, never a host of ours)
/// A private one is the host's resolver or our
/// network's (it would show in the CT's `resolv.conf`); IPv6 is not given to a site CT.
/// An empty value is not neutral (c477a, ct-mask2 N1).
pub fn resolver_is_neutral(value: &str) -> bool {
    let mut any = false;
    for word in value.split_whitespace() {
        any = true;
        let Ok(ip) = word.parse::<core::net::Ipv4Addr>() else {
            return false;
        };
        let [a, b, ..] = ip.octets();
        let shared = a == 100 && (64..128).contains(&b);
        // reserved (240.0.0.0/4, the broadcast address in it) and benchmarking (198.18.0.0/15) (ip-literal-ok: range name / boundary sample, never a host of ours)
        let special = a >= 240 || (a == 198 && (b & 0xFE) == 18);
        if ip.is_private()
            || ip.is_loopback()
            || ip.is_link_local()
            || ip.is_unspecified()
            || ip.is_broadcast()
            || ip.is_multicast()
            || ip.is_documentation()
            || shared
            || special
        {
            return false;
        }
    }
    any
}

/// The `netN` keys of a CT's configuration other than `net0`. A site CT is given one
/// network interface; another one is not judged here (its address and IPv6 are not
/// checked) and is listed for a person (c477a, ct-mask2 N2).
pub fn other_nics(cfg: &[(String, String)]) -> Vec<&str> {
    cfg.iter()
        .map(|(k, _)| k.as_str())
        .filter(|k| {
            k.strip_prefix("net")
                .is_some_and(|n| !n.is_empty() && n != "0" && n.bytes().all(|c| c.is_ascii_digit()))
        })
        .collect()
}

/// When the configuration fixes go in while a site is opened: with its limits and its
/// network, before the CT first starts — the hook only runs at a start, and a hardware
/// address seen once is seen for good.
pub const CONFIG_STEP: crate::sitecreate::CreateStep = crate::sitecreate::CreateStep::Limits;

/// When the files inside the CT are written while a site is opened: with the site's other
/// secrets and settings, before [`crate::sitecreate::CreateStep::Edge`] makes it reachable.
///
/// The CT already runs by then (`Start` comes before `Identity`): a file under
/// `/etc/sysctl.d` is only read at the next start. So the step that writes
/// [`SYSCTL_FILE`] then runs [`SYSCTL_APPLY`] inside the CT and reads
/// `/proc/sys/user/max_user_namespaces` back ([`userns_closed`] must say `Some(true)`) —
/// or restarts the CT before `Edge`. A step that wrote the file and read nothing back has
/// not closed anything (c477a, ct-mask2 L2). The same goes for a site that is already
/// open when the mask is put on it.
pub const FILES_STEP: crate::sitecreate::CreateStep = crate::sitecreate::CreateStep::Identity;

/// Inside the CT: no new user namespaces for anyone in it. A site user who can make one
/// can mount a new `/proc` or `/sys` of their own, and the covers are not on those.
/// Written inside the CT (its own root sets its own limit), so the CT's configuration
/// carries no key `site drift` would call drift.
pub const SYSCTL_FILE: &str = "/etc/sysctl.d/90-ez2-mask.conf";

/// What makes [`SYSCTL_FILE`] count in a CT that already runs (run inside it, as its root).
pub const SYSCTL_APPLY: &str = "sysctl -p /etc/sysctl.d/90-ez2-mask.conf";

/// Whether `/proc/sys/user/max_user_namespaces`, read inside the CT, says no new user
/// namespaces: `Some(true)` for 0, `Some(false)` for another number, `None` for anything
/// that is not a number (or nothing read).
pub fn userns_closed(proc_value: Option<&str>) -> Option<bool> {
    proc_value?.trim().parse::<u64>().ok().map(|n| n == 0)
}

/// The text of [`SYSCTL_FILE`].
pub const SYSCTL_TEXT: &str =
    "# installed by aib-cloud-host; see aib_cloud::mask\nuser.max_user_namespaces = 0\n";

/// Inside the CT: present (empty), the platform leaves `/etc/hosts` alone at each start
/// instead of writing its own block — which carries the platform's name and the host's
/// search domain.
pub const HOSTS_KEEP_FILE: &str = "/etc/.pve-ignore.hosts";

/// The `/etc/hosts` a site CT is given: loopback names and its own name, nothing else.
/// `None` when the name is not one a hosts file can carry as it is.
pub fn hosts_text(hostname: &str) -> Option<String> {
    let ok = !hostname.is_empty()
        && hostname.len() <= 63
        && hostname
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-')
        && !hostname.starts_with('-');
    ok.then(|| {
        alloc::format!(
            "# written by aib-cloud-host; see aib_cloud::mask\n127.0.0.1\tlocalhost\n127.0.1.1\t{hostname}\n::1\tlocalhost ip6-localhost ip6-loopback\n"
        )
    })
}

/// Inside the CT: present (empty), the platform leaves `/etc/resolv.conf` alone at each
/// start instead of writing its own block around the resolver lines — a block that names
/// the platform, and carried the host's search domain on 11-12 (f1fae's baseline, item 8).
pub const RESOLV_KEEP_FILE: &str = "/etc/.pve-ignore.resolv.conf";

/// The `/etc/resolv.conf` a site CT is given: the neutral search domain and the neutral
/// resolver, nothing else.
pub fn resolv_text() -> String {
    alloc::format!(
        "# written by aib-cloud-host; see aib_cloud::mask\nsearch {}\nnameserver {NEUTRAL_RESOLVER}\n",
        crate::verify::SEARCH_DOMAIN
    )
}

/// Files a platform template leaves inside a CT that name the platform or its vendor
/// (the appliance file names the template's maker). Removed while a site is opened
/// ([`FILES_STEP`]); `site verify` S16 says none is there.
pub const PLATFORM_FILES: &[&str] = &["/etc/appliance.info"];

/// `/etc/motd` of a site CT: empty (a template's own says whose template it is).
pub const MOTD_TEXT: &str = "";

/// `/etc/issue` of a site CT: only the system's own name (`\S`: the login prompt shows
/// the operating system's pretty name).
pub const ISSUE_TEXT: &str = "\\S\n";

/// Time servers a site CT may ask: the public pool only, never the host's or the
/// provider's own (an address that names who runs the network).
pub const PUBLIC_NTP: &str = "pool.ntp.org";

/// The time service's drop-in inside a site CT. systemd reads the drop-ins of
/// `/usr/lib`, `/usr/local/lib`, `/run` and `/etc` together, sorted by file name (one in
/// `/etc` hides one of the same name elsewhere), so ours is named to sort last: a
/// template's `95-*.conf` under `/usr/lib` does not undo it (c477a S1 L1).
pub const TIMESYNCD_FILE: &str = "/etc/systemd/timesyncd.conf.d/99-ez2-mask.conf";

/// The text of [`TIMESYNCD_FILE`]: both lists emptied first (an empty value resets a list
/// that an earlier file set), then the public pool.
pub const TIMESYNCD_TEXT: &str = "# installed by aib-cloud-host; see aib_cloud::mask\n[Time]\nNTP=\nNTP=pool.ntp.org\nFallbackNTP=\nFallbackNTP=pool.ntp.org\n";

/// Whether the time servers that the time service's files, read in order (the main file,
/// then its drop-ins), leave in effect are all public: every `NTP=` and `FallbackNTP=`
/// name is [`PUBLIC_NTP`] or one of its sub-pools. An empty value empties that list; an
/// empty list leaves the system's own defaults, which are public.
pub fn ntp_is_public(text: &str) -> bool {
    let mut ntp: Vec<&str> = Vec::new();
    let mut fallback: Vec<&str> = Vec::new();
    for line in text.lines().map(str::trim) {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let list = match key.trim() {
            "NTP" => &mut ntp,
            "FallbackNTP" => &mut fallback,
            _ => continue,
        };
        if value.trim().is_empty() {
            list.clear();
        } else {
            list.extend(value.split_whitespace());
        }
    }
    let public = |n: &&str| {
        let n = n.to_ascii_lowercase();
        n == PUBLIC_NTP || n.ends_with(".pool.ntp.org")
    };
    ntp.iter().chain(fallback.iter()).all(public)
}

/// A hardware address for a site CT out of six random bytes: unicast and locally
/// administered (the low two bits of the first byte are `10`), so no vendor prefix names
/// the platform. Upper-case hex, as the platform writes them.
pub fn local_hwaddr(raw: [u8; 6]) -> String {
    let first = (raw[0] & 0xFC) | 0x02;
    alloc::format!(
        "{first:02X}:{:02X}:{:02X}:{:02X}:{:02X}:{:02X}",
        raw[1],
        raw[2],
        raw[3],
        raw[4],
        raw[5]
    )
}

/// Whether `mac` is six pairs of hex digits, unicast and locally administered.
pub fn hwaddr_is_local(mac: &str) -> bool {
    let parts: Vec<&str> = mac.split(':').collect();
    let shaped = parts.len() == 6
        && parts
            .iter()
            .all(|p| p.len() == 2 && p.bytes().all(|c| c.is_ascii_hexdigit()));
    shaped && u8::from_str_radix(parts[0], 16).is_ok_and(|b| b & 0x03 == 0x02)
}

/// One change a site CT's configuration needs: `pct set <ct> --<key> <value>` (the hook
/// line goes in the configuration file as it is, `pct` has no option for it).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigFix {
    pub key: &'static str,
    pub value: String,
    pub why: &'static str,
}

/// Why no fixes could be worked out.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanError {
    /// The configuration has no `net0` line: there is no address to judge or to replace.
    NoNet0,
}

/// What a site CT's configuration (`cfg`, as `site::parse_pct_config` reads it) still
/// needs for the mask: the hook line once and nothing else on that key, a locally
/// administered hardware address and no IPv6 (both in `net0`), a resolver and the
/// neutral search domain. `new_hwaddr` is used only when the address must change; it
/// must itself be local (see [`local_hwaddr`]). Empty: nothing to change.
pub fn config_fixes(
    cfg: &[(String, String)],
    new_hwaddr: &str,
) -> Result<Vec<ConfigFix>, PlanError> {
    let get = |key: &str| cfg.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str());
    let mut out = Vec::new();
    let hooks: Vec<&str> = cfg
        .iter()
        .filter(|(k, _)| k == "lxc.hook.mount")
        .map(|(_, v)| v.as_str())
        .collect();
    let hook_ok = matches!(hooks.as_slice(), [one] if crate::site::mask_hook_ok(one));
    if !hook_ok {
        out.push(ConfigFix {
            key: "lxc.hook.mount",
            value: HOOK_LINE
                .strip_prefix("lxc.hook.mount: ")
                .unwrap_or(HOOK_LINE)
                .into(),
            why: "the mask's wrapper, once, and no other hook on that key",
        });
    }
    let net0 = get("net0").ok_or(PlanError::NoNet0)?;
    let mut fields: Vec<String> = Vec::new();
    let mut changed = false;
    let mut had_hwaddr = false;
    for f in net0.split(',') {
        if f.starts_with("ip6=") || f.starts_with("gw6=") {
            changed = true;
            continue;
        }
        if let Some(mac) = f.strip_prefix("hwaddr=") {
            had_hwaddr = true;
            if !hwaddr_is_local(mac) {
                changed = true;
                fields.push(alloc::format!("hwaddr={new_hwaddr}"));
                continue;
            }
        }
        fields.push(f.into());
    }
    if !had_hwaddr {
        changed = true;
        fields.push(alloc::format!("hwaddr={new_hwaddr}"));
    }
    if changed {
        out.push(ConfigFix {
            key: "net0",
            value: fields.join(","),
            why: "a locally administered hardware address, and no IPv6 (it would leave from the host's own prefix)",
        });
    }
    if !get("nameserver").is_some_and(resolver_is_neutral) {
        out.push(ConfigFix {
            key: "nameserver",
            value: NEUTRAL_RESOLVER.into(),
            why: "without a public one the CT reads the host's resolver or our network's",
        });
    }
    if get("searchdomain") != Some(crate::verify::SEARCH_DOMAIN) {
        out.push(ConfigFix {
            key: "searchdomain",
            value: crate::verify::SEARCH_DOMAIN.into(),
            why: "without it the CT carries the host's search domain (our private network's name)",
        });
    }
    Ok(out)
}

/// What a person (or the red-team check) reads **as the site's own user** inside a masked
/// CT, and what it should show. From a PHP request too, not only a shell: php-fpm runs in
/// its own sandbox (33e28).
pub const INSIDE_EXPECT: &[(&str, &str)] = &[
    (
        "/proc/cpuinfo",
        "every model name is vCPU; no microcode, cache size, bugs or MHz lines",
    ),
    ("/sys/class/dmi/id/sys_vendor", "missing or empty"),
    ("/sys/class/dmi/id/board_name", "missing or empty"),
    (
        "/sys/devices/system/cpu",
        "only as many processors as the CT has cores",
    ),
    ("/proc/cmdline", "empty"),
    (
        "/etc/resolv.conf",
        "search invalid; the resolver of the CT's configuration; no platform block",
    ),
    (
        "/etc/hosts",
        "loopback and the CT's own name; no platform block, no private network name",
    ),
    (
        "/sys/class/net/eth0/address",
        "a locally administered address (second hex digit 2, 6, A or E)",
    ),
    ("/proc/sys/user/max_user_namespaces", "0"),
    ("unshare -Ur true", "refused"),
    ("dmesg", "refused"),
    ("ip -6 addr show scope global", "nothing"),
    ("/etc/appliance.info", "missing"),
    ("/etc/motd", "missing or empty"),
    ("/etc/issue", "only the system's own name (\\S)"),
    (
        "/etc/systemd/timesyncd.conf and its drop-ins",
        "only the public pool (pool.ntp.org) in effect",
    ),
];

/// What stays readable however the CT is masked, said so nobody claims otherwise.
pub const CANNOT_HIDE: &[&str] = &[
    "the kernel's release string (uname -r): it names the virtualisation platform",
    "that the site runs in a container (systemd-detect-virt, /proc/1/environ, cgroup paths)",
    "the processor's own answer to CPUID, and the feature flags (they tell its generation)",
    "the device names in the mount table and df, which carry the CT's number",
    "who owns the IP address the CT is reached at and leaves from (a public record; a matter for the relay and the egress, not this mask)",
];

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    // shaped like a real host's first processor (made-up model; flags shortened)
    const HOST: &str = "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 23\nmodel\t\t: 113\nmodel name\t: Example Corp Model 9000 8-Core Processor\nstepping\t: 0\nmicrocode\t: 0x1234567\ncpu MHz\t\t: 4139.606\ncache size\t: 512 KB\nphysical id\t: 0\nsiblings\t: 12\ncore id\t\t: 0\ncpu cores\t: 6\napicid\t\t: 0\ninitial apicid\t: 0\nfpu\t\t: yes\nfpu_exception\t: yes\ncpuid level\t: 16\nwp\t\t: yes\nflags\t\t: fpu vme de pse tsc msr sse sse2 ht syscall nx\nbugs\t\t: sysret_ss_attrs spectre_v1 spectre_v2 retbleed\nbogomips\t: 7199.73\nTLB size\t: 3072 4K pages\nclflush size\t: 64\ncache_alignment\t: 64\naddress sizes\t: 43 bits physical, 48 bits virtual\npower management: ts ttp tm hwpstate\n\nprocessor\t: 1\nmodel name\t: Example Corp Model 9000 8-Core Processor\n\n";

    #[test]
    fn the_machine_is_not_named_in_cpuinfo() {
        let text = cpuinfo(HOST, 2).unwrap();
        for word in [
            "Example",
            "Corp",
            "9000",
            "0x1234567",
            "512 KB",
            "retbleed",
            "bogomips",
            "hwpstate",
            "TLB",
            ": 23",
            ": 113",
        ] {
            assert!(!text.contains(word), "{word}");
        }
        assert_eq!(text.matches("model name\t: vCPU\n").count(), 2);
        // two processors, numbered, and the counts say two
        assert!(text.starts_with("processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 0\nmodel\t: 0\nmodel name\t: vCPU\nstepping\t: 0\n"));
        assert!(text.contains("\n\nprocessor\t: 1\n"));
        assert_eq!(text.matches("siblings\t: 2\n").count(), 2);
        for dropped in ["cpu MHz", "cpuid level", "address sizes", "4139"] {
            assert!(!text.contains(dropped), "{dropped}");
        }
        assert_eq!(text.matches("cpu cores\t: 2\n").count(), 2);
        assert!(text.contains("core id\t: 1\n") && text.contains("apicid\t: 1\n"));
        // what programs choose code paths by stays
        assert_eq!(
            text.matches("flags\t: fpu vme de pse tsc msr sse sse2 ht syscall nx\n")
                .count(),
            2
        );
        assert!(text.ends_with("\n\n"));
        assert!(is_masked_cpuinfo(&text));
        assert!(!is_masked_cpuinfo(HOST));
        assert!(!is_masked_cpuinfo(""));
        // one honest processor among masked ones is not masked
        let mixed =
            alloc::format!("{text}model name\t: Example Corp Model 9000 8-Core Processor\n");
        assert!(!is_masked_cpuinfo(&mixed));
    }

    #[test]
    fn a_field_nobody_named_is_not_passed_on() {
        // a kernel that grows a new field: it does not reach the container
        let host = "processor\t: 0\nmodel name\t: X\nserial number\t: ABC123\nflags\t: fpu\n\n";
        let text = cpuinfo(host, 1).unwrap();
        assert_eq!(text, "processor\t: 0\nmodel name\t: vCPU\nflags\t: fpu\n\n");
        // not a cpuinfo, or a count that makes no sense: nothing is made up
        assert_eq!(cpuinfo("", 2), None);
        assert_eq!(cpuinfo("model name\t: X\n", 2), None);
        assert_eq!(cpuinfo(HOST, 0), None);
        assert_eq!(cpuinfo(HOST, CORES_MAX + 1), None);
        assert!(cpuinfo(HOST, CORES_MAX).is_some());
    }

    #[test]
    fn what_the_runtime_passes_is_checked() {
        assert_eq!(valid_ct_name("113"), Some(113));
        for bad in ["", "99", "1e3", "-113", "113 ", "../113", "1234567890"] {
            assert_eq!(valid_ct_name(bad), None, "{bad:?}");
        }
        let conf =
            "arch: amd64\ncores: 4\ncpulimit: 2\nmemory: 1024\n\n[before-upgrade]\ncores: 8\n";
        assert_eq!(conf_cores(conf), Some(4));
        assert_eq!(conf_cores("arch: amd64\n[snap]\ncores: 8\n"), None);
        assert_eq!(conf_cores("cores: many\n"), None);
        assert!(HOOK_LINE.starts_with("lxc.hook.mount: /"));
        // the wrapper cannot fail a start: a time limit, and exit 0 on its last line
        assert!(HOOK_WRAPPER.starts_with("#!/bin/sh\n"));
        assert!(HOOK_WRAPPER.contains("timeout 20 ") && HOOK_WRAPPER.ends_with("\nexit 0\n"));
        assert!(HOOK_LINE.ends_with("/ez2-ct-mask-hook"));
        // the verdict file: only "ok <n>", n above zero, says masked
        assert!(result_is_masked(Some(&result_ok(212))));
        for not in [
            None,
            Some(""),
            Some("ok 0\n"),
            Some("ok\n"),
            Some("NOT MASKED mount\n"),
            Some("ok many"),
        ] {
            assert!(!result_is_masked(not), "{not:?}");
        }
        assert!(EXTRA_HIDE.contains(&"dev/.lxc/proc") && EXTRA_HIDE.contains(&"dev/.lxc/sys"));
        // the processor directory a container sees: its own count and nothing of the host's
        let (files, dirs) = cpu_dir(2).unwrap();
        assert_eq!(
            files,
            [
                ("online", String::from("0-1\n")),
                ("possible", String::from("0-1\n")),
                ("present", String::from("0-1\n")),
            ]
        );
        assert_eq!(dirs, ["cpu0", "cpu1"]);
        assert_eq!(cpu_dir(1).unwrap().0[0].1, "0\n");
        assert_eq!(cpu_dir(0), None);
        assert_eq!(cpu_dir(CORES_MAX + 1), None);
        assert!(PROC_HIDE.contains(&"mdstat") && PROC_HIDE.contains(&"cmdline"));
        // what a container needs of /proc is not on the list
        for needed in [
            "cpuinfo", "meminfo", "stat", "self", "sys", "mounts", "net", "version",
        ] {
            assert!(!PROC_HIDE.contains(&needed), "{needed}");
        }
    }

    /// Andy 11-10: the customer's AI seat is inside; a list of what to hide is always short.
    #[test]
    fn under_sys_only_what_is_named_stays() {
        // /sys itself, as the first host shows it
        let top = sys_step(
            "",
            &[
                "block",
                "bus",
                "class",
                "dev",
                "devices",
                "firmware",
                "fs",
                "hypervisor",
                "kernel",
                "module",
                "power",
            ],
        );
        assert_eq!(top.descend, ["class", "devices", "fs"]);
        assert_eq!(
            top.cover,
            [
                "block",
                "bus",
                "dev",
                "firmware",
                "hypervisor",
                "kernel",
                "module",
                "power"
            ]
        );
        // the provider's name (dmi), the disks (block) and the PCI tree go; net stays
        let devices = sys_step("devices", &["pci0000:00", "platform", "system", "virtual"]);
        assert_eq!(devices.cover, ["devices/pci0000:00", "devices/platform"]);
        assert_eq!(devices.descend, ["devices/system", "devices/virtual"]);
        let virt = sys_step("devices/virtual", &["block", "dmi", "net", "thermal"]);
        assert_eq!(
            virt.cover,
            [
                "devices/virtual/block",
                "devices/virtual/dmi",
                "devices/virtual/thermal"
            ]
        );
        assert!(virt.descend.is_empty());
        let system = sys_step("devices/system", &["cpu", "memory", "node"]);
        assert_eq!(
            system.cover,
            ["devices/system/memory", "devices/system/node"]
        );
        let class = sys_step("class", &["dmi", "hwmon", "net", "nvme"]);
        assert_eq!(class.cover, ["class/dmi", "class/hwmon", "class/nvme"]);
        let fs = sys_step("fs", &["cgroup", "ext4", "fuse"]);
        assert_eq!(fs.cover, ["fs/ext4"]);
        // something new the kernel grows is covered without anyone adding it
        assert_eq!(sys_step("", &["brand_new"]).cover, ["brand_new"]);
        // a name that only starts like a kept one is not kept
        assert_eq!(sys_step("class", &["net2"]).cover, ["class/net2"]);
        assert_eq!(sys_step("", &["f"]).cover, ["f"]);
    }

    #[test]
    fn a_site_hwaddr_is_local_and_unicast() {
        for raw in [
            [0u8; 6],
            [0xFF; 6],
            [0xBC, 0x24, 0x11, 1, 2, 3],
            [0x01, 0, 0, 0, 0, 0],
        ] {
            let mac = local_hwaddr(raw);
            assert!(hwaddr_is_local(&mac), "{mac}");
            assert_eq!(mac.len(), 17);
        }
        assert_eq!(
            local_hwaddr([0xBC, 0x24, 0x11, 0xAB, 0xCD, 0xEF]),
            "BE:24:11:AB:CD:EF"
        );
        // the platform's own prefix, multicast, and what is not an address
        for bad in [
            "BC:24:11:00:00:01",
            "03:00:00:00:00:01",
            "02:00:00:00:00",
            "02:00:00:00:00:0G",
            "02-00-00-00-00-01",
            "",
        ] {
            assert!(!hwaddr_is_local(bad), "{bad}");
        }
        assert!(hwaddr_is_local("02:00:00:00:00:01"));
        assert!(hwaddr_is_local("ae:00:00:00:00:01"));
    }

    fn cfg(lines: &[(&str, &str)]) -> Vec<(String, String)> {
        lines
            .iter()
            .map(|(k, v)| ((*k).into(), (*v).into()))
            .collect()
    }

    const HOOK: &str = "/usr/local/sbin/ez2-ct-mask-hook";
    const NEW: &str = "02:11:22:33:44:55";

    #[test]
    fn a_masked_ct_needs_no_change() {
        let c = cfg(&[
            ("lxc.hook.mount", HOOK),
            ("net0", "name=eth0,bridge=vmbr1,hwaddr=06:AA:BB:CC:DD:EE,ip=10.77.0.9/24,gw=10.77.0.1,type=veth"),
            ("nameserver", "1.1.1.1"), // ip-literal-ok: well-known public resolver, never a host of ours
            ("searchdomain", "invalid"),
        ]);
        assert_eq!(config_fixes(&c, NEW), Ok(Vec::new()));
    }

    #[test]
    fn what_an_unmasked_ct_needs_is_each_named() {
        let c = cfg(&[
            ("net0", "name=eth0,bridge=vmbr1,hwaddr=BC:24:11:00:00:09,ip=10.77.0.9/24,gw=10.77.0.1,ip6=dhcp,type=veth"),
            ("searchdomain", "tailnet.example"),
        ]);
        let fixes = config_fixes(&c, NEW).unwrap();
        let keys: Vec<&str> = fixes.iter().map(|f| f.key).collect();
        assert_eq!(
            keys,
            ["lxc.hook.mount", "net0", "nameserver", "searchdomain"]
        );
        assert_eq!(fixes[0].value, HOOK);
        assert_eq!(
            fixes[1].value,
            "name=eth0,bridge=vmbr1,hwaddr=02:11:22:33:44:55,ip=10.77.0.9/24,gw=10.77.0.1,type=veth"
        );
        assert_eq!(fixes[2].value, NEUTRAL_RESOLVER);
        assert_eq!(fixes[3].value, "invalid");
    }

    #[test]
    fn a_second_hook_or_a_foreign_one_is_not_the_mask() {
        for hooks in [
            vec![HOOK, HOOK],
            vec!["/tmp/other"],
            vec![HOOK, "/tmp/other"],
        ] {
            let mut lines: Vec<(&str, &str)> =
                hooks.iter().map(|h| ("lxc.hook.mount", *h)).collect();
            lines.push(("net0", "name=eth0,hwaddr=02:00:00:00:00:01,ip=10.77.0.9/24"));
            lines.push(("nameserver", "1.1.1.1")); // ip-literal-ok: well-known public resolver, never a host of ours
            lines.push(("searchdomain", "invalid"));
            let fixes = config_fixes(&cfg(&lines), NEW).unwrap();
            assert_eq!(fixes.len(), 1, "{hooks:?}");
            assert_eq!(fixes[0].key, "lxc.hook.mount");
        }
    }

    #[test]
    fn ipv6_alone_or_no_hwaddr_changes_net0() {
        let v6 = cfg(&[
            ("lxc.hook.mount", HOOK),
            (
                "net0",
                "name=eth0,hwaddr=02:00:00:00:00:01,ip=10.77.0.9/24,gw6=fe80::1",
            ),
            ("nameserver", "1.1.1.1"), // ip-literal-ok: well-known public resolver, never a host of ours
            ("searchdomain", "invalid"),
        ]);
        let f = config_fixes(&v6, NEW).unwrap();
        assert_eq!(f.len(), 1);
        assert_eq!(
            f[0].value,
            "name=eth0,hwaddr=02:00:00:00:00:01,ip=10.77.0.9/24"
        );
        let bare = cfg(&[
            ("lxc.hook.mount", HOOK),
            ("net0", "name=eth0,ip=10.77.0.9/24"),
            ("nameserver", "1.1.1.1"), // ip-literal-ok: well-known public resolver, never a host of ours
            ("searchdomain", "invalid"),
        ]);
        assert_eq!(
            config_fixes(&bare, NEW).unwrap()[0].value,
            "name=eth0,ip=10.77.0.9/24,hwaddr=02:11:22:33:44:55"
        );
        assert_eq!(config_fixes(&cfg(&[]), NEW), Err(PlanError::NoNet0));
    }

    #[test]
    fn the_ct_files_say_only_what_they_must() {
        assert!(SYSCTL_TEXT.contains("user.max_user_namespaces = 0\n"));
        let h = hosts_text("s-abc123").unwrap();
        assert!(h.contains("127.0.1.1\ts-abc123\n"));
        for word in ["PVE", "ts.net", "tail"] {
            assert!(!h.contains(word), "{word}");
        }
        for bad in ["", "-x", "a b", "a\nb", "a.example"] {
            assert!(hosts_text(bad).is_none(), "{bad:?}");
        }
        assert_eq!(CONFIG_STEP.as_str(), "limits");
        assert_eq!(FILES_STEP.as_str(), "identity");
        // resolv.conf: the neutral search domain and resolver, no platform block, and the
        // file that keeps the platform from writing its own
        let rv = resolv_text();
        assert!(rv.contains("\nsearch invalid\n"));
        assert!(rv.contains(&alloc::format!("\nnameserver {NEUTRAL_RESOLVER}\n")));
        assert_eq!(RESOLV_KEEP_FILE, "/etc/.pve-ignore.resolv.conf");
        assert!(MOTD_TEXT.is_empty());
        assert_eq!(ISSUE_TEXT, "\\S\n");
        assert!(ntp_is_public(TIMESYNCD_TEXT));
        assert!(TIMESYNCD_FILE.starts_with("/etc/systemd/timesyncd.conf.d/99-"));
        for text in [
            rv.as_str(),
            MOTD_TEXT,
            ISSUE_TEXT,
            TIMESYNCD_TEXT,
            SYSCTL_TEXT,
            h.as_str(),
        ] {
            for word in ["PVE", "ts.net", "tail", "vendor-x"] {
                assert!(!text.contains(word), "{word} in {text:?}");
            }
        }
        assert_eq!(PLATFORM_FILES, ["/etc/appliance.info"]);
    }

    #[test]
    fn the_search_domain_and_the_time_servers_stay_neutral() {
        // a resolv.conf the platform wrote names it; ours does not
        assert!(!resolv_text().contains("PVE"));
        assert!(resolv_text()
            .lines()
            .filter(|l| l.starts_with("search"))
            .eq(["search invalid"]));
        // the time servers in effect, in file order: an empty value resets the list
        let main = "[Time]\nNTP=ntp1.vendor-x.example\nFallbackNTP=ntp.vendor-x.example\n";
        assert!(!ntp_is_public(main));
        assert!(ntp_is_public(&alloc::format!("{main}{TIMESYNCD_TEXT}")));
        assert!(!ntp_is_public(&alloc::format!("{TIMESYNCD_TEXT}{main}")));
        assert!(ntp_is_public("#NTP=ntp.vendor-x.example\n"));
        assert!(ntp_is_public(
            "NTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org\n"
        ));
        assert!(!ntp_is_public("NTP=pool.ntp.org.vendor-x.example\n"));
    }

    #[test]
    fn a_resolver_of_ours_or_the_hosts_is_not_neutral() {
        for good in [
            "1.1.1.1",         // ip-literal-ok: well-known public resolver, never a host of ours
            "1.1.1.1 9.9.9.9", // ip-literal-ok: well-known public resolver, never a host of ours
            "8.8.8.8",         // ip-literal-ok: well-known public resolver, never a host of ours
        ] {
            assert!(resolver_is_neutral(good), "{good}");
        }
        for bad in [
            "",
            "10.77.0.1",
            "100.100.100.100", // ip-literal-ok: Tailscale MagicDNS address, the kind a mask must refuse; never a host of ours
            "100.64.0.1",
            "192.168.1.1",
            "172.16.0.1",
            "127.0.0.53",
            "169.254.1.1",
            "1.1.1.1 10.77.0.1", // ip-literal-ok: well-known public resolver, never a host of ours
            "2606:4700:4700::1111", // ip-literal-ok: well-known public resolver, never a host of ours
            "0.0.0.0",
            "dns.example",
        ] {
            assert!(!resolver_is_neutral(bad), "{bad}");
        }
        let c = cfg(&[
            ("lxc.hook.mount", HOOK),
            ("net0", "name=eth0,hwaddr=02:00:00:00:00:01,ip=10.77.0.9/24"),
            ("nameserver", "100.100.100.100"), // ip-literal-ok: Tailscale MagicDNS address, the kind a mask must refuse; never a host of ours
            ("searchdomain", "invalid"),
        ]);
        let f = config_fixes(&c, NEW).unwrap();
        assert_eq!(f.len(), 1);
        assert_eq!(
            (f[0].key, f[0].value.as_str()),
            ("nameserver", NEUTRAL_RESOLVER)
        );
    }

    #[test]
    fn another_nic_is_named_for_a_person() {
        let c = cfg(&[
            ("net0", "x"),
            ("net1", "y"),
            ("net12", "z"),
            ("netfoo", "w"),
            ("net", "v"),
        ]);
        assert_eq!(other_nics(&c), ["net1", "net12"]);
        assert!(other_nics(&cfg(&[("net0", "x")])).is_empty());
    }

    #[test]
    fn the_user_namespace_limit_is_read_back() {
        assert_eq!(userns_closed(Some("0\n")), Some(true));
        assert_eq!(userns_closed(Some("63289\n")), Some(false));
        assert_eq!(userns_closed(Some("")), None);
        assert_eq!(userns_closed(None), None);
        assert!(SYSCTL_APPLY.ends_with(SYSCTL_FILE));
    }

    #[test]
    fn a_resolver_that_is_no_public_host_is_not_neutral() {
        for bad in [
            "255.255.255.255",
            "224.0.0.251", // ip-literal-ok: range name / boundary sample, never a host of ours
            "239.1.1.1",   // ip-literal-ok: range name / boundary sample, never a host of ours
            "240.0.0.1",   // ip-literal-ok: range name / boundary sample, never a host of ours
            "250.1.2.3",   // ip-literal-ok: range name / boundary sample, never a host of ours
            "192.0.2.53",
            "198.51.100.1",
            "203.0.113.9",
            "198.18.0.1", // ip-literal-ok: range name / boundary sample, never a host of ours
            "198.19.255.254", // ip-literal-ok: range name / boundary sample, never a host of ours
        ] {
            assert!(!resolver_is_neutral(bad), "{bad}");
        }
        for good in [
            "198.17.0.1",    // ip-literal-ok: range name / boundary sample, never a host of ours
            "198.20.0.1",    // ip-literal-ok: range name / boundary sample, never a host of ours
            "223.255.255.1", // ip-literal-ok: range name / boundary sample, never a host of ours
            "9.9.9.9",       // ip-literal-ok: range name / boundary sample, never a host of ours
        ] {
            assert!(resolver_is_neutral(good), "{good}");
        }
    }
}
