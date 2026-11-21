//! N-webonly 第 4 件, b4297's half, slice R3: aibd's own key for the test machines and the
//! ssh that says one line to one of them ([`crate::receipt_runner::Link`]).
//!
//! First version of the key, as 5d33a agreed it 11-21 (PREREAD-receiptrunner, five
//! conditions): a plain ed25519 key file `<data>/trusted/delegate_ed25519`, made by
//! `ssh-keygen` the first time the switch is on.
//!
//! - Who it keeps out: a sandboxed seat, which cannot read aibd's data folder. Who it does
//!   not: a seat with no sandbox on the same account, which can read the file — and already
//!   has the account's own ssh keys, a whole shell on the same machines. This key is less
//!   than that: on a test machine its `authorized_keys` line is
//!   `restrict,command="<gate>",from="<this Mac>"`, so whatever it asks, only the receipt
//!   gate answers, only from this machine, and what the gate runs is sandboxed.
//! - It is never under `~/.ssh` and never in an agent: ssh gets it by path with
//!   `IdentityAgent=none` and `IdentitiesOnly=yes` ([`crate::trusted_hosts::ssh_args_file_key`]).
//! - The private key is read by ssh alone. aibd reads the `.pub` file; what it records and
//!   shows is [`key_id`], never key material.
//! - Replacing it: remove both files (aibd makes a new pair at the next start with the
//!   switch on) and replace the line on each machine; deleting that line on a machine cuts
//!   this Mac off from it at once.
//!
//! [`authorized_line`] is the line the owner pastes on a machine. aibd writes it nowhere on
//! its own: it is shown for the steps page.

use crate::receipt_runner::Link;
use crate::trusted_hosts::TrustedHost;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The key's file name under `<data>/trusted/`.
pub const KEY: &str = "delegate_ed25519";
/// The comment of the key: what the line on a machine ends with (and what is removed to cut
/// this Mac off).
pub const COMMENT: &str = "aibd-delegate";
/// The known-hosts file aibd writes for these connections.
pub const KNOWN_HOSTS: &str = "delegate_known_hosts";
/// The most a machine's answer is read (an `.out` of 1 MB and its first line).
pub const MAX_ANSWER: u64 = 1024 * 1024 + 64;
/// How long ssh waits for the connection itself.
const CONNECT_SECS: u32 = 20;

fn trusted(data: &Path) -> PathBuf {
    data.join(crate::trusted_hosts::DIR)
}

pub fn key_path(data: &Path) -> PathBuf {
    trusted(data).join(KEY)
}

/// A public key line as `ssh-keygen` writes it for this key: `ssh-ed25519 <base64> <comment>`.
pub fn public_ok(line: &str) -> bool {
    let mut w = line.trim_end().split(' ');
    let (kind, blob, comment, more) = (w.next(), w.next(), w.next(), w.next());
    kind == Some("ssh-ed25519")
        && blob.is_some_and(|b| {
            (40..=120).contains(&b.len())
                && b.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'+' || c == b'/' || c == b'=')
        })
        && comment == Some(COMMENT)
        && more.is_none()
}

/// What aibd records and shows of the key: the first 16 hex of the SHA-256 of its public
/// blob's text. Not key material, and enough to tell two keys apart.
pub fn key_id(public_line: &str) -> String {
    let blob = public_line.split(' ').nth(1).unwrap_or_default();
    Sha256::digest(blob.as_bytes())
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// A gate path as it is put in a machine's line: absolute, of letters, digits and
/// `/ . _ -` (nothing that would end the quoted `command=`).
fn gate_ok(p: &str) -> bool {
    p.len() > 1
        && p.len() <= 256
        && p.starts_with('/')
        && p.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
}

/// An address for `from=`: one IPv4 or IPv6 address, no pattern, no name.
fn from_ok(ip: &str) -> bool {
    ip.parse::<std::net::IpAddr>().is_ok()
}

/// The line the owner pastes into a test machine's `authorized_keys`: this key may only run
/// the receipt gate (`restrict` also takes forwarding, a terminal and `~/.ssh/rc` away) and
/// only from this Mac's address.
pub fn authorized_line(
    public_line: &str,
    gate: &str,
    from_ip: &str,
) -> Result<String, &'static str> {
    if !public_ok(public_line) {
        return Err("bad_public_key");
    }
    if !gate_ok(gate) {
        return Err("bad_gate_path");
    }
    if !from_ok(from_ip) {
        return Err("bad_from_address");
    }
    Ok(format!(
        "restrict,command=\"{gate}\",from=\"{from_ip}\" {}",
        public_line.trim_end()
    ))
}

#[cfg(unix)]
fn private_file_ok(p: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file() && m.mode() & 0o077 == 0)
}

#[cfg(not(unix))]
fn private_file_ok(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file())
}

/// The key's public line, making the pair first when there is none. `key_unsafe`: the key
/// file is a link or readable by others; `keygen_failed`: no pair could be made;
/// `bad_public_key`: the `.pub` beside it is not this key's.
pub fn ensure_key(data: &Path, keygen: &Path) -> Result<String, &'static str> {
    let dir = trusted(data);
    let key = key_path(data);
    let public = dir.join(format!("{KEY}.pub"));
    if std::fs::symlink_metadata(&key).is_err() {
        std::fs::create_dir_all(&dir).map_err(|_| "keygen_failed")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
        }
        // no passphrase: aibd uses it unattended; nothing of the key is on a command line
        let made = Command::new(keygen)
            .args(["-q", "-t", "ed25519", "-N", "", "-C", COMMENT, "-f"])
            .arg(&key)
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success());
        if !made {
            return Err("keygen_failed");
        }
    }
    if !private_file_ok(&key) {
        return Err("key_unsafe");
    }
    let line = std::fs::read_to_string(&public).map_err(|_| "bad_public_key")?;
    if !public_ok(&line) {
        return Err("bad_public_key");
    }
    Ok(line.trim_end().to_owned())
}

/// The delegate key's ssh.
pub struct SshLink {
    data: PathBuf,
    ssh: PathBuf,
}

impl SshLink {
    /// `data` is aibd's data folder; `ssh` the program (the system's).
    pub fn new(data: PathBuf, ssh: PathBuf) -> Self {
        Self { data, ssh }
    }

    fn known_hosts(&self) -> Option<PathBuf> {
        let list = trusted(&self.data).join(crate::trusted_hosts::FILE);
        let (hosts, _) = crate::trusted_hosts::load(&list).ok()?;
        let p = trusted(&self.data).join(KNOWN_HOSTS);
        std::fs::write(&p, crate::trusted_hosts::known_hosts_text(&hosts)).ok()?;
        Some(p)
    }
}

impl Link for SshLink {
    fn ready(&self) -> bool {
        private_file_ok(&key_path(&self.data)) && self.ssh.is_file()
    }

    fn say(&self, host: &TrustedHost, line: &str, stdin: Option<&Path>, secs: u64) -> Vec<u8> {
        let Some(known) = self.known_hosts() else {
            return Vec::new();
        };
        let key = key_path(&self.data);
        let Ok(args) = crate::trusted_hosts::ssh_args_file_key(host, &known, &key, CONNECT_SECS)
        else {
            return Vec::new();
        };
        let input = match stdin {
            Some(p) => match std::fs::File::open(p) {
                Ok(f) => Stdio::from(f),
                Err(_) => return Vec::new(),
            },
            None => Stdio::null(),
        };
        // the line is one word to ssh: the machine's sshd hands it to the gate whole
        let spawned = Command::new(&self.ssh)
            .args(&args)
            .arg(line)
            .env_clear()
            .stdin(input)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn();
        let Ok(mut child) = spawned else {
            return Vec::new();
        };
        let Some(out) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Vec::new();
        };
        let reader = std::thread::Builder::new()
            .name("aibd-receipt-link".into())
            .spawn(move || {
                let mut bytes = Vec::new();
                let _ = out.take(MAX_ANSWER).read_to_end(&mut bytes);
                bytes
            });
        let limit = std::time::Instant::now() + std::time::Duration::from_secs(secs);
        let op = crate::timers::RECEIPT_LINK_WAIT.begin(Some(limit));
        let mut ended = false;
        loop {
            if matches!(child.try_wait(), Ok(Some(_)) | Err(_)) {
                ended = true;
                break;
            }
            if !op.tick() {
                break;
            }
        }
        if !ended {
            // past its limit: stopped, and what it printed so far is no answer
            let _ = child.kill();
        }
        let _ = child.wait();
        let bytes = reader.ok().and_then(|r| r.join().ok()).unwrap_or_default();
        if ended {
            bytes
        } else {
            Vec::new()
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const PUB: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl aibd-delegate";

    #[test]
    fn the_line_for_a_machine_only_lets_the_gate_answer_this_mac() {
        let line = authorized_line(
            PUB,
            "/Users/owner/.ez2-aib/bin/aib-receipt-gate",
            "100.64.0.7",
        )
        .unwrap();
        assert_eq!(
            line,
            format!("restrict,command=\"/Users/owner/.ez2-aib/bin/aib-receipt-gate\",from=\"100.64.0.7\" {PUB}")
        );
        assert!(
            line.starts_with("restrict,"),
            "reason: shell_or_forwarding_left"
        );
        assert!(line.ends_with(" aibd-delegate"));
        // an address, not a pattern or a name; a path that cannot end the quotes
        for bad in [
            "*",
            "100.64.0.*",
            "host.example",
            "",
            "100.64.0.7,*",
            "!10.0.0.1",
        ] {
            assert_eq!(
                authorized_line(PUB, "/x/gate", bad),
                Err("bad_from_address"),
                "reason: from {bad:?}"
            );
        }
        assert!(authorized_line(PUB, "/x/gate", "2001:db8::7").is_ok());
        for bad in [
            "gate",
            "/x/ga te",
            "/x/gate\" ssh-rsa AAAA",
            "/x/gate\n",
            "",
        ] {
            assert_eq!(
                authorized_line(PUB, bad, "100.64.0.7"),
                Err("bad_gate_path"),
                "reason: gate {bad:?}"
            );
        }
        // only this key's own public line
        for bad in [
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7 aibd-delegate",
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl someone",
            "ssh-ed25519 AAAA aibd-delegate",
            "command=\"sh\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl aibd-delegate",
            "",
        ] {
            assert_eq!(
                authorized_line(bad, "/x/gate", "100.64.0.7"),
                Err("bad_public_key"),
                "reason: public {bad:?}"
            );
        }
    }

    #[test]
    fn the_key_is_told_apart_by_an_id_that_is_not_the_key() {
        let id = key_id(PUB);
        assert_eq!(id.len(), 16);
        assert!(id.bytes().all(|b| b.is_ascii_hexdigit()));
        assert!(!PUB.contains(&id));
        let other = PUB.replace("IOMq", "IOMr");
        assert_ne!(key_id(&other), id);
        assert_eq!(key_id(PUB), id);
    }

    #[cfg(unix)]
    fn script(dir: &Path, name: &str, body: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let p = dir.join(name);
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p
    }

    #[cfg(unix)]
    fn data_with_hosts() -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        let dir = trusted(d.path());
        std::fs::create_dir_all(&dir).unwrap();
        let hosts = serde_json::json!({"hosts": [{"name": "lin", "hostname": "192.0.2.10",
            "port": 22, "user": "owner", "os": "mac",
            "hostkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"}]});
        std::fs::write(
            dir.join(crate::trusted_hosts::FILE),
            serde_json::to_vec(&hosts).unwrap(),
        )
        .unwrap();
        d
    }

    #[cfg(unix)]
    fn lin(d: &Path) -> TrustedHost {
        let list = trusted(d).join(crate::trusted_hosts::FILE);
        crate::trusted_hosts::load(&list).unwrap().0.remove(0)
    }

    #[cfg(unix)]
    #[test]
    fn a_pair_is_made_once_and_only_a_private_plain_file_is_used() {
        use std::os::unix::fs::PermissionsExt;
        let d = data_with_hosts();
        let tools = tempfile::tempdir().unwrap();
        // a keygen double: writes the pair the way ssh-keygen does (its last word is the file)
        let keygen = script(
            tools.path(),
            "keygen",
            &format!("for f; do :; done\numask 077\necho PRIVATE > \"$f\"\necho '{PUB}' > \"$f.pub\"\necho made >> \"$f.count\""),
        );
        let link = SshLink::new(d.path().to_path_buf(), PathBuf::from("/bin/sh"));
        assert!(!link.ready(), "reason: ready_without_a_key");
        assert_eq!(ensure_key(d.path(), &keygen).unwrap(), PUB);
        assert_eq!(ensure_key(d.path(), &keygen).unwrap(), PUB);
        let count =
            std::fs::read_to_string(trusted(d.path()).join(format!("{KEY}.count"))).unwrap();
        assert_eq!(count.lines().count(), 1, "reason: key_made_twice");
        assert!(link.ready());
        // readable by others: not used
        let key = key_path(d.path());
        std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            ensure_key(d.path(), &keygen),
            Err("key_unsafe"),
            "reason: open_key_used"
        );
        assert!(!link.ready());
        std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o600)).unwrap();
        // a .pub that is not this key's line
        std::fs::write(
            trusted(d.path()).join(format!("{KEY}.pub")),
            "ssh-rsa AAAA x\n",
        )
        .unwrap();
        assert_eq!(ensure_key(d.path(), &keygen), Err("bad_public_key"));
        // a keygen that fails makes no key
        let d2 = data_with_hosts();
        assert_eq!(
            ensure_key(d2.path(), Path::new("/usr/bin/false")),
            Err("keygen_failed")
        );
        assert!(!key_path(d2.path()).exists());
    }

    #[cfg(unix)]
    #[test]
    fn a_line_is_said_with_the_file_key_and_the_bundle_on_standard_input() {
        let d = data_with_hosts();
        let tools = tempfile::tempdir().unwrap();
        let keygen = script(
            tools.path(),
            "keygen",
            &format!(
                "for f; do :; done\numask 077\necho PRIVATE > \"$f\"\necho '{PUB}' > \"$f.pub\""
            ),
        );
        ensure_key(d.path(), &keygen).unwrap();
        let seen = tools.path().join("seen");
        // an ssh double: records its words and its standard input, answers one line
        let ssh = script(
            tools.path(),
            "ssh",
            &format!(
                "printf '%s\\n' \"$@\" > '{0}.args'\ncat > '{0}.stdin'\nenv > '{0}.env'\necho 'OK run'",
                seen.display()
            ),
        );
        let link = SshLink::new(d.path().to_path_buf(), ssh);
        let bundle = tools.path().join("b.bundle");
        std::fs::write(&bundle, b"BUNDLE-BYTES").unwrap();
        let host = lin(d.path());
        let got = link.say(&host, "run web-b4297-012345678 x", Some(&bundle), 20);
        assert_eq!(got, b"OK run\n");
        let args = std::fs::read_to_string(format!("{}.args", seen.display())).unwrap();
        let args: Vec<&str> = args.lines().collect();
        assert_eq!(
            &args[..2],
            ["-F", "none"],
            "reason: account_ssh_config_read"
        );
        let key = key_path(d.path());
        assert!(args.contains(&format!("IdentityFile={}", key.display()).as_str()));
        assert!(args.contains(&"IdentityAgent=none"), "reason: agent_asked");
        assert!(args.contains(&"StrictHostKeyChecking=yes"));
        // the line is the last word, whole
        assert_eq!(
            args.last(),
            Some(&"run web-b4297-012345678 x"),
            "reason: line_split"
        );
        assert_eq!(args[args.len() - 2], "lin");
        assert_eq!(
            std::fs::read(format!("{}.stdin", seen.display())).unwrap(),
            b"BUNDLE-BYTES"
        );
        // nothing of aibd's environment reaches it (no agent socket, no token)
        let env = std::fs::read_to_string(format!("{}.env", seen.display())).unwrap();
        assert!(
            !env.contains("SSH_AUTH_SOCK") && !env.contains("HOME="),
            "reason: env_passed ({env})"
        );
        // the known-hosts file holds the pinned machine's key under its name
        let kh = std::fs::read_to_string(trusted(d.path()).join(KNOWN_HOSTS)).unwrap();
        assert!(kh.starts_with("lin ssh-ed25519 "));
    }

    #[cfg(unix)]
    #[test]
    fn a_machine_that_does_not_answer_in_time_is_no_answer() {
        let d = data_with_hosts();
        let tools = tempfile::tempdir().unwrap();
        let keygen = script(
            tools.path(),
            "keygen",
            &format!(
                "for f; do :; done\numask 077\necho PRIVATE > \"$f\"\necho '{PUB}' > \"$f.pub\""
            ),
        );
        ensure_key(d.path(), &keygen).unwrap();
        let slow = script(tools.path(), "ssh", "echo 'OK run'\nexec sleep 30");
        let link = SshLink::new(d.path().to_path_buf(), slow);
        let host = lin(d.path());
        let t0 = std::time::Instant::now();
        let got = link.say(&host, "fetch web-b4297-012345678", None, 1);
        assert!(got.is_empty(), "reason: half_answer_taken");
        assert!(
            t0.elapsed() < std::time::Duration::from_secs(15),
            "reason: waited_past_the_limit"
        );
        // no key: nothing is started
        let d2 = data_with_hosts();
        let never = script(
            tools.path(),
            "ssh2",
            &format!("touch '{}'", tools.path().join("ran").display()),
        );
        let link = SshLink::new(d2.path().to_path_buf(), never);
        assert!(!link.ready());
    }
}
