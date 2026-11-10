// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host ct mask` — run by the container runtime as a site CT starts
//! (`lxc.hook.mount`, after the lxcfs hook), to cover what the CT would otherwise read of
//! the machine underneath (4.4.1 §23; what and why: `aib_cloud::mask`).
//!
//! The runtime calls it inside the container's mount namespace, before the root is
//! switched: the host's files are still reachable, and what is mounted under
//! `$LXC_ROOTFS_MOUNT` is what the container will see.
//!
//! * `/proc/cpuinfo` is covered by the text `aib_cloud::mask::cpuinfo` makes, and
//!   `/sys/devices/system/cpu` by the directory `mask::cpu_dir` describes, both with as
//!   many processors as the CT's configuration gives it.
//! * Under `/sys` everything but `mask::SYS_KEEP` is covered, and under `/proc` every
//!   entry of `mask::PROC_HIDE`: a directory by an empty directory, a file by an empty
//!   file. A symbolic link is never mounted over (it could lead out of the container).
//!
//! **It never stops a site from starting**: when something cannot be covered it says
//! `NOT MASKED` on standard error (the runtime logs it) and still exits 0. Whether a
//! running CT is masked is for a check to tell, not for this hook to enforce by keeping a
//! customer's site down.
//!
//! Limits, stated plainly: the covers are mounts inside the CT's own namespace, so the
//! CT's root can take them off again; they hold against the site's users. And see
//! `aib_cloud::mask` for what no mount can cover.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use aib_cloud::mask;

use crate::probe::run_quiet;

const STATE: &str = "/run/ez2-cloud/mask";
const PVE_CONF: &str = "/etc/pve/lxc";

pub fn cmd(args: &[String]) -> Result<u8, String> {
    match args.first().map(String::as_str) {
        // the runtime appends its own words (name, section, hook type): not read
        Some("mask") => {
            let name = std::env::var("LXC_NAME").unwrap_or_default();
            let rootfs = std::env::var("LXC_ROOTFS_MOUNT").unwrap_or_default();
            match apply(&name, &rootfs) {
                Ok(n) => eprintln!("aib-cloud-host ct mask: {name}: {n} covers in place"),
                Err(why) => eprintln!("aib-cloud-host ct mask: {name}: NOT MASKED: {why}"),
            }
            Ok(0)
        }
        _ => Err(
            "ct needs: mask (run by the container runtime, see aib_cloud::mask::HOOK_LINE)"
                .to_owned(),
        ),
    }
}

/// Where the container's root is being put together, checked: an existing directory,
/// given as an absolute path, that is not the host's root — and we must be in a mount
/// namespace other than the host's, or every cover below would land on the host itself.
fn checked_root(rootfs: &str) -> Result<PathBuf, String> {
    if !rootfs.starts_with('/') {
        return Err("LXC_ROOTFS_MOUNT is not an absolute path".to_owned());
    }
    let root = fs::canonicalize(rootfs).map_err(|e| format!("{rootfs}: {e}"))?;
    if root == Path::new("/") || !root.is_dir() {
        return Err(format!("{rootfs} is not a container root"));
    }
    let ns = |pid: &str| fs::read_link(format!("/proc/{pid}/ns/mnt")).ok();
    own_namespace(ns("self").as_deref(), ns("1").as_deref())?;
    Ok(root)
}

/// Our mount namespace against the host's (that of process 1): they must both be known
/// and must differ.
fn own_namespace(ours: Option<&Path>, hosts: Option<&Path>) -> Result<(), String> {
    match (ours, hosts) {
        (Some(ours), Some(hosts)) if ours != hosts => Ok(()),
        (Some(_), Some(_)) => Err("running in the host's mount namespace".to_owned()),
        _ => Err("mount namespace could not be read".to_owned()),
    }
}

/// `root`/`rel` when that is a real path under `root` — no symbolic link anywhere on the
/// way (a link could lead back out to the host) — and whether it is a directory.
fn target(root: &Path, rel: &str) -> Option<(PathBuf, bool)> {
    let path = root.join(rel);
    let real = fs::canonicalize(&path).ok()?;
    (real == path).then(|| {
        let dir = real.is_dir();
        (real, dir)
    })
}

/// The names in a directory, sorted; none when it cannot be read.
fn names(dir: &Path) -> Vec<String> {
    let mut found: Vec<String> = fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .collect();
    found.sort();
    found
}

/// Everything to cover under `/sys` (as paths below the container's root): walk from the
/// top, asking [`mask::sys_step`] at each directory that leads to something kept.
fn sys_covers(root: &Path) -> Vec<String> {
    let mut cover = Vec::new();
    let mut open = vec![String::new()];
    while let Some(dir) = open.pop() {
        let listing = names(&root.join("sys").join(&dir));
        let refs: Vec<&str> = listing.iter().map(String::as_str).collect();
        let step = mask::sys_step(&dir, &refs);
        cover.extend(step.cover.into_iter().map(|p| format!("sys/{p}")));
        open.extend(step.descend);
    }
    cover.sort();
    cover
}

fn bind(source: &Path, over: &Path) -> Result<(), String> {
    let mut cmd = Command::new("mount");
    cmd.arg("-n").arg("--bind").arg(source).arg(over);
    match run_quiet(&mut cmd, Duration::from_secs(5)) {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => Err(format!("mount over {}: {status}", over.display())),
        Err(e) => Err(format!("mount over {}: {e}", over.display())),
    }
}

/// Put the covers in place; the number of mounts made. Everything that can be covered is
/// covered even when one of them fails; the first failure is what is reported.
fn apply(name: &str, rootfs: &str) -> Result<usize, String> {
    let ct = mask::valid_ct_name(name).ok_or("LXC_NAME is not a container id")?;
    let root = checked_root(rootfs)?;
    let conf =
        fs::read_to_string(format!("{PVE_CONF}/{ct}.conf")).map_err(|e| format!("conf: {e}"))?;
    let cores = mask::conf_cores(&conf).ok_or("no cores: in the container's configuration")?;
    let host = fs::read_to_string("/proc/cpuinfo").map_err(|e| format!("cpuinfo: {e}"))?;
    let text = mask::cpuinfo(&host, cores).ok_or("the host's cpuinfo could not be rebuilt")?;

    let state = PathBuf::from(format!("{STATE}/{ct}"));
    let (empty_dir, empty_file, cpuinfo, cpu_dir) = (
        state.join("empty"),
        state.join("none"),
        state.join("cpuinfo"),
        state.join("cpu"),
    );
    fs::create_dir_all(&empty_dir).map_err(|e| format!("{}: {e}", empty_dir.display()))?;
    fs::write(&empty_file, "").map_err(|e| format!("{}: {e}", empty_file.display()))?;
    fs::write(&cpuinfo, text).map_err(|e| format!("{}: {e}", cpuinfo.display()))?;
    // the processor directory, rebuilt from nothing each start (the count may have changed)
    let (files, dirs) = mask::cpu_dir(cores).ok_or("no processor directory for that count")?;
    let _ = fs::remove_dir_all(&cpu_dir);
    for d in &dirs {
        fs::create_dir_all(cpu_dir.join(d)).map_err(|e| format!("{}: {e}", cpu_dir.display()))?;
    }
    for (name, content) in &files {
        fs::write(cpu_dir.join(name), content)
            .map_err(|e| format!("{}: {e}", cpu_dir.display()))?;
    }

    let mut made = 0;
    let mut first_failure: Option<String> = None;
    let mut cover = |rel: &str, with: Option<&Path>| {
        // a path the container does not have, or a link, gets no cover
        let Some((over, dir)) = target(&root, rel) else {
            return;
        };
        let source = with.unwrap_or(if dir {
            empty_dir.as_path()
        } else {
            empty_file.as_path()
        });
        match bind(source, &over) {
            Ok(()) => made += 1,
            Err(why) => {
                first_failure.get_or_insert(why);
            }
        }
    };
    cover("proc/cpuinfo", Some(cpuinfo.as_path()));
    cover("sys/devices/system/cpu", Some(cpu_dir.as_path()));
    for rel in sys_covers(&root) {
        cover(&rel, None);
    }
    for name in mask::PROC_HIDE {
        cover(&format!("proc/{name}"), None);
    }
    match first_failure {
        None => Ok(made),
        Some(why) => Err(why),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("aib-mask-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::canonicalize(dir).unwrap()
    }

    #[test]
    fn nothing_is_mounted_on_the_host_or_through_a_link() {
        // run from a test we are in the host's own mount namespace: refused before any mount
        let root = scratch("root");
        let why = checked_root(root.to_str().unwrap()).unwrap_err();
        assert!(
            why.contains("mount namespace"),
            "a test process is not inside a container: {why}"
        );
        // the rule itself, with made-up namespaces: both known, and different
        let (a, b) = (Path::new("mnt:[4026531841]"), Path::new("mnt:[4026532999]"));
        assert_eq!(own_namespace(Some(b), Some(a)), Ok(()));
        assert!(own_namespace(Some(a), Some(a)).is_err());
        assert!(own_namespace(None, Some(a)).is_err());
        assert!(own_namespace(Some(a), None).is_err());
        assert!(checked_root("/").is_err());
        assert!(checked_root("relative/path").is_err());
        assert!(checked_root("/nonexistent/aib-mask").is_err());
        assert!(apply("113", "/").is_err());
        assert!(apply("../113", root.to_str().unwrap()).is_err());

        // targets: real paths only, and what kind they are
        fs::create_dir_all(root.join("sys/firmware")).unwrap();
        fs::create_dir_all(root.join("proc")).unwrap();
        fs::write(root.join("proc/mdstat"), "x").unwrap();
        assert_eq!(
            target(&root, "sys/firmware"),
            Some((root.join("sys/firmware"), true))
        );
        assert_eq!(
            target(&root, "proc/mdstat"),
            Some((root.join("proc/mdstat"), false))
        );
        assert_eq!(target(&root, "proc/cmdline"), None);
        // a link in the container's tree that leads out is not followed
        let outside = scratch("outside");
        std::os::unix::fs::symlink(&outside, root.join("sys/class")).unwrap();
        fs::create_dir_all(outside.join("dmi")).unwrap();
        assert_eq!(target(&root, "sys/class/dmi"), None);
        assert_eq!(target(&root, "sys/class"), None);
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }

    #[test]
    fn the_walk_under_sys_covers_all_but_what_is_kept() {
        let root = scratch("sys");
        for d in [
            "sys/firmware/dmi",
            "sys/block",
            "sys/devices/pci0000:00/0000:00:01.1/nvme",
            "sys/devices/pci0000:40",
            "sys/devices/system/cpu/cpu0",
            "sys/devices/system/node/node0",
            "sys/devices/virtual/dmi/id",
            "sys/devices/virtual/net/eth0",
            "sys/class/net",
            "sys/class/hwmon",
            "sys/fs/cgroup/system.slice",
            "sys/fs/ext4/dm-13",
        ] {
            fs::create_dir_all(root.join(d)).unwrap();
        }
        assert_eq!(
            sys_covers(&root),
            [
                "sys/block",
                "sys/class/hwmon",
                "sys/devices/pci0000:00",
                "sys/devices/pci0000:40",
                "sys/devices/system/node",
                "sys/devices/virtual/dmi",
                "sys/firmware",
                "sys/fs/ext4",
            ]
        );
        let _ = fs::remove_dir_all(&root);
        // the entry point takes one word and never fails a start
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&[])).is_err());
        assert!(cmd(&args(&["unmask"])).is_err());
    }
}
