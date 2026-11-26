#!/bin/bash
# 发布到 GitHub：建仓库 → 推代码 → 发 Release（带 pkg/dmg）
#
# 不需要 gh，只要有 token（浏览器 10 秒就能拿到）：
#   https://github.com/settings/tokens/new  → 勾 repo → 生成 → 复制
#   export GITHUB_TOKEN=ghp_xxxxxxxx
#   bash marketing/github/publish.sh <你的GitHub用户名> [仓库名]
#
# 只做演练（不真发）：加 --dry-run
set -euo pipefail
USER_NAME="${1:?用法: publish.sh <github用户名> [仓库名] [--dry-run]}"
REPO="${2:-magnetsearch}"
DRY=0; [ "${3:-}" = "--dry-run" ] && DRY=1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
TOKEN="${GITHUB_TOKEN:-}"
TAG="v3.4"

api() {  # api <method> <path> [json]
  local m="$1" p="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$m" -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
      -d "$body" "https://api.github.com$p"
  else
    curl -sS -X "$m" -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" \
      "https://api.github.com$p"
  fi
}

[ "$DRY" = "0" ] && [ -z "$TOKEN" ] && {
  echo "缺少 GITHUB_TOKEN。先生成：https://github.com/settings/tokens/new （勾 repo）"
  echo "然后：export GITHUB_TOKEN=ghp_xxx && bash $0 $USER_NAME $REPO"; exit 1; }

echo "== 1/5 本地仓库 =="
if [ "$DRY" = "1" ]; then
  echo "  [dry-run] git init / add / commit（演练不执行）"
else
[ -d .git ] || git init -q
cat > .gitignore <<'IGN'
*.pkg
*.dmg
licensing/public/*.pkg
licensing/public/*.dmg
licensing/keys/
licensing/build/
licensing/*.db*
server/build/
server/dist/
server/*.spec
.DS_Store
__pycache__/
IGN
git add -A
git commit -q -m "MagnetSearch 3.4：搜索容错/中文拆词 + 授权收款 + 下载页" 2>/dev/null || echo "  （无新变更）"
echo "  commit: $(git rev-parse --short HEAD)"
fi

echo "== 2/5 建仓库 =="
if [ "$DRY" = "1" ]; then
  echo "  [dry-run] POST /user/repos  {name:$REPO, private:false}"
else
  RESP=$(api POST /user/repos "{\"name\":\"$REPO\",\"description\":\"macOS 磁力搜索客户端 + 本地 Torznab 服务\",\"private\":false,\"has_issues\":true}")
  echo "$RESP" | grep -q '"full_name"' && echo "  已创建 https://github.com/$USER_NAME/$REPO" \
    || { echo "  建仓返回：$(echo "$RESP" | head -c 200)（若已存在可忽略）"; }
fi

echo "== 3/5 推送代码 =="
if [ "$DRY" = "1" ]; then
  echo "  [dry-run] git push https://x-access-token:***@github.com/$USER_NAME/$REPO.git HEAD"
else
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://x-access-token:$TOKEN@github.com/$USER_NAME/$REPO.git"
  git push -q -u origin HEAD:main 2>&1 | tail -2 || git push -q -F origin HEAD:main
  echo "  已推送"
fi

echo "== 4/5 建 Release =="
NOTES=$(python3 -c "import json,io;print(json.dumps({'tag_name':'$TAG','name':'MagnetSearch 3.4','body':open('marketing/github/RELEASE_NOTES-3.4.md').read()}))")
if [ "$DRY" = "1" ]; then
  echo "  [dry-run] POST /repos/$USER_NAME/$REPO/releases tag=$TAG"
else
  REL=$(api POST "/repos/$USER_NAME/$REPO/releases" "$NOTES")
  UPLOAD=$(echo "$REL" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('upload_url',''))" 2>/dev/null)
  echo "  Release: $(echo "$REL" | python3 -c "import json,sys;print(json.load(sys.stdin).get('html_url','失败'))" 2>/dev/null)"
fi

echo "== 5/5 上传安装包 =="
for f in MagnetSearch-3.4.pkg MagnetSearch-3.4.dmg; do
  [ -f "$f" ] || { echo "  $f 不存在，跳过"; continue; }
  if [ "$DRY" = "1" ]; then echo "  [dry-run] upload $f"; continue; fi
  BASE="${UPLOAD%%\{*}"
  curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
       --data-binary "@$f" "$BASE?name=$f" -o /tmp/up.json
  python3 -c "import json;d=json.load(open('/tmp/up.json'));print('  上传',d.get('name','?'),d.get('state','?'))" 2>/dev/null || echo "  上传 $f 失败"
done

echo
echo "✅ https://github.com/$USER_NAME/$REPO"
echo "✅ https://github.com/$USER_NAME/$REPO/releases/tag/$TAG"
echo "→ 去仓库 About 填 Homepage：https://121.196.163.89.sslip.io/download"
echo "→ 加 Topics：macos swiftui torrent-search magnet torznab"
shasum -a 256 MagnetSearch-3.4.pkg | sed 's/^/   pkg SHA256: /'
