#!/bin/bash
# 发布新版本到下载页：把安装包放到 public/、更新 release.json、同步到云服务器
#
#   bash licensing/tools/publish_release.sh <pkg文件> [dmg文件] [版本号]
# 例：
#   bash licensing/tools/publish_release.sh ../MagnetSearch-3.4.pkg ../MagnetSearch-3.4.dmg 3.4
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PKG="${1:?用法: publish_release.sh <pkg> [dmg] [版本]}"
DMG="${2:-}"
VER="${3:-$(basename "$PKG" | sed -E 's/.*-([0-9.]+)\.pkg/\1/')}"
HOST="${MS_HOST:-root@121.196.163.89}"
DEST="${MS_DEST:-/opt/magnetsearch-license}"

[ -f "$PKG" ] || { echo "找不到 $PKG"; exit 1; }
mkdir -p "$ROOT/public"
cp -f "$PKG" "$ROOT/public/"
FILES="$(basename "$PKG")"
if [ -n "$DMG" ] && [ -f "$DMG" ]; then
    cp -f "$DMG" "$ROOT/public/"
    FILES="$FILES\", \"$(basename "$DMG")"
fi

python3 - "$ROOT/public/release.json" "$VER" "$FILES" <<'PY'
import json, sys, time
path, ver, files = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    meta = json.load(open(path))
except Exception:
    meta = {}
meta["version"] = ver
meta["date"] = time.strftime("%Y-%m-%d")
meta["files"] = [f.strip() for f in files.split('", "')]
json.dump(meta, open(path, "w"), ensure_ascii=False, indent=2)
print("release.json →", meta["files"])
PY

echo "== 同步到 $HOST:$DEST/public =="
tar czf /tmp/ms-public.tgz -C "$ROOT" public
scp /tmp/ms-public.tgz "$HOST:/root/ms-public.tgz"
ssh "$HOST" "cd /tmp && rm -rf pub && mkdir pub && tar xzf ms-public.tgz -C pub && \
  mkdir -p $DEST/public && cp -f pub/public/* $DEST/public/ && \
  chown -R mslic:mslic $DEST/public && systemctl is-active magnetsearch-license"
echo "✅ 下载页：http://121.196.163.89:8899/download"
