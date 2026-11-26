---
name: 兼容性反馈 / Compatibility report
about: 报告某个 macOS 版本 / CPU 上能否正常运行
title: "[兼容性] macOS <版本> / <CPU> "
labels: compatibility
---

## 环境

- macOS 版本（`sw_vers`）：
- CPU 架构（`uname -m`）：
- 是否 Apple 芯片走 Rosetta：
- app 版本（`/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" /Applications/MagnetSearch.app/Contents/Info.plist`）：

## 结果

- [ ] 能启动并进入主界面
- [ ] 能搜索出结果
- [ ] 闪退 / 报错（贴下面的日志）

## 日志

```bash
# 直接运行看 dyld 报错
/Applications/MagnetSearch.app/Contents/MacOS/MagnetSearch

# 搜索服务
curl -s http://127.0.0.1:3333/health
tail -50 ~/Library/Logs/MagnetSearchBackend.log
```

```
（粘贴输出）
```
