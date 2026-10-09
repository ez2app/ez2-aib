#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""金鑰櫃落地加密:XChaCha20-Poly1305(RFC 8439 + HChaCha20),無第三方套件。

wrap.key 32 位元組、0600,與金鑰檔分開。信封前綴 enc.v1. 讓舊的明文行仍可讀。
AAD 綁行名,換到別的 NAME 解不開。值不得進例外或 log。
"""
from __future__ import annotations

import base64
import hmac
import os
import stat
from pathlib import Path

PREFIX = "enc.v1."
KEY_NAME = "wrap.key"
KEY_BYTES = 32
NONCE_BYTES = 24
TAG_BYTES = 16
FILE_MODE = 0o600
DIR_MODE = 0o700
OPEN_MASK = 0o077


class CryptoError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _rotl32(value, count):
    return ((value << count) & 0xffffffff) | (value >> (32 - count))


def _quarter(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & 0xffffffff
    state[d] = _rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xffffffff
    state[b] = _rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xffffffff
    state[d] = _rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xffffffff
    state[b] = _rotl32(state[b] ^ state[c], 7)


def _chacha_rounds(words):
    state = list(words)
    for _round in range(10):
        _quarter(state, 0, 4, 8, 12)
        _quarter(state, 1, 5, 9, 13)
        _quarter(state, 2, 6, 10, 14)
        _quarter(state, 3, 7, 11, 15)
        _quarter(state, 0, 5, 10, 15)
        _quarter(state, 1, 6, 11, 12)
        _quarter(state, 2, 7, 8, 13)
        _quarter(state, 3, 4, 9, 14)
    return state


def _words(raw):
    return [int.from_bytes(raw[index:index + 4], "little")
            for index in range(0, len(raw), 4)]


def _word_bytes(words):
    return b"".join(value.to_bytes(4, "little") for value in words)


def _hchacha20(key, nonce16):
    constants = _words(b"expand 32-byte k")
    state = _chacha_rounds(constants + _words(key) + _words(nonce16))
    return _word_bytes(state[:4] + state[12:16])


def _chacha_block(key, counter, nonce12):
    initial = _words(b"expand 32-byte k") + _words(key) + [counter] \
        + _words(nonce12)
    state = _chacha_rounds(initial)
    return _word_bytes([
        (state[index] + initial[index]) & 0xffffffff
        for index in range(16)
    ])


def _chacha_xor(key, nonce12, raw, counter=1):
    output = bytearray()
    for offset in range(0, len(raw), 64):
        if counter > 0xffffffff:
            raise CryptoError("crypto_unavailable")
        block = _chacha_block(key, counter, nonce12)
        chunk = raw[offset:offset + 64]
        output.extend(left ^ right for left, right in zip(chunk, block))
        counter += 1
    return bytes(output)


def _poly1305(message, one_time_key):
    r = int.from_bytes(one_time_key[:16], "little")
    r &= 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(one_time_key[16:], "little")
    accumulator = 0
    modulus = (1 << 130) - 5
    for offset in range(0, len(message), 16):
        block = message[offset:offset + 16]
        number = int.from_bytes(block + b"\x01", "little")
        accumulator = ((accumulator + number) * r) % modulus
    return ((accumulator + s) % (1 << 128)).to_bytes(16, "little")


def _pad16(raw):
    remainder = len(raw) % 16
    return b"" if remainder == 0 else b"\x00" * (16 - remainder)


def _encrypt_bytes(key, plaintext, aad):
    nonce = os.urandom(NONCE_BYTES)
    subkey = _hchacha20(key, nonce[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce[16:]
    poly_key = _chacha_block(subkey, 0, nonce12)[:32]
    ciphertext = _chacha_xor(subkey, nonce12, plaintext)
    mac_input = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) \
        + len(aad).to_bytes(8, "little") \
        + len(ciphertext).to_bytes(8, "little")
    tag = _poly1305(mac_input, poly_key)
    return nonce, ciphertext, tag


def _decrypt_bytes(key, nonce, ciphertext, tag, aad):
    if len(nonce) != NONCE_BYTES or len(tag) != TAG_BYTES:
        raise CryptoError("decrypt_failed")
    subkey = _hchacha20(key, nonce[:16])
    nonce12 = b"\x00\x00\x00\x00" + nonce[16:]
    poly_key = _chacha_block(subkey, 0, nonce12)[:32]
    mac_input = aad + _pad16(aad) + ciphertext + _pad16(ciphertext) \
        + len(aad).to_bytes(8, "little") \
        + len(ciphertext).to_bytes(8, "little")
    expected = _poly1305(mac_input, poly_key)
    if not hmac.compare_digest(expected, tag):
        raise CryptoError("decrypt_failed")
    return _chacha_xor(subkey, nonce12, ciphertext)


def is_envelope(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def wrap_key_path(secrets):
    return Path(secrets) / KEY_NAME


def _too_open(path, want):
    try:
        mode = stat.S_IMODE(os.lstat(str(path)).st_mode)
    except OSError:
        return True
    return mode != want or bool(mode & OPEN_MASK)


def load_wrap_key(secrets, create=False):
    path = wrap_key_path(secrets)
    if path.is_file():
        if _too_open(path, FILE_MODE):
            raise CryptoError("perm_too_open")
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise CryptoError("wrap_key_invalid")
        return key
    if not create:
        raise CryptoError("wrap_key_missing")
    secrets = Path(secrets)
    secrets.mkdir(parents=True, exist_ok=True)
    os.chmod(str(secrets), DIR_MODE)
    key = os.urandom(KEY_BYTES)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        os.write(fd, key)
        os.fchmod(fd, FILE_MODE)
    finally:
        os.close(fd)
    return key


def encrypt_value(plaintext, name, secrets):
    if not isinstance(plaintext, str) or plaintext == "":
        raise CryptoError("empty")
    key = load_wrap_key(secrets, create=True)
    aad = ("aib-secret/%s" % name).encode("utf-8")
    nonce, ciphertext, tag = _encrypt_bytes(
        key, plaintext.encode("utf-8"), aad)
    blob = nonce + tag + ciphertext
    return PREFIX + base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def decrypt_value(envelope, name, secrets):
    if not is_envelope(envelope):
        return envelope
    key = load_wrap_key(secrets, create=False)
    raw = envelope[len(PREFIX):]
    pad = "=" * ((4 - len(raw) % 4) % 4)
    try:
        blob = base64.urlsafe_b64decode(raw + pad)
    except (ValueError, TypeError) as err:
        raise CryptoError("decrypt_failed") from err
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise CryptoError("decrypt_failed")
    nonce = blob[:NONCE_BYTES]
    tag = blob[NONCE_BYTES:NONCE_BYTES + TAG_BYTES]
    ciphertext = blob[NONCE_BYTES + TAG_BYTES:]
    aad = ("aib-secret/%s" % name).encode("utf-8")
    plain = _decrypt_bytes(key, nonce, ciphertext, tag, aad)
    return plain.decode("utf-8")
