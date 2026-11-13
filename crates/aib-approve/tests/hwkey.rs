//! aib-approve hardware key flow (B2 T135-b slice 2) with a fake device, so it runs
//! everywhere. Each defence has a knife in scripts/knives-approve.sh (KW*).

use aib_approve::category::Category;
use aib_approve::hwkey::{
    self, Device, KeyError, KeyFile, KeyKind, ALG_ECDSA_P256_SHA256, PROVIDER_MACOS_SE,
    PUBLIC_KEY_LEN,
};
use aib_approve::proof::{
    self, parameter_digest, Binding, Evidence, MemoryNonces, Refusal, TrustedKeys,
};
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};

const PUBLIC: [u8; PUBLIC_KEY_LEN] = {
    let mut p = [7u8; PUBLIC_KEY_LEN];
    p[0] = 0x04;
    p
};

#[derive(Default)]
struct Fake {
    probe_err: Option<&'static str>,
    sign_err: Option<&'static str>,
    reported: Option<[u8; PUBLIC_KEY_LEN]>,
    made: RefCell<Vec<KeyKind>>,
    probes: Cell<u32>,
    signs: Cell<u32>,
}

impl Device for Fake {
    fn create(&self, kind: KeyKind) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), &'static str> {
        self.made.borrow_mut().push(kind);
        Ok((b"blob".to_vec(), PUBLIC))
    }
    fn probe(&self, _blob: &[u8]) -> Result<(), &'static str> {
        self.probes.set(self.probes.get() + 1);
        self.probe_err.map_or(Ok(()), Err)
    }
    fn public_key(&self, _blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
        Ok(self.reported.unwrap_or(PUBLIC))
    }
    fn sign(&self, _blob: &[u8], _reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
        self.signs.set(self.signs.get() + 1);
        match self.sign_err {
            Some(e) => Err(e),
            None => Ok(msg.iter().rev().copied().collect()),
        }
    }
}

/// Registered keys that accept exactly the fake's signature (reversed message) for
/// the fake public key, or answer `fixed` when set.
struct Registered {
    fixed: Option<Option<bool>>,
}

impl TrustedKeys for Registered {
    fn check(&self, p: &str, id: &str, alg: &str, msg: &[u8], sig: &[u8]) -> Option<bool> {
        if let Some(f) = self.fixed {
            return f;
        }
        if p != PROVIDER_MACOS_SE || alg != ALG_ECDSA_P256_SHA256 {
            return None;
        }
        if id != hwkey::key_id_of(&PUBLIC) {
            return None;
        }
        Some(msg.iter().rev().copied().collect::<Vec<u8>>() == sig)
    }
}

const GOOD: Registered = Registered { fixed: None };

fn binding() -> Binding {
    Binding {
        category: Category::Payment,
        actor: "ebb36".into(),
        operation: "payment.charge".into(),
        resource: "order-1".into(),
        parameter_digest: parameter_digest(b"amount=1"),
        nonce: "0123456789abcdef0123456789abcdef".into(),
        issued: 1_000,
        expiry: 1_060,
    }
}

fn key() -> KeyFile {
    hwkey::enroll(&Fake::default(), KeyKind::Signing).expect("enroll")
}

// ---------- key file ----------

#[test]
fn a_key_file_reads_back_and_refuses_anything_else() {
    let k = key();
    let v = k.to_json();
    assert_eq!(KeyFile::from_json(&v), Ok(k.clone()));
    let bad: [(&str, Value); 8] = [
        ("v", json!(1)),
        ("provider", json!("windows-tpm")),
        ("kind", json!("admin")),
        ("key_id", json!("00".repeat(16))),
        ("blob", json!("")),
        ("public", json!("04".repeat(64))),
        ("public", json!(format!("02{}", "07".repeat(64)))),
        ("extra", json!(1)),
    ];
    for (f, x) in bad {
        let mut w = v.clone();
        w[f] = x;
        assert!(KeyFile::from_json(&w).is_err(), "{f}");
    }
    let mut w = v.clone();
    w.as_object_mut().unwrap().remove("blob");
    assert!(KeyFile::from_json(&w).is_err());
}

#[test]
fn the_key_id_is_bound_to_the_public_key() {
    let k = key();
    assert_eq!(k.key_id, hwkey::key_id_of(&PUBLIC));
    assert_eq!(k.key_id.len(), 32);
    let mut v = k.to_json();
    let mut other = PUBLIC;
    other[1] = 8;
    v["public"] = json!(other.iter().map(|b| format!("{b:02x}")).collect::<String>());
    assert_eq!(KeyFile::from_json(&v), Err(KeyError::Corrupt("key_id")));
}

// ---------- enroll / load ----------

#[test]
fn a_key_that_does_not_ask_for_the_owner_is_never_kept() {
    let dev = Fake {
        probe_err: Some("weak_key"),
        ..Fake::default()
    };
    assert_eq!(
        hwkey::enroll(&dev, KeyKind::Signing),
        Err(KeyError::Device("weak_key"))
    );
    assert_eq!(dev.probes.get(), 1);
}

#[test]
fn only_the_grant_key_is_made_as_a_grant_key() {
    let dev = Fake::default();
    let s = hwkey::enroll(&dev, KeyKind::Signing).unwrap();
    let g = hwkey::enroll(&dev, KeyKind::Grant).unwrap();
    assert_eq!(*dev.made.borrow(), vec![KeyKind::Signing, KeyKind::Grant]);
    assert_eq!((s.kind, g.kind), (KeyKind::Signing, KeyKind::Grant));
}

#[test]
fn loading_refuses_a_key_the_device_reports_differently() {
    let v = key().to_json();
    assert!(hwkey::load(&Fake::default(), &v).is_ok());
    let mut other = PUBLIC;
    other[2] = 9;
    let dev = Fake {
        reported: Some(other),
        ..Fake::default()
    };
    assert_eq!(hwkey::load(&dev, &v), Err(KeyError::FormatChanged));
}

// ---------- sign ----------

#[test]
fn a_signature_the_registered_key_does_not_verify_never_becomes_a_proof() {
    let k = key();
    for fixed in [Some(false), None] {
        let r = Registered { fixed: Some(fixed) };
        assert_eq!(
            hwkey::sign(&Fake::default(), &k, binding(), "Pay order-1", &r),
            Err(KeyError::NotRegistered),
            "{fixed:?}"
        );
    }
}

#[test]
fn a_signed_proof_passes_the_policy_check_once() {
    let k = key();
    let p = hwkey::sign(&Fake::default(), &k, binding(), "Pay order-1", &GOOD).expect("sign");
    assert_eq!(p.provider, PROVIDER_MACOS_SE);
    assert_eq!(p.alg, ALG_ECDSA_P256_SHA256);
    assert_eq!(p.key_id, k.key_id);
    let mut nonces = MemoryNonces::default();
    assert_eq!(
        proof::verify(
            Evidence::Signed(p.clone()),
            &binding(),
            1_010,
            &GOOD,
            &mut nonces
        ),
        Ok(())
    );
    assert_eq!(
        proof::verify(Evidence::Signed(p), &binding(), 1_010, &GOOD, &mut nonces),
        Err(Refusal::Replay)
    );
}

#[test]
fn the_owner_check_runs_before_every_signature() {
    let k = key();
    let dev = Fake {
        probe_err: Some("weak_key"),
        ..Fake::default()
    };
    assert_eq!(
        hwkey::sign(&dev, &k, binding(), "Pay order-1", &GOOD),
        Err(KeyError::Device("weak_key"))
    );
    assert_eq!(dev.signs.get(), 0);
    let ok = Fake::default();
    hwkey::sign(&ok, &k, binding(), "Pay order-1", &GOOD).unwrap();
    assert_eq!((ok.probes.get(), ok.signs.get()), (1, 1));
}

#[test]
fn a_bad_reason_or_binding_is_refused_before_the_device_is_asked() {
    let k = key();
    let dev = Fake::default();
    for reason in ["", "Pay\u{202e}order", &"x".repeat(321)] {
        let e = hwkey::sign(&dev, &k, binding(), reason, &GOOD).unwrap_err();
        assert!(matches!(e, KeyError::Reason(_)), "{reason:?} {e:?}");
    }
    let mut b = binding();
    b.expiry = b.issued;
    assert_eq!(
        hwkey::sign(&dev, &k, b, "Pay order-1", &GOOD),
        Err(KeyError::Binding(Refusal::BadTime))
    );
    assert_eq!((dev.probes.get(), dev.signs.get()), (0, 0));
}

#[test]
fn a_cancel_is_a_refusal_not_a_proof() {
    let dev = Fake {
        sign_err: Some("cancel"),
        ..Fake::default()
    };
    let e = hwkey::sign(&dev, &key(), binding(), "Pay order-1", &GOOD).unwrap_err();
    assert_eq!(e, KeyError::Device("cancel"));
    assert_eq!(e.why(), "cancel");
}
