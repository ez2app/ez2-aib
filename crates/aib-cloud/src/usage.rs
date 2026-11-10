// SPDX-License-Identifier: Apache-2.0
//! What a site is really using, read from the files the kernel keeps for its container
//! (cgroup v2). The host shell reads the files; this module makes numbers of them for
//! [`crate::arc`]. No I/O.
//!
//! Measured on the first host (11-10): an idle site showed `memory.current` of 650–1100
//! MiB and a `memory.peak` equal to its limit, while what it could not give back was
//! 150–200 MiB. The rest is file cache, which the kernel fills up to the limit and drops
//! on demand. So memory is judged on the **working set** — `memory.current` minus
//! `inactive_file` — and on the kernel's own out-of-memory count, never on the peak.

/// The value of `key` in a flat `key value` file (`memory.stat`, `cpu.stat`,
/// `memory.events`).
pub fn stat(text: &str, key: &str) -> Option<u64> {
    text.lines().find_map(|l| {
        let (k, v) = l.split_once(' ')?;
        (k == key).then(|| v.trim().parse().ok())?
    })
}

/// Working set in MiB, rounded up: `memory.current` minus the inactive file cache.
pub fn working_set_mb(memory_current: &str, memory_stat: &str) -> Option<u32> {
    let current: u64 = memory_current.trim().parse().ok()?;
    let inactive = stat(memory_stat, "inactive_file")?;
    u32::try_from(current.saturating_sub(inactive).div_ceil(1024 * 1024)).ok()
}

/// Processes the kernel killed in the container for lack of memory (`memory.events`).
pub fn oom_kills(memory_events: &str) -> Option<u32> {
    stat(memory_events, "oom_kill").map(|n| u32::try_from(n).unwrap_or(u32::MAX))
}

/// CPU used between two readings of `cpu.stat`, in hundredths of a core. `None` when a
/// reading is missing, the counter went backwards (the container restarted) or no time
/// passed.
pub fn cpu_x100(before: &str, after: &str, elapsed_usec: u64) -> Option<u32> {
    let a = stat(before, "usage_usec")?;
    let b = stat(after, "usage_usec")?;
    if elapsed_usec == 0 || b < a {
        return None;
    }
    u32::try_from((u128::from(b - a) * 100).div_ceil(u128::from(elapsed_usec))).ok()
}

/// Size in GiB out of a `rootfs` value of `pct config` (`local-lvm:vm-112-disk-0,size=16G`).
pub fn rootfs_gb(rootfs: &str) -> Option<u32> {
    rootfs
        .split(',')
        .find_map(|p| p.strip_prefix("size="))?
        .strip_suffix('G')?
        .parse()
        .ok()
}

/// Used space in GiB, rounded up, from a thin volume's size in GiB and its `data_percent`
/// as `lvs` prints it (`13.26`).
pub fn used_gb(size_gb: u32, data_percent: &str) -> Option<u32> {
    let (whole, frac) = data_percent
        .trim()
        .split_once('.')
        .unwrap_or((data_percent.trim(), "0"));
    let frac2 = alloc::format!("{frac:0<2}");
    let x100 = whole.parse::<u64>().ok()? * 100 + frac2.get(..2)?.parse::<u64>().ok()?;
    if x100 > 10_000 {
        return None;
    }
    u32::try_from((u64::from(size_gb) * x100).div_ceil(10_000)).ok()
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    // shaped like what the first host showed for an idle site
    const STAT: &str = "anon 158334976\nfile 906194944\nkernel 41275392\ninactive_file 712343552\nactive_file 193851392\n";

    #[test]
    fn flat_files() {
        assert_eq!(stat(STAT, "anon"), Some(158_334_976));
        assert_eq!(stat(STAT, "file"), Some(906_194_944));
        // a key that is the prefix of another is not that other
        assert_eq!(stat(STAT, "inactive"), None);
        assert_eq!(stat("usage_usec x\n", "usage_usec"), None);
        assert_eq!(stat("", "anon"), None);
    }

    #[test]
    fn the_working_set_leaves_the_cache_out() {
        // current 1084 MiB, of which 679 MiB is cache the kernel can drop
        assert_eq!(working_set_mb("1136656384\n", STAT), Some(405));
        // a counter read a moment apart can be larger than current: not negative
        assert_eq!(working_set_mb("100", STAT), Some(0));
        assert_eq!(working_set_mb("", STAT), None);
        assert_eq!(working_set_mb("1136656384", "anon 1\n"), None);
    }

    #[test]
    fn oom_and_cpu() {
        assert_eq!(
            oom_kills("low 0\nhigh 0\nmax 12\noom 1\noom_kill 3\n"),
            Some(3)
        );
        assert_eq!(oom_kills("low 0\n"), None);
        let before = "usage_usec 1000000\nuser_usec 1\nnr_throttled 0\n";
        let after = "usage_usec 6000000\nuser_usec 1\nnr_throttled 0\n";
        // five CPU-seconds in ten seconds: half a core
        assert_eq!(cpu_x100(before, after, 10_000_000), Some(50));
        assert_eq!(cpu_x100(before, after, 2_500_000), Some(200));
        assert_eq!(cpu_x100(before, before, 10_000_000), Some(0));
        // restarted in between, or no time passed
        assert_eq!(cpu_x100(after, before, 10_000_000), None);
        assert_eq!(cpu_x100(before, after, 0), None);
        assert_eq!(cpu_x100("", after, 10), None);
    }

    #[test]
    fn disk() {
        assert_eq!(rootfs_gb("local-lvm:vm-112-disk-0,size=16G"), Some(16));
        assert_eq!(
            rootfs_gb("local-lvm:vm-112-disk-0,mountoptions=noatime,size=256G"),
            Some(256)
        );
        assert_eq!(rootfs_gb("local-lvm:vm-112-disk-0"), None);
        assert_eq!(rootfs_gb("local-lvm:vm-112-disk-0,size=512M"), None);
        // 16 GiB at 13.26 % and 16.46 %: 2.1 and 2.6 GiB, rounded up
        assert_eq!(used_gb(16, "13.26"), Some(3));
        assert_eq!(used_gb(16, "  16.46\n"), Some(3));
        assert_eq!(used_gb(256, "80.00"), Some(205));
        assert_eq!(used_gb(16, "0.00"), Some(0));
        assert_eq!(used_gb(16, "100.00"), Some(16));
        assert_eq!(used_gb(16, "5"), Some(1));
        assert_eq!(used_gb(16, "101.00"), None);
        assert_eq!(used_gb(16, ""), None);
    }
}
