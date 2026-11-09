//! aib-verify — acceptance runs against a built aibd (1ced7, PLAN-approvals-to-rust C).
//!
//! Each run writes a report whose first line is fixed, so the merge chain can read it:
//!
//!   驗收: 通過|不通過 頭: <40 hex> 機: <host> 樣本: <group n>… 沒驗: <list|無>
//!
//! "沒驗" lists what this run did not cover; a skipped check is never a pass.

mod daemon;
mod g1;
mod http;
mod judge;
mod queue;

const USAGE: &str = "usage: aib-verify g1 --head <40 hex> --bin <abs aibd> --fixture <abs aib-fixture-tree> --out <dir>
                  [--baseline-bin <abs aibd of a known-broken head>]
                  [--claude <abs claude> [--claude-signer <team id>] [--claude-runs N]]
                  [--burst-runs N] [--stuck-runs N] [--host NAME]
       aib-verify queue --head <40 hex> --bin <abs aibd> --fixture <abs aib-fixture-tree> --out <dir> [--host NAME]
rc: 0 通過 / 1 不通過 / 2 usage / 3 could not run";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(dispatch(&args));
}

fn dispatch(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("g1") => match g1::Opts::parse(&args[1..]) {
            Ok(o) => g1::run(&o),
            Err(e) => {
                eprintln!("aib-verify g1: {e}\n{USAGE}");
                2
            }
        },
        Some("queue") => match queue::Opts::parse(&args[1..]) {
            Ok(o) => queue::run(&o),
            Err(e) => {
                eprintln!("aib-verify queue: {e}\n{USAGE}");
                2
            }
        },
        Some("-h" | "--help") => {
            println!("{USAGE}");
            0
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}
