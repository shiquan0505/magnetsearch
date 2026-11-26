#!/bin/bash
# 打 .pkg 安装包。用法: bash packaging/build_pkg.sh <MagnetSearch.app> [版本号]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="${1:?用法: build_pkg.sh <MagnetSearch.app> [版本]}"
VERSION="${2:-3.2}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/payload/Applications" "$WORK/scripts"
cp -R "$APP" "$WORK/payload/Applications/"
APPCOPY="$WORK/payload/Applications/$(basename "$APP")"

# 搜索服务二进制（存在就用，没有就跳过——运行时会退回 python3）
BIN="$ROOT/server/magnetsearch-server"
if [ -f "$BIN" ]; then
    mkdir -p "$APPCOPY/Contents/Resources/backend"
    cp "$BIN" "$APPCOPY/Contents/Resources/backend/"
    chmod +x "$APPCOPY/Contents/Resources/backend/magnetsearch-server"
fi

cp "$HERE/preinstall" "$HERE/postinstall" "$WORK/scripts/"
chmod +x "$WORK/scripts/"*
# postinstall 里的 APP 路径按实际包名替换
sed -i '' "s|/Applications/MagnetSearch.app|/Applications/$(basename "$APP")|g" "$WORK/scripts/postinstall"

# 授权门禁库（licensing/build 里存在就装进去：试用 7 天 + 买断解锁）
LIC_BUILD="$ROOT/licensing/build"
if [ -f "$LIC_BUILD/libMagnetSearchLicense.dylib" ]; then
    mkdir -p "$APPCOPY/Contents/Frameworks"
    cp "$LIC_BUILD/libMagnetSearchLicense.dylib" "$LIC_BUILD/libMagnetSearchPatch.dylib" \
       "$APPCOPY/Contents/Frameworks/"
    chmod +x "$APPCOPY/Contents/Frameworks/"*.dylib
    MAINEXEC="$APPCOPY/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APPCOPY/Contents/Info.plist")"
    if ! otool -L "$MAINEXEC" | grep -q libMagnetSearchPatch.dylib; then
        python3 "$ROOT/patch/add_load_dylib.py" "$MAINEXEC" \
            "@executable_path/../Frameworks/libMagnetSearchPatch.dylib"
    fi
    echo "已打入授权门禁库（试用 + 买断）"
else
    echo "⚠️  未找到 licensing/build/*.dylib，本次包不含授权门禁（先跑 licensing/client/build_license.sh）"
fi

codesign --force --deep --sign - --timestamp=none "$APPCOPY"
[ -f "$HERE/卸载 MagnetSearch.command" ] && cp "$HERE/卸载 MagnetSearch.command" "$WORK/payload/Applications/"

cd "$WORK"
pkgbuild --root payload --scripts scripts \
    --identifier com.magnetsearch.pkg --version "$VERSION" \
    --install-location / core.pkg >/dev/null

# ⚠️ 关键：删掉 <relocate>，否则 Installer 会把 app 装到磁盘上已有的同 id 副本那里
rm -rf exp && pkgutil --expand core.pkg exp
python3 - <<'PY'
import re
p = 'exp/PackageInfo'
s = open(p).read()
s = re.sub(r'\s*<relocate>.*?</relocate>', '', s, flags=re.S)
s = re.sub(r'\s*<upgrade-bundle>.*?</upgrade-bundle>', '', s, flags=re.S)
open(p, 'w').write(s)
print('relocate removed:', '<relocate>' not in s)
PY
pkgutil --flatten exp core-fixed.pkg

python3 - "$HERE/distribution.xml" distribution.xml <<'PYEOF'
import sys
src, dst = sys.argv[1], sys.argv[2]
open(dst, 'w').write(open(src).read().replace('>core.pkg<', '>core-fixed.pkg<'))
PYEOF
OUT="$ROOT/MagnetSearch-${VERSION}.pkg"
productbuild --distribution distribution.xml --package-path . --resources . "$OUT" >/dev/null
echo "✅ $OUT"
