#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MagnetSearch 本地 Torznab 搜索服务 (v2)
======================================
给 macOS 版 MagnetSearch.app 提供联网搜索：app 请求
    http://<服务器地址>:3333/torznab/api?t=search&q=关键词

多源聚合（见 sources.py），按查询语种自动选源：
  中文查询 -> knaben / 磁力搜索(cilisousuo) / 动漫花园 / ACG.RIP / 蜜柑 / 萌番组 / BTSOW / 种子搜
  英文查询 -> knaben / ThePirateBay / Nyaa / TGx / 1337x / BTDig / BT4G / EZTV
连不上的源自动冷却 10 分钟并跳过，不拖慢整体响应。
"""
import argparse, os, re, sys, threading, time, urllib.parse
import xml.sax.saxutils as sx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, wait

import match
import sources

MAX_ITEMS = 120
CACHE_TTL = 300
DEADLINE_EN = 4.0          # 英文主查询总预算
DEADLINE_CN = 6.0          # 中文主查询总预算
EXPAND_BUDGET = 4.5        # 补充检索（拆词/纠错）预算：与主查询并行，不再串行等
EXPAND_BELOW = 14          # 结果少于这个数就触发"拆词/纠错"补充检索
COOLDOWN = 600             # 源失败后冷却秒数
SOFT_EMPTY = {'knaben', 'apibay'}   # 这些源返回空结果不判故障
EXTRA_EN = 1.2             # 有结果后英文查询再多等的秒数
EXTRA_CN = 2.5             # 中文查询要多等中文源

CACHE = {}
DEAD = {}
_LOCK = threading.Lock()

EN_SOURCES = ['knaben', 'apibay', 'nyaa', 'torrentgalaxy', '1337x', 'btdig', 'bt4g', 'eztv']
CN_SOURCES = ['knaben', 'cilisousuo', 'dmhy', 'acgrip', 'mikan', 'bangumi', 'btsow', 'zhongziso']


def _cf(s):
    return (s or '').casefold()


def has_cjk(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s or ''))


def relevant(title, q):
    """相关性过滤：相似度够高就放行（能容忍 rufuse → rufus 这种小错）"""
    return match.is_relevant(q, title)


def _run(name, q):
    with _LOCK:
        if DEAD.get(name, 0) > time.time():
            return []
    fn = sources.REGISTRY[name][0]
    try:
        res = fn(q)
        # 聚合器/大站返回空结果属正常（真的没有），不算源故障
        if not res and name not in SOFT_EMPTY:
            raise RuntimeError('empty')
        return res
    except Exception as e:
        with _LOCK:
            DEAD[name] = time.time() + COOLDOWN
        sys.stderr.write('[源失败] %s: %s（冷却 %d 分钟）\n'
                         % (name, type(e).__name__, COOLDOWN // 60))
        return []


def _gather(q, cjk, budget=None):
    """跑一轮多源聚合，返回结果列表；budget 可覆盖默认预算"""
    names = CN_SOURCES if cjk else EN_SOURCES
    budget = budget or (DEADLINE_CN if cjk else DEADLINE_EN)
    out = []
    ex = ThreadPoolExecutor(max_workers=len(names))
    futs = {ex.submit(_run, n, q): n for n in names}
    deadline = time.time() + budget
    pending = set(futs)
    first_at = None
    while pending:
        left = deadline - time.time()
        if left <= 0:
            break
        done, pending = wait(pending, timeout=min(1.5, left))
        for f in done:
            try:
                batch = f.result()
            except Exception:
                continue
            if batch and first_at is None:
                first_at = time.time()
            for r in batch:
                if not r.get('hash') or not relevant(r.get('title'), q):
                    continue
                out.append(r)
        extra = EXTRA_CN if cjk else EXTRA_EN
        if first_at and time.time() - first_at > extra:
            break
    ex.shutdown(wait=False)
    return out


def search(q):
    key = q.casefold()
    now = time.time()
    if key in CACHE and now - CACHE[key][0] < CACHE_TTL:
        return CACHE[key][1]

    cjk = has_cjk(q)
    pool = ThreadPoolExecutor(max_workers=5)
    futures = []

    # 主查询（中文同时并行取英文译名）
    futures.append((None, pool.submit(_gather, q, cjk)))
    alias = None
    if cjk:
        try:
            titles = sources.translate_titles(q)
        except Exception:
            titles = []
        if titles:
            alias = titles[0][0]
            futures.append((alias, pool.submit(_gather, alias, False)))
            sys.stderr.write('[译名] %s -> %s（并行检索）\n' % (q, alias))

    # 拆词 / 拼写纠正与主查询**并行**发出，避免串行等待把总耗时拖到 10 秒
    extra = [x for x in match.expand_queries(q) if x][:3]
    if extra:
        sys.stderr.write('[补充检索] %s -> %s\n' % (q, extra))
        for x in extra:
            futures.append((x, pool.submit(_gather, x, has_cjk(x), EXPAND_BUDGET)))

    buckets = []
    for tag, fut in futures:
        try:
            buckets.append((tag, fut.result()))
        except Exception:
            buckets.append((tag, []))
    pool.shutdown(wait=False)

    merged, seen = [], set()
    for tag, batch in buckets:
        for r in batch:
            h = r.get('hash')
            if not h or h in seen:
                continue
            seen.add(h)
            if tag:
                r['source'] = '%s ⭐%s' % (r.get('source', ''), tag)
            merged.append(r)

    # 排序：完全命中 > 相似度高 > 做种多（原来是纯按做种排，导致"动物城"混在"疯狂动物城"前后）
    merged.sort(key=lambda r: match.rank_key(q, r), reverse=True)
    merged = merged[:MAX_ITEMS]
    if merged:
        CACHE[key] = (now, merged)
    return merged


def torznab_xml(results):
    def item(r):
        title = (r.get('title') or r['hash']).replace(']]>', ']]&gt;')
        magnet = 'magnet:?xt=urn:btih:%s&dn=%s' % (r['hash'], urllib.parse.quote(title))
        return ('<item>'
                '<title><![CDATA[%s]]></title>'
                '<guid isPermaLink="false">%s</guid>'
                '<link>%s</link>'
                '<enclosure url="%s" length="%d" type="application/x-bittorrent"/>'
                '<torznab:attr name="magneturl" value="%s"/>'
                '<torznab:attr name="infohash" value="%s"/>'
                '<torznab:attr name="category" value="2000"/>'
                '<torznab:attr name="seeders" value="%d"/>'
                '<torznab:attr name="peers" value="%d"/>'
                '<torznab:attr name="size" value="%d"/>'
                '<torznab:attribute name="seeders" value="%d"/>'
                '<torznab:attribute name="peers" value="%d"/>'
                '<torznab:attribute name="size" value="%d"/>'
                '<size>%d</size><seeders>%d</seeders><peers>%d</peers>'
                '<source>%s</source></item>') % (
            title, r['hash'], sx.escape(magnet), sx.escape(magnet),
            int(r.get('size') or 0), sx.escape(magnet), r['hash'],
            int(r.get('seeders') or 0), int(r.get('peers') or 0), int(r.get('size') or 0),
            int(r.get('seeders') or 0), int(r.get('peers') or 0), int(r.get('size') or 0),
            int(r.get('size') or 0), int(r.get('seeders') or 0),
            int(r.get('peers') or 0), r.get('source', ''))
    body = ''.join(item(r) for r in results)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">'
            '<channel><title>MagnetSearch Local Indexer</title>'
            '<description>multi-source magnet index</description>'
            '<link>http://localhost:3333/</link><language>zh-CN</language>'
            '<torznab:response offset="0" total="%d"/>%s</channel></rss>') % (len(results), body)


CAPS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<caps><server version="1.0" title="MagnetSearch Local"/>'
        '<limits max="100" default="80"/>'
        '<searching><search available="yes" supportedParams="q"/></searching>'
        '<categories><category id="2000" name="Movies"/></categories></caps>')


class Handler(BaseHTTPRequestHandler):
    server_version = 'MagnetSearchTorznab/2.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), fmt % args))

    def _send(self, code, body, ctype='application/xml; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ('/health', '/ping'):
            return self._send(200, b'ok', 'text/plain')
        if 'caps' in u.path or qs.get('t', [''])[0] == 'caps':
            return self._send(200, CAPS)
        if u.path in ('/sources', '/api/sources'):
            alive = [n for n in sources.REGISTRY if DEAD.get(n, 0) <= time.time()]
            dead = [n for n in sources.REGISTRY if DEAD.get(n, 0) > time.time()]
            return self._send(200, ('可用源: %s\n冷却中: %s\n' % (', '.join(alive), ', '.join(dead) or '无')),
                              'text/plain; charset=utf-8')
        q = (qs.get('q') or qs.get('Query') or [''])[0].strip()
        if not q:
            return self._send(200, torznab_xml([]))
        try:
            res = search(q)
        except Exception as e:
            return self._send(500, '<?xml version="1.0"?><error>%s</error>' % sx.escape(str(e)))
        return self._send(200, torznab_xml(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3333)
    ap.add_argument('--proxy', default=os.environ.get('MAGNET_PROXY', 'http://127.0.0.1:7897'))
    ap.add_argument('--no-proxy', action='store_true')
    ap.add_argument('--sources', default='', help='逗号分隔，限定时启用的源')
    a = ap.parse_args()
    if not a.no_proxy and a.proxy:
        sources.set_proxy(a.proxy)
    if a.sources:
        keep = {x.strip() for x in a.sources.split(',') if x.strip()}
        global EN_SOURCES, CN_SOURCES
        EN_SOURCES = [n for n in EN_SOURCES if n in keep]
        CN_SOURCES = [n for n in CN_SOURCES if n in keep]
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    sys.stderr.write('Torznab v2 已启动 http://%s:%d  英文源=%s  中文源=%s\n'
                     % (a.host, a.port, ','.join(EN_SOURCES), ','.join(CN_SOURCES)))
    srv.serve_forever()


if __name__ == '__main__':
    main()
