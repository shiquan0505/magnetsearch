#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MagnetSearch 搜索源集合（插件式）：每个源一个函数，返回统一结构
   {title, hash, size, seeders, peers, source}
   连不上就抛异常，上层自动跳过。
"""
import json, re, time, urllib.parse, urllib.request
import xml.sax.saxutils as sx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
TIMEOUT = 5.0
HOST_MODE = {}
_ENABLED = {}
PROXY = None
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
import threading
_LOCK = threading.Lock()


def set_proxy(url):
    global PROXY
    PROXY = urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': url, 'https': url})) if url else None


def _open(mode, req, timeout):
    op = DIRECT if mode == 'direct' else PROXY
    with op.open(req, timeout=timeout) as r:
        return r.read()


def _request(url, data=None, ctype=None, timeout=None):
    timeout = timeout or TIMEOUT
    host = urllib.parse.urlparse(url).netloc
    with _LOCK:
        mode = HOST_MODE.get(host)
    order = [mode] if mode else (['direct', 'proxy'] if PROXY else ['direct'])
    last = None
    headers = {'User-Agent': UA, 'Accept': '*/*',
               'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
    if ctype:
        headers['Content-Type'] = ctype
    for m in order:
        try:
            raw = _open(m, urllib.request.Request(url, data=data, headers=headers), timeout)
            with _LOCK:
                HOST_MODE[host] = m
            for enc in ('utf-8', 'gbk', 'latin-1'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', 'ignore')
        except Exception as e:
            last = e
    raise last or RuntimeError('request failed')


def get(url, **kw):
    return _request(url, **kw)


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def post_json(url, payload, **kw):
    return json.loads(_request(url, data=json.dumps(payload).encode(),
                               ctype='application/json', **kw))


def size_to_bytes(text):
    m = re.search(r'([\d.]+)\s*([KMGT]?)i?B', text or '', re.I)
    if not m:
        return 0
    mul = {'': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    return int(float(m.group(1)) * mul[m.group(2).upper()])


def _item(title, h, size=0, seeders=0, peers=0, source=''):
    return {'title': (title or '').strip(), 'hash': h.upper(), 'size': int(size or 0),
            'seeders': int(seeders or 0), 'peers': int(peers or 0), 'source': source}


# ─────────────────────────────── 英文 / 全球
def knaben(q):
    """knaben.eu 聚合器：索引 TPB / 1337x / TGX / Nyaa / RuTracker 等几十个站"""
    d = post_json('https://api.knaben.org/v1',
                  {'search_field': 'title', 'query': q, 'size': 80,
                   'order_by': 'seeders', 'order_direction': 'desc'})
    out = []
    for h in d.get('hits') or []:
        hh = (h.get('hash') or '').upper()
        if len(hh) != 40:
            continue
        title = h.get('title') or h.get('description') or hh
        if h.get('description') and h.get('description') not in title:
            title = '%s %s' % (title, '')  # description 仅用于相关性判断
        out.append(_item(title, hh, h.get('bytes'), h.get('seeders'),
                         h.get('peers'), 'Knaben'))
    return out


def apibay(q):
    """The Pirate Bay 官方 JSON（直连可用，最稳）"""
    d = get_json('https://apibay.org/q.php?q=%s&cat=0' % urllib.parse.quote(q))
    out = []
    for it in d if isinstance(d, list) else []:
        if not isinstance(it, dict) or it.get('id') == '0':
            continue
        h = (it.get('info_hash') or '').upper()
        if len(h) != 40:
            continue
        out.append(_item(it.get('name'), h, it.get('size'),
                         it.get('seeders'), it.get('leechers'), 'TPB'))
    return out


def nyaa(q, host='https://nyaa.si'):
    xml = get('%s/?f=0&c=0_0&q=%s&page=rss' % (host, urllib.parse.quote(q)))
    out = []
    for item in re.findall(r'<item>(.*?)</item>', xml, re.S):
        h = re.search(r'([0-9A-Fa-f]{40})', item)
        t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.S)
        if not (h and t):
            continue
        sd = re.search(r'<nyaa:seeders>(\d+)<', item)
        lc = re.search(r'<nyaa:leechers>(\d+)<', item)
        sz = re.search(r'<nyaa:size>(.*?)<', item)
        out.append(_item(sx.unescape(t.group(1)), h.group(1),
                         size_to_bytes(sz.group(1)) if sz else 0,
                         sd.group(1) if sd else 0, lc.group(1) if lc else 0, 'Nyaa'))
    return out


def sukebei(q):
    return nyaa(q, 'https://sukebei.nyaa.si')


def torrentgalaxy(q):
    xml = get('https://www.torrentgalaxy.to/rss?searchstr=%s' % urllib.parse.quote(q))
    out = []
    for item in re.findall(r'<item>(.*?)</item>', xml, re.S):
        h = re.search(r'btih:([0-9A-Fa-f]{40})', item)
        t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.S)
        if h and t:
            out.append(_item(sx.unescape(t.group(1)), h.group(1), 0, 0, 0, 'TGx'))
    return out


def x1337(q):
    html = get('https://1337x.to/search/%s/1/' % urllib.parse.quote(q))
    out = []
    for m in re.finditer(r'href="/torrent/(\d+)/([^"]*)/"[^>]*>([^<]{4,200})</a>', html):
        title = sx.unescape(m.group(3)).strip()
        # 1337x 详情页才有 hash，这里只保留标题，hash 由详情页补
        out.append({'title': title, 'hash': '', 'size': 0, 'seeders': 0, 'peers': 0,
                    'source': '1337x', '_detail': 'https://1337x.to/torrent/%s/%s/' % (m.group(1), m.group(2))})
    for it in out[:12]:
        try:
            page = get(it['_detail'])
            h = re.search(r'btih:([0-9A-Fa-f]{40})', page)
            if h:
                it['hash'] = h.group(1)
        except Exception:
            pass
    return [x for x in out if x['hash']]


def btdig(q):
    html = get('https://btdig.com/search?q=%s' % urllib.parse.quote(q))
    out = []
    for m in re.finditer(r'data-infohash="([0-9A-Fa-f]{40})"[^>]*', html):
        out.append(_item(m.group(1), m.group(1), 0, 0, 0, 'BTDig'))
    return out


def bt4g(q):
    out = []
    for host in ('https://bt4gprx.com', 'https://bt4g.org', 'https://www.bt4g.com'):
        try:
            html = get('%s/search?q=%s' % (host, urllib.parse.quote(q)))
        except Exception:
            continue
        for m in re.finditer(r'href="/([0-9A-Fa-f]{40})/([^"]*)"[^>]*>(.*?)</a>', html, re.S):
            title = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            if title:
                out.append(_item(sx.unescape(title), m.group(1), 0, 0, 0, 'BT4G'))
        if out:
            break
    return out


def eztv(q):
    """eztv 只有 TV，且 API 不支持关键词，这里取最新并在本地过滤"""
    d = get_json('https://eztvx.to/api/get-torrents?limit=100')
    out = []
    qq = q.casefold()
    for t in d.get('torrents') or []:
        name = t.get('filename') or ''
        if qq not in name.casefold():
            continue
        out.append(_item(name, t.get('hash'), t.get('size_bytes'),
                         t.get('seeds'), t.get('peers'), 'EZTV'))
    return out


# ─────────────────────────────── 中文
_HASH_CACHE = {}


def cilisousuo(q):
    """磁力搜索 cilisousuo.net —— 中文影视主力源"""
    html = get('https://www.cilisousuo.net/search?q=%s' % urllib.parse.quote(q))
    cands = []
    for blk in re.findall(r'<li class="item">(.*?)</li>', html, re.S):
        t = re.search(r'<div class="result-title">(.*?)</div>', blk, re.S)
        link = re.search(r'href="(/magnet/[^"]+)"', blk)
        size = re.search(r'<div class="size">(.*?)</div>', blk, re.S)
        if not (t and link):
            continue
        title = sx.unescape(re.sub(r'<[^>]+>', '', t.group(1))).strip()
        if not title:
            continue
        cands.append((title, 'https://www.cilisousuo.net' + link.group(1),
                      size_to_bytes(re.sub(r'<[^>]+>', '', size.group(1))) if size else 0))
    out = []
    cache = _HASH_CACHE

    def fill(c):
        title, url, size = c
        if url in cache:
            if cache[url]:
                out.append(_item(title, cache[url], size, 0, 0, '磁力搜索'))
            return
        try:
            page = get(url)
            h = re.search(r'btih[:=]([0-9A-Fa-f]{40})', page)
            cache[url] = h.group(1) if h else ''
            if h:
                out.append(_item(title, h.group(1), size, 0, 0, '磁力搜索'))
        except Exception:
            pass

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(fill, cands[:9]))
    return out


def btsow(q):
    out = []
    for host in ('https://btsow.com', 'https://btsow.lol', 'https://btsow.pics',
                 'https://btsow.one'):
        try:
            html = get(host + '/search/' + urllib.parse.quote(q))
        except Exception:
            continue
        for m in re.finditer(r'href="/detail/([0-9A-Fa-f]{40})"[^>]*title="([^"]*)"', html):
            out.append(_item(m.group(2), m.group(1), 0, 0, 0, 'BTSOW'))
        if out:
            break
    return out


def zhongziso(q):
    out = []
    for host in ('https://www.zhongziso.top', 'https://zhongziso.cfd', 'https://zhongziso.buzz'):
        try:
            html = get('%s/search/%s' % (host, urllib.parse.quote(q)))
        except Exception:
            continue
        for m in re.finditer(r'([0-9A-Fa-f]{40})"[^>]*>([^<]{4,200})<', html):
            out.append(_item(sx.unescape(m.group(2)), m.group(1), 0, 0, 0, '种子搜'))
        if out:
            break
    return out


def dmhy(q):
    """动漫花园：中文动漫 BT，RSS"""
    xml = get('https://share.dmhy.org/topics/rss/rss.xml?keyword=%s' % urllib.parse.quote(q))
    out = []
    for item in re.findall(r'<item>(.*?)</item>', xml, re.S):
        h = re.search(r'([0-9A-Fa-f]{40})', item)
        t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.S)
        if h and t:
            out.append(_item(sx.unescape(t.group(1)), h.group(1), 0, 0, 0, '动漫花园'))
    return out


def acgrip(q):
    xml = get('https://acg.rip/.xml?term=%s' % urllib.parse.quote(q))
    out = []
    for item in re.findall(r'<item>(.*?)</item>', xml, re.S):
        h = re.search(r'([0-9A-Fa-f]{40})', item)
        t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.S)
        if h and t:
            out.append(_item(sx.unescape(t.group(1)), h.group(1), 0, 0, 0, 'ACG.RIP'))
    return out


def mikan(q):
    xml = get('https://mikanani.me/RSS/Search?searchstr=%s' % urllib.parse.quote(q))
    out = []
    for item in re.findall(r'<item>(.*?)</item>', xml, re.S):
        h = re.search(r'([0-9A-Fa-f]{40})', item)
        t = re.search(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', item, re.S)
        if h and t:
            out.append(_item(sx.unescape(t.group(1)), h.group(1), 0, 0, 0, '蜜柑'))
    return out


def bangumi(q):
    d = post_json('https://bangumi.moe/api/torrent/search',
                  {'query': q, 'p': 1})
    out = []
    for t in d.get('torrents') or []:
        h = (t.get('_id') or '').upper()
        title = t.get('title') or ''
        if len(h) == 40 and title:
            out.append(_item(title, h, 0, 0, 0, '萌番组'))
    return out


# 名称 -> (函数, 语言)  lang: 'en' / 'cn' / 'both'
REGISTRY = {
    'knaben':      (knaben, 'both'),
    'apibay':      (apibay, 'en'),
    'nyaa':        (nyaa, 'en'),
    'sukebei':     (sukebei, 'en'),
    'torrentgalaxy': (torrentgalaxy, 'en'),
    '1337x':       (x1337, 'en'),
    'btdig':       (btdig, 'both'),
    'bt4g':        (bt4g, 'both'),
    'eztv':        (eztv, 'en'),
    'cilisousuo':  (cilisousuo, 'cn'),
    'btsow':       (btsow, 'cn'),
    'zhongziso':   (zhongziso, 'cn'),
    'dmhy':        (dmhy, 'cn'),
    'acgrip':      (acgrip, 'cn'),
    'mikan':       (mikan, 'cn'),
    'bangumi':     (bangumi, 'cn'),
}

# 默认可用的源：先放实测能连通的，其余的自动探测失败就跳过
DEFAULT = ['knaben', 'apibay', 'cilisousuo', 'nyaa', 'sukebei', 'torrentgalaxy',
           '1337x', 'btdig', 'bt4g', 'btsow', 'zhongziso', 'dmhy', 'acgrip', 'mikan', 'bangumi']


# ─────────────────────────────── 中文片名 -> 英文片名
_IMDB_CACHE = {}


def translate_titles(q):
    """用 IMDb 建议接口把中文片名映射成英文名（免 key）。
       返回 [(英文名, 年份), ...]，最多 3 条；失败返回 []。"""
    key = q.strip()
    if key in _IMDB_CACHE:
        return _IMDB_CACHE[key]
    out = []
    try:
        d = get_json('https://v3.sg.media-imdb.com/suggestion/x/%s.json'
                     % urllib.parse.quote(key))
        for it in (d.get('d') or [])[:8]:
            label = (it.get('l') or '').strip()
            if not label or not label.isascii() or label.casefold() == key.casefold():
                continue
            if it.get('qid') not in ('movie', 'tvMovie', 'tvSeries', 'tvMiniSeries', 'video'):
                continue
            out.append((label, it.get('y')))
            if len(out) >= 3:
                break
    except Exception as e:
        sys.stderr.write('[译名] %s 失败: %s\n' % (key, type(e).__name__))
    _IMDB_CACHE[key] = out
    return out
