#!/usr/bin/env bash
# aib-knives: os=unix
# PC1–PC3 (7b81e 11-19; 519d0 12:09): in a preview, a carried pairing is sent back to the parent's 打開 (no code,
# no finger), not told to pair again. Each, broken, turns in_a_preview_a_carried_pairing_is_sent_back_to_open red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail in_a_preview_a_carried_pairing_is_sent_back_to_open \
  -- cargo test -p aibd --locked --test page_controls in_a_preview_a_carried_pairing_is_sent_back_to_open; }
# PC1 read-only carried pairing in a preview: told to pair again
k "    if (e.code === 'carried_pair_read_and_safe_only') return inPreview() ? PV_CARRIED : '" "    if (e.code === 'carried_pair_read_and_safe_only') return '"
# PC2 safe-seats-only carried pairing in a preview: told to pair again
k "    if (e.code === 'carried_pair_safe_seats_only') return inPreview() ? PV_CARRIED : '" "    if (e.code === 'carried_pair_safe_seats_only') return '"
# PC3 the preview message sends the person to pair again
k "這個預覽剛換過版,這一頁的配對只能看;回母體那一頁再按一次「打開」就好" "這個預覽剛換過版,這一頁的配對只能看;請重新配對"
