#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MagnetSearch 授权服务（7 天试用 + 终身买断 + 免签收款），纯标准库。

启动：
    python3 license_server.py --config ../config.json
    MS_LICENSE_CONFIG=/etc/ms-license/config.json python3 license_server.py

接口一览：
    GET  /healthz                                健康检查
    GET  /api/v1/ping                            产品/价格/试用天数（客户端可预读）
    POST /api/v1/session                         试用登记 + 授权状态（客户端启动/定时调用）
    GET  /api/v1/status?device_id=               同上（兼容）
    POST /api/v1/order/create                    下单，返回收款二维码内容
    GET  /api/v1/order/status?order_id=&device_id=   轮询支付结果
    ANY  /notify/vmq|/notify/epay|/notify/generic    支付平台异步回调（免签）
    GET  /pay/<order_id>                         收款页（人工渠道/兜底）
    GET  /pay/done                               支付返回页
    /admin/...                                   管理接口（X-Admin-Token）
    GET  /admin/                                 后台页面
"""

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import re
import string
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

from pycompat import ThreadingHTTPServer  # Python 3.6 兼容

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg_mod
import providers as pay_mod
from signer import Signer
from store import Store, now

log = logging.getLogger('license')

STATE_TRIAL = 'trial'
STATE_EXPIRED = 'trial_expired'
STATE_LICENSED = 'licensed'
STATE_BLOCKED = 'blocked'


# ---------------------------------------------------------------- 限速
class RateLimiter:
    def __init__(self, per_minute=60):
        self.per_minute = per_minute
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        if self.per_minute <= 0:
            return True
        ts = time.time()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if ts - t < 60]
            if len(bucket) >= self.per_minute:
                self._hits[key] = bucket
                return False
            bucket.append(ts)
            self._hits[key] = bucket
            if len(self._hits) > 20000:      # 简单防内存膨胀
                self._hits = {k: v for k, v in self._hits.items() if v and ts - v[-1] < 120}
            return True


def gen_id(prefix, n=10):
    alphabet = string.ascii_lowercase + string.digits
    return prefix + ''.join(random.choice(alphabet) for _ in range(n))


class App:
    """把配置、存储、签名器、支付渠道装在一起，handler 通过 self.server.app 取。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg_mod.abspath(cfg, cfg['server']['db_path']))
        sk_hex, pk_hex = cfg_mod.ensure_keys(cfg)
        self.signer = Signer(sk_hex, cfg['signing'].get('key_id', 'k1'))
        self.public_key_hex = pk_hex
        self.provider = pay_mod.get_provider(cfg)
        self.product = cfg['product']
        self.server_cfg = cfg['server']
        self.limiter = RateLimiter(int(self.server_cfg.get('rate_limit_per_min') or 60))
        log.info('签名公钥(hex) = %s', pk_hex)
        log.info('支付渠道 = %s, 价格 = %s 分, 试用 = %s 天',
                 self.provider.name, self.product['price_cents'], self.product['trial_days'])

    # ---- 会话/授权状态 ----
    def session(self, device_id, platform='', app_version=''):
        dev = self.store.touch_device(device_id, int(self.product['trial_days']), platform, app_version)
        lic = self.store.get_license(device_id)
        ts = now()

        if dev['blocked']:
            state = STATE_BLOCKED
        elif lic and not lic['revoked'] and (lic['expires_at'] is None or lic['expires_at'] > ts):
            state = STATE_LICENSED
        elif ts >= dev['trial_start'] + dev['trial_days'] * 86400:
            state = STATE_EXPIRED
        else:
            state = STATE_TRIAL

        trial_end = dev['trial_start'] + dev['trial_days'] * 86400
        data = {
            'state': state,
            'device_id': device_id,
            'server_time': ts,
            'trial_days': dev['trial_days'],
            'trial_start': dev['trial_start'],
            'trial_end': trial_end,
            'trial_remaining': max(0, trial_end - ts),
            'recheck_hours': int(self.server_cfg.get('recheck_hours') or 6),
            'product': {
                'id': self.product['id'], 'name': self.product['name'],
                'price_cents': int(self.product['price_cents']),
                'currency': self.product['currency'],
            },
            'gate_version': 1,
            'pay_mode': 'paywall' if self.provider.name == 'paywall' else 'platform',
        }
        if self.provider.name == 'paywall':
            pw = self.cfg['payment'].get('paywall') or {}
            base = (self.server_cfg.get('public_base') or '').rstrip('/')
            data['paywall'] = {
                'url': pw.get('url', ''),
                'claim_url': '%s/claim/%s' % (base, pw.get('claim_secret', '')),
                'code_ttl_minutes': int(pw.get('code_ttl_minutes') or 120),
                # image：把付费页里那个收款码解出来直接显示（推荐）
                # page ：显示付费页地址的二维码（多一次网页跳转）
                'qr_mode': pw.get('qr_mode') or 'image',
                'qr_payload': pw.get('qr_payload') or '',
                'note': pw.get('note') or '请使用支付宝扫码支付',
            }
        if state == STATE_LICENSED:
            data['license_token'] = self.signer.license_token(
                device_id, lic['plan'], lic['expires_at'])
        else:
            data['trial_token'] = self.signer.trial_token(device_id, dev['trial_start'], trial_end)
        if state == STATE_EXPIRED:
            pending = self.store.pending_orders_for_device(device_id)
            data['pending_order_id'] = pending[0]['order_id'] if pending else None
        return data

    # ---- 下单 ----
    def create_order(self, device_id):
        dev = self.store.get_device(device_id)
        if dev is None:
            dev = self.store.touch_device(device_id, int(self.product['trial_days']))
        lic = self.store.get_license(device_id)
        if lic and not lic['revoked'] and (lic['expires_at'] is None or lic['expires_at'] > now()):
            return {'already_licensed': True, 'state': STATE_LICENSED}

        # 复用 5 分钟内未过期的待支付订单，避免用户狂点生成一堆单
        for o in self.store.pending_orders_for_device(device_id):
            if o['expires_at'] > now() + 60 and o['qr_content']:
                return self._order_view(o)

        ttl = int(self.server_cfg.get('order_ttl_seconds') or 900)
        order_id = gen_id('MS')
        amount = self._unique_amount(int(self.product['price_cents']))
        order = self.store.create_order(order_id, device_id, amount,
                                        self.product['currency'], self.provider.name, ttl)
        try:
            res = self.provider.create(order)
        except Exception as e:
            self.store.set_order_status(order_id, 'failed', str(e))
            log.warning('下单失败 %s: %s', order_id, e)
            raise
        self.store._exec('UPDATE orders SET qr_content=?, pay_url=?, raw=? WHERE order_id=?',
                         (res.get('qr_content', ''), res.get('pay_url', ''),
                          json.dumps(res.get('raw', {}), ensure_ascii=False)[:4000], order_id))
        return self._order_view(self.store.get_order(order_id))

    def _unique_amount(self, base_cents):
        """个人收款码没有订单号可对，只能靠「金额」认人：
        给每单加一个 1~99 分的唯一尾数（¥29.07），你看到到账 29.07 就知道是哪一单。
        只在人工/个人码渠道启用；vmq / epay 有平台订单号，不需要。"""
        manual_cfg = (self.cfg['payment'].get('manual') or {})
        if self.provider.name != 'manual' or not manual_cfg.get('unique_amount', True):
            return base_cents
        used = {o['amount_cents'] for o in self.store.list_orders(status='pending', limit=500)}
        recent = [a for a in range(1, 100) if base_cents + a not in used]
        if not recent:
            return base_cents
        # 用「分钟数 + 订单量」做伪随机，避免连续几单尾数扎堆
        idx = (int(now() / 60) + len(used)) % len(recent)
        return base_cents + recent[idx]

    def _order_view(self, o):
        return {
            'order_id': o['order_id'],
            'amount_cents': o['amount_cents'],
            'currency': o['currency'],
            'provider': o['provider'],
            'status': o['status'],
            'qr_content': o['qr_content'],
            'pay_url': o['pay_url'],
            'expires_at': o['expires_at'],
            'pay_page': '%s/pay/%s' % (self.server_cfg.get('public_base', '').rstrip('/'), o['order_id']),
        }

    def order_status(self, order_id, device_id=''):
        o = self.store.get_order(order_id)
        if o is None:
            return None
        if device_id and o['device_id'] != device_id:
            return None
        if o['status'] == 'pending' and o['expires_at'] < now():
            self.store.set_order_status(order_id, 'expired')
            o = self.store.get_order(order_id)
        data = {'status': o['status'], 'paid': o['status'] == 'paid',
                'order_id': order_id, 'amount_cents': o['amount_cents']}
        if o['status'] == 'paid':
            sess = self.session(o['device_id'])
            data['state'] = sess['state']
            data['license_token'] = sess.get('license_token')
        return data

    # ---- 支付回调 ----
    def handle_notify(self, provider_name, params, raw_body):
        if provider_name != self.provider.name:
            log.warning('回调渠道不匹配: got=%s expected=%s', provider_name, self.provider.name)
            return 400, 'provider mismatch'
        res = self.provider.verify(params, raw_body)
        order_id = (res.get('order_id') or '').strip()
        self.store.log('notify.' + provider_name, '', order_id,
                       detail=json.dumps({k: v for k, v in params.items() if k != 'sign'},
                                         ensure_ascii=False)[:800])
        if not res.get('ok'):
            log.warning('回调验签失败: %s %s', res.get('reason'), order_id)
            return 400, 'sign error'
        order = self.store.get_order(order_id)
        if order is None:
            log.warning('回调订单不存在: %s', order_id)
            return 404, 'order not found'
        if res.get('amount_cents') and res['amount_cents'] != order['amount_cents']:
            log.warning('金额不匹配 订单=%s 订单金额=%s 回调金额=%s', order_id,
                        order['amount_cents'], res['amount_cents'])
            return 400, 'amount mismatch'
        updated, why = self.store.mark_paid(order_id, res.get('trade_no', ''), json.dumps(params,
                                                                                        ensure_ascii=False))
        if why in ('ok', 'already_paid'):
            if why == 'ok':
                self.store.grant_license(order['device_id'], 'lifetime', None, order_id, gen_id('j'))
                log.info('✅ 订单 %s 已支付，设备 %s 已开通终身授权', order_id, order['device_id'][:12])
            return 200, None
        log.warning('回调订单状态异常 %s: %s', order_id, why)
        return 409, why


# ---------------------------------------------------------------- HTML
PAY_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MagnetSearch 授权支付</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#0e1116;color:#e6edf3;
display:flex;justify-content:center;padding:40px 16px;margin:0}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:16px;padding:28px;max-width:420px;width:100%}}
h2{{margin:0 0 6px;font-size:20px}} .muted{{color:#8b949e;font-size:13px;line-height:1.7}}
.amt{{font-size:34px;font-weight:700;margin:14px 0}}
.qr{{background:#fff;padding:12px;border-radius:12px;text-align:center;margin:16px 0}}
.qr img{{width:100%;max-width:260px}} .row{{display:flex;justify-content:space-between;font-size:13px;
padding:6px 0;border-bottom:1px dashed #21262d}} code{{color:#79c0ff;word-break:break-all}}
.tag{{display:inline-block;background:#1f6feb22;color:#58a6ff;border-radius:6px;padding:2px 8px;font-size:12px}}
</style></head><body><div class="card">
<h2>MagnetSearch 终身授权</h2>
<div class="muted">付款完成后，本机自动解锁（无需卡密，设备绑定）</div>
<div class="amt">¥{amount}</div>
{qr_block}
<div class="row"><span>订单号</span><code>{order_id}</code></div>
<div class="row"><span>状态</span><span id="st" class="tag">{status_text}</span></div>
<div class="row"><span>设备</span><code>{device_short}</code></div>
<div class="row"><span>方式</span><span>{provider_text}</span></div>
<div class="muted" style="margin-top:16px">{manual_hint}</div>
<div class="muted" style="margin-top:10px">页面会自动刷新支付状态。</div>
</div>
<script>
async function poll(){{
  try{{
    const r=await fetch('/api/v1/order/status?order_id={order_id}');
    const j=await r.json();
    if(j.data&&j.data.paid){{document.getElementById('st').textContent='已支付 ✓';
      document.getElementById('st').style.color='#3fb950';return;}}
  }}catch(e){{}}
  setTimeout(poll,3000);
}}
if('{paid}'!=='1') poll(); else document.getElementById('st').textContent='已支付 ✓';
</script></body></html>"""

DONE_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>支付结果</title><style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#0e1116;
color:#e6edf3;text-align:center;padding:80px 20px}h1{font-size:22px}.m{color:#8b949e;font-size:14px;line-height:1.8}
</style></head><body><h1>✅ 支付已完成</h1>
<div class="m">回到 MagnetSearch，点「我已付款，立即刷新」即可解锁。<br>
如果 10 秒后仍未解锁，请把订单号发给客服：<b>{order_id}</b></div></body></html>"""

DOWNLOAD_CARD = """<a class="dl{cls}" href="{href}">
  <div class="dl-left"><div class="dl-label">{label}</div><div class="dl-name">{name}</div></div>
  <div class="dl-right"><div class="dl-size">{size}</div><div class="dl-go">下载 ↓</div></div>
  <div class="dl-sha">SHA256 {sha}</div>
</a>"""

DOWNLOAD_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>下载 MagnetSearch {version} — 磁力搜索助手（macOS）</title>
<meta name="description" content="MagnetSearch {version} 是 macOS 上的磁力搜索助手，内置本地 Torznab 聚合服务，支持多源并行检索、中英文关键词容错匹配。试用 {trial} 天，买断 ¥{price} 终身可用。">
<meta name="keywords" content="MagnetSearch,磁力搜索,macOS 磁力搜索,种子搜索客户端,Torznab,资源搜索工具">
<meta name="robots" content="index,follow">
{verify}
<link rel="canonical" href="{base}/download">
<meta property="og:type" content="website">
<meta property="og:title" content="MagnetSearch {version} — macOS 磁力搜索助手">
<meta property="og:description" content="多源聚合的磁力搜索客户端，内置本地 Torznab 服务，试用 {trial} 天。">
<meta property="og:url" content="{base}/download">
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"SoftwareApplication",
 "name":"MagnetSearch","softwareVersion":"{version}",
 "operatingSystem":"macOS {min_macos}+","applicationCategory":"UtilitiesApplication",
 "offers":{{"@type":"Offer","price":"{price}","priceCurrency":"CNY"}},
 "description":"macOS 磁力搜索助手，内置本地 Torznab 聚合服务"}}
</script>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0f14;color:#e6edf3;font-family:-apple-system,"PingFang SC",sans-serif;
line-height:1.6;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:760px;margin:0 auto;padding:48px 20px 64px}}
.hero{{display:flex;align-items:center;gap:16px;margin-bottom:8px}}
.logo{{width:56px;height:56px;border-radius:14px;background:linear-gradient(145deg,#1f6feb,#0b3d91);
display:flex;align-items:center;justify-content:center;font-size:28px}}
h1{{font-size:26px;margin:0}}
.sub{{color:#8b949e;font-size:13px;margin-top:4px}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0 28px}}
.badge{{background:#161b22;border:1px solid #21262d;border-radius:999px;padding:4px 12px;font-size:12px;color:#9da7b3}}
.dl{{display:grid;grid-template-columns:1fr auto;gap:4px 12px;align-items:center;text-decoration:none;
background:#151b23;border:1px solid #232a33;border-radius:14px;padding:16px 18px;margin-bottom:12px;color:#e6edf3;
transition:.15s}}
.dl:hover{{border-color:#2f81f7;transform:translateY(-1px)}}
.dl.primary{{border-color:#238636;background:linear-gradient(180deg,#16241a,#151b23)}}
.dl-label{{font-size:12px;color:#8b949e}}
.dl-name{{font-family:ui-monospace,Menlo,monospace;font-size:14px;margin-top:2px}}
.dl-right{{text-align:right}}
.dl-size{{font-size:13px;color:#9da7b3}}
.dl-go{{font-size:13px;color:#58a6ff;margin-top:2px}}
.dl-sha{{grid-column:1/-1;font-family:ui-monospace,Menlo,monospace;font-size:10.5px;color:#5d6672;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
h2{{font-size:15px;margin:32px 0 12px;color:#e6edf3}}
ol,ul{{margin:0;padding-left:22px;color:#9da7b3;font-size:13.5px}}
li{{margin:6px 0}}
code{{background:#161b22;border:1px solid #21262d;border-radius:6px;padding:1px 6px;font-size:12.5px;color:#79c0ff}}
.card{{background:#151b23;border:1px solid #232a33;border-radius:14px;padding:18px 20px;margin-top:12px}}
.warn{{border-color:#9e6a03;background:#1f1a0f}}
.foot{{margin-top:40px;color:#5d6672;font-size:12px;border-top:1px solid #21262d;padding-top:16px}}
a.link{{color:#58a6ff;text-decoration:none}}
</style></head><body><div class="wrap">

<div class="hero"><div class="logo">🔍</div><div>
<h1>MagnetSearch <span style="color:#58a6ff">{version}</span></h1>
<div class="sub">磁力搜索助手 · 内置本地 Torznab 聚合服务 · 发布 {date}</div>
</div></div>

<div class="badges">
<span class="badge">macOS {min_macos}+</span>
<span class="badge">Intel / Apple 芯片（Rosetta 2）</span>
<span class="badge">试用 {trial} 天</span>
<span class="badge">买断 ¥{price} · 终身可用</span>
</div>

{cards}

<h2>这个软件是什么</h2>
<div class="card">
<p class="sub" style="margin:0 0 10px">MagnetSearch 是一个 macOS 桌面客户端：输入片名或关键词，它把多个公开磁力索引站的搜索结果聚合到一起，
按相关度与做种数排序后展示，点一下即可复制磁力链接或用下载工具接收。</p>
<p class="sub" style="margin:0">搜索由随包安装的<strong>本地 Torznab 服务</strong>（监听 127.0.0.1:3333）提供，
支持中英文自动切换源、拼写容错（输入 rufuse 也能找到 rufus）、中文关键词拆词排序。
软件本身不托管任何资源，只做索引聚合。</p>
</div>

<h2>版本与更新</h2>
<div class="card"><ul>
<li><strong>{version}</strong>（{date}）：搜索支持拼写容错、中文拆词排序，结果上限提升到 120，多源并行检索</li>
</ul></div>

<h2>安装步骤</h2>
<div class="card"><ol>
<li>双击 <code>.pkg</code>，一路「继续 / 安装」，输入一次管理员密码</li>
<li>安装器会自动装好本地搜索服务（开机自启，监听 <code>127.0.0.1:3333</code>）</li>
<li>打开「应用程序 → MagnetSearch」，输入片名即可搜索</li>
</ol></div>

<h2>校验下载是否完整</h2>
<div class="card"><ol>
<li>打开「终端」，输入 <code>shasum -a 256 ~/Downloads/MagnetSearch-{version}.pkg</code></li>
<li>输出应与上方卡片的 SHA256 完全一致</li>
</ol></div>

<h2>首次打开提示「无法验证开发者 / 已损坏」？</h2>
<div class="card warn"><ol>
<li>右键点图标 → 打开 → 再点「打开」，或</li>
<li>终端执行：<code>xattr -dr com.apple.quarantine /Applications/MagnetSearch.app</code>，或</li>
<li>系统设置 → 隐私与安全性 → 仍要打开</li>
</ol></div>

<h2>授权与付款</h2>
<div class="card">
<p class="sub" style="margin:0 0 8px">试用 {trial} 天结束后，软件内弹窗扫码付款 ¥{price}，<b>按设备</b>永久解锁，无需卡密、换机可找客服迁移。</p>
<p class="sub" style="margin:0">客服联系方式：{contact}</p>
</div>

<h2>常见问题</h2>
<div class="card"><ul>
<li>搜不到内容：终端执行 <code>curl http://127.0.0.1:3333/healthz</code>，应返回 <code>ok</code>；不返回请看 <code>~/Library/Logs/MagnetSearchBackend.log</code></li>
<li>端口 3333 被占用：关掉占用程序后重启 app</li>
<li>卸载：<code>sudo rm -rf /Applications/MagnetSearch.app</code>，再删掉 <code>~/Library/LaunchAgents/com.magnetsearch.backend.plist</code></li>
</ul></div>

<div class="foot">
下载即表示同意仅将本软件用于合法用途；磁力索引来自公开站点，请遵守当地法律法规。<br>
<a class="link" href="/admin/">管理后台</a>
</div>
</div></body></html>"""

CLAIM_404 = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>链接无效</title><style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#0b0f14;
color:#e6edf3;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
.c{max-width:360px;padding:24px}a{color:#58a6ff}</style></head><body><div class="c">
<h2>链接无效</h2><p>这个授权页地址不对，请从支付成功后的跳转页进入，或联系客服。</p></div></body></html>"""

CLAIM_BUSY = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>请稍后</title><style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#0b0f14;
color:#e6edf3;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
.c{max-width:360px;padding:24px}</style></head><body><div class="c">
<h2>操作太频繁</h2><p>请过一会儿再打开这个页面，或联系客服处理。</p></div></body></html>"""

CLAIM_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>付款成功 · 获取授权码</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#0b0f14;color:#e6edf3;margin:0;
padding:28px 18px;display:flex;justify-content:center}}
.card{{max-width:420px;width:100%;background:#151b23;border:1px solid #232a33;border-radius:16px;padding:26px}}
h1{{font-size:20px;margin:0 0 6px}} .ok{{color:#3fb950}}
.m{{color:#8b949e;font-size:13px;line-height:1.75}}
.code{{margin:20px 0;padding:18px;background:#0d1117;border:1px dashed #3d444d;border-radius:12px;
text-align:center;font-size:34px;font-weight:700;letter-spacing:3px;color:#58a6ff;font-family:ui-monospace,Menlo,monospace;
user-select:all;-webkit-user-select:all}}
button{{width:100%;border:0;border-radius:10px;padding:12px;font-size:15px;cursor:pointer;
background:#238636;color:#fff;margin-top:10px}} button.ghost{{background:#30363d}}
ol{{margin:8px 0 0;padding-left:20px}} li{{margin:6px 0}}
.hr{{height:1px;background:#232a33;margin:18px 0}}
</style></head><body><div class="card">
<h1><span class="ok">✓ 已收到付款</span></h1>
<div class="m">下面是你的授权码，<b>{ttl} 分钟内有效</b>，只能激活 1 台设备。</div>
<div class="code" id="code">{code}</div>
<button onclick="copyCode()">复制授权码</button>
<div class="hr"></div>
<div class="m"><b>怎么用：</b></div>
<ol class="m">
<li>回到电脑上的 MagnetSearch，点「粘贴授权码」</li>
<li>填入 <b id="c2">{code}</b>，点「开通」</li>
<li>立刻永久解锁，之后无需再登录</li>
</ol>
<div class="m" style="margin-top:14px">授权码只能给这台设备用；丢失可联系客服，凭订单重新发放。</div>
<div class="hr"></div>
<div class="m">还没装软件？<a href="/download" style="color:#58a6ff">点这里下载安装包</a></div>
</div>
<script>
function copyCode(){{
  const t='{code_raw}';
  if(navigator.clipboard){{navigator.clipboard.writeText(t).then(()=>alert('已复制：'+t));}}
  else{{const i=document.createElement('textarea');i.value=t;document.body.appendChild(i);i.select();
    document.execCommand('copy');i.remove();alert('已复制：'+t);}}
}}
</script></body></html>"""

ADMIN_LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>管理后台登录</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0f14;color:#e6edf3;font-family:-apple-system,"PingFang SC",sans-serif}}
.card{{width:340px;background:#151b23;border:1px solid #232a33;border-radius:16px;padding:28px}}
h1{{font-size:17px;margin:0 0 4px}} .m{{color:#8b949e;font-size:12px;margin-bottom:18px}}
label{{display:block;font-size:12px;color:#8b949e;margin:12px 0 6px}}
input{{width:100%;background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:8px;
padding:10px 12px;font-size:14px}}
button{{width:100%;margin-top:20px;border:0;border-radius:8px;padding:11px;font-size:14px;
background:#238636;color:#fff;cursor:pointer}}
.err{{margin-top:14px;color:#f85149;font-size:12px;min-height:16px}}
</style></head><body><form class="card" method="post" action="/admin/login">
<h1>MagnetSearch 授权后台</h1>
<div class="m">输入管理员账号继续</div>
<label>用户名</label><input name="username" autocomplete="username" autofocus>
<label>密码</label><input name="password" type="password" autocomplete="current-password">
<button type="submit">登录</button>
<div class="err">{err}</div>
</form></body></html>"""


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = 'MSLicense/1.0'
    protocol_version = 'HTTP/1.1'

    # 屏蔽默认的 /favicon 之类噪音
    def log_message(self, fmt, *args):
        log.debug('%s - %s', self.address_string(), fmt % args)

    # ---- 工具 ----
    @property
    def app(self):
        return self.server.app

    def _send(self, status, body, content_type='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def json_out(self, data=None, code=0, msg='ok', status=200):
        self._send(status, json.dumps({'code': code, 'msg': msg, 'data': data}, ensure_ascii=False))

    def fail(self, msg, status=400, code=1):
        self.json_out(None, code=code, msg=msg, status=status)

    def client_ip(self):
        xff = self.headers.get('X-Forwarded-For', '')
        if xff:
            return xff.split(',')[0].strip()
        return self.client_address[0]

    def read_body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n else b''
        ctype = (self.headers.get('Content-Type') or '').lower()
        body = {}
        if raw and 'json' in ctype:
            try:
                body = json.loads(raw.decode('utf-8'))
            except Exception:
                body = {}
        elif raw:
            body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode('utf-8', 'replace')).items()}
        return body, raw

    # ---- 后台登录（用户名/密码 + 签名 cookie）----
    def _admin_secret(self):
        cfg = self.app.server_cfg
        raw = '%s|%s' % (cfg.get('admin_token', ''), cfg.get('admin_user', ''))
        return hashlib.sha256(raw.encode()).digest()

    def _admin_session(self, user, hours=None):
        cfg = self.app.server_cfg
        hours = int(hours or cfg.get('admin_session_hours') or 12)
        exp = str(int(time.time()) + hours * 3600)
        payload = base64.urlsafe_b64encode(('%s|%s' % (user, exp)).encode()).rstrip(b'=')
        sig = hmac.new(self._admin_secret(), payload, hashlib.sha256).hexdigest()[:32]
        return payload.decode() + '.' + sig

    def _admin_session_ok(self):
        raw = self.headers.get('Cookie') or ''
        for part in raw.split(';'):
            if '=' not in part:
                continue
            k, v = part.strip().split('=', 1)
            if k != 'ms_admin':
                continue
            try:
                payload, sig = v.rsplit('.', 1)
                expect = hmac.new(self._admin_secret(), payload.encode(),
                                  hashlib.sha256).hexdigest()[:32]
                if not hmac.compare_digest(sig, expect):
                    return False
                decoded = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)).decode()
                user, exp = decoded.split('|', 1)
                if int(exp) < int(time.time()):
                    return False
                want_user = self.app.server_cfg.get('admin_user') or ''
                return (not want_user) or user == want_user
            except Exception:
                return False
        return False

    def _admin_password_ok(self, user, password):
        cfg = self.app.server_cfg
        want_user = cfg.get('admin_user') or ''
        want_hash = (cfg.get('admin_password_sha256') or '').lower()
        want_plain = cfg.get('admin_password') or ''
        if want_user and user.strip() != want_user:
            return False
        if want_hash:
            return hashlib.sha256(password.encode()).hexdigest() == want_hash
        if want_plain:
            return password == want_plain
        return False

    def admin_ok(self, query):
        # 1) 命令行/脚本：X-Admin-Token
        token = self.headers.get('X-Admin-Token') or (query or {}).get('token', [None])[0]
        expected = self.app.server_cfg.get('admin_token') or ''
        if expected and token == expected:
            return True
        # 2) 浏览器：登录后拿到的签名 cookie
        return self._admin_session_ok()

    # ---- 路由 ----
    def do_HEAD(self):
        self.route('HEAD')

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')

    def do_PUT(self):
        self.route('PUT')

    def route(self, method):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip('/') or '/'
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

            if not path.startswith('/admin') and not self.app.limiter.allow(self.client_ip()):
                log.warning('限速触发 ip=%s path=%s（每分钟上限 %s，可调 server.rate_limit_per_min）',
                            self.client_ip(), path, self.app.server_cfg.get('rate_limit_per_min'))
                return self.fail('too many requests', 429, 429)

            if path == '/healthz':
                return self._send(200, 'ok', 'text/plain')

            if path == '/api/v1/ping':
                p = self.app.product
                return self.json_out({
                    'product_id': p['id'], 'product_name': p['name'],
                    'price_cents': int(p['price_cents']), 'currency': p['currency'],
                    'trial_days': int(p['trial_days']), 'server_time': now(),
                    'public_key': self.app.public_key_hex,
                    'gate_version': 1, 'provider': self.app.provider.name,
                })

            if path in ('/api/v1/session', '/api/v1/status'):
                body, _raw = self.read_body() if method == 'POST' else ({}, b'')
                device_id = (body.get('device_id') or query.get('device_id', [''])[0]).strip()
                if not self._valid_device(device_id):
                    return self.fail('bad device_id')
                data = self.app.session(device_id, body.get('platform', ''), body.get('app_version', ''))
                return self.json_out(data)

            if path == '/api/v1/order/create' and method == 'POST':
                body, _raw = self.read_body()
                device_id = (body.get('device_id') or '').strip()
                if not self._valid_device(device_id):
                    return self.fail('bad device_id')
                try:
                    data = self.app.create_order(device_id)
                except Exception as e:
                    return self.fail('创建订单失败：%s' % e, 502)
                return self.json_out(data)

            if path == '/api/v1/order/status':
                order_id = query.get('order_id', [''])[0].strip()
                device_id = query.get('device_id', [''])[0].strip()
                if not order_id:
                    return self.fail('missing order_id')
                data = self.app.order_status(order_id, device_id)
                if data is None:
                    return self.fail('order not found', 404)
                return self.json_out(data)

            if path.startswith('/notify/'):
                pname = path.split('/', 2)[2]
                body, raw = self.read_body()
                params = dict(body)
                for k, v in query.items():
                    params.setdefault(k, v[0])
                status, ack_body = self.app.handle_notify(pname, params, raw)
                if ack_body is None:
                    st, body, ctype = self.app.provider.ack(True)
                    return self._send(st, body, ctype)
                st, body, ctype = self.app.provider.ack(False)
                return self._send(status, body, ctype)

            if path == '/robots.txt':
                base = self._site_base()
                body = ('User-agent: *\n'
                        'Allow: /download\n'
                        'Disallow: /admin\n'
                        'Disallow: /api\n'
                        'Disallow: /claim\n'
                        'Sitemap: %s/sitemap.xml\n') % base
                return self._send(200, body, 'text/plain; charset=utf-8')

            if path == '/sitemap.xml':
                base = self._site_base()
                today = time.strftime('%Y-%m-%d')
                body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        '<url><loc>%s/download</loc><lastmod>%s</lastmod><changefreq>weekly</changefreq>'
                        '<priority>1.0</priority></url>'
                        '<url><loc>%s/</loc><lastmod>%s</lastmod><changefreq>weekly</changefreq>'
                        '<priority>0.8</priority></url>'
                        '</urlset>') % (base, today, base, today)
                return self._send(200, body, 'application/xml; charset=utf-8')

            if path == '/':
                return self._send(302, '', 'text/html; charset=utf-8', {'Location': '/download'})

            if path in ('/download', '/download/', '/dl'):
                return self._download_page()

            if re.match(r'^/[A-Za-z0-9_\-]+\.(html|txt)$', path):
                name = path.lstrip('/')
                vdir = os.path.join(self._public_dir(), 'verification')
                fp = os.path.realpath(os.path.join(vdir, name))
                if fp.startswith(os.path.realpath(vdir) + os.sep) and os.path.isfile(fp):
                    return self._send(200, open(fp, 'rb').read(), 'text/html; charset=utf-8')

            if path.startswith('/download/'):
                return self._send_file(path.split('/', 2)[2])

            if path.startswith('/claim/'):
                return self._claim_page(path.split('/', 2)[2])

            if path == '/api/v1/claim/auto':
                # 客户端轮询：付款后平台把用户跳到 /claim/<secret>，那边发码；
                # 这里按「同一出口 IP + 未核销 + 未过期」把码取回去，实现免输码自动解锁。
                device_id = query.get('device_id', [''])[0].strip()
                if not self._valid_device(device_id):
                    return self.fail('bad device_id')
                ip = self.client_ip()
                claim = self.app.store.latest_unused_claim(ip, within=int(
                    (self.app.cfg['payment'].get('paywall') or {}).get('auto_window_minutes') or 60) * 60)
                if claim is None:
                    return self.json_out({'code': None, 'waiting': True})
                log.info('自动取码 %s（ip=%s）', claim['code'], ip)
                return self.json_out({'code': claim['code'], 'waiting': False,
                                      'expires_at': claim['expires_at']})

            if path == '/api/v1/redeem' and method == 'POST':
                body, _raw = self.read_body()
                return self._redeem(body)

            if path.startswith('/pay/done'):
                order_id = query.get('order_id', [''])[0]
                return self._send(200, DONE_PAGE.replace('{order_id}', order_id or '-'), 'text/html; charset=utf-8')

            if path.startswith('/pay/'):
                order_id = path.split('/', 2)[2]
                o = self.app.store.get_order(order_id)
                if o is None:
                    return self._send(404, '订单不存在', 'text/plain; charset=utf-8')
                return self._send(200, self._pay_page(o), 'text/html; charset=utf-8')

            if path in ('/admin/login', '/admin/login/'):
                if method == 'POST':
                    body, _ = self.read_body()
                    user = (body.get('username') or '')
                    pwd = (body.get('password') or '')
                    if self._admin_password_ok(user, pwd):
                        cookie = 'ms_admin=%s; Path=/admin; HttpOnly; SameSite=Lax; Max-Age=%d' % (
                            self._admin_session(user), int(self.app.server_cfg.get('admin_session_hours') or 12) * 3600)
                        log.info('后台登录成功 user=%s ip=%s', user, self.client_ip())
                        return self._send(302, '', 'text/html; charset=utf-8',
                                          {'Set-Cookie': cookie, 'Location': '/admin/'})
                    log.warning('后台登录失败 user=%s ip=%s', user, self.client_ip())
                    return self._send(401, ADMIN_LOGIN_PAGE.format(err='用户名或密码不正确'), 'text/html; charset=utf-8')
                return self._send(200, ADMIN_LOGIN_PAGE.format(err=''), 'text/html; charset=utf-8')

            if path in ('/admin/logout', '/admin/logout/'):
                return self._send(302, '', 'text/html; charset=utf-8',
                                  {'Set-Cookie': 'ms_admin=; Path=/admin; Max-Age=0',
                                   'Location': '/admin/login'})

            if path == '/admin' or path == '/admin/':
                if not self.admin_ok(query):
                    return self._send(200, ADMIN_LOGIN_PAGE.format(err=''), 'text/html; charset=utf-8')
                return self._send(200, self._send_admin_guard(), 'text/html; charset=utf-8')

            if path.startswith('/admin/api/'):
                if not self.admin_ok(query):
                    return self.fail('unauthorized', 401, 401)
                return self._admin_api(path[len('/admin/api/'):], method, query)

            return self.fail('not found', 404)
        except BrokenPipeError:
            pass
        except Exception:
            log.error('请求异常 %s %s\n%s', method, self.path, traceback.format_exc())
            try:
                self.fail('server error', 500, 500)
            except Exception:
                pass

    # ---- 校验/渲染 ----
    @staticmethod
    def _valid_device(device_id):
        return bool(device_id) and 8 <= len(device_id) <= 128 and all(
            c.isalnum() or c in '-_.' for c in device_id)

    # ---- 下载页 ----

    def _site_base(self):
        cfg = self.app.server_cfg
        return (cfg.get('site_base') or cfg.get('public_base') or '').rstrip('/')

    def _verify_metas(self):
        v = self.app.server_cfg.get('site_verify') or {}
        lines = []
        mapping = [('baidu', 'baidu-site-verification'), ('bing', 'msvalidate.01'),
                   ('google', 'google-site-verification'), ('sogou', 'sogou_site_verification'),
                   ('360', '360-site-verification')]
        for key, name in mapping:
            val = (v.get(key) or '').strip()
            if val:
                lines.append('<meta name="%s" content="%s">' % (name, val))
        return '\n'.join(lines)

    def _public_dir(self):
        d = (self.app.server_cfg.get('download_dir') or 'public')
        if not os.path.isabs(d):
            d = os.path.join(os.path.dirname(os.path.abspath(cfg_mod.__file__)), '..', d)
        return os.path.realpath(d)

    def _release_json(self, pdir):
        try:
            with open(os.path.join(pdir, 'release.json'), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _download_page(self):
        pdir = self._public_dir()
        meta = self._release_json(pdir)
        rows = []
        for name in meta.get('files') or []:
            fp = os.path.join(pdir, name)
            if not os.path.isfile(fp):
                continue
            size = os.path.getsize(fp)
            digest = hashlib.sha256(open(fp, 'rb').read()).hexdigest()
            ext = name.rsplit('.', 1)[-1].lower()
            label = {'pkg': '安装包（推荐）', 'dmg': '拖拽安装版', 'zip': '压缩包'}.get(ext, ext.upper())
            rows.append((name, label, size, digest))
        if not rows:
            return self._send(503, '安装包尚未上传', 'text/plain; charset=utf-8')

        def human(n):
            return '%.1f MB' % (n / 1024.0 / 1024.0) if n > 1024 * 1024 else '%d KB' % (n / 1024)

        cards = []
        for i, (name, label, size, digest) in enumerate(rows):
            primary = ' primary' if i == 0 else ''
            cards.append(DOWNLOAD_CARD.format(
                name=name, label=label, size=human(size), sha=digest,
                cls=primary, href='/download/' + urllib.parse.quote(name)))
        page = DOWNLOAD_PAGE.format(
            version=meta.get('version', '3.3'),
            date=meta.get('date', ''),
            min_macos=meta.get('min_macos', '12.0'),
            price='%.0f' % (int(self.app.product['price_cents']) / 100.0),
            trial=self.app.product['trial_days'],
            cards='\n'.join(cards),
            contact=(self.app.cfg['payment'].get('manual') or {}).get('contact', ''),
            base=self._site_base(),
            verify=self._verify_metas(),
        )
        self._send(200, page, 'text/html; charset=utf-8')

    def _send_file(self, name):
        from urllib.parse import unquote
        name = unquote(name)
        pdir = self._public_dir()
        fp = os.path.realpath(os.path.join(pdir, name))
        if not fp.startswith(pdir + os.sep) or not os.path.isfile(fp):
            return self._send(404, '文件不存在', 'text/plain; charset=utf-8')
        if not name.lower().endswith(('.pkg', '.dmg', '.zip', '.txt', '.json')):
            return self._send(403, '不允许下载该类型文件', 'text/plain; charset=utf-8')
        size = os.path.getsize(fp)
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(size))
        self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
        self.send_header('Cache-Control', 'public, max-age=3600')
        self.end_headers()
        with open(fp, 'rb') as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

    # ---- 付费墙：一次性授权码 ----

    CLAIM_ALPHABET = 'ACDEFGHJKLMNPQRTUVWXY34679'   # 去掉易混字符

    def _claim_page(self, secret):
        pw = self.app.cfg['payment'].get('paywall') or {}
        expect = (pw.get('claim_secret') or '').strip()
        if not expect or secret != expect:
            return self._send(404, CLAIM_404, 'text/html; charset=utf-8')
        ip = self.client_ip()
        limit = int(pw.get('max_claims_per_hour_per_ip') or 30)
        if self.app.store.count_claims_since(ip, 3600) >= limit:
            log.warning('授权码生成限速 ip=%s', ip)
            return self._send(429, CLAIM_BUSY, 'text/html; charset=utf-8')

        ttl = int(pw.get('code_ttl_minutes') or 120) * 60
        code = self._new_claim_code()
        self.app.store.create_claim(code, ttl, ip, note='paywall redirect')
        log.info('发放授权码 %s（ip=%s，%s 分钟内有效）', code, ip, ttl // 60)
        shown = code[:3] + '-' + code[3:]
        return self._send(200, CLAIM_PAGE.format(code=shown, code_raw=code, ttl=ttl // 60),
                          'text/html; charset=utf-8')

    def _new_claim_code(self):
        import random
        alphabet = self.CLAIM_ALPHABET
        for _ in range(20):
            code = ''.join(random.choice(alphabet) for _ in range(6))
            if self.app.store.get_claim(code) is None:
                return code
        return ''.join(random.choice(alphabet) for _ in range(8))

    def _redeem(self, body):
        device_id = (body.get('device_id') or '').strip()
        code = (body.get('code') or '').strip().upper().replace('-', '').replace(' ', '')
        if not self._valid_device(device_id):
            return self.fail('bad device_id')
        if not code:
            return self.fail('缺少授权码')
        claim = self.app.store.get_claim(code)
        if claim is None:
            return self.fail('授权码不存在，请检查是否输错', 404)
        if claim['used_at']:
            return self.fail('这个授权码已经被使用过了（一个码只能激活一台设备）', 409)
        if claim['expires_at'] < now():
            return self.fail('授权码已过期，请重新付款或联系客服', 410)
        if not self.app.store.use_claim(code, device_id, self.client_ip()):
            return self.fail('授权码已被占用，请重试', 409)
        self.app.store.touch_device(device_id, int(self.app.product['trial_days']))
        self.app.store.grant_license(device_id, 'lifetime', None, 'claim:' + code, 'claim')
        self.app.store.record_claim_order(code, device_id, int(self.app.product['price_cents']),
                                          self.app.product['currency'], self.app.provider.name)
        log.info('授权码 %s 已兑换，设备 %s 永久授权', code, device_id[:16])
        return self.json_out(self.app.session(device_id))

    def _pay_page(self, o):
        st = {'pending': '等待支付', 'paid': '已支付', 'expired': '已过期', 'failed': '下单失败'}.get(o['status'],
                                                                                            o['status'])
        qr = ''
        if o['status'] == 'pending' and o['qr_content']:
            qr_url = 'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=' + urllib.parse.quote(
                o['qr_content'], safe='')
            qr = ('<div class="qr"><img alt="支付二维码" src="%s">'
                  '<div class="muted" style="color:#333">扫码支付</div></div>'
                  '<div class="muted">二维码内容：<code>%s</code></div>'
                  % (qr_url, o['qr_content'][:200]))
        manual = self.app.cfg['payment'].get('manual') or {}
        hint = ''
        if self.app.provider.name == 'manual':
            parts = []
            if manual.get('alipay'):
                parts.append('支付宝：<b>%s</b>' % manual['alipay'])
            if manual.get('contact'):
                parts.append(manual['contact'])
            parts.append('付款后把订单号发给客服，人工确认后自动解锁。')
            hint = '<br>'.join(parts)
        return PAY_PAGE.format(
            amount='%.2f' % (o['amount_cents'] / 100.0), order_id=o['order_id'],
            device_short=o['device_id'][:12] + '…', status_text=st,
            provider_text=o['provider'], manual_hint=hint, paid='1' if o['status'] == 'paid' else '0',
            qr_block=qr)

    def _send_admin_guard(self):
        # 页面本身不含数据，数据要登录后才能取；模板放在 server/admin_page.html，方便直接改
        if not getattr(self.app, '_admin_page_cache', None):
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin_page.html')
            with open(fp, 'r', encoding='utf-8') as f:
                self.app._admin_page_cache = f.read()
        return self.app._admin_page_cache

    # ---- 管理 API ----
    def _admin_api(self, action, method, query):
        store = self.app.store
        if action == 'stats' and method == 'GET':
            return self.json_out(store.stats())
        if action == 'orders' and method == 'GET':
            status = query.get('status', [''])[0]
            limit = min(200, int(query.get('limit', ['50'])[0] or 50))
            return self.json_out(store.list_orders(status=status, limit=limit))
        if action == 'devices' and method == 'GET':
            q = query.get('q', [''])[0]
            limit = min(200, int(query.get('limit', ['50'])[0] or 50))
            return self.json_out(store.list_devices(q=q, limit=limit))
        if action == 'order/paid' and method == 'POST':
            body, _ = self.read_body()
            order_id = body.get('order_id', '')
            o, why = store.mark_paid(order_id, 'manual-' + gen_id('', 6), json.dumps(body, ensure_ascii=False))
            if o is None:
                return self.fail('order not found', 404)
            if why == 'ok':
                store.grant_license(o['device_id'], 'lifetime', None, order_id, gen_id('j'))
            return self.json_out({'order_id': order_id, 'result': why})
        if action == 'license/grant' and method == 'POST':
            body, _ = self.read_body()
            did = (body.get('device_id') or '').strip()
            if not self._valid_device(did):
                return self.fail('bad device_id')
            days = body.get('days')
            exp = now() + int(days) * 86400 if days else None
            lic = store.grant_license(did, body.get('plan', 'lifetime'), exp,
                                      body.get('order_id', ''), gen_id('j'))
            tok = self.app.signer.license_token(did, lic['plan'], lic['expires_at'])
            return self.json_out({'license': lic, 'token': tok})
        if action == 'license/revoke' and method == 'POST':
            body, _ = self.read_body()
            store.revoke_license(body.get('device_id', ''), True)
            return self.json_out({'ok': True})
        if action == 'device/block' and method == 'POST':
            body, _ = self.read_body()
            store.set_blocked(body.get('device_id', ''), bool(body.get('blocked', True)), body.get('note'))
            return self.json_out({'ok': True})
        if action == 'claims' and method == 'GET':
            limit = min(300, int(query.get('limit', ['100'])[0] or 100))
            return self.json_out(store.list_claims(limit))
        if action == 'events' and method == 'GET':
            return self.json_out(store.recent_events(min(300, int(query.get('limit', ['100'])[0] or 100))))
        return self.fail('unknown admin action', 404)


def main(argv=None):
    ap = argparse.ArgumentParser(description='MagnetSearch 授权服务')
    ap.add_argument('--config', default=None)
    ap.add_argument('--host', default=None)
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--log-level', default='INFO')
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    cfg = cfg_mod.load(args.config)
    if args.host:
        cfg['server']['listen_host'] = args.host
    if args.port:
        cfg['server']['listen_port'] = args.port
    app = App(cfg)

    httpd = ThreadingHTTPServer((cfg['server']['listen_host'], int(cfg['server']['listen_port'])), Handler)
    httpd.app = app
    httpd.daemon_threads = True
    log.info('监听 http://%s:%s  公网基址 %s', cfg['server']['listen_host'],
             cfg['server']['listen_port'], cfg['server']['public_base'])

    def housekeeping():
        while True:
            try:
                n = app.store.expire_stale_orders()
                if n:
                    log.info('过期订单清理: %s 条', n)
            except Exception:
                log.warning('清理任务异常\n%s', traceback.format_exc())
            time.sleep(120)

    threading.Thread(target=housekeeping, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info('bye')


if __name__ == '__main__':
    main()
