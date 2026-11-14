#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# S66-0d (Andy 11-14, s36): does a pull request touch what a slow cell tests? A cell whose
# crate and everything it is built from are untouched need not run on a pull request.
#
#   closure-changed.sh --event <github event> --base <sha> --head <sha> --crate <package>
#                      [--metadata <cargo metadata json>] [--changed <file of changed paths>]
#
# Prints two lines: `changed=true|false` and `why=<one line>`. rc 0 whenever it printed them.
# Only `changed=false` lets a caller skip; every doubt prints `changed=true` (fail open to
# running, never to skipping):
# - an event other than pull_request (a main push always runs everything, Andy 11-14);
# - no base / head, cargo metadata or git diff unreadable, the crate not in the workspace;
# - a changed path inside the closure: the crate's own folder and the folders of every
#   workspace crate it depends on through path dependencies (normal and build, transitively;
#   the crate's own dev-dependencies too, since its tests build them);
# - a changed path in the fixed set: Cargo.toml, Cargo.lock, rust-toolchain.toml, .cargo/,
#   .github/workflows/aibd.yml, scripts/ (knife.py, the knife scripts, this script), xtask/.
#
# Not counted: aib-cloud-host's build.rs watches all of crates/, but only to stamp the commit
# and the dirty bit into the binary (AIB_CLOUD_HOST_COMMIT / AIB_CLOUD_HOST_DIRTY); that
# does not change what a knife finds. closure-changed-test.sh holds build.rs to exactly those
# two outputs, so a build.rs that starts reading more makes the test red first.
#
# --metadata / --changed replace `cargo metadata --no-deps` and `git diff --name-only
# <base>...<head>` (tests). Needs bash, jq, and without those two: cargo and git.
set -u -o pipefail

FIXED=(Cargo.toml Cargo.lock rust-toolchain.toml .cargo/ .github/workflows/aibd.yml scripts/ xtask/)

out() { # out <true|false> <why>
  printf 'changed=%s\nwhy=%s\n' "$1" "$2"
  exit 0
}

event="" base="" head="" crate="" meta_file="" changed_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --event) event=${2:-}; shift 2 ;;
    --base) base=${2:-}; shift 2 ;;
    --head) head=${2:-}; shift 2 ;;
    --crate) crate=${2:-}; shift 2 ;;
    --metadata) meta_file=${2:-}; shift 2 ;;
    --changed) changed_file=${2:-}; shift 2 ;;
    *) out true "unknown argument $1" ;;
  esac
done

[ "$event" = pull_request ] || out true "event ${event:-none} is not a pull request: run"
[ -n "$base" ] && [ -n "$head" ] || out true "no base / head: run"
[ -n "$crate" ] || out true "no crate named: run"

if [ -n "$meta_file" ]; then
  meta=$(cat "$meta_file" 2>/dev/null) || out true "metadata file unreadable: run"
else
  meta=$(cargo metadata --no-deps --format-version 1 --offline 2>/dev/null) || out true "cargo metadata failed: run"
fi

# the closure: repo-relative folders, one per line, each ending in /
dirs=$(jq -r --arg c "$crate" '
  .workspace_root as $root
  | (.packages | map({key: .name, value: .}) | from_entries) as $p
  | def deps($n): [$p[$n].dependencies[]
        | select(.path != null)
        | select(.kind != "dev" or $n == $c)
        | .name];
    def bfs($todo; $seen):
      if ($todo | length) == 0 then $seen
      elif ($seen | index([$todo[0]])) then bfs($todo[1:]; $seen)
      elif $p[$todo[0]] == null then error("not a workspace crate: \($todo[0])")
      else bfs($todo[1:] + deps($todo[0]); $seen + [$todo[0]])
      end;
  bfs([$c]; [])[]
  | $p[.].manifest_path
  | ltrimstr($root + "/")
  | sub("Cargo\\.toml$"; "")' <<<"$meta" 2>/dev/null) || out true "crate $crate not read from the workspace: run"
[ -n "$dirs" ] || out true "empty closure: run"
while IFS= read -r d; do
  # a crate at the workspace root would make the closure the whole repo
  case "$d" in "" | /*) out true "closure folder '$d' is not a crate folder: run" ;; esac
done <<<"$dirs"

if [ -n "$changed_file" ]; then
  files=$(cat "$changed_file" 2>/dev/null) || out true "changed-file list unreadable: run"
else
  files=$(git diff --name-only "$base...$head" 2>/dev/null) || out true "git diff $base...$head failed: run"
fi

while IFS= read -r f; do
  [ -n "$f" ] || continue
  for x in "${FIXED[@]}"; do
    case "$x" in
      */) case "$f" in "$x"*) out true "$f is in the fixed set ($x): run" ;; esac ;;
      *) [ "$f" = "$x" ] && out true "$f is in the fixed set: run" ;;
    esac
  done
  while IFS= read -r d; do
    case "$f" in "$d"*) out true "$f is in the closure of $crate ($d): run" ;; esac
  done <<<"$dirs"
done <<<"$files"

out false "no change in the closure of $crate ($(tr '\n' ' ' <<<"$dirs" | sed 's/ $//')) or the fixed set since ${base:0:12}"
