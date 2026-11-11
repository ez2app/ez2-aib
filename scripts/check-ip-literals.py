#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Every IP literal in the repo must sit in an allowed range (public repo hygiene).

This repo is public, so a real host address must never land in code, tests,
fixtures or docs. The rule is an allow-list, on purpose: it names only the
ranges that are always safe and never names the addresses it keeps out, so the
check itself leaves no clue in the repo.

Allowed (f1fae 11-11):
    0.0.0.0, 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16,
    100.64.0.0/24 only (a sample slice of 100.64/10; the rest of that range
    is where tailnet addresses live, so it stays outside, c477a 11-11 M1),
    192.0.2/24, 198.51.100/24, 203.0.113/24 (documentation), 255.x (masks)
    IPv6: ::, ::1, fe80::/10, fd00::/64 only (a sample slice of the unique-local
    fc00::/7; the rest of it is where tailnet addresses live), ff00::/8,
    2001:db8::/32 (documentation),
    ::ffff:a.b.c.d / ::ffff:x:y when the IPv4 inside is allowed
Not read as an address:
    four numbers that are part of something longer (v1.2.3.4, 1.2.3.4.5,
    1.2.3.4-rc1, a1.2.3.4), any number above 255, non-ASCII digits, and
    numbers inside an SVG path attribute (d="..."), which minified icons
    write as d="M4 2.3.3.5".
    IPv6: only a form with "::" or all eight groups, and at least three groups
    written (so a Python slice a[2::2] or a line number ::141 is not one) --
    two are enough when the first group is written and the form is a network
    (followed by /len), ends in "::", or has a full four-digit group (c477a
    L1: provider prefixes are usually written that short); a
    form followed by a dotted IPv4 is read through its IPv4 part.
Also read (ebb36 L1): a dotted address after a dot or an underscore
    (static.A.B.C.D.<domain>, x_A.B.C.D), and the dashed form reverse names
    use (A-B-C-D.<domain>, ec2-A-B-C-D.<domain>) when a dot and a letter follow.
Kept on purpose (a test that needs a public or reserved address, a
well-known public resolver): the line carries the marker `ip-literal-ok`
and a reason. The marked lines are listed on every run, so review sees them.
The marker is for third-party or documentation addresses only, never for an
address of ours (c477a N2).

    python3 -I scripts/check-ip-literals.py              check the tracked files
    python3 -I scripts/check-ip-literals.py --self-test  prove each rule on samples

Prints `test ip_literals ... ok` or `test ip_literals ... FAILED` (the form
scripts/knife.py reads) and exits 1 on any address outside the list.
"""
import ipaddress
import os
import re
import subprocess
import sys

MARKER = "ip-literal-ok"
# ASCII digits only; no letter or digit glued on either side. A dot or an underscore in
# front is read through (`static.A.B.C.D.<domain>`, `x_A.B.C.D`, ebb36 L1) unless a digit
# stands before it: that is a longer dotted number (1.2.3.4.5), not an address.
IPV4 = re.compile(
    r"(?<![0-9A-Za-z])(?<![0-9][._])"
    r"([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})"
    r"(?![0-9A-Za-z_]|\.[0-9]|-[0-9A-Za-z])"
)
# The dashed form reverse names use (`A-B-C-D.<domain>`, `ec2-A-B-C-D.<domain>`): only
# when a dot and a letter follow, so dates and version runs are not read.
IPV4_DASHED = re.compile(
    # a dash in front is read through after a word (`ec2-`), not after another number
    r"(?<![0-9])(?<![^A-Za-z][0-9]-)(?<![^A-Za-z][0-9]{2}-)(?<![^A-Za-z][0-9]{3}-)"
    r"(?<!^[0-9]-)(?<!^[0-9]{2}-)(?<!^[0-9]{3}-)"
    r"([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})"
    r"(?=\.[A-Za-z])"
)
# d="...", also escaped inside a string literal (d=\"...\")
SVG_PATH = re.compile(r"""(?<![\w-])d=\\?(?:"[^"]*"|'[^']*')""")


def allowed(o):
    a, b, c, _ = o
    return (
        o == (0, 0, 0, 0)
        or a in (10, 127, 255)
        or (a == 172 and 16 <= b <= 31)
        or (a == 192 and b == 168)
        or (a == 169 and b == 254)
        or (a, b, c) == (100, 64, 0)
        or (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113))
    )


IPV6 = re.compile(
    r"(?<![0-9A-Za-z_:.])"
    r"([0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})"
    r"(?![0-9A-Za-z_:]|\.[0-9])"
)
NETS6 = [ipaddress.ip_network(n) for n in ("fe80::/10", "fd00::/64", "ff00::/8", "2001:db8::/32")]


def v6_outside(s, network=False):
    """The IPv6 literal s when it is one and outside the list, else None.
    network: the text goes on with /len right after s."""
    if "::" not in s and s.count(":") != 7:
        return None
    groups = [g for g in s.split(":") if g]
    short_ok = not s.startswith(":") and (network or s.endswith("::") or any(len(g) == 4 for g in groups))
    need = 2 if short_ok else 3
    if len(groups) < need:
        return None
    try:
        a = ipaddress.IPv6Address(s)
    except ValueError:
        return None
    if a.is_unspecified or a.is_loopback or any(a in n for n in NETS6):
        return None
    if a.ipv4_mapped is not None and allowed(tuple(a.ipv4_mapped.packed)):
        return None
    return s


def scan_text(text):
    """Yield (line_no, literal, marked) for every IPv4 literal outside the list."""
    for n, line in enumerate(text.splitlines(), 1):
        svg = [m.span() for m in SVG_PATH.finditer(line)]
        for m in [*IPV4.finditer(line), *IPV4_DASHED.finditer(line)]:
            if any(a <= m.start() < b for a, b in svg):
                continue
            o = tuple(int(x) for x in m.groups())
            if max(o) > 255 or allowed(o):
                continue
            yield n, m.group(0), MARKER in line
        for m in IPV6.finditer(line):
            if any(a <= m.start() < b for a, b in svg):
                continue
            if v6_outside(m.group(1), re.match(r"/[0-9]", line[m.end():]) is not None):
                yield n, m.group(1), MARKER in line


def tracked_files(root):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def check(root):
    bad, marked = [], []
    for path in tracked_files(root):
        full = os.path.join(root, path)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        with open(full, "rb") as f:
            raw = f.read()
        if b"\0" in raw:
            continue  # binary
        for n, lit, ok in scan_text(raw.decode("utf-8", "replace")):
            (marked if ok else bad).append(f"{path}:{n}: {lit}")
    for m in marked:
        print(f"  kept ({MARKER}): {m}")
    for b in bad:
        print(f"::error::{b} is outside the allowed ranges (use 192.0.2.x / 198.51.100.x / 203.0.113.x, or 2001:db8::/32 for IPv6)")
    print(f"ip_literals: {len(bad)} outside the list, {len(marked)} kept with {MARKER}")
    print(f"test ip_literals ... {'FAILED' if bad else 'ok'}")
    return 1 if bad else 0


SELF = [
    # (text, number of literals that must be reported)
    ("host = 203.0.113.10", 0),
    ("a 192.0.2.1 b 198.51.100.7", 0),
    ("lo 127.0.0.1 any 0.0.0.0 mask 255.255.255.0", 0),
    ("10.1.2.3 172.16.0.1 172.31.255.255 192.168.1.1 169.254.169.254", 0),
    ("sample 100.64.0.1 100.64.0.254", 0),
    ("100.64.1.0 100.127.255.254 100.100.1.1", 3),  # rest of 100.64/10 is not a sample; ip-literal-ok
    ("8.8.8.8", 1),  # ip-literal-ok
    ("203.0.114.10", 1),  # just past the documentation range; ip-literal-ok
    ("198.51.101.1 192.0.3.1", 2),  # ip-literal-ok
    ("100.63.255.255 100.128.0.1", 2),  # both sides of 100.64/10; ip-literal-ok
    ("172.15.0.1 172.32.0.1", 2),  # both sides of 172.16/12; ip-literal-ok
    ("0.0.0.1", 1),  # only 0.0.0.0 itself; ip-literal-ok
    ("ip=9.9.009.9,", 1),  # leading zeros are still an address; ip-literal-ok
    ("\"1.1.1.1/32\"", 1),  # ip-literal-ok
    ("public 1.1.1.1 # ip-literal-ok: well-known resolver", 0),
    ("v1.2.3.4 1.2.3.4.5 1.2.3.4-rc1 a1.2.3.4 999.1.1.1", 0),
    ("1.2.3.4-", 1),  # ip-literal-ok
    ("2001:db8::1 [2001:db8:4b::2]:443 ::1 :: fe80::1%eth0 fd00::1 ff02::1", 0),
    ("fd12:3456::1 fc00::5 fd00:0:0:1::1", 3),  # unique-local outside the fd00::/64 sample; ip-literal-ok
    ("2a00:1::/32 2a00:1:: 2a00::1", 3),  # two groups: a network, a trailing ::, a full group; ip-literal-ok
    ("a[1::2] x[10::5] 2001:db8::/32 fd00::/64", 0),  # short slices stay unread; sample networks pass
    ("::ffff:127.0.0.1 ::ffff:7f00:1 ::ffff:10.0.0.1", 0),
    ("argv[2::2] a[1::2] see ::141 and ::1:1455 BC:24:11:AA:BB:CC 12:34:56", 0),
    ("2001:db9::1", 1),  # just past the documentation range; ip-literal-ok
    ("dns6 2606:4700:4700::1111", 1),  # ip-literal-ok
    ("1:2:3:4:5:6:7:8", 1),  # all eight groups; ip-literal-ok
    ("::ffff:8.8.8.8", 1),  # the IPv4 inside is read; ip-literal-ok
    ("\uff11.2.3.4", 0),  # fullwidth digit is not ASCII
    ('<path d="M12 4.1 2.3.3.5 1.7z"/>', 0),  # SVG path data
    ('<path d="M1 2"/> 8.8.4.4 data-d="8.8.4.4"', 2),  # outside d="", or in another attribute; ip-literal-ok
    ('"<path d=\\"M4.1 2.3.3.5 1.7z\\"/>"', 0),  # escaped inside a JS string; ip-literal-ok
    ("static.8.8.4.4.clients.example.net x_8.8.4.4 host.8.8.4.4", 3),  # reverse-name forms, ebb36 L1; ip-literal-ok
    ("ec2-8-8-4-4.compute.example.net 8-8-4-4.ip.example.net", 2),  # dashed reverse names; ip-literal-ok
    ("1.2.3.4.5.6 v2.1.2.3.4 2026-11-11-12.log 1-2-3-4-5.x 10-0-0-1.example.net", 0),
    ("1-2-3-4-5.x", 0),  # a longer dashed run at the start of a line
]


def self_test():
    fails = 0
    for text, want in SELF:
        got = sum(1 for _, _, ok in scan_text(text) if not ok)
        if got != want:
            fails += 1
            print(f"self-test: {text!r}: reported {got}, want {want}")
    print(f"self-test: {len(SELF) - fails}/{len(SELF)}")
    print(f"test ip_literals_self_test ... {'FAILED' if fails else 'ok'}")
    return 1 if fails else 0


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else check(here))
