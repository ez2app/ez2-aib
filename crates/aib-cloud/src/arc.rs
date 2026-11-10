// SPDX-License-Identifier: Apache-2.0
//! ARC — automatic resource management, first version (4.4.1 §23; Andy 11-10: the plan
//! table is to hold on the first host). What a customer buys is an **envelope**: at most
//! 8 vCPU, 16 GB of memory, 256 GB of disk and 5 TB of traffic a month, shared by all of
//! that customer's sites. Each site is opened from the template on the smallest tier and
//! is given more as it shows it needs more, out of what is left of the envelope. The
//! customer adjusts nothing and is never shown what the machine underneath is. No I/O
//! here: the caller measures.
//!
//! What this version decides, and what it leaves alone:
//! * **It only raises.** Lowering a limit under a running site, and shrinking a disk
//!   (which cannot be done at all), are a person's decision.
//! * **Never past the plan** — counted over all of the customer's sites together — and
//!   **never past what the host can back**: a raise needs that much free on the host
//!   *after* the reserve the host keeps for itself. The plan's numbers are ceilings shown
//!   to the customer, not reservations — two sites cannot both be given the last free
//!   gigabytes.
//! * A site that is hot **at its ceiling**, or hot while **the host is full**, is not a
//!   raise but a finding for a person ([`Advice::AtCeiling`], [`Advice::HostFull`]).
//! * One step at a time: each call proposes at most one raise per resource, sized so a
//!   site that keeps growing is followed, not guessed ahead of.

use alloc::vec::Vec;

use crate::site::Limits;
use crate::verify::Outcome;

/// What the plan promises, as ceilings.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Plan {
    /// vCPUs.
    pub cores: u32,
    /// Memory in MiB.
    pub memory_mb: u32,
    /// Disk in GiB.
    pub disk_gb: u32,
    /// Traffic per calendar month, in bytes.
    pub traffic_bytes: u64,
}

/// The plan sold on the first host.
pub const PLAN: Plan = Plan {
    cores: 8,
    memory_mb: 16_384,
    disk_gb: 256,
    traffic_bytes: 5_000_000_000_000,
};

/// What a site was seen using over the look-back window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Usage {
    /// CPU in hundredths of a core, 95th percentile.
    pub cpu_x100_p95: u32,
    /// Memory the site cannot give back, in MiB: its working set
    /// ([`crate::usage::working_set_mb`]). **Not** the cgroup's peak or current use: those
    /// count file cache, which fills up to the limit on any site that reads files.
    pub memory_mb_working: u32,
    /// Times the kernel killed a process in the CT for lack of memory.
    pub oom_kills: u32,
    /// Disk used, in GiB.
    pub disk_used_gb: u32,
}

/// What the host can still give, **after** its own reserve.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HostRoom {
    /// CPU in hundredths of a core.
    pub cpu_x100: u32,
    /// Memory in MiB.
    pub memory_mb: u32,
    /// Thin-pool space in GiB.
    pub disk_gb: u32,
}

/// What a customer's sites hold together.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Held {
    /// CPU in hundredths of a core (the sum of the sites' `cpulimit`).
    pub cpu_x100: u32,
    /// Memory in MiB.
    pub memory_mb: u32,
    /// Disk in GiB.
    pub disk_gb: u32,
}

/// Add up what a customer's sites hold: each site's limits and disk size. Sums saturate:
/// an absurd total reads as "over the plan", not as a small number.
pub fn held(sites: &[(Limits, u32)]) -> Held {
    let sum = |f: fn(&(Limits, u32)) -> u32| sites.iter().map(f).fold(0u32, u32::saturating_add);
    Held {
        cpu_x100: sum(|s| s.0.cpulimit_x100),
        memory_mb: sum(|s| s.0.memory_mb),
        disk_gb: sum(|s| s.1),
    }
}

/// A resource ARC looks after.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Resource {
    /// CPU time (and the vCPU count that goes with it).
    Cpu,
    /// Memory.
    Memory,
    /// Disk.
    Disk,
}

/// One thing ARC has to say about a site.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Advice {
    /// Raise CPU to this many hundredths of a core (vCPUs follow: one per started core).
    RaiseCpu {
        /// New `cpulimit` in hundredths.
        cpulimit_x100: u32,
        /// New vCPU count.
        cores: u32,
    },
    /// Raise memory to this many MiB.
    RaiseMemory(u32),
    /// Grow the disk to this many GiB. Cannot be undone.
    GrowDisk(u32),
    /// The site needs more of this and already has all the plan allows.
    AtCeiling(Resource),
    /// The site needs more of this and the host cannot back it.
    HostFull(Resource),
}

fn round_up(n: u32, to: u32) -> u32 {
    n.div_ceil(to) * to
}

/// What ARC proposes for one site. `limits` and `disk_gb` are what the site has now;
/// `held` is what all of the customer's sites hold together, this one included.
pub fn advise(
    plan: &Plan,
    limits: &Limits,
    disk_gb: u32,
    held: &Held,
    usage: &Usage,
    room: &HostRoom,
) -> Vec<Advice> {
    let mut out = Vec::new();
    // what is left of the customer's envelope
    let cpu_left = (plan.cores * 100).saturating_sub(held.cpu_x100);
    let memory_left = plan.memory_mb.saturating_sub(held.memory_mb);
    let disk_left = plan.disk_gb.saturating_sub(held.disk_gb);

    // CPU: busy at 80 % of its limit for the 95th percentile → one more core
    let cpu_max = (limits.cpulimit_x100 + cpu_left).min(plan.cores * 100);
    if u64::from(usage.cpu_x100_p95) * 100 >= u64::from(limits.cpulimit_x100) * 80 {
        out.push(if limits.cpulimit_x100 >= cpu_max {
            Advice::AtCeiling(Resource::Cpu)
        } else {
            let next = (limits.cpulimit_x100 + 100).min(cpu_max);
            if room.cpu_x100 < next - limits.cpulimit_x100 {
                Advice::HostFull(Resource::Cpu)
            } else {
                Advice::RaiseCpu {
                    cpulimit_x100: next,
                    cores: next.div_ceil(100).max(limits.cores),
                }
            }
        });
    }

    // memory: working set at 85 % or the kernel had to kill something → half as much again
    if usage.oom_kills > 0
        || u64::from(usage.memory_mb_working) * 100 >= u64::from(limits.memory_mb) * 85
    {
        let memory_max = (limits.memory_mb + memory_left).min(plan.memory_mb);
        out.push(if limits.memory_mb >= memory_max {
            Advice::AtCeiling(Resource::Memory)
        } else {
            let next = round_up(limits.memory_mb + limits.memory_mb / 2, 512).min(memory_max);
            if room.memory_mb < next - limits.memory_mb {
                Advice::HostFull(Resource::Memory)
            } else {
                Advice::RaiseMemory(next)
            }
        });
    }

    // disk: 80 % full → a quarter more, at least 8 GiB
    if disk_gb > 0 && u64::from(usage.disk_used_gb) * 100 >= u64::from(disk_gb) * 80 {
        let disk_max = (disk_gb + disk_left).min(plan.disk_gb);
        out.push(if disk_gb >= disk_max {
            Advice::AtCeiling(Resource::Disk)
        } else {
            let next = (disk_gb + (disk_gb / 4).max(8)).min(disk_max);
            if room.disk_gb < next - disk_gb {
                Advice::HostFull(Resource::Disk)
            } else {
                Advice::GrowDisk(next)
            }
        });
    }
    out
}

/// The limits a site has after the raises in `advice` (findings change nothing). The
/// result is checked like any other limits before it is used.
pub fn apply(limits: &Limits, advice: &[Advice]) -> Limits {
    let mut next = limits.clone();
    for a in advice {
        match *a {
            Advice::RaiseCpu {
                cpulimit_x100,
                cores,
            } => {
                next.cpulimit_x100 = cpulimit_x100;
                next.cores = cores;
            }
            Advice::RaiseMemory(mb) => next.memory_mb = mb,
            Advice::GrowDisk(_) | Advice::AtCeiling(_) | Advice::HostFull(_) => {}
        }
    }
    next
}

/// Where a site stands against its monthly traffic: warn from 80 %, red from 100 %.
/// `used` is `None` when the counters could not be read.
pub fn traffic(plan: &Plan, used: Option<u64>) -> Outcome {
    match used {
        None => Outcome::Unknown("unread"),
        Some(u) if u >= plan.traffic_bytes => Outcome::Red("traffic_over_plan"),
        Some(u) if u128::from(u) * 100 >= u128::from(plan.traffic_bytes) * 80 => {
            Outcome::Warn("traffic_80")
        }
        Some(_) => Outcome::Green,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const TRIAL: Limits = Limits::TEST_TIER; // 2 vCPU, cpulimit 200, 2048 MiB
    const ROOMY: HostRoom = HostRoom {
        cpu_x100: 800,
        memory_mb: 32_768,
        disk_gb: 300,
    };
    const QUIET: Usage = Usage {
        cpu_x100_p95: 20,
        memory_mb_working: 600,
        oom_kills: 0,
        disk_used_gb: 3,
    };

    /// A customer with this one site.
    fn solo(limits: &Limits, disk_gb: u32) -> Held {
        held(&[(limits.clone(), disk_gb)])
    }

    fn usage(f: impl FnOnce(&mut Usage)) -> Usage {
        let mut u = QUIET;
        f(&mut u);
        u
    }

    #[test]
    fn a_quiet_site_is_left_alone() {
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &QUIET, &ROOMY),
            []
        );
    }

    #[test]
    fn a_busy_site_is_raised_one_step_per_resource() {
        let hot = usage(|u| {
            u.cpu_x100_p95 = 160;
            u.memory_mb_working = 1800;
            u.disk_used_gb = 20;
        });
        let advice = advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &hot, &ROOMY);
        assert_eq!(
            advice,
            [
                Advice::RaiseCpu {
                    cpulimit_x100: 300,
                    cores: 3
                },
                Advice::RaiseMemory(3072),
                Advice::GrowDisk(32),
            ]
        );
        let next = apply(&TRIAL, &advice);
        assert_eq!(
            (next.cores, next.cpulimit_x100, next.memory_mb),
            (3, 300, 3072)
        );
        next.validate().unwrap();
        // just under each threshold: nothing
        let warm = usage(|u| {
            u.cpu_x100_p95 = 159;
            u.memory_mb_working = 1740;
            u.disk_used_gb = 19;
        });
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &warm, &ROOMY),
            []
        );
        // an out-of-memory kill counts whatever the peak says
        let oom = usage(|u| u.oom_kills = 1);
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &oom, &ROOMY),
            [Advice::RaiseMemory(3072)]
        );
    }

    #[test]
    fn never_past_the_plan() {
        let top = Limits {
            cores: 8,
            cpulimit_x100: 800,
            memory_mb: 16_384,
            ..TRIAL
        };
        let hot = Usage {
            cpu_x100_p95: 790,
            memory_mb_working: 16_000,
            oom_kills: 3,
            disk_used_gb: 250,
        };
        assert_eq!(
            advise(&PLAN, &top, 256, &solo(&top, 256), &hot, &ROOMY),
            [
                Advice::AtCeiling(Resource::Cpu),
                Advice::AtCeiling(Resource::Memory),
                Advice::AtCeiling(Resource::Disk),
            ]
        );
        assert_eq!(
            apply(
                &top,
                &advise(&PLAN, &top, 256, &solo(&top, 256), &hot, &ROOMY)
            ),
            top
        );
        // the last step stops at the ceiling, it does not overshoot
        let near = Limits {
            cores: 8,
            cpulimit_x100: 750,
            memory_mb: 12_288,
            ..TRIAL
        };
        let advice = advise(&PLAN, &near, 240, &solo(&near, 240), &hot, &ROOMY);
        assert_eq!(
            advice,
            [
                Advice::RaiseCpu {
                    cpulimit_x100: 800,
                    cores: 8
                },
                Advice::RaiseMemory(16_384),
                Advice::GrowDisk(256),
            ]
        );
        apply(&near, &advice).validate().unwrap();
    }

    #[test]
    fn never_past_what_the_host_can_back() {
        let hot = usage(|u| {
            u.cpu_x100_p95 = 200;
            u.memory_mb_working = 2048;
            u.disk_used_gb = 23;
        });
        let full = HostRoom {
            cpu_x100: 99,
            memory_mb: 1023,
            disk_gb: 7,
        };
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &hot, &full),
            [
                Advice::HostFull(Resource::Cpu),
                Advice::HostFull(Resource::Memory),
                Advice::HostFull(Resource::Disk),
            ]
        );
        let just = HostRoom {
            cpu_x100: 100,
            memory_mb: 1024,
            disk_gb: 8,
        };
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &hot, &just).len(),
            3
        );
        assert!(advise(&PLAN, &TRIAL, 24, &solo(&TRIAL, 24), &hot, &just)
            .iter()
            .all(|a| !matches!(a, Advice::HostFull(_))));
    }

    /// The plan is the customer's, shared by their sites (Andy 11-10).
    #[test]
    fn sites_of_one_customer_share_the_envelope() {
        // three sites: two on the trial tier, one already grown; together 7 cores, 14 GiB, 200 GiB
        let grown = Limits {
            cores: 3,
            cpulimit_x100: 300,
            memory_mb: 10_240,
            ..TRIAL
        };
        let all = held(&[(TRIAL, 24), (TRIAL, 24), (grown.clone(), 152)]);
        assert_eq!(
            all,
            Held {
                cpu_x100: 700,
                memory_mb: 14_336,
                disk_gb: 200
            }
        );
        let hot = Usage {
            cpu_x100_p95: 200,
            memory_mb_working: 2048,
            oom_kills: 0,
            disk_used_gb: 23,
        };
        // a trial site may take what is left: one core, 2 GiB (not the usual +1024 → 3072
        // is fine, it fits), and disk within the 56 GiB left
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &all, &hot, &ROOMY),
            [
                Advice::RaiseCpu {
                    cpulimit_x100: 300,
                    cores: 3
                },
                Advice::RaiseMemory(3072),
                Advice::GrowDisk(32),
            ]
        );
        // once the envelope is spent, a hot site is a finding, however small it is itself
        let spent = Held {
            cpu_x100: 800,
            memory_mb: 16_384,
            disk_gb: 256,
        };
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &spent, &hot, &ROOMY),
            [
                Advice::AtCeiling(Resource::Cpu),
                Advice::AtCeiling(Resource::Memory),
                Advice::AtCeiling(Resource::Disk),
            ]
        );
        // a raise is cut to what is left: 512 MiB of memory, 4 GiB of disk, half a core
        let nearly = Held {
            cpu_x100: 750,
            memory_mb: 15_872,
            disk_gb: 252,
        };
        assert_eq!(
            advise(&PLAN, &TRIAL, 24, &nearly, &hot, &ROOMY),
            [
                Advice::RaiseCpu {
                    cpulimit_x100: 250,
                    cores: 3
                },
                Advice::RaiseMemory(2560),
                Advice::GrowDisk(28),
            ]
        );
        // totals that overflow read as spent
        let absurd = held(&[
            (
                Limits {
                    memory_mb: u32::MAX,
                    ..TRIAL
                },
                u32::MAX,
            ),
            (TRIAL, 24),
        ]);
        assert_eq!((absurd.memory_mb, absurd.disk_gb), (u32::MAX, u32::MAX));
    }

    #[test]
    fn it_only_ever_raises() {
        // idle on generous limits: no advice lowers anything
        let big = Limits {
            cores: 6,
            cpulimit_x100: 600,
            memory_mb: 8192,
            ..TRIAL
        };
        assert_eq!(
            advise(&PLAN, &big, 128, &solo(&big, 128), &QUIET, &ROOMY),
            []
        );
        // a site with more vCPUs than its limit needs keeps them
        let wide = Limits {
            cores: 4,
            cpulimit_x100: 200,
            ..TRIAL
        };
        let hot = usage(|u| u.cpu_x100_p95 = 200);
        assert_eq!(
            advise(&PLAN, &wide, 24, &solo(&wide, 24), &hot, &ROOMY),
            [Advice::RaiseCpu {
                cpulimit_x100: 300,
                cores: 4
            }]
        );
    }

    #[test]
    fn traffic_against_the_month() {
        let tb = 1_000_000_000_000_u64;
        assert_eq!(traffic(&PLAN, Some(0)), Outcome::Green);
        assert_eq!(traffic(&PLAN, Some(4 * tb - 1)), Outcome::Green);
        assert_eq!(traffic(&PLAN, Some(4 * tb)), Outcome::Warn("traffic_80"));
        assert_eq!(
            traffic(&PLAN, Some(5 * tb - 1)),
            Outcome::Warn("traffic_80")
        );
        assert_eq!(
            traffic(&PLAN, Some(5 * tb)),
            Outcome::Red("traffic_over_plan")
        );
        assert_eq!(traffic(&PLAN, None), Outcome::Unknown("unread"));
    }
}
