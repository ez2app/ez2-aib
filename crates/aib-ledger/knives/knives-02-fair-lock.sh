#!/usr/bin/env bash
# aib-ledger lock queued with the system (08de5 11-25; main f181649bdf Windows job 139025856402, ebab8 q_f8177dde): a waiter is handed the lock when the holder lets go, and a wait given up keeps nothing. Each rule broken turns its test red. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# fl-kept (a wait given up keeps the lock its thread takes later)
python3 -I scripts/knife.py crates/aib-ledger/src/lib.rs \
  '            let _ = tx.send(f.lock().map(|()| f));' \
  '            if let Err(e) = tx.send(f.lock().map(|()| f)) { std::mem::forget(e); }' \
  --expect-fail 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing' \
  -- cargo test -p aib-ledger --locked --lib 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing'
# fl-unlocked (the queued thread hands back the file without the lock)
python3 -I scripts/knife.py crates/aib-ledger/src/lib.rs \
  '            let _ = tx.send(f.lock().map(|()| f));' \
  '            let _ = tx.send(Ok::<File, std::io::Error>(f));' \
  --expect-fail 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing' \
  -- cargo test -p aib-ledger --locked --lib 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing'
# fl-nowait (a lock not free at once is given up at once instead of queued)
python3 -I scripts/knife.py crates/aib-ledger/src/lib.rs \
  '    let got = rx.recv_timeout(wait);' \
  '    let got = rx.recv_timeout(Duration::ZERO);' \
  --expect-fail 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing' \
  -- cargo test -p aib-ledger --locked --lib 'tests::a_queued_waiter_gets_the_lock_and_a_wait_given_up_keeps_nothing'
