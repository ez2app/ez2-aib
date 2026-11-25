//! W1b (DESIGN-S102-windows-hello-56cda-1115 r1 plan A; DESIGN-persona-propose-osverify-
//! c2f0b-1123 §3 S10-3): the Windows Hello owner key through NCrypt and the Microsoft
//! Passport key storage provider — create, public key, probe, sign, delete. Written before the
//! W0 owner-present run (ebab8 11-23 06:1x「能做的先做」); **unverified on a real Hello
//! machine**. W0 runs the `#[ignore]` tests below on the laptop with the owner there and
//! settles three things this file assumes:
//! 1. the Passport provider gives a third-party program an ECDSA P-256 key (it may be RSA
//!    only: then plan B, 519d0 decides);
//! 2. the use-context text shows in the Hello window;
//! 3. the codes cancel / no Hello really return ([`crate::hello::classify`]).
//!
//! The key's `blob` is its name (UTF-8, the GUID-like string aibd makes), as on macOS the blob
//! is what the key store hands back. The message is hashed here (SHA-256 with BCrypt), the
//! signature goes out as DER and the public key as X9.63, so callers see what the Secure
//! Enclave gives.

use crate::hello::{
    classify, ecc_blob_to_x963, probe_outcome, raw_to_der, reason_wide, HelloError,
};
use windows_sys::Win32::Security::Cryptography::{
    BCryptHash, NCryptCreatePersistedKey, NCryptDeleteKey, NCryptExportKey, NCryptFinalizeKey,
    NCryptFreeObject, NCryptOpenKey, NCryptOpenStorageProvider, NCryptSetProperty, NCryptSignHash,
    BCRYPT_ECCPUBLIC_BLOB, BCRYPT_SHA256_ALG_HANDLE, MS_NGC_KEY_STORAGE_PROVIDER,
    NCRYPT_ECDSA_P256_ALGORITHM, NCRYPT_KEY_HANDLE, NCRYPT_PROV_HANDLE, NCRYPT_SILENT_FLAG,
    NCRYPT_USE_CONTEXT_PROPERTY,
};

/// The longest message signed (same bound as the macOS key).
pub const SIGN_MAX: usize = 64 * 1024;

fn ok(hr: i32) -> Result<(), HelloError> {
    if hr == 0 {
        Ok(())
    } else {
        Err(classify(hr as u32))
    }
}

struct Prov(NCRYPT_PROV_HANDLE);
impl Drop for Prov {
    fn drop(&mut self) {
        // SAFETY: a handle NCryptOpenStorageProvider gave, freed once
        unsafe { NCryptFreeObject(self.0) };
    }
}

struct Key(NCRYPT_KEY_HANDLE);
impl Drop for Key {
    fn drop(&mut self) {
        if self.0 != 0 {
            // SAFETY: a key handle NCrypt gave, freed once (0 after a delete took it)
            unsafe { NCryptFreeObject(self.0) };
        }
    }
}

fn wide(s: &str) -> Result<Vec<u16>, HelloError> {
    if s.is_empty() || s.contains('\0') {
        return Err(HelloError::BadArg("key name"));
    }
    Ok(s.encode_utf16().chain(std::iter::once(0)).collect())
}

fn name_of(blob: &[u8]) -> Result<Vec<u16>, HelloError> {
    std::str::from_utf8(blob)
        .map_err(|_| HelloError::BadKey)
        .and_then(wide)
}

fn provider() -> Result<Prov, HelloError> {
    crate::hello::interactive_session()?;
    let mut h: NCRYPT_PROV_HANDLE = 0;
    // SAFETY: an out handle and a static provider name
    ok(unsafe { NCryptOpenStorageProvider(&mut h, MS_NGC_KEY_STORAGE_PROVIDER, 0) })
        .map_err(|_| HelloError::NoHello)?;
    Ok(Prov(h))
}

fn open(p: &Prov, blob: &[u8]) -> Result<Key, HelloError> {
    let name = name_of(blob)?;
    let mut k: NCRYPT_KEY_HANDLE = 0;
    // SAFETY: the provider is open, the name is NUL-terminated UTF-16
    ok(unsafe { NCryptOpenKey(p.0, &mut k, name.as_ptr(), 0, 0) }).map_err(|e| {
        if matches!(e, HelloError::Os(_)) {
            HelloError::BadKey
        } else {
            e
        }
    })?;
    Ok(Key(k))
}

fn export_public(k: &Key) -> Result<[u8; 65], HelloError> {
    let mut len = 0u32;
    // SAFETY: a size query (null output)
    ok(unsafe {
        NCryptExportKey(
            k.0,
            0,
            BCRYPT_ECCPUBLIC_BLOB,
            std::ptr::null(),
            std::ptr::null_mut(),
            0,
            &mut len,
            0,
        )
    })?;
    let mut b = vec![0u8; len as usize];
    // SAFETY: the buffer is the size NCrypt asked for
    ok(unsafe {
        NCryptExportKey(
            k.0,
            0,
            BCRYPT_ECCPUBLIC_BLOB,
            std::ptr::null(),
            b.as_mut_ptr(),
            len,
            &mut len,
            0,
        )
    })?;
    b.truncate(len as usize);
    ecc_blob_to_x963(&b)
}

fn sha256(msg: &[u8]) -> Result<[u8; 32], HelloError> {
    let mut out = [0u8; 32];
    // SAFETY: the pseudo-handle for SHA-256, no secret, input and output buffers as sized
    let st = unsafe {
        BCryptHash(
            BCRYPT_SHA256_ALG_HANDLE,
            std::ptr::null(),
            0,
            msg.as_ptr(),
            msg.len() as u32,
            out.as_mut_ptr(),
            32,
        )
    };
    if st != 0 {
        return Err(HelloError::Os(st as u32));
    }
    Ok(out)
}

fn sign_digest(k: &Key, digest: &[u8; 32], flags: u32) -> Result<Vec<u8>, u32> {
    let mut raw = [0u8; 64];
    let mut len = 0u32;
    // SAFETY: no padding info (ECDSA), the digest and a 64-byte output for P-256 r‖s
    let hr = unsafe {
        NCryptSignHash(
            k.0,
            std::ptr::null(),
            digest.as_ptr(),
            32,
            raw.as_mut_ptr(),
            64,
            &mut len,
            flags,
        )
    };
    if hr != 0 {
        return Err(hr as u32);
    }
    Ok(raw[..len as usize].to_vec())
}

/// Whether the Passport provider opens here (the signed-in desktop only); no key, no window
/// (W-3 `aibd ctl owner-check probe`).
pub fn provider_ready() -> Result<(), HelloError> {
    provider().map(drop)
}

/// Make a Passport key named `blob`; its X9.63 public key. Making it may itself ask for Hello.
pub fn create(blob: &[u8]) -> Result<[u8; 65], HelloError> {
    let p = provider()?;
    let name = name_of(blob)?;
    let mut k: NCRYPT_KEY_HANDLE = 0;
    // SAFETY: the provider is open; a static algorithm id and a NUL-terminated name
    ok(unsafe {
        NCryptCreatePersistedKey(
            p.0,
            &mut k,
            NCRYPT_ECDSA_P256_ALGORITHM,
            name.as_ptr(),
            0,
            0,
        )
    })?;
    let k = Key(k);
    // SAFETY: a key handle being made
    ok(unsafe { NCryptFinalizeKey(k.0, 0) })?;
    export_public(&k)
}

/// The key's public key as Windows reports it; needs no Hello.
pub fn public_key(blob: &[u8]) -> Result<[u8; 65], HelloError> {
    let p = provider()?;
    export_public(&open(&p, blob)?)
}

/// `Ok` only when a silent signature is refused: the key asks for the owner every time (WH5).
pub fn probe(blob: &[u8]) -> Result<(), HelloError> {
    let p = provider()?;
    let k = open(&p, blob)?;
    let d = sha256(b"aibd/hello-probe/v1")?;
    match sign_digest(&k, &d, NCRYPT_SILENT_FLAG) {
        Ok(_) => Err(HelloError::WeakKey),
        Err(code) => probe_outcome(code),
    }
}

/// Sign `msg` (SHA-256, ECDSA P-256, DER). Windows shows its Hello window with `reason` as the
/// use-context text; this call blocks until the owner answers.
pub fn sign(blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, HelloError> {
    if msg.is_empty() || msg.len() > SIGN_MAX {
        return Err(HelloError::BadArg("message size"));
    }
    let text = reason_wide(reason)?;
    let p = provider()?;
    let k = open(&p, blob)?;
    // SAFETY: the key is open; the text is NUL-terminated UTF-16, its size in bytes
    ok(unsafe {
        NCryptSetProperty(
            k.0,
            NCRYPT_USE_CONTEXT_PROPERTY,
            text.as_ptr().cast(),
            (text.len() * 2) as u32,
            0,
        )
    })?;
    let d = sha256(msg)?;
    let raw = sign_digest(&k, &d, 0).map_err(classify)?;
    raw_to_der(&raw)
}

/// Delete the key named `blob`.
pub fn delete(blob: &[u8]) -> Result<(), HelloError> {
    let p = provider()?;
    let mut k = open(&p, blob)?;
    // SAFETY: the key is open; NCryptDeleteKey frees the handle on success
    ok(unsafe { NCryptDeleteKey(k.0, 0) })?;
    k.0 = 0;
    Ok(())
}

/// W0: run on the Windows laptop, signed in at its desktop, with the owner there:
/// `cargo test -p aib-sys --lib hello_ncrypt::w0 -- --ignored --nocapture --test-threads=1`.
/// Each prints what it saw; the owner answers the Hello window as the test says.
#[cfg(test)]
mod w0 {
    #![allow(clippy::unwrap_used, clippy::print_stdout)] // owner-present runs: print what happened
    use super::*;

    const NAME: &[u8] = b"aibd-w0-test-key";

    /// W0-1: a third-party P-256 key in the Passport provider (plan A stands or falls here).
    #[test]
    #[ignore = "W0: owner at the Windows laptop"]
    fn w0_1_create_a_p256_passport_key() {
        let _ = delete(NAME);
        let r = create(NAME);
        println!("W0-1 create: {r:?}");
        let pk = r.unwrap();
        assert_eq!(public_key(NAME).unwrap(), pk);
        println!("W0-1 probe (want Ok = asks every time): {:?}", probe(NAME));
    }

    /// W0-2: the Hello window shows the use-context text. Answer it with your finger / PIN.
    #[test]
    #[ignore = "W0: owner at the Windows laptop"]
    fn w0_2_the_window_shows_the_reason() {
        let r = sign(NAME, "W0-2 測試:這行字有沒有出現在 Hello 視窗裡?", b"w0-2");
        println!(
            "W0-2 sign (answer the window): {:?}",
            r.as_ref().map(Vec::len)
        );
        r.unwrap();
    }

    /// W0-3: press Cancel in the Hello window; the code it returns.
    #[test]
    #[ignore = "W0: owner at the Windows laptop"]
    fn w0_3_cancel_is_cancel() {
        let r = sign(NAME, "W0-3 測試:請按「取消」", b"w0-3");
        println!("W0-3 sign after cancel: {r:?}");
        assert_eq!(r.err(), Some(HelloError::Cancel));
        delete(NAME).unwrap();
    }
}
