// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host ct mask` — run by the container runtime as a site CT starts
//! (`lxc.hook.mount`, after the lxcfs hook), to cover what the CT would otherwise read of
//! the machine underneath (4.4.1 §23; what and why: `aib_cloud::mask`).
//!
//! The runtime calls it inside the container's mount namespace, before the root is
//! switched: the host's files are still reachable, and what is mounted under
//! `$LXC_ROOTFS_MOUNT` is what the container will see.
//!
//! * `/proc/cpuinfo` is covered by the text `aib_cloud::mask::cpuinfo` makes, with as
//!   many processors as the CT's configuration gives it.
//! * Each directory of `mask::HIDE_DIRS` that exists is covered by an empty directory,
//!   each file of `mask::HIDE_FILES` by an empty file.
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
    match (ns("self"), ns("1")) {
        (Some(ours), Some(hosts)) if ours != hosts => Ok(root),
        (Some(_), Some(_)) => Err("running in the host's mount namespace".to_owned()),
        _ => Err("mount namespace could not be read".to_owned()),
    }
}

/// `root`/`rel` when that is a real path under `root` — no symbolic link anywhere on the
/// way (a link could lead back out to the host) — and of the kind asked for.
fn target(root: &Path, rel: &str, dir: bool) -> Option<PathBuf> {
    let path = root.join(rel);
    let real = fs::canonicalize(&path).ok()?;
    (real == path && real.is_dir() == dir).then_some(real)
}

/// Every directory to cover: [`mask::HIDE_DIRS`] with `pci*` spelled out.
fn hide_dirs(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for rel in mask::HIDE_DIRS {
        match rel.strip_suffix("/pci*") {
            None => out.push((*rel).to_owned()),
            Some(parent) => {
                let mut found: Vec<String> = fs::read_dir(root.join(parent))
                    .into_iter()
                    .flatten()
                    .flatten()
                    .filter_map(|e| e.file_name().into_string().ok())
                    .filter(|n| n.starts_with("pci"))
                    .map(|n| format!("{parent}/{n}"))
                    .collect();
                found.sort();
                out.extend(found);
            }
        }
    }
    out
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
    let (empty_dir, empty_file, cpuinfo) = (
        state.join("empty"),
        state.join("none"),
        state.join("cpuinfo"),
    );
    fs::create_dir_all(&empty_dir).map_err(|e| format!("{}: {e}", empty_dir.display()))?;
    fs::write(&empty_file, "").map_err(|e| format!("{}: {e}", empty_file.display()))?;
    fs::write(&cpuinfo, text).map_err(|e| format!("{}: {e}", cpuinfo.display()))?;

    let mut made = 0;
    let mut first_failure: Option<String> = None;
    let mut cover = |source: &Path, rel: &str, dir: bool| {
        // a path the container does not have needs no cover
        let Some(over) = target(&root, rel, dir) else {
            return;
        };
        match bind(source, &over) {
            Ok(()) => made += 1,
            Err(why) => {
                first_failure.get_or_insert(why);
            }
        }
    };
    cover(&cpuinfo, "proc/cpuinfo", false);
    for rel in hide_dirs(&root) {
        cover(&empty_dir, &rel, true);
    }
    for rel in mask::HIDE_FILES {
        cover(&empty_file, rel, false);
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
        assert!(checked_root("/").is_err());
        assert!(checked_root("relative/path").is_err());
        assert!(checked_root("/nonexistent/aib-mask").is_err());
        assert!(apply("113", "/").is_err());
        assert!(apply("../113", root.to_str().unwrap()).is_err());

        // targets: real paths only, of the right kind
        fs::create_dir_all(root.join("sys/firmware")).unwrap();
        fs::create_dir_all(root.join("proc")).unwrap();
        fs::write(root.join("proc/mdstat"), "x").unwrap();
        assert_eq!(
            target(&root, "sys/firmware", true),
            Some(root.join("sys/firmware"))
        );
        assert_eq!(target(&root, "sys/firmware", false), None);
        assert_eq!(
            target(&root, "proc/mdstat", false),
            Some(root.join("proc/mdstat"))
        );
        assert_eq!(target(&root, "proc/cmdline", false), None);
        // a link in the container's tree that leads out is not followed
        let outside = scratch("outside");
        std::os::unix::fs::symlink(&outside, root.join("sys/class")).unwrap();
        fs::create_dir_all(outside.join("dmi")).unwrap();
        assert_eq!(target(&root, "sys/class/dmi", true), None);
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }

    #[test]
    fn every_pci_root_is_covered() {
        let root = scratch("pci");
        for d in ["pci0000:00", "pci0000:40", "system", "virtual"] {
            fs::create_dir_all(root.join("sys/devices").join(d)).unwrap();
        }
        let dirs = hide_dirs(&root);
        assert!(dirs.contains(&"sys/devices/pci0000:00".to_owned()));
        assert!(dirs.contains(&"sys/devices/pci0000:40".to_owned()));
        assert!(!dirs
            .iter()
            .any(|d| d.contains('*') || d.ends_with("system")));
        assert!(dirs.contains(&"sys/firmware".to_owned()));
        let _ = fs::remove_dir_all(&root);
        // the entry point takes one word and never fails a start
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&[])).is_err());
        assert!(cmd(&args(&["unmask"])).is_err());
    }
}
