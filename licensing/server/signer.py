#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""授权凭证（license token）签发。

格式：  MS1.<base64url(payload_json)>.<base64url(ed25519_sig)>
签名对象：ASCII 字符串 "<header>.<payload>" 的原始字节

客户端只内嵌**公钥**，所以：
  * 拿到公共客户端的二进制解不出私钥、伪造不了 token
  * 断网也能验签 → 买断后可永久离线使用

payload 字段：
  v     版本
  typ   trial | license
  app   bundle id（防止 token 拿去给别的软件用）
  did   设备指纹（防止把 token 拷给别人）
  plan  lifetime
  iat   签发时间（unix 秒）
  exp   到期时间，null = 永久
  ts/te trial 起点/终点
  jti   token 唯一 id（退款吊销用）
  kid   密钥 id（换钥时用）
"""

import json
import time

import ed25519

APP_ID = 'com.magnetsearch.app'
VERSION = 1


def _b64e(raw):
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _b64d(s):
    import base64
    s = s.encode()
    s += b'=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


class Signer:
    def __init__(self, sk_hex, key_id='k1'):
        self.sk = bytes.fromhex(sk_hex)
        self.pk = ed25519.public_key(self.sk)
        self.key_id = key_id

    @property
    def public_key_hex(self):
        return self.pk.hex()

    def _make(self, payload):
        payload = dict(payload)
        payload.setdefault('v', VERSION)
        payload.setdefault('app', APP_ID)
        payload.setdefault('kid', self.key_id)
        header = 'MS1'
        body = header + '.' + _b64e(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())
        sig = ed25519.sign(self.sk, body.encode())
        return body + '.' + _b64e(sig)

    # ---- 签发 ----
    def trial_token(self, device_id, trial_start, trial_end):
        return self._make({
            'typ': 'trial', 'did': device_id,
            'ts': int(trial_start), 'te': int(trial_end), 'iat': int(time.time()),
        })

    def license_token(self, device_id, plan='lifetime', expires_at=None, iat=None, jti=None):
        return self._make({
            'typ': 'license', 'did': device_id, 'plan': plan,
            'iat': int(iat or time.time()),
            'exp': int(expires_at) if expires_at else None,
            'jti': jti or ('%s-%d' % (device_id[:8], int(time.time()))),
        })

    # ---- 校验（服务端自检 / 管理工具用）----
    def verify(self, token):
        try:
            head, body_b64, sig_b64 = token.split('.')
        except ValueError:
            raise ValueError('malformed token')
        if head != 'MS1':
            raise ValueError('bad header')
        signed = (head + '.' + body_b64).encode()
        if not ed25519.verify(self.pk, signed, _b64d(sig_b64)):
            raise ValueError('bad signature')
        payload = json.loads(_b64d(body_b64))
        if payload.get('app') != APP_ID:
            raise ValueError('app mismatch')
        if payload.get('exp') and payload['exp'] < int(time.time()):
            raise ValueError('token expired')
        return payload


def verify_with_pubkey(pubkey_hex, token):
    """只给公钥的离线验签（等价于客户端逻辑，自测用）。"""
    head, body_b64, sig_b64 = token.split('.')
    if not ed25519.verify(bytes.fromhex(pubkey_hex), (head + '.' + body_b64).encode(), _b64d(sig_b64)):
        raise ValueError('bad signature')
    return json.loads(_b64d(body_b64))
