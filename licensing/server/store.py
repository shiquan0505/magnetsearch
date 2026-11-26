#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""授权库：SQLite 持久层（纯标准库）。

三张主表：
  devices   一台设备 = 一条记录，trial_start 一旦写入永不改变 → 重装/删本地文件都重置不了试用
  licenses  买断授权，expires_at 为 NULL 表示终身
  orders    收银台订单，回调幂等落库
"""

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(
    device_id   TEXT PRIMARY KEY,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    trial_start INTEGER NOT NULL,
    trial_days  INTEGER NOT NULL,
    platform    TEXT DEFAULT '',
    app_version TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    blocked     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS licenses(
    device_id  TEXT PRIMARY KEY,
    plan       TEXT NOT NULL,
    issued_at  INTEGER NOT NULL,
    expires_at INTEGER,
    order_id   TEXT,
    jti        TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders(
    order_id          TEXT PRIMARY KEY,
    device_id         TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    provider          TEXT NOT NULL,
    provider_trade_no TEXT DEFAULT '',
    status            TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    paid_at           INTEGER,
    expires_at        INTEGER NOT NULL,
    qr_content        TEXT DEFAULT '',
    pay_url           TEXT DEFAULT '',
    raw               TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orders_device ON orders(device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);
CREATE TABLE IF NOT EXISTS downloads(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    file    TEXT NOT NULL,
    ip      TEXT DEFAULT '',
    ua      TEXT DEFAULT '',
    referer TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_downloads_ts ON downloads(ts DESC);
CREATE TABLE IF NOT EXISTS claim_codes(
    code       TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    INTEGER,
    device_id  TEXT DEFAULT '',
    ip         TEXT DEFAULT '',
    note       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_claim_created ON claim_codes(created_at DESC);
CREATE TABLE IF NOT EXISTS events(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    device_id TEXT DEFAULT '',
    order_id  TEXT DEFAULT '',
    detail    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""


def now():
    return int(time.time())


class Store:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            try:
                self._conn.execute('PRAGMA journal_mode=WAL')
            except sqlite3.DatabaseError:
                pass
            self._conn.commit()

    # ---- 低层 ----
    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _query(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _query_one(self, sql, args=()):
        rows = self._query(sql, args)
        return rows[0] if rows else None

    def log(self, kind, device_id='', order_id='', detail=''):
        self._exec('INSERT INTO events(ts,kind,device_id,order_id,detail) VALUES(?,?,?,?,?)',
                   (now(), kind, device_id, order_id, str(detail)[:2000]))

    # ---- devices ----
    def get_device(self, device_id):
        return self._query_one('SELECT * FROM devices WHERE device_id=?', (device_id,))

    def touch_device(self, device_id, trial_days, platform='', app_version=''):
        """首次出现则开始计时；已存在则只更新 last_seen —— 试用起点不可重置。"""
        dev = self.get_device(device_id)
        ts = now()
        if dev is None:
            self._exec(
                'INSERT INTO devices(device_id,first_seen,last_seen,trial_start,trial_days,platform,app_version)'
                ' VALUES(?,?,?,?,?,?,?)',
                (device_id, ts, ts, ts, trial_days, platform or '', app_version or ''))
            self.log('device.new', device_id, detail='platform=%s ver=%s' % (platform, app_version))
            return self.get_device(device_id)
        self._exec('UPDATE devices SET last_seen=?, platform=COALESCE(NULLIF(?,\'\'),platform),'
                   ' app_version=COALESCE(NULLIF(?,\'\'),app_version) WHERE device_id=?',
                   (ts, platform or '', app_version or '', device_id))
        return self.get_device(device_id)

    def list_devices(self, q='', limit=100):
        if q:
            return self._query(
                'SELECT * FROM devices WHERE device_id LIKE ? OR note LIKE ? ORDER BY last_seen DESC LIMIT ?',
                ('%' + q + '%', '%' + q + '%', limit))
        return self._query('SELECT * FROM devices ORDER BY last_seen DESC LIMIT ?', (limit,))

    def list_devices_by_state(self, state='all', limit=200):
        """state: trial 试用中 / expired 试用到期未购买 / licensed 已买断 / pending 有未支付订单 / all"""
        ts = now()
        base = ("SELECT d.*, l.plan AS lic_plan, l.expires_at AS lic_exp, l.revoked AS lic_revoked,"
                " (SELECT COUNT(*) FROM orders o WHERE o.device_id=d.device_id AND o.status='pending') AS pending_orders,"
                " (SELECT MAX(o.created_at) FROM orders o WHERE o.device_id=d.device_id) AS last_order_ts"
                " FROM devices d LEFT JOIN licenses l ON l.device_id=d.device_id")
        if state == 'trial':
            rows = self._query(base + " WHERE (l.device_id IS NULL OR l.revoked=1)"
                               " AND d.trial_start + d.trial_days*86400 > ? ORDER BY d.last_seen DESC LIMIT ?",
                               (ts, limit))
        elif state == 'expired':
            rows = self._query(base + " WHERE (l.device_id IS NULL OR l.revoked=1)"
                               " AND d.trial_start + d.trial_days*86400 <= ? ORDER BY d.last_seen DESC LIMIT ?",
                               (ts, limit))
        elif state == 'licensed':
            rows = self._query(base + " WHERE l.device_id IS NOT NULL AND l.revoked=0"
                               " ORDER BY d.last_seen DESC LIMIT ?", (limit,))
        elif state == 'pending':
            rows = self._query(base + " WHERE pending_orders > 0 ORDER BY last_order_ts DESC LIMIT ?", (limit,))
        else:
            rows = self._query(base + " ORDER BY d.last_seen DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            r = dict(row)
            end = r['trial_start'] + r['trial_days'] * 86400
            r['trial_end'] = end
            r['trial_left'] = max(0, end - ts)
            r['trial_overdue'] = max(0, ts - end)
            r['licensed'] = bool(r.get('lic_plan')) and not r.get('lic_revoked')
            out.append(r)
        return out

    def set_blocked(self, device_id, blocked=True, note=None):
        self._exec('UPDATE devices SET blocked=? WHERE device_id=?', (1 if blocked else 0, device_id))
        if note is not None:
            self._exec('UPDATE devices SET note=? WHERE device_id=?', (note, device_id))
        self.log('device.block' if blocked else 'device.unblock', device_id)

    def set_note(self, device_id, note):
        self._exec('UPDATE devices SET note=? WHERE device_id=?', (note, device_id))

    # ---- licenses ----
    def get_license(self, device_id):
        return self._query_one('SELECT * FROM licenses WHERE device_id=?', (device_id,))

    def grant_license(self, device_id, plan='lifetime', expires_at=None, order_id='', jti=''):
        ts = now()
        self._exec(
            'INSERT INTO licenses(device_id,plan,issued_at,expires_at,order_id,jti,revoked)'
            ' VALUES(?,?,?,?,?,?,0)'
            ' ON CONFLICT(device_id) DO UPDATE SET plan=excluded.plan, issued_at=excluded.issued_at,'
            ' expires_at=excluded.expires_at, order_id=excluded.order_id, jti=excluded.jti, revoked=0',
            (device_id, plan, ts, expires_at, order_id, jti))
        self.log('license.grant', device_id, order_id, detail='plan=%s exp=%s' % (plan, expires_at))
        return self.get_license(device_id)

    def revoke_license(self, device_id, revoke=True):
        self._exec('UPDATE licenses SET revoked=? WHERE device_id=?', (1 if revoke else 0, device_id))
        self.log('license.revoke' if revoke else 'license.restore', device_id)

    # ---- orders ----
    def create_order(self, order_id, device_id, amount_cents, currency, provider, ttl, qr_content='', pay_url=''):
        ts = now()
        self._exec(
            'INSERT INTO orders(order_id,device_id,amount_cents,currency,provider,status,created_at,'
            'expires_at,qr_content,pay_url) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (order_id, device_id, amount_cents, currency, provider, 'pending', ts, ts + ttl,
             qr_content, pay_url))
        self.log('order.create', device_id, order_id, detail='%s %s' % (amount_cents, provider))
        return self.get_order(order_id)

    def get_order(self, order_id):
        return self._query_one('SELECT * FROM orders WHERE order_id=?', (order_id,))

    def list_orders(self, status='', limit=100, device_id=''):
        if device_id:
            return self._query('SELECT * FROM orders WHERE device_id=? ORDER BY created_at DESC LIMIT ?',
                               (device_id, limit))
        if status:
            return self._query('SELECT * FROM orders WHERE status=? ORDER BY created_at DESC LIMIT ?',
                               (status, limit))
        return self._query('SELECT * FROM orders ORDER BY created_at DESC LIMIT ?', (limit,))

    def mark_paid(self, order_id, trade_no='', raw=''):
        """幂等：已 paid 的订单不会被重复处理。"""
        o = self.get_order(order_id)
        if o is None:
            return None, 'order_not_found'
        if o['status'] == 'paid':
            return o, 'already_paid'
        if o['status'] != 'pending':
            return o, 'status_%s' % o['status']
        ts = now()
        self._exec('UPDATE orders SET status=?, paid_at=?, provider_trade_no=COALESCE(NULLIF(?,\'\'),provider_trade_no),'
                   ' raw=? WHERE order_id=? AND status=?',
                   ('paid', ts, trade_no or '', raw[:4000], order_id, 'pending'))
        self.log('order.paid', o['device_id'], order_id, detail='trade_no=%s' % trade_no)
        return self.get_order(order_id), 'ok'

    def set_order_status(self, order_id, status, note=''):
        self._exec('UPDATE orders SET status=? WHERE order_id=?', (status, order_id))
        self.log('order.%s' % status, '', order_id, detail=note)

    def record_claim_order(self, code, device_id, amount_cents, currency, provider='paywall'):
        """授权码兑换时补一条已支付订单：付费墙模式没有下单动作，但账要对得上。"""
        oid = 'CLM' + code
        ts = now()
        self._exec('INSERT OR IGNORE INTO orders(order_id,device_id,amount_cents,currency,provider,'
                   'provider_trade_no,status,created_at,paid_at,expires_at,raw)'
                   ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (oid, device_id, amount_cents, currency, provider, code, 'paid', ts, ts, ts,
                    'claim_code=%s' % code))
        return self.get_order(oid)

    def expire_stale_orders(self):
        n = self._exec('UPDATE orders SET status=\'expired\' WHERE status=\'pending\' AND expires_at<?',
                       (now(),)).rowcount
        return n

    def find_pending_by_amount(self, amount_cents, window_seconds=7 * 86400):
        """按金额找最近待支付订单（个人收款码对账用）。返回匹配列表，按时间倒序。"""
        since = now() - window_seconds
        return self._query(
            'SELECT * FROM orders WHERE status=\'pending\' AND amount_cents=? AND created_at>=?'
            ' ORDER BY created_at DESC', (amount_cents, since))

    def pending_orders_for_device(self, device_id):
        return self._query(
            'SELECT * FROM orders WHERE device_id=? AND status=\'pending\' ORDER BY created_at DESC',
            (device_id,))

    # ---- 一次性授权码（付费墙跳转后用）----
    def create_claim(self, code, ttl_seconds, ip='', note=''):
        ts = now()
        self._exec('INSERT INTO claim_codes(code,created_at,expires_at,ip,note) VALUES(?,?,?,?,?)',
                   (code, ts, ts + ttl_seconds, ip, note))
        return self.get_claim(code)

    def get_claim(self, code):
        return self._query_one('SELECT * FROM claim_codes WHERE code=?', (code,))

    def use_claim(self, code, device_id, ip=''):
        """原子占用：只有 used_at 为空且未过期才能成功。"""
        ts = now()
        cur = self._exec('UPDATE claim_codes SET used_at=?, device_id=?, ip=? '
                         'WHERE code=? AND used_at IS NULL AND expires_at>=?',
                         (ts, device_id, ip, code, ts))
        if cur.rowcount != 1:
            return False
        self.log('claim.use', device_id, '', detail='code=%s' % code)
        return True

    def latest_unused_claim(self, ip, within=3600):
        """同一出口 IP 最近发放、尚未核销的授权码（用于客户端自动取码）"""
        ts = now()
        return self._query_one(
            'SELECT * FROM claim_codes WHERE used_at IS NULL AND expires_at>? AND created_at>=?'
            ' AND ip=? ORDER BY created_at DESC LIMIT 1', (ts, ts - within, ip))

    def count_claims_since(self, ip, seconds):
        row = self._query_one('SELECT COUNT(*) c FROM claim_codes WHERE ip=? AND created_at>=?',
                              (ip, now() - seconds))
        return (row or {}).get('c', 0)

    def list_claims(self, limit=50):
        return self._query('SELECT * FROM claim_codes ORDER BY created_at DESC LIMIT ?', (limit,))

    # ---- 下载统计 ----
    def log_download(self, file, ip='', ua='', referer=''):
        self._exec('INSERT INTO downloads(ts,file,ip,ua,referer) VALUES(?,?,?,?,?)',
                   (now(), file[:120], ip[:60], (ua or '')[:200], (referer or '')[:200]))

    def download_stats(self, days=14):
        ts = now()
        day = ts - (ts % 86400)
        total = (self._query_one('SELECT COUNT(*) c FROM downloads') or {}).get('c', 0)
        today = (self._query_one('SELECT COUNT(*) c FROM downloads WHERE ts>=?', (day,)) or {}).get('c', 0)
        week = (self._query_one('SELECT COUNT(*) c FROM downloads WHERE ts>=?', (ts - 7 * 86400,))
                or {}).get('c', 0)
        by_file = self._query(
            'SELECT file, COUNT(*) c, MAX(ts) last_ts FROM downloads GROUP BY file ORDER BY c DESC')
        by_day = self._query(
            "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, COUNT(*) c "
            'FROM downloads WHERE ts>=? GROUP BY d ORDER BY d DESC', (ts - days * 86400,))
        recent = self._query('SELECT ts,file,ip,referer FROM downloads ORDER BY id DESC LIMIT 50')
        return {'total': total, 'today': today, 'week': week,
                'by_file': by_file, 'by_day': by_day, 'recent': recent}

    # ---- stats ----
    def stats(self):
        ts = now()
        day = ts - (ts % 86400)
        return {
            'devices_total': (self._query_one('SELECT COUNT(*) c FROM devices') or {}).get('c', 0),
            'devices_today': (self._query_one('SELECT COUNT(*) c FROM devices WHERE first_seen>=?', (day,))
                              or {}).get('c', 0),
            'licenses_total': (self._query_one('SELECT COUNT(*) c FROM licenses WHERE revoked=0') or {}).get('c', 0),
            'orders_paid': (self._query_one('SELECT COUNT(*) c FROM orders WHERE status=\'paid\'') or {}).get('c', 0),
            'orders_pending': (self._query_one('SELECT COUNT(*) c FROM orders WHERE status=\'pending\'') or {}).get('c', 0),
            'revenue_cents': (self._query_one('SELECT COALESCE(SUM(amount_cents),0) s FROM orders WHERE status=\'paid\'')
                              or {}).get('s', 0),
            'paying_devices': (self._query_one(
                'SELECT COUNT(DISTINCT device_id) c FROM orders WHERE status=\'paid\'') or {}).get('c', 0),
        }

    def recent_events(self, limit=100):
        return self._query('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,))
