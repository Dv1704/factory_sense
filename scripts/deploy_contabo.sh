#!/bin/bash

# Configuration
APP_DIR="/root/FactorySenseAI"
REPO_URL="https://github.com/Dv1704/FactorySenseAI.git" # Replace with your actual repo URL if different

# Update and install Docker if not present
if ! command -v docker &> /dev/null
then
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
fi

if ! command -v docker-compose &> /dev/null
then
    echo "Docker Compose not found. Installing..."
    apt-get update && apt-get install -y docker-compose
fi

# Clone or Update Repository
if [ -d "$APP_DIR" ]; then
    echo "Updating repository..."
    cd "$APP_DIR"
    git pull
else
    echo "Cloning repository..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file. Please update the values!"
    cat <<EOT > .env
DB_USER=factory_user
DB_PASSWORD=$(openssl rand -hex 12)
DB_NAME=factorysense
SECRET_KEY=$(openssl rand -hex 32)
EOT
fi

# Build and Start
echo "Starting containers..."
docker-compose -f docker-compose.prod.yml up -d --build

echo "Deployment complete! Your API should be available on port 8000."
