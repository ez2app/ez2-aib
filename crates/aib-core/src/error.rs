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
    /// A session `env` key is on the deny list (`AIBD_*`, `DYLD_*`, `LD_*`, `PATH`).
    EnvNotAllowed,
    /// Request body larger than the endpoint allows.
    PayloadTooLarge,
    /// No session with that id (or it fell out of the recent list).
    SessionNotFound,
    /// The running-session limit is reached.
    TooManySessions,
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
}

impl ErrorCode {
    pub const ALL: [ErrorCode; 16] = [
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
            ErrorCode::TooManySessions => 429,
            ErrorCode::SpawnFailed => 422,
            ErrorCode::ShuttingDown => 503,
            ErrorCode::MethodNotAllowed => 405,
            ErrorCode::SeatNotFound => 404,
            ErrorCode::IdempotencyConflict => 409,
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
