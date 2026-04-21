#!/bin/bash
# Contabo Deployment Script
# This version is optimized for direct local-to-VPS deployment

set -e

echo "=========================================="
echo "FactorySenseAI Production Deployment"
echo "=========================================="
echo ""

# Navigate to project directory
cd /root/FactorySenseAI || exit 1

# [1] REMOVED GITHUB PULL 
# We skip this because you are pushing code directly from your laptop.
echo "[1] Code already pushed from local machine. Skipping GitHub pull..."

echo ""
echo "[2] Checking Docker status..."
# Using 'docker compose' (v2) instead of the older 'docker-compose'
docker compose -f docker-compose.prod.yml ps
echo ""

echo "[3] Restarting containers..."
# Use 'up -d' to ensure everything is running and updated
docker compose -f docker-compose.prod.yml up -d --build web
echo "✓ Service restarted"

echo ""
echo "[4] Waiting for service to be ready..."
sleep 5

echo ""
echo "[5] Testing health endpoint..."
# Added a check to see if the container is actually listening on 8000
curl -s http://localhost:8000/ && echo "" && echo "✓ API is running" || echo "⚠ API not responding yet"

echo ""
echo "[6] Showing recent logs..."
docker compose -f docker-compose.prod.yml logs web | tail -20

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "API Endpoint: http://144.91.111.151:8000"
echo "Docs: http://144.91.111.151:8000/docs"
echo ""