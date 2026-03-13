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
    # Add columns and rename if missing
    $COMPOSE_CMD -f docker-compose.prod.yml exec -T db psql -U factory_user -d factorysense -c "
        ALTER TABLE users ADD COLUMN IF NOT EXISTS has_uploaded_baseline BOOLEAN DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'manager';
        DO \$\$ 
        BEGIN 
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='machine_daily_stats' AND column_name='avg_current') THEN
                ALTER TABLE machine_daily_stats RENAME COLUMN avg_current TO \"avg_current_A\";
            END IF;
        END \$\$;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS max_current DOUBLE PRECISION;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS std_current DOUBLE PRECISION;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS reference_mean DOUBLE PRECISION;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS reference_std DOUBLE PRECISION;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS reference_p95 DOUBLE PRECISION;
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS health_score_details TEXT;

        -- User Isolation Migrations
        ALTER TABLE raw_files ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        ALTER TABLE machine_daily_stats ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        ALTER TABLE machine_baselines ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        ALTER TABLE machine_baselines ADD COLUMN IF NOT EXISTS data_points_count INTEGER DEFAULT 0;
        ALTER TABLE machine_data_points ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
        ALTER TABLE alerts ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

        -- Assign existing data to the first user found
        DO \$\$
        DECLARE 
            default_uid INTEGER;
        BEGIN
            SELECT id INTO default_uid FROM users LIMIT 1;
            IF default_uid IS NOT NULL THEN
                UPDATE raw_files SET user_id = default_uid WHERE user_id IS NULL;
                UPDATE machine_daily_stats SET user_id = default_uid WHERE user_id IS NULL;
                UPDATE machine_baselines SET user_id = default_uid WHERE user_id IS NULL;
                UPDATE machine_data_points SET user_id = default_uid WHERE user_id IS NULL;
                UPDATE alerts SET user_id = default_uid WHERE user_id IS NULL;
            END IF;
        END \$\$;

        -- Make user_id NOT NULL for all tables
        ALTER TABLE raw_files ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE machine_daily_stats ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE machine_baselines ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE machine_data_points ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE alerts ALTER COLUMN user_id SET NOT NULL;

        -- Multi-Mill Migrations
        CREATE TABLE IF NOT EXISTS mills (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            mill_id TEXT NOT NULL,
            api_key TEXT UNIQUE,
            has_uploaded_baseline BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        -- Migrate users to mills
        INSERT INTO mills (user_id, mill_id, api_key, has_uploaded_baseline, created_at)
        SELECT id, mill_id, api_key, has_uploaded_baseline, created_at FROM users
        WHERE api_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mills WHERE mills.api_key = users.api_key);

        -- Fix users table (remove NOT NULL from legacy columns)
        ALTER TABLE users ALTER COLUMN mill_id DROP NOT NULL;
        ALTER TABLE users ALTER COLUMN api_key DROP NOT NULL;

        -- Add Machine Baseline History table if missing (should be handled by create_tables.py too, but safer here)
        CREATE TABLE IF NOT EXISTS machine_baseline_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            mill_id TEXT NOT NULL,
            machine_id TEXT NOT NULL,
            mean_current DOUBLE PRECISION NOT NULL,
            std_current DOUBLE PRECISION NOT NULL,
            p95_current DOUBLE PRECISION NOT NULL,
            data_points_count INTEGER NOT NULL,
            update_type TEXT NOT NULL,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS processing_tasks (
            id SERIAL PRIMARY KEY,
            task_id TEXT UNIQUE NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            mill_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            status TEXT NOT NULL,
            progress DOUBLE PRECISION DEFAULT 0.0,
            message TEXT,
            task_type TEXT NOT NULL,
            records_processed INTEGER DEFAULT 0,
            total_records INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            estimated_seconds_remaining DOUBLE PRECISION
        );
    "
    # Create missing tables
    $COMPOSE_CMD -f docker-compose.prod.yml exec -T web python create_tables.py
    
    # Setup regular automated backups (Daily at 2 AM)
    echo "Setting up DB backup cron job..."
    BACKUP_SCRIPT="/root/FactorySenseAI/backup_db.sh"
    echo "#!/bin/bash" > \$BACKUP_SCRIPT
    echo "mkdir -p /root/FactorySenseAI/backups" >> \$BACKUP_SCRIPT
    echo "cd /root/FactorySenseAI" >> \$BACKUP_SCRIPT
    echo "$COMPOSE_CMD -f docker-compose.prod.yml exec -T db pg_dump -U factory_user factorysense > backups/db_backup_\$(date +\%F).sql" >> \$BACKUP_SCRIPT
    echo "find /root/FactorySenseAI/backups -type f -name '*.sql' -mtime +14 -exec rm {} \;" >> \$BACKUP_SCRIPT
    chmod +x \$BACKUP_SCRIPT
    
    (crontab -l 2>/dev/null | grep -v "backup_db.sh"; echo "0 2 * * * \$BACKUP_SCRIPT") | crontab -

    # Setup Data Retention Policy execution (Daily at 3 AM)
    (crontab -l 2>/dev/null | grep -v "retention_policy.py"; echo "0 3 * * * $COMPOSE_CMD -f /root/FactorySenseAI/docker-compose.prod.yml exec -T web python scripts/retention_policy.py") | crontab -

    docker ps
EOF

echo "Step 4: Cleanup..."
rm app.tar.gz

echo "Deployment finished! Visit http://$VPS_IP:8000/docs"
