#!/bin/bash
# 通过 HTTPS 发布新版本到下载页（不需要 SSH！）
#   export MS_LICENSE_TOKEN=120da27515f516e90f5cc83a0dd2411e
#   bash licensing/tools/push_release.sh <pkg> [dmg] [版本]
set -euo pipefail
PKG="${1:?用法: push_release.sh <pkg> [dmg] [版本]}"
DMG="${2:-}"
VER="${3:-$(basename "$PKG" | sed -E 's/.*-([0-9.]+)\.pkg/\1/')}"
BASE="${MS_LICENSE_BASE:-https://121.196.163.89.sslip.io}"
TOKEN="${MS_LICENSE_TOKEN:?需要 MS_LICENSE_TOKEN（后台 admin token）}"

up() {
  local f="$1"
  printf "  上传 %-26s " "$(basename "$f")"
  curl -sS --fail-with-body -m 600 -X POST \
    -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/octet-stream" \
    --data-binary "@$f" "$BASE/admin/api/release/upload?name=$(basename "$f")" \
    | python3 -c "import json,sys;d=json.load(sys.stdin)['data'];print('%.2f MB  sha256=%s' % (d['size']/1048576, d['sha256'][:16]+'…'))"
}
up "$PKG"
FILES="\"$(basename "$PKG")\""
[ -n "$DMG" ] && { up "$DMG"; FILES="$FILES, \"$(basename "$DMG")\""; }

printf "  切换下载页 → %s  " "$VER"
curl -sS --fail-with-body -m 60 -X POST -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "{\"version\":\"$VER\",\"files\":[$FILES]}" "$BASE/admin/api/release/publish" \
  | python3 -c "import json,sys;print('ok  files=', json.load(sys.stdin)['data']['files'])"
echo "✅ https://121.196.163.89.sslip.io/download 已是 $VER"
