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
}

impl ErrorCode {
    pub const ALL: [ErrorCode; 25] = [
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
    ];

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
        }
    }
}

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
}
