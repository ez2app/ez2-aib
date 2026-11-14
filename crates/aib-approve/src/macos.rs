//! macOS side of [`crate::hwkey`]: the Secure Enclave device and the registered-key
//! check, both through aib-sys `presence_macos` (DECISION-b2-windows-native W-a,
//! DECISION-t135b-objc2-deps). No unsafe here; aib-sys holds it.

use crate::hwkey::{Device, KeyKind, ALG_ECDSA_P256_SHA256, PROVIDER_MACOS_SE, PUBLIC_KEY_LEN};
use crate::proof::TrustedKeys;
use aib_sys::presence_macos as se;

/// The Secure Enclave of this Mac. Making and using an owner key only works in the
/// owner's own logged-in GUI session (over ssh: `se_error`).
#[derive(Clone, Copy, Debug, Default)]
pub struct MacosSe;

impl Device for MacosSe {
    fn create(&self, kind: KeyKind) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), &'static str> {
        let acl = match kind {
            KeyKind::Signing | KeyKind::RegistryRoot => se::Acl::BiometryCurrentSet,
            KeyKind::Grant => se::Acl::UserPresence,
        };
        se::create(acl).map_err(|e| e.code())
    }

    fn probe(&self, blob: &[u8]) -> Result<(), &'static str> {
        se::probe(blob).map_err(|e| e.code())
    }

    fn public_key(&self, blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], &'static str> {
        se::public_key(blob).map_err(|e| e.code())
    }

    fn sign(&self, blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, &'static str> {
        se::sign(blob, reason, msg).map_err(|e| e.code())
    }
}

/// ECDSA P-256 / SHA-256 / DER check through aib-sys, for [`crate::registry`].
#[derive(Clone, Copy, Debug, Default)]
pub struct SeVerify;

impl crate::registry::SigVerify for SeVerify {
    fn verify(&self, public: &[u8; PUBLIC_KEY_LEN], msg: &[u8], sig: &[u8]) -> bool {
        se::verify(public, msg, sig) == Ok(true)
    }
}

/// Public keys registered for Secure Enclave keys, by key id. Where the list comes
/// from is the caller's business and must be somewhere the same-user AI cannot write
/// (see [`TrustedKeys`]); this type only checks signatures against it.
#[derive(Clone, Debug, Default)]
pub struct RegisteredSeKeys {
    keys: Vec<(String, [u8; PUBLIC_KEY_LEN])>,
}

impl RegisteredSeKeys {
    pub fn new() -> RegisteredSeKeys {
        RegisteredSeKeys::default()
    }

    /// Register `public` under its own key id ([`crate::hwkey::key_id_of`]).
    pub fn add(&mut self, public: [u8; PUBLIC_KEY_LEN]) {
        let id = crate::hwkey::key_id_of(&public);
        self.keys.retain(|(k, _)| *k != id);
        self.keys.push((id, public));
    }
}

impl TrustedKeys for RegisteredSeKeys {
    fn check(
        &self,
        provider: &str,
        key_id: &str,
        alg: &str,
        msg: &[u8],
        sig: &[u8],
    ) -> Option<bool> {
        if provider != PROVIDER_MACOS_SE || alg != ALG_ECDSA_P256_SHA256 {
            return None;
        }
        let (_, public) = self.keys.iter().find(|(k, _)| k == key_id)?;
        Some(se::verify(public, msg, sig) == Ok(true))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hwkey::{self, key_id_of};

    /// A fixed P-256 test vector (made with OpenSSL, not a Secure Enclave key):
    /// public key, message, DER signature.
    const PUB: &str = "049910306e9544441e42cfd87682c29282473ae26e7cf53e4ed5581aaf8b5229091a7c6bec8ba76ebf4003e5bacd8f5710efba28e9dbfab8434639fad4f7467972";
    const MSG: &[u8] = b"aib-approve test vector";
    const SIG: &str = "304402200e5806c7cd7448dff42cbccf5752aefca2d7737c753b4bee57caf37b75234f7702201b496b1b7ad85cd01a05e69542c8ce94d6e5c9b860dcb336d805338061ff2101";

    fn unhex(s: &str) -> Vec<u8> {
        crate::proof::unhex(s).expect("hex")
    }

    fn public() -> [u8; PUBLIC_KEY_LEN] {
        <[u8; PUBLIC_KEY_LEN]>::try_from(unhex(PUB).as_slice()).expect("65 bytes")
    }

    #[test]
    fn a_registered_key_verifies_only_its_own_signature() {
        let mut keys = RegisteredSeKeys::new();
        let id = key_id_of(&public());
        let sig = unhex(SIG);
        let check = |k: &RegisteredSeKeys, id: &str, msg: &[u8], sig: &[u8]| {
            k.check(PROVIDER_MACOS_SE, id, ALG_ECDSA_P256_SHA256, msg, sig)
        };
        assert_eq!(check(&keys, &id, MSG, &sig), None, "not registered yet");
        keys.add(public());
        assert_eq!(check(&keys, &id, MSG, &sig), Some(true));
        assert_eq!(
            check(&keys, &id, b"aib-approve test vectoR", &sig),
            Some(false)
        );
        let mut bad = sig.clone();
        bad[10] ^= 1;
        assert_eq!(check(&keys, &id, MSG, &bad), Some(false));
        assert_eq!(check(&keys, &"0".repeat(32), MSG, &sig), None);
        assert_eq!(
            keys.check("windows-tpm", &id, ALG_ECDSA_P256_SHA256, MSG, &sig),
            None
        );
        assert_eq!(keys.check(PROVIDER_MACOS_SE, &id, "rsa", MSG, &sig), None);
    }

    #[test]
    #[ignore = "needs a Secure Enclave and the owner's own GUI session (not ssh)"]
    fn a_new_signing_key_asks_for_the_owner_and_loads_back() {
        let k = hwkey::enroll(&MacosSe, hwkey::KeyKind::Signing).expect("enroll");
        let loaded = hwkey::load(&MacosSe, &k.to_json()).expect("load");
        assert_eq!(loaded, k);
    }
}
