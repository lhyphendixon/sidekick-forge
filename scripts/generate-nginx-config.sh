#!/bin/bash
# Generate nginx configuration from template with environment variables

# Load environment variables
if [ -f "${PROJECT_ROOT:-/root/sidekick-forge}/.env" ]; then
    export $(cat "${PROJECT_ROOT:-/root/sidekick-forge}/.env" | grep -v '^#' | xargs)
fi

# Set defaults if not provided
DOMAIN_NAME=${DOMAIN_NAME:-"sidekickforge.com"}
PROJECT_ROOT=${PROJECT_ROOT:-"/root/sidekick-forge"}
CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-"https?://(www\.)?(localhost:[0-9]+)"}

# Create nginx config directory if it doesn't exist
mkdir -p /etc/nginx/sites-available
mkdir -p /etc/nginx/sites-enabled

# Generate nginx configuration from template
echo "Generating nginx configuration for domain: $DOMAIN_NAME"
envsubst '${DOMAIN_NAME} ${PROJECT_ROOT} ${CORS_ALLOWED_ORIGINS}' \
    < "${PROJECT_ROOT}/nginx/site.conf.template" \
    > "/etc/nginx/sites-available/platform.conf"

# Create symbolic link
ln -sf /etc/nginx/sites-available/platform.conf /etc/nginx/sites-enabled/

# Test nginx configuration
nginx -t

if [ $? -eq 0 ]; then
    echo "Nginx configuration generated successfully"
else
    echo "ERROR: Nginx configuration test failed"
    exit 1
fi