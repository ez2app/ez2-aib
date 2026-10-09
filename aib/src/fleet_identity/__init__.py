# SPDX-License-Identifier: Apache-2.0
"""P1A fleet identity bootstrap.

The package is intentionally independent from tmux and optional knowledge
adapters.  It owns durable project/member/published-persona/native-root truth;
the cockpit may consume its receipts but may not infer equivalent authority
from a directory scan or a display name.
"""
