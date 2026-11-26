# 推广与外链（marketing）

> 目标：① 让人找到并下载 ② 让搜索引擎发现站点 ③ 每条外链都能复盘效果。

## 文件
| 文件 | 内容 |
|---|---|
| `LINKS.md` | 外链台账：每发一条记一行，标注 dofollow、当天下载数 |
| `github/README.md` | 可直接当 GitHub 仓库首页（含关键词、特性、安装、FAQ） |
| `github/RELEASE_NOTES-3.4.md` | Release 说明正文 |
| `github/publish.sh` | 一条命令建仓 + 推代码 + 发 Release（需 `gh`） |
| `zhihu/ANSWER-TEMPLATES.md` | 知乎回答/专栏模板 + 平台链接规则 |
| `bilibili/SCRIPT.md` | 60 秒视频脚本 + 标题/封面/简介 |
| `xiaohongshu/NOTES.md` | 3 篇笔记 + 承接话术（小红书不能放外链） |
| `softsites/SUBMIT.md` | 国内外软件站提交入口、素材清单、注意事项 |

## 落地页
统一指向 <https://121.196.163.89.sslip.io/download>
（有自有域名后全站替换，`LINKS.md` 里记一下切换时间）

## 节奏（别一次性刷完，搜索引擎会当垃圾）
| 时间 | 动作 |
|---|---|
| D1 | GitHub 建仓 + Release（`publish.sh`），About 填 Homepage，加 Topics |
| D2 | 知乎：先发 1 篇专栏文章（含链接），再回答 1 个高流量问题（不放链接） |
| D3 | B站：发 1 条 60 秒视频，简介放链接，评论区置顶兜底 |
| D4 | 小红书：发笔记 1（痛点型），主页简介改「私信「磁力」自动回复」 |
| D5–D7 | AlternativeTo / MacUpdate / r/macapps 各投 1 条（英文，dofollow，收录快） |
| 每周 | 2–3 条新外链 + 回来更新 `LINKS.md` 的下载数 |

## 铁律
1. 一稿一改，别复制粘贴（重复内容会被判低质）
2. 链接指向落地页，不直接给 pkg（落地页有统计、能被收录）
3. 不买链接、不站群，百度打击很重
4. 文案避免「破解 / 免费下全网 / 资源随便下」，用「索引聚合 / 本地搜索客户端」
