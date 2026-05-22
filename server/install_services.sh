#!/bin/bash
# install_services.sh — 安裝 Exegesis Python 套件 + 啟用 systemd 服務
# 在 server 上以 root 身份跑

set -e

PROJECT_DIR="/home/yt2epub/exegesis"
ANAMNESIS_DIR="/home/yt2epub/anamnesis"

echo "=========================================="
echo "Install Exegesis deps + systemd services"
echo "=========================================="

# 0. 確認 Anamnesis 在預期位置（PYTHONPATH 依賴）
if [ ! -d "$ANAMNESIS_DIR" ]; then
    echo "❌ 找不到 Anamnesis repo：$ANAMNESIS_DIR"
    echo "   Exegesis 跨 repo 依賴 Anamnesis 的 interest_model / vault_writer / vocabulary_manager"
    exit 1
fi

# 1. 裝 Python 套件
echo ""
echo "→ pip install -r requirements.txt..."
cd "$PROJECT_DIR"
sudo -u yt2epub pip3 install --break-system-packages -r requirements.txt

# 2. 確保 .env 權限
echo ""
echo "→ 收緊 .env 權限..."
chmod 600 "$PROJECT_DIR/.env"
chown yt2epub:yt2epub "$PROJECT_DIR/.env"

# 3. 複製 systemd unit 檔（timer 預設不啟用）
echo ""
echo "→ 安裝 systemd unit..."
cp "$PROJECT_DIR/server/exegesis-bot.service"    /etc/systemd/system/
cp "$PROJECT_DIR/server/exegesis-brief.service"  /etc/systemd/system/
cp "$PROJECT_DIR/server/exegesis-brief.timer"    /etc/systemd/system/
systemctl daemon-reload

# 4. 啟用 + 啟動 bot service（brief timer 暫不 enable，由 bot in-process loop 排程）
echo ""
echo "→ 啟用 + 啟動 exegesis-bot.service..."
systemctl enable --now exegesis-bot.service

# 5. 顯示狀態
echo ""
echo "=========================================="
echo "✅ 安裝完成"
echo "=========================================="
echo ""
echo "=== Bot 狀態 ==="
systemctl status exegesis-bot.service --no-pager -l | head -15
echo ""
echo "💡 若要啟用 fallback timer（保險用，可能會跟 bot loop 重複推送）："
echo "   systemctl enable --now exegesis-brief.timer"
