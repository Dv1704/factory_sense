#!/bin/bash

# Configuration
VPS_IP="144.91.111.151"
VPS_USER="root"
VPS_PASS="P4K9s8bvtTv6xu77"
APP_DIR="/root/FactorySenseAI"

echo "Step 1: Packaging application..."
tar --exclude='venv' --exclude='.venv' --exclude='__pycache__' --exclude='.git' -czf app.tar.gz .

echo "Step 2: Uploading to VPS..."
sshpass -p "$VPS_PASS" scp -o StrictHostKeyChecking=no app.tar.gz $VPS_USER@$VPS_IP:/root/

echo "Step 3: Deploying on VPS..."
sshpass -p "$VPS_PASS" ssh -o StrictHostKeyChecking=no $VPS_USER@$VPS_IP << 'EOF'
    mkdir -p /root/FactorySenseAI
    tar -xzf /root/app.tar.gz -C /root/FactorySenseAI
    cd /root/FactorySenseAI
    
    # Check for docker/docker-compose or install them
    if ! command -v docker &> /dev/null; then
        echo "Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
    fi
    
    # Simple check for docker-compose (v2 often installed as 'docker compose')
    if ! docker compose version &> /dev/null && ! docker-compose version &> /dev/null; then
        echo "Installing Docker Compose..."
        apt-get update && apt-get install -y docker-compose-plugin
    fi

    # Create .env if missing
    if [ ! -f .env ]; then
        echo "Creating production .env..."
        echo "DB_USER=factory_user" > .env
        echo "DB_PASSWORD=$(openssl rand -hex 12)" >> .env
        echo "DB_NAME=factorysense" >> .env
        echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env
    fi

    echo "Starting containers..."
    # Try docker compose first, then docker-compose
    COMPOSE_CMD="docker compose"
    if ! docker compose version &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    fi
    
    $COMPOSE_CMD -f docker-compose.prod.yml up -d --build

    echo "Waiting for services to start..."
    sleep 10
    
    echo "Running database migrations..."
    # Add column if missing
    $COMPOSE_CMD -f docker-compose.prod.yml exec -T db psql -U factory_user -d factorysense -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS has_uploaded_baseline BOOLEAN DEFAULT FALSE;"
    # Create missing tables
    $COMPOSE_CMD -f docker-compose.prod.yml exec -T web python create_tables.py
    
    docker ps
EOF

echo "Step 4: Cleanup..."
rm app.tar.gz

echo "Deployment finished! Visit http://$VPS_IP:8000/docs"
