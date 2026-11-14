//! The native helper's exit codes and the 1.x result words.
//!
//! The macOS helper (aib/src/cockpit/sehelper/aib-sehelper.swift) prints one code
//! on stderr and exits with a class number. [`HelperOutcome::from_exit`] reads that
//! back. A code that does not belong to its class, an unknown class, or a missing
//! code is [`HelperOutcome::Broken`] with `helper_failed`: never "approved", and
//! never "the person said no" (which a caller might show as a plain cancel).
//!
//! The 1.x words are kept verbatim ([`LA_CODES`], [`CONSENT_RESULTS`]): callers,
//! skills and seat notes match on them.

/// rc 2: the request was wrong, nothing was asked.
pub const RC2_CODES: &[&str] = &[
    "usage",
    "bad_arg",
    "bad_name",
    "bad_dir",
    "bad_secret",
    "bad_data",
    "not_found",
    "corrupt",
    "exists",
    "no_reason",
    "reason_too_long",
    "reason_mismatch",
    "bad_time",
    "no_se",
    "random",
    "write_failed",
    "access_control",
];
/// rc 3: the person did not approve (or could not).
pub const RC3_CODES: &[&str] = &["cancel", "no_biometry", "auth_failed"];
/// rc 4: the secure hardware failed.
pub const RC4_CODES: &[&str] = &["se_error"];
/// rc 5: a signature did not verify.
pub const RC5_CODES: &[&str] = &["bad_sig"];
/// rc 6: the key does not require the owner (or could not be probed).
pub const RC6_CODES: &[&str] = &["weak_key", "key_probe_failed"];

/// What one helper call came to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HelperOutcome {
    /// rc 0. The caller still checks the output (signature, nonce) itself.
    Ok,
    /// rc 2: refused before asking.
    Refused(String),
    /// rc 3: the person did not approve.
    NotApproved(String),
    /// rc 4 / 5 / 6 or anything unreadable: the helper, the key or the hardware is
    /// not in a state to prove anything.
    Broken(String),
}

impl HelperOutcome {
    /// Read an exit status and stderr. `None` for `rc` means the helper did not exit
    /// by itself (killed, timed out).
    pub fn from_exit(rc: Option<i32>, stderr: &str) -> HelperOutcome {
        let code = stderr.lines().next().unwrap_or("").trim();
        let in_set = |set: &[&str]| set.contains(&code);
        match rc {
            Some(0) => HelperOutcome::Ok,
            Some(2) if in_set(RC2_CODES) => HelperOutcome::Refused(code.to_owned()),
            Some(3) if in_set(RC3_CODES) => HelperOutcome::NotApproved(code.to_owned()),
            Some(4) if in_set(RC4_CODES) => HelperOutcome::Broken(code.to_owned()),
            Some(5) if in_set(RC5_CODES) => HelperOutcome::Broken(code.to_owned()),
            Some(6) if in_set(RC6_CODES) => HelperOutcome::Broken(code.to_owned()),
            _ => HelperOutcome::Broken("helper_failed".to_owned()),
        }
    }

    pub fn is_approved(&self) -> bool {
        matches!(self, HelperOutcome::Ok)
    }
}

/// 1.x `touchid_approve.LA_CODES`: LAError code to `why`. Any other code is
/// `failed:<code>`.
pub const LA_CODES: &[(&str, &str)] = &[
    ("-1", "failed"),
    ("-2", "cancel"),
    ("-3", "fallback"),
    ("-4", "system_cancel"),
    ("-5", "no_passcode"),
    ("-6", "no_biometry"),
    ("-8", "lockout"),
    ("-9", "timeout"),
];

/// The 1.x `why` for an LAError code.
pub fn la_why(code: &str) -> String {
    LA_CODES
        .iter()
        .find(|(c, _)| *c == code)
        .map(|(_, w)| (*w).to_owned())
        .unwrap_or_else(|| format!("failed:{code}"))
}

/// 1.x `consent.RESULTS`, plus the two status-only words.
pub const CONSENT_RESULTS: &[&str] = &[
    "ok",
    "rc_nonzero",
    "stopped",
    "denied",
    "not_allowed",
    "unavailable",
    "failed",
    "timeout",
    "expired",
    "invalid",
];
pub const CONSENT_STATUS_ONLY: &[&str] = &["pending", "unknown"];

/// 1.x `consent.decide`: a window that was not approved (no press, cancel, timeout,
/// failed fingerprint) is `result: denied`; the reason goes in `why`.
pub fn consent_result_for_not_approved() -> &'static str {
    "denied"
}
