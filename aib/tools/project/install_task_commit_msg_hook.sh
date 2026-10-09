#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install commit-msg hook that requires a formal Task: trailer (t.… uid only).
# Respects core.hooksPath: installs where git actually reads hooks.
# Usage: tools/project/install_task_commit_msg_hook.sh [--repo PATH] [--force] [--dry-run]
set -euo pipefail

REPO=""
FORCE=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --force|-f) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--repo PATH] [--force] [--dry-run]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REPO" ]]; then
  REPO="$(cd "$(dirname "$0")/../.." && pwd)"
fi
REPO="$(cd "$REPO" && pwd)"

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  echo "not a git repo: $REPO" >&2
  exit 1
fi

# hooksPath may be unset, set-to-path, or set-to-empty (empty ≠ unset).
HOOKS_PATH_SET=0
HOOKS_PATH_CFG=""
if git -C "$REPO" config --get core.hooksPath >/dev/null 2>&1; then
  HOOKS_PATH_SET=1
  HOOKS_PATH_CFG="$(git -C "$REPO" config --get core.hooksPath)"
fi

# Empty / whitespace hooksPath: git cannot resolve a hooks dir; never print installed.
if [[ "$HOOKS_PATH_SET" -eq 1 && -z "${HOOKS_PATH_CFG//[[:space:]]/}" ]]; then
  echo "refusing: core.hooksPath is empty (unset it for default .git/hooks, or set a real path)" >&2
  echo "core.hooksPath: (empty string)" >&2
  exit 1
fi

# Effective hooks dir: respects core.hooksPath (absolute when possible).
HOOKS_DIR=""
if HOOKS_DIR="$(git -C "$REPO" rev-parse --path-format=absolute --git-path hooks 2>/dev/null)"; then
  :
else
  # Older git, or broken hooksPath: try relative --git-path; reject junk.
  if ! _gp="$(git -C "$REPO" rev-parse --git-path hooks 2>/dev/null)"; then
    echo "refusing: cannot resolve hooks dir via git-path" >&2
    if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
      echo "core.hooksPath: $HOOKS_PATH_CFG" >&2
    fi
    exit 1
  fi
  if [[ -z "$_gp" || "$_gp" == "." || "$_gp" == "./" ]]; then
    echo "refusing: git-path hooks resolved to '$_gp' (invalid core.hooksPath?)" >&2
    if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
      echo "core.hooksPath: ${HOOKS_PATH_CFG:-"(empty)"}" >&2
    fi
    exit 1
  fi
  if [[ "$_gp" = /* ]]; then
    HOOKS_DIR="$_gp"
  else
    HOOKS_DIR="$(cd "$REPO" && cd "$(dirname "$_gp")" && pwd)/$(basename "$_gp")"
  fi
fi

# Never install to repo root (false "installed" with a non-hook path).
if [[ "$HOOKS_DIR" == "$REPO" || "$HOOKS_DIR" == "$REPO/" || "$HOOKS_DIR" == "$REPO/." || "$HOOKS_DIR" == "$REPO/./" ]]; then
  echo "refusing: hooks dir resolves to repo root: $HOOKS_DIR" >&2
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: ${HOOKS_PATH_CFG:-"(empty)"}" >&2
  fi
  exit 1
fi

HOOK="$HOOKS_DIR/commit-msg"
CHECKER="$REPO/tools/project/check_task_trailer.py"

if [[ ! -f "$CHECKER" ]]; then
  echo "missing checker: $CHECKER" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "would-install: $HOOK"
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: $HOOKS_PATH_CFG"
  else
    echo "core.hooksPath: (unset → default git hooks)"
  fi
  echo "checker: $CHECKER"
  exit 0
fi

# Must be able to create/write the effective hooks dir (never fall back to unread .git/hooks).
if ! mkdir -p "$HOOKS_DIR" 2>/dev/null; then
  echo "refusing: cannot create hooks dir: $HOOKS_DIR" >&2
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: $HOOKS_PATH_CFG" >&2
  fi
  exit 1
fi

# After mkdir, re-check not repo root (mkdir -p "." succeeds).
HOOKS_DIR_ABS="$(cd "$HOOKS_DIR" && pwd)"
if [[ "$HOOKS_DIR_ABS" == "$REPO" ]]; then
  echo "refusing: hooks dir resolves to repo root: $HOOKS_DIR_ABS" >&2
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: ${HOOKS_PATH_CFG:-"(empty)"}" >&2
  fi
  exit 1
fi

if [[ ! -w "$HOOKS_DIR" ]]; then
  echo "refusing: hooks dir not writable: $HOOKS_DIR" >&2
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: $HOOKS_PATH_CFG" >&2
  fi
  exit 1
fi

if [[ -e "$HOOK" && "$FORCE" -ne 1 ]]; then
  # already our hook?
  if grep -q 'check_task_trailer.py' "$HOOK" 2>/dev/null; then
    echo "hook already installed: $HOOK"
    exit 0
  fi
  echo "refusing to overwrite existing hook: $HOOK (pass --force)" >&2
  exit 1
fi

if ! cat > "$HOOK" << HOOK
#!/usr/bin/env bash
# Auto-installed by tools/project/install_task_commit_msg_hook.sh
# Requires formal Task: t.… trailer (rejects §/# aliases).
set -euo pipefail
MSG_FILE="\${1:-}"
if [[ -z "\$MSG_FILE" || ! -f "\$MSG_FILE" ]]; then
  echo "commit-msg: missing message file" >&2
  exit 1
fi
REPO_ROOT="\$(git rev-parse --show-toplevel)"
CHECKER="\$REPO_ROOT/tools/project/check_task_trailer.py"
if [[ ! -f "\$CHECKER" ]]; then
  echo "commit-msg: missing \$CHECKER" >&2
  exit 1
fi
exec python3 "\$CHECKER" --stdin < "\$MSG_FILE"
HOOK
then
  echo "refusing: cannot write hook: $HOOK" >&2
  if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
    echo "core.hooksPath: $HOOKS_PATH_CFG" >&2
  fi
  exit 1
fi

chmod +x "$HOOK"
echo "installed: $HOOK"
if [[ "$HOOKS_PATH_SET" -eq 1 ]]; then
  echo "core.hooksPath: $HOOKS_PATH_CFG"
fi
echo "checker: $CHECKER"
