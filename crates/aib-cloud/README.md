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
