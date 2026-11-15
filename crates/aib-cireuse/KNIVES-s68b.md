---
status: 定稿
rev: r0
node: S68b
owner: b433b
---
# S68b knives (`aib-cireuse reasons`: why cells could not reuse, by reason prefix)

`cargo test -p aib-cireuse --test reasons <test>`. RS1–RS6 in `crates/aib-cireuse/src/lib.rs`, RS7–RS8 in `crates/aib-cireuse/src/main.rs`. Every anchor occurs once in its file; these are the exact strings the receipt ran. For the integration seat to put into aibd.yml (RULES §21).

| knife | what goes wrong | old | new | test |
|---|---|---|---|---|
| RS1 | cells of an older, superseded judgement of the same push are counted | `want.get(&s(&v, "head_sha")) != Some(&s(&v, "at"))` | `!want.contains_key(&s(&v, "head_sha"))` | reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts |
| RS2 | cells that could reuse are counted as not | `if s(v, "verdict") != "cannot_reuse" {` | `if s(v, "verdict") == "never" {` | reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts |
| RS3 | a cannot-reuse cell with no reason is not shown apart | `if prefixes.is_empty() {` | `if prefixes.is_empty() && false {` | a_cannot_reuse_cell_without_a_reason_is_counted_apart |
| RS4 | a push only ever `wait` / `unknown` is counted as judged (also week) | `&& matches!(s(v, "verdict").as_str(), "could_reuse" \| "cannot_reuse")` | `&& !s(v, "verdict").is_empty()` | reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts |
| RS5 | the whole reason text is the key, so one cause splits into many | `r.split(':').next().unwrap_or("").trim()` | `r.trim()` | reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts |
| RS6 | the most common reason is not first | `.sort_by(\|a, b\| b.cells.cmp(&a.cells).then_with(\|\| a.prefix.cmp(&b.prefix)));` | `.sort_by(\|a, b\| a.cells.cmp(&b.cells).then_with(\|\| a.prefix.cmp(&b.prefix)));` | reasons_count_prefixes_once_per_cell_over_the_judgement_week_counts |
| RS7 | the command reads a window that is not the last `--days` | `reasons_summary(&ledger_lines(dir), now(), a.days)` | `reasons_summary(&ledger_lines(dir), now() - 30 * 86_400, a.days)` | the_reasons_command_reads_the_ledger_folder |
| RS8 | pull does not take a knife cell's ledger (S66-4, 7b81e 11:1x) | `"cireuse-ledger-knives-rust",` | `// "cireuse-ledger-knives-rust",` | pull_lists_the_main_ledger_and_the_three_knife_cell_ledgers |
