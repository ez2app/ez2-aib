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
//!   (a production server …) included, is `host_not_in_list` (HM15).

use crate::host_addr::{classify, Class};
use crate::trusted_hosts::TrustedHost;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::net::IpAddr;
use std::path::{Path, PathBuf};

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
    // this computer's addresses not read: refuse rather than take one of them as a LAN machine
    // (c2f0b PREREAD-egress-chain E5)
    if own.is_empty() {
        return Some("interfaces_unreadable");
    }
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

pub const PARTNER_KEY_FILE: &str = "partner_test_ed25519";
pub const PARTNER_TAG: &str = "aib-partner";

/// HM19: dedicated private key path for `kind=test` (`<data>/trusted/partner_test_ed25519`).
pub fn partner_key_path(data: &Path) -> PathBuf {
    data.join("trusted").join(PARTNER_KEY_FILE)
}

/// HM19: `-i <key>` argument pair for `kind=test` `host_exec`.
pub fn test_host_identity_args(data: &Path) -> [String; 2] {
    [
        "-i".to_owned(),
        partner_key_path(data).to_string_lossy().into_owned(),
    ]
}

/// Check that `partner_test_ed25519` exists as a regular file with `0600` permissions on Unix
/// (or owner-only DACL on Windows).
pub fn partner_key_perms_ok(path: &Path) -> bool {
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if !meta.file_type().is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.permissions().mode() & 0o777 == 0o600
    }
    #[cfg(windows)]
    {
        crate::platform::windows::verify_owner_only(path).is_ok()
    }
    #[cfg(not(any(unix, windows)))]
    {
        false
    }
}

fn valid_b64_key(b64: &str) -> bool {
    !b64.is_empty()
        && b64
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'+' | b'/' | b'='))
}

/// Format a partner `authorized_keys` line:
/// `no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from="<from_ip>" <pubkey> aib-partner`
pub fn format_partner_line(from_ip: &str, pubkey: &str) -> Result<String, &'static str> {
    let parsed_ip: std::net::IpAddr = from_ip.parse().map_err(|_| "bad_partner_line_ip")?;
    if pubkey.contains(['\n', '\r']) {
        return Err("bad_partner_line_input");
    }
    let mut fields = pubkey.split_whitespace();
    let (Some("ssh-ed25519"), Some(b64), None) = (fields.next(), fields.next(), fields.next())
    else {
        return Err("bad_partner_line_pubkey");
    };
    if !valid_b64_key(b64) {
        return Err("bad_partner_line_pubkey");
    }
    Ok(format!(
        "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from=\"{parsed_ip}\" ssh-ed25519 {b64} {PARTNER_TAG}"
    ))
}

/// HM17: strictly validate a partner `authorized_keys` line (exact 3 forwarding restrictions +
/// `no-user-rc`, `from="<IpAddr>"`, no `command=`, `ssh-ed25519` key, trailing `aib-partner`).
pub fn valid_partner_line(line: &str) -> bool {
    if line.contains('\n') || line.contains('\r') {
        return false;
    }
    let Some((opts, rest)) = line.split_once(' ') else {
        return false;
    };
    let parts: Vec<&str> = opts.split(',').collect();
    if parts.len() != 5 {
        return false;
    }
    if parts[0] != "no-agent-forwarding"
        || parts[1] != "no-port-forwarding"
        || parts[2] != "no-X11-forwarding"
        || parts[3] != "no-user-rc"
    {
        return false;
    }
    if !parts[4].starts_with("from=\"") || !parts[4].ends_with('"') || parts[4].len() <= 7 {
        return false;
    }
    let ip_str = &parts[4][6..parts[4].len() - 1];
    if ip_str.parse::<std::net::IpAddr>().is_err() {
        return false;
    }
    if opts.contains("command=") {
        return false;
    }
    let mut fields = rest.split_whitespace();
    let Some(alg) = fields.next() else {
        return false;
    };
    let Some(b64) = fields.next() else {
        return false;
    };
    let Some(tag) = fields.next() else {
        return false;
    };
    alg == "ssh-ed25519" && valid_b64_key(b64) && tag == PARTNER_TAG && fields.next().is_none()
}

fn line_sha(line: &str) -> String {
    let d = Sha256::digest(line.as_bytes());
    d.iter().map(|b| format!("{b:02x}")).collect()
}

/// HM18: snapshot normalized non-empty lines of `~/.ssh/authorized_keys` as SHA-256 hex hashes.
pub fn snapshot_lines(authorized_keys: &str) -> Vec<String> {
    authorized_keys
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(line_sha)
        .collect()
}

/// Outcome status of revoking partner access: blocks `"completed"` when non-partner keys
/// were added during handoff so a human must decide whether to keep or delete them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RevokeStatus {
    Completed,
    PendingHumanDecision,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RevokeOutcome {
    pub remaining: String,
    pub added: Vec<String>,
    pub status: RevokeStatus,
}

impl RevokeOutcome {
    pub fn event_payload(&self, host_name: &str) -> serde_json::Value {
        serde_json::json!({
            "host": host_name,
            "status": match self.status {
                RevokeStatus::Completed => "completed",
                RevokeStatus::PendingHumanDecision => "pending_human_decision",
            },
            "added_count": self.added.len(),
            "added_lines": self.added,
        })
    }
}

/// HM18: revoke partner lines (`aib-partner`) and return [`RevokeOutcome`].
/// If non-partner lines were added during handoff, `status` is `PendingHumanDecision`
/// (never marks `"completed"` silently).
pub fn revoke_and_diff(authorized_keys: &str, before_hashes: &[String]) -> RevokeOutcome {
    let before: BTreeSet<&str> = before_hashes.iter().map(String::as_str).collect();
    let mut kept = Vec::new();
    let mut added = Vec::new();
    for raw in authorized_keys.lines() {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        if valid_partner_line(trimmed) {
            continue;
        }
        let h = line_sha(trimmed);
        if !before.contains(h.as_str()) {
            added.push(trimmed.to_owned());
        }
        kept.push(raw);
    }
    let remaining = if kept.is_empty() {
        String::new()
    } else {
        format!("{}\n", kept.join("\n"))
    };
    let status = if added.is_empty() {
        RevokeStatus::Completed
    } else {
        RevokeStatus::PendingHumanDecision
    };
    RevokeOutcome {
        remaining,
        added,
        status,
    }
}

/// Choose the SSH private key path for a host kind: `"test"` uses `partner_test_ed25519`,
/// otherwise `receipt_link::key_path` (`delegate_ed25519`).
pub fn key_for_kind(data: &Path, kind: &str) -> PathBuf {
    if kind == "test" {
        partner_key_path(data)
    } else {
        crate::receipt_link::key_path(data)
    }
}

/// Persist the pre-handoff `~/.ssh/authorized_keys` line-hash snapshot in `aib_store`.
pub fn save_snapshot(
    db: &aib_store::MessageDb,
    host_name: &str,
    authorized_keys: &str,
    now: i64,
) -> Result<(), aib_store::StoreError> {
    let lines = snapshot_lines(authorized_keys);
    db.test_host_snapshot_set(host_name, &lines, now)
}

/// Load the stored snapshot, revoke partner lines, return [`RevokeOutcome`],
/// and delete the snapshot row only when [`RevokeStatus::Completed`].
pub fn revoke_and_diff_from_db(
    db: &aib_store::MessageDb,
    host_name: &str,
    authorized_keys: &str,
) -> Result<RevokeOutcome, aib_store::StoreError> {
    let before = db.test_host_snapshot_get(host_name)?.unwrap_or_default();
    let res = revoke_and_diff(authorized_keys, &before);
    if res.status == RevokeStatus::Completed {
        let _ = db.test_host_snapshot_del(host_name)?;
    }
    Ok(res)
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
        for n in ["prod-1", "prod-2", "andymini ", "", "lin-macmini\n"] {
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
        assert_eq!(
            from_trusted(&l, &[], "lin-macmini").unwrap_err(),
            "host_addr_refused",
            "reason: no_interfaces_taken"
        );
        assert_eq!(Kind::Test.as_str(), "test");
        assert_eq!(Source::Trusted.as_str(), "trusted");
    }

    #[test]
    fn hm17_partner_line_is_strictly_validated() {
        let good =
            format_partner_line("100.64.0.2", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest").unwrap();
        assert!(good.contains("no-user-rc"));
        assert!(valid_partner_line(&good));
        // Wildcard * or non-IP in from= is refused on both build and validate
        assert!(format_partner_line("*", "ssh-ed25519 AAAA").is_err());
        assert!(!valid_partner_line(
            "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from=\"*\" ssh-ed25519 AAAA aib-partner"
        ));
        // Missing no-user-rc or forwarding flags is refused
        assert!(!valid_partner_line(
            "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,from=\"100.64.0.2\" ssh-ed25519 AAAA aib-partner"
        ));
        assert!(!valid_partner_line(
            "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from=\"100.64.0.2\",command=\"sh\" ssh-ed25519 AAAA aib-partner"
        ));
        assert!(!valid_partner_line(
            "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from=\"100.64.0.2\",no-pty ssh-ed25519 AAAA aib-partner"
        ));
        // Pubkey with comment, extra fields, or non-base64 chars is refused at build/validate time
        assert!(format_partner_line("100.64.0.2", "ssh-ed25519 AAAA extra@host").is_err());
        assert!(format_partner_line("100.64.0.2", "ssh-ed25519 AAAA$bad").is_err());
        assert!(!valid_partner_line(
            "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,from=\"100.64.0.2\" ssh-ed25519 AAAA$bad aib-partner"
        ));
    }

    #[test]
    fn hm18_revoke_removes_partner_line_and_lists_added_lines() {
        let before = "ssh-ed25519 AAAABase owner@mac\n";
        let snap = snapshot_lines(before);
        let partner = format_partner_line("100.64.0.2", "ssh-ed25519 AAAAPartner").unwrap();
        let after_clean = format!("{before}{partner}\n");
        let clean = revoke_and_diff(&after_clean, &snap);
        assert_eq!(clean.status, RevokeStatus::Completed);
        assert!(clean.added.is_empty());

        let after_rogue = format!("{before}{partner}\nssh-ed25519 AAAAExtra rogue@seat\n");
        let out = revoke_and_diff(&after_rogue, &snap);
        assert_eq!(out.status, RevokeStatus::PendingHumanDecision);
        assert_eq!(
            out.remaining,
            "ssh-ed25519 AAAABase owner@mac\nssh-ed25519 AAAAExtra rogue@seat\n"
        );
        assert_eq!(out.added, vec!["ssh-ed25519 AAAAExtra rogue@seat"]);
        let ev = out.event_payload("mini-partner");
        assert_eq!(ev["status"], "pending_human_decision");
        assert_eq!(ev["added_count"], 1);
    }

    #[test]
    fn hm19_test_host_uses_dedicated_partner_key() {
        let data = Path::new("/tmp/aibd-data");
        let args = test_host_identity_args(data);
        assert_eq!(args[0], "-i");
        assert!(args[1].ends_with("trusted/partner_test_ed25519"));
        assert!(!args[1].contains("delegate_ed25519"));
        assert_eq!(key_for_kind(data, "test"), partner_key_path(data));
        assert_eq!(
            key_for_kind(data, "receipts"),
            crate::receipt_link::key_path(data)
        );
        // 0600 check on a real temp file
        let d = tempfile::tempdir().unwrap();
        let kp = d.path().join(PARTNER_KEY_FILE);
        std::fs::write(&kp, b"key").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&kp, std::fs::Permissions::from_mode(0o644)).unwrap();
            assert!(!partner_key_perms_ok(&kp));
            std::fs::set_permissions(&kp, std::fs::Permissions::from_mode(0o600)).unwrap();
            assert!(partner_key_perms_ok(&kp));
        }
    }

    #[test]
    fn hm18_db_snapshot_roundtrip_and_revoke_diff() {
        let d = tempfile::tempdir().unwrap();
        let db = aib_store::MessageDb::open(&d.path().join("m.db")).unwrap();
        let before = "ssh-ed25519 AAAABase owner@mac\n";
        save_snapshot(&db, "mini-partner", before, 1000).unwrap();
        let partner = format_partner_line("100.64.0.2", "ssh-ed25519 AAAAPartner").unwrap();
        let after = format!("{before}{partner}\nssh-ed25519 AAAAExtra rogue@seat\n");
        let out = revoke_and_diff_from_db(&db, "mini-partner", &after).unwrap();
        assert_eq!(out.status, RevokeStatus::PendingHumanDecision);
        assert_eq!(
            out.remaining,
            "ssh-ed25519 AAAABase owner@mac\nssh-ed25519 AAAAExtra rogue@seat\n"
        );
        assert_eq!(out.added, vec!["ssh-ed25519 AAAAExtra rogue@seat"]);
        assert!(db.test_host_snapshot_get("mini-partner").unwrap().is_some());

        let clean_after = format!("{before}{partner}\n");
        let clean = revoke_and_diff_from_db(&db, "mini-partner", &clean_after).unwrap();
        assert_eq!(clean.status, RevokeStatus::Completed);
        assert!(db.test_host_snapshot_get("mini-partner").unwrap().is_none());
    }
}
