#!/bin/bash
# Contabo Deployment Script - HTTPS Enabled

set -e

echo "=========================================="
echo "FactorySenseAI Production Deployment"
echo "=========================================="
echo ""

# Navigate to project directory
cd /root/FactorySenseAI || exit 1

echo "[1] Code already pushed. Syncing state..."
# Ensures the server files perfectly match your last push
git reset --hard

echo ""
echo "[2] Checking Docker status..."
docker compose -f docker-compose.prod.yml ps
echo ""

echo "[3] Restarting all services (including HTTPS Proxy)..."
# We remove 'web' so that Caddy and the DB also restart/start
docker compose -f docker-compose.prod.yml up -d --build
echo "✓ Services restarted"

echo ""
echo "[4] Waiting for SSL handshake (nip.io)..."
sleep 10

echo ""
echo "[5] Testing HTTPS health endpoint..."
# Testing the actual HTTPS URL from your Caddyfile
curl -s -k https://144-91-111-151.nip.io/ && echo "✓ HTTPS API is LIVE" || echo "⚠ HTTPS not responding yet"

echo ""
echo "[6] Running database migrations (Role Normalization)..."
docker compose -f docker-compose.prod.yml exec -T web python3 scripts/normalize_roles.py
echo "✓ Migrations complete"

echo ""
echo "[7] Showing Caddy logs (SSL Status)..."
docker compose -f docker-compose.prod.yml logs caddy | tail -10

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "HTTPS Endpoint: https://144-91-111-151.nip.io"
echo "Docs: https://144-91-111-151.nip.io/docs"
echo ""