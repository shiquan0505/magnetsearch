#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人收款码对账：把支付宝/微信导出的账单 CSV 与待支付订单按「金额 + 时间」自动匹配补单。

为什么需要它：个人收款码没有商户回调，能自动化的只有对账这一环。
配合服务端的「唯一金额尾数」功能（¥29.07 / ¥29.13 …），账单里一笔到账就能唯一定位一单。

用法：
    # 支付宝 App → 我的 → 账单 → 右上角 … → 开具交易流水证明 / 导出账单（CSV）
    # 微信 → 我 → 服务 → 钱包 → 账单 → 常见问题 → 下载账单（CSV，解压得到 csv）

    python3 tools/reconcile.py alipay_bill.csv                 # 只看匹配结果（默认 dry-run）
    python3 tools/reconcile.py alipay_bill.csv --apply         # 真的补单发证
    python3 tools/reconcile.py wechat_bill.csv --apply --config licensing/config.json
    python3 tools/reconcile.py bill.csv --window-hours 72 --min-amount 29

匹配规则：
    * 只取「收入」方向、状态成功的记录
    * 金额（分）与待支付订单完全一致
    * 到账时间落在订单创建时间之前 10 分钟 ~ 之后 window 小时之内
    * 同一金额多单时：时间最近者胜；仍并列则跳过并提示（交人工）

退出码：0 正常（含 0 匹配），2 有歧义需人工。
"""

import argparse
import csv
import io
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))

import config as cfg_mod   # noqa: E402
from store import Store, now  # noqa: E402


def read_text(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'gb18030', 'utf-16', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def find_header(lines):
    """账单前面通常有若干行说明，找真正的表头行。"""
    for i, line in enumerate(lines):
        if ('交易时间' in line or '创建时间' in line or '付款时间' in line) and ('金额' in line):
            return i
    return 0


def parse_amount(s):
    s = (s or '').strip().replace('¥', '').replace('￥', '').replace(',', '').replace('"', '')
    if not s:
        return None
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def parse_time(s):
    s = (s or '').strip().strip('"')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M',
                '%Y-%m-%d'):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def load_records(path):
    """归一化成 [{ts, amount_cents, memo}]，兼容支付宝/微信两种表头。"""
    text = read_text(path)
    lines = text.splitlines()
    start = find_header(lines)
    sample = ', '.join(lines[start:start + 3])
    reader = csv.DictReader(io.StringIO('\n'.join(lines[start:])))
    fields = [f.strip().lstrip('\ufeff') for f in (reader.fieldnames or [])]

    def pick(*names):
        for n in names:
            for f in fields:
                if n in f:
                    return f
        return None

    f_time = pick('交易时间', '创建时间', '付款时间', '交易创建时间')
    f_amount = pick('金额', '金额(元)', '订单金额')
    f_dir = pick('收/支', '收支类型', '资金方向')
    f_status = pick('交易状态', '当前状态')
    f_memo = pick('商品说明', '商品', '备注', '交易对方')

    if not f_time or not f_amount:
        raise SystemExit('无法识别账单表头，前几行是：\n%s\n（需要包含「交易时间」和「金额」列）' % sample)

    out = []
    for row in reader:
        row = {(k or '').strip().lstrip('\ufeff'): v for k, v in row.items()}
        ts = parse_time(row.get(f_time))
        cents = parse_amount(row.get(f_amount))
        if ts is None or cents is None or cents <= 0:
            continue
        direction = (row.get(f_dir) or '').strip()
        if direction and direction not in ('收入', '收', '入', 'in', 'IN', '收款'):
            continue                      # 支出/转账出去的不算
        status = (row.get(f_status) or '').strip()
        if status and status not in ('交易成功', '支付成功', '已存入零钱', '已收钱', '成功', '已完成',
                                     '对方已收钱', '已到账'):
            continue
        out.append({'ts': ts, 'amount_cents': cents, 'memo': (row.get(f_memo) or '').strip()})
    return out, fields


def main():
    ap = argparse.ArgumentParser(description='个人收款码账单对账 → 自动补单')
    ap.add_argument('bill', help='支付宝/微信导出的账单 CSV')
    ap.add_argument('--config', default=None)
    ap.add_argument('--apply', action='store_true', help='真的补单发证（默认只看）')
    ap.add_argument('--window-hours', type=int, default=48,
                    help='到账时间窗（相对订单创建时间，默认 48 小时）')
    ap.add_argument('--min-amount', type=int, default=0, help='只处理不低于该金额（元）的记录')
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    store = Store(cfg_mod.abspath(cfg, cfg['server']['db_path']))
    records, fields = load_records(args.bill)
    print('账单：%s（识别到 %d 条有效收款，表头：%s）'
          % (args.bill, len(records), ', '.join(fields[:6])))

    lo = args.window_hours * 3600
    matched, ambiguous, unmatched = [], [], []
    for r in records:
        if r['amount_cents'] < args.min_amount * 100:
            continue
        cands = store.find_pending_by_amount(r['amount_cents'], window_seconds=7 * 86400)
        cands = [o for o in cands if o['created_at'] - 600 <= r['ts'] <= o['created_at'] + lo]
        if not cands:
            unmatched.append(r)
        elif len(cands) == 1:
            matched.append((r, cands[0]))
        else:
            cands.sort(key=lambda o: abs(o['created_at'] - r['ts']))
            if abs(cands[0]['created_at'] - r['ts']) < abs(cands[1]['created_at'] - r['ts']) - 600:
                matched.append((r, cands[0]))
            else:
                ambiguous.append((r, cands))

    for r, o in matched:
        print('  ✅ %s  ¥%.2f  →  订单 %s（%s，设备 %s）'
              % (datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M'), r['amount_cents'] / 100.0,
                 o['order_id'], datetime.fromtimestamp(o['created_at']).strftime('%m-%d %H:%M'),
                 o['device_id'][:14]))
        if args.apply:
            updated, why = store.mark_paid(o['order_id'], 'reconcile', r['memo'])
            if why == 'ok':
                store.grant_license(o['device_id'], 'lifetime', None, o['order_id'], 'reconcile')
                print('     → 已补单并发证')
            else:
                print('     → 未处理：%s' % why)

    for r, cands in ambiguous:
        print('  ⚠️  ¥%.2f 匹配到多单，跳过（人工处理）：%s'
              % (r['amount_cents'] / 100.0, ', '.join(o['order_id'] for o in cands)))

    if unmatched:
        print('  ·  %d 条收款没有对应待支付订单（可能是已处理/非本系统收款）' % len(unmatched))

    print('\n汇总：匹配 %d，歧义 %d，未匹配 %d %s'
          % (len(matched), len(ambiguous), len(unmatched),
             '（已写入数据库）' if args.apply else '（dry-run，加 --apply 才写入）'))
    return 2 if ambiguous else 0


if __name__ == '__main__':
    sys.exit(main())
