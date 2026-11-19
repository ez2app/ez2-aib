#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! The built-in page lives under `PAGE_CSP` (`style-src 'self'`): a `style` attribute in markup
//! and a `<style>` block are dropped by the browser without a word, so animation delays,
//! progress widths and line colours silently did nothing for a whole evening (043dd 11-18,
//! 0eed7 VERIFY-cardview). The page writes `data-sty` and applies it through CSSOM instead;
//! this keeps any file in `apps/web/dist` from bringing the attribute back.

use std::path::{Path, PathBuf};

fn dist() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/web/dist")
}

fn page_files() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dist())
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| matches!(p.extension().and_then(|x| x.to_str()), Some("js" | "html")))
        .collect();
    out.sort();
    out
}

#[test]
fn the_policy_still_refuses_inline_styles() {
    assert!(aibd::static_files::PAGE_CSP.contains("style-src 'self'"));
    assert!(!aibd::static_files::PAGE_CSP.contains("unsafe-inline"));
}

#[test]
fn no_page_file_writes_a_style_attribute_or_block() {
    let files = page_files();
    assert!(
        !files.is_empty(),
        "no page files under {}",
        dist().display()
    );
    let mut bad = Vec::new();
    for f in &files {
        let text = std::fs::read_to_string(f).unwrap();
        for (i, line) in text.lines().enumerate() {
            let l = line.to_ascii_lowercase();
            if l.contains(" style=\"") || l.contains(" style='") || l.contains("<style") {
                bad.push(format!(
                    "{}:{}",
                    f.file_name().unwrap().to_string_lossy(),
                    i + 1
                ));
            }
        }
    }
    assert!(
        bad.is_empty(),
        "inline style under style-src 'self' (write data-sty): {bad:?}"
    );
}
