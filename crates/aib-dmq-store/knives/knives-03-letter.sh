#!/usr/bin/env bash
# S79-rust slice 1 (5d33a REVIEW-driverust1 L1): the shared 1.x letter writer, each rule loosened
# turns its own test red. Run from the repo root.
set -euo pipefail
lt() { python3 -I scripts/knife.py crates/aib-dmq-store/src/letter.rs "$1" "$2" --expect-fail "letter::tests::$3" \
        -- cargo test -p aib-dmq-store --locked --lib "letter::tests::$3"; }
# LK1 the letter never reaches order.json (the drainer would not see it)
lt '        .order_append(seat, id)' '        .order_ids(if id.is_empty() { id } else { seat })' letters_written_together_all_reach_the_order
# LK2 a relay file may be written over an existing one
lt '    let mut f = crate::fsx::create_private(&path, true)?;' '    let mut f = crate::fsx::create_private(&path, false)?;' a_long_text_is_relayed_and_a_short_one_is_not
# LK3 a field of the letter changes (aibd's frozen copy of the letter no longer matches)
python3 -I scripts/knife.py crates/aib-dmq-store/src/letter.rs '        ("status", json!("queued")),' '        ("status", json!("delivered")),' \
  --expect-fail the_shared_writer_writes_the_letter_byte_for_byte \
  -- cargo test -p aibd --locked --lib legacy_send::tests::the_shared_writer_writes_the_letter_byte_for_byte
