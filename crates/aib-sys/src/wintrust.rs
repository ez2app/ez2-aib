//! Windows Authenticode: is this file signed through a trusted root, and which organisation
//! signed it (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S, r1-2).
//!
//! The file is checked through a handle the caller already holds, so the caller can keep it
//! open with read-only sharing from the hash to the signature to the copy: nothing of the
//! same account can write it in between (§3.3).
//!
//! Every `unsafe` block is one FFI call or one pointer read with its own SAFETY note.
//!
//! [`signed_by_org`] has the shape of L1a's `provider_fetch::Signer` (`Fn(&Path, &File)`), so
//! L2's `signed_by_maker` (`provider_fetch::maker`) passes it to the download and `verify_placed`.
//!
//! UNVERIFIED (not measured on a Windows machine; the Mac only type-checks it through
//! `scripts/dev/wincheck.sh`; the tests below run on CI-windows): the real claude.exe result
//! (is its leaf O exactly "Anthropic, PBC"?), and what an offline machine gets with the
//! whole-chain revocation check.

use std::ffi::c_void;
use std::fs::File;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::AsRawHandle;
use std::os::windows::io::RawHandle;
use std::path::Path;
use std::ptr::null_mut;
use windows_sys::Win32::Foundation::{HANDLE, HWND, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Security::Cryptography::{
    szOID_ORGANIZATION_NAME, CertGetNameStringW, CERT_NAME_ATTR_TYPE,
};
use windows_sys::Win32::Security::WinTrust::{
    WTHelperGetProvCertFromChain, WTHelperGetProvSignerFromChain, WTHelperProvDataFromStateData,
    WinVerifyTrust, WINTRUST_ACTION_GENERIC_VERIFY_V2, WINTRUST_DATA, WINTRUST_DATA_0,
    WINTRUST_FILE_INFO, WTD_CHOICE_FILE, WTD_REVOCATION_CHECK_CHAIN, WTD_REVOKE_NONE,
    WTD_REVOKE_WHOLECHAIN, WTD_STATEACTION_CLOSE, WTD_STATEACTION_VERIFY, WTD_UI_NONE,
};

/// How far the revocation check goes. Written out at every call (r1-2): the download is
/// checked against the whole chain, a seat start against nothing (the hash is the main check
/// there).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Revocation {
    WholeChain,
    None,
}

/// What Windows said about the file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Trust {
    /// Signed through a trusted root; `org` is the leaf certificate's subject O.
    Signed { org: String },
    /// `WinVerifyTrust` refused it; the value is its result code.
    Refused(i32),
    /// Signed and trusted, but the signer's organisation could not be read.
    NoOrg,
}

/// Check `path` (open as `file`) with `WINTRUST_ACTION_GENERIC_VERIFY_V2`, no UI.
pub fn verify(path: &Path, file: RawHandle, revocation: Revocation) -> Trust {
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut info = WINTRUST_FILE_INFO {
        cbStruct: size_of_u32::<WINTRUST_FILE_INFO>(),
        pcwszFilePath: wide.as_ptr(),
        hFile: file as HANDLE,
        pgKnownSubject: null_mut(),
    };
    let mut data = WINTRUST_DATA {
        cbStruct: size_of_u32::<WINTRUST_DATA>(),
        dwUIChoice: WTD_UI_NONE,
        fdwRevocationChecks: match revocation {
            Revocation::WholeChain => WTD_REVOKE_WHOLECHAIN,
            Revocation::None => WTD_REVOKE_NONE,
        },
        dwUnionChoice: WTD_CHOICE_FILE,
        Anonymous: WINTRUST_DATA_0 { pFile: &mut info },
        dwStateAction: WTD_STATEACTION_VERIFY,
        dwProvFlags: match revocation {
            Revocation::WholeChain => WTD_REVOCATION_CHECK_CHAIN,
            Revocation::None => 0,
        },
        ..Default::default()
    };
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    let no_window = INVALID_HANDLE_VALUE as HWND;
    // SAFETY: `data` and `info` are fully set up above and outlive the call; `wide` is a
    // NUL-terminated path that also outlives it; `file` is the caller's open handle.
    let rc = unsafe {
        WinVerifyTrust(
            no_window,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    let out = if rc != 0 {
        Trust::Refused(rc)
    } else {
        match leaf_org(data.hWVTStateData) {
            Some(org) => Trust::Signed { org },
            None => Trust::NoOrg,
        }
    };
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    // SAFETY: the same `data` (now asking to close) releases the state the first call kept.
    let _ = unsafe {
        WinVerifyTrust(
            no_window,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    out
}

/// S (IFACE §3.2) for one expected organisation, in L1a's `Signer` shape: `Ok` only when
/// Windows trusts the signature and the leaf's O is exactly `org`. The error words are fixed;
/// nothing read from the file reaches them.
pub fn signed_by_org(
    path: &Path,
    file: &File,
    org: &str,
    revocation: Revocation,
) -> Result<(), &'static str> {
    match verify(path, file.as_raw_handle(), revocation) {
        Trust::Signed { org: got } if got == org => Ok(()),
        Trust::Signed { .. } => Err("other_publisher"),
        Trust::NoOrg => Err("publisher_unreadable"),
        Trust::Refused(_) => Err("not_trusted"),
    }
}

/// The subject O of the first signer's leaf certificate.
fn leaf_org(state: HANDLE) -> Option<String> {
    if state.is_null() {
        return None;
    }
    // SAFETY: `state` is the hWVTStateData a successful verify left open; not closed yet.
    let prov = unsafe { WTHelperProvDataFromStateData(state) };
    if prov.is_null() {
        return None;
    }
    // SAFETY: `prov` came from the open state above; signer 0, not a counter-signer.
    let sgnr = unsafe { WTHelperGetProvSignerFromChain(prov, 0, 0, 0) };
    if sgnr.is_null() {
        return None;
    }
    // SAFETY: `sgnr` points into the open provider data; certificate 0 is the leaf.
    let cert = unsafe { WTHelperGetProvCertFromChain(sgnr, 0) };
    if cert.is_null() {
        return None;
    }
    // SAFETY: `cert` is non-null and points into the open provider data.
    let ctx = unsafe { (*cert).pCert };
    if ctx.is_null() {
        return None;
    }
    let oid: *const c_void = szOID_ORGANIZATION_NAME.cast();
    // SAFETY: asking only for the length (null buffer, size 0) of the subject O.
    let len = unsafe { CertGetNameStringW(ctx, CERT_NAME_ATTR_TYPE, 0, oid, null_mut(), 0) };
    if len <= 1 {
        return None;
    }
    let mut buf = vec![0u16; len as usize];
    // SAFETY: `buf` has exactly `len` u16 slots, the length the call above returned.
    let got =
        unsafe { CertGetNameStringW(ctx, CERT_NAME_ATTR_TYPE, 0, oid, buf.as_mut_ptr(), len) };
    if got <= 1 {
        return None;
    }
    let text = String::from_utf16(&buf[..(got as usize - 1)]).ok()?;
    Some(text)
}

fn size_of_u32<T>() -> u32 {
    u32::try_from(std::mem::size_of::<T>()).unwrap_or(u32::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_file(bytes: &[u8]) -> (std::path::PathBuf, File) {
        let mut name = [0u8; 8];
        let _ = getrandom_like(&mut name);
        let p = std::env::temp_dir().join(format!(
            "aib-wintrust-{}-{}.exe",
            std::process::id(),
            name.iter().map(|b| format!("{b:02x}")).collect::<String>()
        ));
        std::fs::write(&p, bytes).expect("write");
        let f = File::open(&p).expect("open");
        (p, f)
    }

    // the test only needs a name no other run uses at the same time
    fn getrandom_like(out: &mut [u8]) -> Option<()> {
        let t = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .ok()?
            .as_nanos();
        for (i, b) in out.iter_mut().enumerate() {
            *b = (t >> (i * 8)) as u8;
        }
        Some(())
    }

    #[test]
    fn an_unsigned_file_is_not_trusted() {
        let (p, f) = temp_file(b"MZ not a signed program");
        for rev in [Revocation::WholeChain, Revocation::None] {
            assert!(
                matches!(verify(&p, f.as_raw_handle(), rev), Trust::Refused(_)),
                "reason: unsigned_trusted"
            );
            assert_eq!(
                signed_by_org(&p, &f, "Anthropic, PBC", rev),
                Err("not_trusted")
            );
        }
        drop(f);
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn an_empty_org_never_matches_an_unsigned_file() {
        let (p, f) = temp_file(b"");
        assert_eq!(
            signed_by_org(&p, &f, "", Revocation::None),
            Err("not_trusted")
        );
        drop(f);
        let _ = std::fs::remove_file(&p);
    }
}
