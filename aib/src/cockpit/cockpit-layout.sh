#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Compatibility entrypoint.  The eye-verified assembler is now the single
# owner of layout creation and reconciliation.
set -euo pipefail
exec bash "$(dirname "$0")/cockpit-full.sh" --ensure
