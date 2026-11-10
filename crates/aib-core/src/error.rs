//! Stable error codes returned by the local API. The string form is a contract:
//! clients match on it, so a code is never renamed, only added.

use alloc::string::{String, ToString};
use core::fmt;
use core::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorCode {
    /// `Host` header is not one of the loopback names for our port.
    HostNotAllowed,
    /// `Origin` header present but not in the allow-list.
    OriginNotAllowed,
    /// No `Authorization: Bearer` header, or the token does not match.
    Unauthorized,
    /// A token was supplied in the URL query string (rejected even if correct).
    TokenInUrl,
    /// Unknown route.
    NotFound,
    /// Anything unexpected; details only in the server log.
    Internal,
    /// Request body or a field broke the contract (the message names the field, never its value).
    InvalidRequest,
    /// A session `env` key is not on the configured allow-list (`AIBD_SESSION_POLICY`), or
    /// is on the fixed deny list (`AIBD_*`, `DYLD_*`, `LD_*`, `PATH`) that no list overrides.
    EnvNotAllowed,
    /// Request body larger than the endpoint allows.
    PayloadTooLarge,
    /// No session with that id (or it fell out of the recent list).
    SessionNotFound,
    /// The running-session limit is reached. Since the session queue (ADR 0005 §5)
    /// a full limit queues instead; kept for clients that know the code.
    TooManySessions,
    /// The running limit is reached and the queue of waiting sessions is full too
    /// (ADR 0005 §5: the queue is bounded).
    SessionQueueFull,
    /// The program could not be started (not found, not executable, bad cwd).
    SpawnFailed,
    /// aibd is stopping and takes no new sessions.
    ShuttingDown,
    /// The route exists but not with this method.
    MethodNotAllowed,
    /// No seat with that id.
    SeatNotFound,
    /// This `Idempotency-Key` was already used on this route for a different request.
    IdempotencyConflict,
    /// A session's `argv[0]` is not a program the policy allows (`AIBD_SESSION_POLICY`;
    /// compared by real path; shells, interpreters and launchers never).
    ProgramNotAllowed,
    /// The listed provider is an npm (node) install; only standalone signed builds run.
    ProviderInstallUnsupported,
    /// The machine is too hot to start another session (ADR 0005 §1 thermal guard).
    /// Not queued; answered with `Retry-After`.
    ThermalNogo,
    /// A page credential that is not confirmed yet (ADR 0006 §7): only pair/status,
    /// pair/revoke and health answer until the short code is confirmed in the terminal.
    PairingUnconfirmed,
    /// A pairing code was redeemed a second time; the first redemption's credential is
    /// revoked too (ADR 0006 §7).
    PairingCodeReused,
    /// Too many failed pairing attempts in the window.
    PairingRateLimited,
    /// Pairing failed (unknown, expired or wrong code); deliberately says no more.
    PairingFailed,
    /// A page credential sent a write (anything but GET / HEAD, and pair/revoke). Until the
    /// launch-profile routes exist a page credential may only read (ADR 0006 §7; Q2).
    PageWriteNotAllowed,
    /// A non-API path on an aibd built without the web workbench (cargo feature `web`).
    WebNotBuilt,
}

impl ErrorCode {
    /// Every code once, in contract order (new codes are appended, never inserted). The
    /// length is written out because the array is public; `ordinal` and the check below
    /// keep it honest (a63ab 11-10: a merge once took two edits to `25` silently).
    pub const ALL: [ErrorCode; 26] = [
        ErrorCode::HostNotAllowed,
        ErrorCode::OriginNotAllowed,
        ErrorCode::Unauthorized,
        ErrorCode::TokenInUrl,
        ErrorCode::NotFound,
        ErrorCode::Internal,
        ErrorCode::InvalidRequest,
        ErrorCode::EnvNotAllowed,
        ErrorCode::PayloadTooLarge,
        ErrorCode::SessionNotFound,
        ErrorCode::TooManySessions,
        ErrorCode::SpawnFailed,
        ErrorCode::ShuttingDown,
        ErrorCode::MethodNotAllowed,
        ErrorCode::SeatNotFound,
        ErrorCode::IdempotencyConflict,
        ErrorCode::ProgramNotAllowed,
        ErrorCode::ProviderInstallUnsupported,
        ErrorCode::SessionQueueFull,
        ErrorCode::ThermalNogo,
        ErrorCode::PairingUnconfirmed,
        ErrorCode::PairingCodeReused,
        ErrorCode::PairingRateLimited,
        ErrorCode::PairingFailed,
        ErrorCode::PageWriteNotAllowed,
        ErrorCode::WebNotBuilt,
    ];

    /// Position of this code in [`ErrorCode::ALL`]. The match is exhaustive, so a new
    /// variant does not compile until it gets a number here: give it the next number and
    /// append it to `ALL`. A number outside `ALL` fails the build (`at`), and so does an
    /// `ALL` entry whose ordinal is not its position (the check after this `impl`).
    /// Not caught: a new variant given a number that another code already has.
    pub const fn ordinal(self) -> usize {
        match self {
            ErrorCode::HostNotAllowed => at::<0>(),
            ErrorCode::OriginNotAllowed => at::<1>(),
            ErrorCode::Unauthorized => at::<2>(),
            ErrorCode::TokenInUrl => at::<3>(),
            ErrorCode::NotFound => at::<4>(),
            ErrorCode::Internal => at::<5>(),
            ErrorCode::InvalidRequest => at::<6>(),
            ErrorCode::EnvNotAllowed => at::<7>(),
            ErrorCode::PayloadTooLarge => at::<8>(),
            ErrorCode::SessionNotFound => at::<9>(),
            ErrorCode::TooManySessions => at::<10>(),
            ErrorCode::SpawnFailed => at::<11>(),
            ErrorCode::ShuttingDown => at::<12>(),
            ErrorCode::MethodNotAllowed => at::<13>(),
            ErrorCode::SeatNotFound => at::<14>(),
            ErrorCode::IdempotencyConflict => at::<15>(),
            ErrorCode::ProgramNotAllowed => at::<16>(),
            ErrorCode::ProviderInstallUnsupported => at::<17>(),
            ErrorCode::SessionQueueFull => at::<18>(),
            ErrorCode::ThermalNogo => at::<19>(),
            ErrorCode::PairingUnconfirmed => at::<20>(),
            ErrorCode::PairingCodeReused => at::<21>(),
            ErrorCode::PairingRateLimited => at::<22>(),
            ErrorCode::PairingFailed => at::<23>(),
            ErrorCode::PageWriteNotAllowed => at::<24>(),
            ErrorCode::WebNotBuilt => at::<25>(),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::HostNotAllowed => "host_not_allowed",
            ErrorCode::OriginNotAllowed => "origin_not_allowed",
            ErrorCode::Unauthorized => "unauthorized",
            ErrorCode::TokenInUrl => "token_in_url",
            ErrorCode::NotFound => "not_found",
            ErrorCode::Internal => "internal",
            ErrorCode::InvalidRequest => "invalid_request",
            ErrorCode::EnvNotAllowed => "env_not_allowed",
            ErrorCode::PayloadTooLarge => "payload_too_large",
            ErrorCode::SessionNotFound => "session_not_found",
            ErrorCode::TooManySessions => "too_many_sessions",
            ErrorCode::SpawnFailed => "spawn_failed",
            ErrorCode::ShuttingDown => "shutting_down",
            ErrorCode::MethodNotAllowed => "method_not_allowed",
            ErrorCode::SeatNotFound => "seat_not_found",
            ErrorCode::IdempotencyConflict => "idempotency_conflict",
            ErrorCode::ProgramNotAllowed => "program_not_allowed",
            ErrorCode::ProviderInstallUnsupported => "provider_install_unsupported",
            ErrorCode::SessionQueueFull => "session_queue_full",
            ErrorCode::ThermalNogo => "thermal_nogo",
            ErrorCode::PairingUnconfirmed => "pairing_unconfirmed",
            ErrorCode::PairingCodeReused => "pairing_code_reused",
            ErrorCode::PairingRateLimited => "pairing_rate_limited",
            ErrorCode::PairingFailed => "pairing_failed",
            ErrorCode::PageWriteNotAllowed => "page_write_not_allowed",
            ErrorCode::WebNotBuilt => "web_not_built",
        }
    }

    /// HTTP status the API layer should use for this code.
    pub fn http_status(self) -> u16 {
        match self {
            ErrorCode::HostNotAllowed | ErrorCode::OriginNotAllowed => 403,
            ErrorCode::Unauthorized => 401,
            ErrorCode::TokenInUrl => 400,
            ErrorCode::NotFound => 404,
            ErrorCode::Internal => 500,
            ErrorCode::InvalidRequest | ErrorCode::EnvNotAllowed => 400,
            ErrorCode::PayloadTooLarge => 413,
            ErrorCode::SessionNotFound => 404,
            ErrorCode::TooManySessions | ErrorCode::SessionQueueFull => 429,
            ErrorCode::SpawnFailed => 422,
            ErrorCode::ShuttingDown | ErrorCode::ThermalNogo => 503,
            ErrorCode::MethodNotAllowed => 405,
            ErrorCode::SeatNotFound => 404,
            ErrorCode::IdempotencyConflict => 409,
            ErrorCode::ProgramNotAllowed | ErrorCode::ProviderInstallUnsupported => 403,
            ErrorCode::PairingUnconfirmed => 403,
            ErrorCode::PairingCodeReused => 409,
            ErrorCode::PairingRateLimited => 429,
            ErrorCode::PairingFailed => 401,
            ErrorCode::PageWriteNotAllowed => 403,
            ErrorCode::WebNotBuilt => 404,
        }
    }
}

/// An ordinal, checked at build time to point inside [`ErrorCode::ALL`].
const fn at<const N: usize>() -> usize {
    const { assert!(N < ErrorCode::ALL.len(), "ErrorCode ordinal is outside ALL") };
    N
}

/// Build-time check: `ALL[i]` has ordinal `i` for every `i`, so `ALL` holds no code twice,
/// is in contract order, and its length matches the ordinals it lists.
const _: () = {
    let mut i = 0;
    while i < ErrorCode::ALL.len() {
        assert!(
            ErrorCode::ALL[i].ordinal() == i,
            "ErrorCode::ALL is out of step with ErrorCode::ordinal"
        );
        i += 1;
    }
};

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
#[error("unknown error code: {0}")]
pub struct ErrorCodeParseError(pub String);

impl FromStr for ErrorCode {
    type Err = ErrorCodeParseError;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        ErrorCode::ALL
            .into_iter()
            .find(|c| c.as_str() == s)
            .ok_or_else(|| ErrorCodeParseError(s.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_round_trip_and_are_unique() {
        let mut seen = std::collections::HashSet::new(); // test-only std
        for c in ErrorCode::ALL {
            assert!(seen.insert(c.as_str()), "duplicate code {c}");
            assert_eq!(c.as_str().parse::<ErrorCode>(), Ok(c));
        }
        assert!("nope".parse::<ErrorCode>().is_err());
    }

    /// The order clients see is a contract: codes are only appended. Every variant must be
    /// in `ALL` exactly once and in this order.
    #[test]
    fn all_lists_every_code_once_in_contract_order() {
        const CONTRACT: [&str; 26] = [
            "host_not_allowed",
            "origin_not_allowed",
            "unauthorized",
            "token_in_url",
            "not_found",
            "internal",
            "invalid_request",
            "env_not_allowed",
            "payload_too_large",
            "session_not_found",
            "too_many_sessions",
            "spawn_failed",
            "shutting_down",
            "method_not_allowed",
            "seat_not_found",
            "idempotency_conflict",
            "program_not_allowed",
            "provider_install_unsupported",
            "session_queue_full",
            "thermal_nogo",
            "pairing_unconfirmed",
            "pairing_code_reused",
            "pairing_rate_limited",
            "pairing_failed",
            "page_write_not_allowed",
            "web_not_built",
        ];
        let got: std::vec::Vec<&str> = ErrorCode::ALL.iter().map(|c| c.as_str()).collect();
        assert_eq!(got, CONTRACT, "ALL differs from the contract list");
        for (i, c) in ErrorCode::ALL.iter().enumerate() {
            assert_eq!(c.ordinal(), i, "{c} sits at {i} but its ordinal says otherwise");
        }
    }
}
