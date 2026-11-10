// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site usage [--records dir] [--thin vg/pool] [--sample-seconds N] [--json]`
//! — what every site is really using and what ARC would do about it (4.4.1 §23). Reads
//! the containers' cgroup files from the host side (nothing is run inside a CT), `pct
//! config`, `lvs` and `/proc`; prints usage and advice. **Nothing is changed**: applying a
//! raise is a write and is not part of this command.
//!
//! Sites are grouped by the `tenant_id` their record names (the site itself when it names
//! none): the plan's envelope is the customer's. CPU is a short sample between two
//! readings, not yet the 95th percentile of a long window; that needs a sampler.

use std::collections::BTreeMap;
use std::fs;
use std::process::Command;
use std::time::{Duration, Instant};

use aib_cloud::arc::{self, Advice, HostRoom, Usage};
use aib_cloud::site::{record_tenant, spec_from_record, Limits};
use aib_cloud::usage;
use serde_json::{json, Value};

use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const THIN_POOL: &str = "vg0/data";
/// What the host keeps for itself before anything is offered to a site.
const RESERVE_CPU_X100: u32 = 200;
const RESERVE_MEMORY_MB: u32 = 8192;
const RESERVE_DISK_PERCENT: u32 = 20;

fn read(path: &str) -> Option<String> {
    fs::read_to_string(path).ok()
}

fn observe(program: &str, args: &[&str]) -> Option<String> {
    let mut cmd = Command::new(program);
    cmd.args(args).env("LC_ALL", "C");
    run_capture(&mut cmd, Duration::from_secs(10)).ok()
}

/// `MemAvailable` of `/proc/meminfo` in MiB.
pub fn mem_available_mb(meminfo: &str) -> Option<u32> {
    let kb = meminfo
        .lines()
        .find_map(|l| l.strip_prefix("MemAvailable:"))?
        .trim()
        .strip_suffix("kB")?
        .trim()
        .parse::<u64>()
        .ok()?;
    u32::try_from(kb / 1024).ok()
}

/// One-minute load in hundredths (`/proc/loadavg`).
pub fn load1_x100(loadavg: &str) -> Option<u32> {
    let (whole, frac) = loadavg.split_whitespace().next()?.split_once('.')?;
    (frac.len() == 2).then_some(())?;
    whole
        .parse::<u32>()
        .ok()?
        .checked_mul(100)?
        .checked_add(frac.parse().ok()?)
}

/// What the host can still give: free minus the reserve, never negative.
pub fn host_room(
    threads: u32,
    load_x100: u32,
    available_mb: u32,
    pool_gb: u32,
    pool_used_gb: u32,
) -> HostRoom {
    HostRoom {
        cpu_x100: (threads * 100)
            .saturating_sub(load_x100)
            .saturating_sub(RESERVE_CPU_X100),
        memory_mb: available_mb.saturating_sub(RESERVE_MEMORY_MB),
        disk_gb: pool_gb
            .saturating_sub(pool_used_gb)
            .saturating_sub(pool_gb * RESERVE_DISK_PERCENT / 100),
    }
}

struct Site {
    /// Logical volume of the root disk (`vm-112-disk-0`), from `pct config`.
    volume: Option<String>,
    code: String,
    owner: String,
    ct: u32,
    limits: Limits,
    disk_gb: u32,
    cpu_before: Option<String>,
    usage: Option<Usage>,
}

fn cgroup(ct: u32, file: &str) -> Option<String> {
    read(&format!("/sys/fs/cgroup/lxc/{ct}/{file}"))
}

/// `lvs` size in GiB and data percent of one volume (`  16.00g 13.26`).
fn lv(name: &str) -> Option<(u32, String)> {
    let out = observe(
        "lvs",
        &[
            "--noheadings",
            "--units",
            "g",
            "-o",
            "lv_size,data_percent",
            name,
        ],
    )?;
    let mut f = out.split_whitespace();
    // lvs marks a rounded size with a leading '<'
    let size = f
        .next()?
        .trim_start_matches('<')
        .strip_suffix('g')?
        .split('.')
        .next()?
        .parse()
        .ok()?;
    Some((size, f.next()?.to_owned()))
}

/// The host's room right now; `None` when any part of it could not be read.
fn measure_room(pool: &str) -> Option<HostRoom> {
    let threads = u32::try_from(std::thread::available_parallelism().ok()?.get()).ok()?;
    let load = load1_x100(&read("/proc/loadavg")?)?;
    let available = mem_available_mb(&read("/proc/meminfo")?)?;
    let (pool_gb, pool_pct) = lv(pool)?;
    let pool_used = usage::used_gb(pool_gb, &pool_pct)?;
    Some(host_room(threads, load, available, pool_gb, pool_used))
}

fn advice_json(a: &Advice) -> Value {
    match *a {
        Advice::RaiseCpu {
            cpulimit_x100,
            cores,
        } => json!({ "raise": "cpu", "cpulimit_x100": cpulimit_x100, "cores": cores }),
        Advice::RaiseMemory(mb) => json!({ "raise": "memory", "memory_mb": mb }),
        Advice::GrowDisk(gb) => json!({ "raise": "disk", "disk_gb": gb }),
        Advice::AtCeiling(r) => {
            json!({ "finding": "at_plan_ceiling", "resource": format!("{r:?}") })
        }
        Advice::HostFull(r) => json!({ "finding": "host_full", "resource": format!("{r:?}") }),
    }
}

/// Exit status: 0 all quiet, 2 ARC has something to say, 3 something could not be
/// measured. A records directory that cannot be read, a broken record or a mistyped
/// argument is **3 as well**: the caller's exit status for an error is 2, which here
/// would read as "there is advice" (c477a, #631 seg. 7 M1; the same as `site verify`).
pub fn cmd(args: &[String]) -> Result<u8, String> {
    match run(args) {
        Ok(code) => Ok(code),
        Err(why) => {
            eprintln!("aib-cloud-host site usage: {why}");
            Ok(3)
        }
    }
}

fn run(args: &[String]) -> Result<u8, String> {
    let mut records = RECORDS.to_owned();
    let mut pool = THIN_POOL.to_owned();
    let mut seconds: u64 = 5;
    let mut as_json = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--thin" => pool = it.next().ok_or("--thin needs <vg>/<pool>")?.clone(),
            "--sample-seconds" => {
                seconds = it
                    .next()
                    .and_then(|s| s.parse().ok())
                    .filter(|n| (1..=60).contains(n))
                    .ok_or("--sample-seconds needs 1..=60")?;
            }
            "--json" => as_json = true,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if pool.starts_with('-')
        || !pool
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"/._-".contains(&c))
    {
        return Err(format!("--thin {pool:?} is not a pool name"));
    }
    let vg = pool.split('/').next().unwrap_or_default().to_owned();

    let mut sites: Vec<Site> = Vec::new();
    for entry in fs::read_dir(&records).map_err(|e| format!("{records}: {e}"))? {
        let path = entry.map_err(|e| format!("{records}: {e}"))?.path();
        if path.extension().is_none_or(|x| x != "json") {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rec: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let spec = spec_from_record(&rec).map_err(|e| format!("{}: {e}", path.display()))?;
        let owner = record_tenant(&rec)
            .ok_or_else(|| format!("{}: tenant_id is not a customer code", path.display()))?
            .to_owned();
        // what the CT has is what is judged, not what the record hopes: limits from pct config
        let config = observe("pct", &["config", &spec.ct.to_string()]);
        let get = |key: &str| {
            config.as_deref().and_then(|c| {
                c.lines()
                    .find_map(|l| l.strip_prefix(key)?.strip_prefix(':'))
                    .map(|v| v.trim().to_owned())
            })
        };
        let disk_gb = get("rootfs")
            .as_deref()
            .and_then(usage::rootfs_gb)
            .unwrap_or(0);
        let mut limits = spec.limits.clone();
        if let Some(m) = get("memory").and_then(|v| v.parse().ok()) {
            limits.memory_mb = m;
        }
        if let Some(c) = get("cores").and_then(|v| v.parse().ok()) {
            limits.cores = c;
        }
        // the volume's own name, as the CT's config gives it: not every host names it alike
        let volume = get("rootfs").and_then(|r| {
            let name = r.split(',').next()?.split_once(':')?.1.to_owned();
            (!name.is_empty()
                && name
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c)))
            .then_some(name)
        });
        sites.push(Site {
            volume,
            code: spec.code.clone(),
            owner,
            ct: spec.ct,
            limits,
            disk_gb,
            cpu_before: cgroup(spec.ct, "cpu.stat"),
            usage: None,
        });
    }
    if sites.is_empty() {
        return Err(format!("{records}: no site records"));
    }
    sites.sort_by(|a, b| a.code.cmp(&b.code));

    let t0 = Instant::now();
    // a short pause between the two CPU readings; this is a one-shot command on the
    // host, not the daemon ADR 0005 is about
    #[allow(clippy::disallowed_methods)]
    std::thread::sleep(Duration::from_secs(seconds));
    let elapsed = u64::try_from(t0.elapsed().as_micros()).unwrap_or(u64::MAX);

    for s in &mut sites {
        let cpu = s
            .cpu_before
            .as_deref()
            .zip(cgroup(s.ct, "cpu.stat").as_deref())
            .and_then(|(b, a)| usage::cpu_x100(b, a, elapsed));
        let working = cgroup(s.ct, "memory.current")
            .zip(cgroup(s.ct, "memory.stat"))
            .and_then(|(c, st)| usage::working_set_mb(&c, &st));
        let oom = cgroup(s.ct, "memory.events").and_then(|e| usage::oom_kills(&e));
        let disk = s
            .volume
            .as_deref()
            .and_then(|v| lv(&format!("{vg}/{v}")))
            .and_then(|(size, pct)| usage::used_gb(size, &pct));
        // every part must have been read: half a measurement is not a basis for advice
        s.usage = match (cpu, working, oom, disk) {
            (Some(cpu_x100_p95), Some(memory_mb_working), Some(oom_kills), Some(disk_used_gb)) => {
                Some(Usage {
                    cpu_x100_p95,
                    memory_mb_working,
                    oom_kills,
                    disk_used_gb,
                })
            }
            _ => None,
        };
    }

    let room = measure_room(&pool);

    let mut by_owner: BTreeMap<&str, Vec<(Limits, u32)>> = BTreeMap::new();
    for s in &sites {
        by_owner
            .entry(s.owner.as_str())
            .or_default()
            .push((s.limits.clone(), s.disk_gb));
    }
    let mut unread = false;
    let mut findings = false;
    let mut out: Vec<Value> = Vec::new();
    for s in &sites {
        let held = arc::held(&by_owner[s.owner.as_str()]);
        let advice = match (&s.usage, &room) {
            (Some(u), Some(r)) => Some(arc::advise(&arc::PLAN, &s.limits, s.disk_gb, &held, u, r)),
            _ => None,
        };
        unread |= advice.is_none();
        findings |= advice.as_ref().is_some_and(|a| !a.is_empty());
        out.push(json!({
            "site": s.code, "tenant_id": s.owner, "ct": s.ct,
            "limits": { "cores": s.limits.cores, "cpulimit_x100": s.limits.cpulimit_x100,
                        "memory_mb": s.limits.memory_mb, "disk_gb": s.disk_gb },
            "usage": s.usage.map(|u| json!({ "cpu_x100": u.cpu_x100_p95,
                "memory_mb_working": u.memory_mb_working, "oom_kills": u.oom_kills,
                "disk_used_gb": u.disk_used_gb })),
            "tenant_holds": { "cpu_x100": held.cpu_x100, "memory_mb": held.memory_mb, "disk_gb": held.disk_gb },
            "advice": advice.map(|a| a.iter().map(advice_json).collect::<Vec<_>>()),
        }));
    }
    let doc = json!({
        "sample_seconds": seconds,
        "host_room": room.map(|r| json!({ "cpu_x100": r.cpu_x100, "memory_mb": r.memory_mb, "disk_gb": r.disk_gb })),
        "sites": out,
    });
    if as_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
        );
    } else {
        for s in doc["sites"].as_array().into_iter().flatten() {
            eprintln!(
                "{} ({}): usage {} advice {}",
                s["site"], s["tenant_id"], s["usage"], s["advice"]
            );
        }
        eprintln!("host room: {}", doc["host_room"]);
    }
    // 3 something could not be measured, 2 ARC has something to say, 0 all quiet
    Ok(if unread {
        3
    } else if findings {
        2
    } else {
        0
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn host_numbers() {
        let meminfo = "MemTotal:       65763540 kB\nMemFree:        55788544 kB\nMemAvailable:   62572544 kB\nBuffers: 1 kB\n";
        assert_eq!(mem_available_mb(meminfo), Some(61_106));
        assert_eq!(mem_available_mb("MemTotal: 1 kB\n"), None);
        assert_eq!(load1_x100("3.85 3.85 6.35 1/234 5678\n"), Some(385));
        assert_eq!(load1_x100("95.10 1 1"), Some(9510));
        assert_eq!(load1_x100(""), None);
        assert_eq!(load1_x100("high"), None);
    }

    #[test]
    fn the_host_keeps_its_reserve() {
        // the first host on 11-10: 12 threads, load 3.85, 61 GiB available, 377 GiB pool, 10 used
        let r = host_room(12, 385, 61_106, 377, 10);
        assert_eq!(
            r,
            HostRoom {
                cpu_x100: 615,
                memory_mb: 52_914,
                disk_gb: 292
            }
        );
        // a busy, full host offers nothing, and never a negative number
        let r = host_room(12, 1300, 4000, 377, 350);
        assert_eq!(
            r,
            HostRoom {
                cpu_x100: 0,
                memory_mb: 0,
                disk_gb: 0
            }
        );
    }

    #[test]
    fn nothing_measured_is_exit_3() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        // never 2: in this command 2 says "ARC has advice"
        for bad in [
            &["--records", "/nonexistent/aib-usage"][..],
            &["--sample-seconds", "0"],
            &["--sample-seconds", "61"],
            &["--thin", "-o"],
            &["--apply"],
        ] {
            assert_eq!(cmd(&args(bad)), Ok(3), "{bad:?}");
        }
    }
}
