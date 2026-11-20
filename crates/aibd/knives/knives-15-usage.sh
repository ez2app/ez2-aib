#!/usr/bin/env bash
# N-workspace usage knives UG1-UG14, CX1-CX5, NU1-NU5, SU1-SU9 (7ebca 11-18, ORDER-workspace-519d0-1118 第二波): a seat's
# context read from its own output by the 1.x seat_fork.context_usage rule, the 85 / 95 lines as
# slot-3 cards, a seat waiting or silent past the cutoff as a slot-4 card; CX: a codex seat's thread/tokenUsage/updated read by
# the 1.x codex statusline rule (第三波 3); NU: GET nodes/{id}/usage from the board's
# usage-cache.json (第四波, SPEC §15). Each rule, broken,
# turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
uu() { python3 -I scripts/knife.py crates/aibd/src/usage.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
us() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
uo() { python3 -I scripts/knife.py crates/aibd/src/session_output.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test usage "$3"; }
ub() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test usage "$3"; }
un() { python3 -I scripts/knife.py crates/aibd/src/usage.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test node_usage "$3"; }
# UG1
uu 'if line.get("type").and_then(Value::as_str) != Some("assistant") {' \
   'if false {' \
   usage::tests::
# UG2
uu '.fold(0u64, u64::saturating_add);' \
   '.fold(0u64, u64::max);' \
   usage::tests::
# UG3
uu '(total > 0).then_some(total)' \
   'Some(total)' \
   usage::tests::
# UG4
uu '    if used > SMALL_WINDOW {' \
   '    if used >= SMALL_WINDOW {' \
   usage::tests::
# UG5
uu 'used.saturating_mul(100) / window' \
   'used.saturating_mul(100).div_ceil(window)' \
   usage::tests::
# UG6
uu '    row.last_output_at = Some(at.to_owned());' \
   '    let _ = at;' \
   usage::tests::
# UG7
uu '.min_by_key(|(_, u)| u.order)' \
   '.filter(|_| false).min_by_key(|(_, u)| u.order)' \
   usage::tests::
# UG8
us 'if p < crate::usage::HANDOFF_PCT {' \
   'if p < 90 {' \
   slots::tests::
# UG9
us 'let line = if p >= crate::usage::RED_PCT {' \
   'let line = if p > 100 {' \
   slots::tests::
# UG10
us '"reply" if n.updated_at.as_str() < cutoff => (' \
   '"reply" => (' \
   slots::tests::
# UG11
us '.filter(|last| *last < cutoff)' \
   '.filter(|_| true)' \
   slots::tests::
# UG12
us '.chain(live.iter().cloned())' \
   '.chain(live.iter().cloned().filter(|_| false))' \
   slots::tests::
# UG13
uo 'crate::usage::board().observe(&self.session, without_eol(raw), &now_rfc3339());' \
   'let _ = raw;' \
   a_seat_near_its_context_limit
# UG14
ub 'crate::usage::board().bind(&id, rec.seat_id.as_deref());' \
   'let _ = &id;' \
   a_seat_near_its_context_limit
# CX1
uu 'let raw_used = t.get("last")?.get("totalTokens")?.as_u64()?;' \
   'let raw_used = t.get("total")?.get("totalTokens")?.as_u64()?;' \
   codex_reads_its_last_turn
# CX2
uu '.div_ceil(2)' \
   '.max(0)' \
   codex_reads_its_last_turn
# CX3
uu 'let raw_window = t.get("modelContextWindow")?.as_u64()?;' \
   'let raw_window = t.get("modelContextWindow").and_then(Value::as_u64).unwrap_or(SMALL_WINDOW);' \
   codex_reads_its_last_turn
# CX4
uu '.filter(|r| r.get("thread").is_some())' \
   '.filter(|_| false)' \
   a_codex_row_takes_the_model
# CX5
uu '.or_else(|| codex_context_of(&v));' \
   '.or_else(|| None::<(u64, u64, u8)>);' \
   a_codex_row_takes_the_model
# NU1
uu 'if !want.contains(id) {' \
   'if !want.contains(id) && false {' \
   a_node_s_usage_is_the_board_s_rows
# NU2
uu 'if cache.get("v").and_then(Value::as_u64) != Some(2) {' \
   'if false {' \
   a_node_s_usage_is_the_board_s_rows
# NU3
uu '.entry(hb.saturating_mul(HOUR_S))' \
   '.entry(hb)' \
   a_node_s_usage_is_the_board_s_rows
# NU4
uu 'out.sort_by_key(|(spent, _)| std::cmp::Reverse(*spent));' \
   'out.sort_by_key(|(spent, _)| *spent);' \
   a_node_s_usage_is_the_board_s_rows
# NU5
un 'let Some(path) = crate::workspace::data_file(NODE_USAGE_FILE) else {' \
   'let Some(path) = crate::workspace::data_file("usage.json") else {' \
   a_node_s_tokens_per_seat_per_hour
# SU1 a subtree keeps the largest, not the sum
uu '*s = s.saturating_add(q.get(i).map_or(0, round));' \
   '*s = (*s).max(q.get(i).map_or(0, round));' \
   a_subtree_sums_every_seat
# SU2 grandchildren left out
uu 'next = deeper;' \
   'next = Vec::new();' \
   descendants_walk_the_whole_subtree
# SU3 spent leaves out new input
uu '"spent": sum[0].saturating_add(sum[1]),' \
   '"spent": sum[0],' \
   a_subtree_sums_every_seat
# SU4 first and last swapped
un '"first_at": t[5], "last_at": t[6]' \
   '"first_at": t[6], "last_at": t[5]' \
   a_node_s_tokens_per_seat
# SU5 items not ranked by spend (Andy 11-19 00:3x via 519d0: subtree curve + items)
uu 'rows.sort_by_key(|v| std::cmp::Reverse(v["spent"].as_u64().unwrap_or(0)));' \
   'rows.sort_by_key(|v| v["spent"].as_u64().unwrap_or(0));' \
   a_subtree_carries_the_curve_and_its_items_ranked
# SU6 items not capped at 12 (Andy 11-19 00:3x via 519d0: subtree curve + items)
uu 'rows.truncate(TOP_CHILDREN);' \
   'rows.truncate(TOP_CHILDREN * 2);' \
   a_subtree_carries_the_curve_and_its_items_ranked
# SU7 the same hour of two items not summed (Andy 11-19 00:3x via 519d0: subtree curve + items)
uu '*x = x.saturating_add(r.get(i).map_or(0, round));' \
   '*x = r.get(i).map_or(0, round);' \
   a_subtree_carries_the_curve_and_its_items_ranked
# SU8 first is not the earliest (Andy 11-19 00:3x via 519d0: subtree curve + items)
uu 'if n(5) > 0 && (t[5] == 0 || n(5) < t[5]) {' \
   'if n(5) > 0 {' \
   a_subtree_carries_the_curve_and_its_items_ranked
# SU9 items with no use listed (Andy 11-19 00:3x via 519d0: subtree curve + items)
uu '.filter(|v| v["nodes"].as_u64().unwrap_or(0) > 0)' \
   '.filter(|v| v["nodes"].as_u64().is_some())' \
   a_subtree_carries_the_curve_and_its_items_ranked
