# aib-cloud-host

The host-side **I/O shell** of Ez2 Managed Cloud (4.4.1 §12). `aib-cloud` plans and judges
with no I/O (`#![no_std]`); this binary executes on the Proxmox host (or a public vantage point)
and hands the results back.

```text
aib-cloud-host root-password apply <file>   # 忘了 root 密碼:Mac 上 aib secret ask HOST_ROOT_PASSWORD_<HOST> --remote-only --generate --push → 這裡套用
aib-cloud-host probe run   <plan.json> --host <public ip> [--out report.json] [--external]
aib-cloud-host probe merge <report.json> <report.json>… --out merged.json
```

`plan.json` = `aib_cloud::isolation::probe_plan()`. The whole plan is validated against a
whitelist before anything runs; `from = ct:<id>` cells re-run this same binary under
`nsenter -t <init pid> -n` (pid from `lxc-info -n <id> -p -H`) after proving the namespace
it lands in is `/proc/<pid>/ns/net` and not the host's. TCP is a 3 s `connect`, DNS one UDP
query to `<ip>:53`, ICMP one `ping -c 1 -W 2`. No shell strings anywhere. `plan_fnv` is
`aib_cloud::isolation::plan_fingerprint` of the plan as loaded, so `judge()` accepts only the
report of exactly that plan on that host.

Replaces the Python runner `aib/tools/cloud-host/ez2-isolation-probe` (Andy 11-09: every new
tool is Rust; that file goes once this binary has run a full matrix on EU02).

Build for the host: workflow `cloud-host` (static `x86_64-unknown-linux-musl`), artifact
`aib-cloud-host-x86_64-unknown-linux-musl`; copy to `/usr/local/sbin/aib-cloud-host`.

CI note: after #568 merged and this PR was retargeted to main, GitHub ran no pull_request workflows for 40671e3d; this line exists to fire a synchronize event.
