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

/// The refusal when a program in aibd's providers folder has no row in the measured table.
pub const ERR_UNMEASURED: &str = "provider_unmeasured";

/// The refusal when the data folder, the providers folder or a provider / version folder on
/// the way to a placed program is a link or junction (3e53b r10): aibd starts its programs
/// only from its own folders. A customer who made the data folder a link gets this word.
pub const ERR_LINKED: &str = "provider_path_linked";

/// One line on what to do about a refusal word, for the seat's reply and the API message;
/// empty when there is nothing the customer can do (f163c REVIEW-…-1c836c88 N1).
pub fn what_to_do(word: &str) -> &'static str {
    match word {
        ERR_LINKED => "(AIB 的資料夾或其下的 providers / 品牌 / 版本夾是連結(symlink / junction);請把 AIB 的資料夾設成實際位置，不要指向連結)",
        ERR_UNMEASURED => "(這個版本不在量過的表上;請用 AIB 裝的版本)",
        provider_fetch::ERR_VERIFY => "(放好的程式和量過的不一樣，或開席設定指到的不是那支程式;請讓 AIB 重新安裝;若重裝後仍是這個錯，開席設定指錯了，請聯絡我們協助重設)",
        _ => "",
    }
}

/// Opening a seat (L1 / L2 接點; 3e53b REVIEW-w2policy-90856 L1): when the program the policy
/// names is one aibd placed (`<data>/providers/<provider>/<version>/<name>`), check it against
/// this machine's row of the measured table again, now, and give back the checked handle; the
/// caller keeps it until the seat's program has started (on Windows nobody can change the file
/// meanwhile). A program found elsewhere is the customer's own: `None`, the policy rules it.
pub fn placed_for_seat(data: &Path, program: &Path) -> Result<Option<File>, &'static str> {
    let row = |p: &str, v: &str| {
        let m = aib_seatopen::measured_for(p, provider_fetch::this_platform()?)?;
        (m.version == v).then(|| FetchRow::from(m))
    };
    placed_check(data, program, &row)
}

fn placed_check(
    data: &Path,
    program: &Path,
    row_for: &dyn Fn(&str, &str) -> Option<FetchRow>,
) -> Result<Option<File>, &'static str> {
    // the folder placed_dir puts every provider under, as written and as the disk names it
    let root = data.join("providers");
    let root_real = std::fs::canonicalize(&root).ok();
    let real = std::fs::canonicalize(program).ok();
    let under =
        |p: &Path| p.starts_with(&root) || root_real.as_ref().is_some_and(|r| p.starts_with(r));
    // in the folder by its own name or by where it really is (a link out of the folder, a
    // link into it, another letter case on Windows): either way it must be a placed program
    if !under(program) && !real.as_deref().is_some_and(under) {
        return Ok(None);
    }
    let (Some(root_real), Some(real)) = (root_real, real) else {
        return Err(provider_fetch::ERR_VERIFY);
    };
    let Ok(rest) = real.strip_prefix(&root_real) else {
        return Err(provider_fetch::ERR_VERIFY);
    };
    let parts: Vec<&str> = rest.iter().filter_map(|c| c.to_str()).collect();
    let [provider, version, _] = parts.as_slice() else {
        return Err(provider_fetch::ERR_VERIFY);
    };
    let row = row_for(provider, version).ok_or(ERR_UNMEASURED)?;
    // the policy names exactly this row's placed program, as written (3e53b 05:13 point 1),
    // and no step of that path from the providers folder down is a link or junction: a step
    // changed after the check would change what argv[0] runs (3e53b r6 L2)
    let dir = provider_fetch::placed_dir(data, &row);
    let want = dir.join(provider_fetch::program_name(&row.provider));
    let same = program == want && std::fs::canonicalize(&want).is_ok_and(|w| w == real);
    // <data> itself too (3e53b r8 N1)
    let steps = [
        data.to_path_buf(),
        root.clone(),
        root.join(&row.provider),
        dir,
    ];
    let no_link = steps.iter().all(|d| {
        std::fs::symlink_metadata(d).is_ok_and(|m| m.is_dir() && !m.file_type().is_symlink())
    });
    let plain = std::fs::symlink_metadata(program).is_ok_and(|m| m.file_type().is_file());
    if !no_link {
        return Err(ERR_LINKED);
    }
    if !same || !plain {
        return Err(provider_fetch::ERR_VERIFY);
    }
    provider_fetch::verify_placed(&row, &real).map(Some)
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

    /// Each refusal word a customer can act on says what to do; others say nothing
    /// (f163c REVIEW-…-1c836c88 N1, 3e53b r11).
    #[test]
    fn a_refusal_the_customer_can_fix_says_how() {
        assert!(
            what_to_do(ERR_LINKED).contains("實際位置"),
            "reason: linked_no_hint"
        );
        assert!(
            !what_to_do(ERR_UNMEASURED).is_empty(),
            "reason: unmeasured_no_hint"
        );
        assert!(
            !what_to_do(provider_fetch::ERR_VERIFY).is_empty(),
            "reason: verify_no_hint"
        );
        assert_eq!(what_to_do("program_not_allowed"), "");
    }

    /// Opening a seat checks a program aibd placed against the table again and hands back the
    /// checked handle; a changed file, an unlisted version or a wrong shape is refused; a
    /// program elsewhere is left to the policy (3e53b REVIEW-w2policy-90856 L1).
    #[test]
    fn a_seat_open_checks_a_placed_program_again_and_leaves_others_to_the_policy() {
        let body: &[u8] = b"placed program bytes\n";
        let data = tempfile::tempdir().unwrap();
        let name = provider_fetch::program_name("claude");
        let dir = data.path().join("providers").join("claude").join("9.9.9");
        std::fs::create_dir_all(&dir).unwrap();
        let exe = dir.join(&name);
        std::fs::write(&exe, body).unwrap();
        let sha = hex(body);
        let row_for = |p: &str, v: &str| {
            (p == "claude" && v == "9.9.9").then(|| FetchRow {
                provider: "claude".into(),
                version: "9.9.9".into(),
                platform: "darwin-arm64".into(),
                url: String::new(),
                sha256: sha.clone(),
                size: body.len() as u64,
            })
        };
        let held = placed_check(data.path(), &exe, &row_for);
        assert!(
            matches!(held, Ok(Some(_))),
            "reason: placed_refused {held:?}"
        );
        drop(held);

        // a version the table does not list
        let other = data.path().join("providers").join("claude").join("1.0.0");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join(&name), body).unwrap();
        assert_eq!(
            placed_check(data.path(), &other.join(&name), &row_for).map(|f| f.is_some()),
            Err(ERR_UNMEASURED),
            "reason: unlisted_version_opened"
        );

        // a placed file changed after it was placed
        std::fs::write(&exe, b"changed after it was placed\n").unwrap();
        assert_eq!(
            placed_check(data.path(), &exe, &row_for).map(|f| f.is_some()),
            Err(provider_fetch::ERR_VERIFY),
            "reason: changed_file_opened"
        );

        // not the provider/version/name shape
        let odd = data.path().join("providers").join("claude").join("stray");
        std::fs::write(&odd, body).unwrap();
        assert_eq!(
            placed_check(data.path(), &odd, &row_for).map(|f| f.is_some()),
            Err(provider_fetch::ERR_VERIFY),
            "reason: odd_shape_opened"
        );

        // another file in the version folder: not the row's program
        let stray = dir.join("not-the-program");
        std::fs::write(&stray, body).unwrap();
        assert_eq!(
            placed_check(data.path(), &stray, &row_for).map(|f| f.is_some()),
            Err(provider_fetch::ERR_VERIFY),
            "reason: other_name_opened"
        );

        // a link in the providers folder that leads out of it, and a name that climbs out
        #[cfg(unix)]
        {
            std::fs::write(&exe, body).unwrap();
            let evil = data.path().join("evil");
            std::fs::write(&evil, body).unwrap();
            let link = dir.join("link");
            std::os::unix::fs::symlink(&evil, &link).unwrap();
            assert_eq!(
                placed_check(data.path(), &link, &row_for).map(|f| f.is_some()),
                Err(provider_fetch::ERR_VERIFY),
                "reason: link_out_opened"
            );
            // a link to the placed program itself, from outside: still a link, refused
            let alias = data.path().join("alias");
            std::os::unix::fs::symlink(&exe, &alias).unwrap();
            assert_eq!(
                placed_check(data.path(), &alias, &row_for).map(|f| f.is_some()),
                Err(provider_fetch::ERR_VERIFY),
                "reason: link_in_opened"
            );
            let climb = data.path().join("providers").join("..").join("evil");
            assert_eq!(
                placed_check(data.path(), &climb, &row_for).map(|f| f.is_some()),
                Err(provider_fetch::ERR_VERIFY),
                "reason: climb_opened"
            );

            // the providers folder itself a link (a junction on Windows): the path as written
            // and the file it reaches both look right, but the link could be pointed elsewhere
            // between the check and the start (3e53b r6 L2)
            let other = tempfile::tempdir().unwrap();
            let store = other.path().join("store");
            let vdir = store.join("claude").join("9.9.9");
            std::fs::create_dir_all(&vdir).unwrap();
            std::fs::write(vdir.join(&name), body).unwrap();
            std::os::unix::fs::symlink(&store, other.path().join("providers")).unwrap();
            let via = other
                .path()
                .join("providers")
                .join("claude")
                .join("9.9.9")
                .join(&name);
            assert_eq!(
                placed_check(other.path(), &via, &row_for).map(|f| f.is_some()),
                Err(ERR_LINKED),
                "reason: linked_step_opened"
            );
        }
        // the data folder itself a link: everything under it looks right, still refused with
        // the word that says so (3e53b r10)
        #[cfg(unix)]
        {
            let other = tempfile::tempdir().unwrap();
            let real_data = other.path().join("real");
            let vdir = real_data.join("providers").join("claude").join("9.9.9");
            std::fs::create_dir_all(&vdir).unwrap();
            std::fs::write(vdir.join(&name), body).unwrap();
            let linked = other.path().join("data");
            std::os::unix::fs::symlink(&real_data, &linked).unwrap();
            let via = linked
                .join("providers")
                .join("claude")
                .join("9.9.9")
                .join(&name);
            assert_eq!(
                placed_check(&linked, &via, &row_for).map(|f| f.is_some()),
                Err(ERR_LINKED),
                "reason: linked_data_opened"
            );
        }
        // the same on Windows, with a directory link (a junction is a reparse point too, and
        // `is_symlink` is true for both; the runner can make links, so no skip)
        #[cfg(windows)]
        {
            let other = tempfile::tempdir().unwrap();
            let store = other.path().join("store");
            let vdir = store.join("claude").join("9.9.9");
            std::fs::create_dir_all(&vdir).unwrap();
            std::fs::write(vdir.join(&name), body).unwrap();
            std::os::windows::fs::symlink_dir(&store, other.path().join("providers"))
                .expect("make a directory link (the Windows runner allows it)");
            let via = other
                .path()
                .join("providers")
                .join("claude")
                .join("9.9.9")
                .join(&name);
            assert_eq!(
                placed_check(other.path(), &via, &row_for).map(|f| f.is_some()),
                Err(ERR_LINKED),
                "reason: linked_step_opened"
            );
        }

        // the customer's own program elsewhere: the policy rules it
        let own = data.path().join("elsewhere");
        std::fs::write(&own, body).unwrap();
        assert_eq!(
            placed_check(data.path(), &own, &row_for).map(|f| f.is_some()),
            Ok(false),
            "reason: own_program_checked"
        );
    }

    /// Opening a seat goes through the check above before the program starts (source check:
    /// the call sits in `start_session` before the session is started).
    #[test]
    fn opening_a_seat_calls_the_placed_check_before_the_program_starts() {
        let seats = include_str!("../seats.rs");
        let start = seats
            .find("async fn start_session(")
            .expect("start_session");
        let body = seats.get(start..).unwrap_or("");
        let check = body.find(concat!("maker::placed_for_", "seat(&data, &program)"));
        let run = body.find("start_gated(");
        assert!(
            matches!((check, run), (Some(c), Some(r)) if c < r),
            "reason: seat_open_unchecked"
        );
    }
}
