//! P1–P5: Secure Enclave signing keys that need the owner (Touch ID or login password),
//! for human approval (B2 T135-b; DECISION-b2-windows-native W-a, DECISION-t135b-objc2-deps).
//!
//! | # | call | grade |
//! |---|---|---|
//! | P1 | `SecKeyCreateRandomKey` (token = Secure Enclave, not permanent, ACL from `SecAccessControlCreateWithFlags`) | public C API |
//! | P2 | restore a key from its token object id (`"toid"`) with `SecKeyCreateWithData` | **private attribute** (`kSecAttrTokenOID` is SPI) |
//! | P3 | `SecKeyCopyPublicKey` + `SecKeyCopyExternalRepresentation` | public C API |
//! | P4 | `SecKeyCreateSignature` / `SecKeyVerifySignature`, ECDSA message SHA-256, DER | public C API |
//! | P5 | `LAContext` (`interactionNotAllowed`, `setCredential`, `localizedReason`) as `kSecUseAuthenticationContext` | public ObjC API via objc2 |
//!
//! Why the token object id: the key never leaves the Secure Enclave; what is stored is an
//! opaque blob only this Mac's Secure Enclave can turn back into the key, with the access
//! control fixed when the key was made. It needs no keychain and no entitlement (the 1.x
//! Swift helper used CryptoKit's `dataRepresentation` for the same reason). `toid` is not a
//! documented attribute, so a macOS update may change it: callers must check after every
//! load that [`public_key`] still equals the public key registered when the key was made,
//! and refuse the key if not (aib-approve does this).
//!
//! Measured (NOTES-t135b-spike-ebb36-1113, macOS 15.7.9 / 15.8.1, arm64): restore in another
//! process works for keys with and without an owner ACL; a key with an owner ACL can only be
//! made from the user's own logged-in GUI session (over ssh with no GUI session:
//! `errSecInteractionNotAllowed` -25308); a non-interactive try on an owner key gives
//! `com.apple.LocalAuthentication` -1004, as in 1.x.
//!
//! Not a defence by itself: a same-user AI can call this code. The defence is that the
//! Secure Enclave will not sign with an owner key until the owner presses or types.
//! [`probe`] checks a key really asks for the owner (two non-interactive tries, the second
//! with a random application password, as 1.x `requireOwnerACL`); it screens out known weak
//! keys, it does not prove the ACL is exactly the one asked for.
//!
//! Every `unsafe` block is one FFI call, one static read or one pointer cast, with its own
//! SAFETY note.

use libc::{c_char, c_void};
use objc2::rc::Retained;
use objc2_foundation::{NSData, NSString};
use objc2_local_authentication::{LAContext, LACredentialType};
use std::ffi::CStr;

type CFTypeRef = *const c_void;
type CFIndex = isize;

/// `kCFStringEncodingUTF8`.
const UTF8: u32 = 0x0800_0100;
/// `kCFNumberSInt32Type`.
const CF_NUMBER_SINT32: CFIndex = 3;
/// `kSecAccessControlUserPresence`.
const ACL_USER_PRESENCE: usize = 1 << 0;
/// `kSecAccessControlBiometryCurrentSet`.
const ACL_BIOMETRY_CURRENT_SET: usize = 1 << 3;
/// `kSecAccessControlPrivateKeyUsage`.
const ACL_PRIVATE_KEY_USAGE: usize = 1 << 30;
/// `LAErrorNotInteractive`: the only answer [`probe`] accepts from a non-interactive try.
const LA_NOT_INTERACTIVE: CFIndex = -1004;
/// Largest message [`sign`] takes (1.x helper: stdin data cap).
pub const SIGN_MAX: usize = 65_536;
/// An uncompressed P-256 public key in X9.63 form: `0x04 || X || Y`.
pub const PUBLIC_KEY_LEN: usize = 65;

/// Opaque CoreFoundation callback tables; only their address is used.
#[repr(C)]
struct CallBacks {
    _p: [u8; 0],
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    static kCFTypeDictionaryKeyCallBacks: CallBacks;
    static kCFTypeDictionaryValueCallBacks: CallBacks;
    static kCFBooleanFalse: CFTypeRef;
    fn CFRelease(cf: CFTypeRef);
    fn CFDictionaryCreateMutable(
        alloc: CFTypeRef,
        cap: CFIndex,
        key_cb: *const CallBacks,
        value_cb: *const CallBacks,
    ) -> CFTypeRef;
    fn CFDictionarySetValue(d: CFTypeRef, key: CFTypeRef, value: CFTypeRef);
    fn CFDictionaryGetValue(d: CFTypeRef, key: CFTypeRef) -> CFTypeRef;
    fn CFDataCreate(alloc: CFTypeRef, bytes: *const u8, len: CFIndex) -> CFTypeRef;
    fn CFDataGetLength(d: CFTypeRef) -> CFIndex;
    fn CFDataGetBytePtr(d: CFTypeRef) -> *const u8;
    fn CFNumberCreate(alloc: CFTypeRef, ty: CFIndex, value: *const c_void) -> CFTypeRef;
    fn CFStringCreateWithCString(alloc: CFTypeRef, s: *const c_char, enc: u32) -> CFTypeRef;
    fn CFStringGetCString(s: CFTypeRef, buf: *mut c_char, size: CFIndex, enc: u32) -> u8;
    fn CFErrorGetCode(e: CFTypeRef) -> CFIndex;
    fn CFErrorGetDomain(e: CFTypeRef) -> CFTypeRef;
}

#[link(name = "Security", kind = "framework")]
extern "C" {
    static kSecAttrKeyType: CFTypeRef;
    static kSecAttrKeyTypeECSECPrimeRandom: CFTypeRef;
    static kSecAttrKeySizeInBits: CFTypeRef;
    static kSecAttrTokenID: CFTypeRef;
    static kSecAttrTokenIDSecureEnclave: CFTypeRef;
    static kSecPrivateKeyAttrs: CFTypeRef;
    static kSecAttrIsPermanent: CFTypeRef;
    static kSecAttrKeyClass: CFTypeRef;
    static kSecAttrKeyClassPrivate: CFTypeRef;
    static kSecAttrKeyClassPublic: CFTypeRef;
    static kSecAttrAccessControl: CFTypeRef;
    static kSecAttrAccessibleWhenUnlockedThisDeviceOnly: CFTypeRef;
    static kSecUseAuthenticationContext: CFTypeRef;
    static kSecKeyAlgorithmECDSASignatureMessageX962SHA256: CFTypeRef;
    fn SecAccessControlCreateWithFlags(
        alloc: CFTypeRef,
        protection: CFTypeRef,
        flags: usize,
        err: *mut CFTypeRef,
    ) -> CFTypeRef;
    fn SecKeyCreateRandomKey(params: CFTypeRef, err: *mut CFTypeRef) -> CFTypeRef;
    fn SecKeyCopyAttributes(key: CFTypeRef) -> CFTypeRef;
    fn SecKeyCopyPublicKey(key: CFTypeRef) -> CFTypeRef;
    fn SecKeyCopyExternalRepresentation(key: CFTypeRef, err: *mut CFTypeRef) -> CFTypeRef;
    fn SecKeyCreateWithData(data: CFTypeRef, attrs: CFTypeRef, err: *mut CFTypeRef) -> CFTypeRef;
    fn SecKeyCreateSignature(
        key: CFTypeRef,
        alg: CFTypeRef,
        data: CFTypeRef,
        err: *mut CFTypeRef,
    ) -> CFTypeRef;
    fn SecKeyVerifySignature(
        key: CFTypeRef,
        alg: CFTypeRef,
        data: CFTypeRef,
        sig: CFTypeRef,
        err: *mut CFTypeRef,
    ) -> u8;
}

/// What a key needs before the Secure Enclave uses it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Acl {
    /// Touch ID with the fingers enrolled now (re-enrolling voids the key). The default
    /// for every signing key.
    BiometryCurrentSet,
    /// Touch ID or the login password. 1.x allowed this only for the grant-signing key;
    /// aib-approve keeps that rule.
    UserPresence,
}

/// Why a Secure Enclave call did not give a result.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum SeError {
    /// Wrong input, refused before the Secure Enclave was asked.
    #[error("bad argument: {0}")]
    BadArg(&'static str),
    /// The person cancelled (or the system did).
    #[error("cancelled")]
    Cancel,
    /// No biometry / no passcode / locked out.
    #[error("biometry not available")]
    NoBiometry,
    /// The fingerprint or password did not match.
    #[error("authentication failed")]
    AuthFailed,
    /// A key that should need the owner signed without asking.
    #[error("key does not require the owner")]
    WeakKey,
    /// The owner check could not be completed for another reason.
    #[error("key probe failed")]
    KeyProbeFailed,
    /// The stored blob does not give back a key.
    #[error("key cannot be restored")]
    BadKey,
    /// A Security / LocalAuthentication error not classified above.
    #[error("{domain} {code}")]
    Os { domain: String, code: isize },
}

impl SeError {
    /// The 1.x helper word (`aib-sehelper` stderr code) for this error.
    pub fn code(&self) -> &'static str {
        match self {
            SeError::BadArg(_) => "bad_arg",
            SeError::Cancel => "cancel",
            SeError::NoBiometry => "no_biometry",
            SeError::AuthFailed => "auth_failed",
            SeError::WeakKey => "weak_key",
            SeError::KeyProbeFailed => "key_probe_failed",
            SeError::BadKey => "corrupt",
            SeError::Os { .. } => "se_error",
        }
    }
}

/// Map a Security / LocalAuthentication error to [`SeError`], as the 1.x helper's
/// `mapError` does (aib-sehelper.swift :120–:134). Unknown codes stay [`SeError::Os`].
pub fn classify(domain: &str, code: isize) -> SeError {
    match (domain, code) {
        ("com.apple.LocalAuthentication", -2 | -3 | -4 | -9) => SeError::Cancel,
        ("com.apple.LocalAuthentication", -8..=-5) => SeError::NoBiometry,
        // "not interactive" stays an OS error: [`probe`] tells it apart from a refusal.
        ("com.apple.LocalAuthentication", LA_NOT_INTERACTIVE) => SeError::Os {
            domain: domain.to_owned(),
            code,
        },
        // 1.x: every other LAError is auth_failed.
        ("com.apple.LocalAuthentication", _) => SeError::AuthFailed,
        // errSecUserCanceled
        ("NSOSStatusErrorDomain", -128) => SeError::Cancel,
        _ => SeError::Os {
            domain: domain.to_owned(),
            code,
        },
    }
}

/// A CoreFoundation object we own; released once on drop. Null is allowed and skipped.
struct Owned(CFTypeRef);

impl Owned {
    fn ptr(&self) -> CFTypeRef {
        self.0
    }
    fn is_null(&self) -> bool {
        self.0.is_null()
    }
}

impl Drop for Owned {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: `self.0` came from a CoreFoundation Create / Copy call (we own one
            // reference) and is released exactly once, here.
            unsafe { CFRelease(self.0) };
        }
    }
}

/// Read one of the `extern` CF / Security constants, given as `&raw const NAME`.
fn k(p: *const CFTypeRef) -> CFTypeRef {
    // SAFETY: `p` is the address of a `CFStringRef` / `CFBooleanRef` constant exported by a
    // linked framework, initialised before `main` and never written.
    unsafe { *p }
}

fn domain_and_code(err: CFTypeRef) -> (String, isize) {
    if err.is_null() {
        return ("unknown".to_owned(), 0);
    }
    // SAFETY: `err` is a live CFErrorRef returned by a Security call.
    let code = unsafe { CFErrorGetCode(err) };
    // SAFETY: as above; the domain is borrowed (Get rule), not released.
    let dom = unsafe { CFErrorGetDomain(err) };
    let mut buf = [0 as c_char; 128];
    if dom.is_null() {
        return ("unknown".to_owned(), code);
    }
    // SAFETY: `dom` is a live CFStringRef; `buf` is writable for its full length.
    let ok = unsafe { CFStringGetCString(dom, buf.as_mut_ptr(), buf.len() as CFIndex, UTF8) } != 0;
    let domain = if ok {
        // SAFETY: `CFStringGetCString` returned true, so `buf` holds a NUL-terminated string.
        unsafe { CStr::from_ptr(buf.as_ptr()) }
            .to_string_lossy()
            .into_owned()
    } else {
        "unknown".to_owned()
    };
    (domain, code)
}

/// Take ownership of an error out-parameter and classify it.
fn take_error(err: CFTypeRef) -> SeError {
    let owned = Owned(err);
    let (domain, code) = domain_and_code(owned.ptr());
    classify(&domain, code)
}

fn new_dict() -> Owned {
    let kcb = &raw const kCFTypeDictionaryKeyCallBacks;
    let vcb = &raw const kCFTypeDictionaryValueCallBacks;
    // SAFETY: the callback tables are the framework's constant structures; capacity 0 means
    // unbounded. Returns a new mutable dictionary we own (or null).
    Owned(unsafe { CFDictionaryCreateMutable(std::ptr::null(), 0, kcb, vcb) })
}

fn set(d: &Owned, key: CFTypeRef, value: CFTypeRef) {
    // SAFETY: `d` is a live mutable dictionary created by `new_dict`; key and value are live
    // CF objects, retained by the dictionary's callbacks.
    unsafe { CFDictionarySetValue(d.ptr(), key, value) };
}

fn cf_data(b: &[u8]) -> Owned {
    // SAFETY: `b` is readable for `b.len()` bytes; CFDataCreate copies them.
    Owned(unsafe { CFDataCreate(std::ptr::null(), b.as_ptr(), b.len() as CFIndex) })
}

fn data_bytes(d: CFTypeRef) -> Vec<u8> {
    // SAFETY: `d` is a live CFDataRef.
    let len = unsafe { CFDataGetLength(d) };
    // SAFETY: as above; the pointer is valid until `d` is released, which is after the copy.
    let p = unsafe { CFDataGetBytePtr(d) };
    if p.is_null() || len <= 0 {
        return Vec::new();
    }
    // SAFETY: `p` points at `len` bytes owned by `d`, which outlives this copy.
    unsafe { std::slice::from_raw_parts(p, len as usize) }.to_vec()
}

fn toid_key() -> Owned {
    // SAFETY: a NUL-terminated literal; returns a new CFString we own.
    Owned(unsafe { CFStringCreateWithCString(std::ptr::null(), c"toid".as_ptr(), UTF8) })
}

/// The attributes every Secure Enclave P-256 key here shares.
fn ec_se_attrs() -> Result<(Owned, Owned), SeError> {
    let n: i32 = 256;
    // SAFETY: `n` is a live i32 matching kCFNumberSInt32Type; returns a new CFNumber we own.
    let bits =
        Owned(unsafe { CFNumberCreate(std::ptr::null(), CF_NUMBER_SINT32, (&raw const n).cast()) });
    let a = new_dict();
    if a.is_null() || bits.is_null() {
        return Err(SeError::Os {
            domain: "CoreFoundation".into(),
            code: 0,
        });
    }
    set(
        &a,
        k(&raw const kSecAttrKeyType),
        k(&raw const kSecAttrKeyTypeECSECPrimeRandom),
    );
    set(&a, k(&raw const kSecAttrKeySizeInBits), bits.ptr());
    set(
        &a,
        k(&raw const kSecAttrTokenID),
        k(&raw const kSecAttrTokenIDSecureEnclave),
    );
    Ok((a, bits))
}

fn create_with(flags: Option<usize>) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), SeError> {
    let (a, _bits) = ec_se_attrs()?;
    let p = new_dict();
    set(
        &p,
        k(&raw const kSecAttrIsPermanent),
        k(&raw const kCFBooleanFalse),
    );
    if let Some(flags) = flags {
        let mut err: CFTypeRef = std::ptr::null();
        let prot = k(&raw const kSecAttrAccessibleWhenUnlockedThisDeviceOnly);
        // SAFETY: `prot` is a framework constant, `err` a valid out-pointer. Returns a new
        // SecAccessControlRef we own (or null with `err` set).
        let ac = Owned(unsafe {
            SecAccessControlCreateWithFlags(std::ptr::null(), prot, flags, &mut err)
        });
        if ac.is_null() {
            return Err(take_error(err));
        }
        set(&p, k(&raw const kSecAttrAccessControl), ac.ptr()); // retained by `p`
    }
    set(&a, k(&raw const kSecPrivateKeyAttrs), p.ptr());
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: `a` is a live attribute dictionary, `err` a valid out-pointer. Returns a new
    // SecKeyRef we own (or null with `err` set).
    let key = Owned(unsafe { SecKeyCreateRandomKey(a.ptr(), &mut err) });
    if key.is_null() {
        return Err(take_error(err));
    }
    // SAFETY: `key` is a live SecKeyRef; returns a new dictionary we own.
    let attrs = Owned(unsafe { SecKeyCopyAttributes(key.ptr()) });
    if attrs.is_null() {
        return Err(SeError::BadKey);
    }
    let tk = toid_key();
    // SAFETY: `attrs` and `tk` are live; the value is borrowed (Get rule).
    let toid = unsafe { CFDictionaryGetValue(attrs.ptr(), tk.ptr()) };
    if toid.is_null() {
        return Err(SeError::BadKey);
    }
    let blob = data_bytes(toid);
    let public = public_of(&key)?;
    Ok((blob, public))
}

/// P1: make a new Secure Enclave signing key with `acl`. Returns the stored blob (token
/// object id) and the public key. Must run in the owner's own GUI session.
pub fn create(acl: Acl) -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), SeError> {
    let flags = ACL_PRIVATE_KEY_USAGE
        | match acl {
            Acl::BiometryCurrentSet => ACL_BIOMETRY_CURRENT_SET,
            Acl::UserPresence => ACL_USER_PRESENCE,
        };
    create_with(Some(flags))
}

fn public_of(key: &Owned) -> Result<[u8; PUBLIC_KEY_LEN], SeError> {
    // SAFETY: `key` is a live SecKeyRef; returns a new public SecKeyRef we own (or null).
    let pubk = Owned(unsafe { SecKeyCopyPublicKey(key.ptr()) });
    if pubk.is_null() {
        return Err(SeError::BadKey);
    }
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: `pubk` is live, `err` a valid out-pointer; returns new CFData we own.
    let d = Owned(unsafe { SecKeyCopyExternalRepresentation(pubk.ptr(), &mut err) });
    if d.is_null() {
        return Err(take_error(err));
    }
    let b = data_bytes(d.ptr());
    match <[u8; PUBLIC_KEY_LEN]>::try_from(b.as_slice()) {
        Ok(a) if a[0] == 0x04 => Ok(a),
        _ => Err(SeError::BadKey),
    }
}

/// P2: turn a stored blob back into the key, with `ctx` as its authentication context.
fn restore(blob: &[u8], ctx: Option<&LAContext>) -> Result<Owned, SeError> {
    if blob.is_empty() {
        return Err(SeError::BadArg("empty key blob"));
    }
    let (a, _bits) = ec_se_attrs()?;
    set(
        &a,
        k(&raw const kSecAttrKeyClass),
        k(&raw const kSecAttrKeyClassPrivate),
    );
    let tk = toid_key();
    let tdata = cf_data(blob);
    set(&a, tk.ptr(), tdata.ptr());
    if let Some(ctx) = ctx {
        let p: *const LAContext = ctx;
        // An LAContext is an NSObject, toll-free usable as a CFTypeRef value; the dictionary
        // retains it.
        set(&a, k(&raw const kSecUseAuthenticationContext), p.cast());
    }
    let empty = cf_data(&[]);
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: `empty` and `a` are live; `err` a valid out-pointer. Returns a new SecKeyRef we
    // own (or null with `err` set).
    let key = Owned(unsafe { SecKeyCreateWithData(empty.ptr(), a.ptr(), &mut err) });
    if key.is_null() {
        let e = take_error(err);
        return Err(match e {
            SeError::Os { .. } => SeError::BadKey,
            other => other,
        });
    }
    Ok(key)
}

/// P3: the public key, recomputed from the Secure Enclave key itself (never read from a
/// file). Needs no fingerprint.
pub fn public_key(blob: &[u8]) -> Result<[u8; PUBLIC_KEY_LEN], SeError> {
    let key = restore(blob, None)?;
    public_of(&key)
}

fn la_context() -> Retained<LAContext> {
    // SAFETY: `+[LAContext new]` has no preconditions and returns a fresh, retained context.
    unsafe { LAContext::new() }
}

fn sign_key(key: &Owned, msg: &[u8]) -> Result<Vec<u8>, SeError> {
    let data = cf_data(msg);
    let alg = k(&raw const kSecKeyAlgorithmECDSASignatureMessageX962SHA256);
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: `key` and `data` are live, `alg` a framework constant, `err` a valid
    // out-pointer. Returns new CFData we own (or null with `err` set).
    let sig = Owned(unsafe { SecKeyCreateSignature(key.ptr(), alg, data.ptr(), &mut err) });
    if sig.is_null() {
        return Err(take_error(err));
    }
    Ok(data_bytes(sig.ptr()))
}

/// One non-interactive try. `Ok(())` only for LocalAuthentication "not interactive".
fn probe_once(blob: &[u8], app_password: Option<&[u8]>) -> Result<(), SeError> {
    let ctx = la_context();
    // SAFETY: `ctx` is a live LAContext; setting a BOOL property has no other precondition.
    unsafe { ctx.setInteractionNotAllowed(true) };
    if let Some(pw) = app_password {
        let d = NSData::with_bytes(pw);
        // SAFETY: `ctx` is live and `d` is a live NSData for the call's duration.
        let set_ok =
            unsafe { ctx.setCredential_type(Some(&d), LACredentialType::ApplicationPassword) };
        if !set_ok {
            return Err(SeError::KeyProbeFailed);
        }
    }
    let key = restore(blob, Some(&*ctx)).map_err(|_| SeError::KeyProbeFailed)?;
    match sign_key(&key, b"aib-approve owner probe") {
        Ok(_) => Err(SeError::WeakKey),
        Err(SeError::Os { domain, code })
            if domain == "com.apple.LocalAuthentication" && code == LA_NOT_INTERACTIVE =>
        {
            Ok(())
        }
        Err(_) => Err(SeError::KeyProbeFailed),
    }
}

/// P5: check that the key at `blob` asks for the owner. Two tries, each with a fresh
/// non-interactive context; the second also carries a random 32-byte application password
/// (1.x #419 R2-M1: a key protected by an application password answered the first try the
/// same way). Any signature means [`SeError::WeakKey`]; any answer other than
/// "not interactive" means [`SeError::KeyProbeFailed`]. Needs no fingerprint.
pub fn probe(blob: &[u8]) -> Result<(), SeError> {
    probe_once(blob, None)?;
    let mut pw = [0u8; 32];
    // SAFETY: `pw` is writable for 32 bytes (getentropy's limit is 256).
    let rc = unsafe { libc::getentropy(pw.as_mut_ptr().cast(), pw.len()) };
    if rc != 0 {
        return Err(SeError::KeyProbeFailed);
    }
    probe_once(blob, Some(&pw))
}

/// P4: sign `msg` (ECDSA P-256, message SHA-256, DER) with the key at `blob`. The system
/// shows its own Touch ID / password window with `reason` in it; this call blocks until the
/// person answers. The reason is not checked here beyond "not empty": aib-approve builds it
/// and checks it (text rules, length) before calling.
pub fn sign(blob: &[u8], reason: &str, msg: &[u8]) -> Result<Vec<u8>, SeError> {
    if reason.trim().is_empty() {
        return Err(SeError::BadArg("empty reason"));
    }
    if msg.is_empty() || msg.len() > SIGN_MAX {
        return Err(SeError::BadArg("message size"));
    }
    let ctx = la_context();
    let r = NSString::from_str(reason);
    // SAFETY: `ctx` is live and `r` a live NSString for the call's duration.
    unsafe { ctx.setLocalizedReason(&r) };
    // SAFETY: `ctx` is live; 0 = never reuse an earlier unlock for this signature.
    unsafe { ctx.setTouchIDAuthenticationAllowableReuseDuration(0.0) };
    let key = restore(blob, Some(&*ctx))?;
    sign_key(&key, msg)
}

/// Check an ECDSA P-256 / SHA-256 / DER signature against an X9.63 public key. Needs no
/// Secure Enclave.
pub fn verify(public: &[u8], msg: &[u8], der: &[u8]) -> Result<bool, SeError> {
    if public.len() != PUBLIC_KEY_LEN || public[0] != 0x04 {
        return Err(SeError::BadArg("public key"));
    }
    let a = new_dict();
    set(
        &a,
        k(&raw const kSecAttrKeyType),
        k(&raw const kSecAttrKeyTypeECSECPrimeRandom),
    );
    set(
        &a,
        k(&raw const kSecAttrKeyClass),
        k(&raw const kSecAttrKeyClassPublic),
    );
    let pd = cf_data(public);
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: `pd` and `a` are live, `err` a valid out-pointer; returns a new key we own.
    let key = Owned(unsafe { SecKeyCreateWithData(pd.ptr(), a.ptr(), &mut err) });
    if key.is_null() {
        let _ = take_error(err);
        return Err(SeError::BadArg("public key"));
    }
    let data = cf_data(msg);
    let sig = cf_data(der);
    let alg = k(&raw const kSecKeyAlgorithmECDSASignatureMessageX962SHA256);
    let mut err: CFTypeRef = std::ptr::null();
    // SAFETY: every reference is live; `err` a valid out-pointer (set, and ours, on false).
    let ok = unsafe { SecKeyVerifySignature(key.ptr(), alg, data.ptr(), sig.ptr(), &mut err) };
    drop(Owned(err));
    Ok(ok != 0)
}

/// A key with **no** owner ACL, for tests that must show [`probe`] calls it weak.
#[cfg(test)]
fn create_unprotected() -> Result<(Vec<u8>, [u8; PUBLIC_KEY_LEN]), SeError> {
    create_with(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn errors_map_like_the_1x_helper() {
        let la = "com.apple.LocalAuthentication";
        for c in [-2, -3, -4, -9] {
            assert_eq!(classify(la, c), SeError::Cancel, "{c}");
        }
        for c in [-5, -6, -7, -8] {
            assert_eq!(classify(la, c), SeError::NoBiometry, "{c}");
        }
        assert_eq!(classify(la, -1), SeError::AuthFailed);
        assert_eq!(classify(la, -10), SeError::AuthFailed);
        assert_eq!(classify("NSOSStatusErrorDomain", -128), SeError::Cancel);
        assert_eq!(
            classify(la, -1004),
            SeError::Os {
                domain: la.into(),
                code: -1004
            }
        );
        assert_eq!(classify("NSOSStatusErrorDomain", -25308).code(), "se_error");
        assert_eq!(SeError::WeakKey.code(), "weak_key");
        assert_eq!(SeError::KeyProbeFailed.code(), "key_probe_failed");
    }

    #[test]
    fn bad_arguments_are_refused_before_the_secure_enclave_is_asked() {
        assert_eq!(sign(b"x", " ", b"m"), Err(SeError::BadArg("empty reason")));
        assert_eq!(sign(b"x", "ok", b""), Err(SeError::BadArg("message size")));
        assert_eq!(
            sign(b"x", "ok", &vec![0; SIGN_MAX + 1]),
            Err(SeError::BadArg("message size"))
        );
        assert_eq!(public_key(b""), Err(SeError::BadArg("empty key blob")));
        assert_eq!(
            verify(&[4; 64], b"m", b"s"),
            Err(SeError::BadArg("public key"))
        );
        let mut p = [0u8; PUBLIC_KEY_LEN];
        p[0] = 0x02;
        assert_eq!(verify(&p, b"m", b"s"), Err(SeError::BadArg("public key")));
    }

    #[test]
    fn a_blob_that_gives_no_key_fails_the_probe_and_has_no_public_key() {
        // No Secure Enclave needed: the restore fails before anything is signed.
        assert_eq!(probe(b""), Err(SeError::KeyProbeFailed));
        assert_eq!(probe(b"not a key"), Err(SeError::KeyProbeFailed));
        assert!(public_key(b"not a key").is_err());
    }

    // ---- below: need a Secure Enclave (Apple silicon / T2 Mac); run with --ignored ----

    #[test]
    #[ignore = "needs a Secure Enclave"]
    fn a_key_comes_back_from_its_blob_with_the_same_public_key() {
        let (blob, public) = create_unprotected().expect("create");
        assert_eq!(public[0], 0x04);
        assert_eq!(public_key(&blob), Ok(public));
        let mut other = blob.clone();
        let last = other.len() - 1;
        other[last] ^= 0xff;
        assert!(public_key(&other).is_err());
    }

    #[test]
    #[ignore = "needs a Secure Enclave"]
    fn a_signature_verifies_only_for_the_signed_message() {
        let (blob, public) = create_unprotected().expect("create");
        let der = sign(&blob, "test", b"message").expect("sign");
        assert_eq!(der[0], 0x30, "DER sequence");
        assert_eq!(verify(&public, b"message", &der), Ok(true));
        assert_eq!(verify(&public, b"messagf", &der), Ok(false));
    }

    #[test]
    #[ignore = "needs a Secure Enclave"]
    fn probe_calls_a_key_without_an_owner_acl_weak() {
        let (blob, _) = create_unprotected().expect("create");
        assert_eq!(probe(&blob), Err(SeError::WeakKey));
    }

    #[test]
    #[ignore = "needs a Secure Enclave and the owner's own GUI session (not ssh)"]
    fn probe_accepts_a_biometry_key_without_asking() {
        let (blob, public) = create(Acl::BiometryCurrentSet).expect("create");
        assert_eq!(public_key(&blob), Ok(public));
        assert_eq!(probe(&blob), Ok(()));
    }
}
