#!/bin/bash
# Deploy platform to any domain - master deployment script

# Usage: ./deploy.sh <domain> [app-name] [platform-name]

# Get parameters
DOMAIN_NAME=${1:-$DOMAIN_NAME}
APP_NAME=${2:-"sidekick-forge"}
PLATFORM_NAME=${3:-"Sidekick Forge"}
PROJECT_ROOT=${4:-"/root/sidekick-forge"}

# Validate domain name
if [ -z "$DOMAIN_NAME" ]; then
    echo "Usage: ./deploy.sh <domain> [app-name] [platform-name] [project-root]"
    echo "Example: ./deploy.sh sidekickforge.com sidekick-forge 'Sidekick Forge' /root/sidekick-forge"
    exit 1
fi

echo "==================================="
echo "Deploying platform configuration:"
echo "Domain: $DOMAIN_NAME"
echo "App Name: $APP_NAME"
echo "Platform Name: $PLATFORM_NAME"
echo "Project Root: $PROJECT_ROOT"
echo "==================================="

# Export for use in other scripts
export DOMAIN_NAME APP_NAME PLATFORM_NAME PROJECT_ROOT

# Update .env file
ENV_FILE="${PROJECT_ROOT}/.env"
if [ -f "$ENV_FILE" ]; then
    echo "Updating environment file..."
    # Use temporary file to avoid issues with sed -i on some systems
    cp "$ENV_FILE" "$ENV_FILE.bak"
    
    # Update or add domain configuration
    if grep -q "^DOMAIN_NAME=" "$ENV_FILE"; then
        sed "s|^DOMAIN_NAME=.*|DOMAIN_NAME=\"$DOMAIN_NAME\"|" "$ENV_FILE" > "$ENV_FILE.tmp"
    else
        echo "DOMAIN_NAME=\"$DOMAIN_NAME\"" >> "$ENV_FILE"
    fi
    
    if grep -q "^APP_NAME=" "$ENV_FILE"; then
        sed "s|^APP_NAME=.*|APP_NAME=\"$APP_NAME\"|" "$ENV_FILE.tmp" > "$ENV_FILE.tmp2"
        mv "$ENV_FILE.tmp2" "$ENV_FILE.tmp"
    else
        echo "APP_NAME=\"$APP_NAME\"" >> "$ENV_FILE.tmp"
    fi
    
    if grep -q "^PLATFORM_NAME=" "$ENV_FILE"; then
        sed "s|^PLATFORM_NAME=.*|PLATFORM_NAME=\"$PLATFORM_NAME\"|" "$ENV_FILE.tmp" > "$ENV_FILE.tmp2"
        mv "$ENV_FILE.tmp2" "$ENV_FILE.tmp"
    else
        echo "PLATFORM_NAME=\"$PLATFORM_NAME\"" >> "$ENV_FILE.tmp"
    fi
    
    if grep -q "^PROJECT_ROOT=" "$ENV_FILE"; then
        sed "s|^PROJECT_ROOT=.*|PROJECT_ROOT=\"$PROJECT_ROOT\"|" "$ENV_FILE.tmp" > "$ENV_FILE"
    else
        echo "PROJECT_ROOT=\"$PROJECT_ROOT\"" >> "$ENV_FILE.tmp"
        mv "$ENV_FILE.tmp" "$ENV_FILE"
    fi
    
    rm -f "$ENV_FILE.tmp" "$ENV_FILE.tmp2"
else
    echo "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

# Change to project directory
cd "$PROJECT_ROOT" || exit 1

# Stop existing services
echo "Stopping existing services..."
docker-compose down || true
systemctl stop "${APP_NAME}-fastapi" 2>/dev/null || true

# Generate nginx configuration
echo "Generating nginx configuration..."
./scripts/generate-nginx-config.sh

# Setup SSL certificate (optional - skip if testing locally)
read -p "Do you want to setup SSL certificate? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ./scripts/setup-ssl.sh
fi

# Update systemd service
echo "Updating systemd service..."
SYSTEMD_FILE="/etc/systemd/system/platform-fastapi.service"
cat > "$SYSTEMD_FILE" << EOF
[Unit]
Description=$PLATFORM_NAME FastAPI Service
After=network.target

[Service]
Type=exec
User=root
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=$PROJECT_ROOT/.env
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

# Build docker images
echo "Building Docker images..."
docker-compose build

# Start services
echo "Starting services..."
docker-compose up -d

# Enable and start systemd service (optional)
read -p "Do you want to use systemd service instead of docker-compose? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker-compose down fastapi
    systemctl enable platform-fastapi
    systemctl start platform-fastapi
fi

# Restart nginx
echo "Restarting nginx..."
systemctl restart nginx || service nginx restart

# Check service status
echo ""
echo "==================================="
echo "Deployment complete!"
echo ""
echo "Services status:"
docker-compose ps
echo ""
echo "Access your platform at:"
echo "HTTP: http://$DOMAIN_NAME"
echo "HTTPS: https://$DOMAIN_NAME (if SSL configured)"
echo "Admin: https://$DOMAIN_NAME/admin"
echo ""
echo "To check logs:"
echo "docker-compose logs -f"
echo "==================================="