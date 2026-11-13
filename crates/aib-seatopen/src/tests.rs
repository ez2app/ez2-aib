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
    assert_eq!(
        (s.cabin, s.again),
        (Cabin::FromEnvironment { instance: None }, true)
    );
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
        Cabin::FromEnvironment {
            instance: Some("x".into())
        }
    );
    // Codex #3: AIB_PROBE_LIVE set and not empty = --probe-live
    assert!(open(&d, &[("AIB_PROBE_LIVE", "1")]).probe_live);
    assert!(!open(&d, &[("AIB_PROBE_LIVE", "")]).probe_live);
    assert_eq!(
        open(&d, &[("EZ2_AIB_INSTANCE", "")]).cabin,
        Cabin::Default,
        "empty = unset"
    );
    // a cabin the environment names is name-checked before it is looked up (043dd M2),
    // but not when the words ask for a new one (1.x does not look it up then)
    assert_eq!(
        with(&d, &[("EZ2_AIB_INSTANCE", "Foo")]),
        Err(OpenError::CabinChars("Foo".into()))
    );
    assert_eq!(
        open(
            &["claude", "safe", "dev", "new"],
            &[("EZ2_AIB_INSTANCE", "Foo")]
        )
        .cabin,
        Cabin::New
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
        (
            vec!["claude", "safe", "dev", "--instance", "Bad_"],
            E::CabinChars(s("Bad_")),
        ),
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
        // so is the --instance name (1.x aib_cabin_pick_named checks it first; 043dd M2)
        (
            vec!["foo", "fast", "Dev", "--dry-run", "--instance", "Bad_"],
            E::CabinChars("Bad_".into()),
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

/// A caller with a cabin lookup and a kill switch, recording what it was asked.
struct Fake {
    cabin: Result<Option<String>, OpenError>,
    kill: Option<String>,
    asked: std::cell::RefCell<Vec<String>>,
}

impl Fake {
    fn new(cabin: Result<Option<String>, OpenError>, kill: Option<&str>) -> Fake {
        Fake {
            cabin,
            kill: kill.map(str::to_string),
            asked: Default::default(),
        }
    }
}

impl Context for Fake {
    fn resolve_cabin(&self, q: &CabinQuery<'_>) -> Result<Option<String>, OpenError> {
        self.asked.borrow_mut().push(format!(
            "named={:?} env={:?} new={} dry={}",
            q.named, q.from_env, q.new, q.dry_run
        ));
        self.cabin.clone()
    }
    fn killswitch(&self) -> Option<String> {
        self.kill.clone()
    }
}

fn ctx(a: &[&str], env: &[(&str, &str)], c: &Fake) -> Result<Outcome, OpenError> {
    let env: Vec<(String, String)> = env
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    parse_open_in(
        &args(a),
        move |k| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()),
        c,
    )
}

/// Codex 11-13 #1: the cabin lookup and the kill switch come where 1.x has them, so the
/// first refusal is still 1.x's.
#[test]
fn the_cabin_lookup_and_the_kill_switch_come_where_1x_has_them() {
    let no_cabin = || Fake::new(Err(OpenError::Cabin("aib: no such cabin".into())), None);
    // the cabin lookup comes before the word count and every name check
    for a in [
        &["claude", "safe", "boss", "--instance", "zz"][..],
        &["claude", "safe", "--instance", "zz"],
        &["foo", "fast", "Dev", "zz"],
    ] {
        assert_eq!(
            ctx(a, &[], &no_cabin()),
            Err(OpenError::Cabin("aib: no such cabin".into())),
            "{a:?}"
        );
    }
    // but after the flags and the fourth word's own check
    assert_eq!(
        ctx(&["claude", "safe", "dev", "Bad"], &[], &no_cabin()),
        Err(OpenError::CabinChars("Bad".into()))
    );
    let kill = || Fake::new(Ok(None), Some("stop"));
    // the kill switch comes after the word count and the flag clashes ...
    assert_eq!(
        ctx(&["claude", "safe"], &[], &kill()),
        Err(OpenError::Arity)
    );
    assert_eq!(
        ctx(&["claude", "safe", "dev", "new", "--again"], &[], &kill()),
        Err(OpenError::Usage)
    );
    // ... and before the AI, the mode and the role
    for a in [
        &["foo", "safe", "dev"][..],
        &["claude", "fast", "dev"],
        &["claude", "safe", "boss"],
        // a batch count too large is refused after it (043dd L1: 1.x gives rc 3)
        &["claude", "safe", "dev", "99999999999sits"],
    ] {
        let e = ctx(a, &[], &kill()).unwrap_err();
        assert_eq!(
            (e.rc(), &e),
            (3, &OpenError::Killswitch("stop".into())),
            "{a:?}"
        );
    }
}

#[test]
fn what_the_cabin_lookup_is_asked_and_what_it_gives_back() {
    let asked = |a: &[&str], env: &[(&str, &str)]| {
        let f = Fake::new(Ok(None), None);
        let _ = ctx(a, env, &f);
        f.asked.into_inner()
    };
    let d = ["claude", "safe", "dev"];
    assert_eq!(
        asked(&["claude", "safe", "dev", "6ddf"], &[]),
        ["named=Some(\"6ddf\") env=None new=false dry=false"]
    );
    // the primary by hand is not looked up; nor an env cabin that is empty, new or main
    assert_eq!(
        asked(&["claude", "safe", "dev", "main"], &[]),
        ["named=None env=None new=false dry=false"]
    );
    for v in ["", "new", "main"] {
        assert_eq!(
            asked(&d, &[("EZ2_AIB_INSTANCE", v)]),
            ["named=None env=None new=false dry=false"],
            "{v:?}"
        );
    }
    assert_eq!(
        asked(&d, &[("EZ2_AIB_INSTANCE", "dab9"), ("AIB_DRY_RUN", "1")]),
        ["named=None env=Some(\"dab9\") new=false dry=true"]
    );
    // asking for a new cabin: the environment's is not looked up
    assert_eq!(
        asked(
            &["claude", "safe", "dev", "new"],
            &[("EZ2_AIB_INSTANCE", "dab9")]
        ),
        ["named=None env=None new=true dry=false"]
    );
    // a full name given back replaces the one looked up
    let full = "20261113-035938-dab9";
    let f = Fake::new(Ok(Some(full.into())), None);
    let open = |a: &[&str], env: &[(&str, &str)]| match ctx(a, env, &f) {
        Ok(Outcome::Open(s)) => s.cabin,
        other => panic!("{a:?}: {other:?}"),
    };
    assert_eq!(
        open(&["claude", "safe", "dev", "dab9"], &[]),
        named(full, false)
    );
    assert_eq!(
        open(&d, &[("EZ2_AIB_INSTANCE", "dab9")]),
        Cabin::FromEnvironment {
            instance: Some(full.into())
        }
    );
}

/// 043dd M4: with no cabin named and no new one asked, the cabin the socket points at
/// (given back by the lookup unasked) is the cabin; a new one or an environment cabin
/// wins over it, as in 1.x `aib_apply_socket_instance`.
#[test]
fn a_cabin_the_socket_points_at_is_taken() {
    let f = Fake::new(Ok(Some("x".into())), None);
    let open = |a: &[&str], env: &[(&str, &str)]| match ctx(a, env, &f) {
        Ok(Outcome::Open(s)) => s.cabin,
        other => panic!("{a:?}: {other:?}"),
    };
    assert_eq!(
        open(&["claude", "safe", "dev", "--again"], &[]),
        named("x", false)
    );
    assert_eq!(open(&["claude", "safe", "dev"], &[]), named("x", false));
    assert_eq!(open(&["claude", "safe", "dev", "new"], &[]), Cabin::New);
    assert_eq!(
        open(&["claude", "safe", "dev"], &[("EZ2_AIB_INSTANCE", "main")]),
        Cabin::FromEnvironment {
            instance: Some("main".into())
        }
    );
    assert_eq!(
        open(&["claude", "safe", "dev", "main"], &[]),
        named("main", true)
    );
    // what the socket points at is shape-checked like any cabin
    let bad = Fake::new(Ok(Some("-x".into())), None);
    assert_eq!(
        ctx(&["claude", "safe", "dev"], &[], &bad),
        Err(OpenError::Instance("-x".into()))
    );
}

/// A caller whose bot check refuses codexbot (no thread bound).
struct Bot;

impl Context for Bot {
    fn resolve_cabin(&self, _: &CabinQuery<'_>) -> Result<Option<String>, OpenError> {
        Ok(None)
    }
    fn killswitch(&self) -> Option<String> {
        None
    }
    fn provider_preflight(&self, provider: &str) -> Result<(), OpenError> {
        match provider {
            "codexbot" => Err(OpenError::Preflight("aib: codexbot needs a thread".into())),
            _ => Ok(()),
        }
    }
}

/// 043dd M3: the edition and bot checks come after the AI's name, before the mode.
#[test]
fn the_bot_checks_come_between_the_ai_and_the_mode() {
    let run = |a: &[&str]| parse_open_in(&args(a), |_: &str| None, &Bot);
    let e = run(&["codexbot", "fast", "dev"]).unwrap_err();
    assert_eq!(
        (e.rc(), e.key(), e.to_string()),
        (2, None, "aib: codexbot needs a thread".to_string())
    );
    assert_eq!(
        run(&["foo", "fast", "dev"]),
        Err(OpenError::Provider("foo".into()))
    );
    assert_eq!(
        run(&["claude", "fast", "dev"]),
        Err(OpenError::Mode("fast".into()))
    );
    // words only: no bot check
    assert_eq!(
        p(&["codexbot", "fast", "dev"]),
        Err(OpenError::Mode("fast".into()))
    );
}

/// The refusals 1.x prints as plain text, word for word as they are in its source.
#[test]
fn plain_refusals_match_the_1x_source_word_for_word() {
    let read =
        |p: &str| String::from_utf8_lossy(&std::fs::read(repo().join(p)).unwrap()).into_owned();
    let aib = read("aib/src/cockpit/aib");
    let cabin = read("aib/src/cockpit/cabin_resolve.sh");
    assert!(aib.contains(&OpenError::Thread("$_thr".into()).to_string()));
    assert!(aib.contains(&OpenError::ReservedCabin("%s".into()).to_string()));
    assert!(aib.contains(&OpenError::Model.to_string()));
    // cabin_resolve.sh prints the label (`instance`) through %s as well
    let label = |e: OpenError| e.to_string().replacen("instance", "%s", 1);
    assert!(cabin.contains(&label(OpenError::CabinEmpty)));
    assert!(cabin.contains(&label(OpenError::CabinChars("%s".into()))));
    assert_eq!(
        OpenError::Killswitch("x".into()).key(),
        Some(("aib.err.killswitch", Some(("value", "x"))))
    );
    assert_eq!(OpenError::Cabin("m".into()).to_string(), "m");
}
