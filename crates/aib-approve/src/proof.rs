//! The proof that a person approved one action, and how it is checked.
//!
//! ADR 0003 (spec 9.2) binds a Risk 3 approval to six things: actor, operation,
//! resource, parameter digest, nonce and expiry. [`Binding`] holds those plus a
//! version tag and the category (the purpose), and [`Binding::canonical`] is the
//! exact byte string a hardware key signs.
//!
//! Two levels (PLAN-b2 §3, Codex point 2):
//! - [`ProofLevel::PresenceOnly`] ([`Presence`]): a fresh owner check (fingerprint
//!   or login password) seen by the flow that is about to act. A yes/no inside that
//!   process: only this crate can make one, it cannot be cloned, and
//!   [`Proof::from_json`] refuses the level, so it never comes from a file, a pipe
//!   or another process.
//! - [`ProofLevel::HardwareSigned`] ([`Proof`]): a signature by a hardware-held key (Secure
//!   Enclave, TPM) over [`Binding::canonical`]. Checked against a trusted key list
//!   the caller supplies; the list must not come from a file the same-user AI can
//!   write (1.x `aib quote` keeps it compiled in and empty, so it fails closed).
//!
//! The nonce is consumed only after every other check passed, so a refused proof
//! does not burn it and a replayed one is refused.

use sha2::{Digest, Sha256};

use crate::category::Category;
use crate::text;

/// Version tag at the head of every canonical binding. A new layout gets a new tag;
/// a proof made for one tag is never accepted under another.
pub const BINDING_VERSION: &str = "aib-approve/binding/v2";

/// How much clock skew a proof's issue time may be ahead of the checker.
pub const ISSUE_SKEW_S: u64 = 30;

/// Longest life of a single-action approval: 1.x `consent.EXPIRE_S` (30 min).
pub const SINGLE_ACTION_MAX_S: u64 = 30 * 60;

/// Longest `expiry - issued` a proof for `cat` may have, whatever the caller
/// asks for: a single action lives at most [`SINGLE_ACTION_MAX_S`]; a category that
/// may hold a time window lives at most that window
/// ([`crate::category::window_allowed_s`]). c477a T135-a review L1: without this a
/// caller that copied the proof's expiry into `expect` would accept a proof signed
/// for a year.
pub fn max_lifetime_s(cat: Category) -> u64 {
    SINGLE_ACTION_MAX_S.max(crate::category::window_allowed_s(cat))
}

/// Longest lifetime [`verify`] accepts for any category: the largest
/// [`max_lifetime_s`]. A used-nonce ledger may forget a nonce only after this plus
/// [`ISSUE_SKEW_S`] (`issued` may be up to the skew after the nonce was consumed); the
/// skew is not included here, the ledger adds it (aib-consent `FileNonces::prune`,
/// c477a T136-3b).
pub fn max_proof_lifetime_s() -> u64 {
    crate::category::ALL
        .iter()
        .map(|c| max_lifetime_s(*c))
        .fold(SINGLE_ACTION_MAX_S, u64::max)
}

/// Proof strength, weakest first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ProofLevel {
    PresenceOnly,
    HardwareSigned,
}

impl ProofLevel {
    pub fn id(self) -> &'static str {
        match self {
            ProofLevel::PresenceOnly => "presence_only",
            ProofLevel::HardwareSigned => "hardware_signed",
        }
    }
}

/// What one approval covers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Binding {
    pub category: Category,
    /// Who asks (seat code or person id). A claim, not an identity: the signature
    /// makes it tamper-evident, it does not make it true.
    pub actor: String,
    /// What will be done, e.g. `consent.run`.
    pub operation: String,
    /// What it is done to, e.g. a host and path, a site, a secret name.
    pub resource: String,
    /// sha256 (64 lowercase hex) of the exact parameters that will be used.
    pub parameter_digest: String,
    /// Fresh random value, lowercase hex, at least 32 digits.
    pub nonce: String,
    /// Unix seconds when the approval was made.
    pub issued: u64,
    /// Unix seconds after which the approval is void.
    pub expiry: u64,
}

/// Why a binding cannot be built or a proof is not accepted.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// A field is empty, too long, or has a character that cannot appear in the
    /// canonical form.
    BadField(&'static str),
    /// The proof covers something else than what is about to run.
    Mismatch(&'static str),
    /// `expiry` is not after `issued`.
    BadTime,
    Expired,
    /// `expiry - issued` is longer than [`max_lifetime_s`] allows for the category.
    TooLongLived,
    NotYet,
    /// Weaker than the category needs.
    TooWeak {
        have: ProofLevel,
        need: ProofLevel,
    },
    /// A presence-only proof arrived from outside the flow.
    PresenceNotTransferable,
    /// No signature on a hardware-signed proof.
    NoSignature,
    /// The key is not on the trusted list.
    UntrustedKey,
    BadSig,
    /// The nonce was already used.
    Replay,
    /// The JSON form is not readable.
    Corrupt(&'static str),
}

impl Refusal {
    /// Short machine word, in the style of the 1.x `why` strings.
    pub fn why(&self) -> &'static str {
        match self {
            Refusal::BadField(_) => "bad_field",
            Refusal::Mismatch(_) => "mismatch",
            Refusal::BadTime => "bad_time",
            Refusal::Expired => "expired",
            Refusal::TooLongLived => "too_long_lived",
            Refusal::NotYet => "not_yet",
            Refusal::TooWeak { .. } => "too_weak",
            Refusal::PresenceNotTransferable => "presence_not_transferable",
            Refusal::NoSignature => "no_signature",
            Refusal::UntrustedKey => "untrusted_key",
            Refusal::BadSig => "bad_sig",
            Refusal::Replay => "replay",
            Refusal::Corrupt(_) => "corrupt",
        }
    }
}

/// sha256 of the parameter bytes, as [`Binding::parameter_digest`] wants it.
pub fn parameter_digest(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

pub(crate) fn hex(b: &[u8]) -> String {
    const D: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(b.len() * 2);
    for x in b {
        s.push(D[(x >> 4) as usize] as char);
        s.push(D[(x & 15) as usize] as char);
    }
    s
}

fn is_lower_hex(s: &str) -> bool {
    s.bytes()
        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

const FIELD_MAX: usize = text::FULL_MAX;

fn check_field(name: &'static str, v: &str) -> Result<(), Refusal> {
    if v.is_empty() || v.chars().count() > FIELD_MAX || v.contains('=') {
        return Err(Refusal::BadField(name));
    }
    if v.chars().any(text::is_bad_char) {
        return Err(Refusal::BadField(name));
    }
    Ok(())
}

impl Binding {
    /// Check every field. Called by [`Binding::canonical`] and by the checker.
    pub fn validate(&self) -> Result<(), Refusal> {
        check_field("actor", &self.actor)?;
        check_field("operation", &self.operation)?;
        check_field("resource", &self.resource)?;
        if self.parameter_digest.len() != 64 || !is_lower_hex(&self.parameter_digest) {
            return Err(Refusal::BadField("parameter_digest"));
        }
        if self.nonce.len() < 32 || self.nonce.len() > 128 || !is_lower_hex(&self.nonce) {
            return Err(Refusal::BadField("nonce"));
        }
        if self.expiry <= self.issued {
            return Err(Refusal::BadTime);
        }
        Ok(())
    }

    /// The exact bytes a hardware key signs: the version tag, then one `key=value`
    /// line per field in a fixed order. Field values cannot hold `=` or any line
    /// break, so the form has one reading.
    pub fn canonical(&self) -> Result<Vec<u8>, Refusal> {
        self.validate()?;
        let s = format!(
            "{BINDING_VERSION}\ncategory={}\nactor={}\noperation={}\nresource={}\nparameter_digest={}\nnonce={}\nissued={}\nexpiry={}\n",
            self.category.id(),
            self.actor,
            self.operation,
            self.resource,
            self.parameter_digest,
            self.nonce,
            self.issued,
            self.expiry,
        );
        Ok(s.into_bytes())
    }

    /// The first field that differs from `other`, if any. Every field counts: a
    /// proof with a later expiry or another issue time covers something else than
    /// what the caller is about to allow.
    fn first_difference(&self, other: &Binding) -> Option<&'static str> {
        if self.category != other.category {
            return Some("category");
        }
        if self.actor != other.actor {
            return Some("actor");
        }
        if self.operation != other.operation {
            return Some("operation");
        }
        if self.resource != other.resource {
            return Some("resource");
        }
        if self.parameter_digest != other.parameter_digest {
            return Some("parameter_digest");
        }
        if self.nonce != other.nonce {
            return Some("nonce");
        }
        if self.issued != other.issued {
            return Some("issued");
        }
        if self.expiry != other.expiry {
            return Some("expiry");
        }
        None
    }
}

/// A signature by a hardware-held key over [`Binding::canonical`]. The only kind of
/// proof that may travel between processes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Proof {
    /// e.g. `macos-se`, `windows-tpm`.
    pub provider: String,
    pub key_id: String,
    /// e.g. `ecdsa-p256-sha256`.
    pub alg: String,
    pub binding: Binding,
    pub signature: Vec<u8>,
}

/// A fresh owner check (fingerprint or login password) that the platform flow in
/// this crate saw for `binding`. Private fields, no `Clone`, and only this crate can
/// make one; [`verify`] takes it by value, so it is used once and cannot be copied,
/// stored or read back from elsewhere.
///
/// This keeps other code from producing one by mistake. A same-user AI that edits
/// this crate can still make one; the real defence is the fingerprint press.
#[derive(Debug, PartialEq, Eq)]
pub struct Presence {
    provider: String,
    binding: Binding,
}

impl Presence {
    /// Only the platform flow (T135-b) and this crate's tests call this.
    #[allow(dead_code)]
    pub(crate) fn observed(provider: &str, binding: Binding) -> Presence {
        Presence {
            provider: provider.to_owned(),
            binding,
        }
    }

    pub fn provider(&self) -> &str {
        &self.provider
    }

    pub fn binding(&self) -> &Binding {
        &self.binding
    }
}

/// What [`verify`] is handed.
#[derive(Debug, PartialEq, Eq)]
pub enum Evidence {
    Signed(Proof),
    Presence(Presence),
}

impl Evidence {
    pub fn level(&self) -> ProofLevel {
        match self {
            Evidence::Signed(_) => ProofLevel::HardwareSigned,
            Evidence::Presence(_) => ProofLevel::PresenceOnly,
        }
    }

    fn binding(&self) -> &Binding {
        match self {
            Evidence::Signed(p) => &p.binding,
            Evidence::Presence(p) => &p.binding,
        }
    }
}

/// Trusted public keys. Implementations must not read a file the same-user AI
/// can write; see the module doc.
pub trait TrustedKeys {
    /// `None`: the key is not trusted. `Some(ok)`: the signature check result.
    fn check(
        &self,
        provider: &str,
        key_id: &str,
        alg: &str,
        msg: &[u8],
        sig: &[u8],
    ) -> Option<bool>;
}

/// Records used nonces. `consume` must be atomic: true the first time a nonce is
/// seen, false every time after.
pub trait NonceLedger {
    fn consume(&mut self, nonce: &str) -> bool;
}

/// In-memory ledger (one process; tests and a single long-running daemon).
#[derive(Default, Debug)]
pub struct MemoryNonces(std::collections::HashSet<String>);

impl NonceLedger for MemoryNonces {
    fn consume(&mut self, nonce: &str) -> bool {
        self.0.insert(nonce.to_owned())
    }
}

/// Accept `evidence` for the action described by `expect`, or say why not.
///
/// `expect` is built by the caller from what it is about to run, never copied from
/// the proof. The nonce is consumed last.
pub fn verify(
    evidence: Evidence,
    expect: &Binding,
    now: u64,
    keys: &dyn TrustedKeys,
    nonces: &mut dyn NonceLedger,
) -> Result<(), Refusal> {
    expect.validate()?;
    let got = evidence.binding();
    got.validate()?;
    if let Some(field) = got.first_difference(expect) {
        return Err(Refusal::Mismatch(field));
    }
    let need = expect.category.required_proof();
    let have = evidence.level();
    if have < need {
        return Err(Refusal::TooWeak { have, need });
    }
    if expect.expiry - expect.issued > max_lifetime_s(expect.category) {
        return Err(Refusal::TooLongLived);
    }
    if now >= expect.expiry {
        return Err(Refusal::Expired);
    }
    if expect.issued > now.saturating_add(ISSUE_SKEW_S) {
        return Err(Refusal::NotYet);
    }
    if let Evidence::Signed(proof) = &evidence {
        if proof.signature.is_empty() {
            return Err(Refusal::NoSignature);
        }
        let msg = proof.binding.canonical()?;
        match keys.check(
            &proof.provider,
            &proof.key_id,
            &proof.alg,
            &msg,
            &proof.signature,
        ) {
            None => return Err(Refusal::UntrustedKey),
            Some(false) => return Err(Refusal::BadSig),
            Some(true) => {}
        }
    }
    if !nonces.consume(&expect.nonce) {
        return Err(Refusal::Replay);
    }
    Ok(())
}

impl Proof {
    /// Read a proof another process produced. Only hardware-signed proofs travel;
    /// a presence-only one is refused here, because outside the flow that saw the
    /// fingerprint it is just a claim.
    pub fn from_json(v: &serde_json::Value) -> Result<Proof, Refusal> {
        let s = |k: &'static str| -> Result<String, Refusal> {
            v.get(k)
                .and_then(|x| x.as_str())
                .map(str::to_owned)
                .ok_or(Refusal::Corrupt(k))
        };
        let n = |k: &'static str| -> Result<u64, Refusal> {
            v.get(k)
                .and_then(serde_json::Value::as_u64)
                .ok_or(Refusal::Corrupt(k))
        };
        match s("v")?.as_str() {
            BINDING_VERSION => {}
            _ => return Err(Refusal::Corrupt("v")),
        }
        match s("level")?.as_str() {
            "hardware_signed" => {}
            "presence_only" => return Err(Refusal::PresenceNotTransferable),
            _ => return Err(Refusal::Corrupt("level")),
        }
        let cat_id = s("category")?;
        let category = Category::parse(&cat_id);
        if category.id() != cat_id {
            return Err(Refusal::Corrupt("category"));
        }
        let sig_hex = s("signature")?;
        let signature = unhex(&sig_hex).ok_or(Refusal::Corrupt("signature"))?;
        Ok(Proof {
            provider: s("provider")?,
            key_id: s("key_id")?,
            alg: s("alg")?,
            binding: Binding {
                category,
                actor: s("actor")?,
                operation: s("operation")?,
                resource: s("resource")?,
                parameter_digest: s("parameter_digest")?,
                nonce: s("nonce")?,
                issued: n("issued")?,
                expiry: n("expiry")?,
            },
            signature,
        })
    }
}

pub(crate) fn unhex(s: &str) -> Option<Vec<u8>> {
    if s.is_empty() || !s.len().is_multiple_of(2) || !is_lower_hex(s) {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct NoKeys;
    impl TrustedKeys for NoKeys {
        fn check(&self, _: &str, _: &str, _: &str, _: &[u8], _: &[u8]) -> Option<bool> {
            None
        }
    }

    const NOW: u64 = 1_800_000_000;

    fn binding(cat: Category) -> Binding {
        Binding {
            category: cat,
            actor: "f457/ebb36".into(),
            operation: "consent.run".into(),
            resource: "host:/srv/site".into(),
            parameter_digest: parameter_digest(b"DRY=0\n"),
            nonce: "0123456789abcdef0123456789abcdef".into(),
            issued: NOW - 10,
            expiry: NOW + 120,
        }
    }

    #[test]
    fn presence_only_is_too_weak_for_high_risk_and_enough_for_command() {
        let mut n = MemoryNonces::default();
        let b = binding(Category::Secret);
        assert_eq!(
            verify(
                Evidence::Presence(Presence::observed("macos-la", b.clone())),
                &b,
                NOW,
                &NoKeys,
                &mut n
            ),
            Err(Refusal::TooWeak {
                have: ProofLevel::PresenceOnly,
                need: ProofLevel::HardwareSigned
            })
        );
        let c = binding(Category::Command);
        let p = Presence::observed("macos-la", c.clone());
        assert_eq!(p.provider(), "macos-la");
        assert_eq!(p.binding(), &c);
        assert_eq!(
            verify(Evidence::Presence(p), &c, NOW, &NoKeys, &mut n),
            Ok(())
        );
        assert_eq!(
            verify(
                Evidence::Presence(Presence::observed("macos-la", c.clone())),
                &c,
                NOW,
                &NoKeys,
                &mut n
            ),
            Err(Refusal::Replay)
        );
    }

    #[test]
    fn presence_for_another_action_or_after_expiry_is_refused() {
        let mut n = MemoryNonces::default();
        let want = binding(Category::Command);
        let mut other = want.clone();
        other.expiry += 3600;
        assert_eq!(
            verify(
                Evidence::Presence(Presence::observed("macos-la", other)),
                &want,
                NOW,
                &NoKeys,
                &mut n
            ),
            Err(Refusal::Mismatch("expiry"))
        );
        assert_eq!(
            verify(
                Evidence::Presence(Presence::observed("macos-la", want.clone())),
                &want,
                want.expiry,
                &NoKeys,
                &mut n
            ),
            Err(Refusal::Expired)
        );
    }
}
