//! Startup token: 32 bytes from the OS CSPRNG, base64url, new on every start.
//! Debug output never shows the value.

use base64::Engine;
use subtle::ConstantTimeEq;

#[derive(Clone)]
pub struct Token(String);

impl std::fmt::Debug for Token {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Token(<redacted>)")
    }
}

impl Token {
    pub fn generate() -> std::io::Result<Token> {
        let mut b = [0u8; 32];
        getrandom::fill(&mut b).map_err(std::io::Error::other)?;
        Ok(Token(
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(b),
        ))
    }

    #[doc(hidden)]
    pub fn from_test_value(s: &str) -> Token {
        Token(s.to_string())
    }

    pub fn expose(&self) -> &str {
        &self.0
    }

    pub fn matches(&self, presented: &str) -> bool {
        self.0.as_bytes().ct_eq(presented.as_bytes()).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_are_random_and_redacted() {
        let a = Token::generate().unwrap();
        let b = Token::generate().unwrap();
        assert_ne!(a.expose(), b.expose());
        assert_eq!(a.expose().len(), 43);
        assert!(a.matches(a.expose()));
        assert!(!a.matches(b.expose()));
        assert!(!a.matches(""));
        assert_eq!(format!("{a:?}"), "Token(<redacted>)");
    }

    /// Timing is not measurable reliably here; the rule is checked on the source.
    #[test]
    fn start_up_token_is_compared_in_constant_time() {
        let src = include_str!("token.rs");
        let body = src
            .split("pub fn matches(")
            .nth(1)
            .and_then(|s| s.split("#[cfg(test)]").next())
            .unwrap();
        assert!(body.contains(".ct_eq(presented.as_bytes())"));
        assert!(!body.contains("=="));
    }
}
