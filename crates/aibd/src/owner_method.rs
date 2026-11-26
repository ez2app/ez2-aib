//! Which window the pairing key is asked to sign with on a Mac with Touch ID: the
//! fingerprint only (the default), or the login password too, chosen by the owner
//! (ebab8 11-26 21:15, Andy 「要」; PLAN-owner-method-c2f0b-1126 r2 / r2b / r3, 3e53b
//! pre-read). This module is the pure part: the records, the signed message, and the rule.
//!
//! The real limit is the key's ACL (biometry-current-set or user-presence). The records only
//! decide which window opens, and a window that takes the password opens only when every one
//! of these holds:
//! 1. Touch ID works now (elsewhere the key's own window is used, as before);
//! 2. the last record of the key chain says `to: password`;
//! 3. its signature checks out with its `old_pub`;
//! 4. its `new_pub_sha256` is the current `key.pub`'s and is the pin;
//! 5. the pin was moved by this very record (`record_sha256`), not written at a pairing.
//!
//! The pin moves only by a record signed by the pinned key ([`may_repin`]), up or down. A pin
//! written at a pairing (`record_sha256: null`) never opens the password window, so an AI that
//! deletes both files, puts in a key of its own and waits for the next pairing gets the
//! fingerprint window (r3).
//!
//! Honest limit: an AI of the same account that writes the pin file too (with its own
//! `record_sha256`) can still move this Mac to the password window; then a person must still
//! type the login password in it. It is not an approval by the AI itself.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// `<data>/pair-key-pin.json`.
pub const PIN_FILE: &str = "pair-key-pin.json";
/// `<data>/pair-key-method.json`: the key chain's last record.
pub const RECORD_FILE: &str = "pair-key-method.json";
pub const PIN_SCHEMA: &str = "aib-pair-key-pin/v1";
pub const RECORD_SCHEMA: &str = "aib-pair-key-method/v1";
/// The event of each change.
pub const EVENT: &str = "owner.mode";

/// How the owner check is given.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    /// The fingerprint only.
    Biometry,
    /// The fingerprint or the login password.
    Password,
}

impl Method {
    pub fn word(self) -> &'static str {
        match self {
            Method::Biometry => "biometry",
            Method::Password => "password",
        }
    }
    pub fn parse(s: &str) -> Option<Method> {
        match s {
            "biometry" => Some(Method::Biometry),
            "password" => Some(Method::Password),
            _ => None,
        }
    }
}

pub fn sha256_hex(b: &[u8]) -> String {
    hex(&Sha256::digest(b))
}

pub fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

pub fn unhex(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok())
        .collect()
}

/// The pin: the key the owner paired with, or the one a signed record moved it to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pin {
    pub pub_sha256: String,
    /// `None` when written at a pairing; the record's sha256 when a record moved it.
    pub record_sha256: Option<String>,
    pub at: u64,
}

impl Pin {
    pub fn to_value(&self) -> Value {
        json!({"schema": PIN_SCHEMA, "pub_sha256": self.pub_sha256,
               "record_sha256": self.record_sha256, "at": self.at})
    }
    pub fn from_value(v: &Value) -> Option<Pin> {
        if v["schema"] != PIN_SCHEMA {
            return None;
        }
        let record_sha256 = match &v["record_sha256"] {
            Value::Null => None,
            Value::String(s) if is_sha(s) => Some(s.clone()),
            _ => return None,
        };
        let pub_sha256 = v["pub_sha256"].as_str().filter(|s| is_sha(s))?.to_owned();
        Some(Pin {
            pub_sha256,
            record_sha256,
            at: v["at"].as_u64()?,
        })
    }
}

fn is_sha(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// One link of the key chain: the key `old_pub` signed that the next key is `new_pub_sha256`,
/// made for `to`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub to: Method,
    pub old_pub: Vec<u8>,
    pub new_pub_sha256: String,
    pub at: u64,
    pub sig: Vec<u8>,
}

/// The bytes the old key signs: a version tag, then each part on its own line.
pub fn message(to: Method, old_pub: &[u8], new_pub_sha256: &str, at: u64) -> Vec<u8> {
    format!(
        "aibd/owner-method/v1\n{}\n{}\n{new_pub_sha256}\n{at}\n",
        to.word(),
        hex(old_pub)
    )
    .into_bytes()
}

impl Record {
    pub fn message(&self) -> Vec<u8> {
        message(self.to, &self.old_pub, &self.new_pub_sha256, self.at)
    }
    pub fn to_value(&self) -> Value {
        json!({"schema": RECORD_SCHEMA, "to": self.to.word(), "old_pub": hex(&self.old_pub),
               "new_pub_sha256": self.new_pub_sha256, "at": self.at, "sig": hex(&self.sig)})
    }
    pub fn from_value(v: &Value) -> Option<Record> {
        if v["schema"] != RECORD_SCHEMA {
            return None;
        }
        let new_pub_sha256 = v["new_pub_sha256"].as_str().filter(|s| is_sha(s))?;
        let sig = unhex(v["sig"].as_str()?)?;
        let old_pub = unhex(v["old_pub"].as_str()?)?;
        if sig.is_empty() || old_pub.is_empty() {
            return None;
        }
        Some(Record {
            to: Method::parse(v["to"].as_str()?)?,
            old_pub,
            new_pub_sha256: new_pub_sha256.to_owned(),
            at: v["at"].as_u64()?,
            sig,
        })
    }
    /// The sha256 of the record as written: what a pin it moved names.
    pub fn sha(&self) -> String {
        sha256_hex(self.to_value().to_string().as_bytes())
    }
}

/// Whether `r` may move `pin` to the current key `current_pub`: the pin is `r`'s old key,
/// `r` is signed by it, and `r` names the current key (r2b 1, 2). `verify(pub, msg, sig)`.
pub fn may_repin(
    pin: &Pin,
    r: &Record,
    current_pub: &[u8],
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> bool {
    let pinned_old = pin.pub_sha256 == sha256_hex(&r.old_pub);
    let names_current = r.new_pub_sha256 == sha256_hex(current_pub);
    pinned_old && names_current && verify(&r.old_pub, &r.message(), &r.sig)
}

/// What this aibd reads of the files at start, or after a change.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reading {
    /// The password window may open (all five hold).
    Password,
    /// The fingerprint window, everything consistent.
    Biometry,
    /// The fingerprint window, and selfcheck says why (`owner_key_not_as_paired`, ...).
    BiometryBecause(&'static str),
}

impl Reading {
    pub fn password_window(&self) -> bool {
        *self == Reading::Password
    }
    /// The short word a page / selfcheck shows.
    pub fn word(&self) -> &'static str {
        match self {
            Reading::Password => "password",
            Reading::Biometry => "biometry",
            Reading::BiometryBecause(_) => "unknown",
        }
    }
}

/// The rule (r3 §12): see the module doc. `pin` / `record` are what the files held (None: no
/// file or not readable), `current_pub` the key aibd checked at start.
pub fn read(
    pin: Option<&Pin>,
    record: Option<&Record>,
    current_pub: &[u8],
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> Reading {
    let Some(pin) = pin else {
        return match record {
            None => Reading::Biometry,
            Some(_) => Reading::BiometryBecause("owner_method_no_pin"),
        };
    };
    let current = sha256_hex(current_pub);
    if pin.pub_sha256 != current {
        return Reading::BiometryBecause("owner_key_not_as_paired");
    }
    let Some(r) = record else {
        return Reading::Biometry;
    };
    let signed = verify(&r.old_pub, &r.message(), &r.sig);
    if !signed || r.new_pub_sha256 != current {
        return Reading::BiometryBecause("owner_method_record_not_as_paired");
    }
    let moved_by_it = pin.record_sha256.as_deref() == Some(r.sha().as_str());
    match (r.to, moved_by_it) {
        (Method::Password, true) => Reading::Password,
        (Method::Password, false) => Reading::BiometryBecause("owner_method_pin_not_moved"),
        (Method::Biometry, _) => Reading::Biometry,
    }
}

/// After a crash between the swap and the pin (r2b 2): the pin is still the record's old key,
/// the record is signed by it and names the current key ⇒ the pin is moved now, by this
/// record. Anything else: nothing to do.
pub fn repin_at_start(
    pin: &Pin,
    record: &Record,
    current_pub: &[u8],
    at: u64,
    verify: impl Fn(&[u8], &[u8], &[u8]) -> bool,
) -> Option<Pin> {
    may_repin(pin, record, current_pub, verify).then(|| Pin {
        pub_sha256: sha256_hex(current_pub),
        record_sha256: Some(record.sha()),
        at,
    })
}

/// The pin written when the owner pairs and no pin is there yet.
pub fn pin_at_pairing(current_pub: &[u8], at: u64) -> Pin {
    Pin {
        pub_sha256: sha256_hex(current_pub),
        record_sha256: None,
        at,
    }
}

/// `owner_method_set` body: `{type, to}` only.
pub fn parse_set(body: &Value) -> Option<Method> {
    let o = body.as_object()?;
    if o.keys().any(|k| !matches!(k.as_str(), "type" | "to")) {
        return None;
    }
    Method::parse(o.get("to")?.as_str()?)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    const OLD: &[u8] = b"old-key-public";
    const NEW: &[u8] = b"new-key-public";

    /// A stand-in signature: the key's bytes then the message, so it checks only for that key.
    fn sign(public: &[u8], msg: &[u8]) -> Vec<u8> {
        [public, msg].concat()
    }
    fn verify(public: &[u8], msg: &[u8], sig: &[u8]) -> bool {
        sig == sign(public, msg).as_slice()
    }

    fn record(to: Method, old: &[u8], new: &[u8]) -> Record {
        let new_sha = sha256_hex(new);
        let sig = sign(old, &message(to, old, &new_sha, 7));
        Record {
            to,
            old_pub: old.to_vec(),
            new_pub_sha256: new_sha,
            at: 7,
            sig,
        }
    }

    fn moved(r: &Record, new: &[u8]) -> Pin {
        Pin {
            pub_sha256: sha256_hex(new),
            record_sha256: Some(r.sha()),
            at: 8,
        }
    }

    #[test]
    fn the_password_window_opens_only_for_a_pin_moved_by_the_signed_record() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(
            read(Some(&moved(&r, NEW)), Some(&r), NEW, verify),
            Reading::Password
        );
        // no record, or no files at all: the fingerprint
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(read(Some(&paired), None, NEW, verify), Reading::Biometry);
        assert_eq!(read(None, None, NEW, verify), Reading::Biometry);
        // back to the fingerprint: a record too, and the fingerprint
        let back = record(Method::Biometry, NEW, OLD);
        assert_eq!(
            read(Some(&moved(&back, OLD)), Some(&back), OLD, verify),
            Reading::Biometry
        );
    }

    #[test]
    fn a_pin_written_at_a_pairing_never_opens_the_password_window() {
        // r3: both files gone, a key of the AI's own, the next pairing pins it, then a record
        // signed by a key of the AI's own (old_pub is only a public key)
        let r = record(Method::Password, b"any-software-key", NEW);
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(
            read(Some(&paired), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_pin_not_moved")
        );
    }

    #[test]
    fn a_record_not_signed_by_its_old_key_is_the_fingerprint() {
        let mut r = record(Method::Password, OLD, NEW);
        let pin = moved(&r, NEW);
        r.sig = sign(b"another", &r.message());
        assert_eq!(
            read(Some(&pin), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_record_not_as_paired")
        );
        assert!(!may_repin(&pin_at_pairing(OLD, 1), &r, NEW, verify));
    }

    #[test]
    fn a_key_other_than_the_pinned_one_is_the_fingerprint_and_said() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(
            read(Some(&moved(&r, NEW)), Some(&r), b"swapped", verify),
            Reading::BiometryBecause("owner_key_not_as_paired")
        );
        assert_eq!(
            read(None, Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_method_no_pin")
        );
        assert_eq!(read(None, Some(&r), NEW, verify).word(), "unknown");
    }

    #[test]
    fn the_pin_moves_only_from_the_records_old_key_to_the_current_one() {
        let r = record(Method::Password, OLD, NEW);
        let pin = pin_at_pairing(OLD, 1);
        assert!(may_repin(&pin, &r, NEW, verify));
        // the pin is not the record's old key
        assert!(!may_repin(&pin_at_pairing(b"other", 1), &r, NEW, verify));
        // the record names another key than the current one
        assert!(!may_repin(&pin, &r, b"other", verify));
    }

    #[test]
    fn a_crash_between_the_swap_and_the_pin_is_mended_at_start_only_when_consistent() {
        let r = record(Method::Password, OLD, NEW);
        let got = repin_at_start(&pin_at_pairing(OLD, 1), &r, NEW, 9, verify).unwrap();
        assert_eq!(got, moved(&r, NEW).clone_at(9));
        assert_eq!(read(Some(&got), Some(&r), NEW, verify), Reading::Password);
        // not consistent: nothing mended, and the reading says so
        assert_eq!(
            repin_at_start(&pin_at_pairing(b"x", 1), &r, NEW, 9, verify),
            None
        );
        assert_eq!(
            read(Some(&pin_at_pairing(OLD, 1)), Some(&r), NEW, verify),
            Reading::BiometryBecause("owner_key_not_as_paired")
        );
    }

    #[test]
    fn the_records_go_through_their_files_and_nothing_else_is_read() {
        let r = record(Method::Password, OLD, NEW);
        assert_eq!(Record::from_value(&r.to_value()), Some(r.clone()));
        let p = moved(&r, NEW);
        assert_eq!(Pin::from_value(&p.to_value()), Some(p.clone()));
        let paired = pin_at_pairing(NEW, 1);
        assert_eq!(Pin::from_value(&paired.to_value()), Some(paired));
        let mut v = r.to_value();
        v["to"] = json!("anything");
        assert_eq!(Record::from_value(&v), None);
        let mut v = p.to_value();
        v["record_sha256"] = json!("ABC");
        assert_eq!(Pin::from_value(&v), None);
    }

    #[test]
    fn a_set_body_names_to_and_nothing_else() {
        assert_eq!(
            parse_set(&json!({"type": "owner_method_set", "to": "password"})),
            Some(Method::Password)
        );
        assert_eq!(
            parse_set(&json!({"type": "owner_method_set", "to": "biometry"})),
            Some(Method::Biometry)
        );
        for bad in [
            json!({"type": "owner_method_set"}),
            json!({"type": "owner_method_set", "to": "password", "acl": "user_presence"}),
            json!({"type": "owner_method_set", "to": "pin"}),
        ] {
            assert_eq!(parse_set(&bad), None, "{bad}");
        }
    }

    impl Pin {
        fn clone_at(&self, at: u64) -> Pin {
            Pin { at, ..self.clone() }
        }
    }
}
