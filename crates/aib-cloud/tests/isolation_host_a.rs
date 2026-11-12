// SPDX-License-Identifier: Apache-2.0
//! Host A's real topology (11-09): the matrix this crate derives for it, and the receipt for
//! the probe report recorded on the host. `tests/fixtures/isolation-host-a-*.json` are the
//! acceptance evidence for 4.4.1 §5 / M0 item 4. Run with `-- --nocapture` and
//! `AIB_CLOUD_PRINT_PLAN=1` to print the probe plan for the host-side runner.

use aib_cloud::isolation::{
    judge, matrix, plan_fingerprint, probe_plan, Ct, InternetTargets, Ip, Ip6, Topology,
};
use std::fs;
use std::path::Path;

const HOST_PUBLIC: &str = "203.0.113.90";

/// host A on 11-09: Edge CT 101, site CTs 110 (example.org + hosta-site1) and 112 (hosta-site2),
/// Tailscale on, host B as the peer host, IPv6 on the host only.
fn host_a() -> Topology {
    Topology {
        host_internal: Ip::parse("10.77.0.1").expect("ip"),
        host_public: Ip::parse(HOST_PUBLIC).expect("ip"),
        tailnet: Some(Ip::parse("100.64.0.46").expect("ip")),
        peer_host: Some(Ip::parse("203.0.113.110").expect("ip")), // host B
        edge: Ct::new(101, "10.77.0.2").expect("ct"),
        sites: vec![
            Ct::new(110, "10.77.0.10").expect("ct"),
            Ct::new(112, "10.77.0.12").expect("ct"),
        ],
        internet: InternetTargets {
            dns: Ip::parse("192.0.2.53").expect("ip"),
            http: Ip::parse("192.0.2.80").expect("ip"), // stands in for a package mirror
            https: Ip::parse("192.0.2.44").expect("ip"), // stands in for a git host
            ssh: Ip::parse("192.0.2.44").expect("ip"),  // stands in for a git host sshd
            https6: Some(Ip6::parse("2001:db8::443").expect("ip6")), // stands in for a DNS-over-HTTPS resolver
        },
    }
}

/// Fixtures are committed evidence: a missing file or broken JSON is a failure, never a
/// silent skip (ebb36 r2 L1).
fn fixture(name: &str) -> serde_json::Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name);
    let text = fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: not JSON: {e}", p.display()))
}

#[test]
fn host_a_plan_prints_when_asked() {
    let cells = matrix(&host_a()).expect("matrix");
    let plan = probe_plan(&cells);
    if std::env::var("AIB_CLOUD_PRINT_PLAN").is_ok() {
        println!(
            "PLAN_BEGIN\n{}\nPLAN_END",
            serde_json::to_string_pretty(&plan).expect("json")
        );
        println!("PLAN_FNV {}", plan_fingerprint(&plan));
    }
    let n = plan.as_array().expect("array").len();
    assert!(n >= 66, "{n} cells");
    // the plan the runner is given must be exactly the one judge() will accept
    let committed = fixture("isolation-host-a-plan.json");
    assert_eq!(
        committed, plan,
        "tests/fixtures/isolation-host-a-plan.json is stale: regenerate with AIB_CLOUD_PRINT_PLAN=1"
    );
}

#[test]
fn host_a_recorded_report_passes() {
    let report = fixture("isolation-host-a-1109.json");
    let cells = matrix(&host_a()).expect("matrix");
    // external cells were measured from host B and merged in, so they are required here
    let rc = judge(&cells, HOST_PUBLIC, &report, true).expect("judge");
    assert!(
        rc.ok(),
        "host A isolation receipt not clean: fail={:?} missing={:?}",
        rc.fail,
        rc.missing
    );
    assert!(rc.missing.is_empty(), "missing={:?}", rc.missing);
    assert_eq!(
        rc.pass.len() + rc.observed.len(),
        cells.len(),
        "every cell must have passed or been observed"
    );
    if let Ok(path) = std::env::var("AIB_CLOUD_WRITE_RECEIPT") {
        fs::write(
            path,
            serde_json::to_string_pretty(&rc.to_json()).expect("json"),
        )
        .expect("write receipt");
    }
}

#[test]
fn host_a_report_for_another_plan_is_refused() {
    let mut report = fixture("isolation-host-a-1109.json");
    // a report recorded against a topology with one site fewer must not judge this matrix
    report["plan_fnv"] = serde_json::Value::String("0000000000000000".to_owned());
    let cells = matrix(&host_a()).expect("matrix");
    assert!(judge(&cells, HOST_PUBLIC, &report, true).is_err());
}
