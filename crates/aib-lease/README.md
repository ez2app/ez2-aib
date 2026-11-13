# aib-lease

The per-machine run lease (`aib lease`, 1.x `aib/tools/governance/host_lease.py`) for a holder
that runs on **another** machine. Team tool crate (RULES §9): it reads and writes the 1.x files
under `~/.ez2-aib/state/`; `aibd` must not depend on it.

## Why (b127)

1.x records the holder's pid with `host = <this machine>` and checks it here with `kill -0` / `ps`.
A script on the Mac that takes the mac-c lease over ssh gives a Mac pid. On mac-c that pid
is either missing (judged dead: the lease can be taken over at once, the queue entry is dropped) or
belongs to some unrelated process. A queue entry also drops at `until_epoch` while its runner is
still waiting (11-09 21:47, K10: registered with `--ttl 1500`, dropped 1505 s later, logged only as
`why: gone`).

## What

| command | |
|---|---|
| `acquire --owner O --what W --run-id ID --host H --pid P --heartbeat-s S [--exclusive] [--priority]` | lease for a holder on machine H |
| `queue add --owner O --what W --run-id ID --host H --pid P --heartbeat-s S [--notify C/S] [--priority]` | queue place for it |
| `heartbeat --run-id ID` | still here: pushes the deadline to now + 3 × S |
| `queue list [--all]` | live entries; `--all` also the ones that no longer count, with `gone_why` |

* `--host` another machine ⇒ `--heartbeat-s` (10–600) is required; the pid is recorded but never
  probed here. Without `--host` (or naming this machine) the 1.x rules apply unchanged.
* A beat after the deadline does not revive the record: rc 4 `not_found` = you lost the place.
* With `--heartbeat-s S`, `--ttl` is ignored on both `acquire` and `queue add`: the deadline is always
  now + 3 × S and only beats move it. `--ttl` counts only for holders without a heartbeat (1.x rules).
* `release` / `renew` / `status` / `queue remove`: keep using `aib lease` (same files).
* Output: one JSON line. Exit 0 ok, 2 usage (or the local pid is already gone), 3 files unreadable
  (fail closed), 4 held / queue ahead / not found. Judge by rc only.

## Same files as 1.x

The heartbeat moves `until_epoch` / `expiry_epoch`, the fields 1.x already reads **by time** for a
record of another host. So the frozen 1.x tool reaches the same decision on the same files and does
not drop a beating entry. `tests/python_compat.rs` runs both tools on the same inputs:

| case | 1.x | aib-lease |
|---|---|---|
| remote entry, beating, 1.x `queue add` cleans the file | kept | kept |
| remote entry ahead, 1.x `acquire` on a free machine | rc 4 | rc 4 |
| remote entry, silent past 3 × S | dropped | dropped (`gone_why: heartbeat_stale`) |
| remote lease, beating / silent | rc 4 / taken over | rc 4 / taken over (`heartbeat_stale`) |
| local live pid lease, long after expiry | never taken over | never taken over |
| local entry whose pid exited | dropped | dropped (`pid_dead`) |
| **D1** holder on another machine, no `--host` | taken over at once (`holder_dead`) | `--host`: kept |
| **D2** dropped entry in the log | `why: gone` | `why: gone` + `gone_why` |

## Remote holder (Mac → mac-c)

```sh
J="-J host-a-ts mac-c"; RID=k10k-2240e; H=$(uname -n); S=60
cleanup() { kill $HB $T 2>/dev/null; ssh $J aib lease release --run-id $RID; ssh $J aib lease queue remove --run-id $RID; }
trap cleanup EXIT                 # release on every way out: green, red, lost place, Ctrl-C
trap 'exit 4' TERM                # the beat loop sends TERM when the place is lost
ssh $J aib-lease queue add --owner 2240e --what k10k --run-id $RID --host "$H" --pid $$ --heartbeat-s $S
( while kill -0 $$ 2>/dev/null; do
    ssh $J aib-lease heartbeat --run-id $RID >/dev/null; rc=$?
    # 255 = ssh / jump host hiccup: just beat again next round (the place is lost only after 3 × S of silence)
    # 4   = record gone or taken over: stop everything, the test must not go on without the lease
    [ $rc -eq 4 ] && { kill -TERM $$; break; }
    sleep $S
  done ) & HB=$!
until ssh $J aib-lease acquire --owner 2240e --what k10k --run-id $RID --host "$H" --pid $$ --heartbeat-s $S --exclusive; do sleep 30; done
ssh -tt $J 'cd ~/tree && node tools/test.js' & T=$!   # child, so cleanup can stop it; -tt so the remote side gets a hangup
wait $T
```

* Do not `|| break` on a failed beat: one jump-host hiccup would stop the beats for good while the test keeps
  running, and 3 × S later someone else takes the machine with your test still on it.
* Killing the local `ssh` does not always stop the remote command (no tty ⇒ no hangup). Use `-tt` as above, or
  kill the remote pid yourself before `release`.

Tests: `cargo test -p aib-lease` (not on the Mac A: heat order; mac-c or CI).

## `aib-lease run` — steps on another Mac under its lease (D3 + D5 + D6)

```sh
aib-lease run --host 100.64.0.31 --owner 043dd \
  --step 'cargo clippy -p aibd --all-targets --locked -- -D warnings' \
  --step 'cargo test -p aibd --locked --test sessions'
# or: aib-lease run --host H --owner S --rev <commit|tree> -- cargo test -p aibd
```

* Sends `git archive <rev>` (default HEAD; uncommitted changes are not sent, it warns) on ssh's
  stdin; the remote script goes as a base64 argument. ssh always has `ForwardAgent=no`,
  `ClearAllForwardings=yes`, `BatchMode=yes`, and `ServerAliveInterval=15` /
  `ServerAliveCountMax=4`: a network that vanishes without a FIN / RST ends the local ssh after
  about a minute.
* On the other Mac the ssh session only unpacks, starts a **runner** detached (`nohup`, stdin
  `/dev/null`, output to a log file next to the tree) and follows that log, printing it here.
  The runner: `aib lease acquire … --pid $$` (its own pid) → **nothing runs unless rc 0** →
  `EXIT` trap releases the lease, stops a running step's process tree, removes the tree and
  leaves its exit code for the follower → each step as its own `bash -c`, every rc printed; a
  failing step does not stop the rest.
* The lease is `--exclusive` (the run is being measured, small `aib test` batches wait);
  `--shared` asks without it.
* **A dropped connection does not stop the run (A1, Andy 11-12 04:2x).** The follower ends (its
  beat line every 5 s fails to write), the runner keeps going and keeps the lease until its steps
  end; this end exits **76 `remote_running`** and prints the runner's pid and log path. Check it
  with `aib lease status` on that Mac; stop it with `kill <pid>` there (TERM / INT stop the steps
  and release on the way out). The log and exit-code files stay in `$TMPDIR` after a drop.
  **Ctrl-C (or closing the terminal) here does not stop it either**: as soon as the runner starts,
  this end prints its pid and `ssh <host> kill <pid>`. A drop after unpacking but before that line
  also exits 76 (the run may already be going); check `aib lease status` before sending it again.
  Stopping a step reaches only processes still in its tree; one that left it (re-parented to
  launchd, as Chrome for Testing helpers do) is not stopped.
* cargo builds into `~/.cache/aib-lease/<seat>/target` on that Mac (`AIB_LEASE_TARGET_ROOT`),
  so a short step is not a cold build every time.
* **ttl (D6):** no `--ttl` ⇒ p90 × 1.5 of earlier runs of the same steps on the same host from
  the history (no history: 900 s), at most 1800 s. `--ttl 3600` or more needs `--long REASON`.
* **History (D5):** one JSON line per run (granted or not) in
  `~/.ez2-aib/state/lease/history.jsonl` on the machine that ran `aib-lease` (`AIB_LEASE_HISTORY`):
  host, seat, rev / tree, start / end, secs, rc, outcome, ttl and its source, and per step: the
  command with secret-looking values masked, start / end, secs, rc, user / sys CPU s and max RSS
  (`/usr/bin/time -l`), highest CPU % of its process tree (sampled once a second). Stat lines
  carry a per-run nonce, so a step's own output cannot pass for them. A failed write warns and
  never changes the exit code. The file is created 0600 (masking is best effort).
* Exit: 0 every step passed · 1 a step failed · 2 usage · 3 ssh / connection · 4 `git archive` /
  bad rev · 73 remote unpack · 75 lease not granted (nothing ran) · 76 connection dropped, run still going there (A1).

## `aib-lease classify` — short or long, light or not (D7)

```sh
aib-lease classify --host 100.64.0.31 --step 'cargo test -p aibd --locked --test gate_replay'
# {"class":"short","light":true,"may_go_ahead":true,"est_secs":70,"samples":3,...}
```

DECISION-test-classify v1.1 items 1–4, from the run history (`aib-lease run`) of the same
steps on the same host. It only orders work: a short run still takes the lease and runs the same
commands through the same gates.

* Fingerprint: host + the steps as words; commit ids, `--rev` values and temp paths taken out.
  Shell syntax (`;`, `|`, `&&`, `$`, redirections, globs) ⇒ no fingerprint ⇒ unknown.
* Estimate from passing runs only (sum of step seconds, lease waiting not in it): 5 or more ⇒
  p90 (nearest rank), fewer ⇒ max. Short ≤ 10 min, medium ≤ 20 min, long beyond. No history ⇒
  unknown (scheduled as long), first estimate from the commands.
* Always long: `aib test --all`, `--workspace`, `tools/test.js`, Chrome / CDP / e2e, a real AI
  CLI, `--ignored` / `--include-ignored`.
* Light = measured: every passing run peaked at ≤ 4 cores and ≤ 1 GiB; a missing number ⇒ not light.
* `may_go_ahead` = short + light + not always-long + no run over 15 min (or `short_overrun`)
  in the last 7 days.
* `~/.ez2-aib/state/lease/classify-long.txt` (`AIB_LEASE_CLASSIFY_LONG`): one entry per line;
  a fingerprint containing it is long. Nothing in any file makes a run short.

## `aib-lease insert` — one short, light run next to a shared long holder (D4, off by default)

```sh
aib-lease insert acquire --owner 043dd --what "cargo clippy -p x" --run-id R --pid $$ --hard-timeout-s 600 \
  --reason "receipt before tonight" --est-min 8
aib-lease insert release --run-id R
```

DECISION-d4-insert-043dd-1110 (on DECISION-test-classify v1.1 item 5). The slot
`<lease>.insert.json` is read and written only under the lease's own lock, so the checks and
taking the slot are one critical section:

* the machine opted in (`<lease>.insert-enabled`; until every lease entry on it is this crate,
  the 1.x `aib lease acquire` and test_entry's small batches cannot see the slot) ⇒ else
  `insert_disabled`;
* a lease is held by a live holder that is **not** exclusive ⇒ else `no_long_holder` (take the
  lease itself) / `holder_exclusive`;
* nobody in the queue has waited 30 min ⇒ else `long_waiter`;
* the slot is free (its pid dead; a live pid keeps it even past its hard timeout) ⇒ else
  `insert_taken`;
* fewer than 2 inserts have gone ahead of this long holder (`main_run_id`, counted in
  `<lease>.insert-tally.json` under the same lock; a new holder starts at 0; the same run taking
  its own live slot again does not count) ⇒ else `insert_cap` (rc 4).

Limits (a63ab, Andy 11-10): `--reason` is required (`reason_required`, rc 2) and goes into the
record and the ledger; `--est-min` is required and 1..=15 (`est_min_required` / `est_too_long`,
rc 2: a longer run queues).

An exclusive `acquire` waits for a running insert (`insert_running`). Hard timeout 300–900 s
(max(p90 × 2, 5 min), at most 15 min). An unreadable record fails closed. rc 0 / 4 / 3 / 2 as
for `acquire`.
