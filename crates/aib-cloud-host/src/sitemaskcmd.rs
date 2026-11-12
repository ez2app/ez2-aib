// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site mask <code> --plan [--records dir]` — what one site's CT still
//! needs so that its users (and a customer's AI seat working as one of them) are not shown
//! the machine underneath or the platform it runs on (Andy 11-11; what and why:
//! `aib_cloud::mask`). **Read-only**: without `--plan` the command refuses.
//!
//! It reads the site's record, the CT's configuration (`pct config`), whether the CT runs
//! and, if it does, the mask hook's verdict; and on the host, whether the hook's wrapper is
//! the one this version installs. It prints:
//!
//! * `config_fixes` — keys of the CT's configuration to set, each with the step of opening
//!   a site it belongs to (`aib_cloud::mask::CONFIG_STEP`). A proposed hardware address
//!   is drawn fresh each time the plan is made; it is a proposal, nothing is kept.
//! * `ct_files` — files to write inside the CT (`aib_cloud::mask::FILES_STEP`); the
//!   user-namespace file is applied and read back right away (`then`, `read_back`), the CT
//!   already runs at that step. They are
//!   not read here: reading them means entering the customer's CT, which this plan does
//!   not do; they are listed every time and `site verify` / the red-team check judges them.
//! * `restart_needed` — the covers are put in place as the CT starts: a running CT that
//!   gets configuration fixes shows nothing new until it is restarted.
//! * `check_inside` and `cannot_hide` — what to read inside afterwards, and what no mask
//!   hides.
//!
//! The same fixes are owed after `site restore` (a restored configuration need not carry
//! the hook line or the local address — 33e28).
//!
//! * `other_nics` — network interfaces besides `net0`: not judged here, listed for a
//!   person.
//!
//! Exit: 0 nothing to change and, for a running CT, the verdict says masked; 1 something
//! to change, the running CT is not masked (its verdict file is not there), or another
//! interface is listed; 3 something could not be read, the verdict file included (the plan
//! says what); 2 a usage error.

use std::fs;
use std::process::Command;
use std::time::Duration;

use aib_cloud::mask::{self, ConfigFix, PlanError};
use aib_cloud::site::parse_pct_config;
use serde_json::{json, Value};

use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
/// Where the hook's wrapper is installed (the path `mask::HOOK_LINE` names).
const WRAPPER: &str = "/usr/local/sbin/ez2-ct-mask-hook";

/// What was read on the host for one site. `None` is "could not be read".
#[derive(Default)]
pub struct Seen {
    pub config: Option<String>,
    pub running: Option<bool>,
    /// The hook's verdict file, only read for a running CT: `Some(None)` when it is
    /// known not to be there (the hook did not run at this start), `None` when it could
    /// not be read (c477a, ct-mask2 L1).
    pub verdict: Option<Option<String>>,
    /// The wrapper as installed; `Some(None)` when it is known not to be there.
    pub wrapper: Option<Option<String>>,
}

fn wrapper_word(w: &Option<Option<String>>) -> &'static str {
    match w {
        None => "unread",
        Some(None) => "missing",
        Some(Some(t)) if t == mask::HOOK_WRAPPER => "in_place",
        Some(Some(_)) => "differs",
    }
}

fn fix_json(f: &ConfigFix) -> Value {
    json!({ "key": f.key, "value": f.value, "why": f.why, "step": mask::CONFIG_STEP.as_str() })
}

/// The plan document and its exit code. Separate from [`cmd`] so it can be tested without
/// a host. `new_hwaddr` must be local (`mask::local_hwaddr`).
pub fn plan_doc(code: &str, ct: u32, seen: &Seen, new_hwaddr: &str) -> (Value, u8) {
    let cfg = seen
        .config
        .as_deref()
        .and_then(|t| parse_pct_config(t).ok());
    let fixes = cfg.as_deref().map(|c| mask::config_fixes(c, new_hwaddr));
    let hostname = cfg
        .as_deref()
        .and_then(|c| c.iter().find(|(k, _)| k == "hostname"))
        .map(|(_, v)| v.as_str());
    let hosts = hostname.and_then(mask::hosts_text);
    let verdict = match (seen.running, &seen.verdict) {
        (None, _) | (Some(true), None) => "unread",
        (Some(false), _) => "not_running",
        (Some(true), Some(v)) if mask::result_is_masked(v.as_deref()) => "masked",
        (Some(true), Some(_)) => "not_masked",
    };
    let others: Vec<&str> = cfg.as_deref().map(mask::other_nics).unwrap_or_default();
    let mut unread: Vec<&str> = Vec::new();
    let fixes_json = match &fixes {
        None => {
            unread.push("the CT's configuration");
            Value::Null
        }
        Some(Err(PlanError::NoNet0)) => {
            unread.push("the CT's network line (net0)");
            Value::Null
        }
        Some(Ok(list)) => Value::Array(list.iter().map(fix_json).collect()),
    };
    if seen.running.is_none() {
        unread.push("whether the CT runs");
    } else if verdict == "unread" {
        unread.push("the hook's verdict file");
    }
    if hosts.is_none() {
        unread.push("the CT's own name, for its hosts file");
    }
    let wrapper = wrapper_word(&seen.wrapper);
    if wrapper == "unread" {
        unread.push("the hook's wrapper on the host");
    }
    let to_change = matches!(&fixes, Some(Ok(list)) if !list.is_empty());
    let rc = if !unread.is_empty() {
        3
    } else if to_change || verdict == "not_masked" || wrapper != "in_place" || !others.is_empty() {
        1
    } else {
        0
    };
    let doc = json!({
        "plan": "site-mask",
        "site": code,
        "ct": ct,
        "level": "L2",
        "host_once": { "wrapper": WRAPPER, "now": wrapper, "text": mask::HOOK_WRAPPER },
        "config_fixes": fixes_json,
        "ct_files": [
            {
                "path": mask::SYSCTL_FILE, "text": mask::SYSCTL_TEXT, "step": mask::FILES_STEP.as_str(),
                "then": mask::SYSCTL_APPLY,
                "read_back": { "read": "/proc/sys/user/max_user_namespaces", "want": "0" },
                "or": "restart the CT before the edge step"
            },
            { "path": mask::HOSTS_KEEP_FILE, "text": "", "step": mask::FILES_STEP.as_str() },
            { "path": "/etc/hosts", "text": hosts, "step": mask::FILES_STEP.as_str() },
        ],
        "verdict": verdict,
        "other_nics": others,
        "restart_needed": to_change && seen.running == Some(true),
        "check_inside": mask::INSIDE_EXPECT.iter().map(|(what, want)| json!({ "read": what, "want": want })).collect::<Vec<_>>(),
        "cannot_hide": mask::CANNOT_HIDE,
        "unread": unread,
        "not_looked_at": ["the files inside the CT (not entered by this plan)"],
    });
    (doc, rc)
}

fn capture(program: &str, args: &[&str], seconds: u64) -> Option<String> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    run_capture(&mut cmd, Duration::from_secs(seconds)).ok()
}

fn random6() -> Result<[u8; 6], String> {
    use std::io::Read;
    let mut b = [0u8; 6];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|e| format!("/dev/urandom: {e}"))?;
    Ok(b)
}

fn see(ct: u32) -> Seen {
    let id = ct.to_string();
    let running = match capture("pct", &["status", &id], 20)
        .as_deref()
        .map(str::trim)
    {
        Some("status: running") => Some(true),
        Some("status: stopped") => Some(false),
        _ => None,
    };
    let verdict = if running == Some(true) {
        match fs::read_to_string(format!("/run/ez2-cloud/mask/{ct}/result")) {
            Ok(t) => Some(Some(t)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(None),
            Err(_) => None,
        }
    } else {
        Some(None)
    };
    let wrapper = match fs::read_to_string(WRAPPER) {
        Ok(t) => Some(Some(t)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(None),
        Err(_) => None,
    };
    Seen {
        config: capture("pct", &["config", &id], 20),
        running,
        verdict,
        wrapper,
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = RECORDS.to_owned();
    let mut plan_only = false;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !plan_only {
        return Err(
            "only --plan is available: changing a CT's configuration is a host write, made through a reviewed sheet"
                .to_owned(),
        );
    }
    let spec = crate::quarantinecmd::load_spec(&code, &records)?;
    let seen = see(spec.ct);
    let (doc, rc) = plan_doc(&spec.code, spec.ct, &seen, &mask::local_hwaddr(random6()?));
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(rc)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const NEW: &str = "02:11:22:33:44:55";

    fn masked_config() -> String {
        "arch: amd64\nhostname: s-abc123\nlxc.hook.mount: /usr/local/sbin/ez2-ct-mask-hook\nnameserver: 1.1.1.1\nnet0: name=eth0,bridge=vmbr1,hwaddr=06:AA:BB:CC:DD:EE,ip=10.77.0.9/24,gw=10.77.0.1,type=veth\nsearchdomain: invalid\n".to_owned()
    }

    fn seen(config: Option<String>, running: Option<bool>, verdict: Option<&str>) -> Seen {
        Seen {
            config,
            running,
            verdict: Some(verdict.map(str::to_owned)),
            wrapper: Some(Some(mask::HOOK_WRAPPER.to_owned())),
        }
    }

    #[test]
    fn a_masked_running_site_has_nothing_to_do() {
        let (doc, rc) = plan_doc(
            "s-abc123",
            119,
            &seen(Some(masked_config()), Some(true), Some("ok 40\n")),
            NEW,
        );
        assert_eq!(rc, 0, "{doc}");
        assert_eq!(doc["config_fixes"], json!([]));
        assert_eq!(doc["verdict"], "masked");
        assert_eq!(doc["restart_needed"], false);
        assert!(doc["ct_files"][2]["text"]
            .as_str()
            .unwrap()
            .contains("s-abc123"));
    }

    #[test]
    fn a_running_site_without_its_covers_is_not_done() {
        let (doc, rc) = plan_doc(
            "s-abc123",
            119,
            &seen(
                Some(masked_config()),
                Some(true),
                Some("NOT MASKED cpuinfo\n"),
            ),
            NEW,
        );
        assert_eq!(rc, 1);
        assert_eq!(doc["verdict"], "not_masked");
        let (_, rc) = plan_doc(
            "s-abc123",
            119,
            &seen(Some(masked_config()), Some(true), None),
            NEW,
        );
        assert_eq!(rc, 1, "no verdict file is not masked");
    }

    #[test]
    fn fixes_on_a_running_site_need_a_restart_and_name_their_step() {
        let platform = masked_config().replace("06:AA:BB:CC:DD:EE", "BC:24:11:00:00:09");
        let (doc, rc) = plan_doc(
            "s-abc123",
            119,
            &seen(Some(platform.clone()), Some(true), Some("ok 40\n")),
            NEW,
        );
        assert_eq!(rc, 1);
        assert_eq!(doc["config_fixes"][0]["key"], "net0");
        assert_eq!(doc["config_fixes"][0]["step"], "limits");
        assert!(doc["config_fixes"][0]["value"]
            .as_str()
            .unwrap()
            .contains("hwaddr=02:11:22:33:44:55"));
        assert_eq!(doc["restart_needed"], true);
        let (doc, _) = plan_doc(
            "s-abc123",
            119,
            &seen(Some(platform), Some(false), None),
            NEW,
        );
        assert_eq!(
            doc["restart_needed"], false,
            "a stopped CT takes them at its next start"
        );
        assert_eq!(doc["verdict"], "not_running");
    }

    #[test]
    fn what_could_not_be_read_is_said_and_is_not_a_pass() {
        let (doc, rc) = plan_doc("s-abc123", 119, &seen(None, Some(false), None), NEW);
        assert_eq!(rc, 3);
        assert!(doc["config_fixes"].is_null());
        assert!(doc["unread"]
            .as_array()
            .unwrap()
            .iter()
            .any(|u| u == "the CT's configuration"));
        let no_net = "hostname: s-abc123\n".to_owned();
        let (_, rc) = plan_doc("s-abc123", 119, &seen(Some(no_net), Some(false), None), NEW);
        assert_eq!(rc, 3);
        let (_, rc) = plan_doc(
            "s-abc123",
            119,
            &seen(Some(masked_config()), None, None),
            NEW,
        );
        assert_eq!(rc, 3);
        let mut s = seen(Some(masked_config()), Some(false), None);
        s.wrapper = None;
        assert_eq!(plan_doc("s-abc123", 119, &s, NEW).1, 3);
    }

    #[test]
    fn a_wrapper_missing_or_older_is_owed_on_the_host() {
        for (w, word) in [
            (Some(None), "missing"),
            (Some(Some("#!/bin/sh\nexit 0\n".to_owned())), "differs"),
        ] {
            let mut s = seen(Some(masked_config()), Some(false), None);
            s.wrapper = w;
            let (doc, rc) = plan_doc("s-abc123", 119, &s, NEW);
            assert_eq!(doc["host_once"]["now"], word);
            assert_eq!(rc, 1, "{word}");
        }
    }

    #[test]
    fn without_plan_nothing_is_done() {
        let e = cmd(&["s-abc123".to_owned()]).unwrap_err();
        assert!(e.contains("only --plan"), "{e}");
    }

    #[test]
    fn a_verdict_that_cannot_be_read_is_not_a_finding_of_not_masked() {
        let mut s = seen(Some(masked_config()), Some(true), None);
        s.verdict = None;
        let (doc, rc) = plan_doc("s-abc123", 119, &s, NEW);
        assert_eq!(rc, 3);
        assert_eq!(doc["verdict"], "unread");
        assert!(doc["unread"]
            .as_array()
            .unwrap()
            .iter()
            .any(|u| u == "the hook's verdict file"));
    }

    #[test]
    fn another_nic_needs_a_person_and_the_sysctl_is_read_back() {
        let two = masked_config() + "net1: name=eth1,bridge=vmbr0,hwaddr=BC:24:11:00:00:01\n";
        let (doc, rc) = plan_doc("s-abc123", 119, &seen(Some(two), Some(false), None), NEW);
        assert_eq!(rc, 1);
        assert_eq!(doc["other_nics"], json!(["net1"]));
        assert_eq!(doc["ct_files"][0]["then"], mask::SYSCTL_APPLY);
        assert_eq!(doc["ct_files"][0]["read_back"]["want"], "0");
    }
}
