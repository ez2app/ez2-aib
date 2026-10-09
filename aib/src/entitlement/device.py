#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Which machine is this, for license binding — without revealing anything about the machine.

device_id(license_id) = sha256("aib-device-v1:" + license_id + ":" + machine id), first 32 hex chars.
  * The raw machine id never leaves this function (not printed, not sent, not stored).
  * It is salted with the license id, so the same Mac gives a different id under each license: a device id
    cannot be used to recognise the machine across licenses or vendors.

Machine id sources: macOS IOPlatformUUID (`ioreg`), Linux /etc/machine-id (systemd) or /var/lib/dbus/machine-id.
They survive reboots and OS updates; a reinstall (Linux) or a logic-board swap (Mac) changes them — then the
customer asks for a new binding (offline) or simply activates again (online).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

PREFIX = "aib-device-v1:"
_LINUX_IDS = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def machine_id() -> Optional[str]:
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True,
                                 text=True, timeout=10, stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', out)
        return m.group(1).upper() if m else None
    for p in _LINUX_IDS:
        try:
            v = Path(p).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if re.fullmatch(r"[0-9a-f]{32}", v):
            return v
    return None


def device_id(license_id: str, raw: Optional[str] = None) -> Optional[str]:
    """This machine's id under one license; None when the machine id cannot be read (callers fail closed)."""
    raw = machine_id() if raw is None else raw
    if not raw or not isinstance(license_id, str) or not license_id:
        return None
    return hashlib.sha256((PREFIX + license_id + ":" + raw).encode("utf-8")).hexdigest()[:32]
