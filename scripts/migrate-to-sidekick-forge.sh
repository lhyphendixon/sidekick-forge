#!/bin/bash
# Script to migrate from autonomite-agent-platform to sidekick-forge

echo "========================================"
echo "Migrating to Sidekick Forge"
echo "========================================"

# Set variables
OLD_DIR="/root/sidekick-forge"
NEW_DIR="/root/sidekick-forge"

# Check if running from correct directory
if [ ! -f "$OLD_DIR/docker-compose.yml" ]; then
    echo "ERROR: Must run from project root directory"
    exit 1
fi

# Step 1: Stop all services
echo "Stopping all services..."
cd "$OLD_DIR"
docker-compose down
systemctl stop autonomite-fastapi 2>/dev/null || true

# Step 2: Create new directory structure
echo "Creating new directory structure..."
if [ -d "$NEW_DIR" ]; then
    echo "ERROR: $NEW_DIR already exists. Please remove or rename it first."
    exit 1
fi

# Copy the entire directory
echo "Copying project files..."
cp -r "$OLD_DIR" "$NEW_DIR"

# Step 3: Create compatibility symlink
echo "Creating compatibility symlink..."
mv "$OLD_DIR" "${OLD_DIR}.backup"
ln -s "$NEW_DIR" "$OLD_DIR"

# Step 4: Update file references
echo "Updating file references..."
cd "$NEW_DIR"

# Update Python files
find . -name "*.py" -type f -exec sed -i \
    -e 's|/root/sidekick-forge|/root/sidekick-forge|g' \
    -e 's|autonomite-agent"|sidekick-agent"|g' \
    -e 's|autonomite_backend|sidekick_backend|g' \
    -e 's|autonomite_tools|sidekick_tools|g' \
    {} \;

# Update shell scripts
find . -name "*.sh" -type f -exec sed -i \
    -e 's|/root/sidekick-forge|/root/sidekick-forge|g' \
    {} \;

# Update docker files
find . -name "Dockerfile*" -name "docker-compose*.yml" -type f -exec sed -i \
    -e 's|autonomite-agent-platform|sidekick-forge|g' \
    {} \;

# Step 5: Update systemd service
echo "Updating systemd service..."
if [ -f "/etc/systemd/system/autonomite-fastapi.service" ]; then
    cp "/etc/systemd/system/autonomite-fastapi.service" "/etc/systemd/system/sidekick-forge-fastapi.service"
    sed -i 's|autonomite|sidekick-forge|g' "/etc/systemd/system/sidekick-forge-fastapi.service"
    sed -i 's|Autonomite|Sidekick Forge|g' "/etc/systemd/system/sidekick-forge-fastapi.service"
    systemctl disable autonomite-fastapi.service
    systemctl enable sidekick-forge-fastapi.service
fi

# Step 6: Update .env file
echo "Updating environment configuration..."
if [ -f "$NEW_DIR/.env" ]; then
    # Ensure new variables are set
    if ! grep -q "^PROJECT_ROOT=" "$NEW_DIR/.env"; then
        echo "PROJECT_ROOT=\"$NEW_DIR\"" >> "$NEW_DIR/.env"
    else
        sed -i "s|^PROJECT_ROOT=.*|PROJECT_ROOT=\"$NEW_DIR\"|" "$NEW_DIR/.env"
    fi
fi

# Step 7: Rebuild Docker images
echo "Rebuilding Docker images..."
cd "$NEW_DIR"
docker-compose build

echo ""
echo "========================================"
echo "Migration complete!"
echo ""
echo "Next steps:"
echo "1. Run the deployment script:"
echo "   cd $NEW_DIR"
echo "   ./scripts/deploy.sh sidekickforge.com"
echo ""
echo "2. The old directory is backed up at: ${OLD_DIR}.backup"
echo "3. A symlink maintains compatibility at: $OLD_DIR -> $NEW_DIR"
echo ""
echo "To start services:"
echo "   docker-compose up -d"
echo "========================================"