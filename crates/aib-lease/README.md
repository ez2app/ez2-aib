# aib-lease

The per-machine run lease (`aib lease`, 1.x `aib/tools/governance/host_lease.py`) for a holder
that runs on **another** machine. Team tool crate (RULES §9): it reads and writes the 1.x files
under `~/.ez2-aib/state/`; `aibd` must not depend on it.

## Why (b127)

1.x records the holder's pid with `host = <this machine>` and checks it here with `kill -0` / `ps`.
A script on the Mac that takes the andymini lease over ssh gives a Mac pid. On andymini that pid
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

## Remote holder (Mac → andymini)

```sh
J="-J eu02-ts andymini"; RID=k10k-2240e; H=$(uname -n); S=60
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

Tests: `cargo test -p aib-lease` (not on the Mac Pro 16: heat order; andymini or CI).

## `aib-lease run` — steps on another Mac under its lease (D3 + D5 + D6)

```sh
aib-lease run --host 100.116.110.31 --owner 043dd \
  --step 'cargo clippy -p aibd --all-targets --locked -- -D warnings' \
  --step 'cargo test -p aibd --locked --test sessions'
# or: aib-lease run --host H --owner S --rev <commit|tree> -- cargo test -p aibd
```

* Sends `git archive <rev>` (default HEAD; uncommitted changes are not sent, it warns) on ssh's
  stdin; the remote script goes as a base64 argument. ssh always has `ForwardAgent=no`,
  `ClearAllForwardings=yes`, `BatchMode=yes`, and `ServerAliveInterval=15` /
  `ServerAliveCountMax=4`: a network that vanishes without a FIN / RST ends the local ssh after
  about a minute (rc 3). The remote side stops when its next write fails (the beat line every
  5 s); on a silent loss that is when the remote TCP gives up, so the lease may stay a few
  minutes longer, still tied to that shell.
* On the other Mac, one bash: unpack → `aib lease acquire … --pid $$` → **nothing runs unless
  rc 0** → `EXIT` trap releases the lease, stops a running step's process tree and removes the
  tree → each step as its own `bash -c`, every rc printed; a failing step does not stop the rest.
* The lease is `--exclusive` (the run is being measured, small `aib test` batches wait);
  `--shared` asks without it.
* A dropped ssh sends no HUP (no tty): the remote side writes a beat line every 5 s, and the
  failed write stops the step, releases the lease and cleans up. Stopping a step reaches only
  processes still in its tree; one that left it (re-parented to launchd, as Chrome for Testing
  helpers do) is not stopped.
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
  bad rev · 73 remote unpack · 75 lease not granted (nothing ran).

## `aib-lease classify` — short or long, light or not (D7)

```sh
aib-lease classify --host 100.116.110.31 --step 'cargo test -p aibd --locked --test gate_replay'
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
