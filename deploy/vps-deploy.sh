#!/bin/bash
set -e

APP_DIR="/opt/threads-analytics"
REPO="https://github.com/ocepkejepintu-droid/threads-needle.git"
DOMAIN="${1:-threads.yourdomain.com}"

echo "=== Threads Analytics VPS Deploy ==="
echo "Domain: $DOMAIN"

# 1. Install deps
echo "[1/7] Installing dependencies..."
if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu
    apt-get update
    apt-get install -y python3-venv python3-pip git caddy
elif command -v yum &> /dev/null; then
    # RHEL/CentOS/OpenCloudOS
    yum install -y python3 python3-pip git
    # Install Caddy via official binary
    if ! command -v caddy &> /dev/null; then
        curl -1sLf 'https://caddyserver.com/api/download?os=linux&arch=amd64' -o /usr/local/bin/caddy
        chmod +x /usr/local/bin/caddy
    fi
elif command -v dnf &> /dev/null; then
    # Fedora/RHEL 8+
    dnf install -y python3 python3-pip git caddy
else
    echo "No supported package manager found (apt-get, yum, dnf)"
    exit 1
fi

# 2. Clone or pull
echo "[2/7] Setting up app..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git pull origin main
else
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# 3. Python env
echo "[3/7] Installing Python packages..."
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 4. Database
echo "[4/7] Initializing database..."
mkdir -p data
python3 -c "from threads_analytics.db import init_db; init_db()"

# 5. Environment
echo "[5/7] Checking environment..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "WARNING: .env created from example. Please edit it and add your API keys."
fi

# Fix DB path to absolute
sed -i "s|sqlite:///data/|sqlite:///$APP_DIR/data/|" .env

# 6. Systemd service
echo "[6/7] Installing systemd service..."
cat > /etc/systemd/system/threads-analytics.service <<EOF
[Unit]
Description=Threads Analytics Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/uvicorn threads_analytics.web.app:create_app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable threads-analytics
systemctl start threads-analytics || systemctl restart threads-analytics

# 7. Caddy (reverse proxy + SSL)
echo "[7/7] Configuring Caddy..."
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF

systemctl reload caddy || systemctl start caddy

echo ""
echo "=== Deploy Complete ==="
echo "App:     http://127.0.0.1:8000"
echo "Public:  https://$DOMAIN"
echo ""
echo "Check status: sudo systemctl status threads-analytics"
echo "View logs:    sudo journalctl -u threads-analytics -f"
echo ""
echo "Hermes endpoint:"
echo "  POST https://$DOMAIN/accounts/default/api/hermes/push"
echo "  X-Hermes-Key: <from your .env file>"
