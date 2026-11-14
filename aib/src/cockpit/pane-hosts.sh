#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 座艙中欄下格:aib-hosts 主機群。實作在 pane-plan.sh(同一套找程式 / 重開邏輯)。
exec bash "$(dirname "$0")/pane-plan.sh" aib-hosts
