//! The one ssh command aibd runs for a host seat's `host_exec` (DESIGN-hostmode-3e53b-1123 r3 §3-4;
//! 8b09a REVIEW-hostnet-pre r2 L5, L6). Built here only, never through a shell:
//!
//! - the system's own OpenSSH by absolute path (Mac `/usr/bin/ssh`, Windows
//!   `<System32>\OpenSSH\ssh.exe` from the system directory the caller read with
//!   `GetSystemDirectoryW`); never looked up on `PATH`, and missing ⇒ refused, no other ssh;
//! - `-F none` (no `~/.ssh/config`), a known-hosts file holding the one listed key under
//!   `HostKeyAlias=<id>`, the global one the null device, `StrictHostKeyChecking=yes`, no proxy,
//!   no forwarding, no agent, the one key file;
//! - the address is the IP aibd checked ([`crate::host_addr`]), never the stored name, after `--`.
//!
//! The pinning is a defence (GUIDE-same-user-ai-threat-model: AI CLI options fixed by aibd);
//! that a host seat cannot change these arguments rests on it having no tools (hostmode §1, D3).

use crate::trusted_hosts::{path_ok, user_ok};
use std::net::IpAddr;
use std::path::{Path, PathBuf};

/// Longest command a host seat may send (hostmode §3).
pub const MAX_COMMAND: usize = 8 * 1024;

/// Which shape of the command: the two differ only in the ssh path, the null device and the
/// connection-sharing options Windows' OpenSSH does not have.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    Mac,
    Windows,
}

impl Os {
    pub fn here() -> Os {
        if cfg!(windows) {
            Os::Windows
        } else {
            Os::Mac
        }
    }

    fn null(self) -> &'static str {
        match self {
            Os::Mac => "/dev/null",
            Os::Windows => "NUL",
        }
    }
}

/// The ssh program: on a Mac `<root>/usr/bin/ssh` (root `/`; a test passes its own), on
/// Windows `<system dir>\OpenSSH\ssh.exe`. Refused when it is not a file there.
pub fn ssh_program(os: Os, base: &Path) -> Result<PathBuf, &'static str> {
    let p = match os {
        Os::Mac => base.join("usr").join("bin").join("ssh"),
        Os::Windows => base.join("OpenSSH").join("ssh.exe"),
    };
    if p.is_file() {
        Ok(p)
    } else {
        Err("ssh_missing")
    }
}

/// This computer's own ssh: `/usr/bin/ssh` on a Mac; on Windows `OpenSSH\\ssh.exe` under the
/// system directory the OS reports ([`aib_sys::sysdir::system_dir`]), never `%SystemRoot%` or
/// `PATH` (3e53b DEEP2 T1). Unreadable or missing ⇒ refused.
pub fn system_ssh() -> Result<PathBuf, &'static str> {
    match Os::here() {
        Os::Mac => ssh_program(Os::Mac, Path::new("/")),
        Os::Windows => {
            let dir = aib_sys::sysdir::system_dir().ok_or("system_dir_unreadable")?;
            ssh_program(Os::Windows, &dir)
        }
    }
}

/// A managed host as `host_exec` connects to it.
pub struct Target<'a> {
    /// The list id: the known-hosts alias.
    pub id: &'a str,
    /// The address [`crate::host_addr`] checked for this connection.
    pub ip: IpAddr,
    pub port: u16,
    pub user: &'a str,
    pub key: &'a Path,
}

fn id_ok(s: &str) -> bool {
    (1..=32).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        && !s.starts_with('-')
}

/// A key path: absolute, and nothing ssh would expand (`%`, a leading `~`) or stop at.
/// Blanks are allowed: it travels as its own argument after `-i`, not inside `-o`.
fn key_ok(p: &Path) -> bool {
    let Some(s) = p.to_str() else {
        return false;
    };
    p.is_absolute()
        && !s.starts_with('~')
        && !s.starts_with('-')
        && !s.chars().any(|c| c.is_control() || c == '%')
}

/// The arguments after the program, the command last. `known_hosts` sits inside `-o`, so it
/// must be one word ([`path_ok`]); a place with a blank is refused, not quoted (Windows OpenSSH
/// quoting is not measured yet: hostmode W5).
pub fn args(
    os: Os,
    t: &Target<'_>,
    known_hosts: &Path,
    command: &str,
) -> Result<Vec<String>, &'static str> {
    if !id_ok(t.id) {
        return Err("id");
    }
    if t.port == 0 {
        return Err("port");
    }
    if !user_ok(t.user) {
        return Err("user");
    }
    if !key_ok(t.key) {
        return Err("key_path");
    }
    if !path_ok(known_hosts) {
        return Err("known_hosts_path");
    }
    if command.is_empty() || command.len() > MAX_COMMAND || command.contains('\0') {
        return Err("command");
    }
    let mut a: Vec<String> = ["-F", "none", "-T", "-e", "none"]
        .into_iter()
        .map(String::from)
        .collect();
    let mut opts = vec![
        "BatchMode=yes".to_owned(),
        "StrictHostKeyChecking=yes".into(),
        "UpdateHostKeys=no".into(),
        "CheckHostIP=no".into(),
        format!("HostKeyAlias={}", t.id),
        format!("UserKnownHostsFile={}", known_hosts.display()),
        format!("GlobalKnownHostsFile={}", os.null()),
        "ForwardAgent=no".into(),
        "ForwardX11=no".into(),
        "ClearAllForwardings=yes".into(),
        "PermitLocalCommand=no".into(),
        "ProxyCommand=none".into(),
        "ProxyJump=none".into(),
        "IdentitiesOnly=yes".into(),
        "IdentityAgent=none".into(),
        "ConnectTimeout=20".into(),
        "ServerAliveInterval=15".into(),
        "ServerAliveCountMax=4".into(),
    ];
    if os == Os::Mac {
        opts.push("ControlMaster=no".into());
        opts.push("ControlPath=none".into());
    }
    for o in opts {
        a.push("-o".into());
        a.push(o);
    }
    a.push("-i".into());
    a.push(t.key.display().to_string());
    a.push("-p".into());
    a.push(t.port.to_string());
    a.push("-l".into());
    a.push(t.user.to_owned());
    a.push("--".into());
    a.push(t.ip.to_string());
    a.push(command.to_owned());
    Ok(a)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn target<'a>(key: &'a Path) -> Target<'a> {
        Target {
            id: "lin-mini",
            ip: "10.0.0.5".parse().unwrap(),
            port: 2222,
            user: "andy",
            key,
        }
    }

    fn opt(a: &[String], o: &str) -> bool {
        a.windows(2).any(|w| w[0] == "-o" && w[1] == o)
    }

    /// H7 (hostmode §5): every pinned option, the checked IP after `--`, nothing from the
    /// stored name; a user or key that reads as an option is refused.
    #[test]
    fn the_ssh_command_is_pinned_and_goes_to_the_checked_address() {
        let key = Path::new("/Users/x/.ssh/id_ed25519");
        let kh = Path::new("/Users/x/Library/Application Support/app.Ez2.AIB/run/hk/a1");
        // a known-hosts place with a blank is refused (it sits inside -o)
        assert_eq!(
            args(Os::Mac, &target(key), kh, "uptime"),
            Err("known_hosts_path"),
            "reason: blank_in_option"
        );
        let kh = Path::new("/Users/x/aibd/run/hk/a1");
        let a = args(Os::Mac, &target(key), kh, "uptime; df -h").unwrap();
        assert_eq!(&a[..5], ["-F", "none", "-T", "-e", "none"], "reason: head");
        for o in [
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "UpdateHostKeys=no",
            "CheckHostIP=no",
            "HostKeyAlias=lin-mini",
            "UserKnownHostsFile=/Users/x/aibd/run/hk/a1",
            "GlobalKnownHostsFile=/dev/null",
            "ForwardAgent=no",
            "ForwardX11=no",
            "ClearAllForwardings=yes",
            "PermitLocalCommand=no",
            "ProxyCommand=none",
            "ProxyJump=none",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "ControlMaster=no",
            "ControlPath=none",
        ] {
            assert!(opt(&a, o), "reason: option_missing {o}");
        }
        let n = a.len();
        assert_eq!(
            &a[n - 9..],
            [
                "-i",
                "/Users/x/.ssh/id_ed25519",
                "-p",
                "2222",
                "-l",
                "andy",
                "--",
                "10.0.0.5",
                "uptime; df -h"
            ],
            "reason: tail"
        );
        // the command is one argument at the end: nothing before `--` came from it
        assert_eq!(a.iter().filter(|w| *w == "--").count(), 1);
        // a user, an id or a key that reads as an option
        let mut t = target(key);
        t.user = "-oProxyCommand=x";
        assert_eq!(args(Os::Mac, &t, kh, "id"), Err("user"));
        let mut t = target(key);
        t.id = "-x";
        assert_eq!(args(Os::Mac, &t, kh, "id"), Err("id"));
        let bad = Path::new("-oProxyCommand=x");
        assert_eq!(args(Os::Mac, &target(bad), kh, "id"), Err("key_path"));
        let tilde = Path::new("~/.ssh/id");
        assert_eq!(args(Os::Mac, &target(tilde), kh, "id"), Err("key_path"));
        let pct = Path::new("/Users/%u/key");
        assert_eq!(args(Os::Mac, &target(pct), kh, "id"), Err("key_path"));
        // the command: present, bounded, no NUL
        assert_eq!(args(Os::Mac, &target(key), kh, ""), Err("command"));
        let long = "x".repeat(MAX_COMMAND + 1);
        assert_eq!(args(Os::Mac, &target(key), kh, &long), Err("command"));
        assert_eq!(args(Os::Mac, &target(key), kh, "a\0b"), Err("command"));
        let mut t = target(key);
        t.port = 0;
        assert_eq!(args(Os::Mac, &t, kh, "id"), Err("port"));
        // an IPv6 address is written bare after `--` (no brackets, no port in it)
        let mut t = target(key);
        t.ip = "fd00::5".parse().unwrap();
        assert_eq!(
            args(Os::Mac, &t, kh, "id")
                .unwrap()
                .iter()
                .rev()
                .nth(1)
                .unwrap(),
            "fd00::5"
        );
    }

    /// W1 (hostmode §5): the Windows shape, checked on every platform: NUL, no connection
    /// sharing, a key path with backslashes and a blank carried as one argument.
    #[test]
    fn the_windows_command_has_its_own_null_device_and_no_connection_sharing() {
        let key = Path::new(r"C:\Users\A B\.ssh\id_ed25519");
        let kh = Path::new(r"C:\Users\AB\AppData\Local\Ez2\AIB\run\hk\a1");
        let t = target(key);
        let a = args(Os::Windows, &t, kh, "hostname");
        // Path::is_absolute knows only this platform's form: on a Mac a C:\ path is relative
        if cfg!(windows) {
            let a = a.unwrap();
            assert!(opt(&a, "GlobalKnownHostsFile=NUL"), "reason: null_device");
            assert!(opt(
                &a,
                r"UserKnownHostsFile=C:\Users\AB\AppData\Local\Ez2\AIB\run\hk\a1"
            ));
            assert!(
                !a.iter().any(|w| w.starts_with("Control")),
                "reason: control_on_windows"
            );
            let i = a.iter().position(|w| w == "-i").unwrap();
            assert_eq!(
                a[i + 1],
                r"C:\Users\A B\.ssh\id_ed25519",
                "reason: key_split"
            );
        } else {
            assert_eq!(a, Err("key_path"), "reason: windows_path_on_mac");
            let a = args(Os::Windows, &target(Path::new("/k")), Path::new("/kh"), "x").unwrap();
            assert!(opt(&a, "GlobalKnownHostsFile=NUL"), "reason: null_device");
            assert!(
                !a.iter().any(|w| w.starts_with("Control")),
                "reason: control_on_windows"
            );
        }
    }

    /// DEEP2 T1: the ssh aibd runs is found from the OS, not the environment.
    #[test]
    fn this_computers_ssh_is_found_from_the_os_not_the_environment() {
        let r = system_ssh();
        if cfg!(windows) {
            let p = r.expect("reason: windows_ssh_not_found");
            assert!(
                p.to_string_lossy()
                    .to_ascii_lowercase()
                    .ends_with("system32\\openssh\\ssh.exe"),
                "reason: not_system32 {p:?}"
            );
        } else if Path::new("/usr/bin/ssh").is_file() {
            assert_eq!(r, Ok(PathBuf::from("/usr/bin/ssh")), "reason: not_usr_bin");
        } else {
            assert_eq!(r, Err("ssh_missing"));
        }
    }

    /// W2 (hostmode §5): the ssh program is the system's, by absolute path; missing ⇒ refused
    /// and nothing on PATH is tried.
    #[test]
    fn the_ssh_program_is_the_systems_and_never_one_on_path() {
        let root = tempfile::tempdir().unwrap();
        assert_eq!(ssh_program(Os::Mac, root.path()), Err("ssh_missing"));
        assert_eq!(ssh_program(Os::Windows, root.path()), Err("ssh_missing"));
        // a fake ssh on PATH is not looked at
        let on_path = root.path().join("bin");
        std::fs::create_dir_all(&on_path).unwrap();
        std::fs::write(on_path.join("ssh"), b"").unwrap();
        assert_eq!(ssh_program(Os::Mac, root.path()), Err("ssh_missing"));
        let mac = root.path().join("usr").join("bin");
        std::fs::create_dir_all(&mac).unwrap();
        std::fs::write(mac.join("ssh"), b"").unwrap();
        assert_eq!(ssh_program(Os::Mac, root.path()), Ok(mac.join("ssh")));
        let win = root.path().join("OpenSSH");
        std::fs::create_dir_all(&win).unwrap();
        // a folder named ssh.exe is not the program
        std::fs::create_dir_all(win.join("ssh.exe")).unwrap();
        assert_eq!(ssh_program(Os::Windows, root.path()), Err("ssh_missing"));
    }
}
