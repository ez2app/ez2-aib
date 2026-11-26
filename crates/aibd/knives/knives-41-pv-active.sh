#!/usr/bin/env bash
# aib-knives: os=unix
# PA1 (7b81e 11-20; c7): a preview page sends no preview_active (its own aibd always refused it,
# preview_in_preview 403, once a minute while a person used the page). Broken, it turns
# a_preview_page_sends_no_preview_active_its_own_aibd_refuses red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
k() { python3 -I scripts/knife.py apps/web/dist/ws.js "$1" "$2" \
  --expect-fail a_preview_page_sends_no_preview_active_its_own_aibd_refuses \
  -- cargo test -p aibd --locked --test page_controls a_preview_page_sends_no_preview_active_its_own_aibd_refuses; }
# PA1 the page sends preview_active again
k "  // c7(7b81e 11-20):預覽頁不送 preview_active。以前這裡在預覽裡每分鐘(有操作就)送一次,預覽自己的 aibd 一律拒" \
  "  if (inPreview()) act('preview_active', { preview_id: S.pvId }).catch(() => {}); // knife"
