//! The gate is a pure function (PRDRAFT R4 §5 item 3, ledger checklist L12):
//! no file, process, thread, clock, channel or network in the code outside
//! its unit tests; aib-policy the same.

const BANNED: [&str; 15] = [
    "std::fs",
    "std::process",
    "std::thread",
    "std::net",
    "std::sync::mpsc",
    "SystemTime::now",
    "Instant::now",
    "std::env",
    "Command::new",
    // grouped or re-rooted imports name the module without `std::` in front (1ced7 R4 L4)
    "std::{",
    "fs::",
    "process::",
    "thread::",
    "net::",
    "env::",
];

/// The source with every `#[cfg(test)]` item cut out, wherever it sits: the item runs to the
/// first `}` alone on a line at column 0 (rustfmt writes every top-level item that way, and
/// CI checks fmt). Code written after the test module is still checked (1ced7 R4 L4).
fn non_test_part(src: &str) -> String {
    let mut out = String::new();
    let mut rest = src;
    while let Some(i) = rest.find("#[cfg(test)]") {
        out.push_str(&rest[..i]);
        let item = &rest[i..];
        rest = match item.find("\n}\n") {
            Some(j) => &item[j + 3..],
            None => "",
        };
    }
    out.push_str(rest);
    out
}

fn check(name: &str, src: &str) {
    let code = non_test_part(src);
    for b in BANNED {
        assert!(!code.contains(b), "reason: impure_{name} uses {b}");
    }
}

#[test]
fn the_gate_reads_nothing_and_starts_nothing() {
    check("gate", include_str!("../src/gate.rs"));
    check("reflect_lib", include_str!("../src/lib.rs"));
    check("policy", include_str!("../../aib-policy/src/lib.rs"));
}

#[test]
fn the_check_sees_grouped_imports_and_code_after_the_tests() {
    let grouped = "use std::{fs, process};\nfn f() {}\n";
    assert!(
        non_test_part(grouped).contains("std::{"),
        "reason: grouped_import_missed"
    );
    let after = "fn a() {}\n#[cfg(test)]\nmod tests {\n    fn t() {\n        let _ = 1;\n    }\n}\nfn b() { std::fs::read(\"x\"); }\n";
    let code = non_test_part(after);
    assert!(
        code.contains("std::fs"),
        "reason: code_after_tests_unchecked"
    );
    assert!(!code.contains("let _ = 1"), "reason: test_module_not_cut");
}
