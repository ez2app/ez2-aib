// SPDX-License-Identifier: Apache-2.0
//! S68d (5d33a CHECK-s68d-round1): a newer gh refuses a job log with terminal escape
//! sequences; the read is tried once more with --allow-escape-sequences and the log comes back
//! as plain text. An older gh (no such flag) is never given it.
#![allow(clippy::unwrap_used)]

use aib_cireuse::gh::{get_with, strip_escapes, ALLOW_ESCAPES};
use std::cell::RefCell;

const REFUSED: &str = "gh: exit 1: the response contains terminal escape sequences; pass --allow-escape-sequences to output it anyway";

#[test]
fn a_refused_log_is_read_again_with_the_flag_and_comes_back_plain() {
    let calls = RefCell::new(Vec::<Vec<String>>::new());
    let body = get_with("repos/o/r/actions/jobs/1/logs", |args| {
        calls.borrow_mut().push(args.to_vec());
        if args.iter().any(|a| a == ALLOW_ESCAPES) {
            Ok(b"2026-11-17T00:00:00Z \x1b[36;1mImage: macos-15-arm64\x1b[0m\n".to_vec())
        } else {
            Err(REFUSED.to_string())
        }
    })
    .unwrap();
    let calls = calls.into_inner();
    assert_eq!(calls.len(), 2, "{calls:?}");
    assert!(!calls[0].iter().any(|a| a == ALLOW_ESCAPES), "{calls:?}");
    assert_eq!(calls[1].last().map(String::as_str), Some(ALLOW_ESCAPES));
    assert_eq!(body, "2026-11-17T00:00:00Z Image: macos-15-arm64\n");
}

#[test]
fn another_error_is_not_retried_and_keeps_its_reason() {
    let calls = RefCell::new(0);
    let e = get_with("repos/o/r/actions/jobs/2/logs", |_| {
        *calls.borrow_mut() += 1;
        Err("gh: exit 1: HTTP 404: Not Found".to_string())
    })
    .unwrap_err();
    assert_eq!(*calls.borrow(), 1);
    assert!(e.contains("HTTP 404") && e.contains("jobs/2/logs"), "{e}");
}

#[test]
fn a_second_refusal_is_an_error_not_an_empty_log() {
    let e = get_with(
        "repos/o/r/actions/jobs/3/logs",
        |_| Err(REFUSED.to_string()),
    )
    .unwrap_err();
    assert!(e.contains("jobs/3/logs"), "{e}");
}

#[test]
fn colour_title_and_two_byte_escapes_are_dropped_and_the_text_kept() {
    assert_eq!(strip_escapes("a\x1b[1;31mred\x1b[0m b"), "ared b");
    assert_eq!(strip_escapes("\x1b]0;title\x07x"), "x");
    assert_eq!(strip_escapes("\x1b]8;;u\x1b\\link\x1b]8;;\x1b\\"), "link");
    assert_eq!(strip_escapes("\x1b(Bplain\x1b=x"), "plainx");
    assert_eq!(strip_escapes("no escapes 中文"), "no escapes 中文");
}

#[test]
fn an_unended_sequence_does_not_eat_the_text_after_it() {
    // 5d33a REVIEW-cireuse-ansi L1: CSI cut short before a non-ASCII letter or a line end
    assert_eq!(strip_escapes("a\x1b[中文 b"), "a中文 b");
    assert_eq!(
        strip_escapes("a\x1b[1;\nImage: macos-15"),
        "a\nImage: macos-15"
    );
    // an OSC with no BEL / ESC \\ stops at its own line
    assert_eq!(
        strip_escapes("\x1b]0;title\nImage: macos-15\n"),
        "\nImage: macos-15\n"
    );
}
