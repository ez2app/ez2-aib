# SPDX-License-Identifier: Apache-2.0
"""The dm header address and the "is this another machine?" check pick this machine's IP the same way,
and prefer Tailscale even when `tailscale` is not on PATH (it is found at the macOS app's fixed install path).

10-14: a Mac reached by everyone over Tailscale (100.x) wrote its LAN address in dm headers, because shells
opened over SSH have no `tailscale` on PATH. Fake `tailscale` / `ipconfig` executables only; nothing real is
queried. Standard library only.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "aib" / "src" / "cockpit"
TS_IP, LAN_IP = "100.64.0.1", "192.168.0.10"
PROBE = ("import json, sys; sys.path.insert(0, %r); import dm_header, dm_address; "
         "print(json.dumps({'addr': dm_header.local_addr(), 'ips': sorted(dm_address.local_ips())}))" % str(COCKPIT))


def fake(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class HostIpTailscale(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-hostip-"))
        self.bin = self.td / "bin"
        fake(self.bin / "ipconfig", '[ "$2" = en0 ] && echo %s; exit 0' % LAN_IP)
        self.app = self.td / "Tailscale.app" / "Contents" / "MacOS" / "Tailscale"
        self.ts = 'if [ "$1" = ip ]; then [ "$2" = -4 ] || echo fd7a:115c::1; echo %s; fi' % TS_IP

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def probe(self, app):
        env = {"PATH": str(self.bin), "HOME": str(self.td), "AIB_TAILSCALE_APP": str(app)}
        r = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_tailscale_only_at_the_app_path(self):
        fake(self.app, self.ts)
        got = self.probe(self.app)
        self.assertEqual(got["addr"], TS_IP)
        self.assertIn(TS_IP, got["ips"])
        self.assertIn(LAN_IP, got["ips"])

    def test_tailscale_on_path(self):
        fake(self.bin / "tailscale", self.ts)
        got = self.probe(self.td / "nope")
        self.assertEqual(got["addr"], TS_IP)
        self.assertIn(TS_IP, got["ips"])

    def test_no_tailscale_falls_back_to_lan(self):
        got = self.probe(self.td / "nope")
        self.assertEqual(got["addr"], LAN_IP)
        self.assertNotIn(TS_IP, got["ips"])

    def test_header_address_is_one_the_cross_host_check_knows(self):
        for setup in ("app", "path", "none"):
            with self.subTest(setup=setup):
                shutil.rmtree(self.bin / "tailscale", ignore_errors=True)
                if (self.bin / "tailscale").exists():
                    (self.bin / "tailscale").unlink()
                if setup == "app":
                    fake(self.app, self.ts)
                if setup == "path":
                    fake(self.bin / "tailscale", self.ts)
                got = self.probe(self.app if setup == "app" else self.td / "nope")
                self.assertIn(got["addr"], got["ips"])


if __name__ == "__main__":
    unittest.main()
