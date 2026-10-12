#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Captain's Room web page: shim kept at the documented path. The real server is src/cockpit/captain_room.py
(it must live under src/ so dms sent from the page carry the "captain-room" sender identity). exec keeps the
process command line pointing at the real file, which is what the sender-identity walk reads."""
import os, sys
REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "cockpit", "captain_room.py")
os.execv(sys.executable, [sys.executable, os.path.abspath(REAL), *sys.argv[1:]])
