# MagnetSearch for macOS

> 📦 **下载 / 安装图文 / 更新日志**：<https://121.196.163.89.sslip.io/download>
> macOS 12+（Intel 原生 / Apple 芯片走 Rosetta 2）· 试用 7 天 · ¥29 买断，按设备授权、无需卡密

磁力搜索客户端：多源聚合 + 随包安装的本地 Torznab 服务（只监听 `127.0.0.1:3333`）。
查询容错（`rufuse` → `rufus`）、中文拆词排序（`疯狂动物城` 精确命中优先，并行查 IMDb 译名）、结果上限 120、多源并行。

---

# MagnetSearch for macOS（社区兼容修复版）

> 让一份用 Xcode 26 / macOS 26 SDK 构建的 SwiftUI 磁力搜索客户端，能在 **macOS 12 及以上** 跑起来；
> 并自带一个**本地 Torznab 搜索服务**，把原本"连不上服务器"的空壳变成真能搜的工具。

⚠️ 本仓库**不包含**原始 app 的源码与素材，只包含兼容性修复工具链、搜索服务与打包脚本。
原始二进制需自备（见 [docs/BUILD.md](docs/BUILD.md)）。

---

## 一、适配环境（最重要的一节）

### 1. macOS 版本 × CPU 架构

| macOS 版本 | Intel (x86_64) | Apple Silicon (arm64) |
|---|---|---|
| **15 Sequoia** | ⚪ 未实测（预期可用） | ⚪ 未实测（需 Rosetta 2） |
| **14 Sonoma** | ⚪ 未实测（预期可用） | ⚪ 未实测（需 Rosetta 2） |
| **13 Ventura** | ✅ **已实测通过**（13.7.8） | ⚪ 未实测（需 Rosetta 2） |
| **12 Monterey** | ⚪ 理论可行，未实测 | ⚪ 理论可行，未实测（需 Rosetta 2） |
| **11 Big Sur 及更早** | ❌ 不支持 | ❌ 不支持 |

说明：

- **最低系统 12.0**：app 二进制使用 `LC_DYLD_CHAINED_FIXUPS`（需要 macOS 12 起的 dyld4），
  且链接了 `/usr/lib/swift/libswift_Concurrency.dylib`（macOS 12 起才有）。
  这两条是硬限制，补丁层无法绕过。
- **仅 x86_64**：上游二进制是 Intel 单架构产物，无法产出 arm64 原生版本。
  Apple 芯片机器通过 **Rosetta 2** 运行（首次启动系统会提示安装 Rosetta，同意即可）。
- 上表的 ✅ 来自本项目的实机验证；⚪ 表示"按原理应该可以，但没有机器实测"，欢迎 PR 补充实测结果。

### 2. 硬件与资源占用

| 项目 | 要求 |
|---|---|
| 磁盘 | app ≈ 1.2 MB，搜索服务二进制 ≈ 4.4 MB；另需几百 MB 余量给系统缓存 |
| 内存 | 运行时常驻约 60–100 MB（app + 服务各占一部分） |
| CPU | 任意 Intel 64 位；Apple 芯片需 Rosetta 2 |
| 屏幕 | 无特殊要求（SwiftUI 自适应窗口） |

### 3. 网络与端口

| 项目 | 说明 |
|---|---|
| 出网 | 需要访问公开磁力索引站（`apibay.org`、`api.knaben.org`、`www.cilisousuo.net` 等），HTTPS/443 |
| 本机端口 | 搜索服务监听 `127.0.0.1:3333`（**仅本机**，不对外），被占用会启动失败 |
| 代理 | 服务默认尝试本机 `http://127.0.0.1:7897`（Clash/Mihomo 常见混合端口），失败自动直连；可用 `--no-proxy` 关闭 |
| DNS | 无特殊要求 |

### 4. 软件依赖

| 场景 | 依赖 |
|---|---|
| 使用打包好的 `.pkg` / `.dmg` | **无**（搜索服务是 PyInstaller 独立二进制，不需要 Python） |
| 直接用源码跑服务 | Python 3.7+（仅标准库，无第三方包） |
| 重新编译补丁库 | Xcode Command Line Tools（`swiftc` + `clang`） |
| 重新打包 `.pkg` / `.dmg` | `pkgbuild` / `productbuild` / `hdiutil`（系统自带） |

---

## 二、这个项目解决了什么问题

上游 app 的三个硬门槛，导致低版本 macOS 上"双击没反应 / 一开就崩"：

| 位置 | 原值 | 现象 |
|---|---|---|
| `Info.plist` → `LSMinimumSystemVersion` | `14.0` | LaunchServices 直接拒绝启动 |
| Mach-O `LC_BUILD_VERSION` | `minos 14.0` / `sdk 26.2` | dyld 判定不兼容 |
| 符号表 | 引用了 5 个 macOS 14+/Swift 6 才有的符号 | `dyld: Symbol not found` 崩溃 |

补齐的 5 个符号（实现见 `patch/shim/`）：

```
$s7SwiftUI4ViewPAAE8onChange2of7initial_..._lF     SwiftUI.View.onChange(of:initial:_:)（macOS 14 新增）
$s7SwiftUI4ViewPAAE8onChange2of7initial_..._lFQOMQ 其 opaque 类型 conformance 描述符
$ss12_ArrayBufferV19_getElementSlowPath..._Ts5     Swift 6 标准库泛型预特化 thunk
swift_getOpaqueTypeConformance2                    Swift 6 运行时新入口
swift_getTypeByMangledNameInContext2               Swift 6 运行时新入口
swift_getTypeByMangledNameInContextInMetadataState2
```

做法：
1. 改 `Info.plist` 与 Mach-O `minos` → `12.0`
2. 把主程序里这几个符号的导入从"两级命名空间"改成 **flat lookup**（改 chained-fixups 的 `lib_ordinal` 为 `0xFE`）
3. 注入 `libMagnetSearchPatch.dylib`（ad-hoc 签名），由它提供上述符号，内部转发到 macOS 12/13 已有的旧实现

技术细节见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

---

## 三、搜索服务（Torznab）

上游 app 本身只是 **Torznab 客户端**（请求 `/torznab/api?t=search&q=<关键词>`），默认连 `http://localhost:3333`。
本仓库的 `server/` 就提供这个服务，把多个公开索引聚合成 Torznab RSS：

| 语种 | 源 |
|---|---|
| 英文 / 全球 | `knaben`（聚合 TPB/1337x/TGx/Nyaa/RuTracker）、`apibay`(TPB)、`nyaa`、`sukebei`、`torrentgalaxy`、`1337x`、`btdig`、`bt4g`、`eztv` |
| 中文 | `knaben`（含 Nyaa 中文发布）、`cilisousuo`(磁力搜索)、`dmhy`(动漫花园)、`acgrip`、`mikan`(蜜柑)、`bangumi`(萌番组)、`btsow`、`zhongziso` |

特性：
- **按语种自动选源**；连不上的源自动冷却 10 分钟，不拖慢整体响应
- **相关性过滤**，挡掉站点模糊匹配返回的无关条目
- **中文 → 英文译名回退**：中文结果不足时用 IMDb 建议接口取英文片名再去英文源搜一轮
  （`沙丘2` → `Dune: Part Two`）
- 空结果不缓存；单源超时；直连优先、代理兜底

接口：

```
GET http://127.0.0.1:3333/torznab/api?t=search&q=关键词   # Torznab RSS
GET http://127.0.0.1:3333/health                          # ok
GET http://127.0.0.1:3333/sources                         # 各源状态（可用 / 冷却中）
```

详见 [docs/SEARCH-BACKEND.md](docs/SEARCH-BACKEND.md)。

---

## 四、安装使用

### 方式 A：pkg（推荐）

```
双击 MagnetSearch-3.2-免激活.pkg → 一路下一步
```

安装内容：`/Applications/MagnetSearch.app` + 用户级 LaunchAgent `com.magnetsearch.backend`（开机自启搜索服务）。

### 方式 B：DMG 手动版

```
双击「首次打开.command」   # 清隔离属性 + 装服务 + 启动 app
把 MagnetSearch.app 拖进「应用程序」
```

### 方式 C：只跑源码服务

```bash
python3 server/torznab_server.py --port 3333
curl -s http://127.0.0.1:3333/torznab/api?t=search&q=matrix | head
```

### 卸载

```bash
bash server/uninstall.sh          # 或双击「卸载 MagnetSearch.command」
```

### 常见排查

| 现象 | 处理 |
|---|---|
| 双击提示"无法验证开发者/已损坏" | `xattr -dr com.apple.quarantine /Applications/MagnetSearch.app` 或 系统设置 → 隐私与安全性 → 仍要打开 |
| 搜不到任何东西 | `curl http://127.0.0.1:3333/health`；不通就看 `~/Library/Logs/MagnetSearchBackend.log` |
| 服务起不来 | 端口 3333 被占用；或看日志里的 `Address already in use` |
| 中文搜不到 | 中文源覆盖有限；已内置 IMDb 译名回退，仍为空说明确实没有该资源 |
| Apple 芯片打不开 | 先装 Rosetta：`softwareupdate --install-rosetta --agree-to-license` |

---

## 五、授权与收款（7 天试用 + 终身买断）

`licensing/` 提供一套**设备授权 + 免签收款**方案，取代早先「patch 掉试用校验」的做法：

* 装上即用 7 天，试用起点记录在服务端（按 `IOPlatformUUID` 的哈希绑定设备），删本地文件/重装都重置不了
* 到期在主窗口内弹出收银台对话框（sheet），扫码付款后支付平台回调 → 服务端给这台设备打「永久」标记
* 之后启动/定时联网校验；已买断则直接放行，**断网也认**（凭证是 Ed25519 签名，客户端只内嵌公钥）
* 支付渠道全免签约：V免签监控端 / 彩虹易支付 / 自建聚合 / 人工补单，四选一
* 自带后台（订单、设备、补单、统计）、管理 CLI、本地假支付网关和 13 项端到端自测

```bash
cd licensing
python3 server/admin.py init      # 生成配置 + 签名密钥
python3 server/admin.py serve     # 起授权服务（:8899）
python3 tools/fake_gateway.py     # 可选：本地假网关，零成本走通付款→解锁
cd .. && bash licensing/client/build_license.sh
bash licensing/client/apply_to_app.sh /Applications/MagnetSearch.app
```

细节、部署（systemd/nginx）、支付对接参数、风控边界和发版清单见
[licensing/README.md](licensing/README.md) 与 [licensing/docs/LICENSING.md](licensing/docs/LICENSING.md)。

## 六、目录结构

```
.
├── README.md                  # 本文件
├── LICENSE                    # MIT
├── docs/
│   ├── COMPATIBILITY.md       # 低版本兼容修复原理（minos / 符号 / flat lookup）
│   ├── SEARCH-BACKEND.md      # 搜索服务与各数据源实现说明
│   ├── BUILD.md               # 从零复现：打补丁 → 签名 → 打包
│   └── FAQ.md
├── licensing/                 # 授权与收款（试用/买断/免签支付/后台/注入库）
│   ├── server/                # 授权服务（纯标准库 + SQLite）
│   ├── client/                # 注入库源码与构建脚本
│   ├── tools/                 # 端到端自测 / 本地假支付网关
│   └── docs/LICENSING.md      # 部署、支付对接、风控与发版
├── patch/
│   ├── patch_imports.py       # 改 chained-fixups 的 lib_ordinal → flat lookup
│   ├── add_load_dylib.py      # 给主程序追加 LC_LOAD_DYLIB（注入补丁库）
│   ├── patch_license.py       # 去掉 24h 试用/激活校验（可选）
│   └── shim/                  # 补丁库源码（Swift + C）
│       ├── shim.swift             onChange(of:initial:_:) 的等价实现
│       ├── slowpath.swift         _ArrayBuffer 慢路径的 NSArray 桥接实现
│       ├── rt_clean.c             Swift 6 运行时入口转发
│       └── build_shim.sh          一键编译补丁库
├── server/                    # 本地 Torznab 服务（纯标准库）
│   ├── torznab_server.py
│   ├── sources.py
│   ├── install.sh / uninstall.sh
│   └── com.magnetsearch.backend.plist
└── packaging/
    ├── build_pkg.sh           # 打 .pkg
    ├── build_dmg.sh           # 打 .dmg
    ├── distribution.xml
    ├── preinstall / postinstall
    └── 首次打开.command / 卸载 MagnetSearch.command
```

---

## 七、构建

见 [docs/BUILD.md](docs/BUILD.md)。简要：

```bash
# 1) 编译补丁库
bash patch/shim/build_shim.sh            # 输出 libMagnetSearchPatch.dylib

# 2) 给自备的 MagnetSearch.app 打补丁
python3 patch/patch_imports.py  path/to/MagnetSearch.app/Contents/MacOS/MagnetSearch
python3 patch/add_load_dylib.py path/to/MagnetSearch.app/Contents/MacOS/MagnetSearch
cp libMagnetSearchPatch.dylib path/to/MagnetSearch.app/Contents/Frameworks/
codesign --force --deep --sign - path/to/MagnetSearch.app

# 3) 打包
bash packaging/build_pkg.sh path/to/MagnetSearch.app
```

---

## 八、已知限制

1. **只在 Intel + macOS 13.7.8 实测**，其它组合欢迎反馈实机结果。
2. **无法产出 arm64 原生版**（缺少原始源码）。
3. **中文源覆盖有限**：目前稳定可用的是 `cilisousuo`；`动漫花园 / 蜜柑 / ACG.RIP / BTSOW` 等解析器已写好，但视网络可达性自动启用。
   查询侧已做容错（`rufuse → rufus`）与中文拆词排序，见 [docs/SEARCH-BACKEND.md](docs/SEARCH-BACKEND.md)。
4. **搜索质量取决于索引站**，`apibay` 对中文关键词无结果（已按语种自动跳过）。
5. 上游 app 自带的 `24 小时试用 / 激活码` 逻辑，现在由 `licensing/` 里的自有授权门禁接管（`patch/patch_license.py` 可不再使用，
   见 [licensing/docs/LICENSING.md](licensing/docs/LICENSING.md) 第十节）。

---

## 九、声明

- 本项目仅包含**兼容性修复工具链、搜索聚合服务与打包脚本**，不包含上游 app 的源码、图标等素材。
- 磁力索引来自公开站点，请遵守当地法律法规与各站点服务条款。
- 使用本工具产生的任何后果由使用者自行承担。

## License

MIT（仅适用于本仓库自有代码）。
