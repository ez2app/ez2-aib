//! Hardware signing keys (B2 T135-b slice 2, PLAN-t135b §7): the stored key file,
//! making a key, loading one, and signing a [`Binding`] into a [`Proof`].
//!
//! The rule that matters (DECISION-t135b-objc2-deps, 519d0 11-13 22:2x): the public
//! key a device reports for a stored blob proves nothing about the blob — measured on
//! macOS, a blob with one byte changed still reported the registered public key and
//! only failed when asked to sign. So [`sign`] checks every signature against the
//! **registered** public key ([`TrustedKeys`]) before it hands out a proof; a signature
//! that does not verify never becomes a [`Proof`]. The check at load ([`load`]) only
//! catches a changed storage format.
//!
//! The device is a trait so the flow can be tested without a Secure Enclave; the real
//! one is [`crate::macos::MacosSe`] (aib-sys `presence_macos`).

use crate::proof::{hex, unhex, Binding, Proof, Refusal, TrustedKeys};
use crate::text::{self, TextError};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// Version of the key file this code writes and reads. 1.x `.sekey` files (CryptoKit
/// `dataRepresentation`) are not read: whether they can be reused is part of the
/// owner's acceptance run.
pub const KEYFILE_VERSION: u64 = 2;
pub const PROVIDER_MACOS_SE: &str = "macos-se";
pub const ALG_ECDSA_P256_SHA256: &str = "ecdsa-p256-sha256";
/// An uncompressed P-256 public key in X9.63 form: `0x04 || X || Y`.
pub const PUBLIC_KEY_LEN: usize = 65;
/// Largest stored blob accepted (the Secure Enclave's token object id is a few hundred
/// bytes; measured 284).
pub const BLOB_MAX: usize = 4096;

/// What a key is for; decides what the device asks of the owner.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyKind {
    /// Every approval signature: Touch ID with the fingers enrolled now.
    Signing,
    /// The key that signs time-window grants: Touch ID or the login password (1.x
    /// GRANT_KEY, the only key allowed the password).
    Grant,
}

impl KeyKind {
    pub fn id(self) -> &'static str {
        match self {
            KeyKind::Signing => "signing",
            KeyKind::Grant => "grant",
        }
    }

    fn parse(s: &str) -> Option<KeyKind> {
        match s {
            "signing" => Some(KeyKind::Signing),
            "grant" => Some(KeyKind::Grant),
            _ => None,
        }
    }
}

/// Why a key could not be made, loaded or used.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum KeyError {
    /// The key file is not readable as version 2.
    Corrupt(&'static str),
    /// The device reports another public key for the stored blob than the file says:
    /// the storage format changed (or the file was edited). Make a new key.
    FormatChanged,
    /// The reason cannot be shown to the person.
    Reason(TextError),
    /// The binding is not valid.
    Binding(Refusal),
    /// The device said no; the word is the 1.x helper code (`cancel`, `weak_key`, …).
    Device(&'static str),
    /// The device signed, but the signature does not verify under the registered
    /// public key. No proof is made.
    NotRegistered,
}

impl KeyError {
    /// A stable word for logs and the caller's refusal message.
    pub fn why(&self) -> &'static str {
        match self {
            KeyError::Corrupt(_) => "corrupt",
            KeyError::FormatChanged => "format_changed",
            KeyError::Reason(e) => e.detail(),
            KeyError::Binding(r) => r.why(),
            KeyError::Device(code) => code,
            KeyError::NotRegistered => "not_registered",
        }
    }
}

/// The platform side of a hardware key. Errors are the 1.x helper code words.
pub trait Device {
    /// Make a key for `kind` in the owner's own session; returns the stored blob and the
    /// public key.
    fn create(&self, kind: KeyKind) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), &'static str>;
    /// Ok only if the key at `blob` asks for the owner (no signature without them).
    fn probe(&self, blob: &[u8]) -> Result<(), &'static str>;
    /// The public key the device reports for `blob`.
    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str>;
    /// Sign `msg` (ECDSA P-256, SHA-256, DER); the device shows `reason` to the owner.
    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str>;
}

/// A stored hardware key: the device blob plus the public key it had when made.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct KeyFile {
    pub kind: KeyKind,
    pub key_id: String,
    pub blob: Vec<u8>,
    pub public: [u8; PUBLIC_KEY_LEN],
}

/// The key id: the first 16 bytes of SHA-256 over the public key, in hex.
pub fn key_id_of(public: &[u8; PUBLIC_KEY_LEN]) -> String {
    hex(&Sha256::digest(public)[..16])
}

impl KeyFile {
    pub fn to_json(&self) -> Value {
        json!({
            "v": KEYFILE_VERSION,
            "provider": PROVIDER_MACOS_SE,
            "kind": self.kind.id(),
            "key_id": self.key_id,
            "blob": hex(&self.blob),
            "public": hex(&self.public),
        })
    }

    /// Read a version 2 key file. Exactly these six fields; anything else is refused.
    pub fn from_json(v: &Value) -> Result<KeyFile, KeyError> {
        let obj = v.as_object().ok_or(KeyError::Corrupt("object"))?;
        if obj.len() != 6 {
            return Err(KeyError::Corrupt("fields"));
        }
        if v.get("v").and_then(Value::as_u64) != Some(KEYFILE_VERSION) {
            return Err(KeyError::Corrupt("v"));
        }
        let s = |k: &'static str| v.get(k).and_then(Value::as_str).ok_or(KeyError::Corrupt(k));
        if s("provider")? != PROVIDER_MACOS_SE {
            return Err(KeyError::Corrupt("provider"));
        }
        let kind = KeyKind::parse(s("kind")?).ok_or(KeyError::Corrupt("kind"))?;
        let blob = unhex(s("blob")?).ok_or(KeyError::Corrupt("blob"))?;
        if blob.len() > BLOB_MAX {
            return Err(KeyError::Corrupt("blob"));
        }
        let public = unhex(s("public")?)
            .and_then(|b| <[u8; PUBLIC_KEY_LEN]>::try_from(b.as_slice()).ok())
            .filter(|p| p[0] == 0x04)
            .ok_or(KeyError::Corrupt("public"))?;
        let key_id = s("key_id")?.to_owned();
        if key_id != key_id_of(&public) {
            return Err(KeyError::Corrupt("key_id"));
        }
        Ok(KeyFile {
            kind,
            key_id,
            blob,
            public,
        })
    }
}

/// Make a new key of `kind` and check that it asks for the owner before it is kept.
/// The caller stores [`KeyFile::to_json`] and registers the public key somewhere the
/// same-user AI cannot write (that part is not in this crate).
pub fn enroll(dev: &dyn Device, kind: KeyKind) -> Result<KeyFile, KeyError> {
    let (blob, public) = dev.create(kind).map_err(KeyError::Device)?;
    if blob.is_empty() || blob.len() > BLOB_MAX || public[0] != 0x04 {
        return Err(KeyError::Device("se_error"));
    }
    dev.probe(&blob).map_err(KeyError::Device)?;
    Ok(KeyFile {
        kind,
        key_id: key_id_of(&public),
        blob,
        public,
    })
}

/// Read a key file and check the device still reports the same public key for it
/// (format check only; see the module notes).
pub fn load(dev: &dyn Device, v: &Value) -> Result<KeyFile, KeyError> {
    let kf = KeyFile::from_json(v)?;
    let reported = dev
        .public_key(&kf.blob)
        .map_err(|_| KeyError::FormatChanged)?;
    if reported != kf.public {
        return Err(KeyError::FormatChanged);
    }
    Ok(kf)
}

/// Sign `binding` with `key`, showing `reason` to the owner. Checks, in order: the
/// binding, the reason text, that the key still asks for the owner, then — after the
/// device signs — that the signature verifies under the registered public key. Only
/// then is there a [`Proof`].
pub fn sign(
    dev: &dyn Device,
    key: &KeyFile,
    binding: Binding,
    reason: &str,
    registered: &dyn TrustedKeys,
) -> Result<Proof, KeyError> {
    binding.validate().map_err(KeyError::Binding)?;
    text::check(reason, text::SE_SIGN_MAX).map_err(KeyError::Reason)?;
    dev.probe(&key.blob).map_err(KeyError::Device)?;
    let msg = binding.canonical().map_err(KeyError::Binding)?;
    let signature = dev
        .sign(&key.blob, reason, &msg)
        .map_err(KeyError::Device)?;
    match registered.check(
        PROVIDER_MACOS_SE,
        &key.key_id,
        ALG_ECDSA_P256_SHA256,
        &msg,
        &signature,
    ) {
        Some(true) => {}
        _ => return Err(KeyError::NotRegistered),
    }
    Ok(Proof {
        provider: PROVIDER_MACOS_SE.to_owned(),
        key_id: key.key_id.clone(),
        alg: ALG_ECDSA_P256_SHA256.to_owned(),
        binding,
        signature,
    })
}
