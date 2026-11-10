// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host` — the host-side shell of Ez2 Managed Cloud (4.4.1 §12, I/O half).
//!
//! ```text
//! aib-cloud-host probe run   <plan.json> --host <public ip> [--out report.json] [--external]
//! aib-cloud-host probe merge <report.json> <report.json>… --out merged.json
//! aib-cloud-host probe one   <tcp|dns|icmp> <ip> <port>      (internal: runs inside a CT netns)
//! aib-cloud-host probe netns                                  (internal: prints the netns inode)
//! aib-cloud-host site verify [<code>] [--records dir] [--thin vg/pool] [--json]
//! aib-cloud-host site quarantine <code> --reason <r> --plan   (also: unquarantine, quarantine-archive; plan only)
//! aib-cloud-host backup check [--remote <name>:<path>] [--records dir] [--json]   (lists the off-site backup; reads nothing else)
//! aib-cloud-host edge domain preflight <name> --ip <ipv4> | edge domain add|rm <site> <name> --plan   (DNS lookups; plan only)
//! aib-cloud-host customer --site <code>          (forced command of a customer key; request in SSH_ORIGINAL_COMMAND)
//! aib-cloud-host site usage [--sample-seconds N] [--json]   (what each site really uses and what ARC advises; read-only)
//! aib-cloud-host op status [<op id>] | op unfinished <target> | op recovered <op id>   (journals and the host lock; recovered closes a checked operation)
//! aib-cloud-host root-password apply <file>
//! aib-cloud-host spec lint <file> | spec plan <file> [--from <step>] [--json]   (provisioning spec; reads one file)
//! aib-cloud-host self-update <version> [--dest f] [--allow-downgrade] [--base-url file:///d]
//! aib-cloud-host --version                                    (aib-cloud-host <version> <os>-<arch>)
//! ```
//!
//! `plan.json` is `aib_cloud::isolation::probe_plan()`; the report is what
//! `aib_cloud::isolation::judge()` accepts. The plan is validated as a whole before anything
//! runs; `from = ct:<id>` cells are executed by re-running this same binary under
//! `nsenter -t <init pid> -n` after proving the namespace it lands in is that container's.

// This binary runs on the Proxmox host (Linux) only: nsenter / lxc-info / pct, process groups,
// unix file modes. On any other OS it compiles to a stub that says so (f1fae 11-10: decide once
// at crate level instead of sprinkling #[cfg(unix)]), so the workspace still builds everywhere.
#[cfg(unix)]
mod backupcmd;
#[cfg(unix)]
mod customercmd;
#[cfg(unix)]
mod edgecmd;
#[cfg(unix)]
mod opstore;
#[cfg(unix)]
mod probe;
#[cfg(unix)]
mod quarantinecmd;
#[cfg(unix)]
mod siteverify;
#[cfg(unix)]
mod speccmd;
#[cfg(unix)]
mod update;
#[cfg(unix)]
mod usagecmd;

use std::process::ExitCode;

#[cfg(unix)]
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

#[cfg(not(unix))]
fn main() -> ExitCode {
    eprintln!("aib-cloud-host runs on the Proxmox host (Linux) only; nothing to do on this OS");
    ExitCode::from(2)
}
