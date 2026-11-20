//! Slice 3b-2a (DESIGN-g1-receipt-origacct-b4297-1120 r3 §3 §7): every program the gate
//! starts for one run, as data — its words, its whole environment, where it starts. Nothing
//! is started here; `serve` runs these in order and stops at the first that fails.
//!
//! Outside the sandbox the gate runs git (with a configuration of its own: no hooks, no
//! system or account settings, no credential helper, only the transports a step needs), the
//! machine's lease tool and the pinned cargo's fetch. None of them runs a file of the tree:
//! the tree is cloned without a checkout, what the head changes is read from git's objects
//! (`diff`, `show`), never from files on disk (a `Cargo.lock` in the tree could be a link),
//! and the checkout comes after those checks. Then receipt-run — the machine's own pinned
//! copy, never one that was sent — runs whole inside the sandbox.

use crate::sandbox::{inside, Places, Step};

/// git as the machine has it; only ever run with [`git_env`] and [`SAFE`].
pub const GIT: &str = "/usr/bin/git";
/// The ref the head has in a bundle aibd makes.
pub const BUNDLE_REF: &str = "refs/heads/run";
/// The ref the gate keeps it under in the run's tree.
pub const RUN_REF: &str = "refs/receipt/run";
/// The base's main in the run's tree (it is cloned from the base).
pub const BASE_REF: &str = "refs/remotes/origin/main";

/// Settings every git of the gate starts with: no hook runs, no file-system monitor
/// program, no credential helper, and no transport at all until a step allows one.
pub const SAFE: [&str; 8] = [
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.allow=never",
];

/// What the machine's owner pinned in `<home>/.ez2-aib/receipt-gate.conf` (`key=value`
/// lines; `#` starts a comment line).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    /// The machine's copy of receipt-run.sh, and its SHA-256 (another digest:
    /// `receipt_tool_changed`, nothing runs).
    pub receipt_run: String,
    pub receipt_run_sha256: String,
    /// The toolchain's folder name under `~/.rustup/toolchains`.
    pub toolchain: String,
    /// The lease tool (`aib`).
    pub aib: String,
    pub bash: String,
}

fn hex64(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// A toolchain's folder name: letters, digits, `.`, `_`, `-`; no path.
pub fn toolchain_ok(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && !s.starts_with('.')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

/// Reads the pinned settings. Every key once, no other key, every value of its shape:
/// `config_bad` otherwise (a setting that is half there pins nothing).
pub fn config(text: &str) -> Result<Config, &'static str> {
    const KEYS: [&str; 5] = [
        "receipt_run",
        "receipt_run_sha256",
        "toolchain",
        "aib",
        "bash",
    ];
    let mut got: [Option<&str>; 5] = [None; 5];
    for line in text.lines().map(str::trim) {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (k, v) = line.split_once('=').ok_or("config_bad")?;
        let at = KEYS.iter().position(|x| *x == k).ok_or("config_bad")?;
        if got[at].replace(v).is_some() {
            return Err("config_bad");
        }
    }
    let [Some(receipt_run), Some(sha), Some(toolchain), Some(aib), Some(bash)] = got else {
        return Err("config_bad");
    };
    let paths_ok = [receipt_run, aib, bash]
        .iter()
        .all(|p| crate::sandbox::place_ok(p));
    if !paths_ok || !hex64(sha) || !toolchain_ok(toolchain) {
        return Err("config_bad");
    }
    Ok(Config {
        receipt_run: receipt_run.to_owned(),
        receipt_run_sha256: sha.to_owned(),
        toolchain: toolchain.to_owned(),
        aib: aib.to_owned(),
        bash: bash.to_owned(),
    })
}

/// The seat's five characters of a run id that passed [`crate::run_id_ok`].
pub fn seat_of(run_id: &str) -> &str {
    run_id.split('-').nth(1).unwrap_or_default()
}

/// git's whole environment: nothing of the account (`home` is an empty folder of the gate's,
/// so no `~/.gitconfig`, no `~/.ssh`), no system settings, never a prompt.
pub fn git_env(home: &str) -> Vec<(String, String)> {
    [
        ("HOME", home),
        ("PATH", "/usr/bin:/bin"),
        ("LANG", "C"),
        ("GIT_CONFIG_NOSYSTEM", "1"),
        ("GIT_CONFIG_GLOBAL", "/dev/null"),
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_ASKPASS", "/usr/bin/false"),
        ("GIT_SSH_COMMAND", "/usr/bin/false"),
    ]
    .iter()
    .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
    .collect()
}

fn git(home: &str, cwd: &str, allow: Option<&str>, words: &[&str]) -> Step {
    let mut argv = vec![GIT.to_owned()];
    argv.extend(SAFE.iter().map(|s| (*s).to_owned()));
    if let Some(transport) = allow {
        argv.push("-c".into());
        argv.push(format!("protocol.{transport}.allow=always"));
    }
    argv.extend(words.iter().map(|s| (*s).to_owned()));
    Step {
        argv,
        env: git_env(home),
        cwd: cwd.to_owned(),
    }
}

/// Where the git steps of one run work. All made by the gate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Repo<'a> {
    /// The base bare repository (`recv::base_clean` decides whether it is rebuilt first).
    pub base: &'a str,
    /// The run's tree.
    pub tree: &'a str,
    /// The bundle as received.
    pub bundle: &'a str,
    /// The empty folder that is git's home and the fetch's starting place.
    pub empty: &'a str,
}

/// A new base: a bare repository whose one remote is the public repository's main
/// (the settings `recv::config_clean` takes).
pub fn base_init(r: &Repo<'_>) -> Vec<Step> {
    let g = |words: &[&str]| git(r.empty, r.empty, None, words);
    vec![
        g(&["init", "--bare", "-q", r.base]),
        g(&[
            "--git-dir",
            r.base,
            "config",
            "remote.origin.url",
            crate::recv::BASE_URL,
        ]),
        g(&[
            "--git-dir",
            r.base,
            "config",
            "remote.origin.fetch",
            "+refs/heads/main:refs/heads/main",
        ]),
    ]
}

/// The git steps before anything of the tree is judged, in order: the base's main from the
/// public repository (https, no credentials), the run's tree as a copy of the base with
/// nothing checked out (no hard links: the tested code writes in the tree and must not
/// reach the base's object files through one), the bundle's head into it.
pub fn bring(r: &Repo<'_>) -> Vec<Step> {
    vec![
        git(
            r.empty,
            r.empty,
            Some("https"),
            &["--git-dir", r.base, "fetch", "-q", "--no-tags", "origin"],
        ),
        git(
            r.empty,
            r.empty,
            Some("file"),
            &[
                "clone",
                "-q",
                "--no-hardlinks",
                "--no-checkout",
                "--no-tags",
                r.base,
                r.tree,
            ],
        ),
        git(
            r.empty,
            r.tree,
            Some("file"),
            &[
                "fetch",
                "-q",
                "--no-tags",
                r.bundle,
                &format!("{BUNDLE_REF}:{RUN_REF}"),
            ],
        ),
    ]
}

/// Prints the commit the bundle brought (it must be the head the request named).
pub fn head_of(r: &Repo<'_>) -> Step {
    git(
        r.empty,
        r.tree,
        None,
        &[
            "rev-parse",
            "--verify",
            "-q",
            &format!("{RUN_REF}^{{commit}}"),
        ],
    )
}

/// Prints, NUL between them, every path the head changes since it left the base's main
/// (three dots: against their merge base; a rename as a removal and an addition): what
/// `sandbox::changed_refused` judges. Against the merge base, not main's tip: a head cut
/// from an older main is not refused for what main changed after it. A head that shares
/// no history with main has no merge base and the step fails (refused).
pub fn changed(r: &Repo<'_>) -> Step {
    git(
        r.empty,
        r.tree,
        None,
        &[
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            &format!("{BASE_REF}...{RUN_REF}"),
            "--",
        ],
    )
}

/// Prints the head's `Cargo.lock` from git's objects: what `sandbox::lock_ok` judges.
pub fn lock_at(r: &Repo<'_>) -> Step {
    git(
        r.empty,
        r.tree,
        None,
        &["show", &format!("{RUN_REF}:Cargo.lock")],
    )
}

/// Checks the head out, after the checks.
pub fn checkout(r: &Repo<'_>) -> Step {
    git(
        r.empty,
        r.tree,
        None,
        &["checkout", "-q", "--detach", RUN_REF],
    )
}

/// The paths of [`changed`]'s output.
pub fn paths(z: &[u8]) -> Result<Vec<&str>, &'static str> {
    z.split(|b| *b == 0)
        .filter(|p| !p.is_empty())
        .map(|p| std::str::from_utf8(p).map_err(|_| "tree_refused"))
        .collect()
}

fn lease(cfg: &Config, home: &str, words: Vec<String>) -> Step {
    let mut argv = vec![cfg.aib.clone(), "lease".to_owned()];
    argv.extend(words);
    Step {
        argv,
        env: vec![
            ("HOME".into(), home.to_owned()),
            (
                "PATH".into(),
                "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin".into(),
            ),
            ("LANG".into(), "C".into()),
        ],
        cwd: home.to_owned(),
    }
}

/// The machine's lease, as every receipt takes it: `queue add`, then `acquire` until it
/// answers 0, `release` at the end. A web receipt waits its turn like any other (no
/// priority). `pid` is the supervisor's.
pub fn lease_steps(cfg: &Config, home: &str, run_id: &str, pid: u32) -> [Step; 3] {
    let what = format!("web receipt {run_id}");
    let pid = pid.to_string();
    let named = |verb: &[&str], ttl: &str| {
        let mut words: Vec<String> = verb.iter().map(|s| (*s).to_owned()).collect();
        for w in [
            "--owner",
            seat_of(run_id),
            "--what",
            what.as_str(),
            "--run-id",
            run_id,
            "--pid",
            pid.as_str(),
            "--ttl",
            ttl,
        ] {
            words.push(w.to_owned());
        }
        lease(cfg, home, words)
    };
    [
        named(&["queue", "add"], "7200"),
        named(&["acquire"], "10800"),
        lease(
            cfg,
            home,
            vec!["release".into(), "--run-id".into(), run_id.to_owned()],
        ),
    ]
}

/// The run itself: the machine's receipt-run, whole, inside the sandbox, in its gate mode
/// (no lease, no fetch: `RR_GATE=1`), for the whole workspace with no knife table.
pub fn receipt(cfg: &Config, p: &Places<'_>, profile_file: &str, run_id: &str) -> Step {
    let tree = format!("{}/tree", p.work);
    let mut s = inside(
        p,
        profile_file,
        &[
            cfg.bash.as_str(),
            cfg.receipt_run.as_str(),
            run_id,
            "ws",
            "-",
            "-",
            tree.as_str(),
        ],
    );
    s.env.push(("RR_GATE".into(), "1".into()));
    s.env.push(("OWNER".into(), seat_of(run_id).to_owned()));
    s
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const ID: &str = "web-a9136-012345678";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn conf() -> String {
        format!(
            "# pinned by the owner\nreceipt_run=/Users/owner/.ez2-aib/receipt-run.sh\nreceipt_run_sha256={SHA}\n\ntoolchain=1.99.1-aarch64-apple-darwin\naib=/Users/owner/.local/bin/aib\nbash=/bin/bash\n"
        )
    }

    fn repo() -> Repo<'static> {
        Repo {
            base: "/Users/owner/wt.noindex/web-base.git",
            tree: "/Users/owner/wt.noindex/web-a9136-012345678/tree",
            bundle: "/Users/owner/wt.noindex/web-a9136-012345678.bundle",
            empty: "/Users/owner/wt.noindex/web-a9136-012345678.empty",
        }
    }

    #[test]
    fn the_pinned_settings_are_whole_or_nothing() {
        let c = config(&conf()).unwrap();
        assert_eq!(c.receipt_run, "/Users/owner/.ez2-aib/receipt-run.sh");
        assert_eq!(c.toolchain, "1.99.1-aarch64-apple-darwin");
        assert_eq!(c.bash, "/bin/bash");
        for (from, to) in [
            ("bash=/bin/bash\n", ""),
            ("bash=/bin/bash\n", "bash=/bin/bash\nbash=/bin/sh\n"),
            ("bash=/bin/bash\n", "bash=/bin/bash\nshell=/bin/sh\n"),
            ("bash=/bin/bash\n", "bash=bash\n"),
            ("bash=/bin/bash\n", "bash=/bin/ba sh\n"),
            ("aaaaaaaa", "AAAAAAAA"),
            ("toolchain=1.99.1", "toolchain=../1.99.1"),
            ("toolchain=1.99.1", "toolchain=x/1.99.1"),
            ("aib=/Users", "aib=~/Users"),
            ("receipt_run=", "receipt_run "),
        ] {
            let bad = conf().replacen(from, to, 1);
            assert_ne!(bad, conf());
            assert_eq!(config(&bad), Err("config_bad"), "reason: taken {to:?}");
        }
        assert_eq!(config(""), Err("config_bad"));
    }

    fn flag<'a>(s: &'a Step, name: &str) -> Vec<&'a str> {
        s.argv
            .windows(2)
            .filter(|w| w[0] == "-c" && w[1].starts_with(name))
            .map(|w| w[1].as_str())
            .collect()
    }

    #[test]
    fn every_git_runs_with_the_gates_own_settings() {
        let r = repo();
        let mut all = base_init(&r);
        all.extend(bring(&r));
        all.extend([head_of(&r), changed(&r), lock_at(&r), checkout(&r)]);
        assert_eq!(all.len(), 10);
        for s in &all {
            assert_eq!(s.argv[0], GIT);
            assert_eq!(&s.argv[1..9], SAFE, "reason: unsafe_git {:?}", s.argv);
            let env = |k: &str| s.env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
            // nothing of the account: its home is the empty folder
            assert_eq!(env("HOME"), Some(r.empty), "reason: account_home");
            assert_eq!(env("GIT_CONFIG_GLOBAL"), Some("/dev/null"));
            assert_eq!(env("GIT_CONFIG_NOSYSTEM"), Some("1"));
            assert_eq!(env("GIT_TERMINAL_PROMPT"), Some("0"));
            assert!(env("SSH_AUTH_SOCK").is_none());
            assert_eq!(s.env.len(), 8, "reason: more_env");
        }
        assert!(SAFE.contains(&"core.hooksPath=/dev/null"));
        assert!(SAFE.contains(&"protocol.allow=never"));
        assert!(SAFE.contains(&"credential.helper="));
    }

    #[test]
    fn a_transport_is_open_only_for_the_step_that_needs_it() {
        let r = repo();
        let b = bring(&r);
        // the public repository over https, the base and the bundle as files, nothing else
        assert_eq!(
            flag(&b[0], "protocol."),
            ["protocol.allow=never", "protocol.https.allow=always"]
        );
        assert_eq!(
            flag(&b[1], "protocol."),
            ["protocol.allow=never", "protocol.file.allow=always"]
        );
        assert_eq!(
            flag(&b[2], "protocol."),
            ["protocol.allow=never", "protocol.file.allow=always"]
        );
        for s in base_init(&r)
            .iter()
            .chain(&[head_of(&r), changed(&r), lock_at(&r), checkout(&r)])
        {
            assert_eq!(
                flag(s, "protocol."),
                ["protocol.allow=never"],
                "reason: transport_open {:?}",
                s.argv
            );
        }
        // no step allows ssh or git's own protocol
        let mut all = base_init(&r);
        all.extend(b);
        assert!(!all.iter().any(|s| s
            .argv
            .iter()
            .any(|w| w.contains("ssh.allow") || w.contains("git.allow"))));
    }

    #[test]
    fn the_tree_is_a_copy_with_nothing_checked_out_until_it_is_judged() {
        let r = repo();
        let b = bring(&r);
        assert_eq!(
            &b[0].argv[b[0].argv.len() - 6..],
            ["--git-dir", r.base, "fetch", "-q", "--no-tags", "origin"]
        );
        let clone = &b[1].argv;
        assert!(
            clone.contains(&"--no-checkout".to_owned()),
            "reason: checked_out_early"
        );
        assert!(
            clone.contains(&"--no-hardlinks".to_owned()),
            "reason: base_objects_shared"
        );
        assert_eq!(&clone[clone.len() - 2..], [r.base, r.tree]);
        // the bundle's one ref, under a name of the gate's
        assert_eq!(
            &b[2].argv[b[2].argv.len() - 2..],
            [r.bundle, "refs/heads/run:refs/receipt/run"]
        );
        assert_eq!(b[2].cwd, r.tree);
        // judged from git's objects, never from a file on disk
        let c = changed(&r);
        assert_eq!(
            &c.argv[9..],
            [
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                "refs/remotes/origin/main...refs/receipt/run",
                "--"
            ],
            "reason: main_tip_judged"
        );
        assert_eq!(
            &lock_at(&r).argv[9..],
            ["show", "refs/receipt/run:Cargo.lock"]
        );
        assert_eq!(
            &head_of(&r).argv[9..],
            ["rev-parse", "--verify", "-q", "refs/receipt/run^{commit}"]
        );
        assert_eq!(
            &checkout(&r).argv[9..],
            ["checkout", "-q", "--detach", RUN_REF]
        );
        // the base is made with the one remote config_clean takes
        let init = base_init(&r);
        assert_eq!(&init[0].argv[9..], ["init", "--bare", "-q", r.base]);
        assert_eq!(
            init[1].argv.last().map(String::as_str),
            Some(crate::recv::BASE_URL)
        );
        assert!(crate::recv::config_clean(&format!(
            "[core]\n\tbare = true\n[remote \"origin\"]\n\turl = {}\n\tfetch = {}\n",
            init[1].argv.last().unwrap(),
            init[2].argv.last().unwrap()
        )));
    }

    #[test]
    fn changed_paths_are_read_whole() {
        assert_eq!(
            paths(b"a/b.rs\0Cargo.lock\0").unwrap(),
            ["a/b.rs", "Cargo.lock"]
        );
        assert_eq!(paths(b"").unwrap(), Vec::<&str>::new());
        // a name with a line end in it stays one name
        assert_eq!(
            paths(b"x\n.cargo/config.toml\0").unwrap(),
            ["x\n.cargo/config.toml"]
        );
        assert!(
            crate::sandbox::changed_refused(paths(b"x\n.cargo/config.toml\0").unwrap()).is_ok()
        );
        assert!(
            crate::sandbox::changed_refused(paths(b"x\0.cargo/config.toml\0").unwrap()).is_err()
        );
        // a name that is not text cannot be judged: refused
        assert_eq!(
            paths(b"ok\0\xff\xfe\0"),
            Err("tree_refused"),
            "reason: unreadable_name_taken"
        );
    }

    #[test]
    fn a_web_receipt_waits_its_turn_under_the_seats_code() {
        let c = config(&conf()).unwrap();
        let [add, acquire, release] = lease_steps(&c, "/Users/owner", ID, 4242);
        assert_eq!(
            add.argv,
            [
                "/Users/owner/.local/bin/aib",
                "lease",
                "queue",
                "add",
                "--owner",
                "a9136",
                "--what",
                "web receipt web-a9136-012345678",
                "--run-id",
                ID,
                "--pid",
                "4242",
                "--ttl",
                "7200",
            ]
        );
        assert_eq!(
            &acquire.argv[..3],
            ["/Users/owner/.local/bin/aib", "lease", "acquire"]
        );
        assert!(acquire.argv.contains(&"4242".to_owned()));
        assert_eq!(&release.argv[2..], ["release", "--run-id", ID]);
        for s in [&add, &acquire] {
            assert!(
                !s.argv.contains(&"--priority".to_owned()),
                "reason: jumps_the_queue"
            );
        }
        assert_eq!(seat_of(ID), "a9136");
        assert_eq!(seat_of("web-zz0q9-012345678-2"), "zz0q9");
    }

    #[test]
    fn the_run_is_the_machines_receipt_run_whole_inside_the_sandbox() {
        let c = config(&conf()).unwrap();
        let p = Places {
            home: "/Users/owner",
            work: "/Users/owner/wt.noindex/web-a9136-012345678",
            toolchain: "/Users/owner/.rustup/toolchains/1.99.1-aarch64-apple-darwin",
            gh: None,
        };
        let s = receipt(&c, &p, "/Users/owner/wt.noindex/web-a9136-012345678.sb", ID);
        assert_eq!(
            s.argv,
            [
                "/usr/bin/sandbox-exec",
                "-f",
                "/Users/owner/wt.noindex/web-a9136-012345678.sb",
                "/bin/bash",
                "/Users/owner/.ez2-aib/receipt-run.sh",
                ID,
                "ws",
                "-",
                "-",
                "/Users/owner/wt.noindex/web-a9136-012345678/tree",
            ],
            "reason: outside_the_sandbox"
        );
        let env = |k: &str| s.env.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(env("RR_GATE"), Some("1"), "reason: takes_its_own_lease");
        assert_eq!(env("OWNER"), Some("a9136"));
        assert_eq!(
            env("TMPDIR"),
            Some("/Users/owner/wt.noindex/web-a9136-012345678/tmp")
        );
        assert_eq!(s.cwd, "/Users/owner/wt.noindex/web-a9136-012345678/tree");
    }
}
