# 外链台账（发布一个记一个）

> 目的：① 引流 ② 让搜索引擎发现站点 ③ 复盘哪个渠道真的带来下载。
> 每发一条，在这里加一行；`nofollow` 栏决定它对 SEO 权重有没有用（对引流都算数）。

| 日期 | 平台 | 位置（帖子/回答 URL） | 指向的落地页 | dofollow? | 当日下载数 | 备注 |
|---|---|---|---|---|---|---|
| 2026-11-26 | GitHub | https://github.com/<user>/magnetsearch | /download | 是 |  | Release + README |
|  | 知乎 |  |  | 否 |  | 专栏文章 |
|  | B站 |  |  | 否 |  | 视频简介 |
|  | 小红书 |  |  | 否 |  | 主页简介/评论区 |
|  | 软天空 |  |  | 是 |  | 软件站详情页 |

## 判断 dofollow 的土办法
页面源代码里看链接标签：`<a href="..." rel="nofollow">` → nofollow；没有 `rel` → dofollow。
知乎、B站、小红书、微博、贴吧的站外链接基本都是 nofollow（不传权重，但能带真实用户）。
GitHub README、个人博客、独立软件站、知乎「专栏」正文里的部分链接可能是 dofollow。
