//! The shell glue of the seat view (Andy 11-13 改案; Rust, not Python: Andy 11-13):
//! `aib/src/cockpit/seatview-glue.sh` finds the Rust `aib-seatview` and asks it whether a
//! cockpit window is a seat view. A seat view gets no pending-review box and no right
//! pane healed back (cockpit-full.sh, cockpit-resize.sh, aib). A missing program, a
//! program that dies, or any other answer is "not a seat view": the full layout, as
//! before. Which panes a window plans is `cockpit_pane_plan`, tested in
//! `cockpit_plan.rs`; the program itself is tested in `src/seatview.rs`. tmux is never
//! touched: a fake `aib-seatview` answers.
#![cfg(unix)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

fn cockpit_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit")
}

/// A scratch folder of its own (no tempfile dependency in this crate).
fn scratch(tag: &str) -> PathBuf {
    let n = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let d = std::env::temp_dir().join(format!("aib-sv-glue-{}-{tag}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// rc of `aib_seatview_is_seat sess:cockpit` when the fake `aib-seatview status` answers
/// `layout` (`seat`, `full`, or `crash` = it kills itself); `with_bin = false` = not
/// installed. The fake refuses any other window or subcommand (rc 9).
fn is_seat(layout: &str, with_bin: bool) -> i32 {
    let tmp = scratch(layout);
    let fake = tmp.join("fake-seatview");
    std::fs::write(
        &fake,
        format!(
            "#!/bin/bash\n\
             [ \"$1 $2 $3\" = \"status --window sess:cockpit\" ] || exit 9\n\
             [ {layout} = crash ] && kill -9 $$\n\
             echo {layout}\n"
        ),
    )
    .unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    let glue = std::fs::read_to_string(cockpit_dir().join("seatview-glue.sh")).unwrap();
    let bin = if with_bin {
        fake.clone()
    } else {
        tmp.join("missing")
    };
    let out = Command::new("bash")
        .arg("-c")
        .arg(format!("{glue}\naib_seatview_is_seat sess:cockpit\n"))
        .env("HOME", &tmp)
        .env("AIB_SEATVIEW_BIN", &bin)
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(&tmp);
    assert!(out.stdout.is_empty(), "the glue prints nothing: {out:?}");
    out.status.code().unwrap()
}

#[test]
fn only_a_seat_answer_is_a_seat_view() {
    assert_eq!(is_seat("seat", true), 0);
    assert_eq!(is_seat("full", true), 1);
}

#[test]
fn no_program_or_a_dying_one_keeps_the_full_layout() {
    assert_eq!(is_seat("seat", false), 1, "not installed = full layout");
    // installed but dies (killed, wrong arch) ⇒ full layout, the cabin still opens (review M2)
    assert_eq!(is_seat("crash", true), 1);
}

/// The scripts that act on a seat view load the glue (one finder for the whole cockpit:
/// cockpit-full.sh had its own `_aib_seatview_bin` until S61, 1ced7 11-14).
#[test]
fn the_cockpit_scripts_load_the_one_glue() {
    let c = cockpit_dir();
    for f in ["cockpit-full.sh", "cockpit-resize.sh"] {
        let s = std::fs::read_to_string(c.join(f)).unwrap();
        assert!(s.contains("/seatview-glue.sh\""), "{f} loads the glue");
        assert!(!s.contains("_aib_seatview_bin"), "{f} has no second finder");
    }
    let full = std::fs::read_to_string(c.join("cockpit-full.sh")).unwrap();
    assert!(
        full.contains("if ! aib_seatview_is_seat \"$S:cockpit\"; then"),
        "no pending-review box on a seat view"
    );
}
