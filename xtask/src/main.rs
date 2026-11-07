//! Developer checks for the AIB 2.0 workspace. Not shipped.
//!
//! `aib-xtask purity <crate-dir>` checks aib-core's source on the syntax tree
//! (ADR 0001, review by @3f312 on 11-07: a line-based check was fooled by
//! `/* #[cfg(test)] */ extern crate std as sneaky;`). Comments do not exist
//! in a token stream, so they cannot disguise anything here.
//!
//! Rules, each with its own reason string (the self-test requires the right
//! reason, not just a failure):
//!
//! - `lib.rs` keeps `#![no_std]` and `#![forbid(unsafe_code)]`.
//! - `extern crate std` only as an item whose cfg is exactly `#[cfg(test)]`.
//!   Every `extern crate std` token sequence in a file (including macro
//!   bodies) must be one of those items, so a macro cannot emit it.
//! - cfg predicates (`#[cfg]`, `#[cfg_attr]`, `cfg!`, also inside macros) may
//!   only use `test`, `debug_assertions`, `doc` and `feature`. Anything else
//!   (`target_*`, `unix`, `windows`, `panic`, …) can differ between the host
//!   and another build and is refused.
//! - no `include!` and no `#[path]`: both compile code the walk over `src/`
//!   would not see.

use proc_macro2::{Delimiter, TokenStream, TokenTree};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const ALLOWED_CFG: &[&str] = &["test", "debug_assertions", "doc", "feature"];
const CFG_COMBINATORS: &[&str] = &["any", "all", "not"];

fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    for e in std::fs::read_dir(dir)? {
        let p = e?.path();
        if p.is_dir() {
            rs_files(&p, out)?;
        } else if p.extension().is_some_and(|x| x == "rs") {
            out.push(p);
        }
    }
    Ok(())
}

fn ident_is(t: Option<&TokenTree>, s: &str) -> bool {
    matches!(t, Some(TokenTree::Ident(i)) if i == s)
}

/// Idents of a cfg predicate that are not allowed.
fn bad_cfg_idents(pred: TokenStream, out: &mut Vec<String>) {
    for t in pred {
        match t {
            TokenTree::Ident(i) => {
                let s = i.to_string();
                if !ALLOWED_CFG.contains(&s.as_str()) && !CFG_COMBINATORS.contains(&s.as_str()) {
                    out.push(s);
                }
            }
            TokenTree::Group(g) => bad_cfg_idents(g.stream(), out),
            _ => {}
        }
    }
}

/// `cfg_attr(PRED, attrs...)`: only PRED is a predicate.
fn cfg_attr_predicate(args: TokenStream) -> TokenStream {
    args.into_iter()
        .take_while(|t| !matches!(t, TokenTree::Punct(p) if p.as_char() == ','))
        .collect()
}

#[derive(Default)]
struct Scan {
    extern_std: usize,
    errors: Vec<String>,
}

/// Token-level pass over a whole file, macro bodies included.
fn scan_tokens(ts: TokenStream, scan: &mut Scan) {
    let toks: Vec<TokenTree> = ts.into_iter().collect();
    for (i, t) in toks.iter().enumerate() {
        match t {
            TokenTree::Ident(id) if id == "extern" => {
                if ident_is(toks.get(i + 1), "crate") && ident_is(toks.get(i + 2), "std") {
                    scan.extern_std += 1;
                }
            }
            TokenTree::Ident(id) if id == "include" => {
                if matches!(toks.get(i + 1), Some(TokenTree::Punct(p)) if p.as_char() == '!') {
                    scan.errors
                        .push("include! pulls in code outside the scanned files".into());
                }
            }
            // `cfg!(...)`
            TokenTree::Ident(id) if id == "cfg" => {
                if let (Some(TokenTree::Punct(p)), Some(TokenTree::Group(g))) =
                    (toks.get(i + 1), toks.get(i + 2))
                {
                    if p.as_char() == '!' {
                        check_pred(g.stream(), scan);
                    }
                }
            }
            TokenTree::Group(g) => {
                if g.delimiter() == Delimiter::Bracket {
                    check_attr(g.stream(), scan);
                }
                scan_tokens(g.stream(), scan);
            }
            _ => {}
        }
    }
}

fn check_pred(pred: TokenStream, scan: &mut Scan) {
    let mut bad = Vec::new();
    bad_cfg_idents(pred, &mut bad);
    for b in bad {
        scan.errors
            .push(format!("platform-dependent cfg predicate `{b}`"));
    }
}

/// The inside of `#[...]` / `#![...]`.
fn check_attr(attr: TokenStream, scan: &mut Scan) {
    let toks: Vec<TokenTree> = attr.into_iter().collect();
    match (toks.first(), toks.get(1)) {
        (Some(TokenTree::Ident(id)), Some(TokenTree::Group(g))) if id == "cfg" => {
            check_pred(g.stream(), scan)
        }
        (Some(TokenTree::Ident(id)), Some(TokenTree::Group(g))) if id == "cfg_attr" => {
            check_pred(cfg_attr_predicate(g.stream()), scan)
        }
        (Some(TokenTree::Ident(id)), Some(TokenTree::Punct(p)))
            if id == "path" && p.as_char() == '=' =>
        {
            scan.errors
                .push("#[path] loads a module file outside the scanned tree".into());
        }
        _ => {}
    }
}

fn is_cfg_test(attr: &syn::Attribute) -> bool {
    attr.path().is_ident("cfg") && attr.parse_args::<syn::Ident>().is_ok_and(|i| i == "test")
}

/// Syntax-tree pass: `extern crate std` items that are really `#[cfg(test)]`.
fn approved_extern_std(file: &syn::File, errors: &mut Vec<String>) -> usize {
    struct V<'a> {
        ok: usize,
        errors: &'a mut Vec<String>,
    }
    impl<'ast> syn::visit::Visit<'ast> for V<'_> {
        fn visit_item_extern_crate(&mut self, i: &'ast syn::ItemExternCrate) {
            if i.ident == "std" {
                let cfgs: Vec<_> = i
                    .attrs
                    .iter()
                    .filter(|a| a.path().is_ident("cfg") || a.path().is_ident("cfg_attr"))
                    .collect();
                if cfgs.len() == 1 && is_cfg_test(cfgs[0]) {
                    self.ok += 1;
                } else {
                    self.errors
                        .push("extern crate std outside #[cfg(test)]".into());
                }
            }
        }
    }
    let mut v = V { ok: 0, errors };
    syn::visit::visit_file(&mut v, file);
    v.ok
}

fn has_inner(file: &syn::File, want: &str) -> bool {
    file.attrs.iter().any(|a| {
        matches!(a.style, syn::AttrStyle::Inner(_)) && quote_attr(a).replace(' ', "") == want
    })
}

fn quote_attr(a: &syn::Attribute) -> String {
    use quote::ToTokens;
    a.meta.to_token_stream().to_string()
}

fn purity(crate_dir: &Path) -> Vec<String> {
    let mut errs = Vec::new();
    let src = crate_dir.join("src");
    let mut files = Vec::new();
    if let Err(e) = rs_files(&src, &mut files) {
        return vec![format!("cannot list {}: {e}", src.display())];
    }
    files.sort();
    for f in &files {
        let text = match std::fs::read_to_string(f) {
            Ok(t) => t,
            Err(e) => {
                errs.push(format!("{}: unreadable: {e}", f.display()));
                continue;
            }
        };
        let file = match syn::parse_file(&text) {
            Ok(x) => x,
            Err(e) => {
                errs.push(format!("{}: does not parse: {e}", f.display()));
                continue;
            }
        };
        let mut local = Vec::new();
        if f.file_name().is_some_and(|n| n == "lib.rs") {
            if !has_inner(&file, "no_std") {
                local.push("lib.rs lost #![no_std]".to_string());
            }
            if !has_inner(&file, "forbid(unsafe_code)") {
                local.push("lib.rs lost #![forbid(unsafe_code)]".to_string());
            }
        }
        let approved = approved_extern_std(&file, &mut local);
        let mut scan = Scan::default();
        match text.parse::<TokenStream>() {
            Ok(ts) => scan_tokens(ts, &mut scan),
            Err(e) => local.push(format!("does not tokenize: {e}")),
        }
        if scan.extern_std > approved && !local.iter().any(|e| e.starts_with("extern crate std")) {
            local.push("extern crate std emitted by a macro".into());
        }
        local.append(&mut scan.errors);
        local.sort();
        local.dedup();
        errs.extend(local.into_iter().map(|e| format!("{}: {e}", f.display())));
    }
    errs
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [cmd, dir] if cmd == "purity" => {
            let errs = purity(Path::new(dir));
            if errs.is_empty() {
                ExitCode::SUCCESS
            } else {
                for e in errs {
                    eprintln!("purity: {e}");
                }
                ExitCode::FAILURE
            }
        }
        _ => {
            eprintln!("usage: aib-xtask purity <crate-dir>");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn check(src: &str) -> Vec<String> {
        let d = std::env::temp_dir().join(format!(
            "aib-xtask-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("src")).unwrap();
        std::fs::write(
            d.join("src/lib.rs"),
            format!("#![no_std]\n#![forbid(unsafe_code)]\n{src}\n"),
        )
        .unwrap();
        let r = purity(&d);
        let _ = std::fs::remove_dir_all(&d);
        r
    }

    fn reason(src: &str) -> String {
        check(src).join("\n")
    }

    #[test]
    fn clean_and_test_only_std_pass() {
        assert_eq!(
            check("#[cfg(test)]\nextern crate std;\npub fn f() {}"),
            Vec::<String>::new()
        );
        assert_eq!(
            check("#[cfg(any(test, feature = \"x\"))]\npub fn f() {}"),
            Vec::<String>::new()
        );
    }

    #[test]
    fn comment_disguised_cfg_test_is_caught() {
        let r = reason("/* #[cfg(test)] */\nextern crate std as sneaky;");
        assert!(r.contains("extern crate std outside #[cfg(test)]"), "{r}");
    }

    #[test]
    fn macro_emitted_extern_std_is_caught() {
        let r = reason("macro_rules! m { () => { extern crate std as s; } }\nm!();");
        assert!(r.contains("extern crate std emitted by a macro"), "{r}");
    }

    #[test]
    fn target_cfg_edges_are_caught() {
        for (src, bad) in [
            (
                "#[cfg(target_arch = \"aarch64\")]\nextern crate std;",
                "target_arch",
            ),
            (
                "#[cfg(not(target_os = \"none\"))]\npub fn f() {}",
                "target_os",
            ),
            ("#[cfg_attr(unix, derive(Debug))]\npub struct S;", "unix"),
            ("pub fn w() -> bool { cfg!(windows) }", "windows"),
            (
                "macro_rules! m { () => { #[cfg(panic = \"unwind\")] pub fn f() {} } }",
                "panic",
            ),
        ] {
            let r = reason(src);
            assert!(
                r.contains(&format!("platform-dependent cfg predicate `{bad}`")),
                "{src}: {r}"
            );
        }
    }

    #[test]
    fn include_and_path_are_caught() {
        assert!(reason("include!(\"../x.rs\");").contains("include!"));
        assert!(reason("#[path = \"../../x.rs\"]\nmod x;").contains("#[path]"));
    }

    #[test]
    fn lost_crate_attributes_are_caught() {
        let d = std::env::temp_dir().join(format!("aib-xtask-attrs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("src")).unwrap();
        std::fs::write(d.join("src/lib.rs"), "// #![no_std]\npub fn f() {}\n").unwrap();
        let r = purity(&d).join("\n");
        let _ = std::fs::remove_dir_all(&d);
        assert!(
            r.contains("lost #![no_std]") && r.contains("lost #![forbid(unsafe_code)]"),
            "{r}"
        );
    }
}
