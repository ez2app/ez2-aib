#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Upgrade a user's install of ez2-aib from an older release to the new one, with the same commands aib runs itself.
# usage: upgrade_check.sh <pipx|pip|oneline> <old> <new> [wheel]
#   with a wheel: upgrade to that local file (before the upload to PyPI); without: upgrade from PyPI (after it).
#   STRICT=1: an older release that cannot tell how to update itself is an error; otherwise a warning
#   (a release already out cannot be fixed — its users are told what to run instead).
set -euo pipefail
how=$1 old=$2 new=$3 wheel=${4:-}
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "${RUNNER_TEMP:-/tmp}/aib-up-XXXXXX")
fail() { echo "::error::$*"; exit 1; }
soft() { if [ "${STRICT:-0}" = 1 ]; then fail "$*"; else echo "::warning::$*"; fi; }
asks() { python "$here/update_cmd.py" "$1" 2>/dev/null || echo none; }   # very old releases: no answer at all
# Right after an upload, PyPI's CDN edges disagree for a few minutes: one request sees the new release, the next
# still the old one (0.1.16: pipx reached it, pip 4 s later did not). From PyPI (no wheel) ⇒ retry the upgrade.
tries=${UPGRADE_TRIES:-6} pause=${UPGRADE_SLEEP:-20}
[ -n "$wheel" ] && tries=1    # a local wheel is not behind a CDN: one try
lagging() {   # lagging <i> <got>: true ⇒ try again
  [ "$2" = "$new" ] && return 1
  [ "$1" -lt "$tries" ] || return 1
  echo "still $2 after upgrade $1/$tries, PyPI CDN may lag; retrying in ${pause}s"
  sleep "$pause"
}
case $how in
  pipx)
    export PIPX_HOME="$tmp/pipx" PIPX_BIN_DIR="$tmp/pipx/bin"
    python -m pipx install -q "ez2-aib==$old"
    py="$PIPX_HOME/venvs/ez2-aib/bin/python"
    cmd=$(asks "$py"); echo "ez2-aib $old (pipx) updates itself with: $cmd"
    [ "$cmd" = "pipx upgrade ez2-aib" ] || soft "a pipx install of ez2-aib $old does not update itself (got: $cmd)"
    i=0; while :; do i=$((i+1))
      if [ -n "$wheel" ]; then python -m pipx runpip ez2-aib install -q --upgrade "$wheel"; else python -m pipx upgrade ez2-aib; fi
      got=$("$py" -c "import importlib.metadata as m; print(m.version('ez2-aib'))")   # pipx 的環境沒有 pip
      lagging "$i" "$got" || break
    done ;;
  pip)
    python -m venv "$tmp/venv"
    "$tmp/venv/bin/python" -m pip install -q --no-cache-dir "ez2-aib==$old"
    cmd=$(asks "$tmp/venv/bin/python"); echo "ez2-aib $old (pip) updates itself with: $cmd"
    case "$cmd" in
      *"pip install"*"ez2-aib"*) ;;
      *) soft "ez2-aib $old installed with pip does not update itself (got: $cmd); its users need: pip install --upgrade ez2-aib";;
    esac
    i=0; while :; do i=$((i+1))
      "$tmp/venv/bin/python" -m pip install -q --upgrade --no-cache-dir "${wheel:-ez2-aib}"
      got=$("$tmp/venv/bin/python" -c "import importlib.metadata as m; print(m.version('ez2-aib'))")
      lagging "$i" "$got" || break
    done ;;
  oneline)
    root=${GITHUB_WORKSPACE:-$(pwd)}
    export HOME="$tmp/home"; mkdir -p "$HOME"
    git config --global user.name ci; git config --global user.email ci@example.com
    bash "$root/install.sh" --dir "$HOME/ez2-aib" </dev/null
    git -C "$HOME/ez2-aib" reset -q --hard "v$old" || { soft "no tag v$old to go back to"; exit 0; }
    [ "$(cat "$HOME/ez2-aib/aib/VERSION")" = "$old" ] || fail "could not go back to $old"
    if ! AIB_INSTALL_URL="file://$root/install.sh" "$HOME/ez2-aib/bin/aib" update </dev/null; then
      soft "aib update failed on a one-line install of $old"; exit 0
    fi
    got=$(cat "$HOME/ez2-aib/aib/VERSION") ;;
  *) fail "usage: upgrade_check.sh <pipx|pip|oneline> <old> <new> [wheel]" ;;
esac
[ "$got" = "$new" ] || fail "$how upgrade from $old did not reach $new (has $got)"
echo "ok: $how $old -> $new"
