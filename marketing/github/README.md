# MagnetSearch for macOS

一个 macOS 桌面磁力搜索客户端：**输入片名/关键词 → 多源聚合成一张表 → 复制磁力或丢给下载器**。
仓库同时提供随包安装的**本地 Torznab 搜索服务**（监听 `127.0.0.1:3333`，不需要任何在线账号）。

> 📦 **下载 / 更新日志 / 图文教程**：<https://121.196.163.89.sslip.io/download>
> 试用 7 天，之后 ¥29 一次性买断（按设备授权，无需卡密）。

## 特性

- **多源聚合**：knaben / apibay / nyaa / TGx / 1337x / BTDig / BT4G / EZTV + 中文源（磁力搜索、动漫花园、蜜柑、BTSOW…），按查询语种自动选源
- **查询容错**：输入 `rufuse` 也能搜到 `rufus`（编辑距离 + 前缀扩展）
- **中文拆词排序**：`疯狂动物城` 精确命中排最前，只命中 `动物城` / `疯狂` 的排后面；并行跑 IMDb 译名（→ `Zootopia`）
- **结果上限 120，多源并行**，源挂了自动冷却 10 分钟，不拖慢整体响应
- **低版本兼容**：用 Xcode 26 构建的二进制，通过补丁库在 **macOS 12.0+** 上运行（Intel 原生 / Apple 芯片走 Rosetta 2）
- **本地化优先**：搜索请求只发到本机 `127.0.0.1:3333`，不上传任何用户数据

## 系统要求

| 项 | 要求 |
|---|---|
| macOS | 12.0 Monterey 及以上（13.x 实测通过） |
| CPU | Intel x86_64；Apple 芯片需 Rosetta 2（`softwareupdate --install-rosetta`） |
| 磁盘 | 约 6 MB |
| 网络 | 需能访问公开磁力索引站（HTTPS/443） |

## 安装

```bash
# 方式一：pkg（推荐）
双击 MagnetSearch-3.4.pkg → 一路继续 → 输入管理员密码

# 方式二：dmg
双击 MagnetSearch-3.4.dmg → 把 app 拖进「应用程序」

# 首次打开若提示"无法验证开发者"
xattr -dr com.apple.quarantine /Applications/MagnetSearch.app
```

安装器会顺带装一个用户级 LaunchAgent（`com.magnetsearch.backend`）来跑搜索服务，开机自启；
卸载时删掉 `/Applications/MagnetSearch.app` 与该 plist 即可。

## 目录结构

```
.
├── server/          # 本地 Torznab 搜索服务（纯标准库）+ 打包好的可执行文件
├── licensing/       # 授权与收款：7 天试用 / 买断 / 免签收款 / 管理后台
├── patch/           # 低版本 macOS 兼容补丁（符号 shim、minos、flat lookup）
├── packaging/       # 打 pkg / dmg
├── docs/            # 兼容性原理、搜索后端、构建、FAQ
└── marketing/       # 外链与推广素材
```

## 常见问题

**搜不到任何东西？** `curl http://127.0.0.1:3333/healthz` 应返回 `ok`；不通就看 `~/Library/Logs/MagnetSearchBackend.log`。
**结果偏少？** 取决于源可达性：`nyaa/1337x/tgx/btdig` 等在国内网络常被重置，挂代理后可用源会明显变多
（`magnetsearch-server --proxy http://127.0.0.1:7897`）。
**中文搜索不准？** 中文源覆盖有限，已内置 IMDb 译名回退与拆词排序。

## 说明

本仓库**不包含**上游 app 的原始素材；磁力索引来自公开站点，软件本身不托管、不分发任何资源。
请遵守所在地法律法规与各站点服务条款。
