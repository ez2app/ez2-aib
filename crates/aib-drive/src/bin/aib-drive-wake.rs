//! S79 n-4a (IFACE r1 3): today's own entry for `aib-drive event …` until integration
//! routes `aib-drive event` to `wake::cli_event` in main.rs and drops this file.
//!
//!   aib-drive-wake event <merge|receipt|review|dm_done|seat_free|tick> [--ref X] [--by NAME]

use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

// parse / report_event_of / is_own are for observe and act (integration), not this entry
#[allow(dead_code)]
#[path = "../wake.rs"]
mod wake;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(("event", rest)) = args.split_first().map(|(c, r)| (c.as_str(), r)) else {
        eprintln!("usage: aib-drive-wake event <kind> [--ref X] [--by NAME]");
        return ExitCode::from(2);
    };
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(std::path::PathBuf::from);
    let root = aib_drive::observe::state_root(
        std::env::var("EZ2_AIB_STATE_ROOT").ok().as_deref(),
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        home.as_deref(),
    );
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or((0, 0), |d| (d.as_secs(), d.subsec_nanos()));
    let o = wake::cli_event(
        rest,
        root,
        std::env::var("AIB_DRIVE_BY").ok(),
        now,
        std::process::id(),
    );
    print!("{}", o.stdout);
    eprint!("{}", o.stderr);
    ExitCode::from(o.code)
}
