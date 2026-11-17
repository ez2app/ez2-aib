//! W1–W3 (S102 / T23, DESIGN-S102-windows-hello-56cda-1115 r1, plan A, 519d0 11-16 00:01):
//! Windows Hello owner keys — NCrypt with the Microsoft Passport key storage provider,
//! the prompt text set by aibd.
//!
//! This part (W1a) holds what needs no Hello prompt, so it compiles on every platform and
//! its tests run in every receipt:
//! - [`classify`]: an NCrypt `HRESULT` → the 1.x word ([`HelloError::code`]). Cancel and
//!   time-out are `cancel`, never success (WH1).
//! - [`probe_outcome`]: a silent signature that *succeeds* is a weak key (WH5): the key
//!   must ask for the owner every time.
//! - [`session_gate`]: aibd outside the signed-in user's desktop session (session 0, as
//!   when started over ssh, or not the active console) is `unavailable` at once — no prompt
//!   it could never show, no wait for a time-out (a9136 S83 r1, WH7).
//! - [`raw_to_der`] / [`der_to_raw`] and [`ecc_blob_to_x963`] / [`x963_to_ecc_blob`]:
//!   NCrypt / BCrypt speak raw `r‖s` and `BCRYPT_ECCKEY_BLOB`; aib-approve speaks DER and
//!   X9.63 `04‖X‖Y`, as the macOS Secure Enclave does.
//! - [`reason_wide`]: the prompt text as the NUL-terminated UTF-16 that
//!   `NCRYPT_USE_CONTEXT_PROPERTY` takes.
//!
//! The key calls themselves (create / sign / verify) come in W1b, after the W0 owner-present
//! run on the laptop settles the Passport key name and the codes it really returns. Until
//! then the mapping in [`classify`] for "Hello not set up" is the documented one, unverified.

/// What a Windows Hello owner-key call ended in; [`HelloError::code`] is the 1.x word the
/// callers and aib-approve already use for the macOS key.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum HelloError {
    #[error("bad argument: {0}")]
    BadArg(&'static str),
    #[error("the owner cancelled, or the prompt timed out")]
    Cancel,
    #[error("Windows Hello is not set up or not available on this machine")]
    NoHello,
    #[error("not in the signed-in user's desktop session (session 0, or not the active console)")]
    Unavailable,
    #[error("Windows Hello refused")]
    AuthFailed,
    #[error("the key signs without asking for the owner")]
    WeakKey,
    #[error("the key could not be probed")]
    KeyProbeFailed,
    #[error("the key is gone or not readable")]
    BadKey,
    #[error("os error 0x{0:08x}")]
    Os(u32),
}

impl HelloError {
    /// The 1.x word (same words as `presence_macos::SeError::code`, plus `unavailable`).
    pub fn code(&self) -> &'static str {
        match self {
            HelloError::BadArg(_) => "bad_arg",
            HelloError::Cancel => "cancel",
            HelloError::NoHello => "no_biometry",
            HelloError::Unavailable => "unavailable",
            HelloError::AuthFailed => "auth_failed",
            HelloError::WeakKey => "weak_key",
            HelloError::KeyProbeFailed => "key_probe_failed",
            HelloError::BadKey => "corrupt",
            HelloError::Os(_) => "se_error",
        }
    }
}

/// `HRESULT`s NCrypt returns (values from windows-sys 0.61.2; kept here so the mapping is
/// tested on every platform).
pub mod hr {
    pub const NTE_USER_CANCELLED: u32 = 0x8009_0036;
    /// `HRESULT_FROM_WIN32(ERROR_CANCELLED)`
    pub const WIN32_CANCELLED: u32 = 0x8007_04C7;
    /// `HRESULT_FROM_WIN32(ERROR_TIMEOUT)`: a prompt nobody answered counts as cancel
    pub const WIN32_TIMEOUT: u32 = 0x8007_05B4;
    pub const SCARD_W_CANCELLED_BY_USER: u32 = 0x8010_006E;
    /// the key wants the owner but the call was silent: what a probe hopes for
    pub const NTE_SILENT_CONTEXT: u32 = 0x8009_0022;
    pub const NTE_DEVICE_NOT_FOUND: u32 = 0x8009_0035;
    pub const NTE_DEVICE_NOT_READY: u32 = 0x8009_0030;
    pub const NTE_NOT_SUPPORTED: u32 = 0x8009_0029;
    pub const NTE_BAD_PROVIDER: u32 = 0x8009_0013;
    pub const NTE_PROV_DLL_NOT_FOUND: u32 = 0x8009_001E;
    pub const NTE_NOT_FOUND: u32 = 0x8009_0011;
    pub const NTE_BAD_KEYSET: u32 = 0x8009_0016;
    pub const NTE_NO_KEY: u32 = 0x8009_000D;
    pub const NTE_PERM: u32 = 0x8009_0010;
}

/// A failed NCrypt call's `HRESULT` → its error. Cancel and time-out are [`HelloError::Cancel`]
/// (never a success); a provider or device that is not there is [`HelloError::NoHello`];
/// anything not known is [`HelloError::Os`], which callers treat as a refusal.
pub fn classify(code: u32) -> HelloError {
    match code {
        hr::NTE_USER_CANCELLED
        | hr::WIN32_CANCELLED
        | hr::WIN32_TIMEOUT
        | hr::SCARD_W_CANCELLED_BY_USER => HelloError::Cancel,
        hr::NTE_DEVICE_NOT_FOUND
        | hr::NTE_DEVICE_NOT_READY
        | hr::NTE_NOT_SUPPORTED
        | hr::NTE_BAD_PROVIDER
        | hr::NTE_PROV_DLL_NOT_FOUND => HelloError::NoHello,
        hr::NTE_NOT_FOUND | hr::NTE_BAD_KEYSET | hr::NTE_NO_KEY => HelloError::BadKey,
        hr::NTE_PERM => HelloError::AuthFailed,
        other => HelloError::Os(other),
    }
}

/// A silent (`NCRYPT_SILENT_FLAG`) test signature's result → whether the key is good: it
/// must refuse with [`hr::NTE_SILENT_CONTEXT`] (it wants the owner). A signature made
/// silently is a weak key; any other failure means the key could not be probed.
pub fn probe_outcome(code: u32) -> Result<(), HelloError> {
    match code {
        hr::NTE_SILENT_CONTEXT => Ok(()),
        0 => Err(HelloError::WeakKey),
        _ => Err(HelloError::KeyProbeFailed),
    }
}

/// `WTSGetActiveConsoleSessionId` when no session is attached to the console.
pub const NO_CONSOLE: u32 = 0xFFFF_FFFF;

/// Whether aibd may ask for Windows Hello at all: only from the signed-in user's own
/// desktop, the active console session. Session 0 (services, ssh), no console, or another
/// session ⇒ [`HelloError::Unavailable`] at once. `own` is `None` when the session could not
/// be read, which is unavailable too.
pub fn session_gate(own: Option<u32>, console: u32) -> Result<(), HelloError> {
    match own {
        Some(s) if s != 0 && console != NO_CONSOLE && s == console => Ok(()),
        _ => Err(HelloError::Unavailable),
    }
}

/// One DER INTEGER for a 32-byte unsigned big-endian value.
fn der_int(v: &[u8]) -> Vec<u8> {
    let start = v.iter().position(|&b| b != 0).unwrap_or(v.len() - 1);
    let v = &v[start..];
    let pad = v[0] & 0x80 != 0;
    let mut out = vec![0x02, (v.len() + usize::from(pad)) as u8];
    if pad {
        out.push(0);
    }
    out.extend_from_slice(v);
    out
}

/// NCrypt's raw P-256 signature (`r‖s`, 64 bytes) → DER (`SEQUENCE { INTEGER r, INTEGER s }`).
pub fn raw_to_der(raw: &[u8]) -> Result<Vec<u8>, HelloError> {
    if raw.len() != 64 {
        return Err(HelloError::BadArg("a P-256 signature is 64 bytes"));
    }
    let (r, s) = (der_int(&raw[..32]), der_int(&raw[32..]));
    let mut out = vec![0x30, (r.len() + s.len()) as u8];
    out.extend(r);
    out.extend(s);
    Ok(out)
}

/// One DER INTEGER at the start of `d` → its value left-padded to 32 bytes, and the rest.
fn der_take_int(d: &[u8]) -> Option<([u8; 32], &[u8])> {
    let (&tag, d) = d.split_first()?;
    let (&len, d) = d.split_first()?;
    let len = usize::from(len);
    if tag != 0x02 || len == 0 || len > 33 || d.len() < len {
        return None;
    }
    let (v, rest) = d.split_at(len);
    // minimal: a leading 0 only before a high bit, and a 33rd byte only as that 0
    if len > 1 && v[0] == 0 && v[1] & 0x80 == 0 {
        return None;
    }
    // r and s are positive: a first byte with its high bit set is a negative integer
    if v[0] & 0x80 != 0 {
        return None;
    }
    let v = if len == 33 {
        if v[0] != 0 {
            return None;
        }
        &v[1..]
    } else {
        v
    };
    let mut out = [0u8; 32];
    out[32 - v.len()..].copy_from_slice(v);
    Some((out, rest))
}

/// DER (`SEQUENCE { INTEGER r, INTEGER s }`) → raw `r‖s` for `BCryptVerifySignature`;
/// anything else, or bytes left over, is refused.
pub fn der_to_raw(der: &[u8]) -> Result<[u8; 64], HelloError> {
    let bad = HelloError::BadArg("not a DER P-256 signature");
    let body = match der {
        [0x30, len, body @ ..] if usize::from(*len) == body.len() => body,
        _ => return Err(bad),
    };
    let (r, rest) = der_take_int(body).ok_or_else(|| bad.clone())?;
    let (s, rest) = der_take_int(rest).ok_or_else(|| bad.clone())?;
    if !rest.is_empty() {
        return Err(bad);
    }
    let mut out = [0u8; 64];
    out[..32].copy_from_slice(&r);
    out[32..].copy_from_slice(&s);
    Ok(out)
}

/// `BCRYPT_ECDSA_PUBLIC_P256_MAGIC` ("ECS1", little-endian).
pub const ECS1: u32 = 0x3153_4345;

/// A `BCRYPT_ECCPUBLIC_BLOB` for P-256 (magic, key length 32, X, Y) → X9.63 `04‖X‖Y`.
pub fn ecc_blob_to_x963(blob: &[u8]) -> Result<[u8; 65], HelloError> {
    let bad = HelloError::BadArg("not a P-256 ECCPUBLICBLOB");
    if blob.len() != 72 {
        return Err(bad);
    }
    let word = |i: usize| u32::from_le_bytes([blob[i], blob[i + 1], blob[i + 2], blob[i + 3]]);
    if word(0) != ECS1 || word(4) != 32 {
        return Err(bad);
    }
    let mut out = [0u8; 65];
    out[0] = 0x04;
    out[1..].copy_from_slice(&blob[8..]);
    Ok(out)
}

/// X9.63 `04‖X‖Y` → the `BCRYPT_ECCPUBLIC_BLOB` `BCryptImportKeyPair` takes.
pub fn x963_to_ecc_blob(public: &[u8; 65]) -> Result<[u8; 72], HelloError> {
    if public[0] != 0x04 {
        return Err(HelloError::BadArg("not an uncompressed X9.63 public key"));
    }
    let mut out = [0u8; 72];
    out[..4].copy_from_slice(&ECS1.to_le_bytes());
    out[4..8].copy_from_slice(&32u32.to_le_bytes());
    out[8..].copy_from_slice(&public[1..]);
    Ok(out)
}

/// The prompt text as NUL-terminated UTF-16 (`NCRYPT_USE_CONTEXT_PROPERTY`). aib-approve
/// checks length and characters first; here an empty text or an inner NUL is refused, so
/// the owner never sees a cut-short line.
pub fn reason_wide(reason: &str) -> Result<Vec<u16>, HelloError> {
    if reason.is_empty() {
        return Err(HelloError::BadArg("empty prompt text"));
    }
    if reason.contains('\0') {
        return Err(HelloError::BadArg("prompt text with a NUL"));
    }
    let mut w: Vec<u16> = reason.encode_utf16().collect();
    w.push(0);
    Ok(w)
}

/// [`session_gate`] for this process: its session (`ProcessIdToSessionId`) against the
/// active console session (`WTSGetActiveConsoleSessionId`).
#[cfg(windows)]
pub fn interactive_session() -> Result<(), HelloError> {
    use windows_sys::Win32::System::RemoteDesktop::{
        ProcessIdToSessionId, WTSGetActiveConsoleSessionId,
    };
    let mut own: u32 = 0;
    // SAFETY: `own` is a live u32 the call writes once; the pid is our own; no other pointers.
    let ok = unsafe { ProcessIdToSessionId(std::process::id(), &mut own) };
    // SAFETY: takes no arguments and touches no memory of ours.
    let console = unsafe { WTSGetActiveConsoleSessionId() };
    session_gate((ok != 0).then_some(own), console)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// WH1: cancel and time-out are `cancel`, never a success; unknown codes stay errors.
    #[test]
    fn cancel_and_timeout_are_cancel_and_unknown_codes_are_errors() {
        for c in [
            hr::NTE_USER_CANCELLED,
            hr::WIN32_CANCELLED,
            hr::WIN32_TIMEOUT,
            hr::SCARD_W_CANCELLED_BY_USER,
        ] {
            assert_eq!(classify(c), HelloError::Cancel, "0x{c:08x}");
            assert_eq!(classify(c).code(), "cancel");
        }
        assert_eq!(classify(hr::NTE_DEVICE_NOT_FOUND).code(), "no_biometry");
        assert_eq!(classify(hr::NTE_PROV_DLL_NOT_FOUND).code(), "no_biometry");
        assert_eq!(classify(hr::NTE_BAD_KEYSET).code(), "corrupt");
        assert_eq!(classify(hr::NTE_PERM).code(), "auth_failed");
        assert_eq!(classify(0x8000_4005), HelloError::Os(0x8000_4005));
        assert_eq!(classify(0x8000_4005).code(), "se_error");
    }

    /// WH5: only a key that refuses a silent signature is good.
    #[test]
    fn a_key_that_signs_silently_is_weak() {
        assert_eq!(probe_outcome(hr::NTE_SILENT_CONTEXT), Ok(()));
        assert_eq!(probe_outcome(0), Err(HelloError::WeakKey));
        assert_eq!(
            probe_outcome(hr::NTE_USER_CANCELLED),
            Err(HelloError::KeyProbeFailed)
        );
    }

    /// WH7 (a9136 S83 r1): session 0, no console, another session or an unreadable session
    /// is `unavailable` at once.
    #[test]
    fn only_the_active_console_session_may_ask_for_hello() {
        assert_eq!(session_gate(Some(1), 1), Ok(()));
        for (own, console) in [
            (Some(0), 0),
            (Some(0), 1),
            (Some(1), NO_CONSOLE),
            (Some(2), 1),
            (None, 1),
        ] {
            assert_eq!(
                session_gate(own, console),
                Err(HelloError::Unavailable),
                "{own:?} {console}"
            );
        }
        assert_eq!(HelloError::Unavailable.code(), "unavailable");
    }

    /// Raw `r‖s` ↔ DER, minimal integers both ways; a bad DER is refused.
    #[test]
    fn signatures_go_between_raw_and_der_exactly() {
        let mut raw = [0u8; 64];
        raw[..32].copy_from_slice(&[0x80; 32]); // high bit: a 0 goes in front
        raw[62] = 0x01; // s = 0x0100: leading zeros dropped
        let der = raw_to_der(&raw).unwrap();
        assert_eq!(&der[..4], &[0x30, 4 + 33 + 2, 0x02, 33]);
        assert_eq!(der[4], 0);
        assert_eq!(&der[der.len() - 4..], &[0x02, 2, 0x01, 0x00]);
        assert_eq!(der_to_raw(&der).unwrap(), raw);
        let zero = raw_to_der(&[0u8; 64]).unwrap();
        assert_eq!(zero, vec![0x30, 6, 0x02, 1, 0, 0x02, 1, 0]);
        assert!(raw_to_der(&[1u8; 63]).is_err());
        let mut extra = der.clone();
        extra.push(0);
        assert!(der_to_raw(&extra).is_err(), "bytes left over");
        assert!(
            der_to_raw(&[0x30, 7, 0x02, 2, 0, 1, 0x02, 1, 1]).is_err(),
            "a needless leading zero"
        );
        assert!(der_to_raw(&[0x31, 6, 0x02, 1, 1, 0x02, 1, 1]).is_err());
        // 5d33a ca33269b L1: 02 01 80 is a negative integer, and with 02 02 00 80 it
        // would be a second encoding of one r; only the padded one is DER
        assert!(
            der_to_raw(&[0x30, 6, 0x02, 1, 0x80, 0x02, 1, 1]).is_err(),
            "a negative integer"
        );
        assert!(der_to_raw(&[0x30, 7, 0x02, 2, 0, 0x80, 0x02, 1, 1]).is_ok());
    }

    /// ECCPUBLICBLOB ↔ X9.63; a wrong magic or length is refused.
    #[test]
    fn public_keys_go_between_the_ncrypt_blob_and_x963() {
        let mut public = [7u8; 65];
        public[0] = 0x04;
        let blob = x963_to_ecc_blob(&public).unwrap();
        assert_eq!(&blob[..8], &[0x45, 0x43, 0x53, 0x31, 32, 0, 0, 0]);
        assert_eq!(ecc_blob_to_x963(&blob).unwrap(), public);
        let mut wrong = blob;
        wrong[0] = 0x46;
        assert!(ecc_blob_to_x963(&wrong).is_err());
        assert!(ecc_blob_to_x963(&blob[..71]).is_err());
        public[0] = 0x02;
        assert!(x963_to_ecc_blob(&public).is_err());
    }

    /// The prompt text is NUL-terminated UTF-16; empty or with a NUL is refused.
    #[test]
    fn the_prompt_text_is_whole_utf16_or_refused() {
        assert_eq!(
            reason_wide("確認 ab").unwrap(),
            vec![0x78BA, 0x8A8D, 0x20, 0x61, 0x62, 0]
        );
        assert!(reason_wide("").is_err());
        assert!(reason_wide("a\0b").is_err());
    }
}
