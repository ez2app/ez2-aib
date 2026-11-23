//! Test machines handed to a partner (DESIGN-hostmode-3e53b-1123 §13 r5: 13.3, 13.7, 13.8; f163c
//! pre-read 745d1341 passed, 13.7 0/0/3/0; Andy 11-23 06:0x「把測試機交給夥伴管」).
//!
//! First slice, the deciding only: which of the machines aibd already trusts are offered, what the
//! page is told about them, and which one an add names. The managed_hosts table, the verification
//! window, the partner's key and host_exec are the next slices.
//!
//! - Candidates come only from aibd's own pinned list (`<data>/trusted/hosts.json`, written only
//!   through the owner's fingerprint by `receipt.host_add`, host key pinned), never from
//!   `~/.ez2-aib/hosts.json` or `~/.ssh/config`, which anything running as this account can write.
//! - A machine whose address is this computer, or one aibd never connects to
//!   ([`crate::host_addr::classify`]), is listed as refused and cannot be added; a `HostName` that
//!   is not an address literal is refused too (the first version resolves nothing for a partner).
//! - The page is told a name, the OS, whether it is already handed over and why not, never the
//!   address, the user or a key path (13.3, HM14); those are shown only in the verification window.
//! - An add names a machine of that list (`from: "trusted"`); a name that is not on it, a server's
//!   (EU01 …) included, is `host_not_in_list` (HM15).

use crate::host_addr::{classify, Class};
use crate::trusted_hosts::TrustedHost;
use std::net::IpAddr;

/// What a managed host is (13.2): only aibd writes it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Test,
    Server,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Test => "test",
            Kind::Server => "server",
        }
    }
}

/// Where a managed host's row came from (13.7).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Trusted,
    Manual,
}

impl Source {
    pub fn as_str(self) -> &'static str {
        match self {
            Source::Trusted => "trusted",
            Source::Manual => "manual",
        }
    }
}

/// Why a trusted machine cannot be handed to a partner, or `None`.
pub fn refused(h: &TrustedHost, own: &[IpAddr]) -> Option<&'static str> {
    let Ok(ip) = h.hostname.parse::<IpAddr>() else {
        return Some("not_an_address");
    };
    match classify(ip, own) {
        Class::Own(_) => Some("this_computer"),
        Class::Refuse(why) => Some(why),
        Class::Private | Class::Public => None,
    }
}

/// `managed_host_candidates {kind:"test"}`: name, os, already handed over, refused why. No
/// address, user, port or key.
pub fn candidates(trusted: &[TrustedHost], own: &[IpAddr], managed: &[&str]) -> serde_json::Value {
    let items: Vec<serde_json::Value> = trusted
        .iter()
        .map(|h| {
            let mut v = serde_json::json!({
                "name": h.name,
                "os": h.os,
                "already": managed.contains(&h.name.as_str()),
            });
            if let Some(why) = refused(h, own) {
                v["refused"] = serde_json::json!(why);
            }
            v
        })
        .collect();
    serde_json::json!({ "items": items })
}

/// `managed_host_add {from:"trusted", name}`: the trusted row it names, or why not.
pub fn from_trusted<'a>(
    trusted: &'a [TrustedHost],
    own: &[IpAddr],
    name: &str,
) -> Result<&'a TrustedHost, &'static str> {
    let h = trusted
        .iter()
        .find(|h| h.name == name)
        .ok_or("host_not_in_list")?;
    match refused(h, own) {
        Some(_) => Err("host_addr_refused"),
        None => Ok(h),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn host(name: &str, hostname: &str) -> TrustedHost {
        TrustedHost {
            name: name.into(),
            hostname: hostname.into(),
            port: 22,
            user: "tester".into(),
            hostkey: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample".into(),
            os: "mac".into(),
        }
    }

    fn list() -> Vec<TrustedHost> {
        vec![
            host("andymini", "100.64.0.9"),
            host("lin-macmini", "192.168.1.20"),
            host("pro16", "127.0.0.1"),
            host("self-lan", "192.168.1.9"),
            host("named", "andymini.local"),
        ]
    }

    #[test]
    fn candidates_say_name_os_and_why_not_never_where() {
        let own: Vec<IpAddr> = vec!["192.168.1.9".parse().unwrap()];
        let v = candidates(&list(), &own, &["andymini"]);
        let items = v["items"].as_array().unwrap();
        assert_eq!(items.len(), 5);
        assert_eq!(items[0]["name"], "andymini");
        assert_eq!(items[0]["already"], true);
        assert!(items[0].get("refused").is_none());
        assert_eq!(items[1]["already"], false);
        // this computer, loopback or one of its own addresses (HM12)
        assert_eq!(
            items[2]["refused"], "this_computer",
            "reason: this_computer_offered"
        );
        assert_eq!(
            items[3]["refused"], "this_computer",
            "reason: this_computer_offered"
        );
        assert_eq!(items[4]["refused"], "not_an_address");
        // nothing of where or as whom (HM14)
        let text = v.to_string();
        for x in [
            "100.64.0.9",
            "192.168.1.20",
            "tester",
            "ssh-ed25519",
            "\"port\"",
        ] {
            assert!(!text.contains(x), "reason: candidates_say_where {x}");
        }
    }

    #[test]
    fn an_add_names_a_trusted_machine_and_nothing_else() {
        let own: Vec<IpAddr> = vec!["192.168.1.9".parse().unwrap()];
        let l = list();
        assert_eq!(
            from_trusted(&l, &own, "lin-macmini").unwrap().hostname,
            "192.168.1.20"
        );
        // a server's name, or anything not on aibd's own list (HM15)
        for n in ["EU01", "SIN02", "andymini ", "", "lin-macmini\n"] {
            assert_eq!(
                from_trusted(&l, &own, n).unwrap_err(),
                "host_not_in_list",
                "reason: not_listed_taken {n:?}"
            );
        }
        for n in ["pro16", "self-lan", "named"] {
            assert_eq!(
                from_trusted(&l, &own, n).unwrap_err(),
                "host_addr_refused",
                "reason: refused_taken {n}"
            );
        }
        assert_eq!(Kind::Test.as_str(), "test");
        assert_eq!(Source::Trusted.as_str(), "trusted");
    }
}
