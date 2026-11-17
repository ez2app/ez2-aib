//! T97-2m (043dd, IFACE-T97-2m-043dd-1117.md r1): the only place aibd runs `/bin/launchctl`.
//! SPEC-s107-product r4 §11 platform-tool exception: a fixed absolute path, a fixed argument
//! array built here, never a shell, never PATH. `crate::install` reaches it only through the
//! [`crate::install::Launchd`] trait, so tests use a stand-in and never touch launchd.

use std::path::Path;
use std::process::{Command, Stdio};

use crate::install::{Launchd, LaunchdOut};

/// Fixed path; PATH is never consulted (§11).
pub const LAUNCHCTL: &str = "/bin/launchctl";

/// The real launchd of the logged-in user (`gui/<uid>`).
pub struct Real;

fn run(args: &[&str]) -> LaunchdOut {
    match Command::new(LAUNCHCTL)
        .args(args)
        .stdin(Stdio::null())
        .env_clear()
        .output()
    {
        Ok(o) => LaunchdOut {
            code: o.status.code(),
            stdout: String::from_utf8_lossy(&o.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&o.stderr).into_owned(),
        },
        Err(e) => LaunchdOut {
            code: None,
            stdout: String::new(),
            stderr: format!("{LAUNCHCTL}: {e}"),
        },
    }
}

impl Launchd for Real {
    fn domain(&self) -> String {
        format!("gui/{}", nix::unistd::getuid().as_raw())
    }
    fn bootstrap(&self, plist: &Path) -> LaunchdOut {
        let d = self.domain();
        run(&["bootstrap", &d, &plist.to_string_lossy()])
    }
    fn bootout(&self, label: &str) -> LaunchdOut {
        let t = format!("{}/{label}", self.domain());
        run(&["bootout", &t])
    }
    fn kickstart(&self, label: &str) -> LaunchdOut {
        let t = format!("{}/{label}", self.domain());
        run(&["kickstart", "-k", &t])
    }
    fn print(&self, label: &str) -> LaunchdOut {
        let t = format!("{}/{label}", self.domain());
        run(&["print", &t])
    }
}
