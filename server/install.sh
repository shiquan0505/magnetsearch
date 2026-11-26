#!/bin/bash
# MagnetSearch 本地搜索服务安装脚本（优先用自带的可执行文件，无 Python 依赖）
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/MagnetSearchBackend"
AGENT="$HOME/Library/LaunchAgents/com.magnetsearch.backend.plist"
LOG="$HOME/Library/Logs/MagnetSearchBackend.log"

# 去掉下载隔离属性，避免 Gatekeeper 拦住脚本/可执行文件
xattr -dr com.apple.quarantine "$HERE" 2>/dev/null || true

mkdir -p "$DEST" "$(dirname "$AGENT")" "$(dirname "$LOG")"

if [ -x "$HERE/magnetsearch-server" ]; then
    cp "$HERE/magnetsearch-server" "$DEST/"
    chmod +x "$DEST/magnetsearch-server"
    xattr -dr com.apple.quarantine "$DEST/magnetsearch-server" 2>/dev/null || true
    PROG="$DEST/magnetsearch-server"
    ARGS=""
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
    cp "$HERE/torznab_server.py" "$HERE/sources.py" "$HERE/match.py" "$DEST/"
    PROG="$(command -v python3)"
    ARGS="$DEST/torznab_server.py"
    echo "ℹ️  未找到打包版，改用系统 python3：$PROG"
else
    echo "❌ 缺少可执行文件，且系统没有可用的 python3。"
    echo "   请安装「命令行开发者工具」（终端执行 xcode-select --install）后重试。"
    exit 1
fi

cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.magnetsearch.backend</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROG</string>
        ${ARGS:+<string>$ARGS</string>}
        <string>--host</string><string>127.0.0.1</string>
        <string>--port</string><string>3333</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl unload "$AGENT" 2>/dev/null || true
launchctl load -w "$AGENT"
sleep 3

if curl -s -m 6 http://127.0.0.1:3333/health | grep -q ok; then
    echo "✅ 搜索服务已启动：http://127.0.0.1:3333"
    echo "   日志：$LOG"
else
    echo "⚠️  服务未响应，请查看日志：$LOG"
    exit 1
fi
