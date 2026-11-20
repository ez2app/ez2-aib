//! Slice 3a (DESIGN-g1-receipt-origacct-b4297-1120 r3 §3; Andy 11-20 12:32: receipts run in the
//! test machine's own account, no receipt account): what keeps a sent tree's code away from
//! that account's keys. Pure text and checks; nothing is run here.
//!
//! A receipt builds and tests code a seat wrote (build.rs, tests, proc-macros: any program),
//! as the person whose machine it is. Two things stand between that code and the account:
//!
//! 1. Before anything of the tree is run: [`changed_refused`] and [`lock_ok`] refuse a tree
//!    that changes which program is run or where crates come from, and the one step outside
//!    the sandbox ([`fetch`]) runs the pinned toolchain's cargo by its full path from an empty
//!    folder, so the tree's `rust-toolchain.toml` and `.cargo/config.toml` are read by nothing
//!    (5d33a PREREAD-g1origacct r2 H1; shown on Pro 13: the rustup proxy ran the tree's cargo,
//!    the pinned one did not).
//! 2. Everything after it runs under [`profile`] (macOS `sandbox-exec`): reads and mach
//!    services are lists of what is allowed, not of what is refused (probe sbx1: with reads
//!    open a test prints any file into the receipt, with mach open it asks LaunchServices to
//!    run a program outside), the network is this machine only and not its sshd or aibd, and
//!    writes are the run's own folder.
//!
//! The profile is text in this program, so the gate's own digest pins it. What it does not
//! do, said plainly: refusing a program by its path stops the plain call, not a copy of the
//! program (the copy still reads no key, reaches no network and finds no keychain service);
//! and the `.out` is written where the tested code writes, so a green one is a signal, not
//! evidence (5d33a PREREAD-receiptgate L2: the merge gate reads CI).

/// The crates.io index as `Cargo.lock` names it: the only source a sent tree may have.
pub const CRATES_IO: &str = "registry+https://github.com/rust-lang/crates.io-index";

/// Ports on this machine a run may not reach: sshd, and aibd's (the test one and the real one).
pub const CLOSED_PORTS: [u16; 3] = [22, 47788, 7788];

/// The mach services a run may look up (probe sbx2: what cargo, rustc, the linker and aibd's
/// tests ask for). Nothing that starts a program (LaunchServices, AppleEvents), holds a
/// secret (SecurityServer, securityd, secd) or the pasteboard.
pub const MACH: [&str; 5] = [
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.notification_center",
    "com.apple.system.logger",
    "com.apple.logd",
    "com.apple.diagnosticd",
];

/// Programs refused by path inside the sandbox (a copy runs, and gets nowhere: module doc).
pub const NO_EXEC: [&str; 13] = [
    "/usr/bin/security",
    "/usr/bin/ssh",
    "/usr/bin/scp",
    "/usr/bin/sftp",
    "/usr/bin/curl",
    "/usr/bin/nc",
    "/usr/bin/open",
    "/usr/bin/osascript",
    "/usr/bin/pbpaste",
    "/usr/bin/pbcopy",
    "/bin/launchctl",
    "/usr/bin/at",
    "/usr/bin/crontab",
];

/// System folders a build reads.
const READ_SYSTEM: [&str; 15] = [
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library/Developer",
    "/Library/Apple",
    "/private/etc",
    "/private/var/db/timezone",
    "/private/var/db/xcode_select_link",
    "/dev",
    "/opt/homebrew/Cellar",
    "/opt/homebrew/opt",
    "/opt/homebrew/bin",
    "/opt/homebrew/lib",
    "/Applications/Xcode.app",
];

/// Whether a path of the tree (as git names it, `/` between parts) is one a sent tree may not
/// add or change: `rust-toolchain`, `rust-toolchain.toml` or anything at or under a `.cargo`
/// folder, at any depth. Compared without case: the test machines' file system finds
/// `.Cargo/Config.toml` for cargo too. A `.cargo` that is a link to another folder is the
/// path `.cargo` itself and is refused the same.
pub fn path_refused(path: &str) -> bool {
    path.split(['/', '\\']).any(|part| {
        let p = part.to_ascii_lowercase();
        p == ".cargo" || p == "rust-toolchain" || p == "rust-toolchain.toml"
    })
}

/// The paths a head adds, changes, renames or removes against its base: none may be
/// [`path_refused`] (`tree_refused`). A removal counts: taking the pinned toolchain file
/// away changes what builds it as much as editing it.
pub fn changed_refused<'a>(changed: impl IntoIterator<Item = &'a str>) -> Result<(), &'static str> {
    if changed.into_iter().any(path_refused) {
        return Err("tree_refused");
    }
    Ok(())
}

/// Whether every package of a `Cargo.lock` comes from crates.io or the tree itself (no
/// `source` line): a git dependency, another registry or a `[patch]` to a git URL is fetched
/// outside the sandbox with the account's own git and keys, so the tree is refused.
pub fn lock_ok(lock: &str) -> bool {
    lock.lines()
        .map(str::trim)
        .filter(|l| l.starts_with("source"))
        .all(|l| {
            l.strip_prefix("source")
                .map(str::trim_start)
                .and_then(|r| r.strip_prefix('='))
                .map(str::trim)
                .is_some_and(|v| {
                    v.strip_prefix('"').and_then(|v| v.strip_suffix('"')) == Some(CRATES_IO)
                })
        })
}

/// The folders a run is made of. All are picked by the gate, never sent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Places<'a> {
    /// The account's home.
    pub home: &'a str,
    /// The run's own folder (`<home>/wt.noindex/<run id>`): its tree, target, temporary
    /// files and `.out`. The only place a run writes.
    pub work: &'a str,
    /// The pinned toolchain (`<home>/.rustup/toolchains/<name>`).
    pub toolchain: &'a str,
    /// Where `gh` is on this machine, when it has one (refused by path).
    pub gh: Option<&'a str>,
}

/// A path that goes into the profile's text as it is: absolute, of letters, digits and
/// `/ . _ - +` only (no quote, blank, bracket or backslash can end a string or a rule), no
/// `..` part and no doubled or trailing `/`.
pub fn place_ok(p: &str) -> bool {
    p.len() > 1
        && p.len() <= 512
        && p.starts_with('/')
        && p.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-+".contains(&b))
        && p[1..]
            .split('/')
            .all(|part| !part.is_empty() && part != "..")
}

/// `p` and every folder above it, for the rules that let a path be walked to.
fn walk(p: &str) -> Vec<&str> {
    let mut all = vec!["/"];
    all.extend(
        p.match_indices('/')
            .filter(|(i, _)| *i > 0)
            .map(|(i, _)| &p[..i]),
    );
    all.push(p);
    all
}

/// `p` inside a profile regex: the characters [`place_ok`] lets through that mean something
/// there are escaped.
fn rx(p: &str) -> String {
    let mut out = String::with_capacity(p.len() + 8);
    for c in p.chars() {
        if c == '.' || c == '+' {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

fn quoted(paths: &[&str], word: &str) -> String {
    paths.iter().map(|p| format!(" ({word} \"{p}\")")).collect()
}

/// The `sandbox-exec` profile of one run (module doc, point 2). `bad_place`: a path that
/// cannot go into the text, a run folder outside the home or one that is the home.
pub fn profile(p: &Places<'_>) -> Result<String, &'static str> {
    let Places {
        home,
        work,
        toolchain,
        gh,
    } = *p;
    let below = |inner: &str, outer: &str| {
        inner
            .strip_prefix(outer)
            .is_some_and(|rest| rest.starts_with('/'))
    };
    if ![home, work, toolchain].iter().all(|x| place_ok(x))
        || gh.is_some_and(|g| !place_ok(g))
        || !below(work, home)
        || !below(toolchain, home)
        || below(toolchain, work)
        || below(work, toolchain)
    {
        return Err("bad_place");
    }
    let cargo_home = format!("{home}/.cargo");
    let mut walked: Vec<&str> = walk(work);
    walked.pop(); // the run folder itself is a subpath below
    for w in walk(toolchain).into_iter().chain(walk(&cargo_home)) {
        if !walked.contains(&w) {
            walked.push(w);
        }
    }
    let mut no_exec: Vec<&str> = NO_EXEC.to_vec();
    if let Some(g) = gh {
        no_exec.push(g);
    }
    let cache = format!("^{}/\\.(package-cache|global-cache)", rx(&cargo_home));
    let closed: String = CLOSED_PORTS
        .iter()
        .map(|port| format!(" (remote ip \"localhost:{port}\")"))
        .collect();
    let mach: String = MACH
        .iter()
        .map(|m| format!(" (global-name \"{m}\")"))
        .collect();
    let lines = [
        "(version 1)".to_owned(),
        "(deny default)".to_owned(),
        "(import \"system.sb\")".to_owned(),
        "(allow process-fork)".to_owned(),
        "(allow process-exec)".to_owned(),
        "(allow signal (target same-sandbox))".to_owned(),
        "(allow process-info* (target same-sandbox))".to_owned(),
        "(allow system-sched)".to_owned(),
        "(allow sysctl-read)".to_owned(),
        "(allow ipc-posix-shm* ipc-posix-sem*)".to_owned(),
        "(allow pseudo-tty)".to_owned(),
        "(allow file-ioctl)".to_owned(),
        format!("(allow mach-lookup{mach})"),
        "(allow file-read-metadata)".to_owned(),
        format!("(allow file-read*{})", quoted(&walked, "literal")),
        format!("(allow file-read*{})", quoted(&READ_SYSTEM, "subpath")),
        format!(
            "(allow file-read* (subpath \"{toolchain}\") (subpath \"{cargo_home}/registry\") (subpath \"{cargo_home}/git\") (regex #\"{cache}\") (subpath \"{work}\"))"
        ),
        format!("(allow file-write* (subpath \"{work}\"))"),
        "(allow file-write* (literal \"/dev/null\") (literal \"/dev/dtracehelper\") (regex #\"^/dev/tty\") (regex #\"^/dev/ptmx\") (regex #\"^/dev/pty\"))".to_owned(),
        format!("(allow file-write* (regex #\"{cache}\"))"),
        "(allow network-bind (local ip \"localhost:*\"))".to_owned(),
        "(allow network-inbound (local ip \"localhost:*\"))".to_owned(),
        "(allow network-outbound (remote ip \"localhost:*\"))".to_owned(),
        format!("(allow network* (remote unix-socket (subpath \"{work}\")))"),
        format!("(allow network* (local unix-socket (subpath \"{work}\")))"),
        format!("(deny network-outbound{closed})"),
        format!(
            "(deny file-read* (subpath \"{cargo_home}/registry/credentials\") (regex #\"^{}/credentials\"))",
            rx(&cargo_home)
        ),
        format!("(deny process-exec{})", quoted(&no_exec, "literal")),
    ];
    let mut text = lines.join("\n");
    text.push('\n');
    Ok(text)
}

/// One program to start: its words, its whole environment and the folder it starts in.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Step {
    pub argv: Vec<String>,
    pub env: Vec<(String, String)>,
    pub cwd: String,
}

fn toolchain_name(toolchain: &str) -> &str {
    toolchain.rsplit('/').next().unwrap_or(toolchain)
}

/// The one step outside the sandbox: the pinned toolchain's cargo, by its full path (not the
/// rustup proxy, which runs whatever toolchain the tree names), started in `empty` (a new
/// folder outside the tree: cargo reads `.cargo/config.toml` from where it starts upward),
/// downloading what `Cargo.lock` lists and running nothing of the tree. The environment is
/// these four and nothing of the caller's (`SSH_AUTH_SOCK`, tokens).
pub fn fetch(p: &Places<'_>, empty: &str) -> Step {
    let tc = p.toolchain;
    Step {
        argv: vec![
            format!("{tc}/bin/cargo"),
            "fetch".into(),
            "--locked".into(),
            "--manifest-path".into(),
            format!("{}/tree/Cargo.toml", p.work),
        ],
        env: vec![
            ("HOME".into(), p.home.to_owned()),
            ("PATH".into(), format!("{tc}/bin:/usr/bin:/bin")),
            ("RUSTUP_TOOLCHAIN".into(), toolchain_name(tc).to_owned()),
            ("LANG".into(), "C".into()),
        ],
        cwd: empty.to_owned(),
    }
}

/// A program of the run, inside the sandbox: `sandbox-exec -f <profile file> <argv…>`, started
/// in the tree, temporary files in the run's folder, cargo told there is no network (it has
/// none) and the same pinned toolchain first on the path.
pub fn inside(p: &Places<'_>, profile_file: &str, argv: &[&str]) -> Step {
    let tc = p.toolchain;
    let mut words = vec![
        "/usr/bin/sandbox-exec".to_owned(),
        "-f".to_owned(),
        profile_file.to_owned(),
    ];
    words.extend(argv.iter().map(|a| (*a).to_owned()));
    Step {
        argv: words,
        env: vec![
            ("HOME".into(), p.home.to_owned()),
            (
                "PATH".into(),
                format!("{tc}/bin:/usr/bin:/bin:/opt/homebrew/bin"),
            ),
            ("RUSTUP_TOOLCHAIN".into(), toolchain_name(tc).to_owned()),
            ("LANG".into(), "C".into()),
            ("TMPDIR".into(), format!("{}/tmp", p.work)),
            ("CARGO_NET_OFFLINE".into(), "true".into()),
        ],
        cwd: format!("{}/tree", p.work),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const HOME: &str = "/Users/owner";
    const WORK: &str = "/Users/owner/wt.noindex/web-a9136-012345678";
    const TC: &str = "/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin";

    fn places() -> Places<'static> {
        Places {
            home: HOME,
            work: WORK,
            toolchain: TC,
            gh: Some("/opt/homebrew/bin/gh"),
        }
    }

    #[test]
    fn a_tree_that_picks_its_toolchain_or_cargo_settings_is_refused() {
        for p in [
            "rust-toolchain.toml",
            "rust-toolchain",
            ".cargo/config.toml",
            ".cargo/config",
            "crates/aibd/.cargo/config.toml",
            "crates/aibd/rust-toolchain.toml",
            ".cargo",
            ".Cargo/Config.toml",
            "Rust-Toolchain.TOML",
            ".cargo/anything",
        ] {
            assert!(path_refused(p), "reason: taken {p}");
            assert_eq!(changed_refused(["src/lib.rs", p]), Err("tree_refused"));
        }
        for p in [
            "Cargo.toml",
            "Cargo.lock",
            "build.rs",
            "crates/aibd/src/cargo.rs",
            "docs/rust-toolchain.toml.md",
            "x.cargo/config.toml",
        ] {
            assert!(!path_refused(p), "reason: refused {p}");
        }
        assert_eq!(changed_refused(["Cargo.toml", "build.rs"]), Ok(()));
        assert_eq!(changed_refused([]), Ok(()));
    }

    #[test]
    fn a_lock_with_a_source_that_is_not_crates_io_is_refused() {
        let ok = format!(
            "version = 4\n\n[[package]]\nname = \"aibd\"\nversion = \"0.1.0\"\n\n[[package]]\nname = \"sha2\"\nversion = \"0.10.9\"\nsource = \"{CRATES_IO}\"\nchecksum = \"x\"\n"
        );
        assert!(lock_ok(&ok));
        assert!(lock_ok(""));
        for other in [
            "git+https://example.invalid/x/y?rev=1#abc",
            "registry+https://example.invalid/index",
            "sparse+https://example.invalid/index/",
            "",
        ] {
            let bad = ok.replace(CRATES_IO, other);
            assert!(!lock_ok(&bad), "reason: taken {other:?}");
        }
        // spelled another way a TOML reader takes
        assert!(!lock_ok("source='git+https://example.invalid/x'\n"));
        assert!(!lock_ok(
            "  source   =   \"git+https://example.invalid/x\"\n"
        ));
        assert!(!lock_ok(&format!("source = \"{CRATES_IO}\" # x\n")));
        // one good line does not cover a bad one
        assert!(!lock_ok(&format!(
            "source = \"{CRATES_IO}\"\nsource = \"git+https://example.invalid/x\"\n"
        )));
    }

    #[test]
    fn a_place_is_a_plain_absolute_path() {
        for ok in [HOME, WORK, TC, "/opt/homebrew/bin/gh", "/a/b+c/d_e-f.g"] {
            assert!(place_ok(ok), "reason: refused {ok}");
        }
        for bad in [
            "",
            "/",
            "relative/x",
            "/Users/a b",
            "/Users/a\"b",
            "/Users/a)b",
            "/Users/a\\b",
            "/Users/a\nb",
            "/Users/../etc",
            "/Users//x",
            "/Users/x/",
            "/Users/名",
        ] {
            assert!(!place_ok(bad), "reason: taken {bad:?}");
        }
        let mut p = places();
        p.home = "/Users/ow\"ner";
        assert_eq!(profile(&p), Err("bad_place"), "reason: quote_in_home_taken");
        // the run folder is inside the home and is not the toolchain
        let mut p = places();
        p.work = "/tmp/web-a9136-012345678";
        assert_eq!(profile(&p), Err("bad_place"), "reason: work_outside_taken");
        let mut p = places();
        p.work = HOME;
        assert_eq!(profile(&p), Err("bad_place"), "reason: work_is_home_taken");
        let mut p = places();
        p.work = "/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin/x";
        assert_eq!(
            profile(&p),
            Err("bad_place"),
            "reason: work_in_toolchain_taken"
        );
        let mut p = places();
        p.gh = Some("/opt/home brew/gh");
        assert_eq!(profile(&p), Err("bad_place"));
    }

    /// The rules of `text` that start with `head` (one rule a line).
    fn rules<'a>(text: &'a str, head: &str) -> Vec<&'a str> {
        text.lines().filter(|l| l.starts_with(head)).collect()
    }

    #[test]
    fn the_profile_allows_by_list_and_writes_only_the_run_folder() {
        let text = profile(&places()).unwrap();
        assert_eq!(text.lines().nth(1), Some("(deny default)"));
        // reads: every rule names where; none is open
        let reads = rules(&text, "(allow file-read*");
        assert_eq!(reads.len(), 3);
        assert!(
            reads.iter().all(|r| r.contains("(literal ")
                || r.contains("(subpath ")
                || r.contains("(regex ")),
            "reason: open_read_taken"
        );
        // the home is walked through, never read below
        assert!(text.contains("(literal \"/Users/owner\")"));
        assert!(!text.contains("(subpath \"/Users/owner\")"));
        assert!(!text.contains("(subpath \"/Users\")"));
        assert!(!text.contains("(subpath \"/\")"));
        for kept_out in [
            ".ssh",
            ".config",
            ".ez2-aib",
            "Library/Keychains",
            ".codex",
            ".claude",
        ] {
            assert!(
                !text.contains(&format!("{HOME}/{kept_out}")),
                "reason: {kept_out} named"
            );
        }
        // of the home only the toolchain, cargo's downloads and the run folder
        let below_home: Vec<&str> = text
            .split("(subpath \"")
            .skip(1)
            .filter_map(|s| s.split('"').next())
            .filter(|s| s.starts_with(HOME))
            .collect();
        for s in &below_home {
            assert!(
                *s == TC
                    || *s == WORK
                    || s.starts_with("/Users/owner/.cargo/registry")
                    || *s == "/Users/owner/.cargo/git",
                "reason: {s} readable"
            );
        }
        // writes: the run folder, the terminal devices, cargo's two lock files
        let writes = rules(&text, "(allow file-write*");
        assert_eq!(writes.len(), 3);
        assert_eq!(
            writes[0],
            format!("(allow file-write* (subpath \"{WORK}\"))")
        );
        assert!(writes[1].contains("/dev/null") && !writes[1].contains("subpath"));
        assert!(writes[2].contains("package-cache") && !writes[2].contains("subpath"));
        // a dot in the home is a dot in the regex, not any character
        assert!(text.contains("#\"^/Users/owner/\\.cargo/\\.(package-cache|global-cache)\""));
    }

    #[test]
    fn the_profile_keeps_mach_the_network_and_exec_closed() {
        let text = profile(&places()).unwrap();
        let mach = rules(&text, "(allow mach-lookup");
        assert_eq!(mach.len(), 1);
        assert_eq!(mach[0].matches("(global-name ").count(), MACH.len());
        for never in [
            "SecurityServer",
            "securityd",
            "secd",
            "launchservices",
            "coreservices",
            "pasteboard",
            "appleevents",
        ] {
            assert!(
                !text
                    .to_ascii_lowercase()
                    .contains(&never.to_ascii_lowercase()),
                "reason: {never} allowed"
            );
        }
        // every network rule names this machine or a socket of the run folder
        let net = rules(&text, "(allow network");
        assert_eq!(net.len(), 5);
        assert!(
            net.iter().all(|r| r.contains("localhost:*")
                || r.contains(&format!("unix-socket (subpath \"{WORK}\")"))),
            "reason: open_network_taken"
        );
        assert!(!text.contains("(allow network*)") && !text.contains("(remote ip \"*:*\")"));
        let closed = rules(&text, "(deny network-outbound");
        assert_eq!(closed.len(), 1);
        for port in CLOSED_PORTS {
            assert!(
                closed[0].contains(&format!("(remote ip \"localhost:{port}\")")),
                "reason: port {port} open"
            );
        }
        // the refusals come after the rules they narrow (the last matching rule decides)
        let at = |needle: &str| text.find(needle).unwrap();
        assert!(at("(deny network-outbound") > at("(allow network-outbound"));
        assert!(at("(deny process-exec") > at("(allow process-exec)"));
        assert!(at("(deny file-read*") > at("(allow file-read* (subpath"));
        let exec = rules(&text, "(deny process-exec");
        for prog in NO_EXEC.iter().chain(&["/opt/homebrew/bin/gh"]) {
            assert!(
                exec[0].contains(&format!("(literal \"{prog}\")")),
                "reason: {prog} runs"
            );
        }
        // signals and process facts stay inside
        assert!(text.contains("(allow signal (target same-sandbox))"));
        assert!(!text.contains("(allow signal)") && !text.contains("(allow process-info*)\n"));
        // no gh on the machine: nothing more is named
        let mut p = places();
        p.gh = None;
        let text = profile(&p).unwrap();
        assert_eq!(
            rules(&text, "(deny process-exec")[0]
                .matches("(literal ")
                .count(),
            NO_EXEC.len()
        );
    }

    #[test]
    fn the_fetch_is_the_pinned_cargo_from_an_empty_folder() {
        let s = fetch(
            &places(),
            "/Users/owner/wt.noindex/web-a9136-012345678.empty",
        );
        assert_eq!(
            s.argv,
            [
                "/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin/bin/cargo",
                "fetch",
                "--locked",
                "--manifest-path",
                "/Users/owner/wt.noindex/web-a9136-012345678/tree/Cargo.toml",
            ]
        );
        // not the rustup proxy, and not started in the tree
        assert!(!s.argv[0].contains("/.cargo/bin/"), "reason: proxy_taken");
        assert!(
            !s.cwd.starts_with(&format!("{WORK}/")) && s.cwd != WORK,
            "reason: tree_cwd_taken"
        );
        let env = |k: &str| s.env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(env("RUSTUP_TOOLCHAIN"), Some("1.99.1-aarch64-apple-darwin"));
        assert_eq!(
            env("PATH"),
            Some("/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin/bin:/usr/bin:/bin")
        );
        assert_eq!(s.env.len(), 4, "reason: more_env_taken");
        assert!(env("SSH_AUTH_SOCK").is_none());
    }

    #[test]
    fn a_program_of_the_run_starts_under_the_profile() {
        let s = inside(
            &places(),
            "/Users/owner/wt.noindex/web-a9136-012345678.sb",
            &[
                "/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin/bin/cargo",
                "test",
                "--offline",
            ],
        );
        assert_eq!(
            &s.argv[..3],
            [
                "/usr/bin/sandbox-exec",
                "-f",
                "/Users/owner/wt.noindex/web-a9136-012345678.sb"
            ],
            "reason: no_profile_taken"
        );
        assert_eq!(s.argv.len(), 6);
        assert_eq!(s.cwd, format!("{WORK}/tree"));
        let env = |k: &str| s.env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(
            env("TMPDIR"),
            Some("/Users/owner/wt.noindex/web-a9136-012345678/tmp")
        );
        assert_eq!(env("CARGO_NET_OFFLINE"), Some("true"));
        assert!(env("PATH").is_some_and(|p| p.starts_with(&format!("{TC}/bin:"))));
        assert!(env("SSH_AUTH_SOCK").is_none());
    }
}
