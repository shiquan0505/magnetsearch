#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把服务端配置（含公钥）编译成 Swift 常量文件。

    python3 licensing/tools/gen_client_config.py
    → licensing/client/LicenseConfig.generated.swift

价格/试用天数/服务端地址只改 config.json，客户端重新构建即可，不用动 Swift 代码。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_DIR = os.path.join(ROOT, 'server')
sys.path.insert(0, SERVER_DIR)

import config as cfg_mod  # noqa: E402

TEMPLATE = """// 本文件由 licensing/tools/gen_client_config.py 自动生成，请勿手改。
// 源：licensing/config.json（价格/试用天数/服务端地址/公钥）
import Foundation

enum LicenseConfig {
    static let serverBase = "{server_base}"
    static let forceServer: String? = {force_server}
    static let publicKeyHex = "{public_key}"
    static let appID = "{app_id}"
    static let trialDays = {trial_days}
    static let priceCents = {price_cents}
    static let currency = "{currency}"
    static let productName = "{product_name}"
    static let recheckHours = {recheck_hours}
    static let keyID = "{key_id}"
    static let gateVersion = 1
}
"""


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'client',
                                                                 'LicenseConfig.generated.swift')
    cfg = cfg_mod.load(cfg_path)
    pk = cfg_mod.public_key_for_client(cfg)
    force = os.environ.get('MS_FORCE_SERVER', '').strip()
    values = {
        'server_base': cfg['server']['public_base'].rstrip('/'),
        'force_server': ('"%s"' % force) if force else 'nil',
        'public_key': pk,
        'app_id': cfg.get('app_id') or 'com.magnetsearch.app',
        'trial_days': str(int(cfg['product']['trial_days'])),
        'price_cents': str(int(cfg['product']['price_cents'])),
        'currency': cfg['product']['currency'],
        'product_name': cfg['product']['name'],
        'recheck_hours': str(int(cfg['server'].get('recheck_hours') or 6)),
        'key_id': cfg['signing'].get('key_id', 'k1'),
    }
    text = TEMPLATE
    for k, v in values.items():
        text = text.replace('{%s}' % k, v)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('已生成 %s' % out_path)
    print('  服务端 %s' % cfg['server']['public_base'])
    print('  公钥   %s' % pk)
    print('  试用 %s 天 / 价格 %s 分 / 校验间隔 %s 小时'
          % (cfg['product']['trial_days'], cfg['product']['price_cents'],
             cfg['server'].get('recheck_hours')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
