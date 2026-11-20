//! Slice 2 (DESIGN-receipt-gate-a9136-1120 §2, r2 L1 / N4): what `run` takes on stdin and the
//! base repository it checks the bundle against. Pure checks and plain file writes; no git is
//! run here (slice 3 runs it with a clean configuration).

use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::Path;

/// Read exactly `len` bytes of `input` into a new file `out` (never an existing one) and
/// check their SHA-256 against `sha256`; on any failure the file is removed. A shorter stream
/// is `short_input`, a different digest `sha256_mismatch`.
pub fn receive(
    input: &mut dyn Read,
    len: u64,
    sha256: &str,
    out: &Path,
) -> Result<(), &'static str> {
    let got = (|| {
        let mut f = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(out)
            .map_err(|_| "cannot_write")?;
        let mut h = Sha256::new();
        let mut left = len;
        let mut buf = vec![0_u8; 64 * 1024];
        while left > 0 {
            let want = usize::try_from(left.min(buf.len() as u64)).map_err(|_| "cannot_write")?;
            let n = input.read(&mut buf[..want]).map_err(|_| "short_input")?;
            if n == 0 {
                return Err("short_input");
            }
            h.update(&buf[..n]);
            f.write_all(&buf[..n]).map_err(|_| "cannot_write")?;
            left -= n as u64;
        }
        let hex: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
        if hex != sha256 {
            return Err("sha256_mismatch");
        }
        f.sync_all().map_err(|_| "cannot_write")
    })();
    if got.is_err() {
        let _ = std::fs::remove_file(out);
    }
    got
}

fn plain(s: &str, extra: &[u8]) -> bool {
    !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || extra.contains(&b))
}

/// A knife table's text: each line is six tab-separated fields (name, anchor, replacement,
/// test arguments, file, crate) as receipt-run reads them. The crate is `krate` on every line,
/// the file a relative path inside the tree (no `..`, no drive, no leading `/`), the test
/// arguments plain words (receipt-run puts them on a cargo command line). The anchor and
/// replacement are code and are not judged here.
pub fn knives_ok(text: &str, krate: &str) -> Result<usize, &'static str> {
    let mut n = 0;
    for line in text.lines().filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split('\t').collect();
        let [name, _old, _new, targs, file, c] = f.as_slice() else {
            return Err("knives_shape");
        };
        if *c != krate {
            return Err("knives_crate");
        }
        if !plain(name, b"-_") || !plain(targs, b"-_: ") {
            return Err("knives_words");
        }
        let bad_file = !plain(file, b"-_./")
            || file.starts_with('/')
            || file
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == "..");
        if bad_file {
            return Err("knives_file");
        }
        n += 1;
    }
    Ok(n)
}

/// The public repository the base is fetched from (read-only https, no credentials).
pub const BASE_URL: &str = "https://github.com/ez2app/ez2-aib.git";

/// Whether the base bare repository at `git_dir` is as the gate made it (r2 N4: the last
/// run's test code could have written to it): no hooks, no alternates, and a config holding
/// only the keys a fresh `git init --bare` plus the one remote write. Anything else: rebuild.
pub fn base_clean(git_dir: &Path) -> bool {
    let hooks_empty = match std::fs::read_dir(git_dir.join("hooks")) {
        Ok(mut d) => d.next().is_none(),
        Err(e) => e.kind() == std::io::ErrorKind::NotFound,
    };
    if !hooks_empty || git_dir.join("objects/info/alternates").exists() {
        return false;
    }
    if git_dir.join("info/attributes").exists() || git_dir.join("commondir").exists() {
        return false;
    }
    std::fs::read_to_string(git_dir.join("config")).is_ok_and(|t| config_clean(&t))
}

/// A config with only `[core]` format keys and `[remote "origin"]` naming [`BASE_URL`].
pub fn config_clean(text: &str) -> bool {
    const CORE: [&str; 6] = [
        "repositoryformatversion",
        "filemode",
        "bare",
        "ignorecase",
        "precomposeunicode",
        "logallrefupdates",
    ];
    let mut section = "";
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') {
            section = match line {
                "[core]" => "core",
                "[remote \"origin\"]" => "origin",
                _ => return false,
            };
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            return false;
        };
        let (k, v) = (k.trim().to_ascii_lowercase(), v.trim());
        let ok = match section {
            "core" => CORE.contains(&k.as_str()) && plain(v, b""),
            "origin" => {
                (k == "url" && v == BASE_URL)
                    || (k == "fetch" && v == "+refs/heads/main:refs/heads/main")
            }
            _ => false,
        };
        if !ok {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn sha(b: &[u8]) -> String {
        Sha256::digest(b)
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect()
    }

    #[test]
    fn a_file_is_kept_only_whole_and_with_its_digest() {
        let d = tempfile::tempdir().unwrap();
        let body = b"bundle bytes".to_vec();
        let ok = d.path().join("a");
        receive(&mut body.as_slice(), body.len() as u64, &sha(&body), &ok).unwrap();
        assert_eq!(std::fs::read(&ok).unwrap(), body);
        // only len bytes are taken: what follows is the next file
        let two = [body.clone(), b"knives".to_vec()].concat();
        let mut r = two.as_slice();
        let first = d.path().join("b");
        receive(&mut r, body.len() as u64, &sha(&body), &first).unwrap();
        assert_eq!(r, b"knives", "reason: read_past_len");
        let bad = d.path().join("c");
        assert_eq!(
            receive(
                &mut body.as_slice(),
                body.len() as u64,
                &sha(b"other"),
                &bad
            ),
            Err("sha256_mismatch")
        );
        assert!(!bad.exists(), "reason: bad_file_kept");
        let short = d.path().join("e");
        assert_eq!(
            receive(&mut &body[..3], body.len() as u64, &sha(&body), &short),
            Err("short_input")
        );
        assert!(!short.exists(), "reason: short_file_kept");
        // an existing file is never written over
        assert_eq!(
            receive(&mut body.as_slice(), body.len() as u64, &sha(&body), &ok),
            Err("cannot_write"),
            "reason: existing_file_written"
        );
    }

    #[test]
    fn a_knife_table_is_six_fields_of_one_crate_inside_the_tree() {
        let line = |name: &str, targs: &str, file: &str, c: &str| {
            format!("{name}\told\tnew\t{targs}\t{file}\t{c}\n")
        };
        let good = line("RG1", "--lib tests::x_y", "crates/a/src/lib.rs", "a");
        assert_eq!(knives_ok(&format!("{good}{good}"), "a"), Ok(2));
        assert_eq!(knives_ok("", "a"), Ok(0));
        assert_eq!(knives_ok("RG1\tx\n", "a"), Err("knives_shape"));
        assert_eq!(
            knives_ok(&line("RG1", "--lib t", "crates/a/src/lib.rs", "b"), "a"),
            Err("knives_crate"),
            "reason: other_crate_taken"
        );
        assert_eq!(
            knives_ok(&line("RG1", "--lib t; id", "crates/a/src/lib.rs", "a"), "a"),
            Err("knives_words"),
            "reason: shell_in_args_taken"
        );
        for f in ["../x.rs", "/etc/x", "a//b", "crates/./a", "c:x", "a/$b"] {
            assert_eq!(
                knives_ok(&line("RG1", "t", f, "a"), "a"),
                Err("knives_file"),
                "reason: file_outside_taken {f}"
            );
        }
    }

    #[test]
    fn a_base_repo_the_last_run_touched_is_rebuilt() {
        let fresh = format!(
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = true\n\
             [remote \"origin\"]\n\turl = {BASE_URL}\n\tfetch = +refs/heads/main:refs/heads/main\n"
        );
        assert!(config_clean(&fresh));
        for evil in [
            "[core]\n\thooksPath = /tmp/h\n",
            "[core]\n\tfsmonitor = /tmp/x\n",
            "[core]\n\tbare = true\n[filter \"lfs\"]\n\tsmudge = sh\n",
            "[remote \"origin\"]\n\turl = https://example.com/x.git\n",
            "[include]\n\tpath = /tmp/c\n",
            "[core]\n\tsshCommand\n",
            // any other section, even empty, is not one the gate wrote
            "[core]\n\tbare = true\n[include]\n",
        ] {
            assert!(!config_clean(evil), "reason: config_taken {evil:?}");
        }
        let d = tempfile::tempdir().unwrap();
        let g = d.path();
        std::fs::write(g.join("config"), &fresh).unwrap();
        assert!(base_clean(g));
        std::fs::create_dir_all(g.join("hooks")).unwrap();
        assert!(base_clean(g), "reason: empty_hooks_refused");
        std::fs::write(g.join("hooks/pre-receive"), "#!/bin/sh").unwrap();
        assert!(!base_clean(g), "reason: hook_taken");
        std::fs::remove_file(g.join("hooks/pre-receive")).unwrap();
        std::fs::create_dir_all(g.join("objects/info")).unwrap();
        std::fs::write(g.join("objects/info/alternates"), "/elsewhere").unwrap();
        assert!(!base_clean(g), "reason: alternates_taken");
        std::fs::remove_file(g.join("objects/info/alternates")).unwrap();
        std::fs::write(g.join("config"), "[core]\n\thooksPath = /x\n").unwrap();
        assert!(!base_clean(g), "reason: bad_config_taken");
        std::fs::remove_file(g.join("config")).unwrap();
        assert!(!base_clean(g), "reason: no_config_taken");
    }
}
