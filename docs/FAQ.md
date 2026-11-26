# 常见问题

## 1. 双击 app 没反应 / 一闪就退

先看它是不是被 Gatekeeper 拦了：

```bash
xattr -l /Applications/MagnetSearch.app        # 有 com.apple.quarantine 就是被隔离了
xattr -dr com.apple.quarantine /Applications/MagnetSearch.app
```

还不行就直接在终端跑，让 dyld 把原因打出来：

```bash
/Applications/MagnetSearch.app/Contents/MacOS/MagnetSearch
```

- `dyld: Symbol not found: ...` → 补丁库没被加载（检查 `Contents/Frameworks/libMagnetSearchPatch.dylib`
  是否存在、主程序是否有 `LC_LOAD_DYLIB` 指向它）
- `kLSIncompatibleSystemVersionErr` → `LSMinimumSystemVersion` 没改成功
- 直接段错误 → 版本对不上，补丁偏移需重新定位

## 2. 显示"未找到相关资源"

先确认服务活着：

```bash
curl -s http://127.0.0.1:3333/health          # 应返回 ok
curl -s http://127.0.0.1:3333/sources         # 看哪些源在冷却
curl -s "http://127.0.0.1:3333/torznab/api?t=search&q=matrix" | head -c 400
```

- `health` 不通 → 看 `~/Library/Logs/MagnetSearchBackend.log`，常见是端口 3333 被占用
- `sources` 全是"冷却中" → 你的网络到不了那些站，配个代理
  （`MAGNET_PROXY=http://127.0.0.1:7897` 或改 plist 加 `--proxy`）
- 接口能出结果但 app 里没有 → app 的服务器地址不是 `http://localhost:3333`
  （右上角 Settings 里改）

## 3. 中文搜不到 / 搜出来不相关

- 中文源目前稳定可用的只有 `cilisousuo`，长尾片名确实可能没有
- 已内置 **IMDb 译名回退**：中文结果不足时会自动用英文名再搜一轮
  （结果来源会显示 `TPB ⭐Dune: Part Two` 这种标记）
- 搜出来一堆无关内容 → 那是站点的模糊匹配，已被相关性过滤挡掉大部分；
  如果仍有污染，换更完整的片名（加年份、加"第二季"）

## 4. Apple 芯片能用吗

能，但走 Rosetta 2（上游二进制只有 x86_64）。第一次启动系统会提示安装，或手动：

```bash
softwareupdate --install-rosetta --agree-to-license
```

## 5. 为什么是 3333 端口

上游 app 把 Torznab 地址硬编码成了 `http://<主机>:3333/torznab/api?t=search&q=`。
端口被占用时服务起不来，改端口就得同时改 app 里的服务器地址（Settings 可改）。

## 6. 会不会被 Gatekeeper 拦

默认会——app 是 **ad-hoc 签名、没有公证**。三种处理：

1. 右键 → 打开（macOS 14 及以前）
2. 系统设置 → 隐私与安全性 → "仍要打开"（macOS 15）
3. `xattr -dr com.apple.quarantine /Applications/MagnetSearch.app`

想彻底没有提示只能做 Developer ID 签名 + 公证（99 美元/年，与上架 App Store 是两回事）。

## 7. 能上架 App Store 吗

基本不可能，且不是改改就行：

- **2.4.5(i)**：Mac App Store 应用必须沙箱；沙箱里无法注册 LaunchAgent、也无法起外部常驻进程
- **2.5.2**：必须自包含，不得依赖外部可执行文件
- **5.2.3**：不得助长非法文件共享 / 无授权下载媒体
- **1.1**：搜索结果里的成人内容会被直接拒
- 另外本项目是**给第三方二进制打补丁**，签名、来源、arm64 都过不了

## 8. 有没有内置资源库

没有。服务只是把公开索引站的检索结果转成 Torznab，不存储任何种子或文件。

## 9. 怎么加/换搜索源

`server/sources.py` 里每个源一个函数，签名固定：

```python
def mysource(q):
    html = get("https://example.com/search?q=" + urllib.parse.quote(q))
    return [_item(title, infohash, size, seeders, peers, "MySource")]
```

然后加进 `REGISTRY` 和 `EN_SOURCES` / `CN_SOURCES` 即可。

## 10. 怎么彻底恢复原版

```bash
bash server/uninstall.sh          # 停服务、删自启
rm -rf /Applications/MagnetSearch.app
```

原版 app 从上游重新获取即可（本仓库不修改也不分发它）。
