//! The pinned list of test machines aibd's delegated receipts may reach (ORDER-webonly-today
//! 第 5 項 G1; f1fae PLAN-trusted-delegate-hosts-token r1 §1, 5d33a PREREAD-trusted-g1g4 L1).
//!
//! - The list is `<aibd data>/trusted/hosts.json`, one entry per machine: `name`, `hostname`,
//!   `port`, `user`, `hostkey` (the whole public key line, not a fingerprint), `os`. JSON, not the
//!   plan's TOML: the workspace has no TOML crate and a new dependency is its own review.
//! - Every ssh aibd runs for a delegate is built here from that list only: `-F none` (no
//!   `~/.ssh/config`), the address spelled out, a known-hosts file aibd writes holding only the
//!   listed keys (looked up by the entry's name: `HostKeyAlias`), the global one off,
//!   `StrictHostKeyChecking=yes`, no proxy, no forwarding, `IdentitiesOnly` with aibd's own agent
//!   socket. Editing `~/.ssh/config` or `~/.ssh/known_hosts` changes nothing here.
//! - [`digest`] is what the T2 approval records; aibd compares it before each delegate and stops
//!   on a change (a guard against a mistaken edit, not a defence: the same account can write the
//!   file — GUIDE-same-user-ai-threat-model).
//!
//! This first slice only reads, checks and builds arguments; nothing connects yet.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;

pub const DIR: &str = "trusted";
pub const FILE: &str = "hosts.json";
/// The largest list file read.
const MAX_FILE: u64 = 64 * 1024;
/// At most this many machines.
pub const MAX_HOSTS: usize = 16;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)] // a proxy or jump field is refused, not ignored
pub struct TrustedHost {
    pub name: String,
    pub hostname: String,
    pub port: u16,
    pub user: String,
    pub hostkey: String,
    pub os: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct List {
    hosts: Vec<TrustedHost>,
}

fn name_ok(s: &str) -> bool {
    (1..=32).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        && !s.starts_with('-')
}

/// A host name or address: letters, digits, `.`, `-`, `:` (IPv6); never an option (`-…`).
fn hostname_ok(s: &str) -> bool {
    (1..=253).contains(&s.len())
        && s.as_bytes()[0] != b'-'
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-:".contains(&b))
}

fn user_ok(s: &str) -> bool {
    (1..=32).contains(&s.len())
        && s.bytes()
            .next()
            .is_some_and(|b| b.is_ascii_lowercase() || b == b'_')
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"_-.".contains(&b))
}

/// `ssh-ed25519 <base64>` with nothing after it (a comment is dropped by the list's writer).
fn hostkey_ok(s: &str) -> bool {
    let mut p = s.split(' ');
    match (p.next(), p.next(), p.next()) {
        (Some("ssh-ed25519"), Some(k), None) => {
            (60..=120).contains(&k.len())
                && k.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))
        }
        _ => false,
    }
}

/// Why `h` is refused, if it is.
pub fn check(h: &TrustedHost) -> Result<(), &'static str> {
    if !name_ok(&h.name) {
        return Err("name");
    }
    if !hostname_ok(&h.hostname) {
        return Err("hostname");
    }
    if h.port == 0 {
        return Err("port");
    }
    if !user_ok(&h.user) {
        return Err("user");
    }
    if !hostkey_ok(&h.hostkey) {
        return Err("hostkey");
    }
    if !matches!(h.os.as_str(), "mac" | "windows") {
        return Err("os");
    }
    Ok(())
}

/// The list in `bytes`: every entry must pass [`check`], names are distinct, at most
/// [`MAX_HOSTS`]; anything else refuses the whole file (a list half read is not pinned).
pub fn parse(bytes: &[u8]) -> Result<Vec<TrustedHost>, String> {
    let l: List = serde_json::from_slice(bytes).map_err(|e| format!("not a host list: {e}"))?;
    if l.hosts.is_empty() || l.hosts.len() > MAX_HOSTS {
        return Err(format!("1 to {MAX_HOSTS} machines"));
    }
    for (i, h) in l.hosts.iter().enumerate() {
        check(h).map_err(|f| format!("machine {i}: bad {f}"))?;
        if l.hosts[..i].iter().any(|o| o.name == h.name) {
            return Err(format!("machine {i}: name {} twice", h.name));
        }
    }
    Ok(l.hosts)
}

/// The list at `path`: a regular file (not a link) of at most 64 KB.
pub fn load(path: &Path) -> Result<(Vec<TrustedHost>, String), String> {
    let m = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !m.file_type().is_file() || m.len() > MAX_FILE {
        return Err("the host list is not a plain file of at most 64 KB".into());
    }
    let b = std::fs::read(path).map_err(|e| e.to_string())?;
    Ok((parse(&b)?, digest(&b)))
}

/// SHA-256 of the list file as read: what the T2 approval records.
pub fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The known-hosts file aibd writes beside the list: one line per machine, keyed by its name
/// (each ssh says `HostKeyAlias=<name>`), so nothing else is trusted for it.
pub fn known_hosts_text(hosts: &[TrustedHost]) -> String {
    hosts
        .iter()
        .map(|h| format!("{} {}\n", h.name, h.hostkey))
        .collect()
}

/// The null file for `GlobalKnownHostsFile` on this platform.
fn null_file() -> &'static str {
    if cfg!(windows) {
        "NUL"
    } else {
        "/dev/null"
    }
}

/// A path ssh takes as one word with nothing expanded: absolute, no blank, quote, `%` (ssh's
/// tokens), `$` or leading `~` (5d33a PREREAD-trustedhosts L1: a Windows user name with a blank).
/// Refused, not escaped: aibd picks these paths, so one that needs escaping is a wrong place.
pub fn path_ok(p: &Path) -> bool {
    let Some(s) = p.to_str() else {
        return false;
    };
    p.is_absolute()
        && !s.starts_with('~')
        && !s
            .chars()
            .any(|c| c.is_whitespace() || c.is_control() || "%$\"'#".contains(c))
}

/// aibd's own key for a delegate: the one-use agent socket holding it and its public key file
/// (which tells ssh the one key to offer).
pub struct OwnKey<'a> {
    pub agent: &'a Path,
    pub public: &'a Path,
}

/// The ssh arguments for `h` (destination last; the command goes after them). `key` is aibd's
/// own key; `None` offers no key at all. `IdentityFile` is always set: with `IdentitiesOnly`
/// alone ssh still tries the account's `~/.ssh/id_*` (5d33a PREREAD-trustedhosts M1, measured on
/// 9.9p2 with `ssh -G`), which would log in with Andy's key past the receipt gate's `command=`.
pub fn ssh_args(
    h: &TrustedHost,
    known_hosts: &Path,
    key: Option<&OwnKey<'_>>,
    connect_s: u32,
) -> Result<Vec<String>, &'static str> {
    let mut paths = vec![known_hosts];
    if let Some(k) = key {
        paths.push(k.agent);
        paths.push(k.public);
    }
    if !paths.iter().all(|p| path_ok(p)) {
        return Err("path_not_one_word");
    }
    let shown = |p: Option<&Path>| p.map_or_else(|| "none".to_owned(), |p| p.display().to_string());
    let mut a: Vec<String> = vec!["-F".into(), "none".into()];
    let opts = [
        format!("HostName={}", h.hostname),
        format!("Port={}", h.port),
        format!("User={}", h.user),
        format!("HostKeyAlias={}", h.name),
        format!("UserKnownHostsFile={}", known_hosts.display()),
        format!("GlobalKnownHostsFile={}", null_file()),
        "StrictHostKeyChecking=yes".into(),
        "UpdateHostKeys=no".into(),
        "ProxyCommand=none".into(),
        "ProxyJump=none".into(),
        "ForwardAgent=no".into(),
        "ForwardX11=no".into(),
        "ClearAllForwardings=yes".into(),
        "PermitLocalCommand=no".into(),
        "BatchMode=yes".into(),
        "IdentitiesOnly=yes".into(),
        format!("IdentityFile={}", shown(key.map(|k| k.public))),
        format!("IdentityAgent={}", shown(key.map(|k| k.agent))),
        format!("ConnectTimeout={connect_s}"),
    ];
    for o in opts {
        a.push("-o".into());
        a.push(o);
    }
    a.push("--".into());
    a.push(h.name.clone());
    Ok(a)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;

    const KEY: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl";

    fn host(name: &str) -> serde_json::Value {
        json!({"name": name, "hostname": "192.0.2.10", "port": 22, "user": "receipts",
               "hostkey": KEY, "os": "mac"})
    }

    fn list(hosts: Vec<serde_json::Value>) -> Vec<u8> {
        serde_json::to_vec(&json!({ "hosts": hosts })).unwrap()
    }

    #[test]
    fn a_list_is_taken_whole_or_not_at_all() {
        let l = parse(&list(vec![host("lin"), host("mini")])).unwrap();
        assert_eq!(l.len(), 2);
        assert_eq!(l[0].hostname, "192.0.2.10");
        // one bad entry refuses the file
        let mut bad = host("x");
        bad["hostname"] = json!("-oProxyCommand=sh");
        assert!(
            parse(&list(vec![host("lin"), bad])).is_err(),
            "reason: option_host_taken"
        );
        assert!(
            parse(&list(vec![host("lin"), host("lin")])).is_err(),
            "reason: twice_taken"
        );
        assert!(parse(&list(vec![])).is_err(), "reason: empty_taken");
        let many: Vec<_> = (0..=MAX_HOSTS).map(|i| host(&format!("m{i}"))).collect();
        assert!(parse(&list(many)).is_err(), "reason: too_many_taken");
        // an unknown field (a proxy, a jump) is refused, not ignored
        let mut extra = host("lin");
        extra["jump"] = json!("bastion");
        assert!(
            parse(&list(vec![extra])).is_err(),
            "reason: unknown_field_taken"
        );
        assert!(parse(b"not json").is_err());
    }

    #[test]
    fn each_field_has_one_shape() {
        let ok: TrustedHost = serde_json::from_value(host("lin")).unwrap();
        assert_eq!(check(&ok), Ok(()));
        let with = |f: &str, v: serde_json::Value| {
            let mut h = host("lin");
            h[f] = v;
            check(&serde_json::from_value(h).unwrap())
        };
        assert_eq!(with("name", json!("Lin")), Err("name"));
        assert_eq!(with("name", json!("-x")), Err("name"));
        assert_eq!(with("hostname", json!("a b")), Err("hostname"));
        assert_eq!(with("hostname", json!("-oX=y")), Err("hostname"));
        assert_eq!(with("hostname", json!("-p2222")), Err("hostname"));
        assert_eq!(with("port", json!(0)), Err("port"));
        assert_eq!(with("user", json!("root;id")), Err("user"));
        assert_eq!(with("user", json!("-l")), Err("user"));
        assert_eq!(with("hostkey", json!("SHA256:abc")), Err("hostkey"));
        assert_eq!(
            with("hostkey", json!(format!("{KEY} comment"))),
            Err("hostkey")
        );
        assert_eq!(
            with("hostkey", json!(KEY.replace("ed25519", "rsa"))),
            Err("hostkey")
        );
        assert_eq!(with("os", json!("linux")), Err("os"));
        assert!(with("hostname", json!("2001:db8::1")).is_ok());
    }

    #[test]
    fn every_ssh_reads_only_the_list_and_trusts_only_its_keys() {
        let h: TrustedHost = serde_json::from_value(host("lin")).unwrap();
        // an absolute path on either platform (Windows has no root without a drive)
        let r = |s: &str| PathBuf::from(format!("{}{s}", if cfg!(windows) { "C:" } else { "" }));
        let (agent, public, known) = (r("/k/agent.sock"), r("/k/aibd.pub"), r("/k/known"));
        let own = OwnKey {
            agent: &agent,
            public: &public,
        };
        let a = ssh_args(&h, &known, Some(&own), 5).unwrap();
        assert_eq!(&a[..2], ["-F", "none"], "reason: ssh_config_read");
        let joined = a.join(" ");
        let wants = [
            "HostName=192.0.2.10".to_owned(),
            "Port=22".into(),
            "User=receipts".into(),
            "HostKeyAlias=lin".into(),
            format!("UserKnownHostsFile={}", known.display()),
            "StrictHostKeyChecking=yes".to_owned(),
            "UpdateHostKeys=no".into(),
            "ProxyCommand=none".to_owned(),
            "ProxyJump=none".into(),
            "ForwardAgent=no".into(),
            "ClearAllForwardings=yes".into(),
            "PermitLocalCommand=no".into(),
            "BatchMode=yes".into(),
            "IdentitiesOnly=yes".into(),
            format!("IdentityAgent={}", agent.display()),
            format!("IdentityFile={}", public.display()),
            "ConnectTimeout=5".into(),
        ];
        for want in &wants {
            assert!(
                a.iter().any(|x| x == want),
                "reason: missing_{want} {joined}"
            );
        }
        assert!(
            a.iter().any(|x| x.starts_with("GlobalKnownHostsFile=")),
            "reason: global_known_hosts_read"
        );
        assert_eq!(
            &a[a.len() - 2..],
            ["--", "lin"],
            "reason: destination_not_last"
        );
        let none = ssh_args(&h, &known, None, 5).unwrap();
        assert!(
            none.iter().any(|x| x == "IdentityAgent=none"),
            "reason: default_agent_used"
        );
        // no key given: no ~/.ssh/id_* either (5d33a M1)
        assert!(
            none.iter().any(|x| x == "IdentityFile=none"),
            "reason: default_keys_tried"
        );
        // a path ssh would split or expand is refused (5d33a L1)
        for bad in [
            r("/Users/A B/known"),
            r("/k/%h"),
            PathBuf::from("relative/known"),
            PathBuf::from("~/known"),
            r("/k/$HOME"),
        ] {
            assert_eq!(
                ssh_args(&h, &bad, None, 5),
                Err("path_not_one_word"),
                "reason: path_taken {}",
                bad.display()
            );
        }
        let spaced_pub = r("/Users/A B/aibd.pub");
        let spaced = OwnKey {
            agent: &agent,
            public: &spaced_pub,
        };
        assert!(
            ssh_args(&h, &known, Some(&spaced), 5).is_err(),
            "reason: key_path_taken"
        );
        assert_eq!(known_hosts_text(&[h]), format!("lin {KEY}\n"));
    }

    #[test]
    fn the_list_is_a_plain_small_file_and_its_digest_changes_with_it() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join(FILE);
        let b = list(vec![host("lin")]);
        std::fs::write(&p, &b).unwrap();
        let (l, dg) = load(&p).unwrap();
        assert_eq!((l.len(), dg.len()), (1, 64));
        assert_eq!(dg, digest(&b));
        assert_ne!(
            digest(&list(vec![host("mini")])),
            dg,
            "reason: digest_blind"
        );
        std::fs::write(&p, vec![b' '; usize::try_from(MAX_FILE).unwrap() + 1]).unwrap();
        assert!(load(&p).is_err(), "reason: big_file_read");
        #[cfg(unix)]
        {
            let real = d.path().join("real.json");
            std::fs::write(&real, &b).unwrap();
            let link = d.path().join("link.json");
            std::os::unix::fs::symlink(&real, &link).unwrap();
            assert!(load(&link).is_err(), "reason: link_followed");
        }
    }
}
