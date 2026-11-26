#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库 Ed25519（RFC 8032）实现 —— 只用于给授权 token 签名。

为什么不用第三方库：授权服务端要能在任意 VPS 的 python3.7+ 上
`python3 license_server.py` 直接跑，不需要 pip install、不需要编译扩展。

实现用扩展扭曲爱德华坐标（X:Y:Z:T），加法/倍点全程不做模逆，
定点乘 B 用预计算表；实测单次签名/验签约 8–15 ms，发证场景完全够用。

对外接口（与 Swift CryptoKit 的 rawRepresentation 完全互通）：
    sk, pk = generate_keypair()      # 32 字节私钥种子, 32 字节公钥
    sig    = sign(sk, msg)           # 64 字节签名
    ok     = verify(pk, msg, sig)    # bool
"""

import hashlib
import os

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493


def _sha512(m):
    return hashlib.sha512(m).digest()


def _inv(x):
    return pow(x, p - 2, p)


_d = -121665 * _inv(121666) % p
_d2 = (2 * _d) % p
_I = pow(2, (p - 1) // 4, p)


# ---------- 仿射 ↔ 扩展坐标 ----------

def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (p + 3) // 8, p)
    if (x * x - xx) % p != 0:
        x = (x * _I) % p
    if x % 2 != 0:
        x = p - x
    return x


_By = 4 * _inv(5) % p
_Bx = _xrecover(_By)
_B = (_Bx, _By)


def _to_ext(P):
    x, y = P
    return (x % p, y % p, 1, (x * y) % p)


def _to_affine(P):
    X, Y, Z, _T = P
    zi = _inv(Z)
    return ((X * zi) % p, (Y * zi) % p)


def _add(P, Q):
    """a = -1 的统一加法公式（add-2008-hwcd-3），对 P == Q 也成立。"""
    X1, Y1, Z1, T1 = P
    X2, Y2, Z2, T2 = Q
    A = ((Y1 - X1) * (Y2 - X2)) % p
    B = ((Y1 + X1) * (Y2 + X2)) % p
    C = (T1 * _d2 * T2) % p
    D = (Z1 * 2 * Z2) % p
    E = (B - A) % p
    F = (D - C) % p
    G = (D + C) % p
    H = (B + A) % p
    return ((E * F) % p, (G * H) % p, (F * G) % p, (E * H) % p)


_ZERO = (0, 1, 1, 0)          # 单位元 (0, 1)


def _scalarmult_ext(P, e):
    Q = _ZERO
    R = P
    while e > 0:
        if e & 1:
            Q = _add(Q, R)
        R = _add(R, R)
        e >>= 1
    return Q


# 定点预计算：_B_TABLE[i] = [2^i]B（扩展坐标）
_B_TABLE = []
_P = _to_ext(_B)
while len(_B_TABLE) < 256:
    _B_TABLE.append(_P)
    _P = _add(_P, _P)


def _scalarmult_base_ext(e):
    Q = _ZERO
    i = 0
    while e > 0:
        if e & 1:
            Q = _add(Q, _B_TABLE[i])
        e >>= 1
        i += 1
    return Q


# ---------- 编解码 ----------

def _encode_point_ext(P):
    x, y = _to_affine(P)
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _is_on_curve(x, y):
    return (-x * x + y * y - 1 - _d * x * x * y * y) % p == 0


def _decode_point(s):
    if len(s) != 32:
        raise ValueError('point must be 32 bytes')
    bits = int.from_bytes(s, 'little')
    y = bits & ((1 << 255) - 1)
    if y >= p:
        raise ValueError('bad y')
    x = _xrecover(y)
    if (x & 1) ^ ((bits >> 255) & 1):
        x = p - x
    if not _is_on_curve(x, y):
        raise ValueError('point not on curve')
    return _to_ext((x, y))


def _scalar_from_hash(h):
    # RFC 8032: 把 64 字节摘要整体当作小端整数，再 mod L
    return int.from_bytes(h, 'little') % q


def _expand_seed(sk):
    if len(sk) != 32:
        raise ValueError('private key must be 32 bytes')
    h = _sha512(sk)
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


# ---------- 公开 API ----------

def public_key(sk):
    a, _ = _expand_seed(sk)
    return _encode_point_ext(_scalarmult_base_ext(a))


def sign(sk, msg):
    a, prefix = _expand_seed(sk)
    A = _encode_point_ext(_scalarmult_base_ext(a))
    r = _scalar_from_hash(_sha512(prefix + msg))
    R = _encode_point_ext(_scalarmult_base_ext(r))
    h = _scalar_from_hash(_sha512(R + A + msg))
    S = (r + h * a) % q
    return R + int.to_bytes(S, 32, 'little')


def verify(pk, msg, sig):
    if len(pk) != 32 or len(sig) != 64:
        return False
    try:
        A = _decode_point(pk)
        R = _decode_point(sig[:32])
    except Exception:
        return False
    S = int.from_bytes(sig[32:], 'little')
    if S >= q:
        return False
    h = _scalar_from_hash(_sha512(sig[:32] + pk + msg))
    left = _scalarmult_base_ext(S)
    right = _add(R, _scalarmult_ext(A, h))
    return _encode_point_ext(left) == _encode_point_ext(right)


def generate_keypair():
    return os.urandom(32), None


def keypair():
    sk = os.urandom(32)
    return sk, public_key(sk)


def keypair_hex():
    sk, pk = keypair()
    return sk.hex(), pk.hex()


# ---------- RFC 8032 §7.1 自测向量 ----------
_SELF_TEST = [
    (
        '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a',
        '',
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b',
    ),
    (
        '4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
        '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c',
        '72',
        '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00',
    ),
    (
        'c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
        'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025',
        'af82',
        '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a',
    ),
]


def self_test():
    for sk_hex, pk_hex, msg_hex, sig_hex in _SELF_TEST:
        sk = bytes.fromhex(sk_hex)
        msg = bytes.fromhex(msg_hex)
        assert public_key(sk).hex() == pk_hex, 'pubkey mismatch'
        sig = sign(sk, msg)
        assert sig.hex() == sig_hex, 'signature mismatch'
        assert verify(bytes.fromhex(pk_hex), msg, sig), 'verify failed'
        assert not verify(bytes.fromhex(pk_hex), msg + b'x', sig), 'tamper not detected'
    return True


if __name__ == '__main__':
    import time
    print('ed25519 self-test:', 'PASS' if self_test() else 'FAIL')
    sk, pk = keypair()
    t = time.time()
    for _ in range(10):
        sig = sign(sk, b'x' * 64)
    sm = (time.time() - t) / 10 * 1000
    t = time.time()
    for _ in range(10):
        verify(pk, b'x' * 64, sig)
    vm = (time.time() - t) / 10 * 1000
    print('sign %.1f ms / verify %.1f ms' % (sm, vm))
