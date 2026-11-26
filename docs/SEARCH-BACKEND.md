# 搜索服务（Torznab）实现说明

上游 app 只是个 **Torznab 客户端**：它请求 `/torznab/api?t=search&q=<关键词>`，
用正则从 RSS 里抠 `<title><![CDATA[...]]></title>`、`<enclosure url= length=>`、
`torznab:attr name="seeders" value=...` 这些字段。没有这个服务，app 就只有"未找到相关资源"。

---

## 1. 接口

| 路径 | 说明 |
|---|---|
| `GET /torznab/api?t=search&q=关键词` | 返回 Torznab RSS（正文） |
| `GET /torznab?t=search&q=关键词` | 同上（兼容部分客户端的探测请求） |
| `GET /caps` 或 `?t=caps` | Torznab capabilities |
| `GET /health` | 健康检查，返回 `ok` |
| `GET /sources` | 各源状态（可用 / 冷却中） |

响应结构（必须带 CDATA，否则 app 解析不到标题）：

```xml
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title><![CDATA[流浪地球 2019 国语中字]]></title>
      <guid isPermaLink="false">&lt;infohash&gt;</guid>
      <enclosure url="magnet:?xt=urn:btih:..." length="2150000000" type="application/x-bittorrent"/>
      <torznab:attr name="seeders" value="4"/>
      <torznab:attr name="peers" value="1"/>
      <torznab:attr name="size" value="2150000000"/>
    </item>
  </channel>
</rss>
```

---

## 2. 数据源

`server/sources.py` 里每个源就是一个函数，返回统一结构：

```python
{'title': str, 'hash': str(40 位大写), 'size': int, 'seeders': int, 'peers': int, 'source': str}
```

连不上就抛异常，上层自动跳过并冷却。

| 名称 | 语言 | 实现要点 |
|---|---|---|
| `knaben` | 中英 | `POST https://api.knaben.org/v1`，JSON，聚合 TPB/1337x/TGx/Nyaa/RuTracker 等，**目前最稳** |
| `apibay` | 英 | `GET https://apibay.org/q.php?q=&cat=0`，JSON，TPB 官方接口，**对中文关键词无效** |
| `nyaa` / `sukebei` | 英 | RSS，取 `<nyaa:seeders>` |
| `torrentgalaxy` | 英 | RSS，`btih:` 提取 |
| `1337x` | 英 | 列表页拿标题 → 详情页补 infohash（限前 12 条） |
| `btdig` | 中英 | `data-infohash` 属性 |
| `bt4g` | 中英 | `/<hash>/<name>` 链接结构 |
| `eztv` | 英 | 仅 TV，API 不支持关键词，取最新 100 条后本地过滤 |
| `cilisousuo` | 中 | 搜索页 `/search?q=` → 逐条进 `/magnet/xxxx` 取 infohash（12 并发 + 结果缓存） |
| `btsow` / `zhongziso` | 中 | `href="/detail/<hash>"` |
| `dmhy` / `acgrip` / `mikan` | 中 | RSS（动漫向） |
| `bangumi` | 中 | `POST /api/torrent/search`，`_id` 即 infohash |

> 实测（2026-11，大陆某宽带）：只有 `knaben`、`apibay`、`cilisousuo` 直连可达。
> 其余源在能连通的网络里会自动启用；连不上会冷却 10 分钟，不拖慢响应。

---

## 3. 调度策略

```
                       ┌── 中文源（knaben / cilisousuo / dmhy / acgrip / mikan / bangumi / btsow / zhongziso）
q 含 CJK？ ──是──> 并发 ┤
                       └── IMDb 译名 → 英文源（knaben / apibay / nyaa / TGx / 1337x / btdig / bt4g / eztv）

q 不含 CJK ────────> 并发：knaben / apibay（其余按需 --sources 打开）
```

关键参数（`server/torznab_server.py` 顶部）：

| 参数 | 默认 | 作用 |
|---|---|---|
| `DEADLINE_EN` / `DEADLINE_CN` | 6.5s / 10s | 单次请求总预算 |
| `EXTRA_EN` / `EXTRA_CN` | 1.5s / 4.5s | 拿到首批结果后，再多等多久收其它源 |
| `COOLDOWN` | 600s | 源失败后的冷却时间 |
| `MAX_ITEMS` | 80 | 返回条数上限 |
| `SOFT_EMPTY` | `{knaben, apibay}` | 这些源"返回空"不算故障（确实没资源） |

---

## 4. 相关性过滤

站点普遍有模糊匹配问题：`cilisousuo` 搜"三体"会返回一堆成人内容，`apibay` 搜中文会返回无关热门条目。
过滤规则：

- **含 CJK 的查询**：标题里必须出现查询中的某段汉字（`re.findall(r'[\u4e00-\u9fff]+')`）
- **英文查询**：按空格/点/连字符切词，命中过半即可

过滤发生在两个位置：源函数内部（减少详情页请求）和聚合层（跨源统一）。

---

## 5. 中文 → 英文译名回退

中文源覆盖有限，所以补了一层映射：

```python
# server/sources.py
def translate_titles(q):   # → [('The Wandering Earth', 2019), ...]
    GET https://v3.sg.media-imdb.com/suggestion/x/<关键词>.json
```

- 免 API Key（IMDb 建议接口），返回 `l`（英文名）、`y`（年份）、`qid`（movie/tvSeries…）
- 与中文源**并行**发起，不增加整体耗时
- 只有中文结果 <8 条时才把译名结果并入（避免"狂飙 → F1: The Movie"这类误译污染）
- 并入的结果 `source` 标记为 `TPB ⭐Dune: Part Two`，界面上能看出是译名搜来的

实测：`沙丘2` → 2 条（中文源）＋ 78 条（`Dune: Part Two`），共 80 条。

---

## 6. 安装形态

| 形态 | 路径 |
|---|---|
| 源码运行 | `python3 server/torznab_server.py [--port] [--proxy] [--no-proxy] [--sources]` |
| 打包二进制 | `pyinstaller --onefile run_server.py`（产物约 4.4 MB，无 Python 依赖） |
| 开机自启 | LaunchAgent `~/Library/LaunchAgents/com.magnetsearch.backend.plist`（用户级，不需要 root） |

plist 里的可执行文件路径指向 app 内置的 `Contents/Resources/backend/magnetsearch-server`，
所以"卸载 app + 删 plist"就是完整卸载。

## 查询容错与排序（v3.4 起）

`server/match.py` 负责把"用户输入"变成"能搜到东西的查询"，并对结果打分排序；只依赖标准库。

**拼写容错（英文）**

* 查询补全：去掉多余尾字母、去重复字母、前缀截断，得到候选词（`rufuse → rufus`）
* 相关性不再要求原词原样出现，改为「逐 token 模糊相似度」≥ 0.62 即放行（`rufuse` vs `Rufus` ≈ 0.88）
* 结果排序：完全命中 3 分 + 相似度 2 分 + 命中位置 0.6 分 + 做种对数 0.25 分 − 垃圾内容 0.9 分

**中文拆词**

* 全串命中排最前；子词（`动物城` / `疯狂`）命中的排后面
* 结果不足时自动追加子词检索：取「词尾 3 字 / 词头 3 字 / 词头 2 字 / 词尾 2 字」
* 中文查询同时并行跑 IMDb 译名（`疯狂动物城 → Zootopia`），两边结果合并去重

**性能**

* 主查询、译名、补充检索**并行**发出（原来是串行等待，中文查询能到 12–16 秒）
* 预算：英文 4.0s / 中文 6.0s / 补充检索 4.5s；实测 `rufuse` 4.2s、`疯狂动物城` 6.2s
* 结果上限 80 → 120；空结果不缓存，命中缓存 5 分钟内秒回

**实测对比（同一网络环境）**

| 查询 | 改前 | 改后 |
|---|---|---|
| `rufuse` | 0 条 | **120 条，首条 Rufus v3.4.1430** |
| `rufus` | 80 条 | 120 条 |
| `疯狂动物城` | 7 条（12.2s） | **120 条（6.2s），中文正主排最前** |
| `动物城` | 8 条 | 120 条 |

> 结果多少仍取决于源可达性：本机网络下 `nyaa/1337x/tgx/btdig/dmhy/mikan` 等被墙或超时，
> 实际可用的是 `knaben / apibay / cilisousuo / bt4g / sukebei`。挂上代理（`--proxy http://127.0.0.1:7897`）
> 后可用源会明显变多，结果也会更全。
