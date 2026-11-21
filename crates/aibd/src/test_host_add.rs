//! 「加一台測試機」(519d0 11-21 09:27, Andy: what needs his terminal becomes a button;
//! DESIGN-add-test-host-b4297-1121): the parts that need no connection — which machines the
//! owner can pick, the three fixed commands run through the owner's own ssh login (read the
//! machine's host key, write the one authorized line, take it away), what the read says, and the
//! pinned host list with one machine added or taken away.
//!
//! - The machines offered are the `Host` names of the owner's ssh config, nothing else of it
//!   (no address, user or key is read); a name with a pattern (`*`, `?`, `!`) is not a machine.
//! - The commands are constants; the only thing that changes is the authorized line, which goes
//!   on standard input, never on the command line. Each is run by `/bin/sh` on the test machine.
//! - The write is the paste page's line (PAGE-andy-receipt-key r1, 5d33a M1): a file without a
//!   last newline gets one first, the line is added once, the file is 0600, and it answers with
//!   how many copies there are (exactly 1 is success). The removal takes that exact line away
//!   and keeps every other.

use crate::trusted_hosts::{self, TrustedHost};

/// Read on the test machine through the owner's login, run by `/bin/sh` there (one line each):
/// its ed25519 host key, the login name, the macOS version (`none`: no `sw_vers`, not a Mac),
/// its home, the address it sees this computer at (what the line's `from=` holds), and whether
/// the receipt gate is installed in that home.
pub const READ: &str = r#"cat /etc/ssh/ssh_host_ed25519_key.pub
id -un
sw_vers -productVersion 2>/dev/null || echo none
printf "%s\n" "$HOME"
printf "%s\n" "${SSH_CLIENT%% *}"
if [ -x "$HOME/.ez2-aib/bin/aib-receipt-gate" ]; then echo gate; else echo no_gate; fi"#;

/// Where the receipt gate is in a test machine's home (the line's `command=`).
pub const GATE_IN_HOME: &str = ".ez2-aib/bin/aib-receipt-gate";

/// Add the line read from standard input to `~/.ssh/authorized_keys` once; prints `ADDED <n>`.
/// The line must be a restricted one of aibd's key (5d33a PREREAD-addtesthost N2).
pub const ADD: &str = r#"IFS= read -r L || exit 3
case "$L" in restrict,command=*" aibd-delegate") ;; *) exit 3;; esac
umask 077
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" || exit 4
F="$HOME/.ssh/authorized_keys"
touch "$F" || exit 4
if [ -s "$F" ] && [ -n "$(tail -c1 "$F")" ]; then printf "\n" >> "$F" || exit 4; fi
grep -qxF -- "$L" "$F" || printf "%s\n" "$L" >> "$F" || exit 4
chmod 600 "$F" || exit 4
echo "ADDED $(grep -cxF -- "$L" "$F")""#;

/// Take out of `~/.ssh/authorized_keys` every line of aibd's key read from standard input
/// (`ssh-ed25519 <base64>`) that carries the `aibd-delegate` mark, whatever its options say;
/// every other line is kept, another key's marked line too (5d33a N2). Prints
/// `REMOVED <such lines left>`: 0 is success.
pub const REMOVE: &str = r#"IFS= read -r K || exit 3
case "$K" in "ssh-ed25519 "*) ;; *) exit 3;; esac
case "$K" in *" "*" "*) exit 3;; esac
F="$HOME/.ssh/authorized_keys"
[ -f "$F" ] || { echo "REMOVED 0"; exit 0; }
M=" $K aibd-delegate"
T="$F.aibd.$$"
grep -vF -- "$M" "$F" > "$T"
r=$?
[ "$r" -le 1 ] || { rm -f "$T"; exit 4; }
chmod 600 "$T" && mv "$T" "$F" || { rm -f "$T"; exit 4; }
echo "REMOVED $(grep -cF -- "$M" "$F")""#;

/// The owner operation that writes the line to a machine (5d33a PREREAD-addtesthost B1).
pub const OP_ADD: &str = "receipt.host_add";
/// The owner operation that takes it away.
pub const OP_REMOVE: &str = "receipt.host_remove";

/// The ssh arguments for one of the fixed commands on `m` through the owner's login (5d33a
/// PREREAD-addtesthost M1): no config file, every value spelled out, the owner's known-hosts
/// file read and never written, nothing routed or forwarded, no prompt.
pub fn login_args(
    m: &Machine,
    known_hosts: &std::path::Path,
    command: &str,
) -> Result<Vec<String>, &'static str> {
    let identity = std::path::Path::new(&m.identity_file);
    if !trusted_hosts::path_ok(identity) || !trusted_hosts::path_ok(known_hosts) {
        return Err("path_not_one_word");
    }
    if !trusted_hosts::hostname_ok(&m.hostname) || !trusted_hosts::user_ok(&m.user) || m.port == 0 {
        return Err("machine_values");
    }
    let null = if cfg!(windows) { "NUL" } else { "/dev/null" };
    let mut a: Vec<String> = vec!["-F".into(), "none".into()];
    for o in [
        "IdentitiesOnly=yes".to_owned(),
        format!("IdentityFile={}", m.identity_file),
        "StrictHostKeyChecking=yes".into(),
        format!("UserKnownHostsFile={}", known_hosts.display()),
        format!("GlobalKnownHostsFile={null}"),
        "UpdateHostKeys=no".into(),
        "BatchMode=yes".into(),
        "ForwardAgent=no".into(),
        "ForwardX11=no".into(),
        "ClearAllForwardings=yes".into(),
        "PermitLocalCommand=no".into(),
        "ProxyCommand=none".into(),
        "ProxyJump=none".into(),
        "ConnectTimeout=12".into(),
    ] {
        a.push("-o".into());
        a.push(o);
    }
    a.extend([
        "-l".into(),
        m.user.clone(),
        "-p".into(),
        m.port.to_string(),
        "--".into(),
        m.hostname.clone(),
        command.to_owned(),
    ]);
    Ok(a)
}

/// `SHA256:<base64>` of a `ssh-ed25519 <base64>` key, as `ssh-keygen -l` prints it.
pub fn fingerprint(hostkey: &str) -> Option<String> {
    use base64::Engine;
    use sha2::{Digest, Sha256};
    let blob = hostkey.split(' ').nth(1)?;
    let raw = base64::engine::general_purpose::STANDARD
        .decode(blob)
        .ok()?;
    let d = base64::engine::general_purpose::STANDARD_NO_PAD.encode(Sha256::digest(raw));
    Some(format!("SHA256:{d}"))
}

/// The owner's ask to write (`OP_ADD`) or take away (`OP_REMOVE`) aibd's line on `m`: the window
/// shows the real address and the host key's fingerprint, and the ticket is bound to them and
/// the line (5d33a L1: the alias is not bound, where it points is a local file's word).
pub fn host_ask(
    operation: &'static str,
    m: &Machine,
    hostkey: &str,
    line: &str,
    by: String,
) -> crate::owner_ok::Ask {
    let params = serde_json::json!({
        "hostname": m.hostname, "port": m.port, "user": m.user,
        "hostkey": hostkey, "fingerprint": fingerprint(hostkey).unwrap_or_default(),
        "line": line,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation,
        resource: format!("{}@{}:{}", m.user, m.hostname, m.port),
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// The one remote command for `script`: `/bin/sh -c '<script>'` (none of the scripts holds a
/// single quote, so nothing is escaped).
pub fn remote_command(script: &str) -> Option<String> {
    (!script.contains('\'')).then(|| format!("/bin/sh -c '{script}'"))
}

/// A machine name the owner can pick: what an ssh `Host` line holds for one machine.
fn pick_ok(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-._".contains(&b))
}

/// One `Host` pattern list matches `name` (ssh's `*` / `?` globs; a matching `!pattern` wins).
fn host_matches(patterns: &[String], name: &str) -> bool {
    fn glob(p: &[u8], s: &[u8]) -> bool {
        match (p.first(), s.first()) {
            (None, None) => true,
            (Some(b'*'), _) => glob(&p[1..], s) || (!s.is_empty() && glob(p, &s[1..])),
            (Some(b'?'), Some(_)) => glob(&p[1..], &s[1..]),
            (Some(a), Some(b)) if a.eq_ignore_ascii_case(b) => glob(&p[1..], &s[1..]),
            _ => false,
        }
    }
    let mut yes = false;
    for p in patterns {
        if let Some(n) = p.strip_prefix('!') {
            if glob(n.as_bytes(), name.as_bytes()) {
                return false;
            }
        } else if glob(p.as_bytes(), name.as_bytes()) {
            yes = true;
        }
    }
    yes
}

/// The keys of the owner's ssh config this reads (5d33a PREREAD-addtesthost M1: the file is
/// data, never handed to ssh; every other key is not read).
const KEPT: [&str; 4] = ["hostname", "user", "port", "identityfile"];
/// Keys that make a machine one this cannot reach plainly: each runs or routes through
/// something else (5d33a M1), so the machine is not offered.
const REFUSED: [&str; 4] = [
    "proxycommand",
    "proxyjump",
    "knownhostscommand",
    "localcommand",
];

/// One `Host` block: its patterns and its kept keys (lower case) with their values, in order.
type Block = (Vec<String>, Vec<(String, String)>);

/// What ssh would expand in a value besides a leading `~/` (its `%` tokens, `${VAR}`).
const EXPANSIONS: [char; 2] = ['%', '$'];

/// A machine of the owner's ssh config, as aibd would reach it: every value spelled out.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Machine {
    pub name: String,
    pub hostname: String,
    pub port: u16,
    pub user: String,
    pub identity_file: String,
}

/// The machines of an ssh config, each `Host` name once in order, read as ssh reads its file
/// (the first value of a key among the blocks matching the name wins), with `user` the login
/// name when no block sets one and `home` for a leading `~/`. A name is offered with why it
/// cannot be used instead when a matching block routes it elsewhere, a value has a `%` token or
/// is missing, or the file has a `Match` or `Include` (what those apply to is not read here).
/// Only `~/` is expanded; any other `~`, `%` or `$` makes the machine unusable rather than half
/// expanded (5d33a PREREAD-addtesthost r1 N4), and so does a key file under `own_keys`, aibd's
/// own delegate folder (N5: the two ways in are never mixed).
pub fn machines(
    config: &str,
    user: &str,
    home: &str,
    own_keys: &str,
) -> Vec<(String, Result<Machine, &'static str>)> {
    let mut blocks: Vec<Block> = Vec::new();
    let mut whole_file_refused = false;
    for line in config.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        let (k, v) = match t.find(|c: char| c.is_whitespace() || c == '=') {
            Some(i) => (
                &t[..i],
                t[i..].trim_start_matches(|c: char| c.is_whitespace() || c == '='),
            ),
            None => (t, ""),
        };
        let k = k.to_ascii_lowercase();
        let v = v.split(" #").next().unwrap_or_default().trim();
        match k.as_str() {
            "host" => blocks.push((
                v.split_whitespace().map(str::to_owned).collect(),
                Vec::new(),
            )),
            "match" | "include" => whole_file_refused = true,
            _ => {
                if blocks.is_empty() {
                    blocks.push((vec!["*".to_owned()], Vec::new()));
                }
                if let Some(b) = blocks.last_mut() {
                    b.1.push((k, v.to_owned()));
                }
            }
        }
    }
    let mut names: Vec<String> = Vec::new();
    for (pats, _) in &blocks {
        for p in pats {
            if pick_ok(p) && !names.iter().any(|n| n == p) {
                names.push(p.clone());
            }
        }
    }
    names
        .into_iter()
        .map(|name| {
            let got = machine_of(&blocks, &name, user, home, whole_file_refused)
                .and_then(|m| own_key_refused(m, own_keys));
            (name, got)
        })
        .collect()
}

fn machine_of(
    blocks: &[Block],
    name: &str,
    user: &str,
    home: &str,
    whole_file_refused: bool,
) -> Result<Machine, &'static str> {
    if whole_file_refused {
        return Err("config_has_match_or_include");
    }
    let mut first: Vec<(&str, &str)> = Vec::new();
    for (pats, keys) in blocks {
        if !host_matches(pats, name) {
            continue;
        }
        for (k, v) in keys {
            if REFUSED.contains(&k.as_str()) && !v.eq_ignore_ascii_case("none") {
                return Err("reached_through_something_else");
            }
            if KEPT.contains(&k.as_str()) && !first.iter().any(|(f, _)| f == k) {
                first.push((k, v));
            }
        }
    }
    let get = |k: &str| first.iter().find(|(f, _)| *f == k).map(|(_, v)| *v);
    let hostname = get("hostname").unwrap_or(name);
    let port = match get("port") {
        Some(p) => p.parse::<u16>().ok().filter(|p| *p > 0).ok_or("port")?,
        None => 22,
    };
    let user = get("user").unwrap_or(user);
    let identity_file = match get("identityfile") {
        Some(f) => match f.strip_prefix("~/") {
            Some(rest) => format!("{home}/{rest}"),
            None => f.to_owned(),
        },
        None => return Err("no_identity_file"),
    };
    if [hostname, user, identity_file.as_str()]
        .iter()
        .any(|v| v.is_empty() || v.starts_with('~') || v.contains(EXPANSIONS))
    {
        return Err("value_with_a_token");
    }
    // a Windows path is absolute with its drive; `/…` is taken as absolute on both
    if !(identity_file.starts_with('/') || std::path::Path::new(&identity_file).is_absolute()) {
        return Err("identity_file_not_absolute");
    }
    Ok(Machine {
        name: name.to_owned(),
        hostname: hostname.to_owned(),
        port,
        user: user.to_owned(),
        identity_file,
    })
}

/// A key file in aibd's own delegate folder is not the owner's login (5d33a N5).
fn own_key_refused(m: Machine, own_keys: &str) -> Result<Machine, &'static str> {
    let own = std::path::Path::new(own_keys);
    if !own_keys.is_empty() && std::path::Path::new(&m.identity_file).starts_with(own) {
        return Err("aibd_own_key");
    }
    Ok(m)
}

/// What [`READ`] said about a machine.
#[derive(Debug, PartialEq, Eq)]
pub struct Read {
    /// `ssh-ed25519 <base64>`, its comment dropped (as the host list keeps it).
    pub hostkey: String,
    pub user: String,
    pub macos: String,
    /// The receipt gate's path there (`<home>/` + [`GATE_IN_HOME`]); `None`: not installed (a
    /// machine is added only with one, and taken away without).
    pub gate: Option<String>,
    /// The address the machine sees this computer at.
    pub from: String,
}

/// [`READ`]'s six lines; anything else (no ed25519 key, not a Mac, a home that is not one plain
/// path, an address that is not one) is refused.
pub fn parse_read(out: &str) -> Result<Read, &'static str> {
    let mut l = out.lines().map(str::trim);
    let key = l.next().unwrap_or_default();
    let mut k = key.split(' ');
    let hostkey = match (k.next(), k.next()) {
        (Some("ssh-ed25519"), Some(b)) => format!("ssh-ed25519 {b}"),
        _ => return Err("no_ed25519_host_key"),
    };
    let user = l.next().unwrap_or_default().to_owned();
    let macos = l.next().unwrap_or_default().to_owned();
    if !macos.starts_with(|c: char| c.is_ascii_digit())
        || !macos.bytes().all(|b| b.is_ascii_digit() || b == b'.')
    {
        return Err("not_a_mac");
    }
    let home = l.next().unwrap_or_default();
    let home_ok = home.len() > 1
        && home.len() <= 200
        && home.starts_with('/')
        && home
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b));
    if !home_ok {
        return Err("home");
    }
    let from = l.next().unwrap_or_default().to_owned();
    if from.parse::<std::net::IpAddr>().is_err() {
        return Err("from_address");
    }
    let gate = match l.next() {
        Some("gate") => Some(format!("{}/{GATE_IN_HOME}", home.trim_end_matches('/'))),
        Some("no_gate") => None,
        _ => return Err("read_said_less"),
    };
    if l.next().is_some() {
        return Err("read_said_more");
    }
    Ok(Read {
        hostkey,
        user,
        macos,
        gate,
        from,
    })
}

/// The host list with `h` in it: checked as [`trusted_hosts::check`] checks; the same name
/// is replaced where it stands (a machine added again after its key changed: the list's order
/// is which machine is used first), a new one goes last, at most [`trusted_hosts::MAX_HOSTS`].
pub fn with_host(list: &[TrustedHost], h: TrustedHost) -> Result<Vec<TrustedHost>, &'static str> {
    trusted_hosts::check(&h)?;
    let mut out = list.to_vec();
    if let Some(o) = out.iter_mut().find(|o| o.name == h.name) {
        *o = h;
        return Ok(out);
    }
    if out.len() >= trusted_hosts::MAX_HOSTS {
        return Err("too_many_machines");
    }
    out.push(h);
    Ok(out)
}

/// The receipt gate answered aibd's own key: its first line is `OK status` or `ERR <code>`
/// (anything else, a shell's words or nothing, is not the gate behind the line).
pub fn gate_answered(out: &str) -> bool {
    let first = out.lines().next().unwrap_or_default();
    first == "OK status"
        || first.strip_prefix("ERR ").is_some_and(|c| {
            !c.is_empty() && c.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
        })
}

/// The host list after the check with aibd's own key: `h` is pinned only when the gate answered
/// (5d33a PREREAD-addtesthost L2: the receipt runner only ever sees machines that passed);
/// otherwise the list stays as it was and the card says why.
pub fn pinned_after_check(
    list: &[TrustedHost],
    h: TrustedHost,
    status_out: &str,
) -> Result<Vec<TrustedHost>, &'static str> {
    if !gate_answered(status_out) {
        return Err("gate_did_not_answer");
    }
    with_host(list, h)
}

/// The host list without the machine `name` (none left is an empty list: the file is then
/// removed, since [`trusted_hosts::parse`] refuses an empty one).
pub fn without_host(list: &[TrustedHost], name: &str) -> Vec<TrustedHost> {
    list.iter().filter(|o| o.name != name).cloned().collect()
}

/// The host list file's text for `list`, as [`trusted_hosts::parse`] reads it back.
pub fn list_text(list: &[TrustedHost]) -> String {
    let v = serde_json::json!({ "hosts": list });
    let mut s = serde_json::to_string_pretty(&v).unwrap_or_default();
    s.push('\n');
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl";

    fn mini() -> TrustedHost {
        TrustedHost {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 22,
            user: "owner".into(),
            hostkey: KEY.into(),
            os: "mac".into(),
        }
    }

    #[test]
    fn only_the_names_of_single_machines_are_offered() {
        let cfg = "Host andymini lin-macmini # two\n  HostName 192.0.2.7\n  User owner\n  \
                   IdentityFile ~/.ssh/id_rsa\nhost=pro13\n Port=2222\nHost !bad web-?? -oProxy\n\
                   HostName 192.0.2.9\nHost jump-*\n ProxyJump gw\nHost jump-a\n\
                   Host *\n  User fallback\n  IdentityFile ~/.ssh/id_ed25519\n  ProxyCommand none\n";
        let m = machines(cfg, "me", "/Users/me", "/d/trusted");
        let names: Vec<&str> = m.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, ["andymini", "lin-macmini", "pro13", "jump-a"]);
        assert_eq!(
            m[0].1,
            Ok(Machine {
                name: "andymini".into(),
                hostname: "192.0.2.7".into(),
                port: 22,
                user: "owner".into(),
                identity_file: "/Users/me/.ssh/id_rsa".into(),
            })
        );
        // the first value wins: `Host *` fills only what the name's own block left out
        let pro = m[2].1.as_ref().unwrap();
        assert_eq!(
            (pro.hostname.as_str(), pro.port, pro.user.as_str()),
            ("pro13", 2222, "fallback")
        );
        assert_eq!(pro.identity_file, "/Users/me/.ssh/id_ed25519");
        assert_eq!(
            m[3].1,
            Err("reached_through_something_else"),
            "reason: proxy_offered"
        );
        let no_key = machines(
            "Host lone\n HostName 192.0.2.8\n",
            "me",
            "/Users/me",
            "/d/trusted",
        );
        assert_eq!(no_key[0].1, Err("no_identity_file"));
        let token = machines(
            "Host t\n HostName %h.example\n IdentityFile ~/k\n",
            "me",
            "/h",
            "/d/trusted",
        );
        assert_eq!(token[0].1, Err("value_with_a_token"));
        for (half, why) in [
            ("~other/k", "value_with_a_token"),
            ("${HOME}/k", "value_with_a_token"),
            ("/h/${X}/k", "value_with_a_token"),
            ("k", "identity_file_not_absolute"),
        ] {
            let m = machines(
                &format!("Host t\n IdentityFile {half}\n"),
                "me",
                "/h",
                "/d/trusted",
            );
            assert_eq!(m[0].1, Err(why), "reason: half_expanded {half}");
        }
        let own = machines(
            "Host t\n IdentityFile /d/trusted/delegate_ed25519\n",
            "me",
            "/h",
            "/d/trusted",
        );
        assert_eq!(
            own[0].1,
            Err("aibd_own_key"),
            "reason: delegate_key_as_login"
        );
        for refused in ["Include other\n", "Match exec \"x\"\n"] {
            let m = machines(
                &format!("{refused}Host a\n IdentityFile ~/k\n"),
                "me",
                "/h",
                "/d/trusted",
            );
            assert_eq!(
                m[0].1,
                Err("config_has_match_or_include"),
                "reason: {refused}"
            );
        }
        assert!(
            machines("HostName andymini\n", "me", "/h", "/d/trusted").is_empty(),
            "reason: hostname_read_as_host"
        );
    }

    #[test]
    fn the_read_gives_the_key_without_its_comment_and_refuses_a_machine_that_is_no_mac() {
        let tail = "/Users/owner\n192.0.2.1\ngate\n";
        let r = parse_read(&format!("{KEY} root@andymini\nowner\n15.6.1\n{tail}")).unwrap();
        assert_eq!(r.hostkey, KEY, "reason: comment_kept");
        assert_eq!((r.user.as_str(), r.macos.as_str()), ("owner", "15.6.1"));
        assert_eq!(
            r.gate.as_deref(),
            Some("/Users/owner/.ez2-aib/bin/aib-receipt-gate")
        );
        let none = format!("{KEY}\nowner\n15.6\n/Users/owner\n192.0.2.1\nno_gate\n");
        assert_eq!(
            parse_read(&none).unwrap().gate,
            None,
            "reason: no_gate_taken_as_gate"
        );
        assert_eq!(r.from, "192.0.2.1");
        for (out, why) in [
            (
                format!("ssh-rsa AAAA x\nowner\n15.6\n{tail}"),
                "no_ed25519_host_key",
            ),
            (format!("{KEY}\nowner\nnone\n{tail}"), "not_a_mac"),
            (format!("{KEY}\nowner\n\n{tail}"), "not_a_mac"),
            (
                format!("{KEY}\nowner\n15.6\n/Users/o w\n192.0.2.1\ngate\n"),
                "home",
            ),
            (
                format!("{KEY}\nowner\n15.6\n/Users/o\"x\n192.0.2.1\ngate\n"),
                "home",
            ),
            (
                format!("{KEY}\nowner\n15.6\n/Users/owner\n\ngate\n"),
                "from_address",
            ),
            (
                format!("{KEY}\nowner\n15.6\n/Users/owner\n192.0.2.1\n"),
                "read_said_less",
            ),
            (
                format!("{KEY}\nowner\n15.6\n{tail}more\n"),
                "read_said_more",
            ),
        ] {
            assert_eq!(parse_read(&out), Err(why), "reason: {why}");
        }
        assert!(!READ.contains('\''), "reason: read_not_one_quoted_word");
    }

    fn machine(dir: &std::path::Path) -> Machine {
        Machine {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 2222,
            user: "owner".into(),
            identity_file: dir.join("id_rsa").to_string_lossy().into_owned(),
        }
    }

    #[test]
    fn the_login_takes_no_config_and_spells_every_value_out() {
        let dir = std::env::temp_dir();
        let m = machine(&dir);
        let kh = dir.join("known_hosts");
        let a = login_args(&m, &kh, READ).unwrap();
        assert_eq!(&a[..2], ["-F", "none"], "reason: config_read");
        let o: Vec<&str> = a
            .windows(2)
            .filter(|w| w[0] == "-o")
            .map(|w| w[1].as_str())
            .collect();
        for want in [
            "IdentitiesOnly=yes",
            "StrictHostKeyChecking=yes",
            "UpdateHostKeys=no",
            "BatchMode=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "PermitLocalCommand=no",
            "ProxyCommand=none",
            "ProxyJump=none",
        ] {
            assert!(o.contains(&want), "reason: missing {want}");
        }
        assert!(o.contains(&format!("IdentityFile={}", m.identity_file).as_str()));
        assert!(o.contains(&format!("UserKnownHostsFile={}", kh.display()).as_str()));
        let tail = &a[a.len() - 7..];
        assert_eq!(
            tail,
            ["-l", "owner", "-p", "2222", "--", "192.0.2.7", READ],
            "reason: address_not_spelled"
        );
        let mut bad = m.clone();
        bad.hostname = "-oProxyCommand=x".into();
        assert_eq!(login_args(&bad, &kh, READ), Err("machine_values"));
        let mut bad = m.clone();
        bad.identity_file = dir.join("a b").to_string_lossy().into_owned();
        assert_eq!(login_args(&bad, &kh, READ), Err("path_not_one_word"));
    }

    #[test]
    fn the_window_and_its_ticket_hold_the_real_address_and_host_key() {
        assert_eq!(
            fingerprint(KEY).as_deref(),
            Some("SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"),
            "reason: fingerprint_not_ssh_keygen"
        );
        let m = machine(&std::env::temp_dir());
        let line = format!("restrict,command=\"/x/gate\",from=\"192.0.2.9\" {KEY} aibd-delegate");
        let ask = host_ask(OP_ADD, &m, KEY, &line, "page:p1".into());
        assert_eq!(ask.resource, "owner@192.0.2.7:2222");
        let words = crate::owner_ok::plain_words(ask.operation, &ask.params).unwrap();
        assert!(
            words.contains("owner@192.0.2.7 埠 2222"),
            "reason: window_without_address {words}"
        );
        assert!(
            words.contains("SHA256:+DiY3wvv"),
            "reason: window_without_host_key {words}"
        );
        assert!(
            words.contains("只收從 192.0.2.9 連進來"),
            "reason: window_without_from {words}"
        );
        // a line without from=, or with one the writer refuses, shows no window at all
        for bad in [
            format!("restrict,command=\"/x/gate\" {KEY} aibd-delegate"),
            format!("restrict,command=\"/x/gate\",from=\"a b\" {KEY} aibd-delegate"),
        ] {
            let a = host_ask(OP_ADD, &m, KEY, &bad, "p".into());
            assert_eq!(
                crate::owner_ok::plain_words(a.operation, &a.params),
                None,
                "reason: window_without_from_shown {bad}"
            );
        }
        // each of the four and the line changes the ticket's parameters; the alias does not
        let base = ask.params.clone();
        let mut renamed = m.clone();
        renamed.name = "other".into();
        assert_eq!(
            host_ask(OP_ADD, &renamed, KEY, &line, "p".into()).params,
            base
        );
        let mut changed: Vec<Machine> = vec![m.clone(), m.clone(), m.clone()];
        changed[0].hostname = "192.0.2.8".into();
        changed[1].port = 22;
        changed[2].user = "other".into();
        for c in &changed {
            assert_ne!(
                host_ask(OP_ADD, c, KEY, &line, "p".into()).params,
                base,
                "reason: not_bound {c:?}"
            );
        }
        assert_ne!(
            host_ask(OP_ADD, &m, "ssh-ed25519 AAAA", &line, "p".into()).params,
            base
        );
        assert_ne!(
            host_ask(OP_ADD, &m, KEY, "restrict,command=\"/y\"", "p".into()).params,
            base
        );
    }

    #[test]
    fn the_commands_are_fixed_and_run_by_sh_with_the_line_on_standard_input() {
        for s in [ADD, REMOVE] {
            let c = remote_command(s).unwrap();
            assert!(c.starts_with("/bin/sh -c '") && c.ends_with('\''));
            assert!(
                s.starts_with("IFS= read -r ") && s.contains(" || exit 3\n"),
                "reason: line_not_from_stdin"
            );
            assert!(
                !s.contains("$1") && !s.contains("$@"),
                "reason: argument_read"
            );
        }
        assert_eq!(remote_command("echo 'x'"), None, "reason: quote_passed");
    }

    #[test]
    fn a_machine_is_added_once_replaced_by_name_and_taken_away() {
        let a = with_host(&[], mini()).unwrap();
        let text = list_text(&a);
        assert_eq!(
            trusted_hosts::parse(text.as_bytes()).unwrap(),
            a,
            "reason: list_not_read_back"
        );
        let mut again = mini();
        again.hostname = "192.0.2.8".into();
        let two = with_host(
            &a,
            TrustedHost {
                name: "lin".into(),
                ..mini()
            },
        )
        .unwrap();
        let b = with_host(&two, again.clone()).unwrap();
        assert_eq!(b[0], again, "reason: replaced_machine_moved");
        assert_eq!(b.len(), 2, "reason: name_twice");
        let b = without_host(&b, "lin");
        let mut bad = mini();
        bad.hostkey = "ssh-rsa AAAA".into();
        assert_eq!(with_host(&a, bad), Err("hostkey"));
        let full: Vec<TrustedHost> = (0..trusted_hosts::MAX_HOSTS)
            .map(|i| TrustedHost {
                name: format!("m{i}"),
                ..mini()
            })
            .collect();
        assert_eq!(with_host(&full, mini()), Err("too_many_machines"));
        assert!(without_host(&b, "andymini").is_empty());
        assert_eq!(without_host(&b, "other"), b);
    }

    #[test]
    fn a_machine_is_pinned_only_when_the_gate_answered_aibds_own_key() {
        for ok in ["OK status\nstate none\n", "ERR no_such_run\n"] {
            assert_eq!(
                pinned_after_check(&[], mini(), ok),
                Ok(vec![mini()]),
                "reason: {ok}"
            );
        }
        for not in [
            "",
            "sh: status: command not found\n",
            "ERR \n",
            "ERR No such\n",
            "OK statuses\n",
        ] {
            assert_eq!(
                pinned_after_check(&[], mini(), not),
                Err("gate_did_not_answer"),
                "reason: pinned_without_gate {not:?}"
            );
        }
    }

    /// The two scripts as `/bin/sh` runs them, on a home of their own.
    #[cfg(unix)]
    #[test]
    fn the_line_is_written_once_after_a_last_newline_and_taken_away_alone() {
        use std::io::Write;
        use std::os::unix::fs::PermissionsExt;
        let line = format!("restrict,command=\"/x/gate\",from=\"192.0.2.7\" {KEY} aibd-delegate");
        let home = tempfile::tempdir().unwrap();
        let ssh = home.path().join(".ssh");
        std::fs::create_dir_all(&ssh).unwrap();
        let file = ssh.join("authorized_keys");
        std::fs::write(&file, "ssh-rsa AAAA other").unwrap(); // no last newline
        let run = |script: &str, input: &str| {
            let mut c = std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(script)
                .env_clear()
                .env("HOME", home.path())
                .env("PATH", "/usr/bin:/bin")
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .spawn()
                .unwrap();
            c.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
            let o = c.wait_with_output().unwrap();
            (
                o.status.code(),
                String::from_utf8_lossy(&o.stdout).trim().to_owned(),
            )
        };
        let input = format!("{line}\n");
        assert_eq!(run(ADD, &input), (Some(0), "ADDED 1".to_owned()));
        assert_eq!(
            run(ADD, &input),
            (Some(0), "ADDED 1".to_owned()),
            "reason: added_twice"
        );
        assert_eq!(
            std::fs::read_to_string(&file).unwrap(),
            format!("ssh-rsa AAAA other\n{line}\n"),
            "reason: glued_to_the_last_line"
        );
        let mode = std::fs::metadata(&file).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert_eq!(
            run(ADD, "ssh-rsa AAAA evil\n").0,
            Some(3),
            "reason: any_line_added"
        );
        let unmarked = format!("restrict,command=\"/x/gate\",from=\"192.0.2.7\" {KEY} other");
        assert_eq!(
            run(ADD, &format!("{unmarked}\n")).0,
            Some(3),
            "reason: unmarked_line_added"
        );
        // an older line of the same key (another gate path) and another key's marked line
        let older =
            format!("restrict,command=\"/old/gate\",from=\"192.0.2.7\" {KEY} aibd-delegate");
        let other_key = "restrict,command=\"/x/gate\" ssh-ed25519 AAAAOTHERKEY aibd-delegate";
        let mut f = std::fs::OpenOptions::new()
            .append(true)
            .open(&file)
            .unwrap();
        writeln!(f, "{older}\n{other_key}").unwrap();
        drop(f);
        assert_eq!(
            run(REMOVE, &format!("{KEY}\n")),
            (Some(0), "REMOVED 0".to_owned())
        );
        assert_eq!(
            std::fs::read_to_string(&file).unwrap(),
            format!("ssh-rsa AAAA other\n{other_key}\n"),
            "reason: other_line_lost"
        );
        assert_eq!(
            run(REMOVE, "ssh-ed25519 A B C\n").0,
            Some(3),
            "reason: any_key_taken"
        );
    }
}
