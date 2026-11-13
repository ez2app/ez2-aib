// Row tables read best as vec literals of word lists.
#![allow(clippy::useless_vec)]

use super::*;
use std::path::PathBuf;

fn args(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}

fn with(a: &[&str], env: &[(&str, &str)]) -> Result<Outcome, OpenError> {
    let env: Vec<(String, String)> = env
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    parse_open(&args(a), move |k| {
        env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone())
    })
}

fn p(a: &[&str]) -> Result<Outcome, OpenError> {
    with(a, &[])
}

fn spec(a: &[&str]) -> OpenSpec {
    match p(a) {
        Ok(Outcome::Open(s)) => s,
        other => panic!("{a:?}: {other:?}"),
    }
}

fn named(n: &str, main_explicit: bool) -> Cabin {
    Cabin::Named {
        name: n.into(),
        main_explicit,
    }
}

const UUID: &str = "0b6c8e4e-2f7c-4c3e-9d3a-1c2b3a4d5e6f";

/// Words that open a seat, and what they mean (cases from test-seat-open-defaults,
/// test-short-cabin, test-open-dm-panel, test-gameworld, test-codex-model-requested).
#[test]
fn accepted_words_and_what_they_mean() {
    let s = spec(&["claude", "safe", "dev"]);
    assert_eq!(
        (s.provider.as_str(), s.mode.as_str(), s.role.as_str()),
        ("claude", "safe", "dev")
    );
    assert_eq!(
        (s.cabin, s.dm, s.batch, s.again),
        (Cabin::Default, Dm::Auto, None, false)
    );
    assert_eq!(spec(&["claude", "safe", "dev", "new"]).cabin, Cabin::New);
    assert_eq!(
        spec(&["claude", "safe", "dev", "main"]).cabin,
        named("main", true)
    );
    assert_eq!(
        spec(&["claude", "safe", "dev", "6ddf"]).cabin,
        named("6ddf", false)
    );
    assert_eq!(
        spec(&["claude", "safe", "dev2"]).role,
        "dev2",
        "a role family takes a number"
    );
    // `0sits` is not a batch count (N starts 1-9): it is a cabin name
    assert_eq!(
        spec(&["claude", "safe", "dev", "0sits"]).cabin,
        named("0sits", false)
    );
    let s = spec(&["claude", "safe", "dev", "--again"]);
    assert_eq!((s.cabin, s.again), (Cabin::FromEnvironment, true));
    // --instance new = a new cabin; --instance main is the primary, by hand
    assert_eq!(
        spec(&["--instance=new", "codex", "dgrs", "qa"]).cabin,
        Cabin::New
    );
    assert_eq!(
        spec(&["claude", "safe", "dev", "--instance", "main"]).cabin,
        named("main", true)
    );
    // trailing dm / gameworld, in either order
    let s = spec(&["claude", "dgrs", "dev", "dm"]);
    assert_eq!(
        (s.dm, s.cabin, s.gameworld),
        (Dm::On, Cabin::Default, false)
    );
    let s = spec(&["claude", "safe", "dev", "x1", "gameworld", "dm"]);
    assert_eq!(
        (s.dm, s.cabin, s.gameworld),
        (Dm::On, named("x1", false), true)
    );
    assert_eq!(spec(&["claude", "safe", "dev", "--no-dm"]).dm, Dm::Off);
    // flags are read first, the trailing word after: `dm` wins over `--no-dm`
    assert_eq!(spec(&["claude", "safe", "dev", "dm", "--no-dm"]).dm, Dm::On);
    // batch
    let s = spec(&["claude", "safe", "dev", "3sits"]);
    assert_eq!((s.batch, s.cabin), (Some(3), Cabin::Default));
    let s = spec(&["claude", "safe", "dev", "12sits", "new"]);
    assert_eq!((s.batch, s.cabin), (Some(12), Cabin::New));
    // flags anywhere
    let s = spec(&[
        "--model",
        "claude-opus-4.1",
        "claude",
        "safe",
        "--reports-to=f1fae",
        "dev",
        "--thread",
        UUID,
        "--dry-run",
        "--probe-live",
    ]);
    assert_eq!(s.model.as_deref(), Some("claude-opus-4.1"));
    assert_eq!(s.reports_to.as_deref(), Some("f1fae"));
    assert_eq!(s.thread.as_deref(), Some(UUID));
    assert!(s.dry_run && s.probe_live);
    assert_eq!(
        spec(&["codex", "safe", "dev", "--model=~gpt-x:latest/1"])
            .model
            .as_deref(),
        Some("~gpt-x:latest/1")
    );
}

#[test]
fn the_environment_read_through_the_lookup() {
    let open = |a: &[&str], env: &[(&str, &str)]| match with(a, env) {
        Ok(Outcome::Open(s)) => s,
        other => panic!("{a:?} {env:?}: {other:?}"),
    };
    let d = ["claude", "safe", "dev"];
    assert!(open(&d, &[("AIB_DRY_RUN", "1")]).dry_run);
    assert!(
        !open(&d, &[("AIB_DRY_RUN", "yes")]).dry_run,
        "only 1 means dry"
    );
    assert_eq!(
        open(&d, &[("EZ2_AIB_INSTANCE", "x")]).cabin,
        Cabin::FromEnvironment
    );
    assert_eq!(
        open(&d, &[("EZ2_AIB_INSTANCE", "")]).cabin,
        Cabin::Default,
        "empty = unset"
    );
    // captain roles and their families; the escape hatch
    let cap = [("EZ2_AIB_CAPTAIN_ROLES", "cap  lead")];
    assert_eq!(open(&["claude", "safe", "lead2"], &cap).role, "lead2");
    assert_eq!(
        with(&["claude", "safe", "boss"], &cap),
        Err(OpenError::RoleUndefined("boss".into()))
    );
    assert_eq!(
        open(
            &["claude", "safe", "zzz"],
            &[("AIB_ALLOW_CUSTOM_ROLE", "1")]
        )
        .role,
        "zzz"
    );
    assert_eq!(
        with(
            &["claude", "safe", "zzz"],
            &[("AIB_ALLOW_CUSTOM_ROLE", "true")]
        ),
        Err(OpenError::RoleUndefined("zzz".into()))
    );
}

#[test]
fn help_and_version_stop_the_flag_loop_where_they_stand() {
    assert_eq!(p(&["-h", "--bogus"]), Ok(Outcome::Help));
    assert_eq!(p(&["claude", "--version"]), Ok(Outcome::Version));
    assert_eq!(
        p(&["--bogus", "-h"]),
        Err(OpenError::Usage),
        "an earlier bad flag is reported first"
    );
}

/// Each row: words → the one refusal 1.x gives first.
#[test]
fn refusals_one_per_row() {
    use OpenError as E;
    let s = |v: &str| v.to_string();
    let rows: Vec<(Vec<&str>, OpenError)> = vec![
        (vec![], E::Arity),
        (vec!["claude", "safe"], E::Arity),
        (vec!["claude", "safe", "dev", "x", "y"], E::Arity),
        (vec!["foo", "safe"], E::UnknownCmd(s("foo"))),
        (vec!["foo", "safe", "dev"], E::Provider(s("foo"))),
        (vec!["claude", "fast", "dev"], E::Mode(s("fast"))),
        (vec!["claude", "safe", "Dev"], E::Role(s("Dev"))),
        (vec!["claude", "safe", ""], E::Role(s(""))),
        (vec!["claude", "safe", "boss"], E::RoleUndefined(s("boss"))),
        // 043dd: `3sits` in the role place is a role, not a batch
        (
            vec!["claude", "safe", "3sits"],
            E::RoleUndefined(s("3sits")),
        ),
        (
            vec!["claude", "safe", "dev", "Bad"],
            E::CabinChars(s("Bad")),
        ),
        (vec!["claude", "safe", "dev", ""], E::CabinEmpty),
        (vec!["claude", "safe", "dev", "-x-"], E::Usage), // a word with a dash first is a flag
        (
            vec!["claude", "safe", "dev", "--instance=-x"],
            E::Instance(s("-x")),
        ),
        // dm / gameworld twice: the second one is left in the cabin place
        (
            vec!["claude", "safe", "dev", "dm", "dm"],
            E::ReservedCabin(s("dm")),
        ),
        (
            vec!["claude", "safe", "dev", "gameworld", "gameworld"],
            E::ReservedCabin(s("gameworld")),
        ),
        (vec!["claude", "safe", "dev", "x", "dm", "dm"], E::Arity),
        (
            vec!["claude", "safe", "dev", "x", "--instance", "y"],
            E::Usage,
        ),
        (vec!["claude", "safe", "dev", "new", "--again"], E::Usage),
        (
            vec!["claude", "safe", "dev", "--new", "--instance=y"],
            E::Usage,
        ),
        (
            vec!["claude", "safe", "dev", "--instance=y", "new"],
            E::Usage,
        ),
        (
            vec!["claude", "safe", "dev", "--reports-to", "AB12"],
            E::ReportsTo(s("AB12")),
        ),
        (
            vec!["claude", "safe", "dev", "--reports-to", "abc"],
            E::ReportsTo(s("abc")),
        ),
        (vec!["claude", "safe", "dev", "--reports-to="], E::Usage),
        (vec!["claude", "safe", "dev", "--reports-to"], E::Usage),
        (vec!["claude", "safe", "dev", "--model="], E::Usage),
        (vec!["claude", "safe", "dev", "--model", "-x"], E::Model),
        (vec!["claude", "safe", "dev", "--model", "a b"], E::Model),
        (vec!["claude", "safe", "dev", "--thread="], E::Thread(s(""))),
        (vec!["claude", "safe", "dev", "--thread", ""], E::Usage),
        (
            vec!["claude", "safe", "dev", "--thread", "nope"],
            E::Thread(s("nope")),
        ),
        (vec!["claude", "safe", "dev", "--instance="], E::Usage),
        (vec!["claude", "safe", "dev", "--bogus"], E::Usage),
        (vec!["claude", "safe", "dev", "99999999999sits"], E::Usage), // stricter than 1.x
    ];
    for (a, want) in rows {
        assert_eq!(p(&a), Err(want), "{a:?}");
    }
}

/// 043dd: with several faults in the words, the one 1.x reports first.
#[test]
fn several_faults_the_first_one_wins() {
    use OpenError as E;
    let rows: Vec<(Vec<&str>, OpenError)> = vec![
        (vec!["--bogus", "foo"], E::Usage), // flags first
        (vec!["--thread", "x", "foo"], E::Thread("x".into())), // in flag order
        (vec!["foo", "fast", "Dev"], E::Provider("foo".into())), // then the AI
        (vec!["claude", "fast", "Dev"], E::Mode("fast".into())), // then the mode
        (
            vec!["claude", "safe", "Dev", "--reports-to", "X"],
            E::Role("Dev".into()),
        ),
        (
            vec![
                "claude",
                "safe",
                "boss",
                "--reports-to",
                "XY",
                "--model",
                "!",
            ],
            E::RoleUndefined("boss".into()),
        ),
        (
            vec![
                "claude",
                "safe",
                "dev",
                "--reports-to",
                "XY",
                "--instance",
                "-z",
                "--model",
                "!",
            ],
            E::ReportsTo("XY".into()),
        ),
        (
            vec!["claude", "safe", "dev", "--instance", "-z", "--model", "!"],
            E::Instance("-z".into()),
        ),
        // the cabin word is checked before the AI's name
        (
            vec!["foo", "safe", "dev", "Bad"],
            E::CabinChars("Bad".into()),
        ),
        (
            vec!["claude", "safe", "dev", "new", "--instance", "y", "--again"],
            E::Usage,
        ),
    ];
    for (a, want) in rows {
        assert_eq!(p(&a), Err(want), "{a:?}");
    }
}

#[test]
fn every_refusal_is_rc_2_with_the_1x_key_or_text() {
    assert_eq!(OpenError::Usage.rc(), rc::USAGE);
    assert_eq!(OpenError::Model.rc(), 2);
    assert_eq!(OpenError::Arity.key(), Some(("aib.err.usage", None)));
    assert!(OpenError::Arity.shows_usage() && !OpenError::Usage.shows_usage());
    assert_eq!(
        OpenError::UnknownCmd("x".into()).key(),
        Some(("aib.err.unknown_cmd", Some(("cmd", "x"))))
    );
    assert_eq!(
        OpenError::ReportsTo("x".into()).key(),
        Some(("aib.err.reports_to", Some(("seat", "x"))))
    );
    assert_eq!(OpenError::Thread("x".into()).key(), None);
    assert_eq!(
        OpenError::Thread("x".into()).to_string(),
        "aib: --thread 要 UUID(查法:aib bot list),不是:x"
    );
    assert_eq!(
        OpenError::Model.to_string(),
        "aib: requested model is missing or invalid"
    );
    assert_eq!(
        OpenError::Mode("m".into()).to_string(),
        "aib.err.mode mode=m"
    );
    assert_eq!(
        (
            rc::KILLSWITCH,
            rc::CLI_MISSING,
            rc::TMUX_MISSING,
            rc::RESERVE_FAILED
        ),
        (3, 6, 127, 1)
    );
}

#[test]
fn role_families_strip_trailing_digits_only() {
    assert_eq!(role_family("dev2"), "dev");
    assert_eq!(role_family("qa10"), "qa");
    assert_eq!(role_family("2dev"), "2dev");
    assert_eq!(role_family("123"), "123", "all digits stays");
}

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// ROLES is the set of `aib.role.*` keys in the i18n file of this tree (roles.py's
/// source): a role added or removed there turns this red.
#[test]
fn roles_match_the_i18n_keys_in_this_tree() {
    let text = std::fs::read_to_string(repo().join("aib/i18n/zh-TW.json")).unwrap();
    let d: serde_json::Map<String, serde_json::Value> = serde_json::from_str(&text).unwrap();
    let mut keys: Vec<&str> = d
        .keys()
        .filter_map(|k| k.strip_prefix("aib.role."))
        .filter(|r| role_shape(r))
        .collect();
    keys.sort_unstable();
    let mut ours = ROLES.to_vec();
    ours.sort_unstable();
    assert_eq!(ours, keys);
}

/// PROVIDERS and MODES are the 1.x launcher's own lists, in its order.
#[test]
fn providers_and_modes_match_the_1x_launcher() {
    let bytes = std::fs::read(repo().join("aib/src/cockpit/aib")).unwrap();
    let text = String::from_utf8_lossy(&bytes);
    let list = |name: &str| -> Vec<String> {
        let line = text
            .lines()
            .find_map(|l| l.strip_prefix(&format!("{name}=\"")))
            .unwrap_or_else(|| panic!("{name}= not found"));
        let body = line.split('"').next().unwrap();
        body.split_whitespace().map(str::to_string).collect()
    };
    assert_eq!(list("PROVIDERS"), PROVIDERS);
    assert_eq!(list("MODES"), MODES);
}
