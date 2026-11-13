//! aib-approve policy tests (B2 T135-a). Each defence has a knife in
//! scripts/knives-approve.sh that must turn one of these red.

use aib_approve::category::{self, Category};
use aib_approve::proof::{
    self, parameter_digest, Binding, Evidence, MemoryNonces, NonceLedger, Proof, ProofLevel,
    Refusal, TrustedKeys,
};
use aib_approve::protocol::{self, HelperOutcome};
use aib_approve::text::{self, TextError};
use sha2::{Digest, Sha256};

// ---------- categories ----------

#[test]
fn high_risk_categories_never_get_a_window() {
    for c in category::ALL {
        if c.legacy_high_risk() {
            assert_eq!(c.legacy_max_window_s(), 0, "{}", c.id());
            assert_eq!(c.max_window_s(), 0, "{}", c.id());
            assert_eq!(category::window_allowed_s(c), 0, "{}", c.id());
        }
    }
    assert_eq!(
        category::ALL
            .iter()
            .filter(|c| c.legacy_high_risk())
            .count(),
        17
    );
}

#[test]
fn command_is_risk_3_in_2_0_and_loses_its_window() {
    assert_eq!(Category::Command.legacy_max_window_s(), 3 * 3600);
    assert_eq!(Category::Command.max_window_s(), 0);
    assert_eq!(category::window_allowed_s(Category::Command), 0);
    assert_eq!(category::window_allowed_s(Category::Outbound), 3 * 3600);
}

#[test]
fn no_window_is_longer_than_the_cap() {
    assert_eq!(Category::SeatInput.max_window_s(), 24 * 3600);
    assert_eq!(
        category::window_allowed_s(Category::SeatInput),
        category::WINDOW_CAP_S
    );
    for c in category::ALL {
        assert!(category::window_allowed_s(c) <= category::WINDOW_CAP_S);
    }
}

#[test]
fn an_unknown_category_is_unregistered_and_needs_a_hardware_signature() {
    for id in ["", "Payment", "payment ", "seat-input", "nope"] {
        assert_eq!(Category::parse(id), Category::Unregistered, "{id:?}");
    }
    assert_eq!(
        Category::Unregistered.required_proof(),
        ProofLevel::HardwareSigned
    );
    for c in category::ALL {
        assert_eq!(Category::parse(c.id()), c);
    }
}

#[test]
fn only_seat_input_outbound_and_command_accept_presence_only() {
    for c in category::ALL {
        let want = match c {
            Category::SeatInput | Category::Outbound | Category::Command => {
                ProofLevel::PresenceOnly
            }
            _ => ProofLevel::HardwareSigned,
        };
        assert_eq!(c.required_proof(), want, "{}", c.id());
    }
}

// ---------- text ----------

#[test]
fn invisible_and_control_characters_are_refused_not_cleaned() {
    let bad = [
        '\u{0}',
        '\u{1b}',
        '\n',
        '\u{7f}',
        '\u{85}',
        '\u{9f}',
        '\u{ad}',
        '\u{61c}',
        '\u{180e}',
        '\u{200b}',
        '\u{200f}',
        '\u{2028}',
        '\u{2029}',
        '\u{202a}',
        '\u{202e}',
        '\u{2060}',
        '\u{2069}',
        '\u{fe00}',
        '\u{fe0f}',
        '\u{feff}',
        '\u{e0000}',
        '\u{e0041}',
        '\u{e007f}',
        // 1.x consent extras and categories the first version missed (Codex T135-a H2)
        '\u{34f}',
        '\u{115f}',
        '\u{1160}',
        '\u{17b4}',
        '\u{180b}',
        '\u{206a}',
        '\u{206f}',
        '\u{2800}',
        '\u{3164}',
        '\u{ffa0}',
        '\u{e0100}',
        '\u{e01ef}',
        '\u{e000}',
        '\u{f8ff}',
        '\u{378}',
        '\u{e0080}',
        '\u{a0}',
        '\u{2007}',
        '\u{3000}',
    ];
    for c in bad {
        let s = format!("刪站 a{c}b");
        assert_eq!(
            text::check(&s, text::LA_MAX),
            Err(TextError::BadChar {
                index: 4,
                code: c as u32
            }),
            "U+{:04X}",
            c as u32
        );
    }
    for ok in ["刪站 example.com", "a-b_c.d/e:f", "é ñ 中文 😀", "a b"] {
        assert_eq!(text::check(ok, text::LA_MAX), Ok(ok));
    }
}

#[test]
fn too_long_is_refused_never_cut() {
    let at_max = "字".repeat(text::LA_MAX);
    assert_eq!(text::check(&at_max, text::LA_MAX), Ok(at_max.as_str()));
    let over = "字".repeat(text::LA_MAX + 1);
    assert_eq!(
        text::check(&over, text::LA_MAX),
        Err(TextError::TooLong {
            len: text::LA_MAX + 1,
            max: text::LA_MAX
        })
    );
    assert_eq!(text::check("  ", 10), Err(TextError::Empty));
    assert_eq!(text::TextError::Empty.detail(), "no_text");
    assert_eq!(
        (
            text::FULL_MAX,
            text::SE_SIGN_MAX,
            text::UNSEAL_MAX,
            text::LA_MAX
        ),
        (320, 320, 200, 160)
    );
}

#[test]
fn the_unicode_table_is_pinned() {
    assert_eq!(text::UNICODE_VERSION, (16, 0, 0));
}

// ---------- proof ----------

/// Test key list: key `k1` of provider `test-hw` signs with sha256(b"k1" + msg).
struct TestKeys;
impl TrustedKeys for TestKeys {
    fn check(
        &self,
        provider: &str,
        key_id: &str,
        alg: &str,
        msg: &[u8],
        sig: &[u8],
    ) -> Option<bool> {
        if provider != "test-hw" || key_id != "k1" || alg != "test-sha256" {
            return None;
        }
        Some(sign(msg) == sig)
    }
}

fn sign(msg: &[u8]) -> Vec<u8> {
    let mut h = Sha256::new();
    h.update(b"k1");
    h.update(msg);
    h.finalize().to_vec()
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

fn signed(b: Binding) -> Proof {
    let sig = sign(&b.canonical().expect("canonical"));
    Proof {
        provider: "test-hw".into(),
        key_id: "k1".into(),
        alg: "test-sha256".into(),
        binding: b,
        signature: sig,
    }
}

#[test]
fn a_signed_proof_for_exactly_this_action_is_accepted_once() {
    let b = binding(Category::DeleteData);
    let p = signed(b.clone());
    let mut n = MemoryNonces::default();
    assert_eq!(
        proof::verify(Evidence::Signed(p.clone()), &b, NOW, &TestKeys, &mut n),
        Ok(())
    );
    assert_eq!(
        proof::verify(Evidence::Signed(p.clone()), &b, NOW, &TestKeys, &mut n),
        Err(Refusal::Replay)
    );
}

#[test]
fn a_refused_proof_does_not_use_up_the_nonce() {
    let b = binding(Category::DeleteData);
    let mut bad = signed(b.clone());
    bad.signature = vec![0; 32];
    let mut n = MemoryNonces::default();
    assert_eq!(
        proof::verify(Evidence::Signed(bad.clone()), &b, NOW, &TestKeys, &mut n),
        Err(Refusal::BadSig)
    );
    assert_eq!(
        proof::verify(
            Evidence::Signed(signed(b.clone())),
            &b,
            NOW,
            &TestKeys,
            &mut n
        ),
        Ok(())
    );
}

#[test]
fn a_proof_for_anything_else_is_refused() {
    let want = binding(Category::DeleteData);
    let cases: Vec<(&str, Box<dyn Fn(&mut Binding)>)> = vec![
        ("category", Box::new(|b| b.category = Category::Payment)),
        ("actor", Box::new(|b| b.actor = "f457/c477a".into())),
        (
            "operation",
            Box::new(|b| b.operation = "consent.stop".into()),
        ),
        (
            "resource",
            Box::new(|b| b.resource = "host:/srv/other".into()),
        ),
        (
            "parameter_digest",
            Box::new(|b| b.parameter_digest = parameter_digest(b"DRY=1\n")),
        ),
        ("nonce", Box::new(|b| b.nonce = "f".repeat(32))),
        ("issued", Box::new(|b| b.issued -= 1)),
        ("expiry", Box::new(|b| b.expiry += 3600)),
    ];
    for (field, change) in cases {
        let mut other = want.clone();
        change(&mut other);
        let p = signed(other);
        let mut n = MemoryNonces::default();
        assert_eq!(
            proof::verify(Evidence::Signed(p.clone()), &want, NOW, &TestKeys, &mut n),
            Err(Refusal::Mismatch(field)),
            "{field}"
        );
    }
}

#[test]
fn expired_and_future_proofs_are_refused() {
    let b = binding(Category::DeleteData);
    let p = signed(b.clone());
    let mut n = MemoryNonces::default();
    assert_eq!(
        proof::verify(Evidence::Signed(p.clone()), &b, b.expiry, &TestKeys, &mut n),
        Err(Refusal::Expired)
    );
    let mut f = binding(Category::DeleteData);
    f.issued = NOW + proof::ISSUE_SKEW_S + 1;
    f.expiry = NOW + 600;
    assert_eq!(
        proof::verify(
            Evidence::Signed(signed(f.clone())),
            &f,
            NOW,
            &TestKeys,
            &mut n
        ),
        Err(Refusal::NotYet)
    );
    let mut back = binding(Category::DeleteData);
    back.expiry = back.issued;
    assert_eq!(back.canonical(), Err(Refusal::BadTime));
}

#[test]
fn hardware_signed_needs_a_signature_by_a_trusted_key() {
    let b = binding(Category::Payment);
    let mut n = MemoryNonces::default();
    let mut none = signed(b.clone());
    none.signature = Vec::new();
    assert_eq!(
        proof::verify(Evidence::Signed(none.clone()), &b, NOW, &TestKeys, &mut n),
        Err(Refusal::NoSignature)
    );
    let mut other = signed(b.clone());
    other.key_id = "k2".into();
    assert_eq!(
        proof::verify(Evidence::Signed(other.clone()), &b, NOW, &TestKeys, &mut n),
        Err(Refusal::UntrustedKey)
    );
    assert_eq!(
        proof::verify(
            Evidence::Signed(signed(b.clone())),
            &b,
            NOW,
            &TestKeys,
            &mut n
        ),
        Ok(())
    );
}

#[test]
fn canonical_form_has_one_reading() {
    let b = binding(Category::DeleteData);
    let c = String::from_utf8(b.canonical().expect("canonical")).expect("utf8");
    assert!(c.starts_with("aib-approve/binding/v2\ncategory=delete_data\nactor=f457/ebb36\n"));
    assert_eq!(c.lines().count(), 9);
    for (field, v) in [
        ("actor", "a\nnonce=x"),
        ("operation", "a=b"),
        ("resource", ""),
        ("resource", "a\u{202e}b"),
    ] {
        let mut x = b.clone();
        match field {
            "actor" => x.actor = v.into(),
            "operation" => x.operation = v.into(),
            _ => x.resource = v.into(),
        }
        assert_eq!(x.canonical(), Err(Refusal::BadField(field)), "{v:?}");
    }
    let mut d = b.clone();
    d.parameter_digest = d.parameter_digest.to_uppercase();
    assert_eq!(d.canonical(), Err(Refusal::BadField("parameter_digest")));
    let mut short = b;
    short.nonce = "abc".into();
    assert_eq!(short.canonical(), Err(Refusal::BadField("nonce")));
}

fn json_of(p: &Proof, level: &str) -> serde_json::Value {
    let b = &p.binding;
    let hex: String = p.signature.iter().map(|x| format!("{x:02x}")).collect();
    serde_json::json!({
        "v": proof::BINDING_VERSION, "level": level, "provider": p.provider, "key_id": p.key_id,
        "alg": p.alg, "category": b.category.id(), "actor": b.actor, "operation": b.operation,
        "resource": b.resource, "parameter_digest": b.parameter_digest, "nonce": b.nonce,
        "issued": b.issued, "expiry": b.expiry, "signature": hex,
    })
}

#[test]
fn a_presence_only_proof_cannot_arrive_from_outside() {
    let p = signed(binding(Category::Command));
    assert_eq!(
        Proof::from_json(&json_of(&p, "presence_only")),
        Err(Refusal::PresenceNotTransferable)
    );
    assert_eq!(
        Proof::from_json(&json_of(&p, "hardware_signed")),
        Ok(p.clone())
    );
    let mut old = json_of(&p, "hardware_signed");
    old["v"] = "aib-approve/binding/v1".into();
    assert_eq!(Proof::from_json(&old), Err(Refusal::Corrupt("v")));
    let mut cat = json_of(&p, "hardware_signed");
    cat["category"] = "nope".into();
    assert_eq!(Proof::from_json(&cat), Err(Refusal::Corrupt("category")));
}

#[test]
fn memory_nonces_say_yes_once() {
    let mut n = MemoryNonces::default();
    assert!(n.consume("ab"));
    assert!(!n.consume("ab"));
    assert!(n.consume("ac"));
}

// ---------- helper protocol ----------

#[test]
fn helper_exit_codes_map_to_classes_and_mismatches_are_broken() {
    use HelperOutcome::*;
    assert_eq!(HelperOutcome::from_exit(Some(0), ""), Ok);
    assert_eq!(
        HelperOutcome::from_exit(Some(3), "cancel\n"),
        NotApproved("cancel".into())
    );
    assert_eq!(
        HelperOutcome::from_exit(Some(3), "no_biometry"),
        NotApproved("no_biometry".into())
    );
    assert_eq!(
        HelperOutcome::from_exit(Some(2), "reason_too_long\n"),
        Refused("reason_too_long".into())
    );
    assert_eq!(
        HelperOutcome::from_exit(Some(5), "bad_sig\n"),
        Broken("bad_sig".into())
    );
    assert_eq!(
        HelperOutcome::from_exit(Some(6), "weak_key\n"),
        Broken("weak_key".into())
    );
    assert_eq!(
        HelperOutcome::from_exit(Some(4), "se_error\n"),
        Broken("se_error".into())
    );
    for (rc, err) in [
        (Some(3), "weak_key"),
        (Some(2), "cancel"),
        (Some(6), "cancel"),
        (Some(1), "cancel"),
        (Some(3), ""),
        (None, "cancel"),
        (Some(137), ""),
    ] {
        assert_eq!(
            HelperOutcome::from_exit(rc, err),
            Broken("helper_failed".into()),
            "{rc:?} {err:?}"
        );
    }
    assert!(!HelperOutcome::from_exit(Some(3), "cancel").is_approved());
}

#[test]
fn legacy_words_are_kept_verbatim() {
    assert_eq!(protocol::la_why("-2"), "cancel");
    assert_eq!(protocol::la_why("-9"), "timeout");
    assert_eq!(protocol::la_why("-7"), "failed:-7");
    assert_eq!(protocol::la_why("throw"), "failed:throw");
    assert_eq!(protocol::CONSENT_RESULTS.len(), 10);
    assert_eq!(protocol::consent_result_for_not_approved(), "denied");
}
