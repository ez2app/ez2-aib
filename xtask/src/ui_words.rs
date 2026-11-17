//! `aib-xtask ui-words --base <ref> [--head <ref>] [--list-from <ref>] [--all]`
//!
//! S59 addition (519d0 11-14, Andy approved the AIB 2.0 glossary): a string the 2.0 screens show
//! must not bring back a word the glossary (`docs/i18n/GLOSSARY-aib2.json`) lists under
//! `replaces` (樹, 為什麼, 席 …). Agreed scope (519d0 11-14 08:53):
//! - only string literals that are new in `base...head` (present in the head version of a file,
//!   not in its merge-base version); `--all` lists every hit at head and never fails, as the
//!   renaming progress list;
//! - only code: comments and doc comments are not literals here, and `#[cfg(test)]` items and
//!   `tests/` are skipped;
//! - a one-character old word counts only when it stands alone (string edge, space or
//!   punctuation on both sides), so 席位 / 排隊 are not hits; longer old words match anywhere;
//!   an old word lying wholly inside an approved new word (席 in 席位) is never a hit;
//! - the glossary is read from base, like the Python exception list, so a change cannot loosen it;
//! - a renamed or copied file is compared with its old name at the merge base, so moving a file
//!   does not make its strings new (ebb36 L1).
//!
//! exit: 0 no new hit (or `--all`), 1 new hits, 2 cannot tell.

use crate::no_new_python::{changed_pairs, git};
use proc_macro2::{TokenStream, TokenTree};
use std::collections::HashMap;
use std::path::Path;
use syn::visit::Visit;

pub const GLOSSARY: &str = "docs/i18n/GLOSSARY-aib2.json";

/// The 2.0 screen crates (aib-seatview, aib-hosts and aib-prs are binaries of aib-plan).
pub const SCOPE: &[&str] = &["crates/aib-plan/src/", "crates/aib-title/src/"];

const LANGS: &[&str] = &["zh-TW", "en", "ja", "ko"];

/// One old word and what replaces it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Old {
    pub word: String,
    pub key: String,
    pub new_zh: String,
}

/// The old words, and every approved new word in any language.
#[derive(Debug, Default)]
pub struct Glossary {
    pub old: Vec<Old>,
    pub new_words: Vec<String>,
}

/// Read `{"terms": [{"key", "zh-TW", "en", "ja", "ko", "replaces": [...]}]}`. A term without
/// `key` or with a `replaces` that is not a list of strings is an error, never skipped.
pub fn parse_glossary(text: &str) -> Result<Glossary, String> {
    let v: serde_json::Value =
        serde_json::from_str(text).map_err(|e| format!("{GLOSSARY}: not JSON: {e}"))?;
    let terms = v
        .get("terms")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| format!("{GLOSSARY}: no \"terms\" list"))?;
    let mut g = Glossary::default();
    for (i, t) in terms.iter().enumerate() {
        let key = t
            .get("key")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| format!("{GLOSSARY}: term {i} has no key"))?;
        for l in LANGS {
            if let Some(w) = t.get(*l).and_then(serde_json::Value::as_str) {
                if !w.trim().is_empty() {
                    g.new_words.push(w.to_string());
                }
            }
        }
        let new_zh = t
            .get("zh-TW")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        match t.get("replaces") {
            None | Some(serde_json::Value::Null) => {}
            Some(serde_json::Value::Array(a)) => {
                for w in a {
                    let w = w
                        .as_str()
                        .ok_or_else(|| format!("{GLOSSARY}: {key}: replaces holds a non-string"))?;
                    if !w.is_empty() {
                        g.old.push(Old {
                            word: w.to_string(),
                            key: key.to_string(),
                            new_zh: new_zh.clone(),
                        });
                    }
                }
            }
            Some(_) => return Err(format!("{GLOSSARY}: {key}: replaces is not a list")),
        }
    }
    Ok(g)
}

fn is_test_attr(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| {
        a.path().is_ident("test")
            || (a.path().is_ident("cfg")
                && a.meta
                    .require_list()
                    .is_ok_and(|l| l.tokens.to_string().split_whitespace().any(|w| w == "test")))
    })
}

#[derive(Default)]
struct Lits(Vec<String>);

impl Lits {
    fn tokens(&mut self, ts: TokenStream) {
        for t in ts {
            match t {
                TokenTree::Group(g) => self.tokens(g.stream()),
                TokenTree::Literal(l) => {
                    if let Ok(syn::Lit::Str(s)) =
                        syn::parse2::<syn::Lit>(TokenTree::Literal(l).into())
                    {
                        self.0.push(s.value());
                    }
                }
                _ => {}
            }
        }
    }
}

impl<'ast> Visit<'ast> for Lits {
    fn visit_lit_str(&mut self, s: &'ast syn::LitStr) {
        self.0.push(s.value());
    }
    // doc comments and other attributes are not text the screens show
    fn visit_attribute(&mut self, _: &'ast syn::Attribute) {}
    fn visit_macro(&mut self, m: &'ast syn::Macro) {
        self.tokens(m.tokens.clone());
    }
    fn visit_item_mod(&mut self, i: &'ast syn::ItemMod) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_item_mod(self, i);
        }
    }
    fn visit_item_fn(&mut self, i: &'ast syn::ItemFn) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_item_fn(self, i);
        }
    }
    fn visit_item_impl(&mut self, i: &'ast syn::ItemImpl) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_item_impl(self, i);
        }
    }
    fn visit_impl_item_fn(&mut self, i: &'ast syn::ImplItemFn) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_impl_item_fn(self, i);
        }
    }
    fn visit_item_const(&mut self, i: &'ast syn::ItemConst) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_item_const(self, i);
        }
    }
    fn visit_item_static(&mut self, i: &'ast syn::ItemStatic) {
        if !is_test_attr(&i.attrs) {
            syn::visit::visit_item_static(self, i);
        }
    }
}

/// The string literals of a Rust file outside test code and attributes (macro bodies included).
pub fn literals(src: &str) -> Result<Vec<String>, String> {
    let f = syn::parse_file(src).map_err(|e| format!("not Rust: {e}"))?;
    let mut l = Lits::default();
    l.visit_file(&f);
    Ok(l.0)
}

/// Old words in `s`, each once, in glossary order.
pub fn hits<'g>(s: &str, g: &'g Glossary) -> Vec<&'g Old> {
    let chars: Vec<char> = s.chars().collect();
    // char spans of every approved new word
    let mut covered: Vec<(usize, usize)> = Vec::new();
    for w in &g.new_words {
        covered.extend(spans(&chars, w));
    }
    let mut out: Vec<&Old> = Vec::new();
    for o in &g.old {
        let n = o.word.chars().count();
        let found = spans(&chars, &o.word).into_iter().any(|(a, b)| {
            let inside_new = covered
                .iter()
                .any(|(x, y)| *x <= a && b <= *y && (y - x) > n);
            let alone = n > 1
                || ((a == 0 || !chars[a - 1].is_alphanumeric())
                    && (b == chars.len() || !chars[b].is_alphanumeric()));
            alone && !inside_new
        });
        if found && !out.iter().any(|x| x.word == o.word) {
            out.push(o);
        }
    }
    out
}

/// Every `[start, end)` char span where `w` occurs in `chars`.
fn spans(chars: &[char], w: &str) -> Vec<(usize, usize)> {
    let w: Vec<char> = w.chars().collect();
    if w.is_empty() || w.len() > chars.len() {
        return Vec::new();
    }
    (0..=chars.len() - w.len())
        .filter(|i| chars[*i..*i + w.len()] == w[..])
        .map(|i| (i, i + w.len()))
        .collect()
}

fn in_scope(path: &str) -> bool {
    path.ends_with(".rs")
        && SCOPE.iter().any(|p| path.starts_with(p))
        && !path.split('/').any(|c| c == "tests")
}

fn show(repo: &Path, rev: &str, path: &str) -> Result<Option<String>, String> {
    let spec = format!("{rev}:{path}");
    if git(repo, &["cat-file", "-e", &spec]).is_err() {
        return Ok(None);
    }
    Ok(Some(
        String::from_utf8_lossy(&git(repo, &["show", &spec])?).into_owned(),
    ))
}

/// The glossary at `rev`; missing = no words (an unreadable `rev` is an error).
pub fn glossary_at(repo: &Path, rev: &str) -> Result<Glossary, String> {
    match show(repo, rev, GLOSSARY)? {
        Some(t) => parse_glossary(&t),
        None => {
            git(
                repo,
                &[
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    &format!("{rev}^{{commit}}"),
                ],
            )?;
            Ok(Glossary::default())
        }
    }
}

/// `path: "literal" — old → new` lines for literals new in `base...head` (or, with `all`, every
/// literal at `head`).
pub fn report(
    repo: &Path,
    base: &str,
    head: &str,
    g: &Glossary,
    all: bool,
) -> Result<Vec<String>, String> {
    // (name at the merge base, name at head)
    let paths: Vec<(String, String)> = if all {
        let z = git(
            repo,
            &["ls-tree", "-r", "-z", "--name-only", head, "--", "crates"],
        )?;
        z.split(|b| *b == 0)
            .map(|s| String::from_utf8_lossy(s).into_owned())
            .filter(|p| in_scope(p))
            .map(|p| (p.clone(), p))
            .collect()
    } else {
        let z = git(
            repo,
            &[
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "-z",
                "--name-status",
                "-M",
                &format!("{base}...{head}"),
            ],
        )?;
        changed_pairs(&z)?
            .into_iter()
            .filter(|(_, p)| in_scope(p))
            .collect()
    };
    let mb = if all {
        None
    } else {
        let o = git(repo, &["merge-base", base, head])?;
        Some(String::from_utf8_lossy(&o).trim().to_string())
    };
    let mut out = Vec::new();
    for (was, p) in &paths {
        let p = p.as_str();
        let Some(now) = show(repo, head, p)? else {
            continue;
        };
        let now = literals(&now).map_err(|e| format!("{p}: {e}"))?;
        // multiset of the merge-base literals: a literal is new when head has more copies
        let mut before: HashMap<String, usize> = HashMap::new();
        if let Some(mb) = &mb {
            if let Some(old) = show(repo, mb, was)? {
                for l in literals(&old).unwrap_or_default() {
                    *before.entry(l).or_default() += 1;
                }
            }
        }
        for l in now {
            if let Some(n) = before.get_mut(&l) {
                if *n > 0 {
                    *n -= 1;
                    continue;
                }
            }
            for o in hits(&l, g) {
                let shown: String = l.chars().take(60).collect();
                out.push(format!(
                    "{p}: {shown:?} — {} → {} ({})",
                    o.word, o.new_zh, o.key
                ));
            }
        }
    }
    Ok(out)
}

/// `ui-words` arguments after the subcommand, run in `repo`. Returns the exit code.
pub fn run(repo: &Path, args: &[String]) -> u8 {
    let mut base = None;
    let mut head = "HEAD".to_string();
    let mut from = None;
    let mut all = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--all" {
            all = true;
            continue;
        }
        match (a.as_str(), it.next()) {
            ("--base", Some(v)) => base = Some(v.clone()),
            ("--head", Some(v)) => head = v.clone(),
            ("--list-from", Some(v)) => from = Some(v.clone()),
            _ => {
                eprintln!(
                    "usage: aib-xtask ui-words --base <ref> [--head <ref>] [--list-from <ref>] [--all]"
                );
                return 2;
            }
        }
    }
    let Some(base) = base else {
        eprintln!("ui-words: --base is required");
        return 2;
    };
    // the glossary comes from base unless told otherwise: a change cannot loosen it
    let from = from.unwrap_or_else(|| base.clone());
    let g = match glossary_at(repo, &from) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("ui-words: cannot tell — {e}");
            return 2;
        }
    };
    match report(repo, &base, &head, &g, all) {
        Ok(r) if all => {
            for x in &r {
                println!("ui-words: {x}");
            }
            println!(
                "ui-words --all: {} old word(s) at {head} (renaming list, not a failure)",
                r.len()
            );
            0
        }
        Ok(r) if r.is_empty() => {
            println!("ui-words: ok ({base}...{head}: no new string brings back an old word of {GLOSSARY} at {from})");
            0
        }
        Ok(r) => {
            for x in &r {
                eprintln!("ui-words: {x}");
            }
            eprintln!(
                "ui-words: {} new string(s) use an old word — Andy 11-14 glossary; use the new word",
                r.len()
            );
            1
        }
        Err(e) => {
            eprintln!("ui-words: cannot tell — {e}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process::Command;

    fn g() -> Glossary {
        parse_glossary(
            r#"{"terms":[
              {"key":"ui.plan.items","zh-TW":"專案項目","en":"Project items","replaces":["樹","進度樹","Project Tree"]},
              {"key":"ui.seat","zh-TW":"席位","en":"Seat","replaces":["席"]},
              {"key":"ui.hosts.queued","zh-TW":"排隊","en":"Queued","replaces":["排"]},
              {"key":"ui.hosts.loading","zh-TW":"讀取中","en":"Loading","replaces":["讀取中…"]},
              {"key":"ui.approval.wait","zh-TW":"等你按 Touch ID","replaces":["等你按"]},
              {"key":"ui.review.passed","zh-TW":"通過","en":"Pass"}
            ]}"#,
        )
        .unwrap()
    }

    fn words(s: &str) -> Vec<String> {
        hits(s, &g()).into_iter().map(|o| o.word.clone()).collect()
    }

    #[test]
    fn longer_old_words_match_anywhere() {
        assert_eq!(words("看進度樹"), ["進度樹"]);
        assert_eq!(words("Open Project Tree now"), ["Project Tree"]);
    }

    #[test]
    fn a_one_character_old_word_counts_only_alone() {
        assert_eq!(words("席"), ["席"]);
        assert_eq!(words("3 席 在線"), ["席"]);
        assert_eq!(words("[樹]"), ["樹"]);
        assert!(words("席位").is_empty(), "inside the new word");
        assert!(words("出席率").is_empty(), "part of another word");
        assert!(words("排隊中").is_empty());
        assert!(words("樹狀").is_empty());
    }

    #[test]
    fn an_old_word_inside_its_new_word_is_not_a_hit() {
        assert!(words("等你按 Touch ID").is_empty());
        assert_eq!(words("等你按"), ["等你按"]);
        assert_eq!(words("請等你按一下"), ["等你按"]);
    }

    #[test]
    fn an_old_word_longer_than_the_new_word_inside_it_still_counts() {
        assert_eq!(words("讀取中…"), ["讀取中…"]);
        assert!(words("讀取中").is_empty());
    }

    #[test]
    fn comments_doc_comments_and_test_code_are_not_literals() {
        let src = r#"
            //! 樹 in a module doc
            /// 進度樹 in a doc comment
            // 席 in a comment
            fn show() -> String { format!("{} 席", 3) }
            const T: &str = "進度樹";
            #[cfg(test)]
            mod tests { fn t() { let _ = "樹"; } }
            #[test]
            fn t2() { let _ = "席"; }
        "#;
        assert_eq!(literals(src).unwrap(), ["{} 席", "進度樹"]);
    }

    #[test]
    fn a_bad_glossary_is_an_error() {
        assert!(parse_glossary("{").is_err());
        assert!(parse_glossary("{}").is_err());
        assert!(parse_glossary(r#"{"terms":[{"zh-TW":"x"}]}"#).is_err());
        assert!(parse_glossary(r#"{"terms":[{"key":"k","replaces":"樹"}]}"#).is_err());
        assert!(parse_glossary(r#"{"terms":[{"key":"k","replaces":[1]}]}"#).is_err());
    }

    #[test]
    fn scope_is_the_screen_crates_outside_tests() {
        assert!(in_scope("crates/aib-plan/src/bin/aib-hosts.rs"));
        assert!(in_scope("crates/aib-title/src/beat.rs"));
        assert!(!in_scope("crates/aib-plan/tests/x.rs"));
        assert!(!in_scope("crates/aibd/src/api.rs"));
        assert!(!in_scope("crates/aib-plan/src/notes.md"));
    }

    struct Repo(PathBuf);
    impl Drop for Repo {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    impl Repo {
        fn new() -> Repo {
            let d = std::env::temp_dir().join(format!(
                "aib-xtask-uiw-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_or(0, |d| d.as_nanos())
            ));
            std::fs::create_dir_all(&d).unwrap();
            let r = Repo(d);
            r.git(&["init", "-q", "-b", "main"]);
            r
        }
        fn git(&self, a: &[&str]) -> String {
            let o = Command::new("git")
                .arg("-C")
                .arg(&self.0)
                .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
                .args(["-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"])
                .args(a)
                .output()
                .unwrap();
            assert!(
                o.status.success(),
                "git {a:?}: {}",
                String::from_utf8_lossy(&o.stderr)
            );
            String::from_utf8_lossy(&o.stdout).trim().to_string()
        }
        fn put(&self, p: &str, s: &str) {
            let f = self.0.join(p);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, s).unwrap();
        }
        fn commit(&self) -> String {
            self.git(&["add", "-A"]);
            self.git(&["commit", "-q", "--allow-empty", "-m", "c"]);
            self.git(&["rev-parse", "HEAD"])
        }
    }

    const GL: &str =
        r#"{"terms":[{"key":"ui.plan.items","zh-TW":"專案項目","replaces":["進度樹"]}]}"#;

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| (*s).to_string()).collect()
    }

    #[test]
    fn only_new_strings_count_and_old_ones_are_the_renaming_list() {
        let r = Repo::new();
        r.put(GLOSSARY, GL);
        r.put(
            "crates/aib-plan/src/a.rs",
            "pub const A: &str = \"進度樹\";\n",
        );
        let b = r.commit();
        // an unrelated change in the same file: the old literal is not new
        r.put(
            "crates/aib-plan/src/a.rs",
            "pub const A: &str = \"進度樹\";\npub const B: &str = \"專案項目\";\n",
        );
        r.commit();
        assert_eq!(run(&r.0, &args(&["--base", &b])), 0, "old literal kept");
        r.put(
            "crates/aib-plan/src/a.rs",
            "pub const A: &str = \"進度樹\";\npub const B: &str = \"看進度樹\";\n",
        );
        r.commit();
        assert_eq!(
            run(&r.0, &args(&["--base", &b])),
            1,
            "a new literal with the old word"
        );
        assert_eq!(
            run(&r.0, &args(&["--base", &b, "--all"])),
            0,
            "--all never fails"
        );
        let got = report(&r.0, &b, "HEAD", &glossary_at(&r.0, &b).unwrap(), false).unwrap();
        assert_eq!(got.len(), 1, "{got:?}");
    }

    #[test]
    fn a_renamed_file_keeps_its_old_strings() {
        // ebb36 L1: moving plan.rs to plan/mod.rs must not make its strings new
        let body: String = (0..20)
            .map(|i| format!("pub const C{i}: &str = \"第 {i} 格\";\n"))
            .collect();
        let r = Repo::new();
        r.put(GLOSSARY, GL);
        r.put(
            "crates/aib-plan/src/plan.rs",
            &format!("pub const A: &str = \"進度樹\";\n{body}"),
        );
        let b = r.commit();
        r.git(&[
            "mv",
            "crates/aib-plan/src/plan.rs",
            "crates/aib-plan/src/plan_mod.rs",
        ]);
        r.put(
            "crates/aib-plan/src/plan_mod.rs",
            &format!("pub const A: &str = \"進度樹\";\n{body}pub const Z: &str = \"專案項目\";\n"),
        );
        r.commit();
        let st = r.git(&["diff", "--name-status", "-M", &format!("{b}...HEAD")]);
        assert!(st.starts_with('R'), "git saw a rename: {st}");
        assert_eq!(
            run(&r.0, &args(&["--base", &b])),
            0,
            "moved, old literal kept"
        );
        r.put(
            "crates/aib-plan/src/plan_mod.rs",
            &format!("pub const A: &str = \"進度樹\";\n{body}pub const Z: &str = \"看進度樹\";\n"),
        );
        r.commit();
        let got = report(&r.0, &b, "HEAD", &glossary_at(&r.0, &b).unwrap(), false).unwrap();
        assert_eq!(got.len(), 1, "only the one added after the move: {got:?}");
    }

    #[test]
    fn the_glossary_is_read_from_base() {
        let r = Repo::new();
        r.put(GLOSSARY, GL);
        let b = r.commit();
        r.put(GLOSSARY, r#"{"terms":[]}"#);
        r.put(
            "crates/aib-title/src/x.rs",
            "pub const X: &str = \"進度樹\";\n",
        );
        r.commit();
        assert_eq!(
            run(&r.0, &args(&["--base", &b])),
            1,
            "emptied list at head does not count"
        );
        assert_eq!(run(&r.0, &args(&["--base", &b, "--list-from", "HEAD"])), 0);
        assert_eq!(run(&r.0, &args(&["--base", "no-such-ref"])), 2);
    }
}
