//! ADR 0007 (PTY / ConPTY) invariants, one test per invariant number
//! (DECISION-14 item 3). They are marked `#[ignore]` until the PTY module
//! exists; each body panics, so running them with `--ignored` before the
//! implementation lands is red, never a false green. The implementation PR
//! replaces each body with the real test and removes the `ignore`, and each
//! must then go red under the knife named in its `ignore` reason.

/// Invariant 1 (Windows): the CLI is born in its Job (`PSEUDOCONSOLE` and
/// `JOB_LIST` in one attribute list); the pseudo-console host is in a
/// separate Job with `KILL_ON_JOB_CLOSE`.
#[cfg(windows)]
#[test]
#[ignore = "ADR 0007 invariant 1 not implemented; knives: drop JOB_LIST and assign after spawn / drop the host's Job"]
fn invariant_1_cli_is_born_in_its_job() {
    panic!("ADR 0007 invariant 1: not implemented");
}

/// Invariant 2: no fd / handle of one session leaks into another
/// (macOS: master `FD_CLOEXEC` + `POSIX_SPAWN_CLOEXEC_DEFAULT` or one lock;
/// Windows: no inheritance or a `HANDLE_LIST`, std handles invalid).
#[test]
#[ignore = "ADR 0007 invariant 2 not implemented; knife: two concurrent sessions, A's master / HPCON reaches B"]
fn invariant_2_no_handle_leaks_between_sessions() {
    panic!("ADR 0007 invariant 2: not implemented");
}

/// Invariant 3 (macOS): the shim keeps only the slave and the lifeline read
/// end (an allow-list), holds no copy of the master, and ignores SIGHUP.
#[cfg(target_os = "macos")]
#[test]
#[ignore = "ADR 0007 invariant 3 not implemented; knives: shim keeps a master dup / shim without SIG_IGN"]
fn invariant_3_shim_fds_are_an_allow_list() {
    panic!("ADR 0007 invariant 3: not implemented");
}

/// Invariant 4 (macOS): SIGHUP is a second signal, never proof of cleanup;
/// a result collected by the shim is never `clean`.
#[cfg(target_os = "macos")]
#[test]
#[ignore = "ADR 0007 invariant 4 not implemented; knives: report clean once SIGHUP was sent / SIGKILL the shim with a setsid grandchild"]
fn invariant_4_sighup_is_not_proof_of_cleanup() {
    panic!("ADR 0007 invariant 4: not implemented");
}

/// Invariant 5: a graceful stop does not rely on `\x03` alone; after the
/// grace period the ADR 0004 cancel order always runs.
#[test]
#[ignore = "ADR 0007 invariant 5 not implemented; knife: fixture in raw mode ignores 0x03, a stop that only writes it must fail"]
fn invariant_5_stop_does_not_rely_on_ctrl_c_alone() {
    panic!("ADR 0007 invariant 5: not implemented");
}
