# aib-cloud

Command surface of Ez2 Managed Cloud (dev baseline 4.4.1 §12): every host
operation is a *plan* that can be shown, approved, applied, verified and —
where an inverse exists — rolled back.

**M-R1 (this slice): pure planning logic, no I/O.** `aib_cloud::robot` validates
operator input for the Hetzner Robot webservice, builds the request plan, derives
the approval facts from the same values (so the popup can never name a different
server than the URL), and masks password-like fields out of replies.

Not here yet (waiting on the ADR draft, `pending/ADR-draft-aib-cloud-crate-4542e-1109.md`):
the HTTPS transport, the secret source, the human-approval gate and the CLI binary.
Those are ports; the Python PoC in `aib/tools/cloud-host/robot.py` remains the
acceptance baseline until the Rust path replaces it end to end.

```sh
cargo test -p aib-cloud
```

## `aib_cloud::isolation` (M0 item 4, 11-09)

The 4.4.1 §5 isolation matrix as data: from a host topology (host, Edge CT, site CTs,
tailnet, public probe targets) it derives every cell (who probes what, TCP / DNS / ICMP,
open or blocked) and gives every *blocked* cell a *control* cell that proves the target
is alive — a timeout alone is never a pass. `probe_plan()` is the job list for the
host-side runner `aib/tools/cloud-host/ez2-isolation-probe` (joins each CT's network
namespace); `judge()` turns its results into a pass / fail / missing receipt.
`tests/fixtures/isolation-eu02-1109*.json` is EU02's recorded run: 51 / 51 cells pass
(48 from the host with a hostile twin CT restored from backup, 3 from EU01).
