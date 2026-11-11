// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host leak-words --out <file>` — the host's own part of the private leak list,
//! read from this host on the spot (DEV 4.6 §6 baseline check and §9 V1 (2); f1fae 11-11:
//! a stand-alone read-only tool, **not** wired into `site verify` or anything a customer sees).
//!
//! The hand-written list (provider, city, internal names) does not know what changes with the
//! host: its name, its addresses, the resolvers it was given, the CPU, board and disk models,
//! the serials, the MACs, the resolver search domains. This reads them and writes them as rules in the format the private
//! list already uses (`kind value  # why`, kinds `word` / `re` / `net`), so a leak scan can
//! read the hand list and this file together.
//!
//! * Reads only: files under `/proc`, `/sys`, `/etc` and `ip -o addr show`. Changes nothing.
//! * The values are host information, so they go **only** into `--out` (created 0600 through
//!   a temporary file and a rename), and never inside a git work tree. Standard output gives
//!   counts per kind of fact, how many values of each kind were left out, and the names of
//!   sources that could not be read — never a value.
//! * Left out on purpose: private (RFC 1918 / unique local) and link-local addresses — a
//!   container sees its own bridge network, so they would hit everywhere and say nothing; a
//!   tailnet address (100.64.0.0/10) is ours alone, so the address itself is kept, its
//!   network is not; well-known public resolvers; generic firmware strings ("Default string");
//!   values shorter than 4 characters, or holding a `#` or a control character (they would
//!   either hit everything or break the list's `  # why` comment).
//!
//! Exit status: 0 written, every source read; 3 written, but a source could not be read (its
//! name is printed and written as a comment); 2 nothing written (bad arguments, a file inside
//! a git work tree, no rule at all, or the file could not be written).

use std::collections::BTreeMap;
use std::fs;
use std::io::{ErrorKind, Write};
use std::net::IpAddr;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

const IP: &str = "/usr/sbin/ip";

/// DMI fields worth a rule. Serials and the UUID are readable by root only.
const DMI: &[&str] = &[
    "sys_vendor",
    "product_name",
    "product_version",
    "board_vendor",
    "board_name",
    "bios_vendor",
    "chassis_vendor",
    "product_serial",
    "board_serial",
    "chassis_serial",
    "product_uuid",
];

/// Firmware fill-ins that name nothing (compared lower-case).
const GENERIC: &[&str] = &[
    "to be filled by o.e.m.",
    "default string",
    "system manufacturer",
    "system product name",
    "system version",
    "system serial number",
    "base board manufacturer",
    "base board serial number",
    "chassis manufacturer",
    "chassis serial number",
    "not specified",
    "not applicable",
    "not available",
    "unknown",
    "none",
    "o.e.m.",
    "oem",
    "0123456789",
    "123456789",
    "type1productconfigid",
];

/// Public resolvers anyone may use: naming one says nothing about this host.
const PUBLIC_RESOLVERS: &[&str] = &[
    "1.1.1.1",
    "1.0.0.1",
    "8.8.8.8",
    "8.8.4.4",
    "9.9.9.9",
    "149.112.112.112",
    "2606:4700:4700::1111",
    "2606:4700:4700::1001",
    "2001:4860:4860::8888",
    "2001:4860:4860::8844",
    "2620:fe::fe",
    "2620:fe::9",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Kind {
    Word,
    Re,
    Net,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct Rule {
    kind: Kind,
    value: String,
    why: &'static str,
}

impl Rule {
    fn line(&self) -> String {
        let kind = match self.kind {
            Kind::Word => "word",
            Kind::Re => "re",
            Kind::Net => "net",
        };
        format!("{kind} {}  # {}", self.value, self.why)
    }
}

/// What was read from the host. `None` = the source exists but could not be read; a source
/// this kind of host simply does not have (no DMI in a VM, no `model name` on ARM) is `Some`
/// of nothing. `hw` holds the single values already picked (why, value).
#[derive(Debug, Default)]
struct Facts {
    hostname: Option<String>,
    domainname: Option<String>,
    hosts: Option<String>,
    addrs: Option<String>,
    resolv: Option<String>,
    cpuinfo: Option<String>,
    machine_id: Option<String>,
    hw: Vec<(&'static str, String)>,
    unread: Vec<String>,
}

#[derive(Debug, PartialEq, Eq)]
enum Class {
    Public,
    Tailnet,
    Private,
    Other,
}

fn class(ip: IpAddr) -> Class {
    match ip {
        IpAddr::V4(a) => {
            let o = a.octets();
            if a.is_loopback() || a.is_link_local() || a.is_unspecified() || a.is_broadcast() {
                Class::Other
            } else if a.is_private() {
                Class::Private
            } else if o[0] == 100 && (o[1] & 0xc0) == 64 {
                Class::Tailnet
            } else {
                Class::Public
            }
        }
        IpAddr::V6(a) => {
            let s = a.segments();
            if a.is_loopback() || a.is_unspecified() || (s[0] & 0xffc0) == 0xfe80 {
                Class::Other
            } else if (s[0] & 0xfe00) == 0xfc00 {
                Class::Private
            } else {
                Class::Public
            }
        }
    }
}

/// The network `ip/prefix` lies in, as text; `None` for a prefix that is the address itself
/// or so wide it would name half the internet.
fn network(ip: IpAddr, prefix: u8) -> Option<String> {
    match ip {
        IpAddr::V4(a) if (8..32).contains(&prefix) => {
            let m = u32::from(a) & (u32::MAX << (32 - prefix));
            Some(format!("{}/{prefix}", std::net::Ipv4Addr::from(m)))
        }
        IpAddr::V6(a) if (32..128).contains(&prefix) => {
            let m = u128::from(a) & (u128::MAX << (128 - prefix));
            Some(format!("{}/{prefix}", std::net::Ipv6Addr::from(m)))
        }
        _ => None,
    }
}

/// A value fit for the list: whitespace collapsed, at least 4 characters, no `#` (the list's
/// comment mark) and no control character, not a firmware fill-in.
fn clean(raw: &str) -> Option<String> {
    let v = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    let ok = v.chars().count() >= 4
        && !v.contains('#')
        && !v.chars().any(char::is_control)
        && !GENERIC.contains(&v.to_lowercase().as_str());
    ok.then_some(v)
}

/// A host or domain name as a rule that hits the name and not a longer word around it
/// (`node1` must not hit `node12`). Names are letters, digits, `-` and `.` only.
fn name_rule(raw: &str, why: &'static str) -> Option<Rule> {
    let n = raw.trim().trim_end_matches('.').to_lowercase();
    let shape = n
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.');
    if !shape || n.starts_with("localhost") || n.starts_with("ip6-") {
        return None;
    }
    let n = clean(&n)?;
    Some(Rule {
        why,
        kind: Kind::Re,
        value: format!("(?<![a-z0-9.-]){}(?![a-z0-9-])", n.replace('.', "\\.")),
    })
}

/// `(address, prefix)` of every global-scope address in `ip -o addr show` output.
fn global_addrs(text: &str) -> Vec<(IpAddr, u8)> {
    let mut out = Vec::new();
    for line in text.lines() {
        let t: Vec<&str> = line.split_whitespace().collect();
        let Some(i) = t.iter().position(|w| *w == "inet" || *w == "inet6") else {
            continue;
        };
        if !t.windows(2).any(|w| w == ["scope", "global"]) {
            continue;
        }
        let Some((a, p)) = t.get(i + 1).and_then(|s| s.split_once('/')) else {
            continue;
        };
        if let (Ok(a), Ok(p)) = (a.parse::<IpAddr>(), p.parse::<u8>()) {
            out.push((a, p));
        }
    }
    out
}

/// What the facts give: the rules, and how many values of each kind were left out (too
/// short, a firmware fill-in, a `#` or a control character, not a name) — counted so a
/// summary that lacks a kind says why, never which value (33e28 #656 L2).
#[derive(Debug, Default)]
struct Found {
    rules: Vec<Rule>,
    skipped: BTreeMap<&'static str, usize>,
}

impl Found {
    fn skip(&mut self, raw: &str, why: &'static str) {
        if !raw.trim().is_empty() {
            *self.skipped.entry(why).or_default() += 1;
        }
    }
    fn name(&mut self, raw: &str, why: &'static str) {
        match name_rule(raw, why) {
            Some(r) => self.rules.push(r),
            None => self.skip(raw, why),
        }
    }
    fn word(&mut self, raw: &str, why: &'static str) {
        match clean(raw) {
            Some(value) => self.rules.push(Rule {
                kind: Kind::Word,
                value,
                why,
            }),
            None => self.skip(raw, why),
        }
    }
    fn net(&mut self, value: String, why: &'static str) {
        self.rules.push(Rule {
            kind: Kind::Net,
            value,
            why,
        });
    }
}

/// Every rule the facts give, sorted and without repeats, and what was left out.
fn rules(f: &Facts) -> Found {
    let mut out = Found::default();
    if let Some(h) = &f.hostname {
        out.name(h, "host name");
    }
    if let Some(d) = f.domainname.as_deref().filter(|d| d.trim() != "(none)") {
        out.name(d, "host domain name");
    }
    let own = global_addrs(f.addrs.as_deref().unwrap_or(""));
    for (ip, prefix) in &own {
        match class(*ip) {
            Class::Public => {
                out.net(ip.to_string(), "host address");
                if let Some(n) = network(*ip, *prefix) {
                    out.net(n, "host network");
                }
            }
            Class::Tailnet => out.net(ip.to_string(), "host tailnet address"),
            Class::Private | Class::Other => {}
        }
    }
    for line in f.hosts.as_deref().unwrap_or("").lines() {
        let line = line.split('#').next().unwrap_or("");
        let mut w = line.split_whitespace();
        let Some(Ok(ip)) = w.next().map(str::parse::<IpAddr>) else {
            continue;
        };
        let mine = own
            .iter()
            .any(|(a, _)| *a == ip && class(ip) != Class::Private)
            || ip.to_string() == "127.0.1.1";
        if mine {
            for n in w {
                out.name(n, "host name in /etc/hosts");
            }
        }
    }
    for line in f.resolv.as_deref().unwrap_or("").lines() {
        let mut w = line.split_whitespace();
        match w.next() {
            // the search list often names the provider's or the tailnet's domain (33e28 #656 N1)
            Some("search" | "domain") => {
                for d in w {
                    out.name(d, "resolver search domain");
                }
                continue;
            }
            Some("nameserver") => {}
            _ => continue,
        }
        let Some(Ok(ip)) = w.next().map(str::parse::<IpAddr>) else {
            continue;
        };
        if PUBLIC_RESOLVERS.contains(&ip.to_string().as_str()) {
            continue;
        }
        if matches!(class(ip), Class::Public | Class::Tailnet) {
            out.net(ip.to_string(), "resolver the host was given");
        }
    }
    let cpu = f.cpuinfo.as_deref().unwrap_or("").lines().find_map(|l| {
        let (k, v) = l.split_once(':')?;
        (k.trim() == "model name").then(|| v.to_owned())
    });
    let words = cpu
        .map(|c| ("CPU model", c))
        .into_iter()
        .chain(f.machine_id.clone().map(|m| ("machine id", m)))
        .chain(f.hw.iter().map(|(why, v)| (*why, v.clone())));
    for (why, v) in words {
        out.word(&v, why);
    }
    // sorted by kind and value first, so the same rule from two sources sits side by side
    out.rules.sort();
    out.rules
        .dedup_by(|a, b| a.kind == b.kind && a.value == b.value);
    out
}

/// Refuse an output file inside a git work tree: the values are host information and must
/// not land where a commit can pick them up (33e28 #656 L1). Looks from the file's directory
/// up for a `.git` (directory or file).
fn inside_git_tree(out: &Path) -> bool {
    let dir = match out.parent() {
        Some(p) if !p.as_os_str().is_empty() => p.to_path_buf(),
        _ => PathBuf::from("."),
    };
    let Ok(dir) = fs::canonicalize(&dir) else {
        return false;
    };
    dir.ancestors().any(|a| a.join(".git").exists())
}

/// Read one file under `root`: `Ok(None)` when it does not exist, `Err` when it exists and
/// cannot be read.
fn read(root: &Path, rel: &str) -> Result<Option<String>, ()> {
    match fs::read_to_string(root.join(rel)) {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(_) => Err(()),
    }
}

/// Everything there is to read under `root` (`/` on a host; a fake tree in tests) plus the
/// `ip -o addr show` text, which is passed in.
fn collect(root: &Path, addrs: Option<String>) -> Facts {
    let mut f = Facts {
        addrs,
        ..Facts::default()
    };
    if f.addrs.is_none() {
        f.unread.push("addresses".to_owned());
    }
    let one = |f: &mut Facts, rel: &str| match read(root, rel) {
        Ok(s) => Some(s.unwrap_or_default()),
        Err(()) => {
            f.unread.push(rel.to_owned());
            None
        }
    };
    f.hostname = one(&mut f, "proc/sys/kernel/hostname");
    f.domainname = one(&mut f, "proc/sys/kernel/domainname");
    f.hosts = one(&mut f, "etc/hosts");
    f.resolv = one(&mut f, "etc/resolv.conf");
    f.cpuinfo = one(&mut f, "proc/cpuinfo");
    f.machine_id = one(&mut f, "etc/machine-id");
    for field in DMI {
        let rel = format!("sys/class/dmi/id/{field}");
        if let Some(v) = one(&mut f, &rel) {
            f.hw.push(("firmware field", v));
        }
    }
    let list = |dir: &str| -> Vec<String> {
        let mut v: Vec<String> = fs::read_dir(root.join(dir))
            .map(|d| {
                d.filter_map(Result::ok)
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .collect()
            })
            .unwrap_or_default();
        v.sort();
        v
    };
    for nic in list("sys/class/net") {
        // a physical card has a device link; bridges, veths and taps do not
        if root.join(format!("sys/class/net/{nic}/device")).exists() {
            if let Some(v) = one(&mut f, &format!("sys/class/net/{nic}/address")) {
                if v.trim() != "00:00:00:00:00:00" {
                    f.hw.push(("network card MAC", v.trim().to_lowercase()));
                }
            }
        }
    }
    let virtual_disk = ["loop", "ram", "zram", "zd", "dm-", "sr", "nbd", "md"];
    for disk in list("sys/block") {
        if virtual_disk.iter().any(|p| disk.starts_with(p)) {
            continue;
        }
        for (field, why) in [("model", "disk model"), ("serial", "disk serial")] {
            if let Some(v) = one(&mut f, &format!("sys/block/{disk}/device/{field}")) {
                f.hw.push((why, v));
            }
        }
    }
    f
}

/// The lines printed: counts per kind of fact, what was left out (counts only) and what
/// could not be read. Never a value.
fn summary(found: &Found, unread: &[String]) -> Vec<String> {
    let mut per: BTreeMap<&str, usize> = BTreeMap::new();
    for r in &found.rules {
        *per.entry(r.why).or_default() += 1;
    }
    let mut out: Vec<String> = per.iter().map(|(why, n)| format!("{why}: {n}")).collect();
    for (why, n) in &found.skipped {
        out.push(format!(
            "left out (too short, a fill-in, # or not a name): {why}: {n}"
        ));
    }
    for u in unread {
        out.push(format!("could not read: {u}"));
    }
    out
}

/// The file's text: a header, what could not be read, one rule per line.
fn render(rules: &[Rule], unread: &[String]) -> String {
    let mut s = String::from(
        "# host part of the private leak list, read from this host by `aib-cloud-host leak-words`.\n\
         # Regenerate it; do not edit by hand. Never into a public repository.\n",
    );
    for u in unread {
        s.push_str(&format!("# could not read: {u}\n"));
    }
    for r in rules {
        s.push_str(&r.line());
        s.push('\n');
    }
    s
}

/// Write `text` to `out` as 0600: a new temporary file next to it, then a rename.
fn write_private(out: &str, text: &str) -> Result<(), String> {
    let tmp = format!("{out}.tmp-{}", std::process::id());
    let res = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .and_then(|mut f| f.write_all(text.as_bytes()).and_then(|()| f.sync_all()))
        .and_then(|()| fs::rename(&tmp, out));
    res.map_err(|e| {
        let _ = fs::remove_file(&tmp);
        format!("{out}: {e}")
    })
}

fn addr_text() -> Option<String> {
    let o = Command::new(IP)
        .args(["-o", "addr", "show"])
        .output()
        .ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
}

fn run(args: &[String], root: &Path, addrs: Option<String>) -> u8 {
    let out = match args {
        [flag, file] if flag == "--out" && !file.is_empty() => file,
        _ => {
            eprintln!(
                "usage: aib-cloud-host leak-words --out <file>   (values go to the file only)"
            );
            return 2;
        }
    };
    if inside_git_tree(Path::new(out)) {
        eprintln!("leak-words: {out} is inside a git work tree: the values are host information; write it elsewhere");
        return 2;
    }
    let f = collect(root, addrs);
    let found = rules(&f);
    for line in summary(&found, &f.unread) {
        println!("leak-words: {line}");
    }
    let rules = found.rules;
    if rules.is_empty() {
        println!("leak-words: no rule at all: nothing written");
        return 2;
    }
    if let Err(e) = write_private(out, &render(&rules, &f.unread)) {
        println!("leak-words: not written: {e}");
        return 2;
    }
    println!("leak-words: {} rules written to {out} (0600)", rules.len());
    if f.unread.is_empty() {
        0
    } else {
        3
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    Ok(run(args, Path::new("/"), addr_text()))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    const ADDRS: &str = "\
1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: eno1    inet 203.0.113.70/26 brd 203.0.113.127 scope global eno1\\       valid_lft forever
2: eno1    inet6 2001:db8:5:7::2/64 scope global \\       valid_lft forever preferred_lft forever
2: eno1    inet6 fe80::1/64 scope link \\       valid_lft forever preferred_lft forever
4: vmbr1    inet 10.77.0.1/24 scope global vmbr1\\       valid_lft forever preferred_lft forever
5: tailscale0    inet 100.64.0.31/32 scope global tailscale0\\       valid_lft forever
";

    fn facts() -> Facts {
        Facts {
            hostname: Some("node7\n".into()),
            domainname: Some("(none)\n".into()),
            hosts: Some(
                "127.0.0.1 localhost\n127.0.1.1 node7.lab.example node7\n\
                 203.0.113.70 node7.example.net  # the public name\n10.77.0.12 site2\n"
                    .into(),
            ),
            addrs: Some(ADDRS.into()),
            resolv: Some(
                "# from the provider\nsearch lab.example tail0.example.net\nnameserver 198.51.100.53\nnameserver 1.1.1.1\nnameserver 10.77.0.1\n"
                    .into(),
            ),
            cpuinfo: Some("processor\t: 0\nmodel name\t: Example   CPU 9000X 16-Core\n".into()),
            machine_id: Some("0123456789abcdef0123456789abcdef\n".into()),
            hw: vec![
                ("firmware field", "To Be Filled By O.E.M.\n".into()),
                ("firmware field", "Example Boards Inc.\n".into()),
                ("firmware field", "Board # 2\n".into()),
                ("firmware field", "X1\n".into()),
                ("disk model", "EXAMPLE NVME 2TB\n".into()),
                ("disk serial", "SN12345678\n".into()),
                ("network card MAC", "02:00:5e:10:00:01".into()),
            ],
            unread: vec![],
        }
    }

    fn has(rules: &[Rule], kind: Kind, value: &str) -> bool {
        rules.iter().any(|r| r.kind == kind && r.value == value)
    }

    #[test]
    fn the_host_part_names_what_this_host_is() {
        let r = rules(&facts()).rules;
        let host = "(?<![a-z0-9.-])node7(?![a-z0-9-])";
        assert!(has(&r, Kind::Re, host), "{r:?}");
        assert!(has(
            &r,
            Kind::Re,
            "(?<![a-z0-9.-])node7\\.example\\.net(?![a-z0-9-])"
        ));
        assert!(has(
            &r,
            Kind::Re,
            "(?<![a-z0-9.-])node7\\.lab\\.example(?![a-z0-9-])"
        ));
        assert!(has(&r, Kind::Net, "203.0.113.70"));
        assert!(has(&r, Kind::Net, "203.0.113.64/26"));
        assert!(has(&r, Kind::Net, "2001:db8:5:7::2"));
        assert!(has(&r, Kind::Net, "2001:db8:5:7::/64"));
        // the tailnet address is ours; its network is everybody's
        assert!(has(&r, Kind::Net, "100.64.0.31"));
        assert!(!r.iter().any(|x| x.value.starts_with("100.64.0.0")));
        assert!(has(&r, Kind::Net, "198.51.100.53"));
        assert!(has(
            &r,
            Kind::Re,
            "(?<![a-z0-9.-])lab\\.example(?![a-z0-9-])"
        ));
        assert!(has(
            &r,
            Kind::Re,
            "(?<![a-z0-9.-])tail0\\.example\\.net(?![a-z0-9-])"
        ));
        assert!(has(&r, Kind::Word, "Example CPU 9000X 16-Core"));
        assert!(has(&r, Kind::Word, "0123456789abcdef0123456789abcdef"));
        assert!(has(&r, Kind::Word, "Example Boards Inc."));
        assert!(has(&r, Kind::Word, "EXAMPLE NVME 2TB"));
        assert!(has(&r, Kind::Word, "SN12345678"));
        assert!(has(&r, Kind::Word, "02:00:5e:10:00:01"));
        // one rule per name even when it comes from two places
        assert_eq!(r.iter().filter(|x| x.value == host).count(), 1);
    }

    #[test]
    fn what_hits_everywhere_or_says_nothing_is_left_out() {
        let r = rules(&facts()).rules;
        let all: Vec<&str> = r.iter().map(|x| x.value.as_str()).collect();
        for v in &all {
            // the container's own bridge network, the loopback, link-local
            assert!(
                !v.starts_with("10.") && !v.starts_with("127.") && !v.starts_with("fe80"),
                "{v}"
            );
            // the list's comment mark and line breaks never get into a value
            assert!(!v.contains('#') && !v.contains('\n'), "{v}");
        }
        assert!(!all.contains(&"1.1.1.1"));
        assert!(!all
            .iter()
            .any(|v| v.contains("localhost") || v.contains("site2")));
        assert!(!all.contains(&"To Be Filled By O.E.M."));
        assert!(!all.contains(&"X1"));
        assert!(!all.iter().any(|v| v.contains("(none)")));
        // a value that is too short or of another shape gives no name rule at all
        assert_eq!(name_rule("pve", "host name"), None);
        assert_eq!(name_rule("bad name", "host name"), None);
        assert_eq!(name_rule("ip6-localhost", "host name"), None);
        assert_eq!(network("203.0.113.70".parse().unwrap(), 32), None);
        assert_eq!(network("203.0.113.70".parse().unwrap(), 4), None);
        assert_eq!(class("100.128.0.1".parse().unwrap()), Class::Public);
        assert_eq!(class("fd00::1".parse().unwrap()), Class::Private);
    }

    #[test]
    fn the_printed_summary_never_names_a_value() {
        let f = facts();
        let found = rules(&f);
        let text = summary(&found, &["sys/class/dmi/id/product_serial".to_owned()]).join("\n");
        for x in &found.rules {
            assert!(!text.contains(&x.value), "{} leaked into {text}", x.value);
        }
        // the values left out are not named either
        for v in ["To Be Filled", "Board # 2", "X1"] {
            assert!(!text.contains(v), "{v} leaked into {text}");
        }
        assert!(text.contains("host address: 2"), "{text}");
        // three firmware values left out (a fill-in, a #, too short): counted, not named
        assert!(text.contains(": firmware field: 3"), "{text}");
        assert!(text.contains("could not read: sys/class/dmi/id/product_serial"));
    }

    #[test]
    fn collect_reads_a_host_tree_and_names_what_it_could_not_read() {
        let root = std::env::temp_dir().join(format!("aib-leak-tree-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let w = |rel: &str, text: &str| {
            let p = root.join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, text).unwrap();
        };
        w("proc/sys/kernel/hostname", "node7\n");
        w("sys/class/dmi/id/board_name", "EXAMPLE-BOARD-77\n");
        // a serial that exists but cannot be read (a directory stands in for a 0400 file)
        fs::create_dir_all(root.join("sys/class/dmi/id/product_serial")).unwrap();
        w("sys/class/net/eno1/address", "02:00:5e:10:00:09\n");
        fs::create_dir_all(root.join("sys/class/net/eno1/device")).unwrap();
        w("sys/class/net/vmbr0/address", "02:00:5e:10:00:aa\n");
        w("sys/block/nvme0n1/device/model", "EXAMPLE NVME 1TB\n");
        w("sys/block/zd16/device/model", "ZVOL MODEL NAME\n");
        let f = collect(&root, None);
        assert_eq!(
            f.unread,
            ["addresses", "sys/class/dmi/id/product_serial"],
            "missing files are not errors; unreadable ones and no addresses are"
        );
        let r = rules(&f).rules;
        assert!(has(&r, Kind::Word, "EXAMPLE-BOARD-77"));
        assert!(has(&r, Kind::Word, "02:00:5e:10:00:09"));
        assert!(has(&r, Kind::Word, "EXAMPLE NVME 1TB"));
        // a bridge's MAC and a zvol are not the host's hardware
        assert!(!has(&r, Kind::Word, "02:00:5e:10:00:aa"));
        assert!(!has(&r, Kind::Word, "ZVOL MODEL NAME"));
        // the command: written, but exit 3 because two sources were not read
        let out = root.join("leak-host.txt");
        let args = vec!["--out".to_owned(), out.display().to_string()];
        assert_eq!(run(&args, &root, None), 3);
        let text = fs::read_to_string(&out).unwrap();
        assert!(text.contains("# could not read: addresses\n"));
        assert_eq!(
            fs::metadata(&out).unwrap().permissions().mode() & 0o777,
            0o600
        );
        // with the addresses it is complete: exit 0
        fs::remove_dir_all(root.join("sys/class/dmi/id/product_serial")).unwrap();
        assert_eq!(run(&args, &root, Some(ADDRS.to_owned())), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_file_is_in_the_private_lists_format() {
        let r = rules(&facts()).rules;
        let text = render(&r, &[]);
        let mut n = 0;
        for line in text.lines().filter(|l| !l.starts_with('#')) {
            // the reader splits a line at the first " #": the value must be what is left
            let body = line.split(" #").next().unwrap().trim();
            let (kind, value) = body.split_once(' ').unwrap();
            assert!(["word", "re", "net"].contains(&kind), "{line}");
            assert!(r.iter().any(|x| x.value == value), "{line}");
            n += 1;
        }
        assert_eq!(n, r.len());
    }

    #[test]
    fn a_file_inside_a_git_work_tree_is_refused() {
        let root = std::env::temp_dir().join(format!("aib-leak-git-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("repo/.git")).unwrap();
        fs::create_dir_all(root.join("repo/sub")).unwrap();
        let tree = root.join("tree");
        fs::create_dir_all(tree.join("proc/sys/kernel")).unwrap();
        fs::write(tree.join("proc/sys/kernel/hostname"), "node7\n").unwrap();
        for inside in ["repo/leak.txt", "repo/sub/leak.txt"] {
            let out = root.join(inside).display().to_string();
            assert_eq!(
                run(
                    &["--out".to_owned(), out.clone()],
                    &tree,
                    Some(ADDRS.to_owned())
                ),
                2
            );
            assert!(!Path::new(&out).exists(), "{inside} was written");
        }
        // next to the repository, not in it: written
        let out = root.join("leak.txt").display().to_string();
        assert_eq!(
            run(
                &["--out".to_owned(), out.clone()],
                &tree,
                Some(ADDRS.to_owned())
            ),
            0
        );
        assert!(Path::new(&out).exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn nothing_is_written_without_a_file_or_without_a_rule() {
        let root = std::env::temp_dir().join(format!("aib-leak-empty-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let out = root.join("x.txt").display().to_string();
        assert_eq!(run(&[], &root, None), 2);
        assert_eq!(run(&["--out".to_owned()], &root, None), 2);
        assert_eq!(
            run(
                &["--out".to_owned(), out.clone()],
                &root,
                Some(String::new())
            ),
            2
        );
        assert!(!Path::new(&out).exists());
        let _ = fs::remove_dir_all(&root);
    }
}
