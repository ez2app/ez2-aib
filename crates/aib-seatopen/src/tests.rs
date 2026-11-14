// Row tables read best as vec literals of word lists.
#![allow(clippy::useless_vec)]

use super::*;
use std::path::PathBuf;

fn args(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}

fn card(uid: &str, name: &str) -> Persona {
    Persona {
        uid: uid.into(),
        name: name.into(),
    }
}

/// The cards the tests open seats with: two share a name (a card list may hold that;
/// 2.0 tells such seats apart on screen with a seat-code suffix, Andy 11-14).
fn cards() -> Vec<Persona> {
    vec![
        card("un0eba", "架構師"),
        card("75kbgj", "驗收"),
        card("0ar525", "Ez2 主機維運"),
        card("aaaaa1", "同名"),
        card("aaaaa2", "同名"),
        // a card whose name is another card's uid: the uid wins
        card("zzzzz9", "un0eba"),
    ]
}

fn with(a: &[&str], env: &[(&str, &str)]) -> Result<Outcome, OpenError> {
    let env: Vec<(String, String)> = env
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    parse_open(
        &args(a),
        move |k| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()),
        &cards(),
    )
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
    let s = spec(&["claude", "safe", "架構師"]);
    assert_eq!(
        (s.provider.as_str(), s.mode.as_str(), s.persona),
        ("claude", "safe", card("un0eba", "架構師"))
    );
    assert_eq!(
        (s.cabin, s.dm, s.batch, s.again),
        (Cabin::Default, Dm::Auto, None, false)
    );
    assert_eq!(spec(&["claude", "safe", "架構師", "new"]).cabin, Cabin::New);
    assert_eq!(
        spec(&["claude", "safe", "架構師", "main"]).cabin,
        named("main", true)
    );
    assert_eq!(
        spec(&["claude", "safe", "架構師", "6ddf"]).cabin,
        named("6ddf", false)
    );
    // a persona by uid, and a name with a space in it (one shell word, quoted)
    assert_eq!(
        spec(&["claude", "safe", "75kbgj"]).persona,
        card("75kbgj", "驗收")
    );
    assert_eq!(
        spec(&["claude", "safe", "Ez2 主機維運"]).persona.uid,
        "0ar525"
    );
    // two cards share a name: the uid picks one
    assert_eq!(
        spec(&["claude", "safe", "aaaaa2"]).persona,
        card("aaaaa2", "同名")
    );
    // a word that is one card's uid and another card's name: the uid wins
    assert_eq!(spec(&["claude", "safe", "un0eba"]).persona.name, "架構師");
    // `0sits` is not a batch count (N starts 1-9): it is a cabin name
    assert_eq!(
        spec(&["claude", "safe", "架構師", "0sits"]).cabin,
        named("0sits", false)
    );
    let s = spec(&["claude", "safe", "架構師", "--again"]);
    assert_eq!(
        (s.cabin, s.again),
        (Cabin::FromEnvironment { instance: None }, true)
    );
    // --instance new = a new cabin; --instance main is the primary, by hand
    assert_eq!(
        spec(&["--instance=new", "codex", "dgrs", "驗收"]).cabin,
        Cabin::New
    );
    assert_eq!(
        spec(&["claude", "safe", "架構師", "--instance", "main"]).cabin,
        named("main", true)
    );
    // trailing dm / gameworld, in either order
    let s = spec(&["claude", "dgrs", "架構師", "dm"]);
    assert_eq!(
        (s.dm, s.cabin, s.gameworld),
        (Dm::On, Cabin::Default, false)
    );
    let s = spec(&["claude", "safe", "架構師", "x1", "gameworld", "dm"]);
    assert_eq!(
        (s.dm, s.cabin, s.gameworld),
        (Dm::On, named("x1", false), true)
    );
    assert_eq!(spec(&["claude", "safe", "架構師", "--no-dm"]).dm, Dm::Off);
    // flags are read first, the trailing word after: `dm` wins over `--no-dm`
    assert_eq!(
        spec(&["claude", "safe", "架構師", "dm", "--no-dm"]).dm,
        Dm::On
    );
    // batch
    let s = spec(&["claude", "safe", "架構師", "3sits"]);
    assert_eq!((s.batch, s.cabin), (Some(3), Cabin::Default));
    let s = spec(&["claude", "safe", "架構師", "12sits", "new"]);
    assert_eq!((s.batch, s.cabin), (Some(12), Cabin::New));
    // flags anywhere
    let s = spec(&[
        "--model",
        "claude-opus-4.1",
        "claude",
        "safe",
        "--reports-to=f1fae",
        "架構師",
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
        spec(&["codex", "safe", "架構師", "--model=~gpt-x:latest/1"])
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
    let d = ["claude", "safe", "架構師"];
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
            &["claude", "safe", "架構師", "new"],
            &[("EZ2_AIB_INSTANCE", "Foo")]
        )
        .cabin,
        Cabin::New
    );
    // the 1.x role switches open nothing any more (Andy 11-14: a persona, not a role)
    for env in [
        ("EZ2_AIB_CAPTAIN_ROLES", "cap lead"),
        ("AIB_ALLOW_CUSTOM_ROLE", "1"),
    ] {
        for w in ["lead2", "dev", "root"] {
            assert_eq!(
                with(&["claude", "safe", w], &[env]),
                Err(OpenError::PersonaMissing(w.into())),
                "{env:?} {w}"
            );
        }
    }
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
        (vec!["claude", "safe", "架構師", "x", "y"], E::Arity),
        (vec!["foo", "safe"], E::UnknownCmd(s("foo"))),
        (vec!["foo", "safe", "架構師"], E::Provider(s("foo"))),
        (vec!["claude", "fast", "架構師"], E::Mode(s("fast"))),
        (vec!["claude", "safe", ""], E::PersonaShape(s(""))),
        (
            vec!["claude", "safe", " 架構師"],
            E::PersonaShape(s(" 架構師")),
        ),
        (
            vec!["claude", "safe", "架構師 "],
            E::PersonaShape(s("架構師 ")),
        ),
        (
            vec!["claude", "safe", "架\t構師"],
            E::PersonaShape(s("架\t構師")),
        ),
        (vec!["claude", "safe", "boss"], E::PersonaMissing(s("boss"))),
        // a 1.x role is not a persona
        (vec!["claude", "safe", "dev"], E::PersonaMissing(s("dev"))),
        // names match exactly: no case folding
        (
            vec!["claude", "safe", "ez2 主機維運"],
            E::PersonaMissing(s("ez2 主機維運")),
        ),
        // the screen's seat-code suffix is not part of the name
        (
            vec!["claude", "safe", "架構師 · a9136"],
            E::PersonaMissing(s("架構師 · a9136")),
        ),
        (
            vec!["claude", "safe", "同名"],
            E::PersonaAmbiguous(
                s("同名"),
                vec![card("aaaaa1", "同名"), card("aaaaa2", "同名")],
            ),
        ),
        // 043dd: `3sits` in the persona place is a persona, not a batch
        (
            vec!["claude", "safe", "3sits"],
            E::PersonaMissing(s("3sits")),
        ),
        (
            vec!["claude", "safe", "架構師", "Bad"],
            E::CabinChars(s("Bad")),
        ),
        (vec!["claude", "safe", "架構師", ""], E::CabinEmpty),
        (vec!["claude", "safe", "架構師", "-x-"], E::Usage), // a word with a dash first is a flag
        (
            vec!["claude", "safe", "架構師", "--instance=-x"],
            E::Instance(s("-x")),
        ),
        // dm / gameworld twice: the second one is left in the cabin place
        (
            vec!["claude", "safe", "架構師", "dm", "dm"],
            E::ReservedCabin(s("dm")),
        ),
        (
            vec!["claude", "safe", "架構師", "gameworld", "gameworld"],
            E::ReservedCabin(s("gameworld")),
        ),
        (vec!["claude", "safe", "架構師", "x", "dm", "dm"], E::Arity),
        (
            vec!["claude", "safe", "架構師", "x", "--instance", "y"],
            E::Usage,
        ),
        (vec!["claude", "safe", "架構師", "new", "--again"], E::Usage),
        (
            vec!["claude", "safe", "架構師", "--new", "--instance=y"],
            E::Usage,
        ),
        (
            vec!["claude", "safe", "架構師", "--instance=y", "new"],
            E::Usage,
        ),
        (
            vec!["claude", "safe", "架構師", "--reports-to", "AB12"],
            E::ReportsTo(s("AB12")),
        ),
        (
            vec!["claude", "safe", "架構師", "--reports-to", "abc"],
            E::ReportsTo(s("abc")),
        ),
        (vec!["claude", "safe", "架構師", "--reports-to="], E::Usage),
        (vec!["claude", "safe", "架構師", "--reports-to"], E::Usage),
        (vec!["claude", "safe", "架構師", "--model="], E::Usage),
        (vec!["claude", "safe", "架構師", "--model", "-x"], E::Model),
        (vec!["claude", "safe", "架構師", "--model", "a b"], E::Model),
        (
            vec!["claude", "safe", "架構師", "--thread="],
            E::Thread(s("")),
        ),
        (vec!["claude", "safe", "架構師", "--thread", ""], E::Usage),
        (
            vec!["claude", "safe", "架構師", "--thread", "nope"],
            E::Thread(s("nope")),
        ),
        (vec!["claude", "safe", "架構師", "--instance="], E::Usage),
        (vec!["claude", "safe", "架構師", "--bogus"], E::Usage),
        (
            vec!["claude", "safe", "架構師", "99999999999sits"],
            E::Usage,
        ), // stricter than 1.x
        (
            vec!["claude", "safe", "架構師", "--instance", "Bad_"],
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
            vec!["claude", "safe", "", "--reports-to", "X"],
            E::PersonaShape("".into()),
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
            E::PersonaMissing("boss".into()),
        ),
        (
            vec![
                "claude",
                "safe",
                "架構師",
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
            vec![
                "claude",
                "safe",
                "架構師",
                "--instance",
                "-z",
                "--model",
                "!",
            ],
            E::Instance("-z".into()),
        ),
        // the cabin word is checked before the AI's name
        (
            vec!["foo", "safe", "架構師", "Bad"],
            E::CabinChars("Bad".into()),
        ),
        // so is the --instance name (1.x aib_cabin_pick_named checks it first; 043dd M2)
        (
            vec!["foo", "fast", "Dev", "--dry-run", "--instance", "Bad_"],
            E::CabinChars("Bad_".into()),
        ),
        (
            vec![
                "claude",
                "safe",
                "架構師",
                "new",
                "--instance",
                "y",
                "--again",
            ],
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

/// B4-1a2: the persona word's longest form, and how the refusals read.
#[test]
fn persona_words_their_limit_and_their_refusals() {
    let long = "人".repeat(PERSONA_MAX_CHARS);
    let mut list = cards();
    list.push(card("long01", &long));
    let run = |w: &str| parse_open(&args(&["claude", "safe", w]), |_: &str| None, &list);
    assert!(matches!(run(&long), Ok(Outcome::Open(s)) if s.persona.uid == "long01"));
    let longer = format!("{long}人");
    assert_eq!(run(&longer), Err(OpenError::PersonaShape(longer.clone())));
    // plain text, rc 2, no 1.x key
    let e = run("同名").unwrap_err();
    assert_eq!((e.rc(), e.key()), (2, None));
    assert_eq!(
        e.to_string(),
        "aib: 有 2 張人格卡都叫「同名」,請改寫 uid:aaaaa1 aaaaa2"
    );
    assert_eq!(
        run("boss").unwrap_err().to_string(),
        "aib: 找不到人格「boss」(第三個字寫人格名稱或 6 碼 uid)"
    );
    assert!(run("").unwrap_err().to_string().contains("最多 64 字"));
    // no cards at all: every persona is missing
    assert_eq!(
        parse_open(&args(&["claude", "safe", "架構師"]), |_: &str| None, &[]),
        Err(OpenError::PersonaMissing("架構師".into()))
    );
}

#[test]
fn find_persona_by_uid_then_by_name() {
    let c = cards();
    assert_eq!(
        find_persona("75kbgj", &c),
        PersonaMatch::Found(card("75kbgj", "驗收"))
    );
    assert_eq!(
        find_persona("驗收", &c),
        PersonaMatch::Found(card("75kbgj", "驗收"))
    );
    assert_eq!(
        find_persona("un0eba", &c),
        PersonaMatch::Found(card("un0eba", "架構師"))
    );
    assert_eq!(find_persona("nope", &c), PersonaMatch::Missing);
    // the same card listed twice is still one card
    let twice = vec![card("bbbbb1", "重複"), card("bbbbb1", "重複")];
    assert_eq!(
        find_persona("重複", &twice),
        PersonaMatch::Found(card("bbbbb1", "重複"))
    );
    // several with one name come back by uid, whatever order the list has
    let rev = vec![card("ccccc2", "x"), card("ccccc1", "x")];
    assert_eq!(
        find_persona("x", &rev),
        PersonaMatch::Ambiguous(vec![card("ccccc1", "x"), card("ccccc2", "x")])
    );
}

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
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
    fn persona(&self, w: &str) -> PersonaMatch {
        find_persona(w, &cards())
    }
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
        ctx(&["claude", "safe", "架構師", "Bad"], &[], &no_cabin()),
        Err(OpenError::CabinChars("Bad".into()))
    );
    let kill = || Fake::new(Ok(None), Some("stop"));
    // the kill switch comes after the word count and the flag clashes ...
    assert_eq!(
        ctx(&["claude", "safe"], &[], &kill()),
        Err(OpenError::Arity)
    );
    assert_eq!(
        ctx(
            &["claude", "safe", "架構師", "new", "--again"],
            &[],
            &kill()
        ),
        Err(OpenError::Usage)
    );
    // ... and before the AI, the mode and the persona
    for a in [
        &["foo", "safe", "架構師"][..],
        &["claude", "fast", "架構師"],
        &["claude", "safe", "boss"],
        // a batch count too large is refused after it (043dd L1: 1.x gives rc 3)
        &["claude", "safe", "架構師", "99999999999sits"],
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
    let d = ["claude", "safe", "架構師"];
    assert_eq!(
        asked(&["claude", "safe", "架構師", "6ddf"], &[]),
        ["named=Some(\"6ddf\") env=None new=false dry=false"]
    );
    // the primary by hand is not looked up; nor an env cabin that is empty, new or main
    assert_eq!(
        asked(&["claude", "safe", "架構師", "main"], &[]),
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
            &["claude", "safe", "架構師", "new"],
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
        open(&["claude", "safe", "架構師", "dab9"], &[]),
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
        open(&["claude", "safe", "架構師", "--again"], &[]),
        named("x", false)
    );
    assert_eq!(open(&["claude", "safe", "架構師"], &[]), named("x", false));
    assert_eq!(open(&["claude", "safe", "架構師", "new"], &[]), Cabin::New);
    assert_eq!(
        open(
            &["claude", "safe", "架構師"],
            &[("EZ2_AIB_INSTANCE", "main")]
        ),
        Cabin::FromEnvironment {
            instance: Some("main".into())
        }
    );
    assert_eq!(
        open(&["claude", "safe", "架構師", "main"], &[]),
        named("main", true)
    );
    // what the socket points at is shape-checked like any cabin
    let bad = Fake::new(Ok(Some("-x".into())), None);
    assert_eq!(
        ctx(&["claude", "safe", "架構師"], &[], &bad),
        Err(OpenError::Instance("-x".into()))
    );
}

/// A caller in a pane whose socket points at cabin `x`, checking it as 1.x
/// `aib_apply_socket_instance` does (no lookup of its own).
struct Socket;

impl Context for Socket {
    fn persona(&self, w: &str) -> PersonaMatch {
        find_persona(w, &cards())
    }
    fn resolve_cabin(&self, q: &CabinQuery<'_>) -> Result<Option<String>, OpenError> {
        match q.explicit {
            _ if q.new => Ok(None),
            Some(e) if e != "x" => Err(OpenError::Cabin(format!("conflict {e} vs x"))),
            Some(_) => Ok(None),
            None => Ok(Some("x".into())),
        }
    }
    fn killswitch(&self) -> Option<String> {
        None
    }
}

/// 043dd r3 M1: the primary named by hand or by the environment is handed to the
/// caller, so a socket pointing elsewhere is refused, not quietly overruled.
#[test]
fn a_socket_pointing_elsewhere_than_the_named_cabin_is_refused() {
    let run = |a: &[&str], env: &[(&str, &str)]| {
        let env: Vec<(String, String)> = env
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        parse_open_in(
            &args(a),
            move |k: &str| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()),
            &Socket,
        )
    };
    let d = ["claude", "safe", "架構師"];
    let conflict = |e: &str| Err(OpenError::Cabin(format!("conflict {e} vs x")));
    assert_eq!(
        run(&["claude", "safe", "架構師", "--instance", "main"], &[]),
        conflict("main")
    );
    assert_eq!(
        run(&["claude", "safe", "架構師", "main"], &[]),
        conflict("main")
    );
    assert_eq!(run(&d, &[("EZ2_AIB_INSTANCE", "main")]), conflict("main"));
    assert_eq!(run(&d, &[("EZ2_AIB_INSTANCE", "new")]), conflict("new"));
    assert_eq!(run(&["claude", "safe", "架構師", "y"], &[]), conflict("y"));
    // the same cabin, a new one, or nothing named: no conflict
    let cabin = |r: Result<Outcome, OpenError>| match r {
        Ok(Outcome::Open(s)) => s.cabin,
        other => panic!("{other:?}"),
    };
    assert_eq!(
        cabin(run(&["claude", "safe", "架構師", "x"], &[])),
        named("x", false)
    );
    assert_eq!(
        cabin(run(
            &["claude", "safe", "架構師", "new"],
            &[("EZ2_AIB_INSTANCE", "main")]
        )),
        Cabin::New
    );
    assert_eq!(
        cabin(run(&d, &[("EZ2_AIB_INSTANCE", "")])),
        named("x", false)
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
    fn persona(&self, w: &str) -> PersonaMatch {
        find_persona(w, &cards())
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
    let e = run(&["codexbot", "fast", "架構師"]).unwrap_err();
    assert_eq!(
        (e.rc(), e.key(), e.to_string()),
        (2, None, "aib: codexbot needs a thread".to_string())
    );
    assert_eq!(
        run(&["foo", "fast", "架構師"]),
        Err(OpenError::Provider("foo".into()))
    );
    assert_eq!(
        run(&["claude", "fast", "架構師"]),
        Err(OpenError::Mode("fast".into()))
    );
    // words only: no bot check
    assert_eq!(
        p(&["codexbot", "fast", "架構師"]),
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
