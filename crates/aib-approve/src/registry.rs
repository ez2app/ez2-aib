//! The trusted hardware-key registry (B2 T135-b slice 3,
//! DECISION-hardware-signed-registry-519d0-1113 items 1–8).
//!
//! Where the file lives is not a defence (a same-user AI can write it). What is:
//! - every entry is signed by the **registry root**, a hardware key that needs the owner;
//!   an entry whose signature does not verify does not exist (item 1), so replacing or
//!   cutting the file can only drop keys, never add them;
//! - the root itself is checked at every load: the device must still report the stored
//!   public key, and the root must refuse to sign without the owner (non-interactive
//!   probe). A root that signs without asking is `weak_key` and the whole registry is void
//!   (item 6);
//! - the root's fingerprint and creation date go into **every** approval window, composed
//!   here, so the owner sees it change if the whole file is swapped for one under a new
//!   root (item 7);
//! - a new root must be signed by the old one; the window then shows both (item 2).
//!
//! Known (DECISION residual risk): an AI that makes its own owner-ACL root and swaps the
//! whole file is caught only by the owner seeing a new fingerprint in the next window.
//! Deleting a later `revoke` entry brings that key back (entries are only signed one by
//! one); the window line shows the entry count so a drop is visible.

use crate::hwkey::{key_id_of, Device, KeyError, KeyKind, ALG_ECDSA_P256_SHA256, PUBLIC_KEY_LEN};
use crate::proof::{hex, unhex, Binding, Proof, TrustedKeys};
use crate::text::{self, TextError};
use serde_json::{json, Map, Value};

pub const REGISTRY_VERSION: u64 = 1;
const ENTRY_DOMAIN: &str = "aib-approve/registry/entry/v1";
const ROTATE_DOMAIN: &str = "aib-approve/registry/rotate/v1";
/// Most entries one registry may hold (adds and revokes together).
pub const ENTRIES_MAX: usize = 1024;

/// Checks an ECDSA P-256 / SHA-256 / DER signature. The macOS one is
/// `macos::SeVerify` (macOS only); tests use a fake.
pub trait SigVerify {
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool;
}

/// Why a registry is void (as a whole) or an entry could not be made.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RegistryError {
    /// Not readable as version 1.
    Corrupt(&'static str),
    /// The device reports another public key for the stored root.
    RootChanged,
    /// The root signs without the owner: everything in it is void (item 6).
    RootWeak,
    /// The root could not be checked (device error word).
    RootProbe(&'static str),
    /// A new root without a valid signature by the previous one.
    RotationUnsigned,
    /// Making an entry failed.
    Key(KeyError),
    Reason(TextError),
}

impl RegistryError {
    pub fn why(&self) -> &'static str {
        match self {
            RegistryError::Corrupt(_) => "corrupt",
            RegistryError::RootChanged => "root_changed",
            RegistryError::RootWeak => "weak_key",
            RegistryError::RootProbe(w) => w,
            RegistryError::RotationUnsigned => "rotation_unsigned",
            RegistryError::Key(e) => e.why(),
            RegistryError::Reason(e) => e.detail(),
        }
    }
}

/// The registry root as stored in the file header.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Root {
    pub blob: Vec<u8>,
    pub public: [u8; PUBLIC_KEY_LEN],
    pub key_id: String,
    /// Unix seconds when the root was made.
    pub made_at: u64,
    /// The root this one replaced, and its signature over the hand-over.
    pub prev: Option<Rotation>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rotation {
    pub public: [u8; PUBLIC_KEY_LEN],
    pub made_at: u64,
    pub sig: Vec<u8>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Add,
    Revoke,
}

impl Op {
    pub fn id(self) -> &'static str {
        match self {
            Op::Add => "add",
            Op::Revoke => "revoke",
        }
    }
}

/// One signed line of the registry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub seq: u64,
    pub op: Op,
    pub provider: String,
    pub alg: String,
    pub kind: KeyKind,
    pub public: [u8; PUBLIC_KEY_LEN],
    pub at: u64,
    pub sig: Vec<u8>,
}

/// A trusted key after the registry was read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Trusted {
    pub provider: String,
    pub alg: String,
    pub kind: KeyKind,
    pub public: [u8; PUBLIC_KEY_LEN],
}

/// A registry that passed the root checks, with the entries whose signatures verified.
pub struct Registry<'v> {
    root: Root,
    keys: Vec<(String, Trusted)>,
    last_seq: u64,
    applied: usize,
    /// Entries that did not count (bad signature, out of order, unreadable, revoke of
    /// an unknown key). Reported, never silent.
    pub skipped: usize,
    verify: &'v dyn SigVerify,
}

fn is_word(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| matches!(b, b'a'..=b'z' | b'0'..=b'9' | b'_' | b'-'))
}

fn public_of(s: &str) -> Option<[u8; PUBLIC_KEY_LEN]> {
    unhex(s)
        .and_then(|b| <[u8; PUBLIC_KEY_LEN]>::try_from(b.as_slice()).ok())
        .filter(|p| p[0] == 0x04)
}

/// Bytes the root signs for an entry. Every field is a word, a number or hex, so the
/// newline separator cannot appear inside one.
pub fn entry_message(root_key_id: &str, e: &Entry) -> Vec<u8> {
    [
        ENTRY_DOMAIN.to_owned(),
        root_key_id.to_owned(),
        e.seq.to_string(),
        e.op.id().to_owned(),
        e.provider.clone(),
        e.alg.clone(),
        e.kind.id().to_owned(),
        key_id_of(&e.public),
        hex(&e.public),
        e.at.to_string(),
    ]
    .join("\n")
    .into_bytes()
}

/// Bytes the old root signs to hand over to a new one.
pub fn rotate_message(
    old: &[u8; PUBLIC_KEY_LEN],
    new: &[u8; PUBLIC_KEY_LEN],
    made_at: u64,
) -> Vec<u8> {
    [
        ROTATE_DOMAIN.to_owned(),
        key_id_of(old),
        key_id_of(new),
        hex(new),
        made_at.to_string(),
    ]
    .join("\n")
    .into_bytes()
}

/// `YYYY-MM-DD` (UTC) for unix seconds.
pub fn utc_date(secs: u64) -> String {
    // Howard Hinnant's civil_from_days.
    let z = (secs / 86_400) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

/// First 8 hex digits of a key id: what the owner compares by eye.
pub fn fingerprint8(public: &[u8; PUBLIC_KEY_LEN]) -> String {
    key_id_of(public)[..8].to_owned()
}

impl Root {
    fn to_json(&self) -> Value {
        let mut m = Map::new();
        m.insert("blob".into(), json!(hex(&self.blob)));
        m.insert("public".into(), json!(hex(&self.public)));
        m.insert("key_id".into(), json!(self.key_id));
        m.insert("made_at".into(), json!(self.made_at));
        if let Some(p) = &self.prev {
            m.insert(
                "prev".into(),
                json!({"public": hex(&p.public), "made_at": p.made_at, "sig": hex(&p.sig)}),
            );
        }
        Value::Object(m)
    }

    fn from_json(v: &Value) -> Result<Root, RegistryError> {
        let c = RegistryError::Corrupt;
        let o = v.as_object().ok_or(c("root"))?;
        if !(4..=5).contains(&o.len()) {
            return Err(c("root"));
        }
        let s = |k: &'static str| v.get(k).and_then(Value::as_str).ok_or(c(k));
        let blob = unhex(s("blob")?)
            .filter(|b| b.len() <= crate::hwkey::BLOB_MAX)
            .ok_or(c("blob"))?;
        let public = public_of(s("public")?).ok_or(c("public"))?;
        let key_id = s("key_id")?.to_owned();
        if key_id != key_id_of(&public) {
            return Err(c("key_id"));
        }
        let made_at = v
            .get("made_at")
            .and_then(Value::as_u64)
            .ok_or(c("made_at"))?;
        let prev = match v.get("prev") {
            None if o.len() == 4 => None,
            Some(p) if o.len() == 5 => {
                if p.as_object().map(Map::len) != Some(3) {
                    return Err(c("prev"));
                }
                Some(Rotation {
                    public: p
                        .get("public")
                        .and_then(Value::as_str)
                        .and_then(public_of)
                        .ok_or(c("prev"))?,
                    made_at: p.get("made_at").and_then(Value::as_u64).ok_or(c("prev"))?,
                    sig: p
                        .get("sig")
                        .and_then(Value::as_str)
                        .and_then(unhex)
                        .ok_or(c("prev"))?,
                })
            }
            _ => return Err(c("root")),
        };
        Ok(Root {
            blob,
            public,
            key_id,
            made_at,
            prev,
        })
    }

    /// What the owner sees about the root in every window (item 7).
    pub fn window_line(&self) -> String {
        let mut s = format!(
            "根鑰 {} {}",
            fingerprint8(&self.public),
            utc_date(self.made_at)
        );
        if let Some(p) = &self.prev {
            s.push_str(&format!(
                "(換自 {} {})",
                fingerprint8(&p.public),
                utc_date(p.made_at)
            ));
        }
        s
    }
}

impl Entry {
    fn to_json(&self) -> Value {
        json!({
            "seq": self.seq,
            "op": self.op.id(),
            "provider": self.provider,
            "alg": self.alg,
            "kind": self.kind.id(),
            "public": hex(&self.public),
            "at": self.at,
            "sig": hex(&self.sig),
        })
    }

    fn from_json(v: &Value) -> Option<Entry> {
        if v.as_object().map(Map::len) != Some(8) {
            return None;
        }
        let s = |k: &str| v.get(k).and_then(Value::as_str);
        let n = |k: &str| v.get(k).and_then(Value::as_u64);
        let op = match s("op")? {
            "add" => Op::Add,
            "revoke" => Op::Revoke,
            _ => return None,
        };
        let provider = s("provider").filter(|x| is_word(x))?.to_owned();
        let alg = s("alg").filter(|x| is_word(x))?.to_owned();
        Some(Entry {
            seq: n("seq")?,
            op,
            provider,
            alg,
            kind: KeyKind::parse(s("kind")?)?,
            public: public_of(s("public")?)?,
            at: n("at")?,
            sig: unhex(s("sig")?)?,
        })
    }
}

/// A new, empty registry file for `root` (the caller made the root with
/// [`crate::hwkey::enroll`] and [`KeyKind::RegistryRoot`], with the owner present).
pub fn new_file(root: &Root) -> Value {
    json!({"v": REGISTRY_VERSION, "root": root.to_json(), "entries": []})
}

impl<'v> Registry<'v> {
    /// Read and check a registry. Root problems void the whole registry (`Err`);
    /// a bad entry only drops that entry (counted in [`Registry::skipped`]).
    pub fn load(
        v: &Value,
        dev: &dyn Device,
        verify: &'v dyn SigVerify,
    ) -> Result<Registry<'v>, RegistryError> {
        let o = v.as_object().ok_or(RegistryError::Corrupt("object"))?;
        if o.len() != 3 || v.get("v").and_then(Value::as_u64) != Some(REGISTRY_VERSION) {
            return Err(RegistryError::Corrupt("v"));
        }
        let root = Root::from_json(&v["root"])?;
        let reported = dev
            .public_key(&root.blob)
            .map_err(RegistryError::RootProbe)?;
        if reported != root.public {
            return Err(RegistryError::RootChanged);
        }
        match dev.probe(&root.blob) {
            Ok(()) => {}
            Err("weak_key") => return Err(RegistryError::RootWeak),
            Err(w) => return Err(RegistryError::RootProbe(w)),
        }
        if let Some(p) = &root.prev {
            let msg = rotate_message(&p.public, &root.public, root.made_at);
            if !verify.verify(&p.public, &msg, &p.sig) {
                return Err(RegistryError::RotationUnsigned);
            }
        }
        let entries = v["entries"]
            .as_array()
            .ok_or(RegistryError::Corrupt("entries"))?;
        if entries.len() > ENTRIES_MAX {
            return Err(RegistryError::Corrupt("entries"));
        }
        let mut r = Registry {
            root,
            keys: Vec::new(),
            last_seq: 0,
            applied: 0,
            skipped: 0,
            verify,
        };
        for ev in entries {
            match Entry::from_json(ev) {
                Some(e) if r.accept(&e) => r.applied += 1,
                _ => r.skipped += 1,
            }
        }
        Ok(r)
    }

    fn accept(&mut self, e: &Entry) -> bool {
        if e.seq <= self.last_seq {
            return false;
        }
        let msg = entry_message(&self.root.key_id, e);
        if !self.verify.verify(&self.root.public, &msg, &e.sig) {
            return false;
        }
        let id = key_id_of(&e.public);
        match e.op {
            Op::Add => {
                if self.keys.iter().any(|(k, _)| *k == id) {
                    return false;
                }
                self.keys.push((
                    id,
                    Trusted {
                        provider: e.provider.clone(),
                        alg: e.alg.clone(),
                        kind: e.kind,
                        public: e.public,
                    },
                ));
            }
            Op::Revoke => {
                let before = self.keys.len();
                self.keys.retain(|(k, _)| *k != id);
                if self.keys.len() == before {
                    return false;
                }
            }
        }
        self.last_seq = e.seq;
        true
    }

    pub fn root(&self) -> &Root {
        &self.root
    }

    /// Entries that counted.
    pub fn applied(&self) -> usize {
        self.applied
    }

    pub fn trusted(&self) -> impl Iterator<Item = (&str, &Trusted)> {
        self.keys.iter().map(|(k, t)| (k.as_str(), t))
    }

    /// The root line plus the count of trusted keys, for every window (item 7).
    pub fn window_line(&self) -> String {
        format!("{} · 登記 {} 把", self.root.window_line(), self.keys.len())
    }

    /// `action` as the owner will see it, with the root line added by the core. The
    /// caller's text cannot replace or drop the root line.
    pub fn reason(&self, action: &str) -> Result<String, TextError> {
        text::check(action, text::SE_SIGN_MAX)?;
        let full = format!("{action} · {}", self.window_line());
        text::check(&full, text::SE_SIGN_MAX)?;
        Ok(full)
    }

    /// Next sequence number for a new entry.
    pub fn next_seq(&self) -> u64 {
        self.last_seq + 1
    }

    /// Have the root sign a new entry (one owner press). The window lists what changes
    /// (item 3). The signature is checked under the root's public key before the entry is
    /// returned; the caller appends it to the file.
    pub fn sign_entry(
        &self,
        dev: &dyn Device,
        op: Op,
        kind: KeyKind,
        public: [u8; PUBLIC_KEY_LEN],
        at: u64,
    ) -> Result<Entry, RegistryError> {
        let mut e = Entry {
            seq: self.next_seq(),
            op,
            provider: crate::hwkey::PROVIDER_MACOS_SE.to_owned(),
            alg: ALG_ECDSA_P256_SHA256.to_owned(),
            kind,
            public,
            at,
            sig: Vec::new(),
        };
        let what = match op {
            Op::Add => "登記：多了",
            Op::Revoke => "撤銷：少了",
        };
        let action = format!(
            "{what} {} {} 指紋 {}",
            e.provider,
            kind.id(),
            fingerprint8(&public)
        );
        let reason = self.reason(&action).map_err(RegistryError::Reason)?;
        let msg = entry_message(&self.root.key_id, &e);
        dev.probe(&self.root.blob)
            .map_err(|w| RegistryError::Key(KeyError::Device(w)))?;
        let sig = dev
            .sign(&self.root.blob, &reason, &msg)
            .map_err(|w| RegistryError::Key(KeyError::Device(w)))?;
        if !self.verify.verify(&self.root.public, &msg, &sig) {
            return Err(RegistryError::Key(KeyError::NotRegistered));
        }
        e.sig = sig;
        Ok(e)
    }

    /// Have the current root hand over to `new_public` (one owner press; the window
    /// shows old and new fingerprints, item 2). The caller writes a new file whose root is
    /// the new key with this as `prev`.
    pub fn sign_rotation(
        &self,
        dev: &dyn Device,
        new_public: [u8; PUBLIC_KEY_LEN],
        made_at: u64,
    ) -> Result<Rotation, RegistryError> {
        let action = format!(
            "換根鑰：舊 {} → 新 {} {}",
            fingerprint8(&self.root.public),
            fingerprint8(&new_public),
            utc_date(made_at)
        );
        let reason = self.reason(&action).map_err(RegistryError::Reason)?;
        let msg = rotate_message(&self.root.public, &new_public, made_at);
        dev.probe(&self.root.blob)
            .map_err(|w| RegistryError::Key(KeyError::Device(w)))?;
        let sig = dev
            .sign(&self.root.blob, &reason, &msg)
            .map_err(|w| RegistryError::Key(KeyError::Device(w)))?;
        if !self.verify.verify(&self.root.public, &msg, &sig) {
            return Err(RegistryError::Key(KeyError::NotRegistered));
        }
        Ok(Rotation {
            public: self.root.public,
            made_at: self.root.made_at,
            sig,
        })
    }

    /// Sign `binding` with `key`, the window composed by the core from `action` and the
    /// root line, checked against this registry.
    pub fn sign(
        &self,
        dev: &dyn Device,
        key: &crate::hwkey::KeyFile,
        binding: Binding,
        action: &str,
    ) -> Result<Proof, KeyError> {
        let reason = self.reason(action).map_err(KeyError::Reason)?;
        crate::hwkey::sign(dev, key, binding, &reason, self)
    }
}

/// Append a signed entry to a registry file (the file is the caller's to write).
pub fn append(file: &mut Value, e: &Entry) -> Result<(), RegistryError> {
    let list = file
        .get_mut("entries")
        .and_then(Value::as_array_mut)
        .ok_or(RegistryError::Corrupt("entries"))?;
    if list.len() >= ENTRIES_MAX {
        return Err(RegistryError::Corrupt("entries"));
    }
    list.push(e.to_json());
    Ok(())
}

impl TrustedKeys for Registry<'_> {
    fn check(
        &self,
        provider: &str,
        key_id: &str,
        alg: &str,
        msg: &[u8],
        sig: &[u8],
    ) -> Option<bool> {
        let (_, t) = self.keys.iter().find(|(k, _)| k == key_id)?;
        if t.provider != provider || t.alg != alg {
            return None;
        }
        Some(self.verify.verify(&t.public, msg, sig))
    }
}
