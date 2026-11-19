#!/usr/bin/env bash
# TP1–TP5 (b4297 11-20 第二輪):一席自己那一份給真人看(apps/web/dist/tree.js + ws.js 兩個入口)。
# 每把弄壞一處,the_page_shows_a_seats_tree_read_only(跑 apps/web/test/tree.test.cjs)要紅。
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
t() { python3 -I scripts/knife.py "$1" "$2" "$3" \
  --expect-fail the_page_shows_a_seats_tree_read_only \
  -- cargo test -p aibd --locked --test page_tree the_page_shows_a_seats_tree_read_only; }
# TP1 a line the seat wrote goes into the page unescaped
t apps/web/dist/tree.js "      return c ? '<span class=\"' + c + '\">' + esc(l) + '</span>' : esc(l);" \
  "      return c ? '<span class=\"' + c + '\">' + l + '</span>' : esc(l); // knife"
# TP2 the seat id goes into the URL as it is
t apps/web/dist/tree.js "  const treeUrl = (seat) => '/api/v1/seats/' + enc(seat) + '/tree';" \
  "  const treeUrl = (seat) => '/api/v1/seats/' + seat + '/tree'; // knife"
# TP3 the click is taken after the card's own handler (both act)
t apps/web/dist/tree.js "  }, true);" \
  "  }, false); // knife"
# TP4 the kept-tree card has no button
t apps/web/dist/ws.js "    if (x.tree) b.push(" \
  "    if (false && x.tree) b.push( // knife"
# TP5 the page does not load tree.js
t apps/web/dist/index.html '<script src="tree.js" defer></script>' \
  '<!-- knife -->'
