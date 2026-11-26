#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""支付渠道适配层。

每个 Provider 实现三件事：
    create(order)  -> {'qr_content': ..., 'pay_url': ..., 'raw': {...}}
    verify(params, raw_body, headers) -> {'ok':bool, 'order_id':.., 'trade_no':..,
                                          'amount_cents':.., 'reason':..}
    ack()          -> (http_status, body, content_type)   回调应答（各渠道要求不同）

已内置（都属于「免签」范畴，不需要企业资质/签约）：
    manual   人工/客服收款 —— 二维码页 + 后台补单，最省事，适合起步
    vmq      V免签协议：手机装「V免签监控端」，收到支付宝/微信到账通知后回调本服务
    epay     彩虹易支付 / 码支付 协议（大量个人免签聚合平台兼容这一套）
    generic  自定义聚合：/notify/generic，HMAC-SHA256 签名，接任何自建网关
"""

import hashlib
import hmac
import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger('pay')


def md5(s):
    if isinstance(s, str):
        s = s.encode('utf-8')
    return hashlib.md5(s).hexdigest()


def hmac_sha256(key, msg):
    if isinstance(key, str):
        key = key.encode()
    if isinstance(msg, str):
        msg = msg.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _sign_kv(params, key):
    """彩虹易支付签名：按 key 升序拼 k=v&...，去掉 sign/sign_type/空值，末尾直接拼商户密钥后 md5。"""
    items = [(k, v) for k, v in params.items() if k not in ('sign', 'sign_type') and v not in ('', None)]
    items.sort(key=lambda kv: kv[0])
    raw = '&'.join('%s=%s' % (k, v) for k, v in items) + key
    return md5(raw)


def money_to_cents(v):
    try:
        return int(round(float(str(v).strip()) * 100))
    except Exception:
        return None


def cents_to_money(c):
    return '%.2f' % (c / 100.0)


class BaseProvider:
    name = 'base'
    content_type = 'text/plain'

    def __init__(self, cfg, product, server):
        self.cfg = cfg
        self.product = product
        self.server = server
        self.p = cfg['payment'].get(self.name) or {}

    def create(self, order):
        raise NotImplementedError

    def verify(self, params, raw_body=b'', headers=None):
        raise NotImplementedError

    def ack(self, ok=True):
        return (200, 'success', self.content_type)

    @property
    def public_base(self):
        return (self.server.get('public_base') or '').rstrip('/')


# ---------------------------------------------------------------- manual
class ManualProvider(BaseProvider):
    """人工收款：二维码指向本站订单页，页面上放你的收款码/微信号，付完你和后台核对后补单。"""
    name = 'manual'

    def create(self, order):
        url = '%s/pay/%s' % (self.public_base, order['order_id'])
        return {'qr_content': url, 'pay_url': url, 'raw': {'mode': 'manual'}}

    def verify(self, params, raw_body=b'', headers=None):
        # 人工渠道只接受后台补单（/admin/api/order/paid），不走公开回调
        return {'ok': False, 'reason': 'manual provider has no callback'}


# ---------------------------------------------------------------- V免签
class VmqProvider(BaseProvider):
    """V免签（个人免签）：手机跑监控端，检测到收款后 POST 回调本服务。

    下单：POST {monitor_base}/create
          payId=<订单号>&type=<1微信|2支付宝>&price=<元>&sign=md5(price+type+payId+key)
                  &notifyUrl=<回调>&returnUrl=<回跳>
    回调：payId / price / type / sign（部分版本还带 reallyPrice）
    """
    name = 'vmq'

    def _sign(self, price, pay_type, pay_id):
        return md5('%s%s%s%s' % (price, pay_type, pay_id, self.p.get('key', '')))

    def create(self, order):
        base = (self.p.get('monitor_base') or '').rstrip('/')
        if not base:
            raise RuntimeError('payment.vmq.monitor_base 未配置')
        price = cents_to_money(order['amount_cents'])
        pay_type = int(self.p.get('pay_type') or 2)
        form = {
            'payId': order['order_id'],
            'type': str(pay_type),
            'price': price,
            'sign': self._sign(price, pay_type, order['order_id']),
            'notifyUrl': '%s/notify/vmq' % self.public_base,
            'returnUrl': '%s/pay/done?order_id=%s' % (self.public_base, order['order_id']),
        }
        req = urllib.request.Request(base + '/create', data=urllib.parse.urlencode(form).encode(),
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode('utf-8', 'replace')
        except Exception as e:
            raise RuntimeError('V免签监控端不可达: %s' % e)
        qr = ''
        try:
            data = json.loads(body)
            d = data.get('data') or data
            qr = d.get('payUrl') or d.get('qrCode') or d.get('url') or ''
        except Exception:
            qr = body.strip()
        if not qr:
            raise RuntimeError('V免签未返回支付链接: %s' % body[:200])
        return {'qr_content': qr, 'pay_url': qr, 'raw': {'vmq': body[:500]}}

    def verify(self, params, raw_body=b'', headers=None):
        pay_id = params.get('payId') or params.get('pay_id') or ''
        price = params.get('price') or params.get('reallyPrice') or ''
        pay_type = params.get('type') or params.get('payType') or ''
        sign = (params.get('sign') or '').lower()
        key = self.p.get('key', '')
        price = '%.2f' % float(price) if price else ''
        candidates = [
            md5('%s%s%s%s' % (price, pay_type, pay_id, key)),
            md5('%s%s%s%s' % (pay_id, price, pay_type, key)),
            md5('%s%s%s%s' % (pay_id, pay_type, price, key)),
            md5('%s%s%s' % (pay_id, price, key)),
        ]
        if not sign or sign not in candidates:
            return {'ok': False, 'reason': 'vmq sign mismatch', 'order_id': pay_id}
        return {'ok': True, 'order_id': pay_id, 'trade_no': params.get('tradeNo') or pay_id,
                'amount_cents': money_to_cents(price)}

    def ack(self, ok=True):
        return (200, 'ok' if ok else 'fail', 'text/plain')


# ---------------------------------------------------------------- 易支付
class EpayProvider(BaseProvider):
    """彩虹易支付 / 码支付 兼容协议（多数个人免签聚合站的默认协议）。

    下单：GET {api_base}/submit.php?pid=..&type=alipay&out_trade_no=..&notify_url=..&return_url=..
                &name=..&money=..&sitename=..&sign=..&sign_type=MD5
    回调：POST/GET form：pid, trade_no, out_trade_no, type, name, money, trade_status, sign, sign_type
    """
    name = 'epay'

    def create(self, order):
        api = (self.p.get('api_base') or '').rstrip('/')
        if not api:
            raise RuntimeError('payment.epay.api_base 未配置')
        params = {
            'pid': self.p.get('pid', ''),
            'type': self.p.get('pay_type', 'alipay'),
            'out_trade_no': order['order_id'],
            'notify_url': '%s/notify/epay' % self.public_base,
            'return_url': '%s/pay/done?order_id=%s' % (self.public_base, order['order_id']),
            'name': self.product.get('name', 'License'),
            'money': cents_to_money(order['amount_cents']),
            'sitename': self.p.get('sitename', 'MagnetSearch'),
            'device': self.p.get('device', 'pc'),
            'sign_type': 'MD5',
        }
        params['sign'] = _sign_kv(params, self.p.get('key', ''))
        url = api + '/submit.php?' + urllib.parse.urlencode(params)
        return {'qr_content': url, 'pay_url': url, 'raw': {'epay_params': {k: v for k, v in params.items()
                                                                           if k != 'key'}}}

    def verify(self, params, raw_body=b'', headers=None):
        status = (params.get('trade_status') or '').upper()
        if status not in ('TRADE_SUCCESS', 'SUCCESS', ''):
            return {'ok': False, 'reason': 'trade_status=%s' % status, 'order_id': params.get('out_trade_no', '')}
        if self.p.get('pid') and str(params.get('pid')) != str(self.p.get('pid')):
            return {'ok': False, 'reason': 'pid mismatch', 'order_id': params.get('out_trade_no', '')}
        expect = _sign_kv(params, self.p.get('key', ''))
        if (params.get('sign', '') or '').lower() != expect.lower():
            return {'ok': False, 'reason': 'epay sign mismatch', 'order_id': params.get('out_trade_no', '')}
        return {'ok': True, 'order_id': params.get('out_trade_no', ''),
                'trade_no': params.get('trade_no', ''), 'amount_cents': money_to_cents(params.get('money'))}


# ---------------------------------------------------------------- paywall
class PaywallProvider(BaseProvider):
    """付费墙模式（8图片/北尚互联这类「付费图片 + 支付后跳转」平台）。

    没有收单回调可用，于是把「付款成功后跳转到本站」当作付款凭证：
        app 展示付费图二维码 → 用户扫码付款 → 平台跳到 /claim/<secret>
        → 本站当场发一张一次性授权码 → 用户回 app 填码 → 永久解锁

    配置：payment.paywall.url            付费图/付费页地址（用户扫的那个）
          payment.paywall.claim_secret   跳转地址里的密钥（防止有人猜到 /claim/ 就白拿码）
          payment.paywall.code_ttl_minutes 授权码有效期
          payment.paywall.max_claims_per_hour_per_ip 同一 IP 每小时最多生成几个码
    """
    name = 'paywall'

    def create(self, order):
        url = (self.p.get('url') or '').strip()
        if not url:
            raise RuntimeError('payment.paywall.url 未配置')
        return {'qr_content': url, 'pay_url': url, 'raw': {'mode': 'paywall'}}

    def verify(self, params, raw_body=b'', headers=None):
        # 付费墙没有回调；授权走 /claim/<secret> + /api/v1/redeem
        return {'ok': False, 'reason': 'paywall provider has no server callback'}


# ---------------------------------------------------------------- generic
class GenericProvider(BaseProvider):
    """自定义聚合网关：回调 /notify/generic，签名 = HMAC-SHA256(按 key 升序的 k=v&...)

    下单回调地址：{public_base}/notify/generic
    回调参数：order_id, amount_cents 或 money, trade_no, ts, sign
    """
    name = 'generic'

    def create(self, order):
        url = '%s/pay/%s' % (self.public_base, order['order_id'])
        return {'qr_content': url, 'pay_url': url, 'raw': {'mode': 'generic'}}

    def _sign(self, params):
        items = [(k, v) for k, v in params.items() if k not in ('sign',) and v not in ('', None)]
        items.sort(key=lambda kv: kv[0])
        return hmac_sha256(self.p.get('key', ''), '&'.join('%s=%s' % (k, v) for k, v in items))

    def verify(self, params, raw_body=b'', headers=None):
        if (params.get('sign', '') or '').lower() != self._sign(params).lower():
            return {'ok': False, 'reason': 'generic sign mismatch', 'order_id': params.get('order_id', '')}
        amount = params.get('amount_cents')
        if amount is None:
            amount = money_to_cents(params.get('money', ''))
        return {'ok': True, 'order_id': params.get('order_id', ''),
                'trade_no': params.get('trade_no', ''), 'amount_cents': int(amount) if amount else None}

    def ack(self, ok=True):
        return (200, json.dumps({'code': 0 if ok else 1, 'msg': 'ok' if ok else 'fail'}),
                'application/json')


PROVIDERS = {
    'manual': ManualProvider,
    'paywall': PaywallProvider,
    'vmq': VmqProvider,
    'epay': EpayProvider,
    'generic': GenericProvider,
}


def get_provider(cfg, name=None):
    name = name or cfg['payment'].get('provider') or 'manual'
    cls = PROVIDERS.get(name)
    if cls is None:
        raise RuntimeError('未知支付渠道: %s（可选 %s）' % (name, ', '.join(PROVIDERS)))
    return cls(cfg, cfg.get('product', {}), cfg.get('server', {}))
