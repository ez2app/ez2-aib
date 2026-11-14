//! The field table of crates/aib-slice/IFACE.md (`## 欄位表`, one field per line) and
//! schema/sweep.v1.json name the same fields (519d0 / Andy 11-14): readers code against the
//! document, so a field in one and not the other is red.

use aib_slice::SCHEMA;
use serde_json::Value;
use std::collections::BTreeSet;

/// Every property path of the schema: `a`, `a.b`, `a[].b` for array items.
fn schema_paths(s: &Value, prefix: &str, out: &mut BTreeSet<String>) {
    let Some(props) = s.get("properties").and_then(Value::as_object) else {
        return;
    };
    for (k, v) in props {
        let p = if prefix.is_empty() {
            k.clone()
        } else {
            format!("{prefix}.{k}")
        };
        out.insert(p.clone());
        schema_paths(v, &p, out);
        if let Some(items) = v.get("items") {
            schema_paths(items, &format!("{p}[]"), out);
        }
    }
}

/// The `- \`path\`` lines under `## 欄位表`, up to the next `## `.
fn doc_paths(doc: &str) -> BTreeSet<String> {
    doc.lines()
        .skip_while(|l| l.trim() != "## 欄位表")
        .skip(1)
        .take_while(|l| !l.starts_with("## "))
        .filter_map(|l| l.strip_prefix("- `"))
        .filter_map(|l| l.split_once('`').map(|(p, _)| p.to_owned()))
        .collect()
}

#[test]
fn the_document_and_the_schema_name_the_same_fields() {
    let schema: Value = serde_json::from_str(SCHEMA).expect("schema parses");
    let mut want = BTreeSet::new();
    schema_paths(&schema, "", &mut want);
    let doc = include_str!("../IFACE.md");
    let got = doc_paths(doc);
    assert!(
        got.len() > 60,
        "the field table was not found: {}",
        got.len()
    );
    let only_schema: Vec<_> = want.difference(&got).collect();
    let only_doc: Vec<_> = got.difference(&want).collect();
    assert!(
        only_schema.is_empty() && only_doc.is_empty(),
        "only in schema: {only_schema:?}; only in IFACE.md: {only_doc:?}"
    );
}

#[test]
fn the_table_reader_stops_at_the_next_section() {
    let doc = "# x\n- `no`\n## 欄位表\n- `a` — x\n- `a.b`\ntext\n## 不做\n- `c`\n";
    let got: Vec<String> = doc_paths(doc).into_iter().collect();
    assert_eq!(got, vec!["a", "a.b"]);
}
