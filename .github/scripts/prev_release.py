#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Print the newest ez2-aib release on PyPI that is older than the version given (yanked ones skipped);
with --oldest, the oldest one instead. The release workflow upgrades from both to the new version."""
import json
import sys
import urllib.request


def num(v):
    return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())


def main(argv):
    oldest = "--oldest" in argv
    new = [a for a in argv if a != "--oldest"][0]
    with urllib.request.urlopen("https://pypi.org/pypi/ez2-aib/json", timeout=30) as r:
        releases = json.load(r).get("releases") or {}
    older = [v for v, files in releases.items()
             if files and not all(f.get("yanked") for f in files) and num(v) < num(new)]
    if not older:
        print(new)                       # first release: nothing older to upgrade from
        return 0
    print((min if oldest else max)(older, key=num))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
