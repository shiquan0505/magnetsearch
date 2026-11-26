#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置加载 / 密钥文件管理。

一份 config.json 同时被三处使用：
  * 服务端：价格、试用天数、回调地址、支付渠道
  * 客户端构建脚本：把 public_base / 公钥 / 价格 / 试用天数 编进 dylib
  * 管理 CLI

首次启动会自动生成 Ed25519 密钥对（keys/ed25519.key），公钥会打印出来，
构建客户端时用 `build_client.sh` 自动从私钥推导，不用手抄。
"""

import json
import os

import ed25519

DEFAULTS = {
    'product': {
        'id': 'magnetsearch',
        'name': 'MagnetSearch 终身授权',
        'price_cents': 2900,
        'currency': 'CNY',
        'trial_days': 7,
    },
    'server': {
        'listen_host': '0.0.0.0',
        'listen_port': 8899,
        'public_base': 'http://127.0.0.1:8899',
        'db_path': 'license.db',
        'admin_token': '',
        'recheck_hours': 6,
        'order_ttl_seconds': 900,
        'rate_limit_per_min': 600,
    },
    'signing': {
        'private_key_file': 'keys/ed25519.key',
        'key_id': 'k1',
    },
    'payment': {
        'provider': 'manual',
        'manual': {'qr_image': '', 'contact': '客服微信：your-wechat', 'alipay': ''},
        'epay': {'api_base': '', 'pid': '', 'key': '', 'pay_type': 'alipay',
                 'sitename': 'MagnetSearch', 'device': 'pc'},
        'vmq': {'monitor_base': 'http://127.0.0.1:8080', 'key': '', 'pay_type': 2},
        'generic': {'key': ''},
    },
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    path = path or os.environ.get('MS_LICENSE_CONFIG') or default_config_path()
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            cfg = _deep_merge(cfg, json.load(f))
    cfg['_path'] = path
    cfg['_base_dir'] = os.path.dirname(os.path.abspath(path))
    return cfg


def default_config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')


def save(cfg, path=None):
    path = path or cfg.get('_path') or default_config_path()
    data = {k: v for k, v in cfg.items() if not k.startswith('_')}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def abspath(cfg, p):
    if os.path.isabs(p):
        return p
    return os.path.join(cfg['_base_dir'], p)


def public_key_for_client(cfg):
    """客户端构建只需要公钥：优先从私钥推导，其次用配置里写死的 public_key_hex。"""
    key_file = abspath(cfg, cfg['signing']['private_key_file'])
    if os.path.exists(key_file):
        return ensure_keys(cfg)[1]
    pinned = (cfg['signing'].get('public_key_hex') or '').strip()
    if pinned:
        if len(pinned) != 64:
            raise SystemExit('signing.public_key_hex 长度不对（应为 64 个十六进制字符）')
        return pinned
    raise SystemExit('既没有私钥文件 %s，也没配置 signing.public_key_hex；\n'
                     '请在服务端生成密钥后把公钥填进 config.json → signing.public_key_hex' % key_file)


def ensure_keys(cfg):
    """返回 (sk_hex, pk_hex)。没有密钥文件就生成一份 0600 的新钥。"""
    key_file = abspath(cfg, cfg['signing']['private_key_file'])
    if os.path.exists(key_file):
        with open(key_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                sk_hex = data['private_key_hex']
            except Exception:
                f.seek(0)
                sk_hex = f.read().strip()
        sk = bytes.fromhex(sk_hex)
        return sk_hex, ed25519.public_key(sk).hex()

    if (cfg['signing'].get('public_key_hex') or '').strip():
        raise SystemExit(
            '配置里写死了 signing.public_key_hex（说明私钥在别处，比如云服务器），\n'
            '但本机没有私钥文件 %s —— 本机不能再签发授权。\n'
            '要本地开发请用 config.dev.json（自带私钥）：'
            ' MS_LICENSE_CONFIG=licensing/config.dev.json' % key_file)

    os.makedirs(os.path.dirname(key_file) or '.', exist_ok=True)
    sk, pk = ed25519.keypair()
    with open(key_file, 'w', encoding='utf-8') as f:
        json.dump({
            'algorithm': 'ed25519',
            'key_id': cfg['signing'].get('key_id', 'k1'),
            'created_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime()),
            'private_key_hex': sk.hex(),
            'public_key_hex': pk.hex(),
        }, f, indent=2)
    os.chmod(key_file, 0o600)
    return sk.hex(), pk.hex()
