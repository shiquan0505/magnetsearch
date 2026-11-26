#!/bin/bash
# 一键把授权服务装到 Linux VPS（systemd）。
#
#   本地把这个目录 rsync 上去，然后在服务器上：
#     sudo bash install.sh
#
# 做的事：建 mslic 用户 → 拷到 /opt/magnetsearch-license → 生成密钥 → 装 systemd → 启动
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"      # licensing/
DEST="/opt/magnetsearch-license"
USER_NAME="mslic"

[ "$(id -u)" = "0" ] || { echo "请用 sudo 运行"; exit 1; }

echo "== 1/5 用户与目录 =="
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$DEST"
cp -R "$SRC/server" "$DEST/"
mkdir -p "$DEST/keys"
[ -f "$DEST/config.json" ] || cp "$SRC/config.json" "$DEST/config.json"

echo "== 2/5 生成签名密钥 =="
if [ ! -f "$DEST/keys/ed25519.key" ]; then
    ( cd "$DEST" && python3 server/admin.py --config "$DEST/config.json" init --force >/dev/null )
    chmod 600 "$DEST/keys/ed25519.key"
fi
PUB=$(cd "$DEST" && python3 -c "
import sys; sys.path.insert(0,'server')
import config as c; _sk,pk=c.ensure_keys(c.load('$DEST/config.json')); print(pk)")
echo "   公钥(hex)：$PUB"
echo "   ⚠️  把这个公钥填回开发机的 licensing/config.json 后重新构建客户端（tools/gen_client_config.py 会自动读私钥）"

echo "== 3/5 权限 =="
chown -R "$USER_NAME":"$USER_NAME" "$DEST"
chmod 700 "$DEST/keys"

echo "== 4/5 systemd =="
sed "s|/opt/magnetsearch-license|$DEST|g" "$SRC/deploy/magnetsearch-license.service" \
    > /etc/systemd/system/magnetsearch-license.service
systemctl daemon-reload
systemctl enable magnetsearch-license >/dev/null
systemctl restart magnetsearch-license

echo "== 5/5 自检 =="
sleep 2
PORT=$(python3 -c "import json;print(json.load(open('$DEST/config.json'))['server']['listen_port'])")
curl -s -m 5 "http://127.0.0.1:$PORT/healthz" && echo " ← 服务正常" || {
    echo "启动失败，看日志：journalctl -u magnetsearch-license -n 50 --no-pager"; exit 1; }

cat <<TIP

下一步（务必逐条改 $DEST/config.json）：
  1. server.public_base    改成 https://lic.example.com（走 Nginx + 证书）
  2. server.admin_token    换成一串随机值（后台/管理接口用）
  3. payment.provider      选 manual / vmq / epay / generic
  4. payment.<渠道>.*      填监控端地址或支付平台参数
  5. 改完 systemctl restart magnetsearch-license
  Nginx 样例：$SRC/deploy/nginx.conf.example
TIP
