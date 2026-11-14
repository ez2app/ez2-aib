// SPDX-License-Identifier: Apache-2.0
//! aib-title says which build it is (PLAN-aibtitle-install-043dd-1113 r2, package 1):
//! `--version`, `build-info`, and the same two lines read straight out of the file the way the
//! installer will (aib-release host_build), so the printed and the embedded record cannot differ.

use aib_release::host_build::{info_in_binary, parse_info};
use aib_title::{info_command, version_line, BUILD_INFO, TOOL_INFO};
use std::process::Command;

fn args(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

fn hex40(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[test]
fn build_info_is_the_shape_host_build_reads() {
    let info = parse_info(BUILD_INFO).expect("host_build::parse_info refuses BUILD_INFO");
    assert_eq!(
        info.version,
        env!("CARGO_PKG_VERSION"),
        "version_from_cargo"
    );
    if let Some(c) = &info.commit {
        assert!(hex40(c), "commit_40_lower_hex: {c}");
    }
    assert_eq!(BUILD_INFO.matches(';').count(), 1, "one_end_mark");
    assert!(BUILD_INFO.starts_with("aib-build-info "), "mark_first");
}

#[test]
fn tool_info_names_this_tool_and_a_target() {
    let rest = TOOL_INFO
        .strip_prefix("aib-tool-info ")
        .and_then(|r| r.strip_suffix(';'))
        .expect("tool_info_mark_and_end");
    let parts: Vec<&str> = rest.split(' ').collect();
    assert_eq!(parts.len(), 2, "two_fields: {TOOL_INFO}");
    assert_eq!(parts[0], "name=aib-title", "name_is_aib_title");
    let target = parts[1].strip_prefix("target=").expect("target_key");
    assert!(
        !target.is_empty()
            && target
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || b == b'.'),
        "target_is_a_triple: {target}"
    );
    assert_eq!(TOOL_INFO.matches(';').count(), 1, "one_end_mark");
}

#[test]
fn info_commands_only_as_the_sole_argument() {
    assert_eq!(
        info_command(&args(&["--version"])),
        Some(version_line()),
        "version"
    );
    assert_eq!(
        info_command(&args(&["build-info"])),
        Some(format!("{BUILD_INFO}\n{TOOL_INFO}")),
        "build_info_two_lines"
    );
    for v in [
        vec![],
        vec!["--once"],
        vec!["--version", "--once"],
        vec!["--once", "build-info"],
        vec!["--build-info"],
    ] {
        assert_eq!(info_command(&args(&v)), None, "not_info: {v:?}");
    }
    let line = version_line();
    let parts: Vec<&str> = line.split(' ').collect();
    assert_eq!(parts.len(), 3, "version_three_words: {line}");
    assert_eq!(parts[0], "aib-title", "version_name");
    assert_eq!(parts[1], env!("CARGO_PKG_VERSION"), "version_number");
    assert_eq!(
        parts[2],
        format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        "version_os_arch"
    );
}

#[test]
fn binary_prints_and_carries_the_same_record() {
    let bin = env!("CARGO_BIN_EXE_aib-title");
    // no cockpit variables: the info commands must not need them
    let out = Command::new(bin)
        .arg("build-info")
        .env_clear()
        .output()
        .expect("run aib-title build-info");
    assert_eq!(out.status.code(), Some(0), "build_info_rc_0");
    let text = String::from_utf8(out.stdout).expect("utf8");
    assert_eq!(
        text,
        format!("{BUILD_INFO}\n{TOOL_INFO}\n"),
        "printed_lines"
    );

    let out = Command::new(bin)
        .arg("--version")
        .env_clear()
        .output()
        .expect("run aib-title --version");
    assert_eq!(out.status.code(), Some(0), "version_rc_0");
    assert_eq!(
        String::from_utf8(out.stdout).expect("utf8"),
        format!("{}\n", version_line()),
        "printed_version"
    );

    let bytes = std::fs::read(bin).expect("read the built file");
    let embedded = info_in_binary(&bytes).expect("host_build reads the file");
    assert_eq!(
        Some(embedded),
        parse_info(BUILD_INFO),
        "embedded_equals_printed"
    );
    let tool = TOOL_INFO.as_bytes();
    assert!(
        bytes.windows(tool.len()).any(|w| w == tool),
        "tool_info_in_the_file"
    );
}
