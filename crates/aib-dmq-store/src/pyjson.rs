//! JSON text the way the frozen Python writes it: `json.dumps(v, ensure_ascii=False,
//! sort_keys=True)`.
//!
//! The byte form matters, not only the value: 1.x readers match strings in these files
//! (power_tasks.py looks for `"to": "done"` in audit.jsonl), so Rust lines have to carry
//! Python's separators `", "` and `": "`. Keys come out sorted because `serde_json::Map`
//! is a BTreeMap here (no `preserve_order` in the workspace); byte order of UTF-8 keys is
//! code point order, the order `sort_keys` uses. Non-ASCII stays as is (ensure_ascii=False);
//! control characters are escaped by serde_json as Python does.

use serde_json::ser::Formatter;
use serde_json::Value;
use std::io;

struct PyFormatter;

impl Formatter for PyFormatter {
    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }
}

/// `json.dumps(v, ensure_ascii=False, sort_keys=True)` (no trailing newline).
pub fn dumps(v: &Value) -> Result<String, serde_json::Error> {
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, PyFormatter);
    serde::Serialize::serialize(v, &mut ser)?;
    // serde_json only writes valid UTF-8
    Ok(String::from_utf8_lossy(&out).into_owned())
}

#[cfg(test)]
mod tests {
    use super::dumps;
    use serde_json::json;

    #[test]
    fn same_bytes_as_python_json_dumps() {
        // python3 -c 'import json; print(json.dumps({...}, ensure_ascii=False, sort_keys=True))'
        let v = json!({"to": "done", "b": [1, 2, {"z": null, "a": true}], "a": "席\n", "e": [], "o": {}});
        assert_eq!(
            dumps(&v).expect("dumps"),
            r#"{"a": "席\n", "b": [1, 2, {"a": true, "z": null}], "e": [], "o": {}, "to": "done"}"#
        );
    }
}
