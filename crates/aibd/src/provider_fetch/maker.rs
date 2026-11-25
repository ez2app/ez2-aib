//! L2 (IFACE-provider-lifecycle-c2f0b-1126 §3.2 S, L1 / L2 接點): who made the program.
//!
//! - [`signed_by_maker`] is the one signature check the production path uses, in L1a's
//!   `provider_fetch::Signer` shape. Windows: Authenticode through a trusted root and the leaf
//!   certificate's O exactly the maker's (aib-sys wintrust, L1b), the whole chain checked for
//!   revocation on a download. macOS: `/usr/bin/codesign --verify --strict` against a
//!   requirement naming Apple's root and the maker's Team ID. Anything else: refused.
//! - [`fetch_measured`] downloads one row of the measured table with that check fixed inside:
//!   no caller can hand it another signer (3e53b N2 on the L1 interface). The fetch itself is
//!   passed in so tests can give bytes; the signer cannot be.
//! - A row from the table ([`aib_seatopen::Measured`]) becomes L1's [`FetchRow`] here, never
//!   from a request, a page or a file.
//!
//! UNVERIFIED on a real machine: the claude.exe leaf O and the darwin Team ID are from the
//! maker's published signing details, not measured here (W0 / W-14).

use crate::provider_fetch::{self, Fetch, FetchRow, Progress};
use aib_seatopen::Measured;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;

/// Who signs each provider's program: Windows leaf O, macOS Team ID.
const MAKERS: &[(&str, &str, &str)] = &[("claude", "Anthropic, PBC", "Q6L2SF6YDW")];

/// The refusal when the platform has no signature check here.
pub const ERR_NOT_OFFERED: &str = "not_offered";

impl From<&Measured> for FetchRow {
    fn from(m: &Measured) -> Self {
        FetchRow {
            provider: m.provider.to_owned(),
            version: m.version.to_owned(),
            platform: m.platform.to_owned(),
            url: m.url.to_owned(),
            sha256: m.sha256.to_owned(),
            size: m.size,
        }
    }
}

fn maker(provider: &str) -> Option<(&'static str, &'static str)> {
    MAKERS
        .iter()
        .find(|(p, _, _)| *p == provider)
        .map(|(_, org, team)| (*org, *team))
}

/// The code requirement a macOS program of `team` must meet: Apple's root, the Developer ID
/// intermediate, a Developer ID Application leaf of that Team ID (Apple's own Developer ID
/// requirement plus the Team ID; 3e53b REVIEW-l2-maker N2). No revocation check on macOS here
/// (N3; IFACE §3.2).
pub fn mac_requirement(team: &str) -> String {
    format!(
        "anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists \
         and certificate leaf[field.1.2.840.113635.100.6.1.13] exists \
         and certificate leaf[subject.OU] = \"{team}\""
    )
}

/// S for `provider`'s program at `path` (open as `file`): `Ok` only when the platform says the
/// maker signed it. The error words are fixed; nothing read from the file reaches them.
pub fn signed_by_maker(provider: &str, path: &Path, file: &File) -> Result<(), &'static str> {
    let Some((org, team)) = maker(provider) else {
        return Err(ERR_NOT_OFFERED);
    };
    #[cfg(windows)]
    {
        let _ = team;
        aib_sys::wintrust::signed_by_org(path, file, org, aib_sys::wintrust::Revocation::WholeChain)
    }
    #[cfg(target_os = "macos")]
    {
        let _ = (org, file);
        mac_codesign(path, &mac_requirement(team))
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = (org, team, path, file);
        Err(ERR_NOT_OFFERED)
    }
}

/// `/usr/bin/codesign --verify --strict -R=<requirement> <path>`, empty environment, nothing
/// read back but the exit. (macOS has no read-only lock: the hash before and after is the
/// narrowing, IFACE §3.3, said as is.)
#[cfg(target_os = "macos")]
fn mac_codesign(path: &Path, requirement: &str) -> Result<(), &'static str> {
    let ok = std::process::Command::new("/usr/bin/codesign")
        .arg("--verify")
        .arg("--strict")
        .arg(format!("-R={requirement}"))
        .arg("--")
        .arg(path)
        .env_clear()
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if ok {
        Ok(())
    } else {
        Err("not_trusted")
    }
}

/// Download `m` (this machine's row of the measured table), check P then the maker's
/// signature, place it; the placed path. The signer is [`signed_by_maker`], fixed here.
pub fn fetch_measured(
    data: &Path,
    m: &Measured,
    fetch: &dyn Fetch,
    cancel: &AtomicBool,
    progress: &Progress,
) -> Result<PathBuf, &'static str> {
    let row = FetchRow::from(m);
    let provider = row.provider.clone();
    let sign = move |p: &Path, f: &File| signed_by_maker(&provider, p, f);
    provider_fetch::run(data, &row, fetch, &sign, cancel, progress)
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    struct Bytes(&'static [u8]);
    impl Fetch for Bytes {
        fn fetch(
            &self,
            _row: &FetchRow,
            out: &Path,
            _cancel: &AtomicBool,
            _progress: &Progress,
        ) -> Result<(), &'static str> {
            std::fs::write(out, self.0).map_err(|_| provider_fetch::ERR_OFFLINE)
        }
    }

    fn hex(b: &[u8]) -> String {
        Sha256::digest(b)
            .iter()
            .map(|x| format!("{x:02x}"))
            .collect()
    }

    #[test]
    fn a_measured_row_becomes_the_same_fetch_row() {
        let m = aib_seatopen::MEASURED.first().unwrap();
        let r = FetchRow::from(m);
        assert_eq!(
            (r.provider.as_str(), r.version.as_str(), r.platform.as_str()),
            (m.provider, m.version, m.platform)
        );
        assert_eq!(
            (r.url.as_str(), r.sha256.as_str(), r.size),
            (m.url, m.sha256, m.size)
        );
        // every table row is one L1 would fetch
        for m in aib_seatopen::MEASURED {
            assert!(provider_fetch::source_ok(&FetchRow::from(m)), "{m:?}");
        }
    }

    #[test]
    fn only_a_known_maker_is_checked_and_the_requirement_names_its_team() {
        let f = File::open(std::env::current_exe().unwrap()).unwrap();
        assert_eq!(
            signed_by_maker("grok", Path::new("/nonexistent"), &f),
            Err(ERR_NOT_OFFERED)
        );
        let r = mac_requirement("Q6L2SF6YDW");
        assert!(r.starts_with("anchor apple generic and "), "{r}");
        assert!(
            r.contains("certificate 1[field.1.2.840.113635.100.6.2.6] exists"),
            "{r}"
        );
        assert!(
            r.contains("certificate leaf[field.1.2.840.113635.100.6.1.13] exists"),
            "{r}"
        );
        assert!(
            r.ends_with("certificate leaf[subject.OU] = \"Q6L2SF6YDW\""),
            "{r}"
        );
    }

    /// How many times `text` (a Rust file) names `provider_fetch::run` outside comments: by
    /// path, through a glob or a renamed module, or in a `use` list, across lines and spaces
    /// (3e53b REVIEW-l2-maker r2 N).
    fn names_of_the_download(text: &str) -> usize {
        let code: String = text
            .lines()
            .filter(|l| !l.trim_start().starts_with("//"))
            .flat_map(str::chars)
            .filter(|c| !c.is_whitespace())
            .collect();
        let m = concat!("provider_", "fetch");
        let mut n = code.matches(&format!("{m}::run")).count()
            + code.matches(&format!("{m}::*")).count()
            + code.matches(&format!("{m}as")).count();
        for (i, _) in code.match_indices(&format!("{m}::{{")) {
            // the whole list up to its own closing brace, nested lists included
            let rest = code.get(i + m.len() + 3..).unwrap_or("");
            let mut depth = 1usize;
            let end = rest
                .char_indices()
                .find(|&(_, c)| {
                    match c {
                        '{' => depth += 1,
                        '}' => depth -= 1,
                        _ => {}
                    }
                    depth == 0
                })
                .map_or(rest.len(), |(k, _)| k);
            n += rest
                .get(..end)
                .unwrap_or("")
                .split([',', '{', '}'])
                .filter(|t| *t == "run" || t.starts_with("runas") || t.starts_with("selfas"))
                .count();
        }
        n
    }

    #[test]
    fn every_way_of_naming_the_download_is_counted() {
        let m = concat!("provider_", "fetch");
        for (src, want) in [
            (format!("use crate::{m}::*;\nrun(a);"), 1),
            (format!("use crate::{m} as pf;\npf::run(a);"), 1),
            (format!("use crate::{m}::{{\n    self,\n    run,\n}};"), 1),
            (format!("use crate::{m}::{{run as go}};"), 1),
            (format!("let f = crate::{m} :: run;"), 1),
            (format!("use crate::{m}::{{self as pf}};\npf::run(a);"), 1),
            (
                format!("use crate::{m}::{{maker::{{fetch_measured}}, run}};"),
                1,
            ),
            (format!("// {m}::run\nuse crate::{m}::{{self, Fetch}};"), 0),
        ] {
            assert_eq!(names_of_the_download(&src), want, "reason: missed {src}");
        }
    }

    /// Nothing but `fetch_measured` can call `provider_fetch::run`: it is private to that module,
    /// this file is its child, and in aibd's sources this file is its one caller (the scan is a
    /// second guard; 3e53b / f163c REVIEW-l2-maker r3).
    #[test]
    fn only_fetch_measured_calls_the_download_with_a_signer() {
        let fetch = include_str!("../provider_fetch.rs");
        assert!(fetch.contains("\nfn run("), "reason: run_public");
        assert!(!fetch.contains("\npub fn run("), "reason: run_public");
        assert!(
            !fetch.contains("\npub(crate) fn run("),
            "reason: run_public"
        );
        // every child of provider_fetch sees run: the only ones are this file and the tests
        // (3e53b r3)
        let mut kids: Vec<String> = Vec::new();
        for l in fetch.lines().map(str::trim_start) {
            if l.starts_with("//") {
                continue;
            }
            for piece in l.split([';', '{']) {
                let p = piece.trim_start();
                let p = p
                    .strip_prefix("pub(crate) ")
                    .or_else(|| p.strip_prefix("pub(super) "))
                    .or_else(|| p.strip_prefix("pub "))
                    .unwrap_or(p);
                if let Some(name) = p.strip_prefix("mod ") {
                    kids.push(name.trim().to_owned());
                }
            }
        }
        assert_eq!(kids, ["maker", "tests"], "reason: another_child");
        // every aibd source file: only this one names the download, once (3e53b Nb)
        let mut callers = Vec::new();
        let mut dirs = vec![std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src")];
        while let Some(d) = dirs.pop() {
            for e in std::fs::read_dir(&d).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    dirs.push(p);
                    continue;
                }
                if p.extension().is_none_or(|x| x != "rs") || p.ends_with("provider_fetch.rs") {
                    continue;
                }
                let text = std::fs::read_to_string(&p).unwrap();
                let name = p.file_name().unwrap().to_string_lossy().into_owned();
                for _ in 0..names_of_the_download(&text) {
                    callers.push(name.clone());
                }
            }
        }
        assert_eq!(callers, ["maker.rs"], "reason: more_callers");
    }

    /// The production path refuses a file whose hash is measured but which the maker did not
    /// sign: the signer inside cannot be swapped for one that says yes (3e53b N2).
    #[test]
    fn the_production_fetch_refuses_an_unsigned_file_with_a_measured_hash() {
        let Some(platform) = provider_fetch::this_platform() else {
            return; // not offered on this platform: run refuses before any check
        };
        let body: &'static [u8] = b"not a program the maker signed\n";
        let sha = hex(body);
        let name = if platform.starts_with("win32-") {
            "claude.exe"
        } else {
            "claude"
        };
        let url: &'static str = Box::leak(
            format!("https://downloads.claude.ai/claude-code-releases/9.9.9/{platform}/{name}")
                .into_boxed_str(),
        );
        let m = Measured {
            provider: "claude",
            version: "9.9.9",
            platform,
            url,
            sha256: Box::leak(sha.into_boxed_str()),
            size: body.len() as u64,
        };
        let data = tempfile::tempdir().unwrap();
        let got = fetch_measured(
            data.path(),
            &m,
            &Bytes(body),
            &AtomicBool::new(false),
            &Progress::default(),
        );
        assert_eq!(
            got,
            Err(provider_fetch::ERR_UNSIGNED),
            "reason: kept_unsigned"
        );
        assert!(
            !provider_fetch::placed_dir(data.path(), &FetchRow::from(&m))
                .join(name)
                .exists(),
            "reason: placed_unsigned"
        );
    }
}
