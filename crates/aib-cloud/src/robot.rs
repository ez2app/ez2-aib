// SPDX-License-Identifier: Apache-2.0
//! Hetzner Robot webservice: validated input → [`Plan`] (what will be sent) →
//! [`ApprovalFacts`] (what the human sees). A `Plan` is immutable and can only
//! be built through its constructors, so the popup can never describe a
//! different target than the request (#532 M1).
//!
//! Rules carried over from the Python PoC and its reviews (#528 r1–r4, #532):
//! - the API host is a constant, never read from the environment;
//! - `server` is 1–10 ASCII digits with no leading zero, `--fingerprint` is 16
//!   colon-separated hex pairs (de-duplicated and sorted), `--type` is a closed
//!   list; full-width or Arabic-Indic digits are refused explicitly, not by length;
//! - `rescue` without a fingerprint is refused (Hetzner would answer with a
//!   one-time root password that the mask hides from everyone);
//! - rescue and reset have **no rollback**; the recovery text is part of the plan;
//! - [`mask`] hides every secret-looking key and any `user:pw@` URL userinfo
//!   before a reply or error can be printed.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;

/// The only host this crate will ever address. Not configurable on purpose.
pub const API_HOST: &str = "https://robot-ws.your-server.de";

/// Recovery text attached to every mutating plan (no rollback exists).
pub const NO_ROLLBACK: &str = "不支援回滾:Rescue 只排下一次開機(60 分鐘內要重開、只供一次);reset 一送就送了。恢復路徑 = 再排一次 / 再重開。";

const SERVER_MAX_DIGITS: usize = 10;
const FINGERPRINT_GROUPS: usize = 16;

/// Why an input or a plan was refused. Variants are stable strings for the CLI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Not 1–10 ASCII digits without a leading zero.
    BadServer(String),
    /// Not 16 colon-separated lowercase hex pairs.
    BadFingerprint(String),
    /// Not on [`ResetType::ALL`].
    BadResetType(String),
    /// `rescue` without a fingerprint.
    NoFingerprint,
}

impl Error {
    /// Short machine-readable code (what the Python PoC printed as `why`).
    pub fn code(&self) -> &'static str {
        match self {
            Error::BadServer(_) => "bad_server",
            Error::BadFingerprint(_) => "bad_fingerprint",
            Error::BadResetType(_) => "bad_reset_type",
            Error::NoFingerprint => "no_fingerprint",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::BadServer(s) => write!(
                f,
                "server 只收 1–10 位 ASCII 數字、不能以 0 開頭(Hetzner server number),不是「{s}」"
            ),
            Error::BadFingerprint(s) => {
                write!(f, "fingerprint 要是 aa:bb:…(16 組十六進位),不是「{s}」")
            }
            Error::BadResetType(s) => {
                write!(f, "type 只收 {},不是「{s}」", ResetType::ALL.join("/"))
            }
            Error::NoFingerprint => write!(
                f,
                "沒帶 fingerprint 會拿到一次性 root 密碼(被遮罩,沒人拿得到),Rescue 白排;拒絕"
            ),
        }
    }
}

impl core::error::Error for Error {}

/// A Hetzner server number: 1–10 ASCII digits, no leading zero. Checked
/// character by character (`is_ascii_digit`), so `３１０` or `٣١` are refused
/// even when they fit the byte length (#532 M3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServerNumber(String);

impl ServerNumber {
    pub fn parse(raw: &str) -> Result<Self, Error> {
        let digits_only = !raw.is_empty() && raw.chars().all(|c| c.is_ascii_digit());
        let ok = digits_only && raw.len() <= SERVER_MAX_DIGITS && !raw.starts_with('0');
        if ok {
            Ok(Self(raw.to_owned()))
        } else {
            Err(Error::BadServer(raw.to_owned()))
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// An SSH key fingerprint as Robot lists it: `aa:bb:…` (16 hex pairs), stored lowercase.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Fingerprint(String);

impl Fingerprint {
    pub fn parse(raw: &str) -> Result<Self, Error> {
        let lower = raw.to_ascii_lowercase();
        let groups: Vec<&str> = lower.split(':').collect();
        let ok = groups.len() == FINGERPRINT_GROUPS
            && groups
                .iter()
                .all(|g| g.len() == 2 && g.chars().all(|c| c.is_ascii_hexdigit()));
        if ok {
            Ok(Self(lower))
        } else {
            Err(Error::BadFingerprint(raw.to_owned()))
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Reset types Robot accepts. Keep in sync with `GET /reset/{server}` (A1 of the runbook);
/// The first host's model offers `power / power_long / hw / man` and no `sw`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResetType {
    Sw,
    Hw,
    Power,
    PowerLong,
    Man,
}

impl ResetType {
    pub const ALL: [&'static str; 5] = ["sw", "hw", "power", "power_long", "man"];

    pub fn parse(raw: &str) -> Result<Self, Error> {
        match raw {
            "sw" => Ok(Self::Sw),
            "hw" => Ok(Self::Hw),
            "power" => Ok(Self::Power),
            "power_long" => Ok(Self::PowerLong),
            "man" => Ok(Self::Man),
            other => Err(Error::BadResetType(other.to_owned())),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Sw => "sw",
            Self::Hw => "hw",
            Self::Power => "power",
            Self::PowerLong => "power_long",
            Self::Man => "man",
        }
    }
}

/// What a plan does. The string form is what the approval popup shows as `action`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Rescue,
    Reset(ResetType),
}

impl Action {
    pub fn label(&self) -> String {
        match self {
            Action::Rescue => "rescue".to_owned(),
            Action::Reset(t) => format!("reset:{}", t.as_str()),
        }
    }
}

/// Whether a plan changes the machine (needs the human gate) or only reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mutation {
    ReadOnly,
    Mutating,
}

/// One request the operator is about to send. All fields are private: a plan
/// exists only through [`Plan::rescue`] / [`Plan::reset`], never through a
/// struct literal, and cannot be edited afterwards, so `path`, `form` and
/// [`ApprovalFacts`] always describe the same validated values (#532 M1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    action: Action,
    server: ServerNumber,
    fingerprints: Vec<Fingerprint>,
    method: &'static str,
    path: String,
    form: Vec<(String, String)>,
    mutation: Mutation,
    recovery: &'static str,
}

/// The three read-only requests `status` issues.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatusPlan {
    server: ServerNumber,
    paths: [String; 3],
}

impl StatusPlan {
    pub fn server(&self) -> &ServerNumber {
        &self.server
    }

    pub fn paths(&self) -> &[String; 3] {
        &self.paths
    }
}

/// What the human sees in the approval popup. Derived from the plan, never
/// from raw input, and never truncated here (the gate decides display limits).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApprovalFacts {
    pub action: String,
    pub server: String,
    /// Comma-joined, sorted, de-duplicated fingerprints, or `-` when the plan carries none.
    pub fp: String,
    /// Digest of the canonical form, for display and "same plan?" comparison only
    /// (see [`Plan::plan_hash`]).
    pub plan_hash: String,
}

impl Plan {
    /// `POST /boot/{server}/rescue` with `os=linux` and one `authorized_key[]` per
    /// fingerprint (sorted, duplicates dropped). Refuses an empty list.
    pub fn rescue(server: ServerNumber, fingerprints: Vec<Fingerprint>) -> Result<Self, Error> {
        let mut fps = fingerprints;
        fps.sort();
        fps.dedup();
        if fps.is_empty() {
            return Err(Error::NoFingerprint);
        }
        let mut form = vec![("os".to_owned(), "linux".to_owned())];
        form.extend(
            fps.iter()
                .map(|f| ("authorized_key[]".to_owned(), f.as_str().to_owned())),
        );
        Ok(Self {
            path: format!("/boot/{}/rescue", server.as_str()),
            action: Action::Rescue,
            server,
            fingerprints: fps,
            method: "POST",
            form,
            mutation: Mutation::Mutating,
            recovery: NO_ROLLBACK,
        })
    }

    /// `POST /reset/{server}` with `type=<t>`.
    pub fn reset(server: ServerNumber, kind: ResetType) -> Self {
        Self {
            path: format!("/reset/{}", server.as_str()),
            action: Action::Reset(kind),
            server,
            fingerprints: Vec::new(),
            method: "POST",
            form: vec![("type".to_owned(), kind.as_str().to_owned())],
            mutation: Mutation::Mutating,
            recovery: NO_ROLLBACK,
        }
    }

    /// The read-only `status` bundle: `/server`, `/boot/{n}/rescue`, `/reset/{n}`.
    pub fn status(server: ServerNumber) -> StatusPlan {
        let n = server.as_str();
        StatusPlan {
            paths: [
                format!("/server/{n}"),
                format!("/boot/{n}/rescue"),
                format!("/reset/{n}"),
            ],
            server,
        }
    }

    pub fn action(&self) -> &Action {
        &self.action
    }

    pub fn server(&self) -> &ServerNumber {
        &self.server
    }

    pub fn fingerprints(&self) -> &[Fingerprint] {
        &self.fingerprints
    }

    pub fn method(&self) -> &'static str {
        self.method
    }

    /// Path under [`API_HOST`]; the server number is inserted verbatim because
    /// [`ServerNumber`] only admits ASCII digits (nothing to percent-encode).
    pub fn path(&self) -> &str {
        &self.path
    }

    /// `application/x-www-form-urlencoded` pairs, in send order.
    pub fn form(&self) -> &[(String, String)] {
        &self.form
    }

    pub fn mutation(&self) -> Mutation {
        self.mutation
    }

    pub fn recovery(&self) -> &'static str {
        self.recovery
    }

    /// Full URL (host is the constant [`API_HOST`]).
    pub fn url(&self) -> String {
        format!("{API_HOST}{}", self.path)
    }

    /// Body as `application/x-www-form-urlencoded`.
    pub fn form_body(&self) -> String {
        self.form
            .iter()
            .map(|(k, v)| format!("{}={}", urlencode(k), urlencode(v)))
            .collect::<Vec<_>>()
            .join("&")
    }

    /// What the popup shows. Built from the same fields the request uses.
    pub fn approval_facts(&self) -> ApprovalFacts {
        let fp = if self.fingerprints.is_empty() {
            "-".to_owned()
        } else {
            self.fingerprints
                .iter()
                .map(Fingerprint::as_str)
                .collect::<Vec<_>>()
                .join(",")
        };
        ApprovalFacts {
            action: self.action.label(),
            server: self.server.as_str().to_owned(),
            fp,
            plan_hash: self.plan_hash(),
        }
    }

    /// Canonical form v1 of the request, the only input to [`Plan::plan_hash`]:
    /// `aib-cloud/plan/v1\n<origin>\n<method>\n<path>\n<form body>\n<recovery>`.
    /// Field order and separators are fixed; changing them is a new version.
    /// Secrets never appear here (the Robot plan carries none; a future plan
    /// with a secret field must bind a non-revealing commitment instead).
    pub fn canonical(&self) -> String {
        format!(
            "aib-cloud/plan/v1\n{API_HOST}\n{}\n{}\n{}\n{}",
            self.method,
            self.path,
            self.form_body(),
            self.recovery
        )
    }

    /// 64-bit FNV-1a over [`Plan::canonical`]. **Display and "same plan?"
    /// comparison only — not an approval credential.** It is not a MAC and not
    /// collision resistant; when M-R2 / M-R3 bind approvals to a digest, the
    /// ADR decides the hash dependency (SHA-256 or better) and this changes.
    /// Expiry and nonce belong to the approval contract (aibd), not to the plan.
    ///
    /// M-R3 prerequisite (bb7dc): a plan whose apply depends on a secret must
    /// bind that secret through a non-revealing keyed commitment or a protected
    /// immutable secret version, otherwise swapping the secret keeps an old
    /// approval valid. Robot plans carry no secret, so nothing is bound here.
    pub fn plan_hash(&self) -> String {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for b in self.canonical().bytes() {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
        format!("{h:016x}")
    }
}

/// Percent-encode everything except unreserved characters (RFC 3986), the
/// same conservative set `urllib.parse.quote(v, safe="")` uses.
pub fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Key fragments that mark a value as secret (case-insensitive substring match).
const MASK_KEYS: [&str; 16] = [
    "password",
    "passwd",
    "pass",
    "pwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "privatekey",
    "access_key", // S3-style AccessKeyId / access_key_id (#564 M1)
    "cookie",
    "session",
];

/// Replace the value of every secret-looking key with `***` and blank out any
/// `scheme://user:pw@host` userinfo inside string values, recursively. This is
/// the single entry point before a reply, a request dump or an error is printed.
pub fn mask(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Object(map) => {
            for (k, v) in map.iter_mut() {
                let lk = k.to_ascii_lowercase();
                if MASK_KEYS.iter().any(|m| lk.contains(m)) {
                    *v = serde_json::Value::String("***".to_owned());
                } else {
                    mask(v);
                }
            }
        }
        serde_json::Value::Array(items) => items.iter_mut().for_each(mask),
        serde_json::Value::String(s) => {
            if let Some(masked) = mask_userinfo(s) {
                *s = masked;
            }
        }
        _ => {}
    }
}

/// `https://user:pw@host/x` → `https://***:***@host/x`, for **every** URL in the
/// string (#532 r2 L1: a message quoting two URLs used to leak the second one).
/// Returns `None` when no URL carries userinfo.
pub fn mask_userinfo(s: &str) -> Option<String> {
    // The authority ends at the first path / query / fragment character, whitespace or
    // common punctuation (#532 r3 L1: a host-only URL followed by a newline, tab, comma or
    // '>' must not swallow the next URL's scheme).
    const AUTHORITY_END: &[char] = &[
        '/', '?', '#', ',', ';', '<', '>', '"', '\'', '(', ')', '[', ']', '{', '}', '`', '|', '\\',
    ];
    fn ends_authority(c: char) -> bool {
        c.is_whitespace() || AUTHORITY_END.contains(&c)
    }
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    let mut changed = false;
    while let Some(scheme_end) = rest.find("://") {
        let (head, after) = rest.split_at(scheme_end + 3);
        out.push_str(head);
        let host_end = after.find(ends_authority).unwrap_or(after.len());
        let authority = &after[..host_end];
        if let Some(at) = authority.rfind('@') {
            out.push_str("***:***@");
            out.push_str(&authority[at + 1..]);
            changed = true;
        } else {
            out.push_str(authority);
        }
        rest = &after[host_end..];
    }
    if changed {
        out.push_str(rest);
        Some(out)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::string::ToString;
    use serde_json::json;

    const FP: &str = "aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:bb";
    const FP2: &str = "aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:cc";
    const PW: &str = "S3cr3tPW";

    fn srv() -> ServerNumber {
        ServerNumber::parse("1234567").expect("valid")
    }

    fn fp() -> Fingerprint {
        Fingerprint::parse(FP).expect("valid")
    }

    fn fp2() -> Fingerprint {
        Fingerprint::parse(FP2).expect("valid")
    }

    // R10 / M1: the host is a constant and is https.
    #[test]
    fn api_host_is_fixed_https() {
        assert_eq!(API_HOST, "https://robot-ws.your-server.de");
        assert!(Plan::reset(srv(), ResetType::Sw)
            .url()
            .starts_with("https://robot-ws.your-server.de/reset/"));
    }

    // R9 / H2 / K5 / K8 / #532 M3: only ASCII digits, checked per character —
    // short full-width and Arabic-Indic strings fit the byte length and must still be refused.
    #[test]
    fn server_number_rejects_everything_but_ascii_digits() {
        for bad in [
            "1234567/../../../reset/9999999?",
            "1234567xxxxxxxxxxxxxxxxx",
            "12345678901",
            "abc",
            "",
            "1234567 ",
            "３１０９４２１", // full-width, over the byte length
            "３１０",         // full-width, 9 bytes: only is_ascii_digit catches it
            "٣١",             // Arabic-Indic, 4 bytes
            "①",              // numeric but not a digit
            "-1",
            "01", // leading zero (L2)
            "0",
        ] {
            let err = ServerNumber::parse(bad).expect_err(bad);
            assert_eq!(err.code(), "bad_server", "{bad}");
        }
        assert_eq!(
            ServerNumber::parse("3109421").expect("ok").as_str(),
            "3109421"
        );
        assert_eq!(ServerNumber::parse("1").expect("ok").as_str(), "1");
        assert_eq!(
            ServerNumber::parse("1234567890").expect("ok").as_str(),
            "1234567890"
        );
    }

    // R9: fingerprint shape; upper-case is accepted and normalised.
    #[test]
    fn fingerprint_shape_and_case() {
        let too_many = format!("{}bb", "aa:".repeat(16));
        let not_hex = format!("{}zz", "zz:".repeat(15));
        for bad in [
            "aa:bb",
            "SHA256:abc",
            not_hex.as_str(),
            too_many.as_str(),
            "",
        ] {
            assert_eq!(
                Fingerprint::parse(bad).expect_err(bad).code(),
                "bad_fingerprint",
                "{bad}"
            );
        }
        let upper = FP.to_ascii_uppercase();
        assert_eq!(Fingerprint::parse(&upper).expect("ok").as_str(), FP);
    }

    // R9 / L2: reset type is a closed list.
    #[test]
    fn reset_type_whitelist() {
        for bad in ["hw;rm", "reboot", "power_longer", "", "HW"] {
            assert_eq!(
                ResetType::parse(bad).expect_err(bad).code(),
                "bad_reset_type",
                "{bad}"
            );
        }
        for ok in ResetType::ALL {
            assert_eq!(ResetType::parse(ok).expect("ok").as_str(), ok);
        }
    }

    // R7 / L1 / K4: rescue without a fingerprint is refused before any plan exists.
    #[test]
    fn rescue_needs_fingerprint() {
        assert_eq!(
            Plan::rescue(srv(), vec![]).expect_err("refused"),
            Error::NoFingerprint
        );
    }

    // R2: the rescue form is exactly os=linux + one authorized_key[] per fingerprint.
    #[test]
    fn rescue_plan_shape() {
        let p = Plan::rescue(srv(), vec![fp()]).expect("plan");
        assert_eq!(p.method(), "POST");
        assert_eq!(p.path(), "/boot/1234567/rescue");
        assert_eq!(
            p.form_body(),
            format!("os=linux&authorized_key%5B%5D={}", FP.replace(':', "%3A"))
        );
        assert_eq!(p.mutation(), Mutation::Mutating);
        assert_eq!(p.recovery(), NO_ROLLBACK);
    }

    // #532 L2: fingerprints are de-duplicated and sorted, so the same set always gives the same plan.
    #[test]
    fn rescue_fingerprints_sorted_and_unique() {
        let a = Plan::rescue(srv(), vec![fp2(), fp(), fp()]).expect("plan");
        let b = Plan::rescue(srv(), vec![fp(), fp2()]).expect("plan");
        assert_eq!(a, b);
        assert_eq!(a.fingerprints(), &[fp(), fp2()]);
        assert_eq!(a.approval_facts().fp, format!("{FP},{FP2}"));
        assert_eq!(
            a.form()
                .iter()
                .filter(|(k, _)| k == "authorized_key[]")
                .count(),
            2
        );
    }

    // R5b: reset sends type=<t>.
    #[test]
    fn reset_plan_shape() {
        let p = Plan::reset(srv(), ResetType::Sw);
        assert_eq!(
            (p.method(), p.path(), p.form_body().as_str()),
            ("POST", "/reset/1234567", "type=sw")
        );
        assert_eq!(p.action().label(), "reset:sw");
    }

    // R4: status is three GETs, no mutation, no gate.
    #[test]
    fn status_paths() {
        let s = Plan::status(srv());
        assert_eq!(
            s.paths(),
            &[
                "/server/1234567".to_owned(),
                "/boot/1234567/rescue".to_owned(),
                "/reset/1234567".to_owned()
            ]
        );
        assert_eq!(s.server().as_str(), "1234567");
    }

    // R10 / K6 / #532 M1: popup facts equal what the request uses — same server,
    // same fingerprints, not truncated — and there is no way to change one without the other.
    #[test]
    fn approval_facts_equal_request() {
        let long = ServerNumber::parse("1234567890").expect("ok");
        let p = Plan::rescue(long.clone(), vec![fp(), fp2()]).expect("plan");
        let f = p.approval_facts();
        assert_eq!(f.server, "1234567890");
        assert!(p.path().contains(&f.server));
        assert_eq!(f.fp, format!("{FP},{FP2}"));
        assert!(p.form_body().contains(&FP.replace(':', "%3A")));
        assert_eq!(f.action, "rescue");
        assert_eq!(f.plan_hash, p.plan_hash());
        assert_eq!(Plan::reset(long, ResetType::Hw).approval_facts().fp, "-");
    }

    // The canonical form is versioned and its fields are in a fixed order; a golden
    // value guards against silently changing the hash input.
    #[test]
    fn canonical_is_versioned_and_golden() {
        let p = Plan::reset(srv(), ResetType::Sw);
        assert_eq!(
            p.canonical(),
            format!("aib-cloud/plan/v1\n{API_HOST}\nPOST\n/reset/1234567\ntype=sw\n{NO_ROLLBACK}")
        );
        assert_eq!(p.plan_hash(), "2c66af24ad356f39");
    }

    // plan_hash binds the approval to one request shape: any change to path/body changes it.
    #[test]
    fn plan_hash_changes_with_request() {
        let a = Plan::reset(srv(), ResetType::Sw);
        let b = Plan::reset(srv(), ResetType::Hw);
        let c = Plan::reset(ServerNumber::parse("7654321").expect("ok"), ResetType::Sw);
        assert_eq!(a.plan_hash(), Plan::reset(srv(), ResetType::Sw).plan_hash());
        assert_ne!(a.plan_hash(), b.plan_hash());
        assert_ne!(a.plan_hash(), c.plan_hash());
        assert_eq!(a.plan_hash().len(), 16);
    }

    // R5 / K1 / #532 M2: every secret-looking key is masked at any depth; URL userinfo is
    // blanked inside string values; other values untouched.
    #[test]
    fn mask_hides_secrets_recursively() {
        let mut v = json!({
            "rescue": {"active": true, "password": "ROOTPW123", "os": "linux",
                       "nested": [{"Passwd": PW}, {"x": 1}, {"pwd": PW}]},
            "error": {"status": 401, "pass": "x"},
            "api_key": PW, "apiKey": PW, "access_token": PW, "client_secret": PW,
            "Authorization": format!("Basic {PW}"),
            "url": format!("https://ws:{PW}@robot-ws.your-server.de/server/1?x=1"),
            "urls": [format!("http://u:{PW}@h/"), "https://h/no-userinfo"],
            "plain": "keep me",
        });
        mask(&mut v);
        let text = v.to_string();
        assert!(!text.contains("ROOTPW123") && !text.contains(PW), "{text}");
        assert_eq!(v["rescue"]["password"], "***");
        assert_eq!(v["rescue"]["nested"][0]["Passwd"], "***");
        assert_eq!(v["rescue"]["nested"][2]["pwd"], "***");
        assert_eq!(v["error"]["pass"], "***");
        for k in [
            "api_key",
            "apiKey",
            "access_token",
            "client_secret",
            "Authorization",
        ] {
            assert_eq!(v[k], "***", "{k}");
        }
        assert_eq!(
            v["url"],
            "https://***:***@robot-ws.your-server.de/server/1?x=1"
        );
        assert_eq!(v["urls"][0], "http://***:***@h/");
        assert_eq!(v["urls"][1], "https://h/no-userinfo");
        assert_eq!(v["rescue"]["active"], true);
        assert_eq!(v["rescue"]["nested"][1]["x"], 1);
        assert_eq!(v["plain"], "keep me");
    }

    #[test]
    fn mask_userinfo_edge_cases() {
        assert_eq!(mask_userinfo("no scheme here"), None);
        assert_eq!(mask_userinfo("https://host/path?q=a@b"), None); // '@' only in query
        assert_eq!(
            mask_userinfo("https://u@host"),
            Some("https://***:***@host".to_owned())
        );
        assert_eq!(
            mask_userinfo("ftp://u:p@h:21/x#y"),
            Some("ftp://***:***@h:21/x#y".to_owned())
        );
        // #532 r2 L1: every URL in the string, not only the first one.
        assert_eq!(
            mask_userinfo("see https://a.example/x then https://u:hunter2@b.example/y done"),
            Some("see https://a.example/x then https://***:***@b.example/y done".to_owned())
        );
        assert_eq!(
            mask_userinfo("https://u:p1@a/ https://v:p2@b/"),
            Some("https://***:***@a/ https://***:***@b/".to_owned())
        );
        assert_eq!(mask_userinfo("https://a/ and https://b/"), None);
        // #532 r3 L1 (ebb36 probe): host-only URL followed by newline / tab / comma / '>'.
        for sep in ["\n", "\t", ",", ">"] {
            let input = format!("https://a.example{sep}https://u:hunter2@b.example/y");
            let got = mask_userinfo(&input).unwrap_or_default();
            assert!(!got.contains("hunter2"), "{input:?} leaked: {got:?}");
            assert_eq!(
                got,
                format!("https://a.example{sep}https://***:***@b.example/y")
            );
        }
        assert_eq!(
            mask_userinfo("<https://a.example>https://u:hunter2@b.example/y"),
            Some("<https://a.example>https://***:***@b.example/y".to_owned())
        );
    }

    // #532 r2 L1: key names the first round missed.
    #[test]
    fn mask_hides_credential_private_key_cookie_auth() {
        let mut v = json!({
            "credential": PW, "credentials": [PW], "private_key": PW, "privateKey": PW,
            "Cookie": PW, "set-cookie": PW, "auth": PW, "session_id": PW, "author": "keep me",
            "Access_Key_Id": PW, "aws_access_key_id": PW,
            "note": format!("see https://a.example/x then https://u:{PW}@b.example/y"),
        });
        mask(&mut v);
        let text = v.to_string();
        assert!(!text.contains(PW), "{text}");
        for k in [
            "credential",
            "private_key",
            "privateKey",
            "Cookie",
            "set-cookie",
            "auth",
            "session_id",
            "Access_Key_Id",
            "aws_access_key_id",
        ] {
            assert_eq!(v[k], "***", "{k}");
        }
        assert_eq!(v["credentials"], "***");
        assert_eq!(v["author"], "***"); // "auth" substring: over-masking accepted (ebb36 r2 L1)
        assert_eq!(
            v["note"],
            "see https://a.example/x then https://***:***@b.example/y"
        );
    }

    #[test]
    fn urlencode_is_conservative() {
        assert_eq!(urlencode("aa:bb"), "aa%3Abb");
        assert_eq!(urlencode("authorized_key[]"), "authorized_key%5B%5D");
        assert_eq!(urlencode("a-b_c.d~e"), "a-b_c.d~e");
        assert_eq!(urlencode("é"), "%C3%A9");
    }
}
