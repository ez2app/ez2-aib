//! AP26 W-4 (PRE-READ-w4-winsys-features-c2f0b-1125; 3e53b condition 2): screen capture,
//! synthetic input and the CredUI window are what a same-account program can already do. The
//! owner-present measurements use them to see whether our windows hold; aibd itself never
//! does. So no non-test source in the workspace's crates names them: only
//! `hello_measure.rs`, which `lib.rs` compiles for tests alone. The names are spelled in two
//! halves here so this file does not name them itself.

use std::path::{Path, PathBuf};

/// The calls only the measurement tests may name.
fn names() -> Vec<String> {
    [
        ("Send", "Input"),
        ("keybd", "_event"),
        ("mouse", "_event"),
        ("Bit", "Blt"),
        ("GetDI", "Bits"),
        ("Print", "Window"),
        ("CredUIPromptFor", "WindowsCredentials"),
        ("CredUnPack", "AuthenticationBuffer"),
    ]
    .iter()
    .map(|(a, b)| format!("{a}{b}"))
    .collect()
}

/// The one file that may name them (compiled for tests only).
fn is_measure_file(p: &Path) -> bool {
    p.ends_with(Path::new("aib-sys").join("src").join("hello_measure.rs"))
}

fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            rs_files(&p, out);
        } else if p.extension().is_some_and(|x| x == "rs") {
            out.push(p);
        }
    }
}

/// `"<file>: <name>"` for every listed name in `crates/*/src/**/*.rs` outside the
/// measurement file.
fn named_outside(crates: &Path) -> Vec<String> {
    let names = names();
    let mut files = Vec::new();
    for c in std::fs::read_dir(crates).into_iter().flatten().flatten() {
        rs_files(&c.path().join("src"), &mut files);
    }
    let mut hits = Vec::new();
    for f in files {
        if is_measure_file(&f) {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&f) else {
            continue;
        };
        for n in &names {
            if text.contains(n.as_str()) {
                hits.push(format!("{}: {n}", f.display()));
            }
        }
    }
    hits
}

#[test]
fn only_the_measurement_tests_name_capture_input_and_credui_calls() {
    let crates = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    assert_eq!(named_outside(&crates), Vec::<String>::new());
    let lib = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/lib.rs"))
        .unwrap_or_default();
    assert!(
        lib.contains("#[cfg(test)]\nmod hello_measure;"),
        "reason: measure_file_is_test_only"
    );
}

#[test]
fn the_guard_sees_a_name_planted_anywhere_but_the_measure_file() {
    let root = std::env::temp_dir().join(format!("aib-sys-guard-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let planted = root.join("x").join("src").join("deep");
    std::fs::create_dir_all(&planted).unwrap_or_default();
    let measure = root.join("aib-sys").join("src");
    std::fs::create_dir_all(&measure).unwrap_or_default();
    let n = &names()[0];
    std::fs::write(measure.join("hello_measure.rs"), n).unwrap_or_default();
    assert!(
        named_outside(&root).is_empty(),
        "reason: measure_file_allowed"
    );
    std::fs::write(planted.join("a.rs"), format!("// {n}")).unwrap_or_default();
    let hits = named_outside(&root);
    let _ = std::fs::remove_dir_all(&root);
    assert_eq!(hits.len(), 1, "reason: planted_found {hits:?}");
}
