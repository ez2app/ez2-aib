# KNIVES n-4a wake (7b81e) — for the integration seat to put into aibd.yml (RULES §21 §25)

Each knife takes one rule out of `crates/aib-drive/src/wake.rs`; its test must turn red.
Every anchor occurs exactly once in wake.rs. Runner (macOS, same shape as the other steps):

```bash
wk() { python3 -I scripts/knife.py crates/aib-drive/src/wake.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test wake "$3"; }
```

| # | what breaks | old | new | test (red) |
|---|---|---|---|---|
| WK1 | the temp file is never renamed into place | `std::fs::rename(&tmp, &target)` | `std::fs::rename(&tmp, &tmp)` | an_event_is_one_json_line_put_in_place_by_rename |
| WK2 | a missing drive folder is created | `if !drive.is_dir() {` | `if !drive.is_dir() && std::fs::create_dir_all(drive).is_err() {` | no_drive_folder_means_nothing_written_and_nothing_made |
| WK3 | a failed write leaves its temp file | `let _ = std::fs::remove_file(&tmp);` | `let _ = &tmp;` | a_write_that_fails_is_exit_5_and_leaves_no_temp_file |
| WK4 | seat_free takes any ref | `"seat_free" => seat_code(r),` | `"seat_free" => seat_code(r) || true,` | each_kind_takes_only_its_own_ref |
| WK5 | a tick carries a ref | `"tick" => false,` | `"tick" => true,` | each_kind_takes_only_its_own_ref |
| WK6 | AIB_DRIVE_BY is ignored | `.or(by_env.filter(\|b\| !b.is_empty()))` | `.or(by_env.filter(\|b\| b.is_empty()))` | by_names_the_writer_and_a_consumer_skips_its_own |
| WK7 | observe loses the report id of an event line | `Some(ev) => ev.reference.filter(\|r\| is_event(r)),` | `Some(_) => None,` | observe_reads_the_report_id_of_new_and_old_files |
| WK8 | merge takes another kind's ref | `"merge" => r.starts_with("pr/") && is_event(r),` | `"merge" => is_event(r),` | each_kind_takes_only_its_own_ref |
| WK9 | a consumer wakes on its own event | `ev.by == me` | `ev.by != me` | by_names_the_writer_and_a_consumer_skips_its_own |

As step lines (paste under one `- name: aib-drive wake knives (macOS)` step, `set -eu` first):

```bash
wk 'std::fs::rename(&tmp, &target)' 'std::fs::rename(&tmp, &tmp)' an_event_is_one_json_line_put_in_place_by_rename
wk 'if !drive.is_dir() {' 'if !drive.is_dir() && std::fs::create_dir_all(drive).is_err() {' no_drive_folder_means_nothing_written_and_nothing_made
wk 'let _ = std::fs::remove_file(&tmp);' 'let _ = &tmp;' a_write_that_fails_is_exit_5_and_leaves_no_temp_file
wk '"seat_free" => seat_code(r),' '"seat_free" => seat_code(r) || true,' each_kind_takes_only_its_own_ref
wk '"tick" => false,' '"tick" => true,' each_kind_takes_only_its_own_ref
wk '.or(by_env.filter(|b| !b.is_empty()))' '.or(by_env.filter(|b| b.is_empty()))' by_names_the_writer_and_a_consumer_skips_its_own
wk 'Some(ev) => ev.reference.filter(|r| is_event(r)),' 'Some(_) => None,' observe_reads_the_report_id_of_new_and_old_files
wk '"merge" => r.starts_with("pr/") && is_event(r),' '"merge" => is_event(r),' each_kind_takes_only_its_own_ref
wk 'ev.by == me' 'ev.by != me' by_names_the_writer_and_a_consumer_skips_its_own
```

Integration also: main.rs routes `aib-drive event …` to `wake::cli_event`, lib.rs `pub mod wake;`
with `use aib_drive::report::is_event` → `use crate::report::is_event`, tests/wake.rs drops its
`#[path]` and uses `aib_drive::wake`, src/bin/aib-drive-wake.rs is removed, and
observe::read_event calls `wake::report_event_of` (today a JSON line is a plain wake-up there).

Note (receipt 064002af): a replacement that leaves a function unused (WK4 first wrote `"seat_free" => true,`,
so `seat_code` became dead code) fails to compile under `-D warnings` and the knife "misses" with no
failed test. Every replacement above keeps each function in use.

> 11-15 RULES §21(新版):aib-drive 的刀已從 aibd.yml 搬到 `crates/aib-drive/knives/knives-NN-<step>.sh`(一個舊 step 一支),aibd.yml 只留一行「aib-drive knives (macOS)」照檔名順序跑全部。新刀加在對應那支腳本，不再加進 aibd.yml。
