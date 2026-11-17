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
