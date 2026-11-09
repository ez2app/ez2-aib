// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host` — the host-side shell of Ez2 Managed Cloud (4.4.1 §12, I/O half).
//!
//! ```text
//! aib-cloud-host probe run   <plan.json> --host <public ip> [--out report.json] [--external]
//! aib-cloud-host probe merge <report.json> <report.json>… --out merged.json
//! aib-cloud-host probe one   <tcp|dns|icmp> <ip> <port>      (internal: runs inside a CT netns)
//! aib-cloud-host probe netns                                  (internal: prints the netns inode)
//! ```
//!
//! `plan.json` is `aib_cloud::isolation::probe_plan()`; the report is what
//! `aib_cloud::isolation::judge()` accepts. The plan is validated as a whole before anything
//! runs; `from = ct:<id>` cells are executed by re-running this same binary under
//! `nsenter -t <init pid> -n` after proving the namespace it lands in is that container's.

mod probe;

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match probe::main(&args) {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("aib-cloud-host: {e}");
            ExitCode::from(2)
        }
    }
}
