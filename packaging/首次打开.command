#!/bin/bash
# 双击运行：清除下载隔离 + 安装搜索服务 + 打开 app
cd "$(dirname "$0")"
APP="/Applications/MagnetSearch.app"
[ -d "$APP" ] || APP="$PWD/MagnetSearch.app"

echo "== 1/3 清除下载隔离属性（Gatekeeper）=="
xattr -dr com.apple.quarantine "$APP" 2>/dev/null && echo "   OK: $APP" || echo "   跳过（无隔离属性）"
xattr -dr com.apple.quarantine "$PWD/MagnetSearch 搜索服务" 2>/dev/null || true

echo "== 2/3 安装本地搜索服务 =="
bash "$PWD/MagnetSearch 搜索服务/install.sh"

echo "== 3/3 启动 MagnetSearch =="
open "$APP"
echo
echo "完成。若 macOS 仍提示无法打开，去 系统设置 → 隐私与安全性 → 仍要打开。"
read -n 1 -s -r -p "按任意键关闭本窗口"
