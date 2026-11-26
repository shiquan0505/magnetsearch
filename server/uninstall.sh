#!/bin/bash
AGENT="$HOME/Library/LaunchAgents/com.magnetsearch.backend.plist"
launchctl unload "$AGENT" 2>/dev/null || true
rm -f "$AGENT"
rm -rf "$HOME/Library/Application Support/MagnetSearchBackend"
echo "已卸载搜索服务"
