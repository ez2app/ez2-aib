// SPDX-License-Identifier: Apache-2.0
//! EU02's real topology (11-09): the matrix this crate derives for it, and the receipt for
//! the probe results recorded on the host. `tests/fixtures/isolation-eu02-*.json` are the
//! acceptance evidence for 4.4.1 §5 / M0 item 4. Run with `-- --nocapture` and
//! `AIB_CLOUD_PRINT_PLAN=1` to print the probe plan for the host-side runner.

use aib_cloud::isolation::{judge, matrix, probe_plan, Ct, InternetTargets, Ip, Topology};
use std::fs;
use std::path::Path;

fn eu02(sites: &[(u32, &str)]) -> Topology {
    Topology {
        host_internal: Ip::parse("10.77.0.1").expect("ip"),
        host_public: Ip::parse("135.181.75.90").expect("ip"),
        tailnet: Some(Ip::parse("100.120.222.46").expect("ip")),
        edge: Ct::new(101, "10.77.0.2").expect("ct"),
        sites: sites
            .iter()
            .map(|(id, ip)| Ct::new(*id, ip).expect("ct"))
            .collect(),
        internet: InternetTargets {
            dns: Ip::parse("1.1.1.1").expect("ip"),
            http: Ip::parse("151.101.2.132").expect("ip"), // deb.debian.org
            https: Ip::parse("140.82.121.4").expect("ip"), // github.com
            smtp: Ip::parse("64.233.184.26").expect("ip"), // aspmx.l.google.com
            ssh: Ip::parse("140.82.121.4").expect("ip"),
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
    // Two sites: the real site CT 110 and the drill peer CT 111 restored from backup for the run.
    let cells = matrix(&eu02(&[(110, "10.77.0.10"), (111, "10.77.0.11")])).expect("matrix");
    let plan = probe_plan(&cells);
    if std::env::var("AIB_CLOUD_PRINT_PLAN").is_ok() {
        println!(
            "PLAN_BEGIN\n{}\nPLAN_END",
            serde_json::to_string_pretty(&plan).expect("json")
        );
    }
    assert!(plan.as_array().expect("array").len() >= 40);
}

#[test]
fn eu02_recorded_results_pass() {
    let Some(results) = fixture("isolation-eu02-1109.json") else {
        eprintln!("no fixture yet: tests/fixtures/isolation-eu02-1109.json (run ez2-isolation-probe on EU02)");
        return;
    };
    let cells = matrix(&eu02(&[(110, "10.77.0.10"), (111, "10.77.0.11")])).expect("matrix");
    let rc = judge(&cells, &results, false).expect("judge");
    assert!(
        rc.ok(),
        "EU02 isolation receipt not clean: fail={:?} missing={:?}",
        rc.fail,
        rc.missing
    );
    assert!(rc.pass.len() >= 40, "{}", rc.pass.len());
    // the receipt itself is evidence: keep it next to the results
    if let Ok(path) = std::env::var("AIB_CLOUD_WRITE_RECEIPT") {
        fs::write(
            path,
            serde_json::to_string_pretty(&rc.to_json()).expect("json"),
        )
        .expect("write receipt");
    }
}
