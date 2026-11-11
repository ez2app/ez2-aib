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
//! aib-cloud-host site create <domain> --plan [--blank] [--tenant <id>]   (numbers, title, first administrators, the customer's quota and the ten steps of opening a site; plan only)
//! aib-cloud-host site create <domain> --apply <plan.json> [--tenant <id>]   (B1: recompute the plan under the host lock, same hash, then the clone only; undone on failure)
//! aib-cloud-host site create <domain> --run <code>   (a customer's opening in the background: their plan applied under their code, then --continue to the end; started by their `site create`)
//! aib-cloud-host site create <domain> --continue <code>   (B2: limits and network, firewall file, start; B3: install with the scheduler on a timer, identity — undone on failure; B4: licences, entry, health, then the full record; B5: the DNS records at Cloudflare between licences and entry — kept for a person on failure; from the opening record; each checked)
//! aib-cloud-host site remove <code> --address <main address> --plan [--licences-left-at-vendor]   (probes the ten steps of a removal)
//! aib-cloud-host site remove <code> --address <main address> --apply <plan.json>   (carries it out up to the DNS step; approved operations only)
//! aib-cloud-host backup check [--remote <name>:<path>] [--records dir] [--json]   (lists the off-site backup; reads nothing else)
//! aib-cloud-host edge domain preflight <name> --ip <ipv4> | edge domain add|rm <site> <name> --plan   (DNS lookups; plan only)
//! aib-cloud-host customer --site <code> | --tenant <id> --roles <r,…>          (forced command of a customer key; request in SSH_ORIGINAL_COMMAND)
//! aib-cloud-host site usage [--sample-seconds N] [--json]   (what each site really uses and what ARC advises; read-only)
//! aib-cloud-host op status [<op id>] | op unfinished <target> | op recovered <op id> | op archive <op id> [--target <t>]   (journals and the host lock; recovered closes a checked operation; archive moves a finished journal aside, or a damaged one once the person names its target)
//! aib-cloud-host roster check <file> | entry-lines <file> | ssh-lines <file> <site> | sudoers <file>… | share-check <file> [--owner a]… [--sharer a]   (a customer's roster as authorized_keys lines, or every roster as the entry account's sudoers file; prints, changes nothing)
//! aib-cloud-host leak-words --out <file>   (the host's own part of the private leak list, read on the spot; values only into the 0600 file)
//! aib-cloud-host root-password apply <file>
//! aib-cloud-host spec lint <file> | spec plan <file> [--from <step>] [--json]   (provisioning spec; reads one file)
//! aib-cloud-host self-update <version> [--dest f] [--allow-downgrade] [--base-url file:///d]
//! aib-cloud-host --version                                    (aib-cloud-host <version> <os>-<arch>)
//! aib-cloud-host build-info     (aib-build-info version=<v> commit=<40 hex|unknown> dirty=<0|1>;)
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
mod cfdns;
#[cfg(unix)]
mod customercmd;
#[cfg(unix)]
mod edgecmd;
#[cfg(unix)]
mod leakwordscmd;
#[cfg(unix)]
mod opstore;
#[cfg(unix)]
mod probe;
#[cfg(unix)]
mod quarantinecmd;
#[cfg(unix)]
mod rostercmd;
#[cfg(unix)]
mod sitecreatecmd;
#[cfg(unix)]
mod sitecreaterun;
#[cfg(unix)]
mod siteidentity;
#[cfg(unix)]
mod sitelive;
#[cfg(unix)]
mod siteremovecmd;
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
