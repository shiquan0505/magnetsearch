#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自测：起一个临时授权服务，走完「试用 → 到期 → 下单 → 免签回调 → 发证 → 验签」。

    python3 tools/selfcheck.py                 # 默认 generic 渠道（本地伪造聚合网关回调）
    python3 tools/selfcheck.py --provider vmq   # 顺带起一个假的 V免签监控端
    python3 tools/selfcheck.py --provider epay  # 易支付协议回调
    python3 tools/selfcheck.py --provider manual# 人工渠道走后台补单
    python3 tools/selfcheck.py --keep           # 保留临时目录观察 db/日志

覆盖点：
  1. 首次 session → 7 天试用（trial），重复调用不重置
  2. trial_start 拨回 8 天 → trial_expired
  3. 下单 → 二维码内容，金额由服务端决定
  4. 错误签名 / 金额篡改的回调必须被拒
  5. 正确回调 → 订单 paid + 自动发终身授权，重放幂等
  6. session → licensed，license_token 用**公钥**离线验签通过，篡改即失败
  7. 其它设备不受影响；已授权设备再下单直接告知已授权
  8. 管理接口鉴权 + 统计 + 补单
"""

import argparse
import hashlib
import hmac
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER_DIR = os.path.join(ROOT, 'server')
sys.path.insert(0, SERVER_DIR)

import config as cfg_mod          # noqa: E402
from pycompat import ThreadingHTTPServer  # noqa: E402
from signer import verify_with_pubkey  # noqa: E402

OK = '\033[32m✓\033[0m'
BAD = '\033[31m✗\033[0m'
_fails = []


def check(cond, msg, extra=''):
    print(('  %s %s' % (OK if cond else BAD, msg)) + (('   ' + repr(extra)) if extra and not cond else ''))
    if not cond:
        _fails.append(msg)
    return cond


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(url, method='GET', data=None, headers=None, form=False):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
        else:
            body = json.dumps(data).encode()
            headers.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode('utf-8', 'replace')
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


# ---------------------------------------------------------------- 假 V免签监控端
class FakeMonitor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
        if form.get('payId'):            # 记录监控端收到的下单请求，便于断言
            self.server.seen.append(form)
        body = json.dumps({'code': 1, 'msg': '',
                           'data': {'payId': form.get('payId', ''),
                                    'payUrl': 'alipay://qr/fake-%s' % form.get('payId', 'x'),
                                    'qrCode': 'https://qr.alipay.com/fake-%s' % form.get('payId', 'x')}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake_monitor():
    port = free_port()
    httpd = ThreadingHTTPServer(('127.0.0.1', port), FakeMonitor)
    httpd.seen = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d' % port


# ---------------------------------------------------------------- 回调参数构造
def notify_params(provider, order_id, amount_cents, cfg):
    money = '%.2f' % (amount_cents / 100.0)
    ts = str(int(time.time()))
    if provider == 'generic':
        params = {'order_id': order_id, 'amount_cents': str(amount_cents), 'trade_no': 'T' + ts, 'ts': ts}
        items = sorted((k, v) for k, v in params.items() if v not in ('', None))
        params['sign'] = hmac.new(cfg['payment']['generic']['key'].encode(),
                                  '&'.join('%s=%s' % (k, v) for k, v in items).encode(),
                                  hashlib.sha256).hexdigest()
        return params
    if provider == 'epay':
        params = {'pid': cfg['payment']['epay']['pid'], 'trade_no': '2024' + ts, 'out_trade_no': order_id,
                  'type': 'alipay', 'name': 'MagnetSearch', 'money': money,
                  'trade_status': 'TRADE_SUCCESS', 'sign_type': 'MD5'}
        items = sorted((k, v) for k, v in params.items() if k not in ('sign', 'sign_type') and v not in ('', None))
        params['sign'] = hashlib.md5(('&'.join('%s=%s' % (k, v) for k, v in items)
                                      + cfg['payment']['epay']['key']).encode()).hexdigest()
        return params
    if provider == 'vmq':
        key = cfg['payment']['vmq']['key']
        params = {'payId': order_id, 'price': money, 'type': '2', 'tradeNo': 'T' + ts}
        params['sign'] = hashlib.md5(('%s%s%s%s' % (money, '2', order_id, key)).encode()).hexdigest()
        return params
    return {'order_id': order_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--provider', default='generic', choices=['generic', 'vmq', 'epay', 'manual'])
    args = ap.parse_args()
    pv = args.provider

    work = tempfile.mkdtemp(prefix='ms-license-test-')
    print('临时目录：%s' % work)
    port = free_port()
    base = 'http://127.0.0.1:%d' % port

    cfg = cfg_mod.load(None)
    # 自测自带密钥：不要继承生产配置里“只写公钥、没有私钥”的设定
    cfg['signing'] = {'private_key_file': 'keys/ed25519.key', 'key_id': 'k1'}
    cfg['_base_dir'] = work
    cfg['_path'] = os.path.join(work, 'config.json')
    cfg['server']['db_path'] = os.path.join(work, 'license.db')
    cfg['server']['public_base'] = base
    cfg['server']['listen_port'] = port
    cfg['server']['admin_token'] = 'test-admin'
    cfg['payment']['provider'] = pv
    cfg['payment']['generic']['key'] = 'gateway-secret'
    cfg['payment']['vmq']['key'] = 'vmq-secret'
    cfg['payment']['vmq']['monitor_base'] = 'http://127.0.0.1:1'
    cfg['payment']['epay']['key'] = 'epay-secret'
    cfg['payment']['epay']['pid'] = '1001'
    cfg['payment']['epay']['api_base'] = base + '/fake-epay'

    monitor = None
    if pv == 'vmq':
        monitor, url = start_fake_monitor()
        cfg['payment']['vmq']['monitor_base'] = url
    cfg_mod.save(cfg, cfg['_path'])
    sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)

    log_path = os.path.join(work, 'server.log')
    log_file = open(log_path, 'w')
    proc = subprocess.Popen([sys.executable, os.path.join(SERVER_DIR, 'license_server.py'),
                             '--config', cfg['_path'], '--log-level', 'INFO'],
                            stdout=log_file, stderr=subprocess.STDOUT, cwd=SERVER_DIR)
    rc = 0
    try:
        for _ in range(80):
            try:
                if http(base + '/healthz')[0] == 200:
                    break
            except Exception:
                time.sleep(0.15)
        else:
            print(BAD, '服务没起来：')
            print(open(log_path).read()[-3000:])
            return 1

        DEV = 'dev-' + hashlib.sha256(b'test-device').hexdigest()[:24]

        print('\n1) 首次会话 → 7 天试用')
        st, r = http(base + '/api/v1/session', 'POST',
                     {'device_id': DEV, 'platform': 'macOS 13.7.8 x86_64', 'app_version': '1.0'})
        d = r['data']
        check(st == 200 and d['state'] == 'trial', 'state=trial', r)
        check(d['trial_days'] == 7 and abs(d['trial_remaining'] - 7 * 86400) < 5, 'trial_remaining≈7d',
              d.get('trial_remaining'))
        check(bool(d.get('trial_token')), '返回 trial_token')
        trial_start = d['trial_start']

        print('2) 再次会话 → trial_start 不重置（删本地文件也重置不了）')
        time.sleep(1.1)
        st, r2 = http(base + '/api/v1/session', 'POST', {'device_id': DEV})
        check(r2['data']['trial_start'] == trial_start, 'trial_start 未变化')

        print('3) trial_start 拨回 8 天 → 判定过期')
        con = sqlite3.connect(cfg['server']['db_path'])
        con.execute('UPDATE devices SET trial_start=? WHERE device_id=?', (int(time.time()) - 8 * 86400, DEV))
        con.commit()
        con.close()
        st, r3 = http(base + '/api/v1/session', 'POST', {'device_id': DEV})
        check(r3['data']['state'] == 'trial_expired', 'state=trial_expired', r3['data']['state'])

        print('4) 下单 → 二维码内容')
        st, ro = http(base + '/api/v1/order/create', 'POST', {'device_id': DEV})
        check(st == 200 and ro.get('code') == 0, '下单成功', ro)
        order = ro.get('data') or {}
        amt = order.get('amount_cents')
        if pv == 'manual':      # 个人收款码会加 1~99 分唯一尾数
            check(2900 <= (amt or 0) <= 2999, '金额 = 2900 分 + 唯一尾数', amt)
        else:
            check(amt == 2900, '金额取服务端配置 2900 分', amt)
        check(bool(order.get('qr_content')), '有二维码内容', order.get('qr_content'))
        oid = order.get('order_id')
        if monitor is not None:
            check(monitor.seen and monitor.seen[-1]['sign'], 'V免签监控端收到了带签名的下单请求',
                  monitor.seen[-1] if monitor.seen else None)
            check('29.00' == monitor.seen[-1]['price'], '传给监控端的价格正确')

        if pv != 'manual':
            print('5) 错误签名的回调被拒')
            bad = notify_params(pv, oid, 2900, cfg)
            bad['sign'] = 'deadbeef'
            st, rb = http(base + '/notify/%s' % pv, 'POST', bad, form=True)
            check(st >= 400, '错误签名被拒 (HTTP %s)' % st, rb)
            st, rq = http(base + '/api/v1/order/status?order_id=%s&device_id=%s' % (oid, DEV))
            check(rq['data']['paid'] is False, '订单仍未支付')

            print('6) 金额篡改的回调被拒')
            tampered = notify_params(pv, oid, 1, cfg)
            st, rb = http(base + '/notify/%s' % pv, 'POST', tampered, form=True)
            check(st >= 400, '金额不符被拒 (HTTP %s)' % st, rb)

            print('7) 正确回调 → 已支付 + 自动发终身授权')
            params = notify_params(pv, oid, 2900, cfg)
            st, rn = http(base + '/notify/%s' % pv, 'POST', params, form=True)
            check(st == 200, '回调应答 200', (st, rn))
        else:
            print('5) 人工渠道：后台补单')
            st, rn = http(base + '/admin/api/order/paid', 'POST', {'order_id': oid},
                          headers={'X-Admin-Token': 'test-admin'})
            check(st == 200 and rn['data']['result'] == 'ok', '补单成功', rn)
            params = None

        st, rq = http(base + '/api/v1/order/status?order_id=%s&device_id=%s' % (oid, DEV))
        check(rq['data']['paid'] is True, 'paid=True')
        check(rq['data'].get('state') == 'licensed', 'state=licensed')

        if params is not None:
            print('8) 重放同一回调 → 幂等')
            st, rn2 = http(base + '/notify/%s' % pv, 'POST', params, form=True)
            check(st == 200, '重放应答 200', (st, rn2))

        print('9) 会话 → licensed + 公钥离线验签')
        st, rs = http(base + '/api/v1/session', 'POST', {'device_id': DEV})
        sd = rs['data']
        check(sd['state'] == 'licensed', 'state=licensed')
        tok = sd.get('license_token') or ''
        check(tok.count('.') == 2, 'token 三段式')
        payload = verify_with_pubkey(pk_hex, tok)
        check(payload['typ'] == 'license' and payload['did'] == DEV and payload['plan'] == 'lifetime',
              'payload 正确', payload)
        check(payload['exp'] is None, 'exp=None（永久）')
        pieces = tok.split('.')
        forged = pieces[0] + '.' + pieces[1] + '.' + ('A' * len(pieces[2]))
        try:
            verify_with_pubkey(pk_hex, forged)
            check(False, '篡改 token 应验签失败')
        except Exception:
            check(True, '篡改 token 验签失败（符合预期）')

        print('10) 其它设备仍走新试用；已授权设备再下单直接告知')
        st, rx = http(base + '/api/v1/session', 'POST', {'device_id': 'dev-otherdevice0000000000'})
        check(rx['data']['state'] == 'trial', '另一台设备=新试用', rx['data']['state'])
        st, rl = http(base + '/api/v1/order/create', 'POST', {'device_id': DEV})
        check(rl['data'].get('already_licensed') is True, 'already_licensed', rl)

        print('11) 管理接口鉴权 / 统计')
        st, _ = http(base + '/admin/api/stats')
        check(st == 401, '无 token → 401', st)
        st, rstat = http(base + '/admin/api/stats', headers={'X-Admin-Token': 'test-admin'})
        check(st == 200 and rstat['data']['orders_paid'] == 1, '统计正确', rstat.get('data'))
        st, rpaid = http(base + '/admin/api/order/paid', 'POST', {'order_id': 'MSnotexist'},
                         headers={'X-Admin-Token': 'test-admin'})
        check(st == 404, '补单不存在的订单 → 404')
        st, rdev = http(base + '/admin/api/devices', headers={'X-Admin-Token': 'test-admin'})
        check(st == 200 and len(rdev['data']) >= 2, '设备列表可查')

        print('12) 页面可访问')
        for path in ('/admin/', '/pay/' + oid, '/pay/done?order_id=' + oid):
            st, html = http(base + path)
            check(st == 200 and len(str(html)) > 150, 'GET %s → 200' % path)

        print('\n审计事件：')
        con = sqlite3.connect(cfg['server']['db_path'])
        for row in con.execute('SELECT ts,kind,device_id,order_id FROM events ORDER BY id DESC LIMIT 8'):
            print('   ', time.strftime('%H:%M:%S', time.localtime(row[0])), row[1],
                  row[2][:14], row[3])
        con.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()
        if monitor is not None:
            monitor.shutdown()
        if args.keep:
            print('\n保留目录：', work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    print()
    if _fails:
        print('%s 失败 %d 项：%s' % (BAD, len(_fails), '; '.join(_fails)))
        rc = 1
    else:
        print('%s 全部通过（provider=%s）' % (OK, pv))
    return rc


if __name__ == '__main__':
    sys.exit(main())
