#!/bin/bash
# Contabo Deployment Script
# Run this on Contabo server to deploy latest changes

set -e

echo "=========================================="
echo "FactorySenseAI Production Deployment"
echo "=========================================="
echo ""

# Navigate to project directory
cd /root/FactorySenseAI || exit 1

echo "[1] Pulling latest changes from GitHub..."
git pull origin master
echo "✓ Git pull complete"

echo ""
echo "[2] Checking Docker status..."
docker-compose -f docker-compose.prod.yml ps
echo ""

echo "[3] Restarting web service..."
docker-compose -f docker-compose.prod.yml restart web
echo "✓ Service restarted"

echo ""
echo "[4] Waiting for service to be ready..."
sleep 5

echo ""
echo "[5] Testing health endpoint..."
curl -s http://localhost:8000/ && echo "" && echo "✓ API is running" || echo "⚠ API not responding yet"

echo ""
echo "[6] Showing recent logs..."
docker-compose -f docker-compose.prod.yml logs web | tail -20

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "API Endpoint: http://144.91.111.151:8000"
echo "Docs: http://144.91.111.151:8000/docs"
echo ""
echo "Quick Test:"
echo "curl -X POST http://144.91.111.151:8000/api/v1/auth/register \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"email\":\"test@prod\",\"password\":\"pass123\",\"mill_id\":\"MILL1\"}'"
echo ""
