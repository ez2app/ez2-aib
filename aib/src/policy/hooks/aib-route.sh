#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# decide-route 的薄包裝器。
# 路由結果由 tools/dispatch/decide-route.py 自己落痕;本檔只保留穩定入口。
set -uo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPO="${AIB_REPO:-$(CDPATH= cd -- "$HERE/../../.." && pwd -P)}"
exec python3 "$REPO/tools/dispatch/decide-route.py" "$@"
