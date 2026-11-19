#!/usr/bin/env bash
# The one knife step's own knives (S66-loop, DESIGN-knives-loop-ebb36-1117 r0 "刀(本張自己的)"; 7b81e 11-17):
# each knife takes one rule of crates/aib-knife/run-knives.sh away and must turn its own test in
# xtask/src/run_knives.rs red. Run by the "crate knives" step like every crates/*/knives/*.sh.
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
rk() { python3 -I scripts/knife.py crates/aib-knife/run-knives.sh "$1" "$2" \
         --expect-fail "$3" -- cargo test -p aib-xtask --locked "$3"; }
# RK1 a script for another OS runs anyway
rk '    *) [ "$want_os" = "$here" ] && ok=1 || ok=0 ;;' '    *) ok=1 ;; # knife: every OS is this one' \
  the_scripts_of_the_job_run_and_the_other_os_is_skipped
# RK2 a script of another job runs in this one (it would run twice: here and in its own job)
rk '  [ "$want_job" = "$job" ] || continue' '  [ "$want_job" = "$job" ] || true # knife: any job' \
  the_scripts_of_the_job_run_and_the_other_os_is_skipped
# RK3 the watchdog never fires: a script over its limit runs to its end and reads as green
rk '    if [ $((SECONDS - start)) -ge $((limit * per_min)) ]; then' '    if false; then # knife: no limit' \
  a_script_over_its_limit_is_killed_with_its_children_and_red
# RK4 no process group of its own: the kill reaches the script but not the children it left
rk '  set -m' '  : # knife: no process group' \
  a_script_over_its_limit_is_killed_with_its_children_and_red
# RK5 one red script stops the rest
rk '    [ "$rc" = 0 ] || bad+=("$f")' '    [ "$rc" = 0 ] || { bad+=("$f"); break; } # knife: stop at the first red' \
  one_red_script_does_not_stop_the_others_and_the_run_is_red
# RK6 a bad header is printed but the run stays green
rk $'    echo "KNIVES-HEADER-BAD $f: $broken"\n    bad+=("$f")' '    echo "KNIVES-HEADER-BAD $f: $broken" # knife: not red' \
  a_bad_header_and_a_job_with_no_script_are_red
# RK7 a job with no script at all reads as clean (a broken glob would)
rk 'if [ "$mine" = 0 ]; then' 'if false; then # knife' \
  a_bad_header_and_a_job_with_no_script_are_red
# RK8 an env header leaks into every later script
rk '  envs=()' '  : # knife: envs never reset' \
  an_env_header_reaches_only_its_own_script
# S88-split shards (f1fae 11-20; Andy 11-20 00:2x "刀步拆平行那條直接做")
# RK9 every shard runs every script (N cells would run each script N times)
rk '  if [ $(((here_ok - 1) % shard_n + 1)) != "$shard_k" ]; then' '  if false; then # knife: no shard' \
  shards_take_turns_over_the_scripts_that_run_here_and_together_run_each_once
# RK10 turns are counted over the job's scripts of every OS: a script that cannot run here takes a turn
rk '  here_ok=$((here_ok + 1))' '  here_ok=$mine # knife: other OS takes a turn' \
  shards_take_turns_over_the_scripts_that_run_here_and_together_run_each_once
# RK11 a shard that ran nothing reads as clean (more cells than scripts would hide a lost script)
rk 'if [ "$shard_n" != 1 ] && [ "$ran" = 0 ]; then' 'if false; then # knife' \
  a_shard_that_gets_no_script_is_red_and_a_shard_not_k_of_n_is_refused
# RK12 a shard that is not k/N is read as "all"
rk '  exit 2' '  shard_k=1 shard_n=1 # knife: anything is all' \
  a_shard_that_gets_no_script_is_red_and_a_shard_not_k_of_n_is_refused
# RK13 k above N is taken (4/3 runs nothing instead of being refused)
rk ' && [ "${BASH_REMATCH[1]}" -le "${BASH_REMATCH[2]}" ]; then' '; then # knife: no k <= N' \
  a_shard_that_gets_no_script_is_red_and_a_shard_not_k_of_n_is_refused
# RK14 the shard reaches the scripts it runs
rk '  unset AIB_KNIVES_SHARD' '  : # knife: kept' \
  the_scripts_do_not_see_the_shard
