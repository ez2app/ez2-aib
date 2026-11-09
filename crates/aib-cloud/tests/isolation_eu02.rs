// SPDX-License-Identifier: Apache-2.0
//! EU02's real topology (11-09): the matrix this crate derives for it, and the receipt for
//! the probe report recorded on the host. `tests/fixtures/isolation-eu02-*.json` are the
//! acceptance evidence for 4.4.1 §5 / M0 item 4. Run with `-- --nocapture` and
//! `AIB_CLOUD_PRINT_PLAN=1` to print the probe plan for the host-side runner.

use aib_cloud::isolation::{
    judge, matrix, plan_fingerprint, probe_plan, Ct, InternetTargets, Ip, Ip6, Topology,
};
use std::fs;
use std::path::Path;

const HOST_PUBLIC: &str = "135.181.75.90";

/// EU02 on 11-09: Edge CT 101, site CTs 110 (ez2.work + eu02-site1) and 112 (eu02-site2),
/// Tailscale on, EU01 as the peer host, IPv6 on the host only.
fn eu02() -> Topology {
    Topology {
        host_internal: Ip::parse("10.77.0.1").expect("ip"),
        host_public: Ip::parse(HOST_PUBLIC).expect("ip"),
        tailnet: Some(Ip::parse("100.120.222.46").expect("ip")),
        peer_host: Some(Ip::parse("136.243.105.110").expect("ip")), // EU01
        edge: Ct::new(101, "10.77.0.2").expect("ct"),
        sites: vec![
            Ct::new(110, "10.77.0.10").expect("ct"),
            Ct::new(112, "10.77.0.12").expect("ct"),
        ],
        internet: InternetTargets {
            dns: Ip::parse("1.1.1.1").expect("ip"),
            http: Ip::parse("151.101.2.132").expect("ip"), // deb.debian.org
            https: Ip::parse("140.82.121.4").expect("ip"), // github.com
            ssh: Ip::parse("140.82.121.4").expect("ip"),   // github.com sshd
            https6: Some(Ip6::parse("2606:4700:4700::1111").expect("ip6")), // Cloudflare DNS over HTTPS
        },
    }
}

fn fixture(name: &str) -> Option<serde_json::Value> {
    let p = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name);
    let text = fs::read_to_string(p).ok()?;
    serde_json::from_str(&text).ok()
}

#[test]
fn eu02_plan_prints_when_asked() {
    let cells = matrix(&eu02()).expect("matrix");
    let plan = probe_plan(&cells);
    if std::env::var("AIB_CLOUD_PRINT_PLAN").is_ok() {
        println!(
            "PLAN_BEGIN\n{}\nPLAN_END",
            serde_json::to_string_pretty(&plan).expect("json")
        );
        println!("PLAN_FNV {}", plan_fingerprint(&plan));
    }
    let n = plan.as_array().expect("array").len();
    assert!(n >= 60, "{n} cells");
    // the plan the runner is given must be exactly the one judge() will accept
    if let Some(committed) = fixture("isolation-eu02-plan.json") {
        assert_eq!(committed, plan, "tests/fixtures/isolation-eu02-plan.json is stale: regenerate with AIB_CLOUD_PRINT_PLAN=1");
    }
}

#[test]
fn eu02_recorded_report_passes() {
    let Some(report) = fixture("isolation-eu02-1109.json") else {
        eprintln!("no fixture yet: tests/fixtures/isolation-eu02-1109.json (run aib-cloud-host probe run on EU02)");
        return;
    };
    let cells = matrix(&eu02()).expect("matrix");
    // external cells were measured from EU01 and merged in, so they are required here
    let rc = judge(&cells, HOST_PUBLIC, &report, true).expect("judge");
    assert!(
        rc.ok(),
        "EU02 isolation receipt not clean: fail={:?} missing={:?}",
        rc.fail,
        rc.missing
    );
    assert!(rc.missing.is_empty(), "missing={:?}", rc.missing);
    assert_eq!(rc.pass.len(), cells.len(), "every cell must have passed");
    if let Ok(path) = std::env::var("AIB_CLOUD_WRITE_RECEIPT") {
        fs::write(
            path,
            serde_json::to_string_pretty(&rc.to_json()).expect("json"),
        )
        .expect("write receipt");
    }
}

#[test]
fn eu02_report_for_another_plan_is_refused() {
    let Some(mut report) = fixture("isolation-eu02-1109.json") else {
        return;
    };
    // a report recorded against a topology with one site fewer must not judge this matrix
    report["plan_fnv"] = serde_json::Value::String("0000000000000000".to_owned());
    let cells = matrix(&eu02()).expect("matrix");
    assert!(judge(&cells, HOST_PUBLIC, &report, true).is_err());
}
