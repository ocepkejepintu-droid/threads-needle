# VPS Deploy Guide

Assumes Ubuntu 22.04/24.04 with a domain pointing at your server.

## 1. Clone & setup on the VPS

```bash
# SSH into your VPS, then:
sudo apt update && sudo apt install -y python3-venv python3-pip git caddy

cd /opt
sudo git clone https://github.com/YOUR_USERNAME/threads-needle.git threads-analytics
cd threads-analytics

# Create virtual env
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Copy your .env (or create it)
cp .env.example .env
# EDIT .env and fill in your keys
```

## 2. Fix database path

In `.env`, make sure `DATABASE_URL` uses an absolute path:

```env
DATABASE_URL=sqlite:////opt/threads-analytics/data/threads.db
```

## 3. Create the database

```bash
source .venv/bin/activate
python -c "from threads_analytics.db import init_db; init_db()"
```

## 4. Set permissions

```bash
sudo chown -R www-data:www-data /opt/threads-analytics
sudo chmod 640 /opt/threads-analytics/.env
```

## 5. Install systemd service

```bash
sudo cp deploy/threads-analytics.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable threads-analytics
sudo systemctl start threads-analytics
sudo systemctl status threads-analytics
```

## 6. Install Caddy (reverse proxy + SSL)

Edit `deploy/Caddyfile` and replace `threads.yourdomain.com` with your actual domain.

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy will automatically get an SSL certificate from Let's Encrypt.

## 7. Verify

```bash
# App health
curl http://127.0.0.1:8000/accounts/default

# Public URL (should return 200 with SSL)
curl https://threads.yourdomain.com/accounts/default
```

## 8. Update Hermes endpoint

Once deployed, tell Hermes to POST to:

```
POST https://threads.yourdomain.com/accounts/default/api/hermes/push
X-Hermes-Key: <your key from .env>
```

## Useful commands

```bash
# View logs
sudo journalctl -u threads-analytics -f

# Restart after code update
sudo systemctl restart threads-analytics

# Update app (pull latest, restart)
cd /opt/threads-analytics && sudo git pull
source .venv/bin/activate && pip install -e ".[dev]"
sudo systemctl restart threads-analytics
```
