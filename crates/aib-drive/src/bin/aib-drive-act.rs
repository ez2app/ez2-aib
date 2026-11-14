//! `aib-drive-act`: the S79 n-3 dry run as its own program until the integration routes
//! `aib-drive act` / `aib-drive switch` to `act::cli_act` / `act::cli_switch` from main.rs
//! (IFACE-s79 §0: a slice adds only its own files). The integration deletes this file.

// some constants and helpers are only used by the integration
#[allow(dead_code)]
#[path = "../act.rs"]
mod act;

use std::process::ExitCode;

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let rc = match raw.split_first() {
        Some((c, rest)) if c == "act" => act::cli_act(rest),
        Some((c, rest)) if c == "switch" => act::cli_switch(rest),
        _ => {
            eprintln!("{}", act::ACT_USAGE);
            act::RC_USAGE
        }
    };
    ExitCode::from(rc)
}
