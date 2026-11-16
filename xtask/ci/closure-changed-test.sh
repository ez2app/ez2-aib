#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# S66-0d: tests for closure-changed.sh, on a fake workspace (no cargo, no git needed), plus
# the build.rs lock: aib-cloud-host's build.rs may only stamp COMMIT / DIRTY.
#   bash xtask/ci/closure-changed-test.sh        # rc 0 = all pass
set -u -o pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
S="$here/closure-changed.sh"
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
pass=0 fail=0
# one line per case in cargo's shape, so scripts/knife.py --expect-fail can name it
ok() { pass=$((pass + 1)); printf 'test %s ... ok\n' "$1"; }
bad() { fail=$((fail + 1)); printf 'test %s ... FAILED\n  %s\n' "$1" "$2"; }

# host -> cloud -> core (normal), host -build-> gen, host -dev-> fix, cloud -dev-> cfix,
# other stands alone; serde is a registry dependency (no path)
cat >"$t/meta.json" <<'J'
{"workspace_root":"/w","packages":[
 {"name":"host","manifest_path":"/w/crates/host/Cargo.toml","dependencies":[
   {"name":"cloud","path":"/w/crates/cloud","kind":null},
   {"name":"gen","path":"/w/crates/gen","kind":"build"},
   {"name":"fix","path":"/w/crates/fix","kind":"dev"},
   {"name":"serde","kind":null}]},
 {"name":"cloud","manifest_path":"/w/crates/cloud/Cargo.toml","dependencies":[
   {"name":"core","path":"/w/crates/core","kind":null},
   {"name":"cfix","path":"/w/crates/cfix","kind":"dev"}]},
 {"name":"core","manifest_path":"/w/crates/core/Cargo.toml","dependencies":[]},
 {"name":"gen","manifest_path":"/w/crates/gen/Cargo.toml","dependencies":[]},
 {"name":"fix","manifest_path":"/w/crates/fix/Cargo.toml","dependencies":[]},
 {"name":"cfix","manifest_path":"/w/crates/cfix/Cargo.toml","dependencies":[]},
 {"name":"other","manifest_path":"/w/crates/other/Cargo.toml","dependencies":[]},
 {"name":"stray","manifest_path":"/w/crates/stray/Cargo.toml","dependencies":[
   {"name":"outside","path":"/elsewhere/outside","kind":null}]},
 {"name":"aib-cloud-host","manifest_path":"/w/crates/aib-cloud-host/Cargo.toml","dependencies":[]},
 {"name":"rooted","manifest_path":"/w/Cargo.toml","dependencies":[]}
]}
J

# case <name> <want true|false> <event> <crate> <changed paths, one per line>
case_() {
  local name=$1 want=$2 event=$3 crate=$4 files=$5 got
  printf '%s' "$files" >"$t/changed"
  got=$(bash "$S" --event "$event" --base b0 --head h0 --crate "$crate" \
    --metadata "$t/meta.json" --changed "$t/changed" --root "${ROOT:-$t/empty}")
  if grep -qx "changed=$want" <<<"$got"; then ok "$name"; else
    bad "$name" "want changed=$want, got: $got"
  fi
}

case_ unrelated_crate_skips          false pull_request host 'crates/other/src/lib.rs'
case_ docs_only_skips                false pull_request host $'docs/a.md\nREADME.md'
case_ nothing_changed_skips          false pull_request host ''
case_ own_crate_runs                 true  pull_request host 'crates/host/src/main.rs'
case_ direct_dep_runs                true  pull_request host 'crates/cloud/src/lib.rs'
case_ transitive_dep_runs            true  pull_request host 'crates/core/src/lib.rs'
case_ build_dep_runs                 true  pull_request host 'crates/gen/src/lib.rs'
case_ own_dev_dep_runs               true  pull_request host 'crates/fix/src/lib.rs'
case_ deps_dev_dep_skips             false pull_request host 'crates/cfix/src/lib.rs'
case_ prefix_is_not_a_folder         false pull_request host 'crates/hostile/src/lib.rs'
case_ push_runs                      true  push         host 'crates/other/src/lib.rs'
case_ schedule_runs                  true  schedule     host 'crates/other/src/lib.rs'
case_ cargo_lock_runs                true  pull_request host 'Cargo.lock'
case_ cargo_toml_runs                true  pull_request host 'Cargo.toml'
case_ toolchain_runs                 true  pull_request host 'rust-toolchain.toml'
case_ cargo_config_runs              true  pull_request host '.cargo/config.toml'
case_ workflow_runs                  true  pull_request host '.github/workflows/aibd.yml'
case_ knife_script_runs              true  pull_request host 'scripts/knife.py'
case_ this_script_runs               true  pull_request host 'xtask/ci/closure-changed.sh'
case_ xtask_runs                     true  pull_request host 'xtask/src/knife_anchors.rs'
case_ one_hit_among_many_runs        true  pull_request host $'docs/a.md\ncrates/core/x.rs\nREADME.md'
case_ unknown_crate_runs             true  pull_request nope 'crates/other/src/lib.rs'
case_ dep_outside_workspace_runs     true  pull_request stray 'crates/other/src/lib.rs'
case_ crate_at_root_runs             true  pull_request rooted 'crates/other/src/lib.rs'

# a build.rs in the closure may read anything: run, unless it is the vetted one
mkdir -p "$t/empty" "$t/r/crates/core" "$t/r/crates/other" "$t/r/crates/aib-cloud-host"
touch "$t/r/crates/core/build.rs" "$t/r/crates/other/build.rs" "$t/r/crates/aib-cloud-host/build.rs"
ROOT=$t/r case_ unvetted_build_rs_in_closure_runs true  pull_request host "docs/a.md"
ROOT=$t/r case_ build_rs_outside_closure_skips    false pull_request cfix "docs/a.md"
ROOT=$t/r case_ vetted_build_rs_skips             false pull_request aib-cloud-host "docs/a.md"
case_ docs_only_tool_runs            true  pull_request host "aib/tools/release/docs_only.py"
case_ quoted_path_runs               true  pull_request host '"crates/host/a\"b.rs"'

# real git (7b81e M1 M2): a non-ASCII name in the closure, and a file moved out of the closure
g=$t/git
mkdir -p "$g/crates/host/tests" "$g/crates/other"
(cd "$g" && git init -q && git config user.email t@t && git config user.name t && git config commit.gpgsign false &&
  echo a >crates/host/tests/a.json && echo o >crates/other/o.rs && git add -A && git commit -qm base) >/dev/null 2>&1
gbase=$(git -C "$g" rev-parse HEAD 2>/dev/null)
real_git() { # real_git <name> <want> ; the working tree of $g already holds the change
  local name=$1 want=$2 head got
  (cd "$g" && git add -A && git commit -qm change) >/dev/null 2>&1
  head=$(git -C "$g" rev-parse HEAD 2>/dev/null)
  got=$(cd "$g" && bash "$S" --event pull_request --base "$gbase" --head "$head" --crate host \
    --metadata "$t/meta.json" --root "$t/empty")
  if grep -qx "changed=$want" <<<"$got"; then ok "$name"; else bad "$name" "want changed=$want, got: $got"; fi
  git -C "$g" reset -q --hard "$gbase" >/dev/null 2>&1
}
echo x >"$g/crates/other/o.rs"
real_git real_git_unrelated_skips false
(cd "$g" && mkdir -p docs && git mv crates/host/tests/a.json docs/a.json) >/dev/null 2>&1
real_git moved_out_of_closure_runs true
echo t >"$g/crates/host/tests/"$'\xe6\xb8\xac'".json"  # 測.json
real_git non_ascii_name_in_closure_runs true

# doubts run: bad metadata, missing files, missing arguments, unknown argument
want_true() { # want_true <name> <args...>
  local name=$1 got
  shift
  got=$(bash "$S" "$@" 2>/dev/null)
  if grep -qx 'changed=true' <<<"$got"; then ok "$name"; else
    bad "$name" "want changed=true, got: $got"
  fi
}
printf 'not json' >"$t/bad.json"
printf 'crates/other/x.rs' >"$t/changed"
want_true bad_metadata_runs --event pull_request --base b --head h --crate host --metadata "$t/bad.json" --changed "$t/changed"
want_true missing_metadata_runs --event pull_request --base b --head h --crate host --metadata "$t/none.json" --changed "$t/changed"
want_true missing_changed_runs --event pull_request --base b --head h --crate host --metadata "$t/meta.json" --changed "$t/none"
want_true no_base_runs --event pull_request --head h --crate host --metadata "$t/meta.json" --changed "$t/changed"
want_true no_crate_runs --event pull_request --base b --head h --metadata "$t/meta.json" --changed "$t/changed"
want_true unknown_arg_runs --event pull_request --base b --head h --crate host --oops

# every output has exactly the two lines
got=$(bash "$S" --event pull_request --base b0 --head h0 --crate host --metadata "$t/meta.json" --changed "$t/changed")
if [ "$(wc -l <<<"$got" | tr -d ' ')" = 2 ] && grep -q '^why=no change in the closure of host' <<<"$got"; then
  ok two_lines
else
  bad two_lines "$got"
fi

# the build.rs lock: its only effects on the build are the two stamps and rerun-if-changed
b="$root/crates/aib-cloud-host/build.rs"
other=$(grep -o 'cargo:[a-z-]*=[A-Z_]*' "$b" | sort -u | grep -v -e '^cargo:rerun-if-changed=' \
  -e '^cargo:rustc-env=AIB_CLOUD_HOST_COMMIT$' -e '^cargo:rustc-env=AIB_CLOUD_HOST_DIRTY$')
n=$(grep -c 'cargo:' "$b")
if [ -f "$b" ] && [ -z "$other" ] && [ "$n" = 5 ]; then
  ok build_rs_only_stamps
else
  bad build_rs_only_stamps "build.rs has extra cargo: lines"
  printf '  %s has cargo: lines beyond COMMIT / DIRTY / rerun-if-changed (%s lines; extra: %s).\n' "$b" "$n" "$other"
  printf '  closure-changed.sh does not count crates/ outside the closure for aib-cloud-host; if build.rs now\n  reads more, add it to the closure or the fixed set first.\n'
fi

echo "closure-changed-test: $pass passed, $fail failed"
[ "$fail" = 0 ]
