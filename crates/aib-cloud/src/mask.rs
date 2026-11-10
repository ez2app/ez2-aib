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
//!   users, which is what a customer's key and seat are given.

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
    "devices",
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

/// The line in a container's configuration that runs the mask when it starts.
pub const HOOK_LINE: &str = "lxc.hook.mount: /usr/local/sbin/aib-cloud-host ct mask";

/// What a field of the host's `cpuinfo` becomes for processor `i` of `cores`. `None`
/// drops the field: only what is named here is passed on.
fn field(key: &str, value: &str, i: u32, cores: u32) -> Option<String> {
    let number = |n: u32| Some(alloc::format!("{n}"));
    match key {
        "processor" | "core id" | "apicid" | "initial apicid" => number(i),
        "model name" => Some(CPU_MODEL.into()),
        "cpu family" | "model" | "stepping" | "physical id" => number(0),
        "siblings" | "cpu cores" => number(cores),
        "vendor_id" | "cpu MHz" | "fpu" | "fpu_exception" | "cpuid level" | "wp" | "flags"
        | "clflush size" | "cache_alignment" | "address sizes" => Some(value.into()),
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

/// What the container sees as `/sys/devices/system/cpu`, for `cores` processors: files
/// (name, content) and the names of the per-processor directories. Programs count
/// processors from `online` and from the `cpuN` entries; nothing else of the host's
/// directory is carried over.
pub fn cpu_dir(cores: u32) -> Option<(Vec<(&'static str, String)>, Vec<String>)> {
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
}
