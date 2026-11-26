#!/bin/bash
# 把授权库装进 app 并重签。
#
#   bash licensing/client/apply_to_app.sh /Applications/MagnetSearch.app
#   bash licensing/client/apply_to_app.sh /tmp/MagnetSearch-test.app --build   # 顺带重新编译
#
# 做的事：
#   1. 复制 libMagnetSearchLicense.dylib / libMagnetSearchPatch.dylib 到 Contents/Frameworks
#   2. 主程序里保证有一条 LC_LOAD_DYLIB → libMagnetSearchPatch.dylib（已有则跳过）
#   3. codesign --force --deep --sign -（ad-hoc，先用本机验证；正式发版按 docs 里说的证书签）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIC_ROOT="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$LIC_ROOT/.." && pwd)"
BUILD="$LIC_ROOT/build"

APP="${1:?用法: apply_to_app.sh <MagnetSearch.app> [--build]}"
[ -d "$APP" ] || { echo "找不到 app：$APP"; exit 1; }

if [ "${2:-}" = "--build" ] || [ ! -f "$BUILD/libMagnetSearchLicense.dylib" ]; then
    bash "$HERE/build_license.sh"
fi

MAIN="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
FW="$APP/Contents/Frameworks"
mkdir -p "$FW"

echo "== 1/3 安装 dylib =="
cp "$BUILD/libMagnetSearchLicense.dylib" "$FW/"
cp "$BUILD/libMagnetSearchPatch.dylib" "$FW/"
chmod +x "$FW/libMagnetSearchLicense.dylib" "$FW/libMagnetSearchPatch.dylib"
echo "   $FW/libMagnetSearchLicense.dylib"
echo "   $FW/libMagnetSearchPatch.dylib（覆盖原兼容补丁库，已内置授权钩子）"

echo "== 2/3 确保主程序加载补丁库 =="
if otool -L "$MAIN" | grep -q libMagnetSearchPatch.dylib; then
    echo "   已存在 LC_LOAD_DYLIB → libMagnetSearchPatch.dylib"
else
    python3 "$ROOT/patch/add_load_dylib.py" "$MAIN" "@executable_path/../Frameworks/libMagnetSearchPatch.dylib"
fi

echo "== 3/3 重签名（ad-hoc）=="
xattr -cr "$APP" 2>/dev/null || true
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null 2>&1 || \
    codesign --force --deep --sign - "$APP" >/dev/null
codesign -dv "$APP" 2>&1 | sed 's/^/   /' | head -4

echo
echo "✅ 完成。启动前请确认授权服务已运行："
echo "   python3 licensing/server/admin.py serve"
echo "   自检：curl -s http://127.0.0.1:8899/healthz"
