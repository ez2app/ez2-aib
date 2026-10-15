#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ask an installed ez2-aib how it would update itself (the command opening a seat runs), using that install's own
Python. Prints the command as one line, or "none" when that install does not know how.
Runs that Python with -I so a checkout in the current folder (it also has an aib/ folder) is not imported instead."""
import subprocess
import sys

PROBE = r'''
import os, sys, json
import aib
pkg = os.path.dirname(aib.__file__)
sys.path.insert(0, os.path.join(pkg, "src", "cockpit"))
import cli_versions as C
print(json.dumps(C.aib_update_cmd(pkg)))
'''

if __name__ == "__main__":
    import json
    out = subprocess.run([sys.argv[1], "-I", "-c", PROBE], capture_output=True, text=True, check=True).stdout.strip()
    cmd = json.loads(out.splitlines()[-1])
    if not cmd:
        print("none")
    elif len(cmd) > 2 and cmd[1] == "-m":          # [<python>, "-m", "pip", …] ⇒ "pip …"
        print(" ".join(cmd[2:]))
    else:
        print(" ".join(cmd))
