//! The gate is a pure function (PRDRAFT R4 §5 item 3, ledger checklist L12):
//! no file, process, thread, clock, channel or network in the code outside
//! its unit tests; aib-policy the same.

const BANNED: [&str; 9] = [
    "std::fs",
    "std::process",
    "std::thread",
    "std::net",
    "std::sync::mpsc",
    "SystemTime::now",
    "Instant::now",
    "std::env",
    "Command::new",
];

fn non_test_part(src: &str) -> &str {
    src.split("#[cfg(test)]").next().unwrap_or(src)
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
