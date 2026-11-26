#!/bin/bash
# 给站点加一个域名（自备域名解析到本机后执行）
#   bash add_domain.sh msou.com            # 加域名 + 签证书 + 重载 nginx
#   bash add_domain.sh msou.com --dry-run  # 只看会做什么
# 前提：域名 A 记录已指向本机公网 IP；大陆机器 80/443 需 ICP 备案
set -euo pipefail
DOMAIN="${1:?用法: add_domain.sh <域名> [--dry-run]}"
DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
CONF=/etc/nginx/conf.d/magnetsearch.conf
CERT_DIR=/etc/letsencrypt/live/$DOMAIN
run() { if [ "$DRY" = "1" ]; then echo "  [dry-run] $*"; else eval "$@"; fi; }

echo "== 1/4 检查解析 =="
IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
[ -z "$IP" ] && IP=$(python3 -c "import socket;print(socket.gethostbyname('$DOMAIN'))" 2>/dev/null || true)
PUB=$(curl -s -m 5 http://ip.3322.net/ | tr -d '\r\n')
echo "   $DOMAIN → ${IP:-未解析}   本机公网 IP: $PUB"
[ "${IP:-}" != "$PUB" ] && echo "   ⚠️  解析未指向本机，证书申请会失败"

echo "== 2/4 备份并加入 server_name =="
run "cp -n $CONF $CONF.bak.$(date +%s)"
run "grep -q 'server_name .*$DOMAIN' $CONF || sed -i \"s/server_name \\(.*\\);/server_name \\1 $DOMAIN;/\" $CONF"

echo "== 3/4 先只跑 80 版配置申请证书 =="
if [ "$DRY" = "0" ]; then
  # 临时把 443 段注释掉，避免证书不存在导致 nginx 起不来
  cp "$CONF" /tmp/ms.conf.$$
  python3 - "$CONF" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
s = re.sub(r'server \{\s*listen 443.*?\n\}\n', '', s, flags=re.S)
open(p, 'w').write(s)
PY
  nginx -t && systemctl reload nginx
  certbot certonly --webroot -w /var/www/acme -d "$DOMAIN" --agree-tos \
    -m "admin@$DOMAIN" --non-interactive || echo "   ❌ 证书申请失败（看 /var/log/letsencrypt/letsencrypt.log）"
  cp /tmp/ms.conf.$$ "$CONF" && rm -f /tmp/ms.conf.$$
  sed -i "s|$CERT_DIR/fullchain.pem|$CERT_DIR/fullchain.pem|" "$CONF"
else
  run "certbot certonly --webroot -w /var/www/acme -d $DOMAIN --agree-tos -m admin@$DOMAIN --non-interactive"
fi

echo "== 4/4 应用并校验 =="
run "nginx -t"
run "systemctl reload nginx"
run "curl -s -o /dev/null -w '   https://$DOMAIN/download → %{http_code}\\n' https://$DOMAIN/download"
cat <<TIP

下一步（可选）：
  · 想让它成为主站地址：改 /opt/magnetsearch-license/config.json
      server.site_base = "https://$DOMAIN"    → systemctl restart magnetsearch-license
  · 想让客户端也走新域名（付款/发证）：需要改 config.json 的 public_base 并重新构建客户端
  · 记得把北尚互联后台的「跳转网址」换成 https://$DOMAIN/claim/<密钥>
TIP
