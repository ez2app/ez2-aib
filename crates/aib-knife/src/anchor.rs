//! The anchor: `old` must occur in the file exactly once, or the knife is
//! ANCHOR-MISSING and the file is not touched (a knife whose anchor drifted away proves
//! nothing; a63ab outline r2, P2-7). Counting is non-overlapping, as Python's `str.count`.
//!
//! Restoring writes the original text back (never cp / mv: an old mtime makes cargo reuse
//! the knifed build) and the file must then read back byte-identical, or the whole run
//! stops (design §7.1 step 2). The file side lives in the runner; this is the pure part.

/// The knifed text, or `Err(count)` when `old` does not occur exactly once.
pub fn apply(text: &str, old: &str, new: &str) -> Result<String, usize> {
    if old.is_empty() {
        return Err(0);
    }
    match text.matches(old).count() {
        1 => Ok(text.replacen(old, new, 1)),
        n => Err(n),
    }
}

/// The restore is good only when the bytes read back equal the original bytes.
pub fn restored(original: &[u8], read_back: &[u8]) -> bool {
    original == read_back
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exactly_once_is_applied() {
        assert_eq!(
            apply("a\nkill();\nb\n", "kill();\n", ""),
            Ok("a\nb\n".to_string())
        );
    }

    #[test]
    fn missing_or_twice_is_refused_with_the_count() {
        assert_eq!(apply("a\n", "kill();", ""), Err(0));
        assert_eq!(apply("kill(); kill();", "kill();", ""), Err(2));
        assert_eq!(apply("aaaa", "aa", "b"), Err(2)); // non-overlapping, as str.count
        assert_eq!(apply("x", "", "y"), Err(0));
    }

    #[test]
    fn restore_check_is_byte_exact() {
        assert!(restored(b"a\r\n", b"a\r\n"));
        assert!(!restored(b"a\r\n", b"a\n"));
        assert!(!restored(b"a", b"a "));
    }
}
