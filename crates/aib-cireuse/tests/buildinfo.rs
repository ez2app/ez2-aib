// SPDX-License-Identifier: Apache-2.0
//! aib-cireuse says which build it is (S68c: it is on the binsync must-install list), and
//! binsync's own readers accept it: `build-info` prints BUILD_INFO / TOOL_INFO, and the same
//! records are read straight out of the built file the way `aib-release binsync` reads them.

use aib_cireuse::{BUILD_INFO, TOOL_INFO};
use aib_release::binsync::parse_tool_info;
use aib_release::host_build::{info_in_binary, parse_info};
use std::process::Command;

#[test]
fn records_are_the_shapes_binsync_reads() {
    let info = parse_info(BUILD_INFO).expect("host_build::parse_info refuses BUILD_INFO");
    assert_eq!(info.version, env!("CARGO_PKG_VERSION"));
    let tool = parse_tool_info(TOOL_INFO).expect("binsync::parse_tool_info refuses TOOL_INFO");
    assert_eq!(tool.name, "aib-cireuse");
    assert!(!tool.target.is_empty());
    assert!(
        aib_release::binsync::ALLOWED.contains(&"aib-cireuse"),
        "on the must-install list"
    );
}

#[test]
fn binary_prints_and_carries_the_same_record() {
    let bin = env!("CARGO_BIN_EXE_aib-cireuse");
    let out = Command::new(bin)
        .arg("build-info")
        .env_clear()
        .output()
        .expect("run aib-cireuse build-info");
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(
        String::from_utf8(out.stdout).expect("utf8"),
        format!("{BUILD_INFO}\n{TOOL_INFO}\n")
    );
    // build-info only as the sole argument; anything else is still a usage error
    let out = Command::new(bin)
        .args(["build-info", "--x"])
        .env_clear()
        .output()
        .expect("run aib-cireuse");
    assert_eq!(out.status.code(), Some(2));

    let bytes = std::fs::read(bin).expect("read the built file");
    assert_eq!(info_in_binary(&bytes).ok(), parse_info(BUILD_INFO));
    let tool = TOOL_INFO.as_bytes();
    assert!(
        bytes.windows(tool.len()).any(|w| w == tool),
        "tool_info_in_the_file"
    );
}
