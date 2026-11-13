//! Trusted-key registry (B2 T135-b slice 3, DECISION-hardware-signed-registry 1–8) with
//! a fake device and a fake signature scheme, so it runs everywhere. Each defence has
//! a knife (KR*) in scripts/knives-approve.sh.

use aib_approve::category::Category;
use aib_approve::hwkey::{self, Device, KeyError, KeyKind, PUBLIC_KEY_LEN};
use aib_approve::proof::{self, parameter_digest, Binding, Evidence, MemoryNonces};
use aib_approve::registry::{
    self, fingerprint8, Op, Registry, RegistryError, Root, Rotation, SigVerify,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::cell::{Cell, RefCell};

fn public(n: u8) -> [u8; PUBLIC_KEY_LEN] {
    let mut p = [n; PUBLIC_KEY_LEN];
    p[0] = 0x04;
    p
}

/// Fake signatures: sha256(public || msg). Not secure; only shape and wiring are tested.
fn fake_sig(public: &[u8; PUBLIC_KEY_LEN], msg: &[u8]) -> Vec<u8> {
    let mut h = Sha256::new();
    h.update(public);
    h.update(msg);
    h.finalize().to_vec()
}

struct FakeVerify;
impl SigVerify for FakeVerify {
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
        fake_sig(public, msg) == sig
    }
}

/// A device whose blob is one byte: the fill of its public key.
#[derive(Default)]
struct Dev {
    weak: Vec<u8>,
    reported_other: bool,
    reasons: RefCell<Vec<String>>,
    probes: Cell<u32>,
}

impl Device for Dev {
    fn create(&self, _k: KeyKind) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), &'static str> {
        Ok((vec![9], public(9)))
    }
    fn probe(&self, blob: &[u8]) -> Result<(), &'static str> {
        self.probes.set(self.probes.get() + 1);
        if self.weak.contains(&blob[0]) {
            Err("weak_key")
        } else {
            Ok(())
        }
    }
    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
        Ok(public(blob[0].wrapping_add(u8::from(self.reported_other))))
    }
    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
        self.reasons.borrow_mut().push(reason.to_owned());
        Ok(fake_sig(&public(blob[0]), msg))
    }
}

const MADE: u64 = 1_794_528_000; // 2026-11-13

fn root(n: u8) -> Root {
    Root {
        blob: vec![n],
        public: public(n),
        key_id: hwkey::key_id_of(&public(n)),
        made_at: MADE,
        prev: None,
    }
}

/// A registry file under root 1 with adds for keys 20 and 21.
fn file_with_two() -> Value {
    let dev = Dev::default();
    let mut f = registry::new_file(&root(1));
    for k in [20, 21] {
        let r = Registry::load(&f, &dev, &FakeVerify).expect("load");
        let e = r
            .sign_entry(&dev, Op::Add, KeyKind::Signing, public(k), MADE)
            .expect("entry");
        registry::append(&mut f, &e).expect("append");
    }
    f
}

fn ids(r: &Registry) -> Vec<String> {
    r.trusted().map(|(k, _)| k.to_owned()).collect()
}

// ---------- entries ----------

#[test]
fn signed_entries_count_and_a_bad_signature_does_not_exist() {
    let f = file_with_two();
    let r = Registry::load(&f, &Dev::default(), &FakeVerify).unwrap();
    assert_eq!(ids(&r).len(), 2);
    assert_eq!((r.applied(), r.skipped), (2, 0));

    let mut g = f.clone();
    g["entries"][1]["sig"] = json!("00".repeat(32));
    let r = Registry::load(&g, &Dev::default(), &FakeVerify).unwrap();
    assert_eq!(ids(&r), vec![hwkey::key_id_of(&public(20))]);
    assert_eq!(r.skipped, 1);
}

#[test]
fn an_unsigned_new_entry_is_not_trusted() {
    let mut f = file_with_two();
    let mut forged = f["entries"][1].clone();
    forged["seq"] = json!(3);
    forged["public"] = json!(hex(&public(66)));
    f["entries"].as_array_mut().unwrap().push(forged);
    let r = Registry::load(&f, &Dev::default(), &FakeVerify).unwrap();
    assert!(!ids(&r).contains(&hwkey::key_id_of(&public(66))));
    assert_eq!(r.skipped, 1);
}

#[test]
fn an_old_entry_replayed_out_of_order_does_not_count() {
    let dev = Dev::default();
    let mut f = file_with_two();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    let rev = r
        .sign_entry(&dev, Op::Revoke, KeyKind::Signing, public(20), MADE)
        .unwrap();
    registry::append(&mut f, &rev).unwrap();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    assert_eq!(ids(&r), vec![hwkey::key_id_of(&public(21))]);
    // the old signed add for key 20 copied to the end: still signed, but out of order
    let old = f["entries"][0].clone();
    f["entries"].as_array_mut().unwrap().push(old);
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    assert_eq!(ids(&r), vec![hwkey::key_id_of(&public(21))]);
    assert_eq!(r.skipped, 1);
}

#[test]
fn a_revoke_removes_the_key() {
    let dev = Dev::default();
    let mut f = file_with_two();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    let rev = r
        .sign_entry(&dev, Op::Revoke, KeyKind::Signing, public(21), MADE)
        .unwrap();
    registry::append(&mut f, &rev).unwrap();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    assert_eq!(ids(&r), vec![hwkey::key_id_of(&public(20))]);
}

// ---------- root ----------

#[test]
fn a_root_that_signs_without_the_owner_voids_everything() {
    let f = file_with_two();
    let dev = Dev {
        weak: vec![1],
        ..Dev::default()
    };
    assert!(matches!(
        Registry::load(&f, &dev, &FakeVerify),
        Err(RegistryError::RootWeak)
    ));
    assert_eq!(dev.probes.get(), 1);
}

#[test]
fn a_root_the_device_reports_differently_voids_everything() {
    let dev = Dev {
        reported_other: true,
        ..Dev::default()
    };
    assert!(matches!(
        Registry::load(&file_with_two(), &dev, &FakeVerify),
        Err(RegistryError::RootChanged)
    ));
}

#[test]
fn a_new_root_needs_the_old_roots_signature() {
    let dev = Dev::default();
    let old = Registry::load(&file_with_two(), &dev, &FakeVerify).unwrap();
    let rot = old.sign_rotation(&dev, public(2), MADE + 86_400).unwrap();
    let mut new_root = root(2);
    new_root.made_at = MADE + 86_400;
    new_root.prev = Some(rot.clone());
    let f = registry::new_file(&new_root);
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    assert!(r
        .window_line()
        .contains(&format!("換自 {}", fingerprint8(&public(1)))));
    assert!(r.window_line().contains(&fingerprint8(&public(2))));
    let reason = dev.reasons.borrow().last().cloned().unwrap();
    assert!(
        reason.contains(&fingerprint8(&public(1))) && reason.contains(&fingerprint8(&public(2)))
    );

    new_root.prev = Some(Rotation {
        sig: vec![0; 32],
        ..rot
    });
    assert!(matches!(
        Registry::load(&registry::new_file(&new_root), &dev, &FakeVerify),
        Err(RegistryError::RotationUnsigned)
    ));
}

#[test]
fn the_registry_file_is_read_strictly() {
    let f = file_with_two();
    let dev = Dev::default();
    for (path, val) in [
        ("/v", json!(2)),
        ("/root/key_id", json!("00".repeat(16))),
        ("/root/public", json!(format!("02{}", "01".repeat(64)))),
        ("/root/extra", json!(1)),
    ] {
        let mut g = f.clone();
        let (parent, key) = path.rsplit_once('/').unwrap();
        let obj = if parent.is_empty() {
            &mut g
        } else {
            g.pointer_mut(parent).unwrap()
        };
        obj[key] = val;
        assert!(Registry::load(&g, &dev, &FakeVerify).is_err(), "{path}");
    }
}

// ---------- windows ----------

#[test]
fn every_window_carries_the_root_fingerprint_and_date() {
    let dev = Dev::default();
    let f = file_with_two();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    let line = format!("根鑰 {} 2026-11-13 · 登記 2 把", fingerprint8(&public(1)));
    assert_eq!(r.window_line(), line);
    for reason in dev.reasons.borrow().iter() {
        assert!(reason.contains(&fingerprint8(&public(1))), "{reason}");
    }
    let key = hwkey::KeyFile {
        kind: KeyKind::Signing,
        key_id: hwkey::key_id_of(&public(20)),
        blob: vec![20],
        public: public(20),
    };
    let b = binding();
    let p = r.sign(&dev, &key, b.clone(), "付款 order-1").unwrap();
    assert_eq!(
        dev.reasons.borrow().last().unwrap(),
        &format!("{line} · 付款 order-1")
    );
    let mut nonces = MemoryNonces::default();
    assert_eq!(
        proof::verify(Evidence::Signed(p), &b, 1_010, &r, &mut nonces),
        Ok(())
    );
}

#[test]
fn a_key_not_in_the_registry_cannot_sign() {
    let dev = Dev::default();
    let r = Registry::load(&file_with_two(), &dev, &FakeVerify).unwrap();
    let key = hwkey::KeyFile {
        kind: KeyKind::Signing,
        key_id: hwkey::key_id_of(&public(77)),
        blob: vec![77],
        public: public(77),
    };
    assert_eq!(
        r.sign(&dev, &key, binding(), "付款 order-1"),
        Err(KeyError::NotRegistered)
    );
}

#[test]
fn dates_are_utc_calendar_days() {
    assert_eq!(registry::utc_date(0), "1970-01-01");
    assert_eq!(registry::utc_date(MADE), "2026-11-13");
    assert_eq!(registry::utc_date(951_782_400), "2000-02-29");
    assert_eq!(registry::utc_date(4_107_542_399), "2100-02-28");
}

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

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

// ---------- roles (c477a slice 3 L1, N1, N3) ----------

fn key_file(n: u8, kind: KeyKind) -> hwkey::KeyFile {
    hwkey::KeyFile {
        kind,
        key_id: hwkey::key_id_of(&public(n)),
        blob: vec![n],
        public: public(n),
    }
}

#[test]
fn a_grant_key_cannot_sign_an_approval_proof() {
    let dev = Dev::default();
    let mut f = file_with_two();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    let e = r
        .sign_entry(&dev, Op::Add, KeyKind::Grant, public(22), MADE)
        .unwrap();
    registry::append(&mut f, &e).unwrap();
    let r = Registry::load(&f, &dev, &FakeVerify).unwrap();
    let grant = key_file(22, KeyKind::Grant);
    assert_eq!(
        r.sign(&dev, &grant, binding(), "付款 order-1"),
        Err(KeyError::NotRegistered)
    );
    let msg = b"window grant";
    let sig = dev.sign(&[22], "x", msg).unwrap();
    use aib_approve::hwkey::{ALG_ECDSA_P256_SHA256, PROVIDER_MACOS_SE};
    assert_eq!(
        r.check_kind(
            KeyKind::Grant,
            PROVIDER_MACOS_SE,
            &grant.key_id,
            ALG_ECDSA_P256_SHA256,
            msg,
            &sig
        ),
        Some(true)
    );
    assert_eq!(
        r.check_kind(
            KeyKind::Signing,
            PROVIDER_MACOS_SE,
            &grant.key_id,
            ALG_ECDSA_P256_SHA256,
            msg,
            &sig
        ),
        None
    );
}

#[test]
fn the_root_is_never_a_trusted_key() {
    let mut f = file_with_two();
    let rk = root(1);
    for (seq, kind, p) in [
        (3, KeyKind::RegistryRoot, public(30)),
        (4, KeyKind::Signing, public(1)),
    ] {
        let mut e = registry::Entry {
            seq,
            op: Op::Add,
            provider: hwkey::PROVIDER_MACOS_SE.into(),
            alg: hwkey::ALG_ECDSA_P256_SHA256.into(),
            kind,
            public: p,
            at: MADE,
            sig: Vec::new(),
        };
        e.sig = fake_sig(&rk.public, &registry::entry_message(&rk.key_id, &e));
        registry::append(&mut f, &e).unwrap();
    }
    let r = Registry::load(&f, &Dev::default(), &FakeVerify).unwrap();
    assert_eq!(ids(&r).len(), 2);
    assert_eq!(r.skipped, 2);
}

#[test]
fn an_entry_that_would_be_dropped_is_refused_before_the_owner_is_asked() {
    let dev = Dev::default();
    let r = Registry::load(&file_with_two(), &dev, &FakeVerify).unwrap();
    let cases = [
        (Op::Add, KeyKind::Signing, public(20), "already_trusted"),
        (Op::Revoke, KeyKind::Signing, public(77), "not_trusted"),
        (Op::Add, KeyKind::RegistryRoot, public(31), "root_not_entry"),
        (Op::Add, KeyKind::Signing, public(1), "root_not_entry"),
    ];
    for (op, kind, p, why) in cases {
        let e = r.sign_entry(&dev, op, kind, p, MADE).unwrap_err();
        assert_eq!(e.why(), why);
    }
    assert!(dev.reasons.borrow().is_empty());
}

#[test]
fn the_real_root_line_comes_before_the_callers_text() {
    let r = Registry::load(&file_with_two(), &Dev::default(), &FakeVerify).unwrap();
    let fake = "根鑰 abcd1234 2026-01-01 · 登記 9 把 · 付款";
    let reason = r.reason(fake).unwrap();
    assert!(reason.starts_with(&r.window_line()), "{reason}");
}
