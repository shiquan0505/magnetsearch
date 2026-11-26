#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""授权服务管理 CLI。

    python3 admin.py init                     # 生成 config.json + Ed25519 密钥，打印公钥
    python3 admin.py pubkey                   # 显示公钥（构建客户端要用）
    python3 admin.py serve                    # 前台启动授权服务
    python3 admin.py stats                    # 概览
    python3 admin.py devices [--q 关键词]      # 设备列表
    python3 admin.py orders [--status paid]   # 订单列表
    python3 admin.py grant --device DID [--days 30]   # 手动开授权（无 --days = 终身）
    python3 admin.py revoke --device DID      # 吊销
    python3 admin.py block --device DID [--unblock]  # 封禁设备（试用也拦）
    python3 admin.py token --device DID       # 打印该设备的 license token
    python3 admin.py verify --token TOKEN     # 用公钥验签（排查客户端报错）
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod
from signer import Signer, verify_with_pubkey
from store import Store, now


def load_all(args):
    cfg = cfg_mod.load(getattr(args, 'config', None))
    store = Store(cfg_mod.abspath(cfg, cfg['server']['db_path']))
    return cfg, store


def cmd_init(args):
    path = args.config or os.environ.get('MS_LICENSE_CONFIG') or cfg_mod.default_config_path()
    if os.path.exists(path) and not args.force:
        print('配置已存在：%s（用 --force 覆盖）' % path)
    else:
        cfg = cfg_mod.load(None)
        cfg['_base_dir'] = os.path.dirname(os.path.abspath(path))
        cfg['_path'] = path
        cfg['server']['admin_token'] = cfg['server']['admin_token'] or __import__('secrets').token_hex(16)
        cfg_mod.save(cfg, path)
        print('已写入 %s' % path)
    cfg = cfg_mod.load(path)
    sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)
    print('私钥文件：%s' % cfg_mod.abspath(cfg, cfg['signing']['private_key_file']))
    print('公钥(hex)：%s' % pk_hex)
    print('管理 token：%s' % cfg['server']['admin_token'])
    print('\n下一步：\n  1) 改 config.json 里的 server.public_base 为你的公网地址')
    print('  2) 填 payment.* 支付渠道参数（provider 可选 manual / vmq / epay / generic）')
    print('  3) python3 admin.py serve')


def cmd_pubkey(args):
    cfg = cfg_mod.load(args.config)
    sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)
    print(pk_hex)


def cmd_serve(args):
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, 'license_server.py')]
    if args.config:
        cmd += ['--config', args.config]
    if args.port:
        cmd += ['--port', str(args.port)]
    os.execv(sys.executable, cmd)


def cmd_stats(args):
    cfg, store = load_all(args)
    s = store.stats()
    s['revenue_yuan'] = '%.2f' % (s['revenue_cents'] / 100.0)
    for k, v in s.items():
        print('%-16s %s' % (k, v))


def cmd_devices(args):
    cfg, store = load_all(args)
    ts = now()
    print('%-34s %-10s %-19s %-8s %s' % ('device_id', 'state', 'last_seen', 'ver', 'note'))
    for d in store.list_devices(q=args.q or '', limit=args.limit):
        lic = store.get_license(d['device_id'])
        if d['blocked']:
            state = 'blocked'
        elif lic and not lic['revoked']:
            state = 'lifetime' if not lic['expires_at'] else 'until %s' % time_s(lic['expires_at'])
        elif ts >= d['trial_start'] + d['trial_days'] * 86400:
            state = 'expired'
        else:
            state = 'trial'
        print('%-34s %-10s %-19s %-8s %s' % (d['device_id'], state, time_s(d['last_seen']),
                                             d['app_version'], d['note']))


def cmd_orders(args):
    cfg, store = load_all(args)
    print('%-14s %-30s %-8s %-9s %-19s %s' % ('order', 'device', 'amount', 'status', 'created', 'paid'))
    for o in store.list_orders(status=args.status or '', limit=args.limit):
        print('%-14s %-30s %-8s %-9s %-19s %s' % (
            o['order_id'], o['device_id'][:30], '%.2f' % (o['amount_cents'] / 100.0),
            o['status'], time_s(o['created_at']), time_s(o['paid_at'])))


def cmd_claims(args):
    cfg, store = load_all(args)
    print('%-8s %-19s %-9s %-30s %-16s %s' % ('code', 'created', 'state', 'device', 'ip', 'used'))
    for c in store.list_claims(args.limit):
        state = 'used' if c['used_at'] else ('expired' if c['expires_at'] < now() else 'unused')
        print('%-8s %-19s %-9s %-30s %-16s %s' % (
            c['code'], time_s(c['created_at']), state, (c['device_id'] or '-')[:30],
            c['ip'] or '-', time_s(c['used_at'])))


def cmd_grant(args):
    cfg, store = load_all(args)
    did = args.device
    store.touch_device(did, int(cfg['product']['trial_days']))
    exp = now() + args.days * 86400 if args.days else None
    lic = store.grant_license(did, args.plan, exp, 'manual-cli', 'cli')
    sk_hex, _pk = cfg_mod.ensure_keys(cfg)
    signer = Signer(sk_hex, cfg['signing'].get('key_id', 'k1'))
    tok = signer.license_token(did, lic['plan'], lic['expires_at'])
    print('已授权 %s  plan=%s  expires=%s' % (did, lic['plan'], time_s(lic['expires_at']) or '终身'))
    print('license_token:\n%s' % tok)


def cmd_pay(args):
    """个人收款码补单：按金额找订单（你看到支付宝/微信到账多少就输多少）。"""
    cfg, store = load_all(args)
    if args.order:
        order = store.get_order(args.order)
        if order is None:
            print('订单不存在：%s' % args.order)
            sys.exit(1)
        matched = [order]
    else:
        cents = int(round(float(args.amount) * 100))
        matched = store.find_pending_by_amount(cents, window_seconds=args.window_days * 86400)
        if not matched:
            print('没有金额为 %.2f 的待支付订单（时间窗 %s 天）' % (args.amount, args.window_days))
            sys.exit(1)
        if len(matched) > 1 and not args.all:
            print('金额 %.2f 匹配到 %d 单，请指定订单号或加 --all：' % (args.amount, len(matched)))
            for o in matched:
                print('   %s  %s  %s' % (o['order_id'], time_s(o['created_at']),
                                         o['device_id'][:16]))
            sys.exit(2)

    for o in matched:
        if o['status'] == 'paid':
            print('已是已支付状态：%s' % o['order_id'])
            continue
        if args.dry_run:
            print('[dry-run] 将补单 %s  金额 %.2f  设备 %s' %
                  (o['order_id'], o['amount_cents'] / 100.0, o['device_id'][:16]))
            continue
        updated, why = store.mark_paid(o['order_id'], 'manual-cli', 'admin.py pay')
        if why == 'ok':
            store.grant_license(o['device_id'], 'lifetime', None, o['order_id'], 'cli')
            print('✅ 已补单并开通终身授权：%s  设备 %s' % (o['order_id'], o['device_id'][:16]))
        else:
            print('订单 %s 未处理：%s' % (o['order_id'], why))


def cmd_revoke(args):
    cfg, store = load_all(args)
    store.revoke_license(args.device, not args.restore)
    print('%s %s' % ('已恢复' if args.restore else '已吊销', args.device))


def cmd_block(args):
    cfg, store = load_all(args)
    store.touch_device(args.device, int(cfg['product']['trial_days']))
    store.set_blocked(args.device, not args.unblock, args.note)
    print('%s %s' % ('已解封' if args.unblock else '已封禁', args.device))


def cmd_token(args):
    cfg = cfg_mod.load(args.config)
    sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)
    signer = Signer(sk_hex, cfg['signing'].get('key_id', 'k1'))
    print(signer.license_token(args.device, args.plan, None))


def cmd_verify(args):
    cfg = cfg_mod.load(args.config)
    _sk, pk_hex = cfg_mod.ensure_keys(cfg)
    try:
        payload = verify_with_pubkey(pk_hex, args.token.strip())
    except Exception as e:
        print('验签失败：%s' % e)
        sys.exit(1)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def time_s(t):
    if not t:
        return ''
    import time
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))


def _keypair(cfg):
    sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)
    return sk_hex, pk_hex


def main(argv=None):
    ap = argparse.ArgumentParser(description='MagnetSearch 授权管理')
    ap.add_argument('--config', default=None)
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('init'); p.add_argument('--force', action='store_true'); p.set_defaults(fn=cmd_init)
    p = sub.add_parser('pubkey'); p.set_defaults(fn=cmd_pubkey)
    p = sub.add_parser('serve'); p.add_argument('--port', type=int); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser('stats'); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser('devices'); p.add_argument('--q'); p.add_argument('--limit', type=int, default=50); p.set_defaults(fn=cmd_devices)
    p = sub.add_parser('orders'); p.add_argument('--status'); p.add_argument('--limit', type=int, default=50); p.set_defaults(fn=cmd_orders)
    p = sub.add_parser('claims', help='授权码列表（付费墙模式）')
    p.add_argument('--limit', type=int, default=50); p.set_defaults(fn=cmd_claims)
    p = sub.add_parser('grant'); p.add_argument('--device', required=True); p.add_argument('--days', type=int)
    p.add_argument('--plan', default='lifetime'); p.set_defaults(fn=cmd_grant)
    p = sub.add_parser('pay', help='个人收款码补单：按金额或订单号')
    p.add_argument('--amount', help='到账金额，例如 29.07')
    p.add_argument('--order', help='直接指定订单号')
    p.add_argument('--window-days', type=int, default=7)
    p.add_argument('--all', action='store_true', help='金额匹配到多单时全部补单')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=cmd_pay)
    p = sub.add_parser('revoke'); p.add_argument('--device', required=True); p.add_argument('--restore', action='store_true'); p.set_defaults(fn=cmd_revoke)
    p = sub.add_parser('block'); p.add_argument('--device', required=True); p.add_argument('--unblock', action='store_true')
    p.add_argument('--note'); p.set_defaults(fn=cmd_block)
    p = sub.add_parser('token'); p.add_argument('--device', required=True); p.add_argument('--plan', default='lifetime'); p.set_defaults(fn=cmd_token)
    p = sub.add_parser('verify'); p.add_argument('--token', required=True); p.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    if not getattr(args, 'fn', None):
        ap.print_help()
        return
    args.fn(args)


if __name__ == '__main__':
    main()
