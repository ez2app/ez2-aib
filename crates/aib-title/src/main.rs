// SPDX-License-Identifier: Apache-2.0
//! aib-title — `title-beat.sh --once` in one process. See the library docs.
//!
//!   aib-title [--once] [--print] [--dry-run]
//!
//! Exit: 0 done (a title tmux would not take is still 0), 2 usage, the sampler's exit code
//! when title_sample.py fails, 1 when it cannot start or cockpit-env.sh was not sourced.

use aib_title::{once, parse_args, Env};
use std::io::Write;

fn main() {
    let env = Env::from_process();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opts = match parse_args(&args, &env) {
        Ok(o) => o,
        Err(m) => {
            eprintln!("{m}");
            std::process::exit(2);
        }
    };
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    let rc = once(&env, opts, &mut out);
    let _ = out.flush();
    std::process::exit(rc);
}
