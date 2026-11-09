//! An ADR that points to one of its own sections by title ("see \"Update …\"") must
//! name a section that exists, word for word. Titles are long and a reference that
//! drops the tail cannot be searched (a9136 11-09 on ADR 0004; the same file already
//! had one such broken reference).
//!
//! Rule checked: in `docs/adr/*.md`, every double-quoted span that starts with
//! `Update ` equals the text of a `## ` heading in the same file, after line breaks
//! and runs of spaces inside the quote are folded to one space.

use std::path::{Path, PathBuf};

fn adr_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../docs/adr")
}

fn fold(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Every `"Update …"` span in `text`, folded.
fn update_refs(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(i) = rest.find("\"Update ") {
        let after = &rest[i + 1..];
        match after.find('"') {
            Some(j) => {
                out.push(fold(&after[..j]));
                rest = &after[j + 1..];
            }
            None => break,
        }
    }
    out
}

fn headings(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| l.strip_prefix("## "))
        .map(fold)
        .collect()
}

fn broken(text: &str) -> Vec<String> {
    let hs = headings(text);
    update_refs(text)
        .into_iter()
        .filter(|r| !hs.contains(r))
        .collect()
}

#[test]
fn update_references_name_a_section_of_the_same_adr() {
    let mut bad = Vec::new();
    let mut files = 0;
    for e in std::fs::read_dir(adr_dir()).expect("docs/adr") {
        let p = e.unwrap().path();
        if p.extension().is_some_and(|x| x == "md") {
            files += 1;
            let text = std::fs::read_to_string(&p).unwrap();
            for r in broken(&text) {
                bad.push(format!(
                    "{}: \"{r}\"",
                    p.file_name().unwrap().to_string_lossy()
                ));
            }
        }
    }
    assert!(files > 0, "no ADR found under {}", adr_dir().display());
    assert!(
        bad.is_empty(),
        "references to a missing section:\n{}",
        bad.join("\n")
    );
}

#[test]
fn the_check_catches_a_cut_title_and_accepts_a_wrapped_one() {
    let doc = "## Update 2026-11-07 (performance budget, ADR 0005)\n\
               text (see \"Update 2026-11-07 (performance budget)\").\n\
               more (see \"Update 2026-11-07\n    (performance budget, ADR 0005)\").\n";
    assert_eq!(broken(doc), ["Update 2026-11-07 (performance budget)"]);
    assert!(broken("no references here\n## Update x\n").is_empty());
}
