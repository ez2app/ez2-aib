// SPDX-License-Identifier: Apache-2.0
//! aib-title: the pieces, then the bash path and the binary side by side on the same
//! inputs (a stub sampler records what it was given and prints a fixed title).

use aib_title::{
    advance, beat_text, chained, parse_args, title_commands, transition, version_fields, Env,
    BEAT_FILE, VERSION_FILE,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn env_of(pairs: &[(&str, &str)]) -> Env {
    Env(pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect())
}

#[test]
fn beat_text_is_bash_case() {
    assert_eq!(beat_text(""), "0", "empty_is_0");
    assert_eq!(beat_text("2\n\n"), "2", "trailing_newlines_dropped");
    assert_eq!(beat_text("x1"), "0", "non_digit_is_0");
    assert_eq!(beat_text("1 "), "0", "space_is_non_digit");
    assert_eq!(beat_text("007"), "007", "digits_kept_as_text");
}

#[test]
fn advance_is_mod_4() {
    let got: Vec<u32> = ["", "0", "1", "2", "3\n", "junk"]
        .iter()
        .map(|s| advance(s))
        .collect();
    assert_eq!(got, vec![1, 1, 2, 3, 0, 1], "advance_not_n_plus_1_mod_4");
    // 10^22 + 7 = 3 (mod 4): no overflow on a long digit string
    assert_eq!(
        advance("10000000000000000000007"),
        0,
        "long_digits_overflow"
    );
}

#[test]
fn transition_writes_under_lock_and_leaves_no_temp() {
    let d = tempfile::tempdir().expect("tempdir");
    let f = d.path().join("cockpit").join(BEAT_FILE);
    for want in ["1\n", "2\n", "3\n", "0\n", "1\n"] {
        transition(&f).expect("transition");
        assert_eq!(fs::read_to_string(&f).expect("read"), want, "beat_sequence");
    }
    let names: Vec<String> = fs::read_dir(f.parent().expect("dir"))
        .expect("ls")
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .collect();
    assert!(
        names.iter().all(|n| !n.contains(".tmp.")),
        "temp_left_behind {names:?}"
    );
    assert!(
        names.iter().any(|n| n == &format!("{BEAT_FILE}.lock")),
        "lock_file_not_shared {names:?}"
    );
}

#[test]
fn version_fields_follow_python_print() {
    let d = tempfile::tempdir().expect("tempdir");
    assert_eq!(version_fields(d.path()), None, "missing_file_is_none");
    let vf = d.path().join(VERSION_FILE);
    let case = |body: &str| {
        fs::write(&vf, body).expect("write");
        version_fields(d.path()).expect("some")
    };
    let s = |a: &str, b: &str| (a.to_string(), b.to_string());
    assert_eq!(
        case(r#"{"ver":"0.1.69"}"#),
        s("0.1.69", "unknown"),
        "state_default_unknown"
    );
    assert_eq!(
        case(r#"{"ver":"","state":"ok"}"#),
        s("", "ok"),
        "empty_ver_is_empty"
    );
    assert_eq!(
        case(r#"{"ver":null,"state":""}"#),
        s("", "unknown"),
        "falsy_takes_default"
    );
    assert_eq!(
        case(r#"{"ver":true,"state":7}"#),
        s("True", "7"),
        "python_str_of_bool_int"
    );
    assert_eq!(
        case(r#"{"ver":"1.2\n"}"#),
        s("1.2", "unknown"),
        "trailing_newline_stripped"
    );
    assert_eq!(case("not json"), s("", ""), "unparsable_is_both_empty");
    assert_eq!(case(r#"["0.1"]"#), s("", ""), "non_object_is_both_empty");
}

#[test]
fn commands_skip_empty_and_chain_with_semicolon() {
    let v = |a: &str, b: &str| Some((a.to_string(), b.to_string()));
    let n = |c: &Vec<Vec<String>>| c.iter().map(|x| x[3].clone()).collect::<Vec<_>>();
    assert_eq!(
        n(&title_commands("s", "T", None)),
        vec!["@aib_title"],
        "no_file_title_only"
    );
    assert_eq!(
        n(&title_commands("s", "T", v("", ""))),
        vec!["@aib_title"],
        "empty_fields_skipped"
    );
    let all = title_commands("s", "T", v("0.1", "ok"));
    assert_eq!(
        n(&all),
        vec!["@aib_title", "@aib_ver", "@aib_ver_state"],
        "three_options"
    );
    let one = chained(&all).expect("chain");
    assert_eq!(
        one.iter().filter(|a| a.as_str() == ";").count(),
        2,
        "two_separators"
    );
    assert_eq!(one[0], "set-option", "chain_starts_with_set_option");
    assert!(
        chained(&title_commands("s", "ends;", None)).is_none(),
        "semicolon_value_not_chained"
    );
}

#[test]
fn args_like_bash() {
    let e = env_of(&[]);
    let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    assert!(
        parse_args(&a(&["--once", "--print"]), &e)
            .expect("ok")
            .print_only,
        "print_flag"
    );
    assert!(
        parse_args(&a(&["--dry-run"]), &e).expect("ok").dry,
        "dry_flag"
    );
    assert!(
        parse_args(&a(&[]), &env_of(&[("AIB_DRY_RUN", "1")]))
            .expect("ok")
            .dry,
        "dry_env"
    );
    assert_eq!(
        parse_args(&a(&["-x"]), &e).err().as_deref(),
        Some("unknown option: -x"),
        "unknown_option_text"
    );
    assert!(parse_args(&a(&["--loop"]), &e).is_err(), "loop_not_here");
}

// ── bash path vs binary ───────────────────────────────────────────────────────────────

fn cockpit() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit")
}

struct Side {
    out: Vec<String>,
    dump: String,
    beat: String,
}

/// Run one path twice in its own state dir. `rust` false = `env -u TMUX bash title-beat.sh`,
/// what pane-right-tabs.sh ran; true = source cockpit-env.sh, then the binary (what the pane
/// does now: it has sourced cockpit-env.sh itself).
fn run_side(rust: bool, extra: &[&str]) -> Side {
    let td = tempfile::tempdir().expect("tempdir");
    let t = td.path();
    let bin = t.join("bin");
    let shim = t.join("shim");
    fs::create_dir_all(&bin).expect("bin");
    fs::create_dir_all(&shim).expect("shim");
    let stub = bin.join("py-stub");
    fs::write(
        &stub,
        // `-c` = the bash path reading ver / state from title-version.json: real python3
        "#!/bin/sh\n\
         [ \"$1\" = -c ] && exec python3 \"$@\"\n\
         printf 'argv=%s/%s\\n' \"$(basename \"$(dirname \"$1\")\")\" \"$(basename \"$1\")\" >> \"$AIB_TITLE_TEST_DUMP\"\n\
         env | grep -E '^(EZ2_TB_[A-Z_]*|TMUX)=' | sort >> \"$AIB_TITLE_TEST_DUMP\"\n\
         printf 'T b=%s w=%s\\n\\n' \"$EZ2_TB_BEAT\" \"$EZ2_TB_WINDOW\"\n",
    )
    .expect("stub");
    // macOS runners have no flock(1): a no-op one at the END of PATH, so a real one wins
    fs::write(shim.join("flock"), "#!/bin/sh\nexit 0\n").expect("flock shim");
    for p in [&stub, &shim.join("flock")] {
        Command::new("chmod")
            .arg("+x")
            .arg(p)
            .status()
            .expect("chmod");
    }
    let state = t.join("state");
    fs::create_dir_all(&state).expect("state");
    // cockpit-env.sh refuses a cabin with no instance-token (32 hex) under the state root
    let inst = t.join("root/instances/20261112-000000-abcd");
    fs::create_dir_all(&inst).expect("instance");
    fs::write(
        inst.join("instance-token"),
        "0123456789abcdef0123456789abcdef\n",
    )
    .expect("token");
    fs::write(
        state.join(VERSION_FILE),
        r#"{"ver":"0.1.69","state":"current"}"#,
    )
    .expect("vf");
    let dump = t.join("dump.txt");
    let path = format!(
        "{}:{}",
        std::env::var("PATH").unwrap_or_default(),
        shim.display()
    );
    let mut out = Vec::new();
    for _ in 0..2 {
        let mut c = Command::new("bash");
        if rust {
            c.args([
                "-c",
                ". \"$1/cockpit-env.sh\" && exec \"$2\" --once \"${@:3}\"",
                "_",
            ])
            .arg(cockpit())
            .arg(env!("CARGO_BIN_EXE_aib-title"));
        } else {
            // pane-right-tabs.sh: env -u TMUX bash title-beat.sh --once
            c = Command::new("env");
            c.args(["-u", "TMUX", "bash"])
                .arg(cockpit().join("title/title-beat.sh"))
                .arg("--once");
        }
        c.args(extra)
            .env_clear()
            .env("PATH", &path)
            .env("HOME", t)
            .env("EZ2_AIB_STATE_ROOT", t.join("root"))
            .env("EZ2_AIB_INSTANCE", "20261112-000000-abcd")
            .env("EZ2_COCKPIT_STATE_DIR", &state)
            .env("EZ2_PY3", &stub)
            .env("EZ2_TB_WINDOW", "@7")
            .env("CLAUDE_CODE_SESSION_ID", "uuid-1")
            .env("TMUX", "/nonexistent/tmux-x,1,2")
            .env("AIB_TITLE_TEST_DUMP", &dump);
        let o = c.output().expect("run");
        assert!(
            o.status.success(),
            "rc {:?} stderr {}",
            o.status.code(),
            String::from_utf8_lossy(&o.stderr)
        );
        out.push(String::from_utf8_lossy(&o.stdout).into_owned());
    }
    Side {
        out,
        dump: fs::read_to_string(&dump).unwrap_or_default(),
        beat: fs::read_to_string(state.join(BEAT_FILE)).unwrap_or_default(),
    }
}

fn skip_without_bash() -> bool {
    if cfg!(windows) {
        eprintln!("skip parity_needs_sh_stub (Windows)");
        return true;
    }
    false
}

#[test]
fn parity_dry_run_same_stdout_beat_and_sampler_input() {
    if skip_without_bash() {
        return;
    }
    let b = run_side(false, &["--dry-run"]);
    let r = run_side(true, &["--dry-run"]);
    assert_eq!(r.out, b.out, "stdout_differs");
    assert_eq!(r.beat, b.beat, "beat_file_differs");
    assert_eq!(r.dump, b.dump, "sampler_input_differs");
    // the inputs themselves, so "both wrong the same way" is caught too
    assert!(
        b.out[0].contains("@aib_ver_state current"),
        "ver_state_line_missing {:?}",
        b.out[0]
    );
    assert!(
        b.out[1].ends_with("T b=2 w=@7\n"),
        "second_beat_title {:?}",
        b.out[1]
    );
    assert_eq!(b.beat, "2\n", "two_beats");
    assert!(
        b.dump.contains("argv=title/title_sample.py"),
        "sampler_file {:?}",
        b.dump
    );
    assert!(
        b.dump.contains("EZ2_TB_UUID=uuid-1"),
        "uuid_fallback {:?}",
        b.dump
    );
    assert!(!b.dump.contains("TMUX="), "tmux_env_leaked {:?}", b.dump);
}

#[test]
fn parity_print_same_and_no_tmux_lines() {
    if skip_without_bash() {
        return;
    }
    let b = run_side(false, &["--print", "--dry-run"]);
    let r = run_side(true, &["--print", "--dry-run"]);
    assert_eq!(r.out, b.out, "print_stdout_differs");
    assert_eq!(r.dump, b.dump, "print_sampler_input_differs");
    assert_eq!(b.out[0], "T b=1 w=@7\n", "print_is_title_only");
    assert!(
        b.dump.contains("EZ2_TB_PRINT_ONLY=1"),
        "print_only_passed {:?}",
        b.dump
    );
}
