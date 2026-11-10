# aib-cloud

Command surface of Ez2 Managed Cloud (dev baseline 4.4.1 §12): every host
operation is a *plan* that can be shown, approved, applied, verified and —
where an inverse exists — rolled back.

**M-R1 (this slice): pure planning logic, no I/O.** `aib_cloud::robot` validates
operator input for the provider console webservice, builds the request plan, derives
the approval facts from the same values (so the popup can never name a different
server than the URL), and masks password-like fields out of replies.

Not here yet (waiting on the ADR draft, `pending/ADR-draft-aib-cloud-crate-4542e-1109.md`):
the HTTPS transport, the secret source, the human-approval gate and the CLI binary.
Those are ports; the Python PoC in `aib/tools/cloud-host/robot.py` remains the
acceptance baseline until the Rust path replaces it end to end.

```sh
cargo test -p aib-cloud
```

## `aib_cloud::isolation` (M0 item 4, 11-09; r2 after ebb36's review)

The 4.4.1 §5 isolation matrix as data: from a host topology (host, Edge CT, site CTs,
tailnet, peer host, public probe targets, IPv6) it derives every cell (who probes what,
TCP / DNS / ICMP, open / blocked / closed). Every *blocked* cell names a *control* cell
with the **same target ip:port and protocol** from an allowed source, so a timeout alone
is never a pass; `check_matrix()` asserts that invariant and unique ids. `probe_plan()`
is the job list for the host-side runner `aib-cloud-host probe run` (crate `aib-cloud-host`;
validates the plan against a whitelist, joins each CT's network namespace via
`lxc-info` + `nsenter` and verifies the namespace it landed in); `judge()` accepts only a
report bound to this exact plan (`plan_fnv`) and host, checks every result's
from / kind / ip / port against its cell, and returns a pass / fail / missing receipt.
`tests/fixtures/isolation-host-a-*.json` is host A's recorded run (sites CT 110 + 112, the
3 public cells measured from host B).
Every receipt also lists `not_covered`: optional topology parts that were absent and
`egress-25` — the provider blocks outbound 25 for us, so no cell can tell our DROP from theirs;
before port 25 is ever opened for this host the egress-25 cells must come back (control from
an External source to the same MX:25).

## `aib_cloud::r2token` (M-R1b, 11-09)

Planning logic for the per-host Cloudflare R2 backup token (4.4.1 §10.1; Andy:
"key 從 host B 取得,再彈出生物辨識"): validates bucket / host alias / egress IPv4 /
zone / destination / rclone remote, derives the approval facts, the token policy
(one bucket, Item Read + Item Write, one `/32`) and the remote job spec from the
same values, judges the five-check verification report and strips secret-looking
keys before anything prints. Still no I/O: the ssh pipeline and the Touch ID gate
stay in the Python PoC `aib/tools/cloud-host/r2token.py` (#563, draft) until the
Rust shell lands with the ADR.
