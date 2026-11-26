#!/bin/bash
# 打 .dmg 手动版。用法: bash packaging/build_dmg.sh <MagnetSearch.app> [版本号]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="${1:?用法: build_dmg.sh <MagnetSearch.app> [版本]}"
VERSION="${2:-3.2}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/stage/MagnetSearch 搜索服务"
cp -R "$APP" "$WORK/stage/"
ln -s /Applications "$WORK/stage/应用程序"

cp "$ROOT/server/torznab_server.py" "$ROOT/server/sources.py" \
   "$ROOT/server/install.sh" "$ROOT/server/uninstall.sh" \
   "$ROOT/server/com.magnetsearch.backend.plist" "$WORK/stage/MagnetSearch 搜索服务/"
[ -f "$ROOT/server/magnetsearch-server" ] && \
   cp "$ROOT/server/magnetsearch-server" "$WORK/stage/MagnetSearch 搜索服务/"
cp "$HERE/首次打开.command" "$HERE/卸载 MagnetSearch.command" "$WORK/stage/" 2>/dev/null || true
[ -f "$ROOT/README.md" ] && cp "$ROOT/README.md" "$WORK/stage/使用说明.md"

OUT="$ROOT/MagnetSearch-${VERSION}.dmg"
hdiutil create -volname "MagnetSearch" -srcfolder "$WORK/stage" -ov -format UDZO "$OUT" >/dev/null
echo "✅ $OUT"
