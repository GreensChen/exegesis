#!/bin/bash
# deploy.sh — rsync Exegesis 程式碼到 Hetzner server
# 在 Mac 上跑：./server/deploy.sh

set -e

SERVER_IP="${SERVER_IP:?Please set SERVER_IP env var: export SERVER_IP=1.2.3.4}"
REMOTE_USER="root"
REMOTE_DIR="/home/yt2epub/exegesis"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=========================================="
echo "Deploy Exegesis → $SERVER_IP"
echo "=========================================="
echo ""
echo "Local:  $LOCAL_DIR"
echo "Remote: $REMOTE_USER@$SERVER_IP:$REMOTE_DIR"
echo ""

# 用 rsync 推程式碼上去（排除 server 自己的狀態 / log）
rsync -avz --delete \
    --exclude='.env' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.stdout.log' \
    --exclude='*.stderr.log' \
    --exclude='.claude/' \
    --exclude='.git/' \
    --exclude='server/deploy.sh' \
    -e "ssh -o StrictHostKeyChecking=accept-new" \
    "$LOCAL_DIR/" \
    "$REMOTE_USER@$SERVER_IP:$REMOTE_DIR/"

# 修正 ownership
ssh "$REMOTE_USER@$SERVER_IP" \
    "mkdir -p $REMOTE_DIR && chown -R yt2epub:yt2epub $REMOTE_DIR && chmod +x $REMOTE_DIR/server/*.sh 2>/dev/null"

echo ""
echo "✅ 程式碼已部署到 $SERVER_IP"
echo ""
echo "💡 首次部署別忘了："
echo "   1. ssh root@$SERVER_IP 'cd $REMOTE_DIR/server && ./install_services.sh'"
echo "   2. 確認 .env 已存在於 $REMOTE_DIR/.env（包含 EXEGESIS_BOT_TOKEN）"
echo "   3. systemctl status exegesis-bot.service"
