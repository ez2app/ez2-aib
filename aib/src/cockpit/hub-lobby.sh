#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
printf '\033[H\033[J\n  AI hub is ready. Starting the requested provider...\n'
exec "${SHELL:-/bin/sh}" -f
