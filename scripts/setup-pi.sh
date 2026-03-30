#!/bin/bash
# Delfin - Raspberry Pi Setup Script
# Run this on your Raspberry Pi to set up the backend as a service

set -e

DELFIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DELFIN_USER="$(whoami)"
DELFIN_PORT="${1:-8000}"

echo "=== Delfin Raspberry Pi Setup ==="
echo "Directory: $DELFIN_DIR"
echo "User: $DELFIN_USER"
echo "Port: $DELFIN_PORT"
echo ""

# 1. Install Python dependencies
echo "[1/3] Installing Python dependencies..."
pip3 install -r "$DELFIN_DIR/requirements.txt"

# 2. Create systemd service
echo "[2/3] Creating systemd service..."
sudo tee /etc/systemd/system/delfin.service > /dev/null <<EOF
[Unit]
Description=Delfin Finance Server
After=network.target

[Service]
Type=simple
User=$DELFIN_USER
WorkingDirectory=$DELFIN_DIR
ExecStart=$(which python3) -m uvicorn backend.main:app --host 0.0.0.0 --port $DELFIN_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 3. Enable and start the service
echo "[3/3] Enabling and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable delfin.service
sudo systemctl start delfin.service

PI_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=== Setup complete! ==="
echo "Delfin is running at: http://$PI_IP:$DELFIN_PORT"
echo "Frontend available at: http://$PI_IP:$DELFIN_PORT/app/index.html"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status delfin    # Check status"
echo "  sudo systemctl restart delfin   # Restart"
echo "  sudo systemctl stop delfin      # Stop"
echo "  journalctl -u delfin -f         # View logs"
