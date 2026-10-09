#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ed25519 (RFC 8032) in pure Python, standard library only.

Used to verify license signatures without a third-party dependency (aib's open-source package has
`dependencies = []`). Verification only needs the public key, which is safe to publish; the private
key never lives in any repository (see tools/license/aib-license.py, which keeps it in the signer's
home folder).

This follows the reference algorithm of RFC 8032 section 5.1 / 6. It is slow (milliseconds per call)
and NOT constant-time: fine for verifying a license file once, not for signing on a shared machine
under timing attack. Signing is provided for the maintainer's offline tool and for tests.
"""
from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_mod_l(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % _L


# Points in extended homogeneous coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z.
def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)                 # neutral element
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q) -> bool:
    # x1/z1 == x2/z2  <=>  x1*z2 == x2*z1
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(p) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x = p[0] * zinv % _P
    y = p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _secret_expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("Ed25519 private key (seed) must be 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """32-byte private seed → 32-byte public key."""
    a, _prefix = _secret_expand(seed)
    return _compress(_mul(a, _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    """32-byte private seed, message → 64-byte signature."""
    a, prefix = _secret_expand(seed)
    pub = _compress(_mul(a, _G))
    r = _sha512_mod_l(prefix + msg)
    rs = _compress(_mul(r, _G))
    h = _sha512_mod_l(rs + pub + msg)
    s = (r + h * a) % _L
    return rs + int.to_bytes(s, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    """True only for a valid signature. Any malformed input is simply False (never raises)."""
    if not isinstance(pub, (bytes, bytearray)) or not isinstance(sig, (bytes, bytearray)) or len(pub) != 32 or len(sig) != 64:
        return False
    a = _decompress(bytes(pub))
    if a is None:
        return False
    rs = bytes(sig[:32])
    r = _decompress(rs)
    if r is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    h = _sha512_mod_l(rs + bytes(pub) + bytes(msg))
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
