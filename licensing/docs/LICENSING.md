# 授权与收款系统 · 设计 · 部署 · 运维

> 面向 MagnetSearch（macOS SwiftUI 客户端 + 本地搜索服务）的「7 天试用 + 终身买断 + 免签收款」实现说明。
> 代码在 `licensing/`，本文是给未来的自己/运维看的操作手册。

---

## 一、整体架构

```
┌──────────────────────  用户 Mac  ──────────────────────┐
│  MagnetSearch.app                                       │
│    └── libMagnetSearchPatch.dylib（兼容补丁）            │
│          └── LC_LOAD_DYLIB → libMagnetSearchLicense     │
│                 ├── DeviceIdentity   IOPlatformUUID→哈希│
│                 ├── LicenseToken     Ed25519 验签(公钥)│
│                 ├── LicenseAPI       /api/v1/*          │
│                 └── PaywallWindow    到期收银台          │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼────────────────────────────┐
│  授权服务（VPS，python3 stdlib + SQLite）                │
│    /api/v1/session        试用登记/状态/发凭证           │
│    /api/v1/order/create   下单 → 渠道下单 → 二维码内容   │
│    /api/v1/order/status   轮询支付结果                   │
│    /notify/<provider>     支付平台回调（验签+金额+幂等）  │
│    /pay/<order_id>        收款页（人工渠道兜底）          │
│    /admin/                后台（订单/设备/补单/统计）     │
└───────────────────────────┬────────────────────────────┘
                            │ 支付平台回调 / 主动下单
                ┌───────────▼───────────┐
                │ V免签监控端 / 易支付   │
                │ 自建聚合 / 人工收款    │
                └───────────────────────┘
```

**三条不变量**（改代码时别破坏）：

1. `devices.trial_start` 写入后**永不更新** —— 这是「重装也不能重置试用」的唯一支点。
2. 授权凭证由服务端私钥签名，客户端只内嵌**公钥** —— 客户端无法自造凭证。
3. 订单金额永远取服务端 `product.price_cents`，回调必须校验金额一致 —— 客户端改价无效。

---

## 二、状态机

服务端 `session` 返回五种状态之一：

| state | 触发条件 | 客户端行为 |
|---|---|---|
| `licensed` | 有未吊销授权且未过期 | 存 token，静默放行，每 N 小时复查 |
| `trial` | `now < trial_start + trial_days*86400` | 放行；首次弹一次「剩余 X 天」；缓存 trial token |
| `trial_expired` | 试用期已过且无授权 | 主窗口内弹出收银台 sheet，自动下单出二维码 |
| `blocked` | `devices.blocked=1` | 拉「设备已停用」页，不给付款入口 |
| — | 客户端断网且无有效本地凭证 | 弹离线收银台（「重新连接」） |

客户端判定顺序（`LicenseGate.start()`）：

```
本地 license token 验签通过？
├─ 是 → 放行 + 后台静默复核（发现吊销会立刻收回）
└─ 否 → 联网 session
        ├─ licensed        → 存 token，放行
        ├─ trial           → 放行（剩余天数写本地）
        ├─ trial_expired   → 收银台 sheet
        ├─ blocked         → 停用页（同一个弹窗换文案）
        └─ 网络失败 → 本地 trial token 未过期？放行 : 离线收银台
```

### 收银台交互（客户端 `PaywallWindow`）

到期不是全屏遮罩，而是一个**软件内弹窗**：

* 优先把收银台作为 **sheet** 挂到 app 主窗口上 —— 主窗口变灰不可点、弹窗不能随手关掉，
  但视觉上就是正常的对话框，不会突然铺满整个屏幕；
* 高度随内容自适应：有二维码时约 460×430，离线/停用（无二维码）更矮；
* 找不到宿主窗口（主窗口还没建好）时，先退化成居中的浮动小窗，并在 0.7s×6 内持续尝试挂回 sheet；
* 支付成功由 3 秒轮询 `/api/v1/order/status` 发现 → 自动 `endSheet` 收起 → 会话刷新为 `licensed`；
* 弹窗复用同一个实例（只 `orderOut`/`endSheet`，绝不 `close()`），避免程序化窗口释放后主线程再访问导致崩溃；
* 「打开支付页面」用系统浏览器打开真实收银台，扫码不方便时可直接跳转；
* 「退出」按钮给不想买的用户一个体面的出口（`NSApp.terminate`），不会把 app 卡死。

---

## 三、凭证格式

```
MS1.<base64url(payload_json)>.<base64url(ed25519_sig)>
签名对象 = "MS1.<payload>" 的 ASCII 字节
```

payload：

```json
{"v":1,"typ":"license","app":"com.magnetsearch.app",
 "did":"dev-d680090a6ca5f5e8…","plan":"lifetime",
 "iat":1795666975,"exp":null,"jti":"dev-d6800-1795666975","kid":"k1"}
```

* `typ`: `license`（买断）或 `trial`（试用，带 `ts`/`te`）
* `did`: 设备指纹，防把凭证拷给别人（客户端校验自身指纹是否一致）
* `app`: bundle id，防拿去给别的软件用
* `exp`: `null` = 永久
* `kid`: 换钥时用于灰度

密钥管理：

```bash
python3 server/admin.py init        # 生成 keys/ed25519.key（0600）+ 打印公钥
python3 server/admin.py pubkey      # 只打印公钥
```

* 私钥**只**放服务端；客户端构建机没有私钥也行，把公钥写进 `config.json → signing.public_key_hex`。
* 换钥流程：新 `key_id` 发一版客户端（内嵌新公钥）→ 服务端切 `signing.key_id` → 老客户端失效前留 1–2 个版本过渡。

---

## 四、部署（VPS）

### 4.1 装服务

```bash
# 本机把 licensing/ 同步上去
rsync -av licensing/ user@vps:/tmp/licensing/

# 服务器上
sudo bash /tmp/licensing/deploy/install.sh
```

脚本会建 `mslic` 用户、拷到 `/opt/magnetsearch-license`、生成密钥、装 systemd、自检。

### 4.1.1 已部署实例（阿里云 ECS，2026-11-26）

| 项 | 值 |
|---|---|
| 公网 IP | `121.196.163.89`（私网 `172.19.39.33`，实例 `i-bp142n69v7rvsadp1h8l`，`cn-hangzhou`） |
| 系统 | Alibaba Cloud Linux 3，自带 python3.6.8（服务已做 3.6 兼容层 `server/pycompat.py`） |
| 部署路径 | `/opt/magnetsearch-license`（systemd 单元 `magnetsearch-license`，开机自启，用户 `mslic`） |
| 监听 | `0.0.0.0:8899`（直连） |
| 客户端基址 | `http://121.196.163.89:8899` |
| 管理 token | `120da27515f516e90f5cc83a0dd2411e`（后台 `/admin/`，建议尽快换） |
| 公钥 | `df25e052a7f7d7d2e87aa6889ca1de228892a28115c53fc5c040f2d70acff4d7` |
| 私钥 | `/opt/magnetsearch-license/keys/ed25519.key`（0600，只在服务器上，**务必备份**） |
| 数据库 | `/opt/magnetsearch-license/license.db`（SQLite，**务必定期备份**） |
| 支付渠道 | 目前 `manual`（人工收款 + 后台补单），换成 vmq/epay 只需改 config.json 再 restart |

常用运维命令：

```bash
systemctl status magnetsearch-license
journalctl -u magnetsearch-license -f
curl -s http://127.0.0.1:8899/healthz
cd /opt/magnetsearch-license && python3 server/admin.py --config config.json stats
python3 server/admin.py --config config.json devices
python3 server/admin.py --config config.json orders --status paid
```

⚠️ **安全组目前只放行了 22 端口**，8899 从公网不可达（实测 80/443/8081/8082/8888 也不通）。
必须去阿里云控制台放行，否则客户端连不上：

> ECS 控制台 → 实例 `i-bp142n69v7rvsadp1h8l` → 安全组 → 配置规则 → 入方向 → 手动添加
> 端口范围 `8899/8899`，授权对象 `0.0.0.0/0`（或只放你的用户所在区域）→ 保存

放行后自检（任意一台机器）：

```bash
curl http://121.196.163.89:8899/healthz          # 期望 ok
curl http://121.196.163.89:8899/api/v1/ping      # 期望 JSON（含公钥、价格、试用天数）
```

服务端与这台机器上已有业务是**并存**的，没有动它们：
`payment-nginx`（宿主机 8081/8082 → 容器 80）与 `activation-server`（宿主机 8888）两个 Docker 容器保持原样；
宿主 nginx 之前没在跑（8082 被 docker-proxy 占用），所以没有再装反代。等有域名/证书时可以再加
`deploy/nginx.conf.example` 的 443 反代。

### 4.2 改配置（`/opt/magnetsearch-license/config.json`）

```json
{
  "product": { "name": "MagnetSearch 终身授权", "price_cents": 2900, "currency": "CNY", "trial_days": 7 },
  "server": {
    "listen_port": 8899,
    "public_base": "https://lic.example.com",
    "admin_token": "换成一串随机值",
    "recheck_hours": 6,
    "order_ttl_seconds": 900
  },
  "signing": { "private_key_file": "keys/ed25519.key", "key_id": "k1" },
  "payment": { "provider": "vmq" }
}
```

`public_base` 必须是**用户能访问到的 HTTPS 地址**：二维码里的支付链接、回调地址都从它拼出来。

### 4.3 Nginx + 证书

用 `deploy/nginx.conf.example`，certbot 签证书，最重要的是：

* `/notify/` 不要鉴权、不要缓存（支付平台是匿名 POST）；
* `/admin` 限 IP。

### 4.4 Mac 本机自用（不买 VPS）

`deploy/com.magnetsearch.license.plist` 装成 LaunchAgent 即可；
`public_base` 用 `http://你的公网IP:8899`（ATS 对纯 IP 的 HTTP 放行，但**强烈建议**还是上 HTTPS）。

---

## 五、支付渠道对接（全部免签约）

`payment.provider` 四选一：

### 5.1 `manual` — 个人收款码 / 人工收款（只有个人码就走这条）

只有个人微信、支付宝收款码、没有商户号也能跑通的方案。核心问题是：
**个人收款码没有任何官方回调**，钱到账不会通知任何服务器。所以这里用「唯一金额 + 对账」把这件事变成可自动化：

1. **唯一金额尾数**（`payment.manual.unique_amount: true`，默认开）
   每单在 ¥29.00 基础上加 1~99 分的唯一尾数，例如 ¥29.47、¥29.13 —— 到账金额本身就成了订单标识，
   同一时刻不会有两单同额。收银台二维码下的文案会自动显示这个金额，用户必须原封不动付这个数。
2. **看到账短信/通知，一条命令补单**

   ```bash
   python3 server/admin.py --config /opt/magnetsearch-license/config.json pay --amount 29.47
   # → ✅ 已补单并开通终身授权：MSxxxx  设备 dev-xxxx
   python3 server/admin.py pay --amount 29.47 --dry-run     # 只看匹配
   ```

   也可以在后台 `/admin/` 的待支付列表里点「标记已付」。
3. **账单自动对账**（推荐，省事）

   ```bash
   # 支付宝 App：我的 → 账单 → 右上角 … → 开具交易流水证明 / 导出账单（CSV）
   # 微信：我 → 服务 → 钱包 → 账单 → 常见问题 → 下载账单（CSV）
   python3 tools/reconcile.py alipay_bill.csv                 # 先看匹配结果
   python3 tools/reconcile.py alipay_bill.csv --apply         # 真的补单发证
   ```

   脚本按「金额完全一致 + 时间窗（默认 48h）」把账单收款与待支付订单对齐；
   同金额多单会列出来让你人工裁决，不会乱发证。适合每天/每周跑一次。

配置：

```json
"payment": {
  "provider": "manual",
  "manual": {
    "alipay": "your@alipay.com",
    "contact": "微信：your-wechat",
    "qr_image": "收款码图片地址（可选）",
    "unique_amount": true
  }
}
```

二维码内容默认是你授权服务的 `/pay/<order_id>` 收款页（页面上再放你的收款码/账号），
也可以把 `qr_content` 换成你自己的收款码链接（`alipays://…`、`wxp://…`）。

⚠️ 个人码的现实约束：央行 2021 年第 259 号文要求个人静态收款码不得用于经营性收款（2022-03-01 起），
长期、大额、异地扫码容易被限额或风控；想稳定长期卖，还是去办个体户/小微商户 → 支付宝当面付 / 微信 Native（有正规回调）。

### 5.2 `vmq` — V免签（个人免签，推荐）

原理：手机装「V免签监控端」App，监听支付宝/微信到账通知，收到后回调你的服务。

```json
"payment": {
  "provider": "vmq",
  "vmq": { "monitor_base": "http://你的监控端:8080", "key": "监控端密钥", "pay_type": 2 }
}
```

* `pay_type`: 1=微信，2=支付宝（跟监控端一致）
* 下单：`POST {monitor_base}/create`，签名 `md5(price+type+payId+key)`
* 回调：`/notify/vmq`，同样签名；服务端同时兼容三种常见拼接顺序（有密钥在，顺序猜测不降低安全性）

### 5.3 `epay` — 彩虹易支付 / 码支付协议

多数个人免签聚合站兼容这一套：

```json
"payment": {
  "provider": "epay",
  "epay": { "api_base": "https://pay.example.com", "pid": "1000", "key": "商户密钥",
            "pay_type": "alipay", "sitename": "MagnetSearch" }
}
```

* 下单：`GET {api_base}/submit.php?pid=..&type=..&out_trade_no=..&notify_url=..&money=..&sign=..`
  签名 = 参数按 key 升序 `k=v&…` 拼商户密钥后 MD5（自动剔除 `sign`/`sign_type`/空值）
* 回调：`/notify/epay`，校验 `pid`、`sign`、`trade_status=TRADE_SUCCESS`、金额

### 5.4 `generic` — 自建/任意聚合

回调 `/notify/generic`，签名 `HMAC-SHA256(按 key 升序的 k=v&…)`：

```json
"payment": { "provider": "generic", "generic": { "key": "你的网关密钥" } }
```

回调参数：`order_id`、`amount_cents`（或 `money`）、`trade_no`、`ts`、`sign`。
用来自建网关、或把别的支付商包一层。

### 4.4 下载页（发给用户的那个链接）

授权服务自带一个下载页，不用另装 nginx：

```
http://121.196.163.89:8899/download
```

* 页面自动列出 `public/` 里 `release.json` 声明的安装包，显示**版本、大小、SHA256**
* pkg / dmg 走 `/download/<文件名>` 直出（带 `Content-Disposition`，浏览器直接下载）
* 页面包含：安装三步、校验命令、Gatekeeper 报错处理、授权与付款说明、常见问题
* 安全：只允许下载 `public/` 下的 `.pkg/.dmg/.zip/.txt/.json`，路径穿透会被 404 拦掉（实测 `../server/config.json`、`..%2f..%2fetc%2fpasswd` 均 404）

目录结构：

```
/opt/magnetsearch-license/public/
├── release.json                # 版本号、日期、文件清单
├── MagnetSearch-3.3.pkg
└── MagnetSearch-3.3.dmg
```

发新版本一条命令（**走 HTTPS，不需要 SSH**，服务器 22 端口被墙时也能发）：

```bash
export MS_LICENSE_TOKEN=<后台 admin token>
bash licensing/tools/push_release.sh ../MagnetSearch-3.6.pkg ../MagnetSearch-3.6.dmg 3.6
```

对应接口（都在 `/admin/api/`，用 `X-Admin-Token` 鉴权）：

| 接口 | 作用 |
|---|---|
| `POST /admin/api/release/upload?name=xxx.pkg` | 原始字节上传（单文件 ≤300MB，落地到 `public/`，返回 sha256） |
| `POST /admin/api/release/publish` | `{version, files, notes}` → 重写 `release.json`，下载页立即切换 |
| `GET  /admin/api/release/list` | 查看当前版本与已上传文件 |

旧的 `scp + tar` 流程（下面这段）作为备用保留：

发新版本一条命令：

```bash
bash licensing/tools/publish_release.sh ../MagnetSearch-3.4.pkg ../MagnetSearch-3.4.dmg 3.4
```

（会自动更新 `release.json`、scp 到服务器 `/opt/magnetsearch-license/public/` 并 chown。）

### 5.5.1 `paywall` — 付费墙 + 一次性授权码（当前线上方案）

适合「只有一个固定付费图/付费页，付款成功后平台跳到指定网址」的场景（8图片/北尚互联这类）。
把「跳转成功」当作付款凭证，链路：

```
app 收银台 ── 展示付费图二维码 ──► 用户扫码付款
                                      │ 平台判定已付
                                      ▼
                       平台跳转到 http://<你的域名>/claim/<CLAIM_SECRET>
                                      │ 本站当场发一张一次性授权码（6 位，120 分钟有效）
                                      ▼
                  用户把码填回 app 收银台的「授权码」框 → 点开通
                                      │ POST /api/v1/redeem
                                      ▼
                        服务端核销该码并绑定这台设备 → 永久授权
```

配置：

```json
"payment": {
  "provider": "paywall",
  "paywall": {
    "url": "https://bs.97ta.xyz/p.php?8tp=t1.9681a7280b2900.pg1",
    "claim_secret": "35fd33a773d3d561150e6633e3b835d3",
    "code_ttl_minutes": 120,
    "max_claims_per_hour_per_ip": 30
  }
}
```

#### 二维码显示方式（`qr_mode`）

平台的付费页其实是一张**空白图 + 左下角一个收款链接二维码**，我们把这个链接解出来直接画进弹窗，
买家就不用先去翻那张网页（`qr_mode=image`，默认）：

* 客户端每次打开收银台时，拉一次付费页 → 解 base64 图片 → Vision 识别二维码 → 拿到形如
  `http://a.yakejiayuan.com/3.php?p=9681_7281_2900_xxx&t=YYYY_MM_DD_HH&q=0` 的收款链接
  → 用 CoreImage 重新画一张高清二维码显示
* 该链接会展开成 `https://bs.97ta.xyz/pay.php?fee=2900&orderid=<每单唯一>&picurl=…`，
  **fee 就是图的价格（分）**、orderid 每次扫码新建 → 付款后平台再跳回我们 `/claim/<密钥>`
* 链接里带小时级时间桶（`t=YYYY_MM_DD_HH`），所以每次开弹窗都会重新取一次；
  解不出来时自动退回「显示付费页地址的二维码」（`qr_mode=page`），或在 config 里写死
  `payment.paywall.qr_payload` 作为兜底
* 文案由 `payment.paywall.note` 控制，默认「请使用支付宝扫码支付」，后面自动跟金额

```json
"paywall": {
  "url": "https://bs.97ta.xyz/p.php?8tp=t3.9681a7281b2900.pg3",
  "qr_mode": "image",
  "note": "请使用支付宝扫码支付"
}
```

平台侧（北尚互联/8图片后台）两个必须设置：

1. **图片价格 = 29.00 元**（和 `product.price_cents` 对上）
2. **「网址或文字」填证书发放页**：`http://121.196.163.89:8899/claim/35fd33a773d3d561150e6633e3b835d3`
   （即模式三「支付后跳转到指定网址」；这个 URL 就是付款凭证，别公开贴到任何地方）

安全设计（在不接支付回调的前提下能做的都在做）：

| 措施 | 作用 |
|---|---|
| `/claim/<CLAIM_SECRET>` 校验路径里的密钥 | 猜不到地址就领不到码；密钥可随时改 config 重启轮换 |
| 授权码 6 位、去掉易混字符、一次性 | 一个码只能激活一台设备，用过即失效 |
| 120 分钟过期 | 限制传播窗口 |
| 每 IP 每小时最多 30 个码 | 防止有人拿跳转地址刷码囤货 |
| 兑换时校验设备并记录 IP/时间 | `claim_codes` 表可回溯，异常可封禁设备 |

已知边界（如实说明）：**「跳转即成功」意味着判定权在平台/用户侧**。
如果用户拿到跳转 URL 后手动访问，也能生成码；把码转发给朋友，朋友也能激活一台。
要做到严格一一对应，只有两条路：接真实支付回调（V免签/易支付/当面付），或者改成人工核对付款截图。
现阶段用「一码一机 + 限时限速 + 可轮换密钥」把这个风险压到「白嫖需要自己先付一次钱」的程度。

#### 设置页面里的授权区（客户端注入）

app 的设置页是 SwiftUI 画的，没有源码，所以从 AppKit 层注入（`client/SettingsLicense.swift`）：

* 监听所有 sheet 窗口，用「是 sheet + 450×350 尺寸 + 不是我们的收银台」认出设置页
* 把底部那两行无效提示（`局域网 IP: …`、`其他设备访问: …`）整块盖掉，替换成一条授权栏：

  ```
  ─────────────────────────────────────────────
  授权
  试用中 · 剩余 6 天 23 小时            了解购买 ›
  ```

  已买断时显示 `已永久授权 · 终身可用`（绿色）
* 「了解购买 ›」先 `endSheet` 关掉设置，再拉起收银台弹窗
* 面板挂在 contentView 的父视图（theme frame）上——SwiftUI 的 hostingView 会吞掉鼠标事件，
  挂在它内部按钮点不动

其它常驻提示（不占用 app 界面空间）：

| 位置 | 内容 |
|---|---|
| 窗口标题 | `MagnetSearch · 试用 6 天 23 小时`（到期变 `试用已结束`） |
| Dock 图标角标 | `6天` / `已到期`（买断后清空） |
| 首次启动 | 一次性 sheet：已开始 7 天免费试用 + 剩余天数 + [开始使用] [了解购买] |

### 5.6 北尚互联 / 8图片（`bs.97ta.xyz`、`www.8tupian.com`）能不能当收款通道？

**结论：不能。它是「付费内容墙」，不是「收单网关」。** 核对过它的官方接口文档：

| 它提供的 API | 说明 |
|---|---|
| 查询商户信息 / 查询图片 / 删除图片 / 上传图片（接口1、2 免审核） | 都是围绕「付费图片」这个业务 |
| 图片地址用自己的域名 | 反代它的付费页 |
| 对接自己的支付接口 | **方向是反的**：用户扫码 → 它 GET 你的支付地址（`orderid`、`fee`、`picurl`）→ 你收钱 |
| `http://web.8tupian.com/api/notify.php?orderid=&fee=&sign=` | **你去通知它**「用户已付款，请放行图片」，不是它通知你 |

也就是说：**它从来没有「创建订单 → 支付成功 → 异步回调商户」的收单接口**。
它的 `notify.php` 是商户（你）向平台汇报，签名用平台的 key；它并不掌握你的到账事实。

它的两种收款方式，对你的场景分别是：

* **方式一（平台自带个人收款码）**：平台代收 → 结算到你账户，抽 6%（北尚互联页写明，次日结算，满 10 元可提）。
  属于「二清」资金池：钱先进平台账户，平台跑路/冻结你就没了；而且只支持支付宝，不支持微信。
  更关键：付款后的「放行」由它判定，**你能拿到的最多是「这单已付」的页面跳转，不是可验签的服务端回调**，
  拿它给「永久买断发证」做依据不可靠。
* **方式二（对接自己的支付接口）**：它把用户导到你自己的支付地址，每单收 0.2 元服务费。
  但前提是你**先得有一个能收款的接口** —— 又回到「个人收款码怎么收」的原点，绕了一圈没解决问题。

可行的借力方式只有一个，而且只适合「卖内容」不适合「发授权」：
用它的**模式三（支付后跳转到指定网址）**做个付费页，跳回你授权服务的 `/pay/return?oid=…`。
但跳转 URL 一旦被拿到就能反复访问，等于把「已付款」的判定交给了用户，**不能作为发证依据**（除非再加人工复核）。
所以不推荐，也没有为它写适配器。

**该走哪条？**

| 你的情况 | 选 |
|---|---|
| 只有个人收款码，想要自动到账 | **V免签监控端 + `provider=vmq`**（个人码 + 一台常开安卓手机监听通知，0 费率、秒级回调） |
| 只有个人收款码，不想折腾手机 | `provider=manual`（唯一金额尾数 + `admin.py pay` / `reconcile.py` 对账，半自动） |
| 有第三方个人免签聚合平台的 pid/key | `provider=epay`（易支付协议，抽 2%~6%，注意平台跑路风险） |
| 想长期稳定、正规发票 | 个体户/小微商户 → 支付宝当面付 / 微信 Native（费率 0.38%~0.6%，有官方回调，可再加一个 provider） |

### 5.5 本地假网关（零成本验流程）

```bash
python3 tools/fake_gateway.py --port 8080     # 模拟 V免签监控端 + 网页收银台
```

`payment.provider=vmq`、`monitor_base=http://127.0.0.1:8080` 即可在真机上把
「下单 → 出二维码 → 点我付了 → 回调 → 自动解锁」全流程走一遍。

---

## 六、客户端构建与发版

```bash
# 1) 配置（服务端地址/价格/试用天数/公钥都在 config.json）
vi licensing/config.json

# 2) 编译注入库
bash licensing/client/build_license.sh
#    产物：licensing/build/libMagnetSearchLicense.dylib
#          licensing/build/libMagnetSearchPatch.dylib（补丁库 + 授权钩子）

# 3) 装进 app（会重签）
bash licensing/client/apply_to_app.sh /Applications/MagnetSearch.app

# 4) 打包（build_pkg.sh 会自动带上 licensing/build 里的库）
bash packaging/build_pkg.sh /Applications/MagnetSearch.app 3.3
```

发版前 checklist：

- [ ] `config.json → server.public_base` 是公网 HTTPS 地址
- [ ] `config.json → signing.public_key_hex` 与服务端私钥匹配（`admin.py pubkey` 对比）
- [ ] 价格、试用天数、`recheck_hours` 符合预期
- [ ] `codesign --force --deep --sign -` 后 `codesign -dv` 正常（正式分发建议用开发者证书 + 公证）
- [ ] 装到一台干净机器上跑一次：首次启动 → 试用提示 → 到期收银台 → 付款 → 解锁 → 重启仍解锁 → 断网仍解锁

---

## 七、后台与运维

| 操作 | 方式 |
|---|---|
| 概览（设备/授权/收入） | `/admin/` 后台，或 `python3 server/admin.py stats` |
| 设备列表 | 后台，或 `admin.py devices --q 关键词` |
| 订单列表 / 待支付 | 后台，或 `admin.py orders --status paid` |
| 手动补单 | 后台「标记已付」，或 `POST /admin/api/order/paid` |
| 手动授权（送测/客服） | `admin.py grant --device dev-xxx [--days 30]`（不带 days = 终身） |
| 吊销（退款） | `admin.py revoke --device dev-xxx`；客户端最长 `recheck_hours` 内收回 |
| 封设备（连试用都停） | `admin.py block --device dev-xxx` |
| 导出/备份 | 直接备份 `license.db`（SQLite WAL，`sqlite3 license.db ".backup bak.db"`） |
| 审计 | `GET /admin/api/events`，或查 `events` 表 |

运维要点：

* **数据库是唯一真相**。备份 `license.db` 比什么都重要（丢了等于所有买断用户都要重新处理）。
* 服务挂了不影响已买断用户（离线验签），只影响新用户试用登记与下单 —— 所以 `public_base` 建议挂个 CDN/多线路。
* `order_ttl_seconds` 默认 900 秒；过期订单由后台线程每分钟清理。

---

## 八、风控与已知边界

设计上能顶住的：

| 攻击 | 结果 |
|---|---|
| 删 `~/Library/Application Support/MagnetSearchLicense` | 无效：服务端 `trial_start` 不变，重装/删文件都拿回同一试用到期的判定 |
| 只删 json 不删 UserDefaults（反之亦然） | 本地状态存两份，删一份仍能读到 |
| 改本地 token | 验签失败 → 回落联网 → 服务端重新签发 |
| 改客户端里的价格 | 无效：金额由服务端下单决定，回调还要校验一致 |
| 改系统时钟延后到期 | 本地记录服务端时间偏移 + 时间水位，回拨基本无效（每次联网进一步校准） |
| 截获别人的 token 拷到自己机器 | 无效：token 绑定 `did`，客户端校验设备指纹 |
| 伪造支付回调 | 需渠道密钥（签名校验），且金额必须与订单一致 |
| 重放回调 | 订单状态机幂等，`paid` 后不再重复发证 |

设计上**不做**的（写清楚，避免过度承诺）：

* **不防反编译**。dylib 里有公钥、有校验逻辑，技术用户能把它 patch 掉 —— 这是所有本地授权方案的共同上限；
  能防的是「普通用户顺手白嫖」，防不住「职业破解者」。想提高成本可以后续叠：代码混淆、把关键判定放到服务端下发、
  关键功能依赖服务端数据（本项目的搜索服务天然有这个位置：可以让 Torznab 接口带授权校验）。
* **退款依赖你手动吊销**：支付渠道不会自动通知退款；`admin.py revoke` 后联网客户端在 `recheck_hours` 内失效。
* **纯离线用户**（装完从不联网）拿不到授权，也永远不会被收回 —— 这是「买断 + 离线可用」的必然代价，业务上可接受。

---

## 九、故障排查

| 现象 | 排查 |
|---|---|
| app 启动没有试用提示、也没有收银台 | `MS_LICENSE_DISABLE=1` 环境变量？dylib 没装上？`otool -L MagnetSearch` 看有没有 `libMagnetSearchPatch.dylib`；Console 里搜 `[LIC]` 日志 |
| 收银台一直转圈/「下单失败」 | `curl -s $PUBLIC_BASE/healthz`；看服务日志里渠道报错（V免签监控端不可达最常见） |
| 付款成功但 app 不解锁 | 回调没到：查 `orders.raw`、`events` 里有没有 `notify.*`；`public_base` 是否公网可达；渠道后台的 `notify_url` 是否被墙/被 WAF 拦 |
| 提示「签名无效」/「token 与当前设备不匹配」 | 客户端公钥和服务端私钥不是一对（重新 `gen_client_config.py` + 重新构建）；或用户换了主板 |
| 用户说试用被重置了 | 正常不会。查 `devices` 里该 `device_id` 的 `first_seen` 是否多条（说明指纹不稳定，通常是沙盒/虚拟机） |
| 想临时关掉门禁做调试 | 启动时 `MS_LICENSE_DISABLE=1 open -a MagnetSearch`，或 `MS_LICENSE_SERVER=http://127.0.0.1:8899` 指到本地服务 |

---

## 十、和旧方案的关系

仓库里原本有一个 `patch/patch_license.py`，作用是「把上游 app 自带的 24 小时试用/激活校验 patch 掉」。
本方案是**反向补齐**：不再依赖上游那套逻辑，而是用自己的门禁（服务端权威 + 签名凭证）。
两者不要同时用 —— 保留 `patch_license.py` 只会让二进制更难解释；需要合规售卖时直接删掉这一步。
