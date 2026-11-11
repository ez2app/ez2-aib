// SPDX-License-Identifier: Apache-2.0
//! The customer's side of [`crate::customer`]: how a request for their site is sent to
//! the host (4.4.1 §1; Andy 11-10: "客戶要自己在他的 aib 裡操作主機").
//!
//! The transport is the system's `ssh`. This module builds its arguments and nothing
//! else: the caller runs the program. No I/O.
//!
//! What the arguments guarantee, whatever is in the customer's own ssh configuration:
//! * **No configuration file is read** (`-F none`): no alias, proxy command, forwarding
//!   or local command comes from a file on the customer's machine.
//! * **The host must already be known** from the one file named here
//!   (`StrictHostKeyChecking=yes`): a host that answers with another key is not talked
//!   to, and nobody is asked a question.
//! * **Nothing is forwarded** and no terminal is requested: the connection carries one
//!   request and its answer.
//! * **The request is checked here first**, with the parser the host uses for that kind
//!   of line ([`parse_line`]: a site key's request, or a customer key's `sites`, `usage`
//!   and `site <code> …`), so a mistyped
//!   request is refused before a connection is made; a request not offered to customers
//!   yet is refused the same way. The host checks again: this side is
//!   a convenience, not the gate.
//! * **Nothing on the line can be read as an option**: every value is checked, and the
//!   destination comes after `--`.

use alloc::string::String;
use alloc::vec::Vec;

use crate::customer::{parse_owner_request, parse_request, OwnerRequest, Refused, Request};

/// Where a customer's requests go.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Target<'a> {
    /// Host name or IPv4 address.
    pub host: &'a str,
    /// TCP port.
    pub port: u16,
    /// Account on the host that holds the customer's key entry.
    pub user: &'a str,
    /// Private key file, when not the default identities.
    pub key: Option<&'a str>,
    /// File with the host's public key (known_hosts format).
    pub known_hosts: &'a str,
}

/// Why nothing was sent. Fixed words: what was typed is not echoed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NotSent {
    /// The host is not a plain host name or IPv4 address.
    BadHost,
    /// The port is 0.
    BadPort,
    /// The account name is not a plain account name.
    BadUser,
    /// A file path that `ssh` would read differently from how it is written.
    BadPath,
    /// The request itself is not one the host takes.
    Request(Refused),
}

impl NotSent {
    /// The word shown to the customer.
    pub fn as_str(self) -> &'static str {
        match self {
            NotSent::BadHost => "bad_host",
            NotSent::BadPort => "bad_port",
            NotSent::BadUser => "bad_user",
            NotSent::BadPath => "bad_path",
            NotSent::Request(r) => r.as_str(),
        }
    }
}

/// A host name or IPv4 address: 1–253 characters of `a-z 0-9 . -`, starting and ending
/// with a letter or digit. (IPv6 literals are not taken in this version.)
pub fn valid_host(host: &str) -> bool {
    let b = host.as_bytes();
    let edge = |c: Option<&u8>| c.is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit());
    (1..=253).contains(&b.len())
        && edge(b.first())
        && edge(b.last())
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'.' || *c == b'-')
}

/// An account name: 1–32 characters of `a-z 0-9 _ -`, starting with a letter.
pub fn valid_user(user: &str) -> bool {
    let b = user.as_bytes();
    (1..=32).contains(&b.len())
        && b.first().is_some_and(u8::is_ascii_lowercase)
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_' || *c == b'-')
}

/// A path `ssh` reads as written: not empty, at most 1024 bytes, not starting with `-`,
/// and free of blanks, control characters, quotes, `%` (which `ssh` expands) and `=`
/// and `,` (which end an option's value).
pub fn valid_path(path: &str) -> bool {
    (1..=1024).contains(&path.len())
        && !path.starts_with('-')
        && path
            .chars()
            .all(|c| !c.is_whitespace() && !c.is_control() && !"\"'%=,".contains(c))
}

/// What a line asks for, by the grammar the host will read it with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Line<'a> {
    /// A site key's request: the key says which site.
    Site(Request),
    /// A customer key's request: every site of the customer, or one named site.
    Owner(OwnerRequest<'a>),
}

impl Line<'_> {
    /// Whether this is offered to customers yet: the lists are, and of a site whatever
    /// [`Request::offered`] says, under either kind of key.
    pub fn offered(self) -> bool {
        match self {
            Line::Site(request) | Line::Owner(OwnerRequest::Site { request, .. }) => {
                request.offered()
            }
            Line::Owner(owner) => owner.offered(),
        }
    }
}

/// Check a line with the parser the host uses for it. `sites`, `usage` and `site` are
/// words of a customer key only, and no site request starts with them, so the first word
/// decides; which kind of key the customer really holds is for the host to say.
pub fn parse_line(line: &str) -> Result<Line<'_>, Refused> {
    match line.split(' ').find(|w| !w.is_empty()) {
        Some("sites" | "usage" | "site") => parse_owner_request(line).map(Line::Owner),
        _ => parse_request(line).map(Line::Site),
    }
}

/// The line for what was typed after `site`: a site request as it is (`status`), or with
/// a site code in front (`site3 status`) the customer key's `site site3 status`. Only
/// words that are a whole request after a code are read that way; anything else is left
/// alone, to be refused as the site request it is not.
pub fn site_line(typed: &str) -> String {
    let words: Vec<&str> = typed.split(' ').filter(|w| !w.is_empty()).collect();
    match words.as_slice() {
        // a customer key's own words after `site`: opening a site, a site's domain
        ["create", rest @ ..] => format!("site create {}", rest.join(" ")),
        [code, "domain", rest @ ..] if crate::site::valid_code(code) => {
            format!("site {code} domain {}", rest.join(" "))
        }
        [code, rest @ ..]
            if parse_request(typed).is_err()
                && crate::site::valid_code(code)
                && parse_request(&rest.join(" ")).is_ok() =>
        {
            format!("site {code} {}", rest.join(" "))
        }
        _ => String::from(typed),
    }
}

/// The arguments for `ssh` that send `line` to `target` — the program name not included.
/// The request is normalised to single blanks and travels as one argument.
pub fn ssh_args(target: &Target<'_>, line: &str) -> Result<Vec<String>, NotSent> {
    if !valid_host(target.host) {
        return Err(NotSent::BadHost);
    }
    if target.port == 0 {
        return Err(NotSent::BadPort);
    }
    if !valid_user(target.user) {
        return Err(NotSent::BadUser);
    }
    if !valid_path(target.known_hosts) || target.key.is_some_and(|k| !valid_path(k)) {
        return Err(NotSent::BadPath);
    }
    let asked = parse_line(line).map_err(NotSent::Request)?;
    // what is not offered yet is answered as if the word did not exist (Andy 11-10)
    if !asked.offered() {
        return Err(NotSent::Request(Refused::UnknownRequest));
    }
    let request = line
        .split(' ')
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ");

    let mut args: Vec<String> = vec!["-F".into(), "none".into(), "-T".into()];
    for option in [
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "UpdateHostKeys=no",
        "ForwardAgent=no",
        "ForwardX11=no",
        "ClearAllForwardings=yes",
        "PermitLocalCommand=no",
        "ControlMaster=no",
        "ControlPath=none",
        "ProxyCommand=none",
        "ConnectTimeout=20",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=4",
        "LogLevel=ERROR",
    ] {
        args.push("-o".into());
        args.push(option.into());
    }
    args.push("-o".into());
    args.push(format!("UserKnownHostsFile={}", target.known_hosts));
    if let Some(key) = target.key {
        args.push("-o".into());
        args.push("IdentitiesOnly=yes".into());
        args.push("-i".into());
        args.push(key.into());
    }
    args.push("-p".into());
    args.push(format!("{}", target.port));
    args.push("--".into());
    args.push(format!("{}@{}", target.user, target.host));
    args.push(request);
    Ok(args)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const T: Target<'static> = Target {
        host: "host1.example.net",
        port: 22,
        user: "site2",
        key: None,
        known_hosts: "/home/me/.ez2-aib/cloud/known_hosts",
    };

    fn with(f: impl FnOnce(&mut Target<'static>)) -> Target<'static> {
        let mut t = T;
        f(&mut t);
        t
    }

    #[test]
    fn a_request_becomes_one_fixed_shape_of_arguments() {
        let args = ssh_args(&T, "logs  nginx lines 50").unwrap();
        let tail: Vec<&str> = args
            .iter()
            .rev()
            .take(5)
            .rev()
            .map(String::as_str)
            .collect();
        assert_eq!(
            tail,
            [
                "-p",
                "22",
                "--",
                "site2@host1.example.net",
                "logs nginx lines 50"
            ]
        );
        assert_eq!(&args[..3], ["-F", "none", "-T"]);
        for must in [
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "PermitLocalCommand=no",
            "ProxyCommand=none",
            "UserKnownHostsFile=/home/me/.ez2-aib/cloud/known_hosts",
        ] {
            let at = args.iter().position(|a| a == must).expect(must);
            assert_eq!(args[at - 1], "-o", "{must}");
        }
        // every option sits before the `--`; after it come exactly two words
        let dashes = args.iter().position(|a| a == "--").unwrap();
        assert_eq!(args.len(), dashes + 3);
        // no key named: the default identities, and nothing about identities is said
        assert!(!args.iter().any(|a| a == "-i" || a == "IdentitiesOnly=yes"));
    }

    #[test]
    fn a_named_key_is_the_only_one_offered() {
        let t = with(|t| {
            t.key = Some("/home/me/.ssh/site2_ed25519");
            t.port = 2222;
        });
        let args = ssh_args(&t, "status").unwrap();
        let at = args.iter().position(|a| a == "-i").unwrap();
        assert_eq!(args[at + 1], "/home/me/.ssh/site2_ed25519");
        assert_eq!(args[at - 1], "IdentitiesOnly=yes");
        assert!(args.windows(2).any(|w| w[0] == "-p" && w[1] == "2222"));
    }

    #[test]
    fn nothing_can_be_read_as_an_option() {
        for host in [
            "",
            "-oProxyCommand=id",
            "-lroot",
            "host1.example.net-",
            ".example.net",
            "Host1",
            "host 1",
            "a@b",
            "[::1]",
            "host1;id",
        ] {
            assert_eq!(
                ssh_args(&with(|t| t.host = host), "status"),
                Err(NotSent::BadHost),
                "{host:?}"
            );
        }
        assert!(valid_host("203.0.113.90"));
        assert!(valid_host("a"));
        for user in ["", "-l", "0day", "Root", "a b", "a@b", "site2\n"] {
            assert_eq!(
                ssh_args(&with(|t| t.user = user), "status"),
                Err(NotSent::BadUser),
                "{user:?}"
            );
        }
        assert_eq!(
            ssh_args(&with(|t| t.port = 0), "status"),
            Err(NotSent::BadPort)
        );
        for path in [
            "",
            "-oProxyCommand=id",
            "/home/my name/known_hosts",
            "/tmp/%h",
            "/tmp/a\"b",
            "/tmp/a,b",
            "/tmp/a=b",
            "/tmp/a\nb",
        ] {
            assert_eq!(
                ssh_args(&with(|t| t.known_hosts = path), "status"),
                Err(NotSent::BadPath),
                "{path:?}"
            );
            assert_eq!(
                ssh_args(&with(|t| t.key = Some(path)), "status"),
                Err(NotSent::BadPath),
                "{path:?}"
            );
        }
        // a Windows path without blanks is a path
        assert!(valid_path("C:\\Users\\me\\.ez2-aib\\cloud\\known_hosts"));
    }

    #[test]
    fn a_request_the_host_would_refuse_is_not_sent() {
        assert_eq!(ssh_args(&T, ""), Err(NotSent::Request(Refused::Empty)));
        assert_eq!(
            ssh_args(&T, "status; id"),
            Err(NotSent::Request(Refused::Malformed))
        );
        assert_eq!(
            ssh_args(&T, "stop"),
            Err(NotSent::Request(Refused::UnknownRequest))
        );
        assert_eq!(
            ssh_args(&T, "status site3"),
            Err(NotSent::Request(Refused::BadArguments))
        );
        // what is not offered yet is not sent, and reads like any unknown word
        for hidden in ["php-reload", "restart", "backup"] {
            assert_eq!(
                ssh_args(&T, hidden),
                Err(NotSent::Request(Refused::UnknownRequest)),
                "{hidden}"
            );
        }
        assert_eq!(
            NotSent::Request(Refused::UnknownRequest).as_str(),
            "unknown_request"
        );
        assert_eq!(NotSent::BadHost.as_str(), "bad_host");
    }

    /// DEV 4.5 §4: a customer key asks `sites`, `usage` and `site <code> …`.
    #[test]
    fn a_customer_keys_lines_are_checked_by_their_own_grammar() {
        for line in [
            "sites",
            "usage",
            "site site3 status",
            "site  site3 logs php lines 20",
        ] {
            let args = ssh_args(&T, line).unwrap();
            let sent = args.last().unwrap();
            assert!(!sent.contains("  "), "{sent:?}");
            assert!(matches!(parse_line(line), Ok(Line::Owner(_))), "{line}");
        }
        assert_eq!(
            ssh_args(&T, "site site3 logs php lines 20")
                .unwrap()
                .last()
                .unwrap(),
            "site site3 logs php lines 20"
        );
        // the same refusals as the host's, before a connection
        assert_eq!(
            ssh_args(&T, "sites all"),
            Err(NotSent::Request(Refused::BadArguments))
        );
        assert_eq!(
            ssh_args(&T, "site site3"),
            Err(NotSent::Request(Refused::BadArguments))
        );
        assert_eq!(
            ssh_args(&T, "site ../x status"),
            Err(NotSent::Request(Refused::Malformed))
        );
        assert_eq!(
            ssh_args(&T, "site site3 stop"),
            Err(NotSent::Request(Refused::UnknownRequest))
        );
        // not offered yet stays unsent under a customer key too
        for hidden in [
            "site site3 restart",
            "site site3 php-reload",
            "site site3 backup",
            // opening a site and binding a domain: the host cannot do them yet
            "site create www.example.com",
            "site site3 domain check example.org",
            "site site3 domain add example.org",
        ] {
            assert_eq!(
                ssh_args(&T, hidden),
                Err(NotSent::Request(Refused::UnknownRequest)),
                "{hidden}"
            );
        }
    }

    #[test]
    fn a_code_in_front_of_a_request_names_the_site() {
        assert_eq!(site_line("status"), "status");
        assert_eq!(site_line("logs nginx lines 50"), "logs nginx lines 50");
        assert_eq!(site_line("site3 status"), "site site3 status");
        assert_eq!(site_line(" site3  logs php "), "site site3 logs php");
        // a site that is called like a request is still a site when a request follows
        // a request word is nobody's site code (`site::RESERVED_CODES`)
        assert_eq!(site_line("status status"), "status status");
        assert_eq!(
            site_line("create www.example.com"),
            "site create www.example.com"
        );
        assert_eq!(site_line("create"), "site create ");
        assert_eq!(
            site_line("site3  domain check example.org"),
            "site site3 domain check example.org"
        );
        // the customer key's parser refuses what is wrong with these, not this function
        assert_eq!(site_line("site3 domain"), "site site3 domain ");
        assert_eq!(site_line("create site9"), "site create site9");
        // not a request after a code, or not a code: left as typed, and refused as typed
        for typed in [
            "",
            "stop",
            "status site3",
            "site3 stop",
            "site3",
            "Site3 status",
            "site3 status;id",
        ] {
            assert_eq!(site_line(typed), typed, "{typed:?}");
            assert!(ssh_args(&T, &site_line(typed)).is_err(), "{typed:?}");
        }
    }
}
