#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地假支付网关（模拟「免签聚合 / V免签监控端」），用来零成本走通整条收款链路。

它做两件事：
  1. 实现 V免签监控端的 POST /create 协议 —— 授权服务下单时会调它，拿回一个支付链接
  2. 提供一个网页版“收银台”，点「我已完成付款」就按协议签名回调授权服务

用法：
    # 终端 A：授权服务（config.json 里 provider=vmq，monitor_base=http://127.0.0.1:8080）
    python3 licensing/server/admin.py serve
    # 终端 B：假网关
    python3 licensing/tools/fake_gateway.py --port 8080

然后让 app 试用到期（或 admin.py grant 后 revoke 再等复查），点开二维码里的链接，
在网页上点「我已完成付款」，app 会在 3 秒内自动解锁。

真上线时把这个脚本换成：V免签监控端 App / 易支付 / 你自己的聚合网关，协议不变。
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'server'))

ORDERS = {}       # payId -> {price, type, notify, created}


def sign(price, pay_type, pay_id, key):
    return hashlib.md5(('%s%s%s%s' % (price, pay_type, pay_id, key)).encode()).hexdigest()


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>模拟收银台</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#0b0f14;color:#e6edf3;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#151b23;border:1px solid #232a33;border-radius:16px;padding:28px;width:340px;text-align:center}}
h2{{font-size:18px;margin:0 0 6px}} .m{{color:#8b949e;font-size:13px}}
.amt{{font-size:34px;font-weight:700;margin:16px 0}}
button{{width:100%;border:0;border-radius:10px;padding:12px;font-size:15px;cursor:pointer;
background:#2ea043;color:#fff;margin-top:8px}}
button.gray{{background:#30363d}}
code{{color:#79c0ff;font-size:12px;word-break:break-all}}
</style></head><body><div class="card">
<h2>模拟收银台（本地测试）</h2>
<div class="m">这是 fake_gateway.py 生成的假支付页<br>上线后此处是支付宝/微信的真实收银台</div>
<div class="amt">¥{price}</div>
<div class="m">订单 <code>{pay_id}</code></div>
<button onclick="pay()">我已完成付款</button>
<button class="gray" onclick="location.reload()">刷新</button>
<div class="m" id="msg" style="margin-top:14px"></div>
</div>
<script>
async function pay(){{
  document.getElementById('msg').textContent='正在回调授权服务…';
  const r=await fetch('/pay/{pay_id}/done',{{method:'POST'}});
  const t=await r.text();
  document.getElementById('msg').textContent='回调结果：'+t+'\\n回到 app 点「我已付款，立即刷新」';
  document.getElementById('msg').style.whiteSpace='pre-wrap';
}}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    key = 'vmq-secret'

    def log_message(self, fmt, *a):
        sys.stderr.write('[gateway] %s\n' % (fmt % a))

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n).decode('utf-8', 'replace') if n else ''
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        if path == '/create':
            pay_id = form.get('payId', '')
            price = form.get('price', '0.00')
            ptype = str(form.get('type', '2'))
            expect = sign(price, ptype, pay_id, self.key)
            if form.get('sign', '').lower() != expect:
                return self._send(400, json.dumps({'code': -1, 'msg': 'sign error'}),
                                  'application/json')
            ORDERS[pay_id] = {'price': price, 'type': ptype, 'notify': form.get('notifyUrl', ''),
                              'created': time.time()}
            host = self.headers.get('Host', '127.0.0.1')
            pay_url = 'http://%s/pay/%s' % (host, pay_id)
            body = json.dumps({'code': 1, 'msg': '', 'data': {'payId': pay_id, 'payUrl': pay_url,
                                                             'qrCode': pay_url}})
            return self._send(200, body, 'application/json')

        if path.startswith('/pay/') and path.endswith('/done'):
            pay_id = path.split('/')[2]
            o = ORDERS.get(pay_id)
            if not o:
                return self._send(404, 'order not found', 'text/plain; charset=utf-8')
            price, ptype = o['price'], o['type']
            params = {
                'payId': pay_id, 'price': price, 'type': ptype,
                'tradeNo': 'FAKE' + str(int(time.time())),
                'sign': sign(price, ptype, pay_id, self.key),
            }
            url = o['notify']
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(),
                                             headers={'Content-Type': 'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(req, timeout=10) as r:
                    resp = r.read().decode('utf-8', 'replace')
                return self._send(200, 'HTTP %s %s %s' % (r.status, resp, url),
                                  'text/plain; charset=utf-8')
            except Exception as e:
                return self._send(502, 'notify failed: %s' % e, 'text/plain; charset=utf-8')

        return self._send(404, 'not found', 'text/plain; charset=utf-8')

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith('/pay/'):
            pay_id = path.split('/')[2]
            o = ORDERS.get(pay_id)
            if not o:
                return self._send(404, '订单不存在或未在本网关下单', 'text/plain; charset=utf-8')
            return self._send(200, PAGE.format(price=o['price'], pay_id=pay_id))
        if path == '/':
            rows = ''.join('<li><code>%s</code> ¥%s</li>' % (k, v['price']) for k, v in ORDERS.items())
            return self._send(200, '<h1>假支付网关</h1><ul>%s</ul>' % (rows or '<li>暂无订单</li>'))
        return self._send(404, 'not found', 'text/plain; charset=utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--key', default=None, help='V免签密钥，默认取 config.json payment.vmq.key')
    args = ap.parse_args()
    if args.key:
        Handler.key = args.key
    else:
        try:
            cfg = json.load(open(os.path.join(ROOT, 'config.json')))
            Handler.key = cfg['payment']['vmq'].get('key') or Handler.key
        except Exception:
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('假支付网关：http://127.0.0.1:%d  （key=%s）' % (args.port, Handler.key))
    print('授权服务 config.json 需设置： payment.provider=vmq, '
          'payment.vmq.monitor_base=http://127.0.0.1:%d' % args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
