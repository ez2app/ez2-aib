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
J="-J eu02-ts andymini"; RID=k10k-2240e
ssh $J aib-lease queue add --owner 2240e --what k10k --run-id $RID --host "$(uname -n)" --pid $$ --heartbeat-s 60
( while kill -0 $$ 2>/dev/null; do ssh $J aib-lease heartbeat --run-id $RID >/dev/null || break; sleep 60; done ) &
until ssh $J aib-lease acquire --owner 2240e --what k10k --run-id $RID --host "$(uname -n)" --pid $$ --heartbeat-s 60 --exclusive; do sleep 30; done
# … run …
ssh $J aib lease release --run-id $RID
```

Tests: `cargo test -p aib-lease` (not on the Mac Pro 16: heat order; andymini or CI).
