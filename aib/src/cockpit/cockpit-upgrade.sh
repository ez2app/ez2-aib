#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Compatibility entrypoint retained for older callers.  It is intentionally
# non-destructive: existing hub processes and conversations are preserved.
set -euo pipefail
exec bash "$(dirname "$0")/cockpit-full.sh" --ensure
