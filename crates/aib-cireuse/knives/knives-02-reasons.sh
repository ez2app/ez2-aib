#!/usr/bin/env bash
# The "aib-cireuse reasons knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/;IFACE-drive-shadow-519d0-1117 §5). The "aib-cireuse knives (macOS)" step
# (#823) runs every crates/aib-cireuse/knives/*.sh in name order; aib-xtask knife-anchors reads them.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S68b reasons report (b433b): each counting rule, broken, turns its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
rk() { python3 -I scripts/knife.py "crates/aib-cireuse/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-cireuse --locked --test reasons "$4"; }
# RS1 cells of an older, superseded judgement of the same push are counted
rk lib.rs 'want.get(&s(&v, "head_sha")) != Some(&s(&v, "at"))' '!want.contains_key(&s(&v, "head_sha"))' \
   reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts
# RS2 cells that could reuse are counted as not
rk lib.rs 'if s(v, "verdict") != "cannot_reuse" {' 'if s(v, "verdict") == "never" { // knife' \
   reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts
# RS3 a cannot-reuse cell with no reason is not shown apart
rk lib.rs 'if prefixes.is_empty() {' 'if prefixes.is_empty() && false { // knife' \
   a_cannot_reuse_cell_without_a_reason_is_counted_apart
# RS4 a push only ever wait / unknown is counted as judged (also week)
rk lib.rs '&& matches!(s(v, "verdict").as_str(), "could_reuse" | "cannot_reuse")' '&& !s(v, "verdict").is_empty()' \
   reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts
# RS5 the whole reason text is the key, so one cause splits into many
rk lib.rs "r.split(':').next().unwrap_or(\"\").trim()" 'r.trim()' \
   reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts
# RS6 the most common reason is not first
rk lib.rs '.sort_by(|a, b| b.cells.cmp(&a.cells).then_with(|| a.prefix.cmp(&b.prefix)));' \
   '.sort_by(|a, b| a.cells.cmp(&b.cells).then_with(|| a.prefix.cmp(&b.prefix)));' \
   reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts
# RS7 the command reads a window that is not the last --days
rk main.rs 'reasons_summary(&ledger_lines(dir), now(), a.days)' \
   'reasons_summary(&ledger_lines(dir), now() - 30 * 86_400, a.days)' \
   the_reasons_command_reads_the_ledger_folder
# RS8 pull does not take a knife cell's ledger (S66-4)
rk main.rs '"cireuse-ledger-knives-rust",' '// "cireuse-ledger-knives-rust",' \
   pull_lists_the_main_ledger_and_the_three_knife_cell_ledgers
