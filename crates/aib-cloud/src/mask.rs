// SPDX-License-Identifier: Apache-2.0
//! What a site's container is **not** shown of the machine underneath (4.4.1 §23: the
//! plan does not disclose the physical host; Andy 11-10: "CPU 型號先遮、能遮的預設全遮").
//!
//! Measured in a site CT on the first host (11-10, read-only): the CPU's model name, the
//! provider's name and the board's model in DMI, the BIOS vendor, the disks' model, the
//! RAID layout, the host's boot command line and its hardware sensors were all readable.
//!
//! This module says what is put in their place; the host shell does the mounting when
//! the container starts. No I/O.
//!
//! * `/proc/cpuinfo` is rebuilt from an **allowlist** of fields: the model name becomes
//!   [`CPU_MODEL`], family / model / stepping become 0, and a field not named here is
//!   dropped (microcode, cache size, bugs, power management …). The feature flags stay:
//!   programs choose code paths by them.
//! * The directories in [`HIDE_DIRS`] are covered by an empty read-only directory and
//!   the files in [`HIDE_FILES`] by an empty file.
//!
//! **What this cannot hide**, and so does not claim to: the kernel's release string
//! (`uname`), and whatever a program asks the processor itself (the CPUID instruction
//! answers with the real brand string). This keeps the machine from being named to
//! someone looking around, not from someone measuring.

use alloc::string::String;
use alloc::vec::Vec;

/// The model name a container sees.
pub const CPU_MODEL: &str = "Virtual CPU";

/// Most processors one container is given a `cpuinfo` for.
pub const CORES_MAX: u32 = 256;

/// Paths under the container's root covered by an empty read-only directory: firmware
/// tables and DMI (provider, board, BIOS), the PCI tree (disk and controller models,
/// sensors), block devices and their classes. `pci*` stands for every PCI root.
pub const HIDE_DIRS: &[&str] = &[
    "sys/firmware",
    "sys/devices/virtual/dmi",
    "sys/class/dmi",
    "sys/devices/pci*",
    "sys/devices/virtual/block",
    "sys/block",
    "sys/class/block",
    "sys/class/nvme",
    "sys/class/scsi_host",
    "sys/class/hwmon",
    "proc/scsi",
];

/// Paths under the container's root covered by an empty file: the RAID layout and the
/// host's boot command line.
pub const HIDE_FILES: &[&str] = &["proc/mdstat", "proc/cmdline"];

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

    // the first processor of the first host, flags shortened
    const HOST: &str = "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 23\nmodel\t\t: 113\nmodel name\t: AMD Ryzen 5 3600 6-Core Processor\nstepping\t: 0\nmicrocode\t: 0x8701034\ncpu MHz\t\t: 4139.606\ncache size\t: 512 KB\nphysical id\t: 0\nsiblings\t: 12\ncore id\t\t: 0\ncpu cores\t: 6\napicid\t\t: 0\ninitial apicid\t: 0\nfpu\t\t: yes\nfpu_exception\t: yes\ncpuid level\t: 16\nwp\t\t: yes\nflags\t\t: fpu vme de pse tsc msr sse sse2 ht syscall nx\nbugs\t\t: sysret_ss_attrs spectre_v1 spectre_v2 retbleed\nbogomips\t: 7199.73\nTLB size\t: 3072 4K pages\nclflush size\t: 64\ncache_alignment\t: 64\naddress sizes\t: 43 bits physical, 48 bits virtual\npower management: ts ttp tm hwpstate\n\nprocessor\t: 1\nmodel name\t: AMD Ryzen 5 3600 6-Core Processor\n\n";

    #[test]
    fn the_machine_is_not_named_in_cpuinfo() {
        let text = cpuinfo(HOST, 2).unwrap();
        for word in [
            "Ryzen",
            "AMD R",
            "3600",
            "0x8701034",
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
        assert_eq!(text.matches("model name\t: Virtual CPU\n").count(), 2);
        // two processors, numbered, and the counts say two
        assert!(text.starts_with("processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 0\nmodel\t: 0\nmodel name\t: Virtual CPU\nstepping\t: 0\n"));
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
        let mixed = alloc::format!("{text}model name\t: AMD Ryzen 5 3600 6-Core Processor\n");
        assert!(!is_masked_cpuinfo(&mixed));
    }

    #[test]
    fn a_field_nobody_named_is_not_passed_on() {
        // a kernel that grows a new field: it does not reach the container
        let host = "processor\t: 0\nmodel name\t: X\nserial number\t: ABC123\nflags\t: fpu\n\n";
        let text = cpuinfo(host, 1).unwrap();
        assert_eq!(
            text,
            "processor\t: 0\nmodel name\t: Virtual CPU\nflags\t: fpu\n\n"
        );
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
        // the lists say what they say: the provider's name lives under these
        assert!(
            HIDE_DIRS.contains(&"sys/devices/virtual/dmi") && HIDE_DIRS.contains(&"sys/firmware")
        );
        assert!(HIDE_FILES.contains(&"proc/mdstat"));
        assert!(HOOK_LINE.starts_with("lxc.hook.mount: /"));
    }
}
