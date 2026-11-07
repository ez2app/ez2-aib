//! Stable error codes returned by the local API. The string form is a contract:
//! clients match on it, so a code is never renamed, only added.

use std::fmt;
use std::str::FromStr;

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
}

impl ErrorCode {
    pub const ALL: [ErrorCode; 6] = [
        ErrorCode::HostNotAllowed,
        ErrorCode::OriginNotAllowed,
        ErrorCode::Unauthorized,
        ErrorCode::TokenInUrl,
        ErrorCode::NotFound,
        ErrorCode::Internal,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::HostNotAllowed => "host_not_allowed",
            ErrorCode::OriginNotAllowed => "origin_not_allowed",
            ErrorCode::Unauthorized => "unauthorized",
            ErrorCode::TokenInUrl => "token_in_url",
            ErrorCode::NotFound => "not_found",
            ErrorCode::Internal => "internal",
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
        let mut seen = std::collections::HashSet::new();
        for c in ErrorCode::ALL {
            assert!(seen.insert(c.as_str()), "duplicate code {c}");
            assert_eq!(c.as_str().parse::<ErrorCode>(), Ok(c));
        }
        assert!("nope".parse::<ErrorCode>().is_err());
    }
}
