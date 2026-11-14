//! Small helpers that make Rust read text the way the 1.x Python did.

use serde_json::Value;

/// Python `str.isspace()` for one character: Unicode White_Space plus the four
/// information separators U+001C..U+001F, which Python also counts as space.
pub fn py_isspace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `str.strip()` with no argument.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// Python `s[:n]` on a str: the first `n` code points.
pub fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python's `\w` for one character (`str.isalnum()` or `_`); `char::is_alphanumeric` is
/// the closest Rust has.
pub fn py_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Python truthiness of a JSON value (`if item.get(k):`).
pub fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `item.get(k) is None`: the key is missing or holds JSON null.
pub fn is_none(v: Option<&Value>) -> bool {
    matches!(v, None | Some(Value::Null))
}

/// Python `int(text)` for a str: surrounding space, an optional sign, ASCII digits with
/// single `_` between digits. Python also takes other Unicode digits; this does not, so a
/// rev written with them is refused as a mismatch (fail-closed, never a wrong match).
pub fn py_int(text: &str) -> Option<i64> {
    let t = py_strip(text);
    let (neg, body) = match t.as_bytes().first() {
        Some(b'+') => (false, &t[1..]),
        Some(b'-') => (true, &t[1..]),
        _ => (false, t),
    };
    if body.is_empty() || body.starts_with('_') || body.ends_with('_') || body.contains("__") {
        return None;
    }
    let mut n: i64 = 0;
    for b in body.bytes() {
        match b {
            b'_' => continue,
            b'0'..=b'9' => n = n.checked_mul(10)?.checked_add(i64::from(b - b'0'))?,
            _ => return None,
        }
    }
    Some(if neg { -n } else { n })
}
