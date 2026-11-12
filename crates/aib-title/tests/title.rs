// SPDX-License-Identifier: Apache-2.0
//! aib-title: the pieces, then the bash path and the binary side by side on the same
//! inputs (a stub sampler records what it was given and prints a fixed title).

use aib_title::{
    advance, beat_text, chained, parse_args, proto_check, sampler_proto, title_commands,
    transition, version_fields, Env, BEAT_FILE, PROTO_FALLBACK_RC, PROTO_MISMATCH_RC, SAMPLER,
    SAMPLER_PROTO, VERSION_FILE,
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
    rcs: Vec<i32>,
    dump: String,
    beat: String,
    /// every tmux call the shim saw, one line each, arguments ended by \x1f
    tmux: String,
}

/// Run one path twice in its own state dir. `rust` false = `env -u TMUX bash title-beat.sh`,
/// what pane-right-tabs.sh ran; true = source cockpit-env.sh, then the binary (what the pane
/// does now: it has sourced cockpit-env.sh itself). A tmux shim is FIRST on PATH, so a real
/// tmux server is never touched; it answers `display-message` with @9.
fn run_side(rust: bool, extra: &[&str], envs: &[(&str, &str)]) -> Side {
    let td = tempfile::tempdir().expect("tempdir");
    let t = td.path();
    let bin = t.join("bin");
    let shim = t.join("shim");
    let tshim = t.join("tshim");
    for d in [&bin, &shim, &tshim] {
        fs::create_dir_all(d).expect("dir");
    }
    let stub = bin.join("py-stub");
    fs::write(
        &stub,
        // `-c` = the bash path reading ver / state from title-version.json: real python3
        "#!/bin/sh\n\
         [ \"$1\" = -c ] && exec python3 \"$@\"\n\
         printf 'argv=%s/%s\\n' \"$(basename \"$(dirname \"$1\")\")\" \"$(basename \"$1\")\" >> \"$AIB_TITLE_TEST_DUMP\"\n\
         env | grep -E '^(EZ2_TB_[A-Z_]*|TMUX)=' | sort >> \"$AIB_TITLE_TEST_DUMP\"\n\
         [ -n \"${AIB_TITLE_TEST_RC:-}\" ] && exit \"$AIB_TITLE_TEST_RC\"\n\
         printf 'T b=%s w=%s\\n\\n' \"$EZ2_TB_BEAT\" \"$EZ2_TB_WINDOW\"\n",
    )
    .expect("stub");
    // macOS runners have no flock(1): a no-op one at the END of PATH, so a real one wins
    fs::write(shim.join("flock"), "#!/bin/sh\nexit 0\n").expect("flock shim");
    fs::write(
        tshim.join("tmux"),
        "#!/bin/sh\n\
         for a in \"$@\"; do printf '%s\\037' \"$a\"; done >> \"$AIB_TITLE_TEST_TMUX\"\n\
         printf '\\n' >> \"$AIB_TITLE_TEST_TMUX\"\n\
         case \" $* \" in *\" display-message \"*) echo @9 ;; esac\n\
         exit 0\n",
    )
    .expect("tmux shim");
    for p in [&stub, &shim.join("flock"), &tshim.join("tmux")] {
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
    let tlog = t.join("tmux.txt");
    let path = format!(
        "{}:{}:{}",
        tshim.display(),
        std::env::var("PATH").unwrap_or_default(),
        shim.display()
    );
    let mut out = Vec::new();
    let mut rcs = Vec::new();
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
            .env("CLAUDE_CODE_SESSION_ID", "uuid-1")
            .env("TMUX", "/nonexistent/tmux-x,1,2")
            .env("AIB_TITLE_TEST_DUMP", &dump)
            .env("AIB_TITLE_TEST_TMUX", &tlog);
        for (k, v) in envs {
            c.env(k, v);
        }
        let o = c.output().expect("run");
        rcs.push(o.status.code().unwrap_or(-1));
        out.push(String::from_utf8_lossy(&o.stdout).into_owned());
    }
    Side {
        out,
        rcs,
        dump: fs::read_to_string(&dump).unwrap_or_default(),
        beat: fs::read_to_string(state.join(BEAT_FILE)).unwrap_or_default(),
        tmux: fs::read_to_string(&tlog).unwrap_or_default(),
    }
}

/// tmux calls as commands: a chained call (`a ; b ; c`) counts as its three commands.
fn tmux_commands(log: &str) -> Vec<Vec<String>> {
    let mut cmds = Vec::new();
    for line in log.lines() {
        let mut cur: Vec<String> = Vec::new();
        for a in line.split('\x1f').filter(|a| !a.is_empty()) {
            if a == ";" {
                cmds.push(std::mem::take(&mut cur));
            } else {
                cur.push(a.to_string());
            }
        }
        cmds.push(cur);
    }
    cmds
}

fn skip_without_bash() -> bool {
    if cfg!(windows) {
        eprintln!("skip parity_needs_sh_stub (Windows)");
        return true;
    }
    false
}

const WIN7: &[(&str, &str)] = &[("EZ2_TB_WINDOW", "@7")];

#[test]
fn parity_dry_run_same_stdout_beat_and_sampler_input() {
    if skip_without_bash() {
        return;
    }
    let b = run_side(false, &["--dry-run"], WIN7);
    let r = run_side(true, &["--dry-run"], WIN7);
    assert_eq!(b.rcs, vec![0, 0], "bash_rc");
    assert_eq!(r.rcs, b.rcs, "rc_differs");
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
    assert_eq!(b.tmux, "", "dry_touched_tmux {:?}", b.tmux);
    assert_eq!(r.tmux, "", "dry_touched_tmux_rust {:?}", r.tmux);
}

#[test]
fn parity_print_same_and_no_tmux_lines() {
    if skip_without_bash() {
        return;
    }
    let b = run_side(false, &["--print", "--dry-run"], WIN7);
    let r = run_side(true, &["--print", "--dry-run"], WIN7);
    assert_eq!(r.out, b.out, "print_stdout_differs");
    assert_eq!(r.dump, b.dump, "print_sampler_input_differs");
    assert_eq!(b.out[0], "T b=1 w=@7\n", "print_is_title_only");
    assert!(
        b.dump.contains("EZ2_TB_PRINT_ONLY=1"),
        "print_only_passed {:?}",
        b.dump
    );
}

/// a63ab L2: the real write path, the hub window probe and the session-id order, with no
/// window given. bash makes 1 display + 3 set-option calls per run; the binary 1 + 1 chained.
#[test]
fn parity_real_write_same_tmux_commands_and_probe() {
    if skip_without_bash() {
        return;
    }
    let ids = &[("EZ2_SESSION_UUID", "uuid-0")];
    let b = run_side(false, &[], ids);
    let r = run_side(true, &[], ids);
    assert_eq!(b.rcs, vec![0, 0], "bash_rc");
    assert_eq!(r.rcs, b.rcs, "rc_differs");
    assert_eq!(r.out, b.out, "stdout_differs");
    assert_eq!(r.dump, b.dump, "sampler_input_differs");
    let (bc, rc) = (tmux_commands(&b.tmux), tmux_commands(&r.tmux));
    assert_eq!(rc, bc, "tmux_commands_differ");
    assert_eq!(b.tmux.lines().count(), 8, "bash_tmux_calls {:?}", b.tmux);
    assert_eq!(r.tmux.lines().count(), 4, "rust_not_chained {:?}", r.tmux);
    assert_eq!(bc.len(), 8, "commands {bc:?}");
    assert_eq!(
        &bc[0][..2],
        ["-L".to_string(), "aihub-20261112-000000-abcd".to_string()].as_slice(),
        "probe_socket {:?}",
        bc[0]
    );
    assert_eq!(
        &bc[0][2..],
        ["display-message", "-p", "#{window_id}"]
            .map(String::from)
            .as_slice(),
        "probe_args"
    );
    assert_eq!(
        bc[1],
        [
            "set-option",
            "-t",
            "aib-20261112-000000-abcd",
            "@aib_title",
            "T b=1 w=@9"
        ]
        .map(String::from)
        .to_vec(),
        "title_set"
    );
    assert_eq!(
        bc[3][3..],
        ["@aib_ver_state", "current"].map(String::from),
        "state_set"
    );
    assert!(
        b.dump.contains("EZ2_TB_WINDOW=@9"),
        "probe_window {:?}",
        b.dump
    );
    assert!(
        b.dump.contains("EZ2_TB_UUID=uuid-0"),
        "uuid_order {:?}",
        b.dump
    );
}

/// a63ab L2: --print without --dry-run never touches tmux, not even the window probe.
#[test]
fn parity_print_real_never_touches_tmux() {
    if skip_without_bash() {
        return;
    }
    let b = run_side(false, &["--print"], &[]);
    let r = run_side(true, &["--print"], &[]);
    assert_eq!(r.out, b.out, "print_stdout_differs");
    assert_eq!(b.tmux, "", "bash_print_touched_tmux {:?}", b.tmux);
    assert_eq!(r.tmux, "", "print_touched_tmux {:?}", r.tmux);
}

/// a63ab L2: the sampler's exit code comes back, and nothing is written.
#[test]
fn parity_sampler_failure_same_rc_no_write() {
    if skip_without_bash() {
        return;
    }
    let fail = &[("AIB_TITLE_TEST_RC", "3")];
    let b = run_side(false, &[], fail);
    let r = run_side(true, &[], fail);
    assert_eq!(b.rcs, vec![3, 3], "bash_rc");
    assert_eq!(r.rcs, b.rcs, "rc_differs");
    assert_eq!(r.out, b.out, "stdout_differs");
    let (bc, rc) = (tmux_commands(&b.tmux), tmux_commands(&r.tmux));
    assert_eq!(rc, bc, "tmux_commands_differ");
    assert!(
        bc.iter().all(|c| !c.contains(&"set-option".to_string())),
        "wrote_after_failure {bc:?}"
    );
}

// ── handshake with title_sample.py (PLAN-aibtitle-install r2 package 2) ───────────────

/// The pin: the number in this binary and the `TB_PROTO = N` line of the sampler in the same
/// tree are equal. Changing one side alone turns this red.
#[test]
fn proto_pinned_to_the_sampler_in_this_tree() {
    let text = fs::read_to_string(cockpit().join(SAMPLER)).expect("read title_sample.py");
    assert_eq!(sampler_proto(&text), Some(SAMPLER_PROTO), "proto_pin");
    assert!(proto_check(&cockpit()).is_ok(), "proto_check_this_tree");
}

#[test]
fn sampler_proto_reads_one_digits_line_only() {
    assert_eq!(sampler_proto("x\nTB_PROTO = 3\ny\n"), Some(3), "one_line");
    assert_eq!(sampler_proto("TB_PROTO = 3  \n"), Some(3), "trailing_space");
    assert_eq!(sampler_proto("import os\n"), None, "none_before_handshake");
    assert_eq!(
        sampler_proto("TB_PROTO = 1\nTB_PROTO = 1\n"),
        None,
        "twice_refused"
    );
    assert_eq!(sampler_proto("TB_PROTO = 1x\n"), None, "not_digits");
    assert_eq!(sampler_proto("TB_PROTO = \n"), None, "empty");
    assert_eq!(
        sampler_proto("  TB_PROTO = 1\n"),
        None,
        "indented_is_not_it"
    );
}

/// A cockpit dir whose sampler says `body`; the binary run against it with a python stub that
/// records whether it was called. Returns (rc, stub called, beat file written).
fn run_against_sampler(body: &str) -> (i32, bool, bool) {
    let td = tempfile::tempdir().expect("tempdir");
    let t = td.path();
    let ck = t.join("cockpit");
    fs::create_dir_all(ck.join("title")).expect("cockpit");
    fs::write(ck.join(SAMPLER), body).expect("sampler");
    let state = t.join("state");
    fs::create_dir_all(&state).expect("state");
    let called = t.join("called");
    let stub = t.join("py-stub");
    fs::write(
        &stub,
        format!("#!/bin/sh\ntouch '{}'\necho T\n", called.display()),
    )
    .expect("stub");
    Command::new("chmod")
        .arg("+x")
        .arg(&stub)
        .status()
        .expect("chmod");
    let o = Command::new(env!("CARGO_BIN_EXE_aib-title"))
        .args(["--once", "--print"])
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("EZ2_COCKPIT_DIR", &ck)
        .env("EZ2_COCKPIT_STATE_DIR", &state)
        .env("EZ2_PY3", &stub)
        .output()
        .expect("run");
    (
        o.status.code().unwrap_or(-1),
        called.exists(),
        state.join(BEAT_FILE).exists(),
    )
}

#[test]
fn other_proto_or_none_is_126_before_anything_runs_or_is_written() {
    if cfg!(windows) {
        return; // the stub is a shell script
    }
    let other = format!("TB_PROTO = {}\n", SAMPLER_PROTO + 1);
    for (body, what) in [
        (other.as_str(), "newer_sampler"),
        ("import os\n", "older_sampler"),
    ] {
        let (rc, called, beat) = run_against_sampler(body);
        assert_eq!(rc, PROTO_FALLBACK_RC, "rc_126 {what}");
        assert!(!called, "sampler_not_run {what}");
        assert!(!beat, "beat_not_moved {what}");
    }
    let same = format!("TB_PROTO = {SAMPLER_PROTO}\n");
    let (rc, called, beat) = run_against_sampler(&same);
    assert_eq!((rc, called, beat), (0, true, true), "same_proto_runs");
}

/// The sampler's own refusal (rc 75, an older binary than the sampler) comes back as 126;
/// any other sampler failure keeps its code (parity_sampler_failure_same_rc_no_write).
#[test]
fn sampler_refusal_75_becomes_126() {
    if skip_without_bash() {
        return;
    }
    let r = run_side(true, &[], &[("AIB_TITLE_TEST_RC", "75")]);
    assert_eq!(r.rcs, vec![PROTO_FALLBACK_RC; 2], "rc_75_to_126");
    assert!(
        tmux_commands(&r.tmux)
            .iter()
            .all(|c| !c.contains(&"set-option".to_string())),
        "no_write_after_refusal"
    );
    // the stub ran (it dumps its EZ2_TB_* inputs before exiting 75)
    assert!(!r.dump.is_empty(), "stub_ran");
}

/// The real title_sample.py refuses a different number before it imports anything (no
/// cockpit around it here, so getting past the check would crash with another code), and
/// says nothing on stdout.
#[test]
fn real_sampler_refuses_another_number_first() {
    let py = if cfg!(windows) { "python" } else { "python3" };
    let td = tempfile::tempdir().expect("tempdir");
    let run = |proto: Option<&str>| {
        let mut c = Command::new(py);
        c.arg(cockpit().join(SAMPLER))
            .env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_default())
            .env("EZ2_COCKPIT_DIR", td.path());
        if let Some(p) = proto {
            c.env("AIB_TITLE_PROTO", p);
        }
        c.output()
    };
    let Ok(o) = run(Some(&(SAMPLER_PROTO + 1).to_string())) else {
        return; // no python on this runner
    };
    assert_eq!(o.status.code(), Some(PROTO_MISMATCH_RC), "rc_75");
    assert!(o.stdout.is_empty(), "nothing_on_stdout");
    // the same number gets past the check (and then fails on the empty cockpit, not with 75)
    let o = run(Some(&SAMPLER_PROTO.to_string())).expect("run");
    assert_ne!(
        o.status.code(),
        Some(PROTO_MISMATCH_RC),
        "same_number_passes_check"
    );
    // the bash path sends no number: no check
    let o = run(None).expect("run");
    assert_ne!(
        o.status.code(),
        Some(PROTO_MISMATCH_RC),
        "no_number_no_check"
    );
}
