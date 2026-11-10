// SPDX-License-Identifier: Apache-2.0
//! Minting a per-host Cloudflare R2 token for off-site backups (4.4.1 §10.1,
//! Andy 11-09: "key 從 host B 取得,再彈出生物辨識").
//!
//! Pure planning logic, same contract as [`crate::robot`]: validate operator
//! input once, derive **the approval facts, the API policy body and the remote
//! job spec from the same validated values**, and judge the verification
//! report the destination host sends back. No I/O here — the ssh pipeline
//! (credential host → destination host) and the Touch ID gate are ports; the
//! Python PoC `aib/tools/cloud-host/r2token.py` (#563, draft) is the acceptance
//! baseline until the Rust shell replaces it.
//!
//! Rules this module enforces (mirrors R1–R7 of the PoC's test):
//! - bucket, host alias, egress IP, zone, rclone remote name and destination
//!   path are validated; anything else is refused before a plan exists;
//! - the token policy is scoped to **one bucket** (`…r2.bucket.<account>_default_<bucket>`),
//!   **two permission groups** (Item Read + Item Write) and **one IPv4 /32**;
//! - the popup names exactly the bucket / host / IP that the policy uses;
//! - a verification report counts as success only when all five checks pass
//!   (list own bucket, write, read back, delete blocked by bucket lock, other
//!   bucket denied);
//! - anything secret-looking in a report is dropped before it can be printed.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;

use serde_json::{json, Value};

/// Cloudflare permission group "Workers R2 Storage Bucket Item Read".
pub const PERM_ITEM_READ: &str = "6a018a9f2fc74eb6b293b0c548f38b39";
/// Cloudflare permission group "Workers R2 Storage Bucket Item Write".
pub const PERM_ITEM_WRITE: &str = "2efd5506f9c8494dacb1fa10a3e7d5b6";

/// Why a plan could not be built or a report could not be judged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Bucket name breaks the S3/R2 naming rules.
    Bucket(String),
    /// Not a usable public IPv4 address.
    Ip(String),
    /// ssh alias / host name has characters ssh would misread.
    Host(String),
    /// Zone used to look up the account is not a DNS name.
    Zone(String),
    /// Destination path is not absolute or has odd characters.
    Dest(String),
    /// rclone remote section name is not `[A-Za-z0-9_-]+`.
    Remote(String),
    /// Custom token name has characters Cloudflare / the popup should not see.
    Name(String),
    /// Account id is not 32 lowercase hex characters.
    Account(String),
    /// The verification report is missing a field or has the wrong shape.
    Report(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Bucket(s) => write!(f, "bucket name refused: {s}"),
            Error::Ip(s) => write!(f, "egress ip refused: {s}"),
            Error::Host(s) => write!(f, "host alias refused: {s}"),
            Error::Zone(s) => write!(f, "zone refused: {s}"),
            Error::Dest(s) => write!(f, "destination path refused: {s}"),
            Error::Remote(s) => write!(f, "rclone remote name refused: {s}"),
            Error::Name(s) => write!(f, "token name refused: {s}"),
            Error::Account(s) => write!(f, "account id refused: {s}"),
            Error::Report(s) => write!(f, "verification report unusable: {s}"),
        }
    }
}

impl core::error::Error for Error {}

fn is_ascii_lower_digit_dash(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'
}

/// An R2 bucket name: 3–63 chars of `[a-z0-9-]`, no leading / trailing dash.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BucketName(String);

impl BucketName {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let n = s.chars().count();
        if !(3..=63).contains(&n) {
            return Err(Error::Bucket(format!("length {n} not in 3..=63")));
        }
        if !s.chars().all(is_ascii_lower_digit_dash) {
            return Err(Error::Bucket("only a-z 0-9 and '-' allowed".into()));
        }
        if s.starts_with('-') || s.ends_with('-') {
            return Err(Error::Bucket("must not start or end with '-'".into()));
        }
        Ok(BucketName(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// An ssh alias or host name: `[A-Za-z0-9._-]`, 1–64 chars, not starting with `-` or `.`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostAlias(String);

impl HostAlias {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let n = s.chars().count();
        if !(1..=64).contains(&n) {
            return Err(Error::Host(format!("length {n} not in 1..=64")));
        }
        if !s
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        {
            return Err(Error::Host("only A-Z a-z 0-9 . _ - allowed".into()));
        }
        if s.starts_with('-') || s.starts_with('.') {
            return Err(Error::Host("must not start with '-' or '.'".into()));
        }
        Ok(HostAlias(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// The destination host's IPv4 egress address; the token is usable only from it.
/// Parsed by hand (four dotted ASCII octets) because this crate's `src/` must not
/// touch the standard network module (the crate is no_std; #532 M4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EgressIp([u8; 4]);

impl EgressIp {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let parts: Vec<&str> = s.split('.').collect();
        if parts.len() != 4 {
            return Err(Error::Ip(format!("not an IPv4 address: {s}")));
        }
        let mut o = [0u8; 4];
        for (i, part) in parts.iter().enumerate() {
            if part.is_empty() || part.len() > 3 || !part.chars().all(|c| c.is_ascii_digit()) {
                return Err(Error::Ip(format!("not an IPv4 address: {s}")));
            }
            if part.len() > 1 && part.starts_with('0') {
                return Err(Error::Ip(format!(
                    "octet with leading zero is ambiguous (#564 L1): {s}"
                )));
            }
            o[i] = part
                .parse::<u16>()
                .ok()
                .filter(|v| *v <= 255)
                .map(|v| v as u8)
                .ok_or_else(|| Error::Ip(format!("octet out of range: {s}")))?;
        }
        // Only a public unicast address can be a host's egress IP. Everything below is refused
        // with a reason, because the usual slip is pasting a Tailscale 100.64/10 address (#564 L1).
        let why = if o[0] == 0 {
            Some("0.0.0.0/8 (this network)")
        } else if o[0] == 10 {
            Some("10.0.0.0/8 (private)")
        } else if o[0] == 127 {
            Some("127.0.0.0/8 (loopback)")
        } else if o[0] == 169 && o[1] == 254 {
            Some("169.254.0.0/16 (link-local)")
        } else if o[0] == 172 && (16..=31).contains(&o[1]) {
            Some("172.16.0.0/12 (private)")
        } else if o[0] == 192 && o[1] == 168 {
            Some("192.168.0.0/16 (private)")
        } else if o[0] == 100 && (64..=127).contains(&o[1]) {
            Some("100.64.0.0/10 (carrier-grade NAT / Tailscale, not the host's public egress)")
        } else if o[0] == 192 && o[1] == 0 && o[2] == 2 {
            Some("192.0.2.0/24 (documentation range)")
        } else if o[0] == 198 && o[1] == 51 && o[2] == 100 {
            Some("198.51.100.0/24 (documentation range)")
        } else if o[0] == 203 && o[1] == 0 && o[2] == 113 {
            Some("203.0.113.0/24 (documentation range)")
        } else if o[0] == 198 && (18..=19).contains(&o[1]) {
            Some("198.18.0.0/15 (benchmarking range)")
        } else if o[0] >= 224 {
            Some("224.0.0.0/3 (multicast / reserved / broadcast)")
        } else {
            None
        };
        if let Some(why) = why {
            return Err(Error::Ip(format!(
                "{s} is in {why}; want the host's public IPv4 egress"
            )));
        }
        Ok(EgressIp(o))
    }

    pub fn octets(&self) -> [u8; 4] {
        self.0
    }

    /// Dotted form, e.g. `8.8.8.8`.
    pub fn text(&self) -> String {
        format!("{}.{}.{}.{}", self.0[0], self.0[1], self.0[2], self.0[3])
    }

    /// The exact condition written into the token: a single-address CIDR.
    pub fn cidr(&self) -> String {
        format!("{}/32", self.text())
    }
}

/// A DNS zone name used only to look up the account id on the credential host.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Zone(String);

impl Zone {
    pub fn parse(s: &str) -> Result<Self, Error> {
        let ok_label = |l: &str| {
            !l.is_empty()
                && l.len() <= 63
                && l.chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
                && !l.starts_with('-')
                && !l.ends_with('-')
        };
        let labels: Vec<&str> = s.split('.').collect();
        if s.len() > 253 || labels.len() < 2 || !labels.iter().all(|l| ok_label(l)) {
            return Err(Error::Zone(format!("not a lowercase DNS name: {s}")));
        }
        Ok(Zone(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Cloudflare account id: 32 lowercase hex characters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccountId(String);

impl AccountId {
    pub fn parse(s: &str) -> Result<Self, Error> {
        if s.len() != 32
            || !s
                .chars()
                .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
        {
            return Err(Error::Account("expected 32 lowercase hex chars".into()));
        }
        Ok(AccountId(s.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// What the Touch ID popup shows. Every field is the full validated value —
/// the popup can never name a different bucket / host / IP than the policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApprovalFacts {
    pub bucket: String,
    pub host: String,
    pub ip: String,
}

/// A validated, immutable mint plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    bucket: BucketName,
    host: HostAlias,
    ip: EgressIp,
    creds_host: HostAlias,
    zone: Zone,
    dest: String,
    remote: String,
    name: String,
}

impl Plan {
    /// Build a plan. `name == None` derives `ez2-cloud-<host without -ts>-<bucket>-r2`.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bucket: &str,
        host: &str,
        ip: &str,
        creds_host: &str,
        zone: &str,
        dest: &str,
        remote: &str,
        name: Option<&str>,
    ) -> Result<Self, Error> {
        let bucket = BucketName::parse(bucket)?;
        let host = HostAlias::parse(host)?;
        let ip = EgressIp::parse(ip)?;
        let creds_host = HostAlias::parse(creds_host)?;
        let zone = Zone::parse(zone)?;
        let file_name = dest.rsplit('/').next().unwrap_or("");
        if !dest.starts_with('/')
            || dest.contains("..")
            || dest.contains("//")
            || dest.len() > 200
            || file_name.is_empty()
            || file_name == "."
            || !dest
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '.' | '_' | '-'))
        {
            return Err(Error::Dest(format!(
                "want an absolute plain path ending in a file name, got {dest}"
            )));
        }
        if remote.is_empty()
            || remote.len() > 32
            || !remote
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            return Err(Error::Remote(format!(
                "want [A-Za-z0-9_-]{{1,32}}, got {remote}"
            )));
        }
        let name = match name {
            Some(n) => {
                if n.is_empty()
                    || n.len() > 120
                    || !n
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
                {
                    return Err(Error::Name(format!("token name refused: {n}")));
                }
                n.to_owned()
            }
            None => format!(
                "ez2-cloud-{}-{}-r2",
                host.as_str().strip_suffix("-ts").unwrap_or(host.as_str()),
                bucket.as_str()
            ),
        };
        Ok(Plan {
            bucket,
            host,
            ip,
            creds_host,
            zone,
            dest: dest.to_owned(),
            remote: remote.to_owned(),
            name,
        })
    }

    pub fn bucket(&self) -> &BucketName {
        &self.bucket
    }
    pub fn host(&self) -> &HostAlias {
        &self.host
    }
    pub fn ip(&self) -> EgressIp {
        self.ip
    }
    pub fn creds_host(&self) -> &HostAlias {
        &self.creds_host
    }
    pub fn zone(&self) -> &Zone {
        &self.zone
    }
    pub fn dest(&self) -> &str {
        &self.dest
    }
    pub fn remote(&self) -> &str {
        &self.remote
    }
    pub fn token_name(&self) -> &str {
        &self.name
    }

    /// Facts for the approval popup (site `cloud_r2token.mint`, all three untruncated).
    pub fn approval_facts(&self) -> ApprovalFacts {
        ApprovalFacts {
            bucket: self.bucket.as_str().to_owned(),
            host: self.host.as_str().to_owned(),
            ip: self.ip.text(),
        }
    }

    /// The exact resource key Cloudflare expects for one bucket of one account.
    pub fn resource_key(&self, account: &AccountId) -> String {
        format!(
            "com.cloudflare.edge.r2.bucket.{}_default_{}",
            account.as_str(),
            self.bucket.as_str()
        )
    }

    /// Body of `POST /accounts/<account>/tokens`: one bucket, two permission groups, one /32.
    pub fn policy_body(&self, account: &AccountId) -> Value {
        json!({
            "name": self.name,
            "policies": [{
                "effect": "allow",
                "resources": { self.resource_key(account): "*" },
                "permission_groups": [{ "id": PERM_ITEM_READ }, { "id": PERM_ITEM_WRITE }],
            }],
            "condition": { "request_ip": { "in": [ self.ip.cidr() ] } },
        })
    }

    /// The job handed to the credential host (it looks the account up by `zone`, then
    /// builds the same body as [`Plan::policy_body`]).
    pub fn remote_spec(&self) -> Value {
        json!({
            "zone": self.zone.as_str(),
            "bucket": self.bucket.as_str(),
            "ip": self.ip.text(),
            "name": self.name,
            "perm_read": PERM_ITEM_READ,
            "perm_write": PERM_ITEM_WRITE,
        })
    }

    /// What `plan` mode prints: nothing secret, nothing executable.
    pub fn summary(&self) -> Value {
        json!({
            "bucket": self.bucket.as_str(),
            "host": self.host.as_str(),
            "ip": self.ip.text(),
            "ip_condition": self.ip.cidr(),
            "creds_host": self.creds_host.as_str(),
            "zone": self.zone.as_str(),
            "dest": self.dest,
            "remote_name": self.remote,
            "name": self.name,
            "perms": ["R2 Storage Bucket Item Read", "R2 Storage Bucket Item Write"],
        })
    }
}

/// The five checks the destination host runs with the freshly installed token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifyReport {
    pub token_id: String,
    pub list_own_bucket: bool,
    pub write: bool,
    pub read_back: bool,
    pub delete_blocked_by_lock: bool,
    pub other_bucket_denied: bool,
}

impl VerifyReport {
    /// Parse the JSON the destination host prints. Missing / non-bool fields are an error,
    /// not a silent `false`, so a half-written report cannot pass as "all checked".
    pub fn from_json(v: &Value) -> Result<Self, Error> {
        let obj = v
            .as_object()
            .ok_or_else(|| Error::Report("not an object".into()))?;
        if obj.get("ok").and_then(Value::as_bool) == Some(false) {
            let why = obj.get("why").and_then(Value::as_str).unwrap_or("unknown");
            return Err(Error::Report(format!("remote reported failure: {why}")));
        }
        let b = |k: &str| {
            obj.get(k)
                .and_then(Value::as_bool)
                .ok_or_else(|| Error::Report(format!("missing bool {k}")))
        };
        let token_id = obj
            .get("token_id")
            .and_then(Value::as_str)
            .filter(|s| s.len() == 32 && s.chars().all(|c| c.is_ascii_hexdigit()))
            .ok_or_else(|| Error::Report("missing or malformed token_id".into()))?
            .to_owned();
        Ok(VerifyReport {
            token_id,
            list_own_bucket: b("list_own_bucket")?,
            write: b("write")?,
            read_back: b("read_back")?,
            delete_blocked_by_lock: b("delete_blocked_by_lock")?,
            other_bucket_denied: b("other_bucket_denied")?,
        })
    }

    /// All five checks passed.
    pub fn ok(&self) -> bool {
        self.failures().is_empty()
    }

    /// Names of the checks that did not pass, in a fixed order.
    pub fn failures(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if !self.list_own_bucket {
            out.push("list_own_bucket");
        }
        if !self.write {
            out.push("write");
        }
        if !self.read_back {
            out.push("read_back");
        }
        if !self.delete_blocked_by_lock {
            out.push("delete_blocked_by_lock");
        }
        if !self.other_bucket_denied {
            out.push("other_bucket_denied");
        }
        out
    }
}

/// Mask a report before it is printed: the crate-wide [`crate::robot::mask`] (substring key
/// match on password / token / secret / key / auth / cookie …, plus `scheme://user:pw@`
/// userinfo inside strings), so a remote side that renames a key cannot leak a value
/// (#564 M1). On top of that, any key named `value` is masked (Cloudflare returns the raw
/// token under `result.value`). `token_id` is the one token-ish key the report legitimately
/// carries; it is kept only when it is exactly a Cloudflare token id (32 hex chars). A
/// 64-hex value in that slot is the shape of `secret_access_key` and gets masked (#564 r2 L2).
pub fn sanitize(v: &mut Value) {
    let keep = v
        .get("token_id")
        .and_then(Value::as_str)
        .filter(|s| s.len() == 32 && s.chars().all(|c| c.is_ascii_hexdigit()))
        .map(str::to_owned);
    crate::robot::mask(v);
    mask_value_keys(v);
    if let (Some(id), Some(obj)) = (keep, v.as_object_mut()) {
        obj.insert("token_id".to_owned(), Value::String(id));
    }
}

fn mask_value_keys(v: &mut Value) {
    match v {
        Value::Object(m) => {
            for (k, x) in m.iter_mut() {
                if k.eq_ignore_ascii_case("value") {
                    *x = Value::String("***".to_owned());
                } else {
                    mask_value_keys(x);
                }
            }
        }
        Value::Array(a) => a.iter_mut().for_each(mask_value_keys),
        _ => {}
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::string::ToString;

    fn plan() -> Plan {
        Plan::new(
            "ez2-cloud-host-a",
            "host-a-ts",
            "8.8.8.8",
            "host-b-ts",
            "ez2.work",
            "/etc/ez2-cloud/backup-r2.env",
            "r2",
            None,
        )
        .unwrap()
    }

    fn account() -> AccountId {
        AccountId::parse("3d7f55aaaaaaaaaaaaaaaaaaaaaaaaaa").unwrap()
    }

    #[test]
    fn r1_summary_has_no_secret_and_marks_ip_condition() {
        let s = plan().summary();
        assert_eq!(s["ip_condition"], "8.8.8.8/32");
        assert_eq!(s["name"], "ez2-cloud-host-a-ez2-cloud-host-a-r2");
        let text = s.to_string();
        for k in ["secret", "access_key", "value", "authorization"] {
            assert!(!text.contains(k), "{k} leaked into summary");
        }
    }

    #[test]
    fn r2_policy_is_one_bucket_two_perms_one_cidr() {
        let p = plan();
        let b = p.policy_body(&account());
        let res = b["policies"][0]["resources"].as_object().unwrap();
        assert_eq!(res.len(), 1);
        assert_eq!(
            res.keys().next().unwrap(),
            "com.cloudflare.edge.r2.bucket.3d7f55aaaaaaaaaaaaaaaaaaaaaaaaaa_default_ez2-cloud-host-a"
        );
        let perms: Vec<&str> = b["policies"][0]["permission_groups"]
            .as_array()
            .unwrap()
            .iter()
            .map(|g| g["id"].as_str().unwrap())
            .collect();
        assert_eq!(perms, vec![PERM_ITEM_READ, PERM_ITEM_WRITE]);
        assert_eq!(b["condition"]["request_ip"]["in"], json!(["8.8.8.8/32"]));
        assert_eq!(b["policies"][0]["effect"], "allow");
    }

    #[test]
    fn r2b_popup_names_exactly_what_the_policy_uses() {
        let p = plan();
        let f = p.approval_facts();
        let spec = p.remote_spec();
        assert_eq!(
            (f.bucket.as_str(), f.host.as_str(), f.ip.as_str()),
            ("ez2-cloud-host-a", "host-a-ts", "8.8.8.8")
        );
        assert_eq!(spec["bucket"], f.bucket);
        assert_eq!(spec["ip"], f.ip);
        assert_eq!(spec["perm_read"], PERM_ITEM_READ);
        assert_eq!(spec["perm_write"], PERM_ITEM_WRITE);
        assert_eq!(spec["zone"], "ez2.work");
    }

    #[test]
    fn r3_bucket_rules() {
        for bad in [
            "ab",
            "Upper",
            "has_underscore",
            "-lead",
            "trail-",
            "a.b",
            "１２３",
            &"x".repeat(64),
        ] {
            assert!(
                matches!(BucketName::parse(bad), Err(Error::Bucket(_))),
                "{bad:?} should be refused"
            );
        }
        assert!(BucketName::parse("ez2-cloud-host-a").is_ok());
        assert!(BucketName::parse("abc").is_ok());
    }

    #[test]
    fn r4_ip_rules() {
        for bad in [
            "2a01:4f9:4b:134f::2",
            "0.0.0.0",
            "0.1.2.3",
            "127.0.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "169.254.1.1",
            "10.1.1.1",
            "172.16.5.5",
            "172.31.255.1",
            "192.168.1.1",
            "100.64.0.1",
            "100.64.0.46",
            "010.1.1.1",
            "8.8.08.8", // #564 r2 L1: leading zero on an otherwise public address
            "8.8.8.08",
            "192.0.2.10", // #564 r2 L3: documentation / benchmark ranges
            "198.51.100.7",
            "203.0.113.200",
            "198.18.0.1",
            "198.19.255.254",
            "1.2.3",
            "１.2.3.4",
            "1.2.3.256",
            "01.2.3.4.5",
            "1.2.3.4 ",
        ] {
            assert!(
                matches!(EgressIp::parse(bad), Err(Error::Ip(_))),
                "{bad:?} should be refused"
            );
        }
        assert_eq!(EgressIp::parse("8.8.8.8").unwrap().cidr(), "8.8.8.8/32");
        assert!(
            matches!(EgressIp::parse("8.8.08.8"), Err(Error::Ip(m)) if m.contains("leading zero"))
        );
        assert!(EgressIp::parse("198.17.0.1").is_ok()); // just outside 198.18/15
        assert!(EgressIp::parse("198.20.0.1").is_ok());
        assert!(EgressIp::parse("172.32.0.1").is_ok()); // just outside 172.16/12
        assert!(EgressIp::parse("100.128.0.1").is_ok()); // just outside 100.64/10
        assert!(
            matches!(EgressIp::parse("100.64.0.46"), Err(Error::Ip(m)) if m.contains("Tailscale"))
        );
    }

    #[test]
    fn r5_host_zone_dest_remote_rules() {
        for bad in ["", "-x", ".x", "a b", "a;b", "a/b", &"h".repeat(65)] {
            assert!(
                matches!(HostAlias::parse(bad), Err(Error::Host(_))),
                "{bad:?}"
            );
        }
        assert!(HostAlias::parse("host-a-ts").is_ok());
        for bad in ["ez2", "Ez2.work", "-a.b", "a..b", "a.b-"] {
            assert!(matches!(Zone::parse(bad), Err(Error::Zone(_))), "{bad:?}");
        }
        let mk = |dest: &str, remote: &str| {
            Plan::new("abc", "h", "8.8.8.8", "c", "a.b", dest, remote, None)
        };
        assert!(matches!(mk("relative.env", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/etc/../x", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/etc/x;rm", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/", "r2"), Err(Error::Dest(_)))); // #564 L2: must name a file
        assert!(matches!(mk("/etc/ez2-cloud/", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/etc//x", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/etc/.", "r2"), Err(Error::Dest(_))));
        assert!(matches!(mk("/etc/x", "r 2"), Err(Error::Remote(_))));
        assert!(matches!(mk("/etc/x", ""), Err(Error::Remote(_))));
        assert!(mk("/etc/x", "r2").is_ok());
        assert!(matches!(
            Plan::new(
                "abc",
                "h",
                "8.8.8.8",
                "c",
                "a.b",
                "/etc/x",
                "r2",
                Some("bad name")
            ),
            Err(Error::Name(_)) // #564 L3
        ));
        assert!(matches!(
            AccountId::parse("3D7F55AAAAAAAAAAAAAAAAAAAAAAAAAA"),
            Err(Error::Account(_))
        ));
        assert!(matches!(AccountId::parse("3d7f55"), Err(Error::Account(_))));
    }

    #[test]
    fn r6_report_all_five_or_nothing() {
        let good = json!({"ok": true, "token_id": "709325321f3e207ca9c8152720134ae2", "list_own_bucket": true, "write": true,
                          "read_back": true, "delete_blocked_by_lock": true, "other_bucket_denied": true});
        let r = VerifyReport::from_json(&good).unwrap();
        assert!(r.ok());
        assert!(r.failures().is_empty());
        // #564 M2 (ebb36): each of the five checks on its own must fail the report and be
        // named; dropping any one of them from `failures()` turns this red.
        const CHECKS: [&str; 5] = [
            "list_own_bucket",
            "write",
            "read_back",
            "delete_blocked_by_lock",
            "other_bucket_denied",
        ];
        for k in CHECKS {
            let mut one_off = good.clone();
            one_off[k] = json!(false);
            let r = VerifyReport::from_json(&one_off).unwrap();
            assert!(!r.ok(), "{k}=false must fail the report");
            assert_eq!(r.failures(), vec![k], "{k}");
            let mut missing = good.clone();
            missing.as_object_mut().unwrap().remove(k);
            assert!(
                matches!(VerifyReport::from_json(&missing), Err(Error::Report(_))),
                "missing {k}"
            );
        }
        let mut two_off = good.clone();
        two_off["write"] = json!(false);
        two_off["other_bucket_denied"] = json!(false);
        assert_eq!(
            VerifyReport::from_json(&two_off).unwrap().failures(),
            vec!["write", "other_bucket_denied"]
        );
        let mut not_bool = good.clone();
        not_bool["write"] = json!("yes");
        assert!(matches!(
            VerifyReport::from_json(&not_bool),
            Err(Error::Report(_))
        ));
        let failed = json!({"ok": false, "why": "create_failed"});
        assert!(
            matches!(VerifyReport::from_json(&failed), Err(Error::Report(m)) if m.contains("create_failed"))
        );
        for bad in [
            "not-hex!",
            "",
            "709325321f3e207ca9c8152720134ae",
            &"a".repeat(64),
        ] {
            let mut bad_id = good.clone();
            bad_id["token_id"] = json!(bad);
            assert!(
                matches!(VerifyReport::from_json(&bad_id), Err(Error::Report(_))),
                "token_id {bad:?} must be exactly 32 hex (#564 r2 L2)"
            );
        }
    }

    #[test]
    fn r7_sanitize_masks_like_robot_and_keeps_token_id() {
        // #564 M1 (ebb36 probe): renamed keys and URL userinfo must not survive.
        let mut v = json!({
            "ok": true, "token_id": "709325321f3e207ca9c8152720134ae2",
            "api_token": "T1", "cf_api_token": "T2", "password": "P1", "secret_key": "S1",
            "SecretAccessKey": "S2", "secret_access_key": "S3", "Access_Key_Id": "A1",
            "value": "V1", "authorization": "Basic x",
            "endpoint": "https://k:S7@x.r2.cloudflarestorage.com",
            "nested": {"private_key": "K1", "keep": 1, "result": {"Value": "V2"}},
            "list": [{"Cookie": "C1", "fine": true}],
        });
        sanitize(&mut v);
        let text = v.to_string();
        for leak in [
            "T1", "T2", "P1", "S1", "S2", "S3", "A1", "V1", "V2", "Basic x", "S7", "K1", "C1",
        ] {
            assert!(!text.contains(leak), "{leak} survived sanitize: {text}");
        }
        assert_eq!(v["token_id"], "709325321f3e207ca9c8152720134ae2");
        assert_eq!(v["endpoint"], "https://***:***@x.r2.cloudflarestorage.com");
        assert_eq!(v["nested"]["keep"], 1);
        assert_eq!(v["list"][0]["fine"], true);
        // a token_id that does not look like an id is masked like any other token-ish key
        for not_id in [
            "not-an-id value!",
            &"a".repeat(64),
            "709325321f3e207ca9c8152720134ae",
        ] {
            let mut w = json!({"token_id": not_id});
            sanitize(&mut w);
            assert_eq!(
                w["token_id"], "***",
                "{not_id:?} is not a 32-hex id (#564 r2 L2)"
            );
        }
    }
}
