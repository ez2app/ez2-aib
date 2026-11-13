//! Owner-present acceptance for the Secure Enclave path (B2 T135-b, PLAN-t135b §「還沒證」
//! + DECISION-hardware-signed-registry). Every test here is `#[ignore]`: it needs a Mac
//! with Touch ID, the lid open, the owner's own GUI session (not ssh), and the owner at
//! the keyboard. Run one at a time so each window is read before it is pressed:
//!
//! ```text
//! cargo test -p aib-approve --test se_owner_acceptance -- --ignored --nocapture --test-threads 1 <name>
//! ```
//!
//! Each test prints what the owner should see and press. Nothing here writes outside
//! a fresh folder under the temp dir, and nothing here becomes the real registry: where
//! the real registry and key files live is decided in T136.

#![cfg(target_os = "macos")]

use aib_approve::category::Category;
use aib_approve::hwkey::{self, Device, KeyKind};
use aib_approve::macos::{MacosSe, RegisteredSeKeys, SeVerify};
use aib_approve::proof::{parameter_digest, Binding, TrustedKeys};
use aib_approve::registry::{self, fingerprint8, Op, Registry, Root};
use aib_sys::presence_macos as se;
use serde_json::Value;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock after 1970")
        .as_secs()
}

fn say(step: &str, what: &str) {
    eprintln!("\n==== {step} ====\n{what}\n");
}

fn binding(resource: &str) -> Binding {
    let t = now();
    Binding {
        category: Category::Payment,
        actor: "acceptance".into(),
        operation: "payment.charge".into(),
        resource: resource.into(),
        parameter_digest: parameter_digest(b"amount=1"),
        nonce: format!(
            "{:032x}",
            (u128::from(t) << 20) | u128::from(std::process::id())
        ),
        issued: t,
        expiry: t + 60,
    }
}

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aib-se-accept-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&d).expect("scratch dir");
    d
}

// ---------- A1 Pro 16 單支:see macos::tests::a_new_signing_key_asks_for_the_owner_and_loads_back ----------

/// A2 真簽 + 視窗字:the window shows the core-composed text, the proof verifies under
/// the registered public key.
#[test]
#[ignore = "owner present: one Touch ID press; check the window text"]
fn a2_a_real_signature_shows_the_text_and_verifies() {
    let k = hwkey::enroll(&MacosSe, KeyKind::Signing).expect("enroll");
    let mut keys = RegisteredSeKeys::new();
    keys.add(k.public);
    let reason = "驗收 A2:付款 order-a2 金額 1";
    say(
        "A2",
        &format!("視窗要出現「{reason}」(系統可能前後加字)。內容對才按指紋;截圖留給審查。"),
    );
    let p = hwkey::sign(&MacosSe, &k, binding("order-a2"), reason, &keys).expect("sign");
    let msg = p.binding.canonical().expect("canonical");
    assert_eq!(
        keys.check(&p.provider, &p.key_id, &p.alg, &msg, &p.signature),
        Some(true)
    );
}

/// A3 取消碼:pressing Cancel comes back as `cancel`, never as a signature.
#[test]
#[ignore = "owner present: press Cancel in the window"]
fn a3_cancel_comes_back_as_cancel() {
    let k = hwkey::enroll(&MacosSe, KeyKind::Signing).expect("enroll");
    say("A3", "視窗出現後請按「取消」,不要按指紋。");
    let r = MacosSe.sign(&k.blob, "驗收 A3:請按取消", b"a3");
    assert_eq!(r, Err("cancel"));
}

/// A4 試簽兩次(第二次帶隨機 applicationPassword):a real owner key passes the probe
/// with no window, for both ACLs. No press; still needs the GUI session.
#[test]
#[ignore = "owner's GUI session, lid open; no press expected (a window here is a failure)"]
fn a4_the_probe_passes_real_owner_keys_without_a_window() {
    say("A4", "這項不應該跳任何視窗。跳了就按取消,並記下來。");
    for acl in [se::Acl::BiometryCurrentSet, se::Acl::UserPresence] {
        let (blob, _) = se::create(acl).expect("create");
        assert_eq!(se::probe(&blob), Ok(()), "{acl:?}");
        assert_eq!(se::probe(&blob), Ok(()), "{acl:?} second time");
    }
}

/// A5 userPresence:the grant key accepts the login password instead of Touch ID.
#[test]
#[ignore = "owner present: use the login password, not Touch ID"]
fn a5_a_grant_key_takes_the_login_password() {
    let k = hwkey::enroll(&MacosSe, KeyKind::Grant).expect("enroll");
    say("A5", "視窗出現後請改用「登入密碼」(不要按指紋)。");
    let msg = b"aib-approve acceptance a5";
    let sig = MacosSe
        .sign(&k.blob, "驗收 A5:這次請用登入密碼", msg)
        .expect("sign");
    assert_eq!(se::verify(&k.public, msg, &sig), Ok(true));
}

/// A6 1.x `.sekey` 相容:a 1.x key file (aib-sehelper se-create / grant-create) gives the
/// same public key through aib-sys, passes the probe and signs. Read only; the file is
/// not changed. Path in `AIB_SEKEY_1X` (e.g. the 1.x GRANT_KEY.sekey under secrets/se).
#[test]
#[ignore = "owner present: one press; needs AIB_SEKEY_1X=<path to a 1.x .sekey>"]
fn a6_a_1x_sekey_still_works() {
    let path = std::env::var("AIB_SEKEY_1X").expect("set AIB_SEKEY_1X");
    let doc: Value =
        serde_json::from_slice(&std::fs::read(&path).expect("read .sekey")).expect("json");
    let blob = b64(doc["se_key"].as_str().expect("se_key"));
    let public = b64(doc["public_key"].as_str().expect("public_key"));
    let reported = se::public_key(&blob).expect("public_key");
    assert_eq!(reported.as_slice(), public.as_slice(), "1.x public key");
    assert_eq!(se::probe(&blob), Ok(()), "1.x key keeps its owner ACL");
    say(
        "A6",
        &format!("1.x 鑰 {path}:視窗出現就按(GRANT_KEY 可用密碼)。"),
    );
    let msg = b"aib-approve acceptance a6";
    let sig = se::sign(&blob, "驗收 A6:1.x 鑰還能用", msg).expect("sign");
    assert_eq!(se::verify(&reported, msg, &sig), Ok(true));
}

/// A7 換指紋失效,part 1:make a Touch ID key and keep it in `AIB_SE_ACCEPT_DIR`.
/// Then the owner adds (or removes) a finger in System Settings and runs part 2.
/// Optional: it changes the owner's Touch ID set.
#[test]
#[ignore = "owner present, optional: changes the Touch ID finger set between parts"]
fn a7a_keep_a_key_before_the_fingers_change() {
    let dir = PathBuf::from(std::env::var("AIB_SE_ACCEPT_DIR").expect("set AIB_SE_ACCEPT_DIR"));
    let k = hwkey::enroll(&MacosSe, KeyKind::Signing).expect("enroll");
    let f = dir.join("a7-signing.json");
    std::fs::write(&f, k.to_json().to_string()).expect("write");
    say(
        "A7 第 1 段",
        &format!(
            "鑰存在 {}。請到「系統設定 → Touch ID」加一根手指,再跑 a7b。",
            f.display()
        ),
    );
}

/// A7 part 2:the kept key must no longer sign (biometryCurrentSet voids it).
#[test]
#[ignore = "owner present, optional: run after a7a and a Touch ID finger change"]
fn a7b_after_the_fingers_change_the_old_key_does_not_sign() {
    let dir = PathBuf::from(std::env::var("AIB_SE_ACCEPT_DIR").expect("set AIB_SE_ACCEPT_DIR"));
    let v: Value =
        serde_json::from_slice(&std::fs::read(dir.join("a7-signing.json")).expect("read"))
            .expect("json");
    let k = hwkey::KeyFile::from_json(&v).expect("key file");
    say("A7 第 2 段", "若跳出視窗就按指紋;預期簽不出來。");
    let r = MacosSe.sign(&k.blob, "驗收 A7:換過手指的舊鑰", b"a7");
    assert!(
        r.is_err(),
        "a key from before the finger change still signed"
    );
    eprintln!("A7 結果碼:{r:?}");
}

/// A8 + A9 建根鑰 + 第一次登記 + 用登記的鑰簽:the whole registry path on the real
/// Secure Enclave. Three presses: the entry, the proof, and nothing for loading.
#[test]
#[ignore = "owner present: two Touch ID presses; note the root fingerprint shown"]
fn a8_a9_a_new_root_registers_a_key_that_then_signs() {
    let dir = scratch("registry");
    let made = now();
    let rk = hwkey::enroll(&MacosSe, KeyKind::RegistryRoot).expect("root");
    let root = Root {
        blob: rk.blob,
        public: rk.public,
        key_id: rk.key_id,
        made_at: made,
        prev: None,
    };
    let mut file = registry::new_file(&root);
    let verify = SeVerify;
    let reg = Registry::load(&file, &MacosSe, &verify).expect("load empty");
    say(
        "A8 建根鑰",
        &format!(
            "根鑰指紋 {} 建立日 {}。請記下來,下兩個視窗都要出現這一行。",
            fingerprint8(&root.public),
            registry::utc_date(made)
        ),
    );
    let sk = hwkey::enroll(&MacosSe, KeyKind::Signing).expect("signing key");
    say(
        "A9 第一次登記",
        &format!(
            "視窗第一行是根鑰行,後面是「登記：多了 … 指紋 {}」。對才按。",
            fingerprint8(&sk.public)
        ),
    );
    let e = reg
        .sign_entry(&MacosSe, Op::Add, KeyKind::Signing, sk.public, now())
        .expect("sign entry");
    registry::append(&mut file, &e).expect("append");
    std::fs::write(dir.join("registry.json"), file.to_string()).expect("write");
    let reg = Registry::load(&file, &MacosSe, &verify).expect("reload");
    assert_eq!(reg.applied(), 1);
    assert_eq!(reg.skipped, 0);
    say(
        "A9 用登記的鑰簽",
        "視窗第一行同一個根鑰行,後面是「付款 order-a9」。對才按。",
    );
    let p = reg
        .sign(&MacosSe, &sk, binding("order-a9"), "付款 order-a9")
        .expect("sign with registered key");
    assert_eq!(p.key_id, sk.key_id);
    eprintln!("登記清單留在 {}(驗收用,不是正式清單)", dir.display());
}

fn b64(s: &str) -> Vec<u8> {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0u32);
    for c in s.bytes().filter(|&c| c != b'=') {
        let v = A.iter().position(|&a| a == c).expect("base64 char") as u32;
        acc = acc << 6 | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    out
}

#[test]
fn b64_reads_standard_base64() {
    assert_eq!(b64("YWli"), b"aib");
    assert_eq!(b64("YWlidg=="), b"aibv");
    assert_eq!(b64("AAEC/w=="), [0, 1, 2, 255]);
}
