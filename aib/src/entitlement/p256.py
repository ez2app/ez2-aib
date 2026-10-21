# SPDX-License-Identifier: Apache-2.0
"""ECDSA P-256 / SHA-256 驗章,只用標準函式庫(照 ed25519.py 的寫法;aib quote 用,@abb89 10-21 裁)。

只驗不簽:簽章在 Secure Enclave 裡做(aib-sehelper se-sign),這裡讓沒有 SE / 不是 Mac 的機器也能驗。
公鑰 = X9.63 未壓縮(65 位元組,0x04 ‖ X ‖ Y);簽章 = DER SEQUENCE{INTEGER r, INTEGER s}。
任何格式不對一律回 False(不丟例外)。
"""
import hashlib

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _on_curve(pt):
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


# Jacobian 座標(X, Y, Z);None = 無窮遠點
def _jdouble(p):
    if p is None:
        return None
    x, y, z = p
    if y == 0:
        return None
    yy = y * y % P
    s = 4 * x * yy % P
    zz = z * z % P
    m = (3 * x * x + A * zz * zz) % P
    x3 = (m * m - 2 * s) % P
    y3 = (m * (s - x3) - 8 * yy * yy) % P
    z3 = 2 * y * z % P
    return (x3, y3, z3)


def _jadd(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    if u1 == u2:
        return _jdouble(p) if s1 == s2 else None
    h, r = (u2 - u1) % P, (s2 - s1) % P
    hh = h * h % P
    hhh = h * hh % P
    v = u1 * hh % P
    x3 = (r * r - hhh - 2 * v) % P
    y3 = (r * (v - x3) - s1 * hhh) % P
    z3 = h * z1 * z2 % P
    return (x3, y3, z3)


def _mul2(a, pa, b, pb):
    """a·pa + b·pb(Shamir 同時乘),回仿射座標或 None。"""
    ja, jb = (pa[0], pa[1], 1), (pb[0], pb[1], 1)
    both = _jadd(ja, jb)
    acc = None
    for i in range(max(a.bit_length(), b.bit_length()) - 1, -1, -1):
        acc = _jdouble(acc)
        bit = ((a >> i) & 1, (b >> i) & 1)
        if bit == (1, 1):
            acc = _jadd(acc, both)
        elif bit == (1, 0):
            acc = _jadd(acc, ja)
        elif bit == (0, 1):
            acc = _jadd(acc, jb)
    if acc is None or acc[2] == 0:
        return None
    zi = pow(acc[2], -1, P)
    return (acc[0] * zi * zi % P, acc[1] * zi * zi * zi % P)


def _der_int(buf, i):
    if i + 2 > len(buf) or buf[i] != 0x02:
        raise ValueError("int")
    ln = buf[i + 1]
    if ln == 0 or ln > 33 or i + 2 + ln > len(buf):
        raise ValueError("len")
    raw = buf[i + 2:i + 2 + ln]
    if raw[0] & 0x80 or (ln > 1 and raw[0] == 0 and not raw[1] & 0x80):
        raise ValueError("not minimal / negative")
    return int.from_bytes(raw, "big"), i + 2 + ln


def parse_der(sig):
    """DER ⇒ (r, s);只收最短編碼(防同一張簽章多種寫法)。"""
    sig = bytes(sig)
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2:
        raise ValueError("seq")
    r, i = _der_int(sig, 2)
    s, i = _der_int(sig, i)
    if i != len(sig):
        raise ValueError("trailing")
    return r, s


def parse_pub(pub):
    pub = bytes(pub)
    if len(pub) != 65 or pub[0] != 0x04:
        raise ValueError("pub")
    pt = (int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big"))
    if not _on_curve(pt):
        raise ValueError("not on curve")
    return pt


def verify(pub, msg, sig):
    """pub:65 位元組 X9.63;msg:原文(這裡做 SHA-256);sig:DER。對 ⇒ True。"""
    try:
        q = parse_pub(pub)
        r, s = parse_der(sig)
    except (ValueError, TypeError, IndexError):
        return False
    if not (1 <= r < N and 1 <= s < N):
        return False
    e = int.from_bytes(hashlib.sha256(bytes(msg)).digest(), "big")
    w = pow(s, -1, N)
    pt = _mul2(e * w % N, G, r * w % N, q)
    return pt is not None and pt[0] % N == r
